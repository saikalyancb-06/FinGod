"""Goal pace and spending impact: goals are deadlines the user should hit, and every budget,
recommendation and answer is checked against them."""
from __future__ import annotations

from datetime import date, datetime


def required_per_month(remaining: float, days_left: int | None) -> float | None:
    """What has to be saved per month to hit the deadline. Under a month left, it's the whole
    remaining amount (not remaining / 0.25 months). Past the deadline there is no monthly figure."""
    if days_left is None or days_left < 0:
        return None
    months = days_left / 30.44
    return round(remaining if months < 1 else remaining / months, 2)


def pace(g: dict, today: date | None = None) -> dict:
    """Linear plan from when the goal was created to its target date."""
    today = today or date.today()
    target, saved = float(g["target"]), float(g["saved"])
    remaining = max(0.0, target - saved)
    out = {"remaining": round(remaining, 2), "progress_pct": round(100 * saved / target, 1) if target else 0.0, "done": remaining <= 0}
    td = g.get("target_date")
    if isinstance(td, str):
        td = date.fromisoformat(td)
    if out["done"]:
        out["status"] = "achieved"
        return out
    if not td:
        out["status"] = "no_deadline"
        return out
    created = g.get("created_at")
    start = created.date() if isinstance(created, datetime) else (created if isinstance(created, date) else today)
    start_saved = float(g.get("start_saved") if g.get("start_saved") is not None else saved)
    total_days = max(1, (td - start).days)
    elapsed = min(max(0, (today - start).days), total_days)
    expected = start_saved + (target - start_saved) * elapsed / total_days
    days_left = (td - today).days
    out.update(days_left=days_left, expected_by_now=round(expected, 2), gap=round(saved - expected, 2),
               required_monthly=required_per_month(remaining, days_left),
               required_daily=round(remaining / max(days_left, 1), 2) if days_left >= 0 else None)
    if days_left < 0:
        out["status"] = "overdue"
        out["overdue_days"] = -days_left
    elif saved >= expected - max(0.02 * target, 1):
        out["status"] = "ahead" if saved > expected + 0.05 * target else "on_track"
    else:
        out["status"] = "behind"
    return out


def primary(goals: list[dict]) -> dict | None:
    """The goal spending decisions should protect: the most urgent unfinished one (behind first)."""
    live = [g for g in goals if not g.get("pace", {}).get("done")]
    if not live:
        return None
    rank = {"behind": 0, "overdue": 0, "on_track": 1, "ahead": 2, "no_deadline": 3}
    return sorted(live, key=lambda g: (rank.get(g["pace"].get("status"), 3), g["pace"].get("days_left", 10 ** 6)))[0]


def impact(amount: float, goal: dict | None) -> dict | None:
    """How many days of goal saving a spend of `amount` costs."""
    if not goal or amount <= 0:
        return None
    per_day = float(goal.get("monthly_contribution") or goal["pace"].get("required_monthly") or 0) / 30.44
    if per_day <= 0:
        return None
    days = amount / per_day
    return {"goal": goal["name"], "days": round(days, 1),
            "text": f"≈ {days:.0f} day{'s' if round(days) != 1 else ''} of saving for your {goal['name']} goal"}
