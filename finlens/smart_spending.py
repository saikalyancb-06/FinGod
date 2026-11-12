"""Smart Spending: conversational preference collection -> search -> rank -> explain -> savings estimate.

LLM: understands preferences, asks follow-ups, explains matches.
Backend: search, ranking, budget fit and savings calculations.
"""
from __future__ import annotations

import json
import math
import re

from shared import llm

from . import search as search_mod
from .analytics import spend_df

FOOD_WORDS = ["biryani", "burger", "pizza", "dosa", "idli", "noodles", "momos", "momo", "roll", "thali", "meals", "food",
              "eat", "order", "dinner", "lunch", "breakfast", "fried rice", "pasta", "kebab", "sandwich", "shawarma", "cake"]
FASHION_WORDS = ["shoe", "shoes", "sneaker", "t-shirt", "tshirt", "shirt", "jeans", "jacket", "kurta", "dress", "clothes",
                 "hoodie", "trousers", "fashion", "wear", "outfit", "sandals", "slippers"]
SHOP_WORDS = ["headphone", "earbuds", "earphones", "laptop", "phone", "backpack", "bag", "power bank", "calculator",
              "watch", "smartwatch", "keyboard", "mouse", "lamp", "bottle", "charger", "speaker", "buy"]

QUESTIONS = {
    "category": "What are you looking for today - food, fashion, or something else to buy?",
    "item": {"food": "What kind of food are you in the mood for?", "fashion": "What would you like to buy - shoes, a t-shirt, jeans…?",
             "shopping": "What product are you looking for?"},
    "budget": "What's your budget? (e.g. around ₹200)",
    "delivery": "Should it be delivery, or are you happy to dine in / pick up?",
    "style": "Any preferred style or occasion? (e.g. casual for college, sports, formal) - or say 'no preference'.",
    "size": "What size do you need? (or 'skip')",
    "specs": "Any must-have features? (e.g. wireless, noise cancelling) - or say 'no preference'.",
}
REQUIRED = {"food": ["item", "budget", "delivery"], "fashion": ["item", "budget", "style", "size"], "shopping": ["item", "budget", "specs"]}


def _detect_category(text: str) -> str | None:
    t = text.lower()
    if any(w in t for w in FASHION_WORDS):
        return "fashion"
    if any(re.search(rf"\b{re.escape(w)}", t) for w in FOOD_WORDS):
        return "food"
    if any(w in t for w in SHOP_WORDS):
        return "shopping"
    return None


def _rule_extract(text: str, state: dict) -> dict:
    t = text.lower().strip()
    upd: dict = {}
    m = re.search(r"(?:₹|rs\.?|inr|under|below|around|budget|max|upto|up to)\s*(\d[\d,]*)", t) or re.fullmatch(r"\s*(\d[\d,]*)\s*", t)
    if m:
        upd["budget"] = float(m.group(1).replace(",", ""))
    cat = _detect_category(t)
    if cat and not state.get("category"):
        upd["category"] = cat
    cat = state.get("category") or upd.get("category")
    asking = state.get("_asked")
    if cat and not state.get("item"):
        words = FOOD_WORDS if cat == "food" else FASHION_WORDS if cat == "fashion" else SHOP_WORDS
        found = [w for w in words if w in t and w not in {"food", "eat", "order", "buy", "wear", "fashion", "clothes", "outfit"}]
        if found:
            # keep qualifiers like "chicken biryani"
            mm = re.search(rf"([a-z]+\s+)?{re.escape(found[0])}", t)
            upd["item"] = mm.group(0).strip() if mm and mm.group(1) and mm.group(1).strip() not in {"a", "an", "some", "to", "order", "buy", "want", "need", "the"} else found[0]
        elif asking == "item" and len(t) < 60:
            upd["item"] = re.sub(r"[^a-z0-9 \-]", "", t).strip()
    if any(w in t for w in ["deliver", "home", "order in"]):
        upd["delivery"] = True
    elif any(w in t for w in ["dine", "pick up", "pickup", "takeaway", "go out"]):
        upd["delivery"] = False
    m = (re.search(r"\b((?:uk|us|eu)\s*\d{1,2}(?:\.5)?)\b", t)
         or re.search(r"\bsize\s*(\d{1,2}|xxl|xl|xs|s|m|l)\b", t)
         or (re.search(r"^\s*(xxl|xl|xs|s|m|l|\d{1,2})\s*$", t) if asking == "size" else None))
    if m:
        upd["size"] = m.group(1).upper()
    skip = any(w in t for w in ["no preference", "skip", "anything", "doesn't matter", "dont care", "don't care", "any"])
    if asking in {"style", "specs", "size"} and asking not in upd:
        upd[asking] = "no preference" if skip else re.sub(r"[^a-z0-9 \-,]", "", t)[:60]
    if asking == "delivery" and "delivery" not in upd:
        upd["delivery"] = not any(w in t for w in ["no", "dine", "pick"])
    return upd


def _llm_extract(text: str, state: dict) -> dict:
    sys = ("You extract shopping/food preferences from a user's message for a budgeting app. "
           "Return ONLY JSON with any of these keys you can confidently fill: "
           "category ('food'|'fashion'|'shopping'), item (short search phrase e.g. 'chicken biryani'), "
           "budget (number, rupees), delivery (true/false), style (string), size (string), specs (string), "
           "preferences (list of short strings like 'spicy','veg','wireless'). "
           "Do not guess values that are not stated. If the user declines a preference, use 'no preference'.")
    ctx = {k: v for k, v in state.items() if not k.startswith("_")}
    try:
        out = llm.chat_json([{"role": "system", "content": sys},
                             {"role": "user", "content": f"Known so far: {json.dumps(ctx)}\nLast question asked: {state.get('_asked')}\nUser message: {text}"}],
                            max_tokens=200, temperature=0)
        return {k: v for k, v in out.items() if k in {"category", "item", "budget", "delivery", "style", "size", "specs", "preferences"} and v not in (None, "")}
    except Exception:
        return {}


def update_state(state: dict, text: str) -> dict:
    s = dict(state)
    upd = _rule_extract(text, s)
    if llm.available():
        l = _llm_extract(text, s)
        for k, v in l.items():
            if k == "budget":
                try:
                    v = float(str(v).replace(",", "").replace("₹", ""))
                except ValueError:
                    continue
            if k == "category" and v not in {"food", "fashion", "shopping"}:
                continue
            if k not in upd or k == "item":  # LLM phrase for the item is usually better; rules win for numbers
                upd[k] = v
    for k, v in upd.items():
        if k == "preferences":
            s["preferences"] = sorted(set(s.get("preferences", [])) | set(v if isinstance(v, list) else [v]))
        elif k == "category" and s.get("category"):
            continue
        else:
            s[k] = v
    return s


def next_question(state: dict) -> str | None:
    if not state.get("category"):
        state["_asked"] = "category"
        return QUESTIONS["category"]
    for slot in REQUIRED[state["category"]]:
        if state.get(slot) in (None, ""):
            state["_asked"] = slot
            q = QUESTIONS[slot]
            return q[state["category"]] if isinstance(q, dict) else q
    state["_asked"] = None
    return None


# ---------------- ranking ----------------
def _bayes_rating(r, n, prior=4.0, m=50):
    if r is None:
        return None
    n = n or 0
    return (r * n + prior * m) / (n + m)


def rank(items: list[dict], state: dict, k: int = 5) -> list[dict]:
    budget = float(state.get("budget") or 0)
    terms = set(re.findall(r"[a-z]+", " ".join([str(state.get("item", "")), str(state.get("style", "")), str(state.get("specs", "")),
                                                " ".join(state.get("preferences", []))]).lower())) - {"no", "preference", "any", "and", "the", "for"}
    head = re.sub(r"s$", "", (re.findall(r"[a-z]+", str(state.get("item", "")).lower()) or [""])[-1])
    scored = []
    for it in items:
        if it.get("is_demo") and head:
            blob = f"{it.get('title','')} {it.get('subtitle','')} {' '.join(it.get('tags', []))}".lower()
            if head not in blob:
                continue  # demo catalog is coarse; keep only items of the requested kind
        price = it.get("price")
        if budget and price and price > budget * 1.25:
            continue  # clearly outside budget
        # budget fit (0-40)
        if not budget or price is None:
            bf = 15.0
        elif price <= budget:
            bf = 40.0 - 10.0 * (1 - price / budget)  # within budget; mildly prefer using it on quality over the absolute cheapest
        else:
            bf = max(0.0, 40.0 * (1 - (price - budget) / (0.25 * budget)) * 0.6)
        # rating quality (0-30), Bayesian-smoothed so 5.0 from 3 reviews doesn't win
        br = _bayes_rating(it.get("rating"), it.get("reviews"))
        rq = 0.0 if br is None else max(0.0, min(30.0, (br - 3.0) / 2.0 * 30.0))
        # preference match (0-20)
        text = f"{it.get('title','')} {it.get('subtitle','')} {' '.join(it.get('tags', []))} {' '.join(map(str, it.get('extensions', [])))}".lower()
        pm = 20.0 * (sum(1 for t in terms if t in text) / len(terms)) if terms else 10.0
        # availability / delivery (0-10)
        av = 5.0
        if state.get("category") == "food" and state.get("delivery") is True:
            av = 10.0 if it.get("delivery") else 3.0
        elif it.get("delivery"):
            av = 8.0
        if it.get("open_now") is False:
            av -= 3
        total = bf + rq + pm + av
        scored.append({**it, "score": round(total, 1),
                       "score_breakdown": {"budget_fit": round(bf, 1), "rating_quality": round(rq, 1), "preference_match": round(pm, 1), "availability": round(av, 1)},
                       "within_budget": bool(budget and price and price <= budget)})
    scored.sort(key=lambda x: -x["score"])
    # de-duplicate near-identical titles
    seen, out = set(), []
    for s in scored:
        key = re.sub(r"[^a-z]", "", s["title"].lower())[:30]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) == k:
            break
    return out


def spending_context(df, category: str) -> dict:
    """Limited, user-authorized context: typical comparable spend and frequency only (no raw statement)."""
    if df is None or df.empty:
        return {"typical_spend": None, "monthly_frequency": None}
    sp = spend_df(df)
    if category == "food":
        g = sp[(sp["category"] == "Food") & (sp["subcategory"].isin(["Food delivery", "Restaurant", "Cafe"]))]
    elif category == "fashion":
        g = sp[(sp["category"] == "Shopping") & (sp["subcategory"] == "Clothing")]
    else:
        g = sp[sp["category"] == "Shopping"]
    if g.empty:
        return {"typical_spend": None, "monthly_frequency": None}
    months = max(1, df["month"].nunique())
    return {"typical_spend": round(float(g["amount"].median()), 2), "monthly_frequency": round(len(g) / months, 1),
            "based_on": f"{len(g)} past {category} transactions"}


def savings(option_price: float | None, ctx: dict) -> dict | None:
    t = ctx.get("typical_spend")
    if not t or option_price is None:
        return None
    diff = t - option_price
    if diff <= 0:
        return {"per_order": 0.0, "monthly_estimate": 0.0, "note": "Not cheaper than your typical comparable spend."}
    freq = ctx.get("monthly_frequency") or 0
    return {"per_order": round(diff, 2), "monthly_estimate": round(diff * freq, 2) if freq else None,
            "note": f"Estimate: typical ₹{t:,.0f} vs ₹{option_price:,.0f}" + (f" × ~{freq:g} orders/month" if freq else "") + ". Not guaranteed savings."}


def explain(results: list[dict], state: dict) -> list[str]:
    """Short 'why it matches' per result; LLM if available, else from the score breakdown. Never invents facts."""
    def template(r):
        bits = []
        if r.get("price") is None:
            bits.append("price not listed by the source")
        elif r.get("within_budget"):
            bits.append(f"within your ₹{state.get('budget'):,.0f} budget")
        elif r.get("price") and state.get("budget"):
            bits.append(f"slightly over budget (₹{r['price']:,.0f})")
        if r.get("rating"):
            bits.append(f"rated {r['rating']}" + (f" from {r['reviews']:,} reviews" if r.get("reviews") else ""))
        if r["score_breakdown"]["preference_match"] >= 10:
            bits.append(f"matches '{state.get('item')}'")
        if state.get("delivery") and r.get("delivery"):
            bits.append("delivery listed")
        return ("Good fit: " + ", ".join(bits) + ".") if bits else "Matches your search."
    if not llm.available() or not results:
        return [template(r) for r in results]
    facts = [{"n": i + 1, "title": r["title"], "price": r.get("price_text"), "price_listed": r.get("price") is not None,
              "rating": r.get("rating"), "reviews": r.get("reviews"), "subtitle": r.get("subtitle"),
              "within_budget": r.get("within_budget") if r.get("price") is not None else "unknown - price not listed",
              "delivery_listed": bool(r.get("delivery")), "size_availability_verified": False,
              "score_breakdown": r["score_breakdown"]} for i, r in enumerate(results)]
    try:
        out = llm.chat_json([
            {"role": "system", "content": "Write one short sentence (max 20 words) per option explaining why it matches the user's request. "
                                          "Use ONLY the facts given. Never invent prices, ratings, fees or availability. "
                                          "Size availability is never verified - do not claim a size is available. "
                                          "If price_listed is false, say the price isn't listed instead of claiming it fits the budget. "
                                          "Return JSON {\"reasons\": [string, ...]} in the same order."},
            {"role": "user", "content": json.dumps({"request": {k: v for k, v in state.items() if not k.startswith('_')}, "options": facts})},
        ], max_tokens=400, temperature=0.2)
        reasons = out.get("reasons", [])
        if len(reasons) == len(results):
            return [str(x) for x in reasons]
    except Exception:
        pass
    return [template(r) for r in results]


def run_search(state: dict, location: str, df) -> dict:
    cat = state["category"]
    q = state["item"]
    if cat == "fashion" and state.get("style") and state["style"] != "no preference" and state["style"].lower() not in q.lower():
        q = f"{state['style']} {q}"
    if cat == "shopping" and state.get("specs") and state["specs"] != "no preference":
        q = f"{q} {state['specs']}"
    if cat == "fashion" and state.get("size") and state["size"] not in {"no preference", "skip"}:
        q_search = f"{q} size {state['size']}"
    else:
        q_search = q
    q_search = " ".join(dict.fromkeys(q_search.split()))
    res = search_mod.search(cat, q_search, location)
    top = rank(res["results"], state)
    ctx = spending_context(df, cat)
    reasons = explain(top, state)
    for r, why in zip(top, reasons):
        r["why"] = why
        r["savings"] = savings(r.get("price"), ctx)
    best_month = max([r["savings"]["monthly_estimate"] or 0 for r in top if r.get("savings")] or [0])
    return {"query": q_search, "mode": res["mode"], "notice": res["notice"], "results": top,
            "retrieved": len(res["results"]), "spending_context": ctx,
            "savings_headline": (f"Choosing from these could save up to ~₹{best_month:,.0f}/month versus your typical spend (estimate)." if best_month else None),
            "purchase_control": "FinLens only recommends. It never places orders or makes purchases for you."}
