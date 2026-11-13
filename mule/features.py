"""Graph construction, temporal features and rule indicators for multi-account data.

Input: a normalized transactions DataFrame with columns
    txn_id, timestamp (datetime64), src, dst, amount (float > 0)
Output: one feature row per account plus rule evidence.

The same functions are used for model training (ml/train_mule_model.py) and for
live analysis in the API, so features never drift between the two.
"""
from __future__ import annotations

import io
from collections import defaultdict
from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd

from shared.normalize import parse_amount

REQUIRED = ["timestamp", "src", "dst", "amount"]
SYNONYMS = {
    "txn_id": ["txn_id", "transaction_id", "tran_id", "id"],
    "timestamp": ["timestamp", "datetime", "date_time", "txn_time", "time", "date"],
    # sender_id/receiver_id = MuleGuard / mr-mule live-feed schema
    "src": ["src", "from", "from_account", "sender", "sender_id", "sender_account", "orig", "nameorig", "source"],
    "dst": ["dst", "to", "to_account", "receiver", "receiver_id", "receiver_account", "dest", "namedest", "beneficiary", "target"],
    "amount": ["amount", "amt", "value", "txn_amount"],
    "channel": ["channel", "mode", "payment_type", "type"],
    "is_mule_label": ["is_mule", "label", "is_laundering", "is_sar"],
}

# Rule thresholds (documented in docs/MULE_MODEL_CARD.md). Window = 24h unless noted.
RULES = {
    "fan_in_min_senders_24h": 8,
    "fan_out_min_receivers_24h": 8,
    "pass_through_ratio": 0.8,
    "pass_through_max_minutes": 60,
    "velocity_ratio": 6.0,
    "dormant_days": 30,
    "dormant_burst_txns_48h": 8,
    "structuring_low": 45_000,
    "structuring_high": 50_000,
    "structuring_min_count_24h": 3,
    "cycle_max_len": 5,
    "cycle_max_hours": 72,
    "cycle_min_amount": 5_000,
}

FEATURE_COLUMNS = [
    "in_count", "out_count", "in_degree", "out_degree", "in_amount", "out_amount",
    "pass_through_ratio", "median_hold_minutes", "max_senders_24h", "max_receivers_24h",
    "velocity_ratio", "dormant_days_before_burst", "structuring_count_24h", "in_temporal_cycle",
    "pagerank", "clustering", "active_days", "avg_in_amount", "avg_out_amount", "night_share",
]


class SchemaError(ValueError):
    pass


def normalize_transactions(content: bytes | pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Validate and normalize an uploaded multi-account file."""
    if isinstance(content, (bytes, bytearray)):
        raw = pd.read_csv(io.BytesIO(content), dtype=str)
    else:
        raw = content.astype(str)
    lower = {c.strip().lower(): c for c in raw.columns}
    colmap = {}
    for canon, opts in SYNONYMS.items():
        for o in opts:
            if o in lower:
                colmap[canon] = lower[o]
                break
    missing = [c for c in REQUIRED if c not in colmap]
    if missing:
        raise SchemaError(f"Missing required columns: {missing}. Found: {list(raw.columns)}")

    df = pd.DataFrame({
        "timestamp": pd.to_datetime(raw[colmap["timestamp"]], errors="coerce", format="mixed"),
        "src": raw[colmap["src"]].astype(str).str.strip(),
        "dst": raw[colmap["dst"]].astype(str).str.strip(),
        "amount": raw[colmap["amount"]].map(parse_amount),
    })
    df["txn_id"] = raw[colmap["txn_id"]].astype(str) if "txn_id" in colmap else [f"T{i:07d}" for i in range(len(raw))]
    df["channel"] = raw[colmap["channel"]].astype(str) if "channel" in colmap else "unknown"
    if "is_mule_label" in colmap:
        df["label_src"] = raw[colmap["is_mule_label"]]

    n_in = len(df)
    bad = df["timestamp"].isna() | df["amount"].isna() | (df["amount"] <= 0) | (df["src"] == "") | (df["dst"] == "") | (df["src"] == df["dst"])
    df = df[~bad].copy()
    dupes = df.duplicated(subset=["txn_id"]).sum()
    df = df.drop_duplicates(subset=["txn_id"]).sort_values("timestamp").reset_index(drop=True)
    report = {
        "rows_in": n_in,
        "rows_dropped_invalid": int(bad.sum()),
        "rows_dropped_duplicate": int(dupes),
        "rows_out": len(df),
        "accounts": int(pd.unique(pd.concat([df["src"], df["dst"]])).size),
        "period_start": str(df["timestamp"].min()) if len(df) else None,
        "period_end": str(df["timestamp"].max()) if len(df) else None,
        "column_map": colmap,
    }
    return df, report


def build_graph(df: pd.DataFrame) -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    for r in df.itertuples(index=False):
        G.add_edge(r.src, r.dst, key=r.txn_id, amount=float(r.amount), ts=r.timestamp)
    return G


def _collapse(G: nx.MultiDiGraph) -> nx.DiGraph:
    D = nx.DiGraph()
    for u, v, d in G.edges(data=True):
        if D.has_edge(u, v):
            D[u][v]["amount"] += d["amount"]
            D[u][v]["count"] += 1
        else:
            D.add_edge(u, v, amount=d["amount"], count=1)
    return D


def _max_distinct_in_window(times: np.ndarray, parties: np.ndarray, window: np.timedelta64) -> tuple[int, int, int]:
    """Max distinct counterparties within any sliding window; returns (max, start_idx, end_idx)."""
    best, bi, bj = 0, 0, 0
    j = 0
    counts: dict = defaultdict(int)
    for i in range(len(times)):
        counts[parties[i]] += 1
        while times[i] - times[j] > window:
            counts[parties[j]] -= 1
            if counts[parties[j]] == 0:
                del counts[parties[j]]
            j += 1
        if len(counts) > best:
            best, bi, bj = len(counts), j, i
    return best, bi, bj


def _temporal_cycles(df: pd.DataFrame) -> dict[str, list[list[str]]]:
    """Cycles (len<=5) whose edges occur in time order within 72h, on significant edges."""
    sig = df[df["amount"] >= RULES["cycle_min_amount"]]
    D = nx.DiGraph()
    times: dict[tuple, list] = defaultdict(list)
    for r in sig.itertuples(index=False):
        D.add_edge(r.src, r.dst)
        times[(r.src, r.dst)].append(r.timestamp)
    for k in times:
        times[k].sort()
    found: dict[str, list[list[str]]] = defaultdict(list)
    max_h = pd.Timedelta(hours=RULES["cycle_max_hours"])
    n = 0
    for cyc in nx.simple_cycles(D, length_bound=RULES["cycle_max_len"]):
        n += 1
        if n > 20000:
            break
        if len(cyc) < 3:
            continue
        # try each rotation as the start, greedily follow earliest later timestamp
        for rot in range(len(cyc)):
            path = cyc[rot:] + cyc[:rot]
            ok = True
            for t0 in times[(path[0], path[1])]:
                t = t0
                ok = True
                for a, b in zip(path[1:], path[2:] + path[:1]):
                    nxt = [x for x in times[(a, b)] if x >= t]
                    if not nxt or nxt[0] - t0 > max_h:
                        ok = False
                        break
                    t = nxt[0]
                if ok:
                    break
            if ok:
                for node in cyc:
                    if len(found[node]) < 3:
                        found[node].append(path)
                break
    return found


@dataclass
class AnalysisResult:
    features: pd.DataFrame          # index = account
    evidence: dict[str, dict]       # account -> rule evidence
    graph: nx.MultiDiGraph


def compute_features(df: pd.DataFrame) -> AnalysisResult:
    G = build_graph(df)
    D = _collapse(G)
    pr = nx.pagerank(D, weight="amount") if D.number_of_nodes() else {}
    clust = nx.clustering(D.to_undirected()) if D.number_of_nodes() else {}
    cycles = _temporal_cycles(df)

    period_start = df["timestamp"].min()
    rows = {}
    evidence: dict[str, dict] = {}
    inc = {k: g for k, g in df.groupby("dst")}
    out = {k: g for k, g in df.groupby("src")}
    accounts = sorted(set(df["src"]) | set(df["dst"]))
    day = np.timedelta64(24, "h")

    for acct in accounts:
        gi = inc.get(acct, df.iloc[0:0])
        go = out.get(acct, df.iloc[0:0])
        ev: dict = {"rules": {}}
        in_amt, out_amt = float(gi["amount"].sum()), float(go["amount"].sum())

        # fan-in / fan-out within 24h
        fi, fi_s, fi_e = (0, 0, 0)
        if len(gi):
            fi, fi_s, fi_e = _max_distinct_in_window(gi["timestamp"].values, gi["src"].values, day)
        fo, fo_s, fo_e = (0, 0, 0)
        if len(go):
            fo, fo_s, fo_e = _max_distinct_in_window(go["timestamp"].values, go["dst"].values, day)
        if fi >= RULES["fan_in_min_senders_24h"]:
            w = gi.iloc[fi_s : fi_e + 1]
            ev["rules"]["fan_in"] = {
                "distinct_senders": int(fi), "transfers": len(w), "total": round(float(w["amount"].sum()), 2),
                "senders": sorted(w["src"].unique().tolist())[:30],
                "window_start": str(w["timestamp"].min()), "window_end": str(w["timestamp"].max()),
            }
        if fo >= RULES["fan_out_min_receivers_24h"]:
            w = go.iloc[fo_s : fo_e + 1]
            ev["rules"]["fan_out"] = {
                "distinct_receivers": int(fo), "transfers": len(w), "total": round(float(w["amount"].sum()), 2),
                "receivers": sorted(w["dst"].unique().tolist())[:30],
                "window_start": str(w["timestamp"].min()), "window_end": str(w["timestamp"].max()),
            }

        # pass-through: for each credit, how much leaves within N minutes
        holds = []
        passed_fast = 0.0
        if len(gi) and len(go):
            ot = go["timestamp"].values
            left = go["amount"].values.astype(float).copy()  # each outflow can only be "forwarding" once (FIFO)
            win = np.timedelta64(RULES["pass_through_max_minutes"], "m")
            for t, a in zip(gi["timestamp"].values, gi["amount"].values):
                idx = np.searchsorted(ot, t)
                if idx < len(ot):
                    holds.append((ot[idx] - t) / np.timedelta64(1, "m"))
                j = np.searchsorted(ot, t + win, side="right")
                need = float(a)
                for k in range(idx, j):
                    if need <= 0:
                        break
                    take = min(need, left[k])
                    left[k] -= take
                    need -= take
                    passed_fast += take
        pt_ratio = min(out_amt, in_amt) / in_amt if in_amt else 0.0
        fast_ratio = min(passed_fast, out_amt, in_amt) / in_amt if in_amt else 0.0
        med_hold = float(np.median(holds)) if holds else 1e5
        if in_amt >= 20_000 and fast_ratio >= RULES["pass_through_ratio"]:
            ev["rules"]["rapid_pass_through"] = {
                "received": round(in_amt, 2), "forwarded_within_60m_share": round(fast_ratio, 3),
                "median_hold_minutes": round(med_hold, 1),
            }

        # velocity & dormancy
        allt = pd.concat([gi["timestamp"], go["timestamp"]]).sort_values()
        daily = allt.dt.floor("D").value_counts()
        vel = float(daily.max() / max(1.0, daily.median())) if len(daily) else 0.0
        if vel >= RULES["velocity_ratio"] and daily.max() >= 10:
            ev["rules"]["velocity_spike"] = {"max_txns_in_a_day": int(daily.max()), "median_daily_txns": float(daily.median()), "ratio": round(vel, 1)}
        dormant = 0.0
        if len(allt) >= 2:
            gaps = allt.diff().dt.total_seconds().div(86400).fillna(0).values
            tv = allt.values
            for k in range(1, len(tv)):
                if gaps[k] >= RULES["dormant_days"]:
                    burst = np.sum((tv >= tv[k]) & (tv <= tv[k] + np.timedelta64(48, "h")))
                    if burst >= RULES["dormant_burst_txns_48h"]:
                        dormant = max(dormant, float(gaps[k]))
                        ev["rules"]["dormant_activation"] = {
                            "inactive_days": round(float(gaps[k]), 1), "txns_in_next_48h": int(burst),
                            "reactivated_at": str(pd.Timestamp(tv[k])),
                        }
            first_gap = (allt.iloc[0] - period_start).total_seconds() / 86400
            if first_gap >= RULES["dormant_days"]:
                burst = int(np.sum(tv <= tv[0] + np.timedelta64(48, "h")))
                if burst >= RULES["dormant_burst_txns_48h"]:
                    dormant = max(dormant, first_gap)
                    ev["rules"]["dormant_activation"] = {"inactive_days": round(first_gap, 1), "txns_in_next_48h": burst, "reactivated_at": str(allt.iloc[0])}

        # structuring: several transfers just below the reporting-style threshold
        struct_max = 0
        both = pd.concat([gi, go])
        near = both[(both["amount"] >= RULES["structuring_low"]) & (both["amount"] < RULES["structuring_high"])].sort_values("timestamp")
        if len(near):
            tv = near["timestamp"].values
            j = 0
            for i in range(len(tv)):
                while tv[i] - tv[j] > day:
                    j += 1
                struct_max = max(struct_max, i - j + 1)
            if struct_max >= RULES["structuring_min_count_24h"]:
                ev["rules"]["amount_splitting"] = {"transfers_45k_to_50k_in_24h": int(struct_max), "total_near_threshold": round(float(near["amount"].sum()), 2)}

        if acct in cycles:
            ev["rules"]["circular_flow"] = {"cycles": cycles[acct]}

        hours = allt.dt.hour
        rows[acct] = {
            "in_count": len(gi), "out_count": len(go),
            "in_degree": gi["src"].nunique(), "out_degree": go["dst"].nunique(),
            "in_amount": in_amt, "out_amount": out_amt,
            "pass_through_ratio": fast_ratio if in_amt else 0.0,
            "median_hold_minutes": min(med_hold, 1e5),
            "max_senders_24h": fi, "max_receivers_24h": fo,
            "velocity_ratio": vel, "dormant_days_before_burst": dormant,
            "structuring_count_24h": struct_max, "in_temporal_cycle": int(acct in cycles),
            "pagerank": pr.get(acct, 0.0), "clustering": clust.get(acct, 0.0),
            "active_days": int(len(daily)),
            "avg_in_amount": in_amt / len(gi) if len(gi) else 0.0,
            "avg_out_amount": out_amt / len(go) if len(go) else 0.0,
            "night_share": float(((hours < 6) | (hours >= 23)).mean()) if len(hours) else 0.0,
        }
        ev["pass_through_ratio_overall"] = round(pt_ratio, 3)
        evidence[acct] = ev

    # --- MuleGuard-derived pattern detectors (see mule/patterns.py) ---
    from . import patterns
    for chain in patterns.shell_chains(df):
        for a in chain:
            sc = evidence[a]["rules"].setdefault("shell_chain", {"chains": [], "stage": None})
            if len(sc["chains"]) < 3:
                sc["chains"].append(chain)
            if a in chain[1:-1]:
                sc["stage"] = f"stage{min(3, chain.index(a))}" + ("+" if chain.index(a) >= 3 else "")
            elif sc["stage"] is None:
                sc["stage"] = "origin" if a == chain[0] else "exit"
    for a, d in patterns.smurfing(df).items():
        for direction, info in d.items():
            evidence[a]["rules"][f"smurfing_fan_{direction}"] = info
    for a in patterns.high_volume_merchants(df):
        evidence[a]["high_volume_merchant"] = True
    for a, ev in evidence.items():
        ev["pattern_score"] = patterns.account_pattern_score(sorted(ev["rules"]))

    feats = pd.DataFrame.from_dict(rows, orient="index")[FEATURE_COLUMNS]
    feats.index.name = "account"
    return AnalysisResult(features=feats, evidence=evidence, graph=G)


def rule_families(ev: dict) -> list[str]:
    """Independent rule families. Smurfing in/out are variants of fan-in/out, so they
    count once together with their fan-in/out counterpart."""
    names = set(ev.get("rules", {}).keys())
    if "smurfing_fan_in" in names and "fan_in" in names:
        names.discard("smurfing_fan_in")
    if "smurfing_fan_out" in names and "fan_out" in names:
        names.discard("smurfing_fan_out")
    return sorted(names)


def explain(acct: str, ev: dict) -> list[str]:
    """Plain-English, evidence-linked sentences (deterministic, no LLM)."""
    out = []
    r = ev.get("rules", {})
    if "fan_in" in r:
        x = r["fan_in"]
        out.append(f"Account {acct} received {x['transfers']} incoming transfers from {x['distinct_senders']} distinct accounts "
                   f"between {x['window_start']} and {x['window_end']} (total ₹{x['total']:,.0f}).")
    if "rapid_pass_through" in r:
        x = r["rapid_pass_through"]
        out.append(f"{x['forwarded_within_60m_share']*100:.0f}% of the ₹{x['received']:,.0f} received was transferred out within 60 minutes "
                   f"(median holding time {x['median_hold_minutes']:.0f} min).")
    if "fan_out" in r:
        x = r["fan_out"]
        out.append(f"It sent {x['transfers']} transfers to {x['distinct_receivers']} distinct accounts within 24 hours (total ₹{x['total']:,.0f}).")
    if "circular_flow" in r:
        c = r["circular_flow"]["cycles"][0]
        out.append(f"Funds moved in a time-ordered cycle: {' → '.join(c + [c[0]])} within 72 hours.")
    if "amount_splitting" in r:
        x = r["amount_splitting"]
        out.append(f"{x['transfers_45k_to_50k_in_24h']} transfers between ₹45,000 and ₹49,999 occurred within 24 hours, consistent with amount splitting.")
    if "dormant_activation" in r:
        x = r["dormant_activation"]
        out.append(f"The account was inactive for {x['inactive_days']:.0f} days, then made {x['txns_in_next_48h']} transactions within 48 hours of {x['reactivated_at']}.")
    if "velocity_spike" in r:
        x = r["velocity_spike"]
        out.append(f"Peak activity reached {x['max_txns_in_a_day']} transactions in a day versus a median of {x['median_daily_txns']:.0f}.")
    if "shell_chain" in r:
        x = r["shell_chain"]
        c = x["chains"][0]
        out.append(f"It sits in a layered chain {' → '.join(c)} whose intermediate accounts have only 2-3 transactions each (role: {x['stage']}).")
    for d, word in (("in", "from"), ("out", "to")):
        if f"smurfing_fan_{d}" in r:
            x = r[f"smurfing_fan_{d}"]
            out.append(f"Smurfing pattern: {x['transfers']} small transfers (≤ ₹{x['small_amount_cap']:,.0f}) {word} {x['unique_counterparties']} unique accounts within 72 hours.")
    if ev.get("high_volume_merchant"):
        out.append("Note: activity profile matches a high-volume merchant (many counterparties over many days), a common source of false positives.")
    return out
