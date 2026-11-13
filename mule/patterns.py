"""Pattern detectors ported from the team's MuleGuard project (reference/muleguard,
Rust/WASM kernels in wasm/src/lib.rs and src/analysis.worker.ts), re-implemented
in Python so they run server-side alongside the rules + ML + graph layers.

  * layered shell chains     - paths of >=3 hops whose intermediates are low-degree
                               "shell" accounts (total degree 2-3) ending at a non-shell
  * smurfing (fan-in/out)    - >=10 unique counterparties within 72h using only
                               small transfers (<= 0.5 x median amount)
  * high-volume merchant FP  - degree >= 200, >= 50 counterparties, >= 20 active days
  * fraud rings + risk score - base 90 cycle / 85 shell chain / 70 smurfing,
                               + min(20, 2 x members), capped at 100
  * account pattern score    - 40 cycle, 35 shell, 25 smurfing, +5 per extra pattern
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

SHELL_MIN_DEG, SHELL_MAX_DEG = 2, 3
SHELL_MAX_DEPTH = 5
SHELL_MAX_PATHS_PER_START = 2000
MAX_NEIGHBORS = 60
SMURF_WINDOW = pd.Timedelta(hours=72)
SMURF_MIN_UNIQUE = 10
SMURF_SMALL_FRACTION = 0.5
MERCHANT_MIN_DEGREE, MERCHANT_MIN_COUNTERPARTIES, MERCHANT_MIN_ACTIVE_DAYS = 200, 50, 20
RING_BASE = {"cycle": 90, "shell_chain": 85, "smurfing_fan_in": 70, "smurfing_fan_out": 70, "fan_in_fan_out": 75}
# MuleGuard's account weights, extended to every rule family so a fan-in / pass-through alert never shows 0/100
ACCOUNT_BASE = {"circular_flow": 40, "shell_chain": 35, "smurfing_fan_in": 25, "smurfing_fan_out": 25,
                "rapid_pass_through": 35, "fan_in": 25, "fan_out": 25, "amount_splitting": 25, "dormant_activation": 20,
                "velocity_spike": 15}


def degrees(df: pd.DataFrame) -> dict[str, int]:
    """Total transaction count per account (in + out), as in MuleGuard."""
    return (df["src"].value_counts().add(df["dst"].value_counts(), fill_value=0)).astype(int).to_dict()


def shell_chains(df: pd.DataFrame) -> list[list[str]]:
    deg = degrees(df)
    adj: dict[str, list[str]] = defaultdict(list)
    for s, d in df[["src", "dst"]].drop_duplicates().itertuples(index=False):
        adj[s].append(d)
    is_shell = lambda a: SHELL_MIN_DEG <= deg.get(a, 0) <= SHELL_MAX_DEG  # noqa: E731
    found: dict[tuple, list[str]] = {}

    for start in list(adj):
        count = [0]
        path = [start]
        visited = {start}

        def dfs(cur: str):
            if len(path) - 1 >= SHELL_MAX_DEPTH or count[0] > SHELL_MAX_PATHS_PER_START:
                return
            for nxt in adj.get(cur, [])[:MAX_NEIGHBORS]:
                if nxt in visited:
                    continue
                count[0] += 1
                path.append(nxt)
                visited.add(nxt)
                if len(path) >= 4 and all(is_shell(v) for v in path[1:-1]) and not is_shell(nxt):
                    found.setdefault(tuple(path), list(path))
                # only shells can be intermediates, so only extend through shells (pruning)
                if is_shell(nxt):
                    dfs(nxt)
                path.pop()
                visited.discard(nxt)

        if adj.get(start):
            dfs(start)
    # keep maximal chains: drop any chain that is a contiguous sub-path of a longer one
    chains = sorted(found.values(), key=len, reverse=True)
    keep: list[list[str]] = []
    for c in chains:
        key = "|".join(c)
        if not any(key in "|".join(k) for k in keep):
            keep.append(c)
    return keep


def smurfing(df: pd.DataFrame) -> dict[str, dict]:
    """Returns {account: {"direction": "in"|"out", "counterparties": [...], ...}}."""
    med = float(df["amount"].median()) if len(df) else 0.0
    small = df[df["amount"] <= SMURF_SMALL_FRACTION * med] if med > 0 else df
    out: dict[str, dict] = {}
    for direction, key, other in (("in", "dst", "src"), ("out", "src", "dst")):
        for acct, g in small.groupby(key):
            if g[other].nunique() < SMURF_MIN_UNIQUE:
                continue
            g = g.sort_values("timestamp")
            t = g["timestamp"].values
            p = g[other].values
            counts: dict = defaultdict(int)
            j = 0
            for i in range(len(t)):
                counts[p[i]] += 1
                while t[i] - t[j] > np.timedelta64(SMURF_WINDOW):
                    counts[p[j]] -= 1
                    if counts[p[j]] == 0:
                        del counts[p[j]]
                    j += 1
                if len(counts) >= SMURF_MIN_UNIQUE:
                    w = g.iloc[j : i + 1]
                    out.setdefault(acct, {})[direction] = {
                        "counterparties": sorted(counts)[:40], "unique_counterparties": len(counts),
                        "transfers": int(len(w)), "total": round(float(w["amount"].sum()), 2),
                        "small_amount_cap": round(SMURF_SMALL_FRACTION * med, 2),
                        "window_start": str(w["timestamp"].min()), "window_end": str(w["timestamp"].max()),
                    }
                    break
    return out


def high_volume_merchants(df: pd.DataFrame) -> set[str]:
    deg = degrees(df)
    cps = pd.concat([df[["src", "dst"]].rename(columns={"src": "a", "dst": "b"}),
                     df[["dst", "src"]].rename(columns={"dst": "a", "src": "b"})]).groupby("a")["b"].nunique().to_dict()
    days = pd.concat([df[["src", "timestamp"]].rename(columns={"src": "a"}), df[["dst", "timestamp"]].rename(columns={"dst": "a"})])
    active = days.assign(d=days["timestamp"].dt.floor("D")).groupby("a")["d"].nunique().to_dict()
    return {a for a, d in deg.items() if d >= MERCHANT_MIN_DEGREE and cps.get(a, 0) >= MERCHANT_MIN_COUNTERPARTIES and active.get(a, 0) >= MERCHANT_MIN_ACTIVE_DAYS}


def ring_risk(pattern: str, members: int) -> int:
    return int(min(100, RING_BASE.get(pattern, 70) + min(20, 2 * members)))


def account_pattern_score(rule_names: list[str]) -> int:
    hits = [ACCOUNT_BASE[r] for r in rule_names if r in ACCOUNT_BASE]
    if not hits:
        return 0
    return int(min(100, max(hits) + 5 * (len(rule_names) - 1)))


def build_rings(evidence: dict[str, dict], flagged: set[str]) -> list[dict]:
    """Group accounts into candidate fraud rings from pattern evidence (deterministic IDs)."""
    cands: dict[tuple, str] = {}
    for acct, ev in evidence.items():
        r = ev.get("rules", {})
        for c in r.get("circular_flow", {}).get("cycles", []):
            cands.setdefault(tuple(sorted(c)), "cycle")
        for c in r.get("shell_chain", {}).get("chains", []):
            cands.setdefault(tuple(sorted(c)), "shell_chain")
        for d in ("in", "out"):
            k = f"smurfing_fan_{d}"
            if k in r:
                cands.setdefault(tuple(sorted([acct, *r[k]["counterparties"]])), k)
        if "fan_in" in r and "fan_out" in r and acct in flagged:
            mem = [acct, *r["fan_out"].get("receivers", [])]  # collector + downstream (senders may be victims)
            cands.setdefault(tuple(sorted(set(mem))), "fan_in_fan_out")
    rings = []
    for i, (members, pattern) in enumerate(sorted(cands.items(), key=lambda kv: (kv[1], "|".join(kv[0]))), start=1):
        rings.append({"ring_id": f"RING_{i:03d}", "pattern_type": pattern, "member_accounts": list(members),
                      "members": len(members), "risk_score": ring_risk(pattern, len(members)),
                      "flagged_members": sorted(set(members) & flagged)})
    return sorted(rings, key=lambda r: (-r["risk_score"], r["ring_id"]))
