"""Spending room: how much the user can reasonably spend right now.

Uses the uploaded statement when available (balance, safe-to-spend, usual order
sizes), otherwise the declared profile (income - fixed costs - savings target).
All numbers are deterministic; the LLM only phrases them.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from . import analytics as A
from . import goals as G
from . import profile as P


def _days_until_money(ref: date, money_day: int | None) -> tuple[int, str]:
    if money_day:
        y, m = ref.year, ref.month
        target = date(y, m, min(money_day, calendar.monthrange(y, m)[1]))
        if target <= ref:
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            target = date(y, m, min(money_day, calendar.monthrange(y, m)[1]))
        return max(1, (target - ref).days), f"until your money arrives on the {money_day}{_suffix(money_day)}"
    end = date(ref.year, ref.month, calendar.monthrange(ref.year, ref.month)[1])
    return max(1, (end - ref).days), "until month end"


def _suffix(d: int) -> str:
    return "th" if 11 <= d % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")


def typical_spend(df, category: str) -> dict:
    if df is None or df.empty:
        return {}
    sp = A.spend_df(df)
    if category == "food":
        g = sp[(sp["category"] == "Food") & (sp["subcategory"].isin(["Food delivery", "Restaurant", "Cafe"]))]
    elif category == "fashion":
        g = sp[(sp["category"] == "Shopping") & (sp["subcategory"].isin(["Clothing", "Sports"]))]
    else:
        g = sp[sp["category"] == "Shopping"]
    if g.empty:
        return {}
    months = max(1, df["month"].nunique())
    return {"typical_order": round(float(g["amount"].median()), 2), "orders_per_month": round(len(g) / months, 1),
            "monthly_total": round(float(g["amount"].sum()) / months, 2), "based_on": int(len(g))}


def compute(df, ctx: dict | None, prof: dict | None, today: date | None = None, goals: list | None = None) -> dict:
    prof = prof or {}
    goals = goals or []
    room: dict = {"source": None}
    ref = today or (A.as_of(df) if df is not None and not df.empty else date.today())
    days, until = _days_until_money(ref, prof.get("money_day"))
    room.update(days_left=days, period=until)
    sts = (ctx or {}).get("safe_to_spend")
    if sts:
        # safe-to-spend reserves commitments to month end; if money arrives sooner the room is the same pot
        room.update(source="statement", safe_to_spend=sts["safe_to_spend"], balance=sts["available_balance"])
    elif prof.get("completed") and P.declared_income(prof):
        goal_need = sum(float(g.get("monthly_contribution") or 0) for g in goals if not g["pace"].get("done"))
        monthly_free = (P.declared_income(prof) - P.declared_fixed_costs(prof) - max(float(prof.get("savings_target") or 0), goal_need)
                        - float(prof.get("emergency_buffer") or 0))
        free = max(0.0, monthly_free) * min(1.0, days / 30.0)
        room.update(source="profile", safe_to_spend=round(free, 2), monthly_free=round(monthly_free, 2))
    if room.get("safe_to_spend") is not None:
        room["daily_room"] = round(room["safe_to_spend"] / days, 2)
    room["typical"] = {c: typical_spend(df, c) for c in ("food", "fashion", "shopping")} if df is not None else {}
    room["goals"] = [{"name": g["name"], "status": g["pace"].get("status"), "needs_per_month": g["pace"].get("required_monthly"),
                      "contribution": g.get("monthly_contribution"), "days_left": g["pace"].get("days_left"),
                      "progress_pct": g["pace"].get("progress_pct")} for g in goals]
    pg = G.primary(goals)
    # is the goal plan realistic? (needed per month vs what the user actually frees up)
    need = sum(float(g.get("monthly_contribution") or 0) for g in goals if not g["pace"].get("done"))
    cap = None
    if goals and goals[0].get("plan") is not None:
        cap = float(goals[0]["plan"].get("monthly_capacity_estimate") or 0)
    if cap is None and prof.get("completed"):
        cap = max(0.0, P.declared_income(prof) - P.declared_fixed_costs(prof))
    if pg and need > 0 and cap is not None and need > cap * 1.05:
        rem = float(pg["pace"].get("remaining") or 0)
        realistic = None
        if cap > 0:
            months = rem / (cap * 0.8)
            realistic = (ref + timedelta(days=int(months * 30.44))).isoformat()
        room["goal_warning"] = {
            "goal": pg["name"], "needs_per_month": round(need, 2), "frees_up_per_month": round(cap, 2), "realistic_date": realistic,
            "text": (f"Your goals need ₹{need:,.0f}/month but you currently free up about ₹{cap:,.0f}/month"
                     + (f", so reserving it leaves almost nothing to spend. A realistic date for {pg['name']} is around {realistic}." if realistic
                        else "; right now you spend more than you earn, so the goal can't be funded yet."))}
    room["primary_goal"] = ({"name": pg["name"], "status": pg["pace"].get("status"), "monthly_contribution": pg.get("monthly_contribution"),
                             "required_monthly": pg["pace"].get("required_monthly"), "days_left": pg["pace"].get("days_left"),
                             "pace": pg["pace"]} if pg else None)
    return room


def goal_impact(room: dict, amount: float) -> dict | None:
    pg = room.get("primary_goal")
    if not pg:
        return None
    return G.impact(amount, {"name": pg["name"], "monthly_contribution": pg.get("monthly_contribution"), "pace": pg["pace"]})


def suggested_budget(room: dict, category: str, prof: dict | None) -> dict:
    """A reasonable budget for one purchase, with the reasoning shown to the user."""
    prof = prof or {}
    daily = room.get("daily_room")
    sts = room.get("safe_to_spend")
    typ = (room.get("typical") or {}).get(category, {}).get("typical_order")
    reasons = []
    pg = room.get("primary_goal")
    if pg and pg.get("monthly_contribution"):
        reasons.append(f"₹{pg['monthly_contribution']:,.0f}/month is set aside for your {pg['name']} goal"
                       + (" (currently behind)" if pg.get("status") in ("behind", "overdue") else ""))
    if category == "food":
        base = float(prof.get("food_budget_per_meal") or 0) or (typ or 0)
        if prof.get("food_budget_per_meal"):
            reasons.append(f"you said you usually spend about ₹{base:,.0f} per meal")
        elif typ:
            reasons.append(f"your usual food order is about ₹{typ:,.0f}")
        cap = base * 1.3 if base else None
        if daily is not None:
            reasons.append(f"you have about ₹{daily:,.0f}/day to spend {room['period']}")
            day_cap = daily * 1.5
            cap = min(cap, day_cap) if cap else day_cap
        cap = max(cap or 0, 80) if (cap or daily) else None
    else:
        if typ:
            reasons.append(f"you usually spend about ₹{typ:,.0f} on {category}")
        cap = typ * 2 if typ else None  # a purchase like the ones you usually make, with headroom
        if sts is not None:
            reasons.append(f"your safe-to-spend {room['period']} is ₹{sts:,.0f}")
            room_cap = max(0.0, sts * (0.5 if cap else 0.15))
            cap = min(cap, room_cap) if cap else room_cap
    if cap and pg and pg.get("status") in ("behind", "overdue"):
        cap *= 0.8  # tighten while the user's main goal is behind schedule
    return {"cap": round(cap, -1) if cap else None, "reasons": reasons}
