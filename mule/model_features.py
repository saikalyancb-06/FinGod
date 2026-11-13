"""Vectorised, scale-free account features for the money-mule ML model.

Used identically for training (IBM AMLworld, tens of millions of transactions)
and for inference in the app (uploaded INR files), so there is no train/serve skew.

Design choices
  * Scale-free: amounts enter only relative to the dataset's median transaction,
    so a model trained on USD-denominated data transfers to INR data.
  * Vectorised with pandas + scipy (no per-account Python loops): ~30M rows fit
    in memory on a 16 GB laptop.
  * Graph features (PageRank, strongly-connected components, reciprocity) use
    scipy.sparse so they scale to millions of accounts.

Input DataFrame columns: timestamp (datetime64), src, dst, amount (float > 0).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.sparse.csgraph import connected_components

MODEL_FEATURES = [
    "log_in_count", "log_out_count", "log_in_degree", "log_out_degree",
    "rel_in_amount", "rel_out_amount", "rel_avg_in", "rel_avg_out", "amount_cv",
    "flow_balance", "fwd_share_1h", "fwd_share_24h", "log_median_hold_h",
    "log_max_daily_in_cps", "log_max_daily_out_cps", "velocity_ratio", "active_days_share",
    "max_gap_days", "night_share", "repeat_ratio", "reciprocity",
    "pagerank_scaled", "log_scc_size", "in_cycle", "fan_ratio",
]


def _pagerank(src_idx: np.ndarray, dst_idx: np.ndarray, w: np.ndarray, n: int, d: float = 0.85, iters: int = 40) -> np.ndarray:
    A = sparse.csr_matrix((w, (src_idx, dst_idx)), shape=(n, n))
    out_w = np.asarray(A.sum(axis=1)).ravel()
    inv = np.divide(1.0, out_w, out=np.zeros_like(out_w), where=out_w > 0)
    P = sparse.diags(inv) @ A  # row-stochastic (dangling rows are zero)
    PT = P.T.tocsr()
    r = np.full(n, 1.0 / n)
    dangling = out_w == 0
    for _ in range(iters):
        r_new = d * (PT @ r) + (d * r[dangling].sum() + (1 - d)) / n
        if np.abs(r_new - r).sum() < 1e-9:
            r = r_new
            break
        r = r_new
    return r


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Return one row per account (index = account id) with MODEL_FEATURES."""
    df = df[["timestamp", "src", "dst", "amount"]].copy()
    df = df[(df["src"] != df["dst"]) & (df["amount"] > 0)]
    accts = pd.Index(pd.unique(np.concatenate([df["src"].to_numpy(), df["dst"].to_numpy()])))
    n = len(accts)
    s = accts.get_indexer(df["src"]).astype(np.int64)
    t = accts.get_indexer(df["dst"]).astype(np.int64)
    amt = df["amount"].to_numpy(dtype=np.float64)
    med = float(np.median(amt)) if len(amt) else 1.0
    ts = df["timestamp"].to_numpy()
    day = (ts.astype("datetime64[D]")).astype(np.int64)
    hour = pd.DatetimeIndex(ts).hour.to_numpy()

    F = pd.DataFrame(index=np.arange(n))
    # ---- counts, degrees, amounts (bincount = fast)
    in_cnt = np.bincount(t, minlength=n)
    out_cnt = np.bincount(s, minlength=n)
    in_amt = np.bincount(t, weights=amt, minlength=n)
    out_amt = np.bincount(s, weights=amt, minlength=n)
    pairs = pd.DataFrame({"s": s, "t": t}).drop_duplicates()
    in_deg = np.bincount(pairs["t"], minlength=n)
    out_deg = np.bincount(pairs["s"], minlength=n)
    F["log_in_count"] = np.log1p(in_cnt)
    F["log_out_count"] = np.log1p(out_cnt)
    F["log_in_degree"] = np.log1p(in_deg)
    F["log_out_degree"] = np.log1p(out_deg)
    F["rel_in_amount"] = np.log1p(in_amt / med)
    F["rel_out_amount"] = np.log1p(out_amt / med)
    F["rel_avg_in"] = np.log1p(np.divide(in_amt, in_cnt, out=np.zeros(n), where=in_cnt > 0) / med)
    F["rel_avg_out"] = np.log1p(np.divide(out_amt, out_cnt, out=np.zeros(n), where=out_cnt > 0) / med)
    # amount coefficient of variation across all of an account's transactions
    both_idx = np.concatenate([s, t])
    both_amt = np.concatenate([amt, amt])
    cnt = np.bincount(both_idx, minlength=n)
    sm = np.bincount(both_idx, weights=both_amt, minlength=n)
    sq = np.bincount(both_idx, weights=both_amt ** 2, minlength=n)
    mean = np.divide(sm, cnt, out=np.zeros(n), where=cnt > 0)
    var = np.maximum(np.divide(sq, cnt, out=np.zeros(n), where=cnt > 0) - mean ** 2, 0)
    F["amount_cv"] = np.divide(np.sqrt(var), mean, out=np.zeros(n), where=mean > 0)
    hi = np.maximum(in_amt, out_amt)
    F["flow_balance"] = np.divide(np.minimum(in_amt, out_amt), hi, out=np.zeros(n), where=hi > 0)

    # ---- pass-through timing: for each incoming txn, the next outgoing txn of the same account
    inc = pd.DataFrame({"acct": t, "ts": ts, "amt": amt}).sort_values("ts", kind="mergesort")
    outg = pd.DataFrame({"acct": s, "ts_out": ts}).sort_values("ts_out", kind="mergesort")
    m = pd.merge_asof(inc, outg, left_on="ts", right_on="ts_out", by="acct", direction="forward", allow_exact_matches=True)
    gap_h = (m["ts_out"] - m["ts"]).dt.total_seconds().to_numpy() / 3600.0
    acc = m["acct"].to_numpy()
    a_amt = m["amt"].to_numpy()
    w1 = np.where(gap_h <= 1.0, a_amt, 0.0)
    w24 = np.where(gap_h <= 24.0, a_amt, 0.0)
    F["fwd_share_1h"] = np.divide(np.bincount(acc, weights=w1, minlength=n), in_amt, out=np.zeros(n), where=in_amt > 0)
    F["fwd_share_24h"] = np.divide(np.bincount(acc, weights=w24, minlength=n), in_amt, out=np.zeros(n), where=in_amt > 0)
    gaps = pd.Series(gap_h).fillna(24 * 60.0)  # never forwarded -> 60 days
    med_gap = gaps.groupby(acc).median().reindex(np.arange(n)).fillna(24 * 60.0).to_numpy()
    F["log_median_hold_h"] = np.log1p(med_gap)

    # ---- daily behaviour
    d_in = pd.DataFrame({"a": t, "d": day, "cp": s}).drop_duplicates()
    d_out = pd.DataFrame({"a": s, "d": day, "cp": t}).drop_duplicates()
    max_in_cps = d_in.groupby(["a", "d"]).size().groupby(level=0).max().reindex(np.arange(n)).fillna(0).to_numpy()
    max_out_cps = d_out.groupby(["a", "d"]).size().groupby(level=0).max().reindex(np.arange(n)).fillna(0).to_numpy()
    F["log_max_daily_in_cps"] = np.log1p(max_in_cps)
    F["log_max_daily_out_cps"] = np.log1p(max_out_cps)
    act = pd.DataFrame({"a": both_idx, "d": np.concatenate([day, day])})
    per_day = act.groupby(["a", "d"]).size()
    g = per_day.groupby(level=0)
    max_day = g.max().reindex(np.arange(n)).fillna(0).to_numpy()
    med_day = g.median().reindex(np.arange(n)).fillna(1).to_numpy()
    active = g.size().reindex(np.arange(n)).fillna(0).to_numpy()
    F["velocity_ratio"] = max_day / np.maximum(med_day, 1)
    span_days = max(1, int(day.max() - day.min()) + 1) if len(day) else 1
    F["active_days_share"] = active / span_days
    days_sorted = per_day.reset_index()[["a", "d"]]
    gap_d = days_sorted.groupby("a")["d"].diff()
    F["max_gap_days"] = gap_d.groupby(days_sorted["a"]).max().reindex(np.arange(n)).fillna(0).to_numpy()
    night = ((hour < 6) | (hour >= 23)).astype(float)
    F["night_share"] = np.divide(np.bincount(both_idx, weights=np.concatenate([night, night]), minlength=n), cnt, out=np.zeros(n), where=cnt > 0)
    deg_tot = in_deg + out_deg
    F["repeat_ratio"] = np.divide(cnt, deg_tot, out=np.zeros(n), where=deg_tot > 0)

    # ---- graph structure
    key = pairs["s"].to_numpy() * n + pairs["t"].to_numpy()
    rev = pairs["t"].to_numpy() * n + pairs["s"].to_numpy()
    mutual = np.isin(rev, key)
    mutual_cnt = np.bincount(pairs["s"].to_numpy()[mutual], minlength=n)
    F["reciprocity"] = np.divide(mutual_cnt, np.maximum(out_deg, 1), out=np.zeros(n), where=out_deg > 0)
    pw = pd.DataFrame({"s": s, "t": t, "a": amt}).groupby(["s", "t"], sort=False)["a"].sum().reset_index()
    pr = _pagerank(pw["s"].to_numpy(), pw["t"].to_numpy(), pw["a"].to_numpy(), n) if n else np.zeros(0)
    F["pagerank_scaled"] = np.log1p(pr * n)
    G = sparse.csr_matrix((np.ones(len(pairs)), (pairs["s"].to_numpy(), pairs["t"].to_numpy())), shape=(n, n))
    _, lab = connected_components(G, directed=True, connection="strong")
    scc_size = np.bincount(lab)[lab]
    F["log_scc_size"] = np.log1p(np.minimum(scc_size, 1000))
    F["in_cycle"] = (scc_size > 1).astype(float)
    F["fan_ratio"] = np.divide(in_deg - out_deg, np.maximum(deg_tot, 1), out=np.zeros(n), where=deg_tot > 0)

    F.index = accts
    F.index.name = "account"
    return F[MODEL_FEATURES].astype(np.float32)
