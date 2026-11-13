"""Smart Spending: conversational preference collection -> search -> rank -> explain -> savings estimate.

LLM: understands preferences, asks follow-ups, explains matches.
Backend: search, ranking, budget fit and savings calculations.
"""
from __future__ import annotations

import json
import math
import re

from .amounts import amounts, party_size
from .amounts import budget as parse_budget

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
# shorthand the user may type for a kind of food -> searchable phrase
FOOD_SHORTHAND = {"fast": "fast food", "junk": "fast food", "street": "street food", "chinese": "chinese food", "italian": "italian food",
                  "mexican": "mexican food", "healthy": "healthy food", "south indian": "south indian food", "north indian": "north indian food",
                  "continental": "continental food", "thai": "thai food", "korean": "korean food", "japanese": "japanese food",
                  "arabian": "arabian food", "mughlai": "mughlai food", "desserts": "desserts", "sweets": "sweets", "snacks": "snacks"}
VEG_DIETS = {"veg", "vegan", "jain"}
GENERIC_ITEMS = {"food", "something", "anything", "meal", "eat", "order", "fashion", "clothes", "shopping", "stuff", "item", "things", "buy"}
SHOE_WORDS = ("shoe", "sneaker", "sandal", "slipper", "boot", "loafer")


def seed_from_profile(state: dict, prof: dict | None) -> dict:
    """Pre-fill what the profile already tells us, so we don't ask again."""
    if not prof or not prof.get("completed"):
        return state
    s = dict(state)
    if s.get("category") == "fashion":
        item = str(s.get("item", "")).lower()
        if not s.get("size") and item:
            if any(w in item for w in SHOE_WORDS) and prof.get("shoe_size"):
                s["size"] = f"UK {prof['shoe_size']}"
            elif not any(w in item for w in SHOE_WORDS) and prof.get("clothing_size"):
                s["size"] = prof["clothing_size"]
        if not s.get("style") and prof.get("style"):
            s["style"] = prof["style"]
    if s.get("category") == "food" and prof.get("diet") in VEG_DIETS:
        s["preferences"] = sorted(set(s.get("preferences", [])) | {"veg"})
    return s


def normalise_item(state: dict) -> dict:
    if state.get("category") == "food" and state.get("item"):
        it = str(state["item"]).strip().lower()
        state["item"] = FOOD_SHORTHAND.get(it, it)
    return state


def _budget_check_reply(text: str) -> dict:
    """Interpret the answer to 'that budget looks high - is it for a group?'"""
    t = text.lower()
    upd: dict = {}
    m = re.search(r"\bfor\s*(\d{1,2})\b", t)
    ps = party_size(t) or (int(m.group(1)) if m and 1 <= int(m.group(1)) <= 20 else None)
    if ps:
        upd["party"] = ps
    b = parse_budget(t)
    if b is not None and "party" not in upd:
        upd["budget"] = b
    if any(w in t for w in ("suggest", "use that", "go with that", "lower", "reduce", "cheaper", "use yours")):
        upd["_use_suggested"] = True
    if "budget" not in upd and any(w in t for w in ("keep", "yes", "go ahead", "continue", "sure", "fine", "proceed", "special", "treat", "party", "celebrat", "just me", "only me", "alone")):
        upd["_budget_ok"] = True
    return upd


_YES = ("no mistake", "not a mistake", "not mistake", "yes", "yeah", "yep", "sure", "i'm sure", "im sure", "i am sure", "confirm",
        "correct", "that's right", "thats right", "right", "keep it", "go ahead", "proceed", "continue", "ok", "okay", "haan", "ha ")
_NO = ("no", "nope", "mistake", "typo", "wrong", "oops", "change", "cancel", "less", "lower")


def _confirm_reply(text: str) -> dict:
    """Answer to 'are you sure about this budget?'. A new number replaces the budget; otherwise yes/no.
    'yes no mistake' is a yes, so yes-phrases are checked before the bare 'no'."""
    t = " " + text.lower().strip() + " "
    b = parse_budget(text) or next((v for v, _ in amounts(text) if v >= 50), None)
    if b:
        return {"budget": b}
    if any(w in t for w in ("use that", "use yours", "suggested", "go with that")):
        return {"_use_suggested": True}
    if any(re.search(rf"\b{re.escape(w.strip())}\b", t) for w in _YES):
        return {"yes": True}
    if any(re.search(rf"\b{re.escape(w)}\b", t) for w in _NO):
        return {"no": True}
    return {}


def _typo_guess(budget: float, limit: float | None, target: float | None = None) -> float | None:
    """7888888 -> 788 / 7,888 / 78,888: prefixes of the typed digits that fit the limit; the one
    closest to what the user normally spends (log scale) is the likeliest intended amount."""
    import math
    digits = str(int(budget))
    cands = [float(digits[:n]) for n in range(2, len(digits)) if float(digits[:n]) >= 50 and (limit is None or float(digits[:n]) <= limit)]
    if not cands:
        return None
    if target:
        return min(cands, key=lambda v: abs(math.log(v / target)))
    return max(cands)


def budget_guard(state: dict, room: dict | None, sug: dict) -> str | None:
    """Hard affordability check, independent of the 'comfortable' suggestion. Returns a question
    when the budget is beyond what the user can pay, or looks like a typo, and the user hasn't
    explicitly confirmed this exact amount."""
    budget = float(state.get("budget") or 0)
    if not budget or state.get("_confirmed") == budget:
        return None
    room = room or {}
    bal, sts = room.get("balance"), room.get("safe_to_spend")
    cat = state.get("category")
    party = int(state.get("party") or 1)
    sane = {"food": 5000 * party, "fashion": 50000, "shopping": 300000}.get(cat, 300000)
    why = None
    if bal is not None and budget > bal:
        why = (f"₹{budget:,.0f} is more than your whole account balance (₹{bal:,.0f}), so you couldn't actually pay this right now.")
        limit = sts if sts else bal
    elif sts is not None and budget > sts:
        why = (f"₹{budget:,.0f} is more than you can safely spend {room.get('period', 'right now')} (₹{sts:,.0f}). "
               f"Spending it would eat into money kept aside for bills, your goals and your safety buffer.")
        limit = sts
    elif budget > sane:
        why = f"₹{budget:,.0f} is unusually high for {state.get('item') or cat}."
        limit = sug.get("cap") or sane
    if not why:
        return None
    state["_asked"] = "budget_confirm"
    guess = _typo_guess(budget, limit, sug.get("cap"))
    tip = f" If it was a typo, did you mean ₹{guess:,.0f}?" if guess else ""
    if sug.get("cap"):
        tip += f" About ₹{sug['cap']:,.0f} would keep you comfortable."
        state["_suggested"] = sug["cap"]
    state["_quick"] = ["Yes, no mistake"] + ([f"₹{guess:,.0f}"] if guess else []) + (
        [f"₹{sug['cap']:,.0f}"] if sug.get("cap") and sug["cap"] != guess else [])
    nag = "I need a clear answer before I search. " if state.get("_unclear") else ""
    return (f"{nag}Are you sure about that budget? {why}{tip}"
            " Reply 'yes, no mistake' to search with it anyway, or type the budget you meant.")


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
    b = parse_budget(t)
    if b is not None:
        upd["budget"] = b
    ps = party_size(t)
    if ps:
        upd["party"] = ps  # "biryani for 4 people around 1200" -> budget is for the group
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


def _llm_extract(text: str, state: dict, prof_summary: dict | None = None) -> dict:
    sys = ("You extract shopping/food preferences from a user's message for a budgeting app. "
           "For food, 'item' must be a searchable dish or cuisine: expand shorthand like 'fast' -> 'fast food', 'chinese' -> 'chinese food'. "
           "Return ONLY JSON with any of these keys you can confidently fill: "
           "category ('food'|'fashion'|'shopping'), item (short search phrase e.g. 'chicken biryani'), "
           "budget (number, rupees), delivery (true/false), style (string), size (string), specs (string), "
           "preferences (list of short strings like 'spicy','veg','wireless'). "
           "Only extract what THIS message states. 'About the user' is background for interpreting words - never copy a value "
           "from it (do not fill budget, size or item from the profile). Generic words like 'food' or 'something' are not an item. "
           "Do not guess values that are not stated. If the user declines a preference, use 'no preference'.")
    ctx = {k: v for k, v in state.items() if not k.startswith("_")}
    try:
        out = llm.chat_json([{"role": "system", "content": sys},
                             {"role": "user", "content": f"About the user: {json.dumps(prof_summary or {})}\nKnown so far: {json.dumps(ctx)}\nLast question asked: {state.get('_asked')}\nUser message: {text}"}],
                            max_tokens=200, temperature=0)
        return {k: v for k, v in out.items() if k in {"category", "item", "budget", "delivery", "style", "size", "specs", "preferences"} and v not in (None, "")}
    except Exception:
        return {}


def update_state(state: dict, text: str, prof: dict | None = None, prof_summary: dict | None = None) -> dict:
    s = dict(state)
    if s.get("_asked") == "budget_confirm":
        ans = _confirm_reply(text)
        if ans.get("budget"):
            s["budget"], s["_budget_ok"], s["_confirmed"] = ans["budget"], False, None
        elif ans.get("_use_suggested") and s.get("_suggested"):
            s["budget"], s["_budget_ok"], s["_confirmed"] = float(s["_suggested"]), False, None
        elif ans.get("yes"):
            s["_confirmed"] = float(s["budget"])  # explicit "yes, no mistake" for this exact amount
        elif ans.get("no"):
            s["budget"], s["_confirmed"] = None, None  # ask for the budget again
        else:
            s["_unclear"] = int(s.get("_unclear") or 0) + 1
            return s
        s["_unclear"] = 0
        return s
    if s.get("_asked") == "budget_check":
        upd = _budget_check_reply(text)
        if upd.get("_use_suggested") and s.get("_suggested"):
            s["budget"], s["_budget_ok"], s["party"] = float(s["_suggested"]), True, 1
        if "party" in upd:
            s["party"], s["_budget_ok"] = upd["party"], True  # the user explained it - don't ask twice
        if "budget" in upd:
            s["budget"], s["_budget_ok"] = upd["budget"], False
        if upd.get("_budget_ok") or not upd:  # unclear answer: respect the user's choice rather than nag
            s["_budget_ok"] = True
        return s
    upd = _rule_extract(text, s)
    if llm.available():
        l = _llm_extract(text, s, prof_summary)
        for k, v in l.items():
            if k == "budget":
                try:
                    v = float(str(v).replace(",", "").replace("₹", ""))
                except ValueError:
                    continue
            if k == "category" and v not in {"food", "fashion", "shopping"}:
                continue
            if k == "budget" and not re.search(r"\d", text):
                continue  # never take a budget the user didn't type (e.g. copied from the profile)
            if k not in upd or k == "item":  # LLM phrase for the item is usually better; rules win for numbers
                upd[k] = v
    for k, v in upd.items():
        if k == "preferences":
            s["preferences"] = sorted(set(s.get("preferences", [])) | set(v if isinstance(v, list) else [v]))
        elif k == "category" and s.get("category"):
            continue
        else:
            s[k] = v
    if "budget" in upd:
        s["_budget_ok"] = False  # a new budget is re-checked
    return seed_from_profile(normalise_item(s), prof)


def next_question(state: dict, room: dict | None = None, prof: dict | None = None) -> str | None:
    from .room import suggested_budget
    if not state.get("category"):
        state["_asked"] = "category"
        return QUESTIONS["category"]
    sug = suggested_budget(room, state["category"], prof) if room else {"cap": None, "reasons": []}
    if sug["cap"]:
        state["_suggested"] = sug["cap"]
    if str(state.get("item", "")).strip().lower() in GENERIC_ITEMS:
        state.pop("item", None)  # "food" / "something" isn't a searchable choice yet
    if state.get("budget"):
        hard = budget_guard(state, room, sug)  # check the amount as soon as it's typed
        if hard:
            return hard
    for slot in REQUIRED[state["category"]]:
        if state.get(slot) in (None, ""):
            state["_asked"] = slot
            q = QUESTIONS[slot]
            q = q[state["category"]] if isinstance(q, dict) else q
            sts0 = (room or {}).get("safe_to_spend")
            if slot == "budget" and sts0 is not None and sts0 <= 0:
                q = (f"What's your budget? Heads-up: after bills, goal savings and your safety buffer you have ₹0 left to "
                     f"spend safely {room.get('period', 'right now')}, so anything you spend now comes out of money set aside.")
                return q
            if slot == "budget" and sug["cap"]:
                per = " per person" if state["category"] == "food" else ""
                q = (f"What's your budget{per}? Since {' and '.join(sug['reasons'])}, about ₹{sug['cap']:,.0f}{per} "
                     f"would keep you comfortable. Say a number, or 'use that'.")
            return q
    # hard guard first: more than the user's balance / safe-to-spend, or an implausible amount, needs an explicit yes
    hard = budget_guard(state, room, sug)
    if hard:
        return hard
    # soft guard: compare the (per-person) budget with what the user's money situation supports
    if sug["cap"] and not state.get("_budget_ok") and state.get("_confirmed") != float(state.get("budget") or 0):
        party = int(state.get("party") or 1)
        per_person = float(state["budget"]) / party
        if per_person > 1.5 * sug["cap"]:
            state["_asked"] = "budget_check"
            sts = (room or {}).get("safe_to_spend")
            share = (f" That's {float(state['budget']) / sts * 100:.0f}% of your safe-to-spend (₹{sts:,.0f} {room['period']})."
                     if sts else "")
            from .room import goal_impact
            gi = goal_impact(room or {}, float(state["budget"]))
            if gi:
                share += f" It would cost {gi['text']}."
            who = "for a group, or just you" if state["category"] == "food" and party == 1 else "what you intended"
            return (f"Just checking: ₹{float(state['budget']):,.0f}{' for ' + str(party) + ' people' if party > 1 else ''} is well above "
                    f"what fits right now ({' and '.join(sug['reasons'])}).{share} Is it {who}? You can say 'for 4 people', "
                    f"'use ₹{sug['cap']:,.0f}', or 'keep it'.")
        state["_budget_ok"] = True
    state["_asked"] = None
    return None


# ---------------- ranking ----------------
def _bayes_rating(r, n, prior=4.0, m=50):
    if r is None:
        return None
    n = n or 0
    return (r * n + prior * m) / (n + m)


def rank(items: list[dict], state: dict, k: int = 5) -> list[dict]:
    budget = float(state.get("budget") or 0) / max(1, int(state.get("party") or 1))  # per person for group orders
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
            n = max(1, int(state.get("party") or 1))
            bits.append(f"within your ₹{float(state.get('budget')) / n:,.0f}-per-person budget" if n > 1
                        else f"within your ₹{state.get('budget'):,.0f} budget")
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


def run_search(state: dict, location: str, df, room: dict | None = None) -> dict:
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
    if cat == "food" and "veg" in state.get("preferences", []) and "veg" not in q_search.lower():
        q_search = f"veg {q_search}"
    q_search = " ".join(dict.fromkeys(q_search.split()))
    res = search_mod.search(cat, q_search, location, delivery=bool(state.get("delivery")))
    top = rank(res["results"], state)
    ctx = spending_context(df, cat)
    reasons = explain(top, state)
    from .room import goal_impact
    party_n = max(1, int(state.get("party") or 1))
    for r, why in zip(top, reasons):
        r["why"] = why
        r["savings"] = savings(r.get("price"), ctx)
        r["goal_impact"] = goal_impact(room or {}, float(r["price"]) * party_n) if (r.get("price") and room) else None
    best_month = max([r["savings"]["monthly_estimate"] or 0 for r in top if r.get("savings")] or [0])
    party = int(state.get("party") or 1)
    return {"query": q_search, "mode": res["mode"], "notice": res["notice"], "results": top,
            "retrieved": len(res["results"]), "spending_context": ctx, "party": party,
            "budget_per_person": round(float(state.get("budget") or 0) / party, 2) if state.get("budget") else None,
            "savings_headline": (f"Choosing from these could save up to ~₹{best_month:,.0f}/month versus your typical spend (estimate)." if best_month else None),
            "purchase_control": "FinLens only recommends. It never places orders or makes purchases for you."}
