"""Proactive, personalised nudges and the weekly money report.

Every number is computed here (deterministic); the LLM only writes the optional
summary paragraph of the weekly report, which is figure-verified like Ask FinLens.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import analytics as A


def _inr(v: float) -> str:
    return f"₹{v:,.0f}"


def _month_progress(ref: date) -> tuple[float, int]:
    days = calendar.monthrange(ref.year, ref.month)[1]
    return ref.day / days, days - ref.day


def compute(df: pd.DataFrame, ctx: dict, prof: dict, room: dict | None, dismissed: set | None = None) -> list[dict]:
    dismissed = dismissed or set()
    comfy = (ctx.get("health") or {}).get("status") in ("healthy", "steady")  # tone follows the user's real position
    out: list[dict] = []
    if df is None or df.empty:
        return out
    ref = A.as_of(df)
    month = f"{ref:%Y-%m}"
    sp = A.spend_df(df)
    frac, days_left = _month_progress(ref)

    def add(nid, severity, icon, title, body, link=None, label=None):
        if nid not in dismissed:
            out.append({"id": nid, "severity": severity, "icon": icon, "title": title, "body": body,
                        "action": {"label": label, "link": link} if link else None})

    # 1. category pace vs the user's usual month
    months = sorted(df["month"].unique())
    prev = [m for m in months if m < month][-3:]
    if prev:
        usual = sp[sp["month"].isin(prev)].groupby(["month", "category"])["amount"].sum().groupby("category").mean()
        now = sp[sp["month"] == month].groupby("category")["amount"].sum()
        for cat, spent in now.items():
            base = float(usual.get(cat, 0))
            if base < 500 or cat in ("Accommodation & Utilities", "Education", "Transfers & Other"):
                continue
            used = spent / base
            if used > 0.6 and used / max(frac, 0.05) > 1.25:
                if comfy:
                    add(f"pace_{cat}_{month}", "info", "👀", f"{cat} is running ahead of your usual month",
                        f"{_inr(spent)} so far versus your usual {_inr(base)}, with {days_left} days to go. You can afford it; just keep it intentional.",
                        "/finlens/transactions?category=" + cat, "See transactions")
                    continue
                add(f"pace_{cat}_{month}", "warn" if used < 1 else "alert", "🔥",
                    f"{cat}: {used*100:.0f}% of your usual month already",
                    f"You've spent {_inr(spent)} on {cat.lower()} this month versus your usual {_inr(base)}, with {days_left} days still to go.",
                    "/finlens/transactions?category=" + cat, "See transactions")
    # 2. little money left until the next money day
    if room and room.get("safe_to_spend") is not None:
        daily = room.get("daily_room") or 0
        typical_day = float(sp[sp["month"].isin(prev or months)].groupby("date")["amount"].sum().median() or 0) if len(sp) else 0
        if room["safe_to_spend"] <= 0:
            add(f"broke_{ref}", "alert", "🚨", "Nothing left to spend safely",
                f"After bills, goal savings and your buffer there's no spare money {room['period']}. Try to keep spending to essentials.",
                "/finlens/planner", "Review plan")
        elif typical_day and daily < typical_day * 0.6:
            add(f"tight_{ref}", "warn", "⚠️", f"Tight days ahead: {_inr(daily)}/day",
                f"You can safely spend {_inr(room['safe_to_spend'])} {room['period']}, about {_inr(daily)} a day, while you usually spend {_inr(typical_day)} on a spending day.",
                "/finlens/spending", "Find cheaper options")
    # 3. bills due soon
    sts_ctx = ctx.get("safe_to_spend") or {}
    horizon = date.fromisoformat(sts_ctx["horizon_end"]) if sts_ctx.get("horizon_end") else None
    for r in ctx.get("recurring", []):
        due = date.fromisoformat(r["next_expected"])
        if 0 <= (due - ref).days <= 4:
            reserved = " It's already reserved in your safe-to-spend." if horizon and due <= horizon else " Keep it aside."
            add(f"due_{r['merchant_key']}_{due}", "info", "📅", f"{r['merchant']} due {'today' if due == ref else f'in {(due - ref).days} days'}",
                f"Expected {_inr(r['typical_amount'])} around {due:%d %b}.{reserved}")
    # 4. subscription / bill price changes
    rec_keys = {r["merchant_key"]: r for r in ctx.get("recurring", [])}
    for key, g in sp[sp["merchant_key"].isin(rec_keys)].groupby("merchant_key"):
        g = g.sort_values("date")
        if len(g) >= 3:
            last, before = float(g["amount"].iloc[-1]), float(g["amount"].iloc[:-1].median())
            if before > 0 and last > before * 1.05 and last - before >= 10:
                add(f"price_{key}_{g['date'].iloc[-1].date()}", "warn", "📈", f"{rec_keys[key]['merchant']} costs more now",
                    f"Your last payment was {_inr(last)}, up from the usual {_inr(before)}. Worth checking your plan.")
    # 5. possible double charge
    dup = sp[sp["date"] >= pd.Timestamp(ref - timedelta(days=10))]
    for (d, key, amt), g in dup.groupby([dup["date"], "merchant_key", "amount"]):
        if len(g) >= 2 and amt >= 100:
            add(f"dup_{key}_{d.date()}_{amt}", "alert", "🧾", f"Possible double charge: {g['merchant'].iloc[0]}",
                f"{len(g)} identical payments of {_inr(amt)} on {d:%d %b}. If you didn't mean to pay twice, contact the merchant or bank.",
                "/finlens/transactions", "Check")
    # 6. goals
    for g in ctx.get("goals", []):
        p = g["pace"]
        if p.get("status") == "behind":
            add(f"goal_behind_{g['id']}_{ref:%Y-%W}", "warn", "🎯", f"{g['name']} is behind plan",
                f"You're {_inr(-p['gap'])} behind where you should be. Saving {_inr(p['required_monthly'])}/month from now gets you there by {g['target_date']}.",
                "/finlens/planner", "Open goal")
        elif p.get("status") == "ahead":
            add(f"goal_ahead_{g['id']}_{ref:%Y-%W}", "good", "🚀", f"{g['name']}: ahead of plan!",
                f"{p['progress_pct']}% saved. Keep going: you could finish early.", "/finlens/planner", "See goal")
        elif p.get("days_left") is not None and 0 <= p["days_left"] <= 30 and not p.get("done"):
            add(f"goal_soon_{g['id']}", "info", "⏳", f"{g['name']} deadline in {p['days_left']} days",
                f"{_inr(p['remaining'])} still to go ({_inr(p['required_daily'])}/day).", "/finlens/planner", "Open goal")
    # 7. money arrived -> pay yourself first
    inc = df[(df["type"] == "credit") & (df["credit_kind"] == "income") & (df["date"] >= pd.Timestamp(ref - timedelta(days=3)))]
    if len(inc):
        g0 = next((g for g in ctx.get("goals", []) if not g["pace"].get("done")), None)
        amt = float(inc["amount"].sum())
        body = f"{_inr(amt)} arrived."
        if g0 and g0.get("monthly_contribution"):
            body += f" Move {_inr(min(amt, g0['monthly_contribution']))} to your {g0['name']} goal first, before it gets spent."
        add(f"income_{inc['date'].max().date()}", "good", "💰", "Money in!", body, "/finlens/planner" if g0 else None, "Add to goal" if g0 else None)
    # 8. unusually large payment this week
    week = sp[sp["date"] >= pd.Timestamp(ref - timedelta(days=7))]
    med = float(sp["amount"].median() or 0)
    shareable = {"Restaurant", "Food delivery", "Groceries", "Movies & events", "Taxi", "Train", "Bus", "Fuel"}
    for r in week[week["amount"] > max(1000, 4 * med)].itertuples():
        if (r.subcategory or "") in shareable:  # only things people actually share
            add(f"big_{r.uid}", "info", "💸", f"Big payment: {_inr(r.amount)} at {r.merchant}",
                f"On {r.date:%d %b}, about {r.amount / max(med, 1):.0f}× your typical payment. Shared with friends? Split it so only your share counts.",
                "/finlens/split", "Split it")
    # 9. no-spend days (positive reinforcement)
    covered = min(7, (ref - df["date"].min().date()).days + 1)
    disc = sp[(~sp["is_essential"]) & (sp["date"] > pd.Timestamp(ref - timedelta(days=7)))]
    nospend = covered - disc["date"].dt.date.nunique()
    if covered >= 7 and nospend >= 2:
        add(f"nospend_{ref:%Y-%W}", "good", "🌱", f"{nospend} no-spend days this week",
            "Days without any non-essential spending. Small streaks like this add up fast.")
    # 10. declared vs detected income
    from .profile import declared_income
    decl = declared_income(prof or {})
    det = float(df[(df["type"] == "credit") & (df["credit_kind"] == "income")].groupby("month")["amount"].sum().mean() or 0)
    if decl and det and abs(det - decl) / decl > 0.25:
        add(f"income_mismatch_{month}", "info", "🔍", "Your income looks different from your profile",
            f"You told FinLens about {_inr(decl)}/month, but your statement shows about {_inr(det)}/month of income. Update your profile so plans stay accurate.",
            "/finlens/settings", "Update profile")
    order = {"alert": 0, "warn": 1, "info": 2, "good": 3}
    pace = [n for n in out if n["id"].startswith("pace_")]
    keep = {n["id"] for n in sorted(pace, key=lambda n: order[n["severity"]])[:2]}  # only the two most pressing categories
    out = [n for n in out if not n["id"].startswith("pace_") or n["id"] in keep]
    return sorted(out, key=lambda n: order[n["severity"]])


def weekly_report(df: pd.DataFrame, ctx: dict, end: date | None = None) -> dict:
    latest = A.as_of(df)
    first = df["date"].min().date()
    ref = min(end or latest, latest)
    if ref < first:
        return {"no_data": True, "period": {"start": str(ref - timedelta(days=6)), "end": str(ref)},
                "first_date": str(first), "last_date": str(latest),
                "message": f"Your statement starts on {first:%d %b %Y}, so there's no data for this week."}
    start, pstart = ref - timedelta(days=6), ref - timedelta(days=13)
    covered = (ref - max(start, first)).days + 1
    prev_covered = max(0, (start - max(pstart, first)).days)
    sp = A.spend_df(df)
    cur = sp[(sp["date"] >= pd.Timestamp(start)) & (sp["date"] <= pd.Timestamp(ref))]
    prv = sp[(sp["date"] >= pd.Timestamp(pstart)) & (sp["date"] < pd.Timestamp(start))]
    total, ptotal = float(cur["amount"].sum()), float(prv["amount"].sum())
    cats = []
    for cat, g in cur.groupby("category"):
        before = float(prv[prv["category"] == cat]["amount"].sum())
        cats.append({"category": cat, "spent": round(float(g["amount"].sum()), 2), "count": int(len(g)),
                     "previous": round(before, 2), "change": round(float(g["amount"].sum()) - before, 2)})
    cats.sort(key=lambda c: -c["spent"])
    big = cur.sort_values("amount", ascending=False).head(1)
    inc = df[(df["type"] == "credit") & (df["credit_kind"] == "income") & (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(ref))]
    disc = cur[~cur["is_essential"]]
    nospend = covered - disc["date"].dt.date.nunique()
    daily = cur.groupby(cur["date"].dt.date)["amount"].sum()
    days = [{"date": str(start + timedelta(days=i)), "spent": round(float(daily.get(start + timedelta(days=i), 0)), 2)} for i in range(7)]
    goals = [{"name": g["name"], "saved": g["saved"], "target": g["target"], "progress_pct": g["pace"].get("progress_pct"),
              "status": g["pace"].get("status"), "needs_per_month": g["pace"].get("required_monthly")} for g in ctx.get("goals", [])]
    win, focus = None, None
    falls = [c for c in cats if c["change"] < -200] if prev_covered >= 7 else []
    rises = [c for c in cats if c["change"] > 200] if prev_covered >= 7 else []
    if prev_covered < 7:
        ptotal_cmp = None  # previous week not fully in the statement: no fair comparison
    else:
        ptotal_cmp = ptotal
    if ptotal_cmp is not None and total < ptotal:
        win = f"You spent {_inr(ptotal - total)} less than the week before."
    elif falls:
        win = f"{falls[0]['category']} spending fell by {_inr(-falls[0]['change'])}."
    elif nospend >= 2:
        win = f"{nospend} no-spend days."
    if rises:
        focus = f"{rises[0]['category']} rose by {_inr(rises[0]['change'])} ({rises[0]['count']} payments). Set a soft cap for next week."
    elif any(g["status"] == "behind" for g in goals):
        g = next(g for g in goals if g["status"] == "behind")
        focus = f"Catch up on {g['name']}: it needs {_inr(g['needs_per_month'])}/month."
    return {
        "period": {"start": str(start), "end": str(ref)}, "spent": round(total, 2),
        "previous_week": round(ptotal, 2) if ptotal_cmp is not None else None,
        "change": round(total - ptotal, 2) if ptotal_cmp is not None else None,
        "change_pct": round((total - ptotal) / ptotal * 100, 1) if ptotal_cmp and ptotal else None,
        "is_latest_week": ref == latest, "days_covered": covered, "last_date": str(latest), "first_date": str(first),
        "income_received": round(float(inc["amount"].sum()), 2), "transactions": int(len(cur)), "no_spend_days": int(max(0, nospend)),
        "categories": cats[:6], "daily": days,
        "biggest": ({"merchant": big["merchant"].iloc[0], "amount": round(float(big["amount"].iloc[0]), 2), "date": str(big["date"].iloc[0].date()),
                     "category": big["category"].iloc[0]} if len(big) else None),
        "goals": goals if ref == latest else [], "win": win, "focus": focus,
        "safe_to_spend": (ctx.get("safe_to_spend") or {}).get("safe_to_spend") if ref == latest else None,
    }


def _name_from(desc: str) -> str | None:
    m = re.search(r"\b(?:UPI\s+)?(?:TO|FROM|PAID TO|RECEIVED FROM|SENT TO)\s+([A-Z][A-Z .]{1,30}?)(?:\s+-|\s*$|\s+[A-Z]*\d)", desc.upper())
    if not m:
        return None
    return " ".join(w.capitalize() for w in m.group(1).split() if len(w) > 1 or w.isalpha())[:30] or None
