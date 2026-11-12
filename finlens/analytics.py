"""FinLens analytics: all financial numbers are calculated here, deterministically.
The LLM never calculates; it only explains values returned by these functions.
"""
from __future__ import annotations

import calendar
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd


def to_df(txns: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(txns)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M").astype(str)
    return df


def spend_df(df: pd.DataFrame) -> pd.DataFrame:
    """Debits that count as spending (not reversed, not own-account transfers)."""
    if df.empty:
        return df
    m = (df["type"] == "debit") & (~df["excluded"]) & (df["subcategory"].fillna("") != "Own-account transfer")
    return df[m]


def as_of(df: pd.DataFrame) -> date:
    return df["date"].max().date()


def monthly_summary(df: pd.DataFrame) -> list[dict]:
    out = []
    for m, g in df.groupby("month"):
        cr = g[(g["type"] == "credit") & (~g["excluded"])]
        sp = spend_df(g)
        income = cr[cr["credit_kind"] == "income"]["amount"].sum()
        out.append({
            "month": m,
            "inflow_total": round(cr["amount"].sum(), 2),
            "income": round(income, 2),
            "other_inflows": {k: round(v, 2) for k, v in cr[cr["credit_kind"] != "income"].groupby("credit_kind")["amount"].sum().items()},
            "outflow": round(sp["amount"].sum(), 2),
            "essential": round(sp[sp["is_essential"]]["amount"].sum(), 2),
            "discretionary": round(sp[~sp["is_essential"]]["amount"].sum(), 2),
            "net": round(cr["amount"].sum() - sp["amount"].sum(), 2),
            "income_minus_spending": round(income - sp["amount"].sum(), 2),
            "transactions": int(len(g)),
        })
    return out


def category_breakdown(df: pd.DataFrame, month: str | None = None) -> list[dict]:
    sp = spend_df(df)
    if month:
        sp = sp[sp["month"] == month]
    total = sp["amount"].sum()
    rows = []
    for cat, g in sp.groupby("category"):
        rows.append({"category": cat, "total": round(g["amount"].sum(), 2), "count": int(len(g)),
                     "share_pct": round(100 * g["amount"].sum() / total, 1) if total else 0.0,
                     "avg_txn": round(g["amount"].mean(), 2)})
    return sorted(rows, key=lambda r: -r["total"])


def subcategory_breakdown(df: pd.DataFrame, month: str, category: str) -> list[dict]:
    sp = spend_df(df)
    sp = sp[(sp["month"] == month) & (sp["category"] == category)]
    return [{"subcategory": k or "Other", "total": round(g["amount"].sum(), 2), "count": int(len(g))}
            for k, g in sp.groupby(sp["subcategory"].fillna("Other"))]


def change_explanation(df: pd.DataFrame, category: str, prev: str, cur: str, upto_day: int | None = None, _sub: bool = True) -> dict:
    """Decompose a category change into 'more transactions' vs 'higher average value'.
    If upto_day is set, both months are compared over days 1..upto_day (like-for-like month-to-date)."""
    sp = spend_df(df)
    if upto_day:
        sp = sp[sp["date"].dt.day <= upto_day]
    field = "subcategory" if not _sub else "category"
    a_rows = sp[(sp["month"] == prev) & (sp[field] == category)]
    b_rows = sp[(sp["month"] == cur) & (sp[field] == category)]
    a, b = a_rows["amount"], b_rows["amount"]
    n0, n1 = len(a), len(b)
    avg0 = a.mean() if n0 else 0.0
    avg1 = b.mean() if n1 else 0.0
    t0, t1 = a.sum(), b.sum()
    count_effect = (n1 - n0) * avg0
    value_effect = n1 * (avg1 - avg0)
    driver = "count" if abs(count_effect) >= abs(value_effect) else "average_value"
    subs = []
    if _sub:
        for s in sorted(set(a_rows["subcategory"].dropna()) | set(b_rows["subcategory"].dropna())):
            x = change_explanation(df, s, prev, cur, upto_day, _sub=False)
            if x["change"]:
                subs.append({k: x[k] for k in ("category", "previous_total", "current_total", "change", "previous_count", "current_count", "previous_avg", "current_avg", "main_driver")})
        subs.sort(key=lambda r: -abs(r["change"]))
    return {
        "category": category, "previous_month": prev, "current_month": cur,
        "comparison": f"days 1-{upto_day} of each month (month-to-date)" if upto_day else "full months",
        "by_subcategory": subs,
        "previous_total": round(t0, 2), "current_total": round(t1, 2), "change": round(t1 - t0, 2),
        "change_pct": round(100 * (t1 - t0) / t0, 1) if t0 else None,
        "previous_count": n0, "current_count": n1,
        "previous_avg": round(avg0, 2), "current_avg": round(avg1, 2),
        "effect_of_more_transactions": round(count_effect, 2), "effect_of_avg_value": round(value_effect, 2),
        "main_driver": driver,
    }


def biggest_changes(df: pd.DataFrame) -> list[dict]:
    months = sorted(df["month"].unique())
    if len(months) < 2:
        return []
    prev, cur = months[-2], months[-1]
    ref = as_of(df)
    month_len = calendar.monthrange(ref.year, ref.month)[1]
    upto = ref.day if ref.day < month_len else None  # partial current month -> compare month-to-date
    cats = set(spend_df(df)[spend_df(df)["month"].isin([prev, cur])]["category"])
    ch = [change_explanation(df, c, prev, cur, upto) for c in cats]
    return sorted(ch, key=lambda x: -abs(x["change"]))


def detect_recurring(df: pd.DataFrame, overrides: dict[str, str] | None = None) -> list[dict]:
    """Repeated debits to the same merchant at a roughly regular interval with similar amounts.
    Tolerates +/-4 days on cadence and +/-15% on amount. Users are invited to review, not told it's wasteful."""
    sp = spend_df(df)
    res = []
    for key, g in sp.groupby("merchant_key"):
        if len(g) < 2:
            continue
        g = g.sort_values("date")
        amts = g["amount"].values
        med = float(np.median(amts))
        similar = g[np.abs(amts - med) <= 0.15 * med]
        if len(similar) < 2:
            continue
        gaps = similar["date"].diff().dt.days.dropna().values
        if len(gaps) == 0:
            continue
        mg = float(np.median(gaps))
        if 25 <= mg <= 35:
            cadence, period = "monthly", 30
        elif 6 <= mg <= 8:
            cadence, period = "weekly", 7
        else:
            continue
        if np.any(np.abs(gaps - period) > 4 if cadence == "monthly" else np.abs(gaps - period) > 2):
            continue
        last = similar["date"].max().date()
        res.append({
            "merchant": similar["merchant"].iloc[-1], "merchant_key": key, "category": similar["category"].iloc[-1],
            "subcategory": similar["subcategory"].iloc[-1], "cadence": cadence, "typical_amount": round(med, 2),
            "occurrences": int(len(similar)), "last_date": str(last),
            "next_expected": str(last + timedelta(days=period if cadence == "weekly" else 30)),
            "is_essential": bool(similar["is_essential"].iloc[-1]),
            "review_prompt": "Is this still useful to you? Review it - we don't assume it is wasteful.",
        })
    return sorted(res, key=lambda r: r["next_expected"])


def current_balance(df: pd.DataFrame, opening_balance: float | None = None) -> dict:
    """Use the statement's running balance if present; otherwise opening balance + net flows."""
    posted = df[df["status"] == "posted"]
    if "balance" in df and posted["balance"].notna().any():
        last = posted[posted["balance"].notna()].iloc[-1]
        return {"balance": round(float(last["balance"]), 2), "source": f"Statement running balance on {last['date'].date()}"}
    if opening_balance is not None:
        net = posted.apply(lambda r: r["amount"] if r["type"] == "credit" else -r["amount"], axis=1).sum()
        return {"balance": round(opening_balance + net, 2), "source": "Opening balance you entered + statement net flow"}
    return {"balance": None, "source": "No balance column in statement and no opening balance provided"}


def upcoming_commitments(recurring: list[dict], ref: date, horizon_end: date) -> list[dict]:
    out = []
    for r in recurring:
        nxt = date.fromisoformat(r["next_expected"])
        step = 7 if r["cadence"] == "weekly" else 30
        while nxt <= horizon_end:
            if nxt > ref:
                out.append({"merchant": r["merchant"], "due": str(nxt), "amount": r["typical_amount"], "source": "detected recurring"})
            nxt += timedelta(days=step)
    return out


def safe_to_spend(balance: float, commitments: list[dict], planned_savings: float, buffer: float, pending_debits: float) -> dict:
    total_commit = sum(c["amount"] for c in commitments)
    sts = balance - pending_debits - total_commit - planned_savings - buffer
    return {
        "available_balance": round(balance, 2), "pending_debits": round(pending_debits, 2),
        "upcoming_commitments": round(total_commit, 2), "planned_savings": round(planned_savings, 2),
        "emergency_buffer": round(buffer, 2), "safe_to_spend": round(max(sts, 0.0), 2),
        "shortfall": round(-sts, 2) if sts < 0 else 0.0,
        "formula": "available balance - pending debits - upcoming commitments - planned savings - emergency buffer",
        "assumption": "No future income is assumed.",
        "commitments_detail": commitments,
    }


def forecast_month_end(df: pd.DataFrame, balance: float, recurring: list[dict], include_income: bool = False) -> dict:
    """Baseline forecast: average daily non-recurring discretionary+essential spend over recent days,
    plus known recurring payments due before month end. Shows assumptions and an uncertainty band."""
    ref = as_of(df)
    last_day = date(ref.year, ref.month, calendar.monthrange(ref.year, ref.month)[1])
    days_left = (last_day - ref).days
    sp = spend_df(df)
    rec_keys = {r["merchant_key"] for r in recurring}
    lookback_start = pd.Timestamp(ref - timedelta(days=60))
    window = sp[(sp["date"] > lookback_start) & (~sp["merchant_key"].isin(rec_keys))]
    days = pd.date_range(lookback_start + pd.Timedelta(days=1), pd.Timestamp(ref))
    daily = window.groupby(window["date"].dt.normalize())["amount"].sum().reindex(days, fill_value=0)
    mu, sd = float(daily.mean()), float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    rec_due = upcoming_commitments(recurring, ref, last_day)
    rec_total = sum(r["amount"] for r in rec_due)
    exp_income = 0.0
    if include_income:
        inc = df[(df["type"] == "credit") & (df["credit_kind"] == "income")]
        exp_income = float(inc.groupby("month")["amount"].sum().mean()) if len(inc) else 0.0
    spend = mu * days_left + rec_total
    band = 1.28 * sd * math.sqrt(max(days_left, 1))  # ~80% interval under a simple iid assumption
    proj = balance - spend + exp_income
    return {
        "as_of": str(ref), "month_end": str(last_day), "days_left": days_left,
        "avg_daily_variable_spend": round(mu, 2), "recurring_due": round(rec_total, 2),
        "projected_spend": round(spend, 2), "projected_month_end_balance": round(proj, 2),
        "range_80pct": [round(proj - band, 2), round(proj + band, 2)],
        "assumptions": [
            f"Variable spending continues at the last-60-day daily average (₹{mu:,.0f}/day).",
            f"Detected recurring payments due before month end: ₹{rec_total:,.0f}.",
            "Expected income included." if include_income else "No future income assumed.",
            "Range assumes day-to-day spending varies independently (a simplification).",
        ],
    }


def goal_plan(target: float, saved: float, target_date: date | None, monthly_capacity: float, today: date,
              discretionary_monthly: float = 0.0) -> dict:
    remaining = max(target - saved, 0.0)
    plan = {"target": target, "saved": saved, "remaining": round(remaining, 2),
            "progress_pct": round(100 * saved / target, 1) if target else 0.0,
            "monthly_capacity_estimate": round(monthly_capacity, 2)}
    if target_date:
        months = max(1, (target_date.year - today.year) * 12 + target_date.month - today.month)
        need = remaining / months
        plan.update(months_to_target=months, required_monthly=round(need, 2),
                    feasible=bool(monthly_capacity > 0 and need <= monthly_capacity))
    options = []
    if monthly_capacity > 0:
        basis = "share of your average monthly surplus"
        levels = [("Relaxed", monthly_capacity * 0.25), ("Balanced", monthly_capacity * 0.5), ("Aggressive", monthly_capacity * 0.75)]
    else:
        # no surplus on average: show what trimming discretionary spending would fund
        basis = "trimming discretionary spending (you currently have no average monthly surplus)"
        levels = [(f"Trim {p}% of discretionary", discretionary_monthly * p / 100) for p in (10, 20, 30)]
    for label, c in levels:
        if c > 0:
            m = math.ceil(remaining / c) if remaining else 0
            options.append({"plan": label, "monthly_contribution": round(c, 2), "months": m,
                            "reach_by": str(_add_months(today, m))})
    plan["alternatives"] = options
    plan["alternatives_basis"] = basis
    return plan


def _add_months(d: date, m: int) -> date:
    y, mo = divmod(d.month - 1 + m, 12)
    return date(d.year + y, mo + 1, min(d.day, 28))


def what_if_purchase(price: float, sts: dict) -> dict:
    left = sts["safe_to_spend"] - price
    return {"price": price, "safe_to_spend": sts["safe_to_spend"], "after_purchase": round(left, 2),
            "affordable_now": left >= 0,
            "note": "Affordable within safe-to-spend." if left >= 0 else f"Exceeds safe-to-spend by ₹{-left:,.0f}."}
