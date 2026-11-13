"""User profile: who the user is, used to personalise every FinLens feature.

Collected in a short onboarding wizard and editable in Settings. Only what helps
the advice: occupation, income sources, living situation, location, fixed costs,
goals, food and shopping preferences. Nothing here is shared outside FinLens, and
only a short summary is sent to the LLM.
"""
from __future__ import annotations

OCCUPATIONS = {"student": "Student", "working": "Working professional", "both": "Studying and working",
               "between_jobs": "Between jobs", "other": "Other"}
LIVING = {"family": "Living with family", "hostel": "College hostel", "pg": "PG / paying guest",
          "shared_flat": "Shared flat", "own": "Own / rented place alone"}
DIETS = {"veg": "Vegetarian", "nonveg": "Non-vegetarian", "egg": "Eggetarian", "vegan": "Vegan", "jain": "Jain"}
PRIORITIES = {"save": "Save more each month", "smarter": "Spend smarter", "purchase": "Plan a big purchase",
              "loan": "Understand loans / EMIs", "track": "Just track where money goes", "emergency": "Build an emergency fund"}

DEFAULTS: dict = {
    "occupation": None, "course": "", "institution": "", "year": "", "age": 0, "years_working": 0, "self_employed": False,
    "living": None, "city": "Bengaluru", "area": "",
    # monthly money in (₹)
    "pocket_money": 0, "salary": 0, "stipend": 0, "part_time": 0, "scholarship": 0, "other_income": 0,
    "money_day": None,  # day of month money usually arrives
    # monthly fixed costs (₹) - used before a statement exists and to sanity-check detection
    "rent": 0, "fees_monthly": 0, "emi": 0, "phone_internet": 0, "transport_pass": 0,
    # goals & comfort
    "savings_target": 0, "goal_name": "", "emergency_buffer": 500,
    "priorities": [],
    # food
    "diet": None, "cuisines": [], "food_budget_per_meal": 0, "orders_per_week": None,
    # shopping
    "clothing_size": "", "shoe_size": "", "brands": [], "style": "",
    # credit (optional; improves the lender-style estimate)
    "cibil_score": 0, "credit_history_years": 0,
    # language & voice (Sarvam AI)
    "language": "en", "language_style": "formal",
    "completed": False,
}

INCOME_KEYS = ["pocket_money", "salary", "stipend", "part_time", "scholarship", "other_income"]
FIXED_KEYS = ["rent", "fees_monthly", "emi", "phone_internet", "transport_pass"]


def merge(current: dict | None, patch: dict) -> dict:
    out = {**DEFAULTS, **(current or {})}
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        default = DEFAULTS[k]
        if isinstance(default, bool):
            v = bool(v)
        elif isinstance(default, (int, float)) and not isinstance(default, bool):
            try:
                v = max(0.0, float(v or 0))
            except (TypeError, ValueError):
                continue
        elif isinstance(default, list):
            v = [str(x)[:40] for x in (v or [])][:12]
        elif v is not None:
            v = str(v)[:80]
        out[k] = v
    if out.get("money_day") not in (None, ""):
        try:
            out["money_day"] = int(min(31, max(1, int(float(out["money_day"])))))
        except (TypeError, ValueError):
            out["money_day"] = None
    return out


def declared_income(p: dict) -> float:
    return float(sum(float(p.get(k) or 0) for k in INCOME_KEYS))


def declared_fixed_costs(p: dict) -> float:
    return float(sum(float(p.get(k) or 0) for k in FIXED_KEYS))


def location(p: dict | None, fallback: str) -> str:
    if not p:
        return fallback
    city = (p.get("city") or "").strip()
    area = (p.get("area") or "").strip()
    if area and city:
        return f"{area}, {city}"
    return city or fallback


def summary(p: dict | None) -> dict:
    """Small, LLM-safe description of the user (no names, no institutions)."""
    if not p or not p.get("completed"):
        return {}
    inc = {k: p[k] for k in INCOME_KEYS if p.get(k)}
    fixed = {k: p[k] for k in FIXED_KEYS if p.get(k)}
    return {k: v for k, v in {
        "occupation": OCCUPATIONS.get(p.get("occupation"), p.get("occupation")),
        "age": p.get("age") or None,
        "living": LIVING.get(p.get("living"), p.get("living")),
        "city": p.get("city"),
        "declared_monthly_income": inc or None,
        "declared_monthly_income_total": declared_income(p) or None,
        "money_arrives_on_day": p.get("money_day"),
        "declared_fixed_costs": fixed or None,
        "savings_target_per_month": p.get("savings_target") or None,
        "goal": p.get("goal_name") or None,
        "priorities": [PRIORITIES.get(x, x) for x in p.get("priorities", [])] or None,
        "diet": DIETS.get(p.get("diet"), p.get("diet")),
        "favourite_cuisines": p.get("cuisines") or None,
        "usual_food_budget_per_meal": p.get("food_budget_per_meal") or None,
    }.items() if v not in (None, "", [])}


def options() -> dict:
    return {"occupations": OCCUPATIONS, "living": LIVING, "diets": DIETS, "priorities": PRIORITIES,
            "cuisines": ["North Indian", "South Indian", "Biryani", "Chinese", "Pizza", "Burgers", "Fast food", "Street food",
                         "Healthy", "Desserts", "Cafe", "Continental"]}
