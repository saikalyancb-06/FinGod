"""Risk Lab: Monte Carlo simulation of a person's cash flow (uncertainty analysis).

Model (all inputs come from the user's own statement):
  B_{t+1} = B_t - S_t - R_t + I_t
    S_t  day-to-day spending: bootstrap-resampled from the user's own past days (non-recurring debits),
         optionally with wants cut by c%:   S_t = s_t - c * w_t   where w_t is that day's want spending
    R_t  detected recurring bills, on their expected dates (deterministic)
    I_t  income on its usual day of month, amount resampled from past income credits
Outputs over N paths (default 10,000):
  P(run out)       = share of paths whose balance goes below 0 before the horizon
  P(below buffer)  = same for the user's emergency buffer
  VaR_95           = 5th percentile of the end balance;  CVaR_95 = mean of the worst 5% of end balances
  fan chart        = P5/P25/P50/P75/P95 of the balance for every day
  goal odds        = P(saved >= target by the deadline) with monthly saving = min(planned, simulated surplus)
Monte Carlo standard error of a probability p: sqrt(p(1-p)/N).
Assumptions: days are drawn independently (no autocorrelation); history is representative of the future.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import analytics as A


def _daily_history(df: pd.DataFrame, rec_keys: set, nature: pd.Series | None, lookback: int = 90):
    ref = A.as_of(df)
    first = df["date"].min().date()
    start = max(ref - timedelta(days=lookback - 1), first)
    sp = A.spend_df(df)
    sp = sp[(sp["date"].dt.date >= start) & ~sp["merchant_key"].isin(rec_keys)]
    days = pd.date_range(pd.Timestamp(start), pd.Timestamp(ref))
    total = sp.groupby(sp["date"].dt.normalize())["amount"].sum().reindex(days, fill_value=0.0).to_numpy()
    if nature is not None:
        w = sp[nature.reindex(sp.index).fillna("") == "want"]
        wants = w.groupby(w["date"].dt.normalize())["amount"].sum().reindex(days, fill_value=0.0).to_numpy()
    else:
        wants = np.zeros_like(total)
    return total, wants, len(days)


def _income_schedule(df: pd.DataFrame):
    inc = df[(df["type"] == "credit") & (df["credit_kind"] == "income")]
    out = []
    for key, g in inc.groupby("merchant_key"):
        if g["month"].nunique() < 2:
            continue
        out.append({"source": g["merchant"].iloc[-1], "day": int(g["date"].dt.day.median()),
                    "amounts": g.groupby("month")["amount"].sum().to_numpy(dtype=float)})
    return out


def simulate(df: pd.DataFrame, ctx: dict, goals: list, buffer: float, horizon_days: int | None = None, cut_wants_pct: float = 0.0,
             n: int = 10_000, seed: int = 42, nature: pd.Series | None = None) -> dict:
    rng = np.random.default_rng(seed)
    ref = A.as_of(df)
    bal0 = (ctx.get("balance") or {}).get("balance")
    if bal0 is None:
        return {"available": False, "message": "Risk Lab needs your balance: upload a statement with a balance column or set an opening balance."}
    sts = ctx.get("safe_to_spend") or {}
    H = int(horizon_days or max(7, sts.get("days_left") or 30))
    rec = ctx.get("recurring", [])
    rec_keys = {r["merchant_key"] for r in rec}
    hist, wants, n_hist = _daily_history(df, rec_keys, nature)
    if n_hist < 14:
        return {"available": False, "message": f"Only {n_hist} days of history: Risk Lab needs at least 14 days to resample from."}
    cut = max(0.0, min(1.0, cut_wants_pct / 100.0))
    pool = hist - cut * wants
    # day-to-day spending: bootstrap whole days (keeps the real mix of quiet days and big days)
    S = pool[rng.integers(0, len(pool), size=(n, H))]
    days = [ref + timedelta(days=i + 1) for i in range(H)]
    R = np.zeros(H)
    bills = []
    for r in rec:
        step = 7 if r["cadence"] == "weekly" else 30
        d = date.fromisoformat(r["next_expected"])
        while d <= days[-1]:
            if d > ref:
                R[(d - ref).days - 1] += r["typical_amount"]
                bills.append({"date": str(d), "merchant": r["merchant"], "amount": r["typical_amount"]})
            d += timedelta(days=step)
    I = np.zeros((n, H))
    incomes = _income_schedule(df)
    for src in incomes:
        for i, d in enumerate(days):
            if d.day == min(src["day"], 28):
                I[:, i] += rng.choice(src["amounts"], size=n)
    flows = -S - R[None, :] + I
    B = bal0 + np.cumsum(flows, axis=1)
    mins = B.min(axis=1)
    end = B[:, -1]
    p_out = float((mins < 0).mean())
    p_buf = float((mins < buffer).mean())
    se = lambda p: math.sqrt(max(p * (1 - p), 1e-12) / n)  # noqa: E731
    first_neg = np.where((B < 0).any(axis=1), (B < 0).argmax(axis=1), -1)
    neg_days = first_neg[first_neg >= 0]
    var95 = float(np.quantile(end, 0.05))
    tail = end[end <= var95]
    pct = np.quantile(B, [0.05, 0.25, 0.5, 0.75, 0.95], axis=0)
    fan = [{"date": str(days[i]), "p5": round(float(pct[0, i])), "p25": round(float(pct[1, i])), "p50": round(float(pct[2, i])),
            "p75": round(float(pct[3, i])), "p95": round(float(pct[4, i]))} for i in range(H)]
    fan.insert(0, {"date": str(ref), "p5": round(bal0), "p25": round(bal0), "p50": round(bal0), "p75": round(bal0), "p95": round(bal0)})
    out = {
        "available": True, "as_of": str(ref), "horizon_days": H, "paths": n, "seed": seed, "start_balance": round(bal0, 2),
        "p_run_out": round(p_out, 4), "p_run_out_se": round(se(p_out), 4),
        "p_below_buffer": round(p_buf, 4), "buffer": buffer,
        "median_day_run_out": (str(days[int(np.median(neg_days))]) if len(neg_days) else None),
        "end_balance": {"p5": round(var95), "p50": round(float(np.median(end))), "p95": round(float(np.quantile(end, 0.95))),
                        "mean": round(float(end.mean()))},
        "var95": round(var95), "cvar95": round(float(tail.mean())) if len(tail) else round(var95),
        "fan": fan, "bills": bills,
        "inputs": {"history_days": n_hist, "avg_daily_spend": round(float(hist.mean()), 2), "daily_sd": round(float(hist.std()), 2),
                   "share_zero_days": round(float((hist == 0).mean()), 3), "want_share": round(float(wants.sum() / max(hist.sum(), 1)), 3),
                   "incomes": [{"source": s["source"], "day": s["day"], "typical": round(float(np.median(s["amounts"])), 2)} for s in incomes],
                   "cut_wants_pct": cut_wants_pct},
        "goals": goal_odds(df, goals, n=n, seed=seed),
        "assumptions": [
            f"Day-to-day spending is resampled from your last {n_hist} days (bootstrap), so quiet days and big-spend days appear as often as they did.",
            "Detected recurring bills are paid on their expected dates; income arrives on its usual day with amounts drawn from your past income.",
            "Days are treated as independent; a sudden change in your life (new job, move) isn't in the history.",
            f"{n:,} simulated paths; the Monte Carlo error on the run-out probability is ±{se(p_out) * 100:.1f} percentage points.",
        ],
    }
    return out


def goal_odds(df: pd.DataFrame, goals: list, n: int = 10_000, seed: int = 42) -> list[dict]:
    """P(goal reached by its deadline). Each month you save min(planned contribution, that month's surplus),
    where the surplus is resampled from your complete months with day-level noise."""
    rng = np.random.default_rng(seed + 1)
    ms = A.monthly_summary(df)
    full = ms[:-1] if len(ms) > 1 and df["date"].max().day < 25 else ms
    surplus = np.array([m["income"] - m["outflow"] for m in full], dtype=float)
    out = []
    ref = A.as_of(df)
    for g in goals:
        p = g.get("pace") or {}
        if p.get("done") or not g.get("target_date"):
            continue
        td = g["target_date"] if isinstance(g["target_date"], date) else date.fromisoformat(str(g["target_date"]))
        months = max(0, round((td - ref).days / 30.44))
        remaining = float(p.get("remaining") or (g["target"] - g["saved"]))
        planned = float(g.get("monthly_contribution") or 0)
        if months == 0 or len(surplus) == 0:
            out.append({"goal": g["name"], "probability": None, "reason": "deadline too close or no complete months of history"})
            continue
        sd = max(float(surplus.std()), 0.1 * abs(float(surplus.mean())) + 1)
        sims = surplus[rng.integers(0, len(surplus), size=(n, months))] + rng.normal(0, sd * 0.25, size=(n, months))
        saved = np.minimum(np.maximum(sims, 0), planned if planned > 0 else np.inf).sum(axis=1)
        prob = float((saved >= remaining).mean())
        need = remaining / months
        out.append({"goal": g["name"], "probability": round(prob, 4), "months": months, "remaining": round(remaining, 2),
                    "planned_monthly": round(planned, 2), "needed_monthly": round(need, 2),
                    "median_saved": round(float(np.median(saved)), 2),
                    "p_se": round(math.sqrt(max(prob * (1 - prob), 1e-12) / n), 4)})
    return out
