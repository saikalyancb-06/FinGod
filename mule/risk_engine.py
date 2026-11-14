"""Quantitative risk engine: transaction-, account- and network-level risk, an uncertainty layer and an
investigation-priority score. Pure computation (no API / DB code) on top of detection.analyze().

COMPONENT SCORES (each 0..1, documented so an investigator can audit them)
  Transaction risk  T_i for each transaction i (sender s):
      z_i  = (log a_i - median(log a_s)) / (1.4826 * MAD(log a_s))      robust z-score vs the sender's own history
      T_i  = 1 - prod(1 - c_k)  over signals k:   (noisy-OR: any strong signal raises risk, several add up)
             amount anomaly  c = clip((|z|-2)/4, 0, 1)        (|z|>2 starts to count, |z|>=6 is maximal)
             night (00-05)   c = 0.25
             structuring     c = 0.5 if 45,000 <= a < 50,000
             repeated pair   c = 0.3 if >=3 transfers s->d within 24h
             burst           c = clip((n_1h(s) - 3) / 7, 0, 1)   transfers by s in the surrounding hour
      account T = mean of its top-3 transaction scores
  Account risk  A = noisy-OR of: rapid pass-through (forwarded share), fan-in / fan-out (distinct counterparties / 20),
                    dormant activation, velocity spike, amount splitting  (from the rule evidence)
  Network risk  N = noisy-OR of: betweenness percentile (top 5% -> up to 0.6), cycle / layered chain membership,
                    flagged neighbours within 2 hops (n/5), member of a suspicious community (0.4)
  ML            M = isotonic-calibrated probability from the IBM-trained XGBoost (only when the model applies)

COMPOSITE  R = 100 * sum_c w_c * x_c / sum_c w_c   over available components
  Default weights  ML 0.35 | account 0.25 | network 0.25 | transaction 0.15
  Rationale: ML is the only component validated against labels (PR-AUC on held-out worlds), so it carries the most
  weight; account and network evidence are the classic mule typologies (equal weight); single-transaction anomalies
  are the noisiest signal, so the least weight. Weights are configurable per dataset.

UNCERTAINTY (separate from risk)
  probability      calibrated P(mule)                         - isotonic regression on unseen INR scenarios
  conformal set    {mule} / {normal} / both / none            - split conformal, 90% target coverage
  out-of-distrib.  isolation-forest score vs training accounts - unusual feature vectors are not trusted
  data sufficiency transactions involving the account          - < 3: insufficient, 3-9: limited, >= 10: sufficient
  evidence strength independent rule families + network corroboration (0..1)
  confidence = high only if the conformal set is a single label, the account is in-distribution, data is at least
  limited, and ML and rules agree; otherwise low.

PRIORITY  P = 100 * (0.35 severity + 0.20 exposure + 0.15 linked + 0.10 centrality + 0.10 velocity + 0.10 evidence)
  exposure = log(1 + money through the account) / log(1 + dataset max); linked = min(flagged within 2 hops, 10)/10
"""
from __future__ import annotations

import math
from functools import lru_cache

import joblib
import networkx as nx
import numpy as np
import pandas as pd

from shared.config import ARTIFACT_DIR

DEFAULT_WEIGHTS = {"ml": 0.35, "account": 0.25, "network": 0.25, "transaction": 0.15}
PRIORITY_WEIGHTS = {"severity": 0.35, "exposure": 0.20, "linked": 0.15, "centrality": 0.10, "velocity": 0.10, "evidence": 0.10}


@lru_cache(maxsize=1)
def uncertainty_model():
    p = ARTIFACT_DIR / "mule_uncertainty.joblib"
    return joblib.load(p) if p.exists() else None


def _noisy_or(xs) -> float:
    prod = 1.0
    for x in xs:
        prod *= 1 - min(max(float(x), 0.0), 1.0)
    return 1 - prod


# ---------------- transaction level ----------------
def transaction_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Per-transaction risk with its signals. df: txn_id, timestamp, src, dst, amount (aliases)."""
    t = df[["txn_id", "timestamp", "src", "dst", "amount"]].copy().sort_values("timestamp").reset_index(drop=True)
    la = np.log(t["amount"].clip(lower=1.0))
    g = la.groupby(t["src"])
    med = g.transform("median")
    mad = (la - med).abs().groupby(t["src"]).transform("median")
    cnt = g.transform("size")
    scale = 1.4826 * mad.where(mad > 0, 0.25)  # floor for accounts with near-constant amounts
    z = ((la - med) / scale).where(cnt >= 4, 0.0)  # no baseline with < 4 sends
    c_amt = ((z.abs() - 2) / 4).clip(0, 1)
    hour = t["timestamp"].dt.hour
    c_night = np.where(hour < 5, 0.25, 0.0)
    c_struct = np.where((t["amount"] >= 45_000) & (t["amount"] < 50_000), 0.5, 0.0)
    ts = t["timestamp"].astype("int64") // 10 ** 9
    pair = t["src"] + "→" + t["dst"]
    rep = np.zeros(len(t))
    burst = np.zeros(len(t))
    for key, idx in t.groupby(pair).groups.items():
        if len(idx) >= 3:
            s = ts[idx].to_numpy()
            n24 = np.searchsorted(s, s + 86400, side="right") - np.searchsorted(s, s - 86400, side="left")
            rep[idx] = np.where(n24 >= 3, 0.3, 0.0)
    for key, idx in t.groupby("src").groups.items():
        if len(idx) >= 4:
            s = ts[idx].to_numpy()
            n1h = np.searchsorted(s, s + 1800, side="right") - np.searchsorted(s, s - 1800, side="left")
            burst[idx] = np.clip((n1h - 3) / 7, 0, 1)
    sig = np.vstack([c_amt.to_numpy(), c_night, c_struct, rep, burst]).T
    t["risk"] = 1 - np.prod(1 - sig, axis=1)
    t["z"] = z.round(2)
    t["signals"] = [[n for n, v in zip(("unusual amount", "night-time", "structuring band", "repeated pair", "burst"), row) if v > 0] for row in sig]
    return t


# ---------------- network level ----------------
def network_metrics(G: nx.MultiDiGraph, flagged: set[str]) -> dict:
    U = nx.Graph()
    for u, v, d in G.edges(data=True):
        w = U[u][v]["weight"] + d.get("amount", 1) if U.has_edge(u, v) else d.get("amount", 1)
        U.add_edge(u, v, weight=w)
    n = U.number_of_nodes()
    if n == 0:
        return {"betweenness": {}, "community": {}, "communities": [], "deg": {}}
    k = min(n, 300)
    bc = nx.betweenness_centrality(U, k=k if k < n else None, seed=7, normalized=True)
    try:
        comms = nx.community.louvain_communities(U, weight="weight", seed=7)
    except Exception:
        comms = [set(c) for c in nx.connected_components(U)]
    comm_of, out = {}, []
    for i, c in enumerate(comms):
        for a in c:
            comm_of[a] = i
        fl = c & flagged
        sub = U.subgraph(c)
        out.append({"id": i, "size": len(c), "flagged": len(fl), "flagged_share": round(len(fl) / len(c), 3),
                    "flow": round(sum(d["weight"] for _, _, d in sub.edges(data=True)), 2),
                    "density": round(nx.density(sub), 4) if len(c) > 1 else 0.0,
                    "members": sorted(c)[:200]})
    for c in out:
        c["suspicious"] = c["flagged"] >= 2 or (c["flagged"] >= 1 and c["size"] <= 6)
    deg = dict(U.degree())
    return {"betweenness": bc, "community": comm_of, "communities": out, "deg": deg}


# ---------------- account profile ----------------
def profiles(df: pd.DataFrame, out: dict, weights: dict | None = None) -> dict[str, dict]:
    """Risk components, composite, uncertainty and priority for every account in an analyze() result."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    feats, ev, scores, thr = out["features"], out["evidence"], out["scores"], out["threshold"]
    applicable = out.get("model_applicable", True)
    flagged = {a["account"] for a in out["alerts"]}
    tx = transaction_scores(df)
    t_acc = tx.groupby("src")["risk"].apply(lambda s: float(np.mean(np.sort(s.to_numpy())[-3:])))
    net = network_metrics(out["graph"], flagged)
    bc = net["betweenness"]
    bc_vals = np.array(list(bc.values())) if bc else np.array([0.0])
    bc_p95 = float(np.quantile(bc_vals, 0.95)) if len(bc_vals) else 0.0
    susp_comm = {c["id"] for c in net["communities"] if c["suspicious"]}
    U = nx.Graph(out["graph"].to_undirected()) if out["graph"].number_of_nodes() else nx.Graph()
    flow = pd.concat([df.groupby("src")["amount"].sum(), df.groupby("dst")["amount"].sum()], axis=1).fillna(0).sum(axis=1)
    max_flow = float(flow.max()) if len(flow) else 1.0
    n_tx = pd.concat([df["src"], df["dst"]]).value_counts()
    um = uncertainty_model()
    accts = list(feats.index)
    calib = {a: None for a in accts}
    conf_set, ood_flag = {}, {}
    if um and applicable:
        from .model_features import MODEL_FEATURES, compute as mf_compute
        mf = mf_compute(df).reindex(accts).fillna(0)
        X = mf[MODEL_FEATURES].to_numpy(np.float32)
        raw = np.array([scores[a] for a in accts])
        p = um["isotonic"].predict(raw)
        q = 1 - um["qhat"]
        od = um["ood"].score_samples(X) < um["ood_cut"]
        for a, pa, o in zip(accts, p, od):
            calib[a] = float(pa)
            s = [lab for lab, ok in (("mule", pa >= q), ("normal", 1 - pa >= q)) if ok]
            conf_set[a] = s
            ood_flag[a] = bool(o)
    res = {}
    for a in accts:
        e = ev[a]
        r = e.get("rules", {})
        # account components
        pt = r.get("rapid_pass_through", {}).get("forwarded_within_60m_share", 0)
        fi = r.get("fan_in", {}).get("distinct_senders", 0) / 20
        fo = r.get("fan_out", {}).get("distinct_receivers", 0) / 20
        comp_a = {"rapid pass-through": pt, "fan-in": min(fi, 1), "fan-out": min(fo, 1),
                  "dormant activation": 0.6 if "dormant_activation" in r else 0, "velocity spike": 0.4 if "velocity_spike" in r else 0,
                  "amount splitting": 0.5 if "amount_splitting" in r else 0}
        A = _noisy_or(comp_a.values())
        # network components
        near = [x for x in nx.single_source_shortest_path_length(U, a, cutoff=2) if x in flagged and x != a] if a in U else []
        b = float(bc.get(a, 0.0))
        comp_n = {"broker position (betweenness)": min(0.6, 0.6 * b / bc_p95) if bc_p95 > 0 and b > 0 else 0,
                  "cycle / layered chain": 0.6 if ({"circular_flow", "shell_chain"} & set(r)) else 0,
                  "flagged neighbours": min(len(near) / 5, 1), "suspicious community": 0.4 if net["community"].get(a) in susp_comm else 0}
        N = _noisy_or(comp_n.values())
        T = float(t_acc.get(a, 0.0))
        M = calib[a] if calib[a] is not None else (float(scores[a]) if applicable else None)
        comps = {"ml": M, "account": A, "network": N, "transaction": T}
        avail = {k: v for k, v in comps.items() if v is not None}
        wsum = sum(w[k] for k in avail)
        contrib = {k: round(100 * w[k] * v / wsum, 2) for k, v in avail.items()}
        R = round(sum(contrib.values()), 1)
        # uncertainty
        ntx = int(n_tx.get(a, 0))
        suff = "insufficient" if ntx < 3 or not applicable and ntx < 5 else "limited" if ntx < 10 else "sufficient"
        fams = len([k for k in r if k not in ("smurfing_fan_in",)])
        strength = round(min(1.0, 0.25 * fams + 0.25 * (comp_n["cycle / layered chain"] > 0) + 0.1 * min(len(near), 3)), 2)
        agree = M is None or ((M >= 0.5) == (A >= 0.4 or N >= 0.4) or fams >= 2)
        cs = conf_set.get(a)
        single = cs is not None and len(cs) == 1
        conf = "high" if (single and not ood_flag.get(a) and suff != "insufficient" and agree) else "low"
        if cs is None and applicable is False:
            conf = "high" if (suff == "sufficient" and strength >= 0.5) else "low"
        tier = "high" if R >= 60 else "moderate" if R >= 35 else "low"
        label = ("Insufficient evidence" if suff == "insufficient" else
                 f"High risk / {conf} confidence" if tier == "high" else "Moderate risk" if tier == "moderate" else "Low risk")
        missing = []
        if not applicable:
            missing.append("ML model not used: dataset too small for its dataset-relative features")
        if suff != "sufficient":
            missing.append(f"only {ntx} transactions involve this account")
        if ood_flag.get(a):
            missing.append("account behaviour is unlike anything in the training data (out-of-distribution)")
        if cs is not None and len(cs) != 1:
            missing.append("the calibrated model cannot separate mule from normal at 90% coverage (ambiguous)")
        missing.append("no KYC, device, IP or customer-contact data in this dataset")
        # priority
        exposure = math.log1p(float(flow.get(a, 0))) / math.log1p(max_flow) if max_flow > 0 else 0
        fr = feats.loc[a]
        vel = min(1.0, max(float(fr.get("max_senders_24h", 0) or 0), float(fr.get("max_receivers_24h", 0) or 0)) / 20)
        pr_parts = {"severity": R / 100, "exposure": exposure, "linked": min(len(near), 10) / 10,
                    "centrality": min(1.0, b / bc_p95) if bc_p95 > 0 else 0, "velocity": vel, "evidence": strength}
        P = round(100 * sum(PRIORITY_WEIGHTS[k] * v for k, v in pr_parts.items()), 1)
        res[a] = {
            "composite": R, "tier": tier, "label": label, "contributions": contrib,
            "components": {"ml": None if M is None else round(M, 4), "account": round(A, 4), "network": round(N, 4), "transaction": round(T, 4)},
            "account_factors": {k: round(v, 3) for k, v in comp_a.items() if v},
            "network_factors": {k: round(v, 3) for k, v in comp_n.items() if v},
            "uncertainty": {"probability": None if calib[a] is None else round(calib[a], 4), "raw_model_score": round(float(scores[a]), 4),
                            "conformal_set": cs, "out_of_distribution": ood_flag.get(a), "data_sufficiency": suff, "transactions": ntx,
                            "evidence_strength": strength, "confidence": conf, "ml_rules_agree": bool(agree),
                            "calibrated": calib[a] is not None},
            "missing_information": missing,
            "priority": P, "priority_parts": {k: round(v, 3) for k, v in pr_parts.items()},
            "network": {"betweenness": round(b, 5), "community": net["community"].get(a), "flagged_within_2_hops": sorted(near)[:20],
                        "degree": int(net["deg"].get(a, 0))},
            "exposure": round(float(flow.get(a, 0)), 2),
        }
    return {"accounts": res, "communities": net["communities"], "weights": w, "priority_weights": PRIORITY_WEIGHTS,
            "uncertainty_model": (um or {}).get("version"), "transactions": tx}


# ---------------- money trail ----------------
def trace(df: pd.DataFrame, account: str, direction: str = "down", hops: int = 3, start=None, end=None, min_amount: float = 0.0,
          max_edges: int = 400) -> dict:
    """Time-respecting multi-hop trace. Downstream: from `account`, follow transfers that happen AFTER the money
    arrived at each hop (upstream: the mirror image). Shows transaction relationships, not proof that specific
    rupees moved (balances are commingled)."""
    t = df[(df["amount"] >= min_amount)].copy()
    if start is not None:
        t = t[t["timestamp"] >= pd.Timestamp(start)]
    if end is not None:
        t = t[t["timestamp"] <= pd.Timestamp(end)]
    t = t.sort_values("timestamp")
    out_by = {k: g for k, g in t.groupby("src")}
    in_by = {k: g for k, g in t.groupby("dst")}
    edges, seen = [], set()
    frontier = [(account, pd.Timestamp.min if direction != "up" else pd.Timestamp.max, 0)]
    level = {account: 0}
    while frontier and len(edges) < max_edges:
        nxt = []
        for node, t0, h in frontier:
            if h >= hops:
                continue
            if direction in ("down", "both"):
                g = out_by.get(node)
                if g is not None:
                    for r in g[g["timestamp"] >= t0].itertuples():
                        if r.txn_id in seen:
                            continue
                        seen.add(r.txn_id)
                        edges.append({"txn_id": r.txn_id, "from": r.src, "to": r.dst, "amount": round(float(r.amount), 2),
                                      "ts": str(r.timestamp), "hop": h + 1, "dir": "out"})
                        if r.dst not in level:
                            level[r.dst] = h + 1
                            nxt.append((r.dst, r.timestamp, h + 1))
            if direction in ("up", "both"):
                g = in_by.get(node)
                if g is not None:
                    lim = t0 if direction == "up" else pd.Timestamp.max
                    for r in g[g["timestamp"] <= lim].itertuples():
                        if r.txn_id in seen:
                            continue
                        seen.add(r.txn_id)
                        edges.append({"txn_id": r.txn_id, "from": r.src, "to": r.dst, "amount": round(float(r.amount), 2),
                                      "ts": str(r.timestamp), "hop": h + 1, "dir": "in"})
                        if r.src not in level:
                            level[r.src] = h + 1
                            nxt.append((r.src, r.timestamp, h + 1))
        frontier = nxt
    # layering: chains of >= 3 hops where each hop forwards >= 80% of what arrived within 24h
    chains = []
    by_from = {}
    for e in edges:
        by_from.setdefault(e["from"], []).append(e)

    def walk(e, path):
        nxts = [x for x in by_from.get(e["to"], []) if pd.Timestamp(x["ts"]) >= pd.Timestamp(e["ts"])
                and pd.Timestamp(x["ts"]) - pd.Timestamp(e["ts"]) <= pd.Timedelta(hours=24) and x["amount"] >= 0.8 * e["amount"]
                and x["to"] not in [p["from"] for p in path]]
        if not nxts:
            if len(path) >= 3:
                chains.append(path)
            return
        for x in nxts[:3]:
            walk(x, path + [x])

    for e in [e for e in edges if e["from"] == account][:30]:
        walk(e, [e])
    chains = sorted(chains, key=lambda c: -len(c))[:8]
    return {"account": account, "direction": direction, "hops": hops, "edges": edges, "levels": level,
            "accounts": len(level) - 1, "total_amount": round(sum(e["amount"] for e in edges), 2),
            "layering_chains": [{"hops": len(c), "path": [c[0]["from"]] + [x["to"] for x in c],
                                 "amounts": [x["amount"] for x in c], "start": c[0]["ts"], "end": c[-1]["ts"],
                                 "retained_share": round(c[-1]["amount"] / c[0]["amount"], 3),
                                 "txn_ids": [x["txn_id"] for x in c]} for c in chains],
            "truncated": len(edges) >= max_edges,
            "disclaimer": "This trail shows transaction relationships in time order. Because balances mix money from many sources, "
                          "it does not prove that specific rupees moved from one account to the next."}
