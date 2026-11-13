"""Ask FinLens: question -> approved metric retrieval -> LLM explanation of verified numbers.

The backend decides which metrics a question needs and computes them. Only a
limited structured summary (never raw descriptions or the full statement) is
sent to the LLM. If the LLM is unavailable, a deterministic template answer is
returned from the same facts.
"""
from __future__ import annotations

import json
import re

from shared import llm

from . import analytics as A

SYSTEM = (
    "You are FinLens, a friendly personal-finance assistant for students and young professionals in India. "
    "If FACTS has 'about_you' (the user's own profile: occupation, living situation, declared income, goals, diet), tailor the "
    "advice to it - e.g. pocket money vs salary, hostel vs family, their stated goal and priorities - and mention when the "
    "statement disagrees with what they declared. If FACTS has 'goals_progress', treat those goals as commitments with deadlines: "
    "say whether they are on track or behind, and frame every spending suggestion around keeping them on schedule. "
    "FACTS.money_health is the computed verdict on the user's overall position. Never say or imply the user is running out of "
    "money, broke, overspending or in trouble unless money_health.status is 'tight' or 'at_risk'. If they ask why they are running "
    "out of money but the status is 'healthy' or 'steady', say clearly that the data shows they are NOT running out, cite "
    "money_health.strengths, then show where most of the money goes and how to put the surplus to work for their goals. "
    "Match the tone to the status: encouraging when healthy, calm when steady, practical when tight or at_risk. "
    "Answer the user's question using ONLY the numbers in FACTS (JSON), quoted exactly. Never do arithmetic: do not add, subtract, "
    "estimate, project or recompute any figure, and do not promise savings amounts. If something is not in FACTS, say you don't have "
    "that data. Never write approximate amounts (no ≈, ~, 'about ₹', 'roughly ₹'). Use ₹ and Indian number formatting. "
    "Format in Markdown: start with a one-sentence answer, then a short '**What the data shows**' bullet list, then "
    "'**What you could do**' with at most two numbered steps. Bold key figures. A small Markdown table is allowed for comparisons. "
    "Be concise (under 130 words), concrete, non-judgmental and practical: suggest at most two realistic actions "
    "that don't require giving up things the user enjoys. Do not give investment advice or guarantee outcomes."
)

INTENTS = [
    ("afford", [r"afford", r"can i buy", r"should i buy", r"enough (money|balance) (for|to)"]),
    ("health", [r"how am i doing", r"how.*(my )?financ", r"am i (doing )?(ok|okay|fine|good|well)", r"financial health", r"money health"]),
    ("forecast", [r"forecast", r"predict", r"month.?end balance", r"end of (the )?month", r"projected", r"will i have"]),
    ("running_out", [r"running out", r"run out", r"broke", r"short of money", r"why.*(no|less) money"]),
    ("why_change", [r"why.*(increase|more|higher|up|spike|change|went up)", r"what changed", r"spend(ing)? (more|increase)"]),
    ("recurring", [r"subscription", r"recurring", r"bills?", r"auto.?debit", r"membership"]),
    ("safe_to_spend", [r"safe to spend", r"how much can i spend", r"spend this (week|month)"]),
    ("goals", [r"goal", r"save for", r"saving for", r"how long.*save"]),
    ("category_spend", [r"how much.*(spend|spent)", r"spent on", r"spend on", r"spending on"]),
    ("where_money", [r"where.*(money|spending)", r"most of my money", r"biggest expense", r"breakdown", r"top categor"]),
]

CATEGORY_ALIASES = {
    "food delivery": ("subcategory", "Food delivery"), "delivery": ("subcategory", "Food delivery"),
    "swiggy": ("merchant", "Swiggy"), "zomato": ("merchant", "Zomato"), "groceries": ("subcategory", "Groceries"),
    "grocery": ("subcategory", "Groceries"), "food": ("category", "Food"), "eating": ("category", "Food"),
    "transport": ("category", "Transportation"), "travel": ("category", "Transportation"), "metro": ("subcategory", "Metro"),
    "uber": ("merchant", "Uber"), "ola": ("merchant", "Ola"), "shopping": ("category", "Shopping"), "clothes": ("subcategory", "Clothing"),
    "clothing": ("subcategory", "Clothing"), "entertainment": ("category", "Entertainment"), "subscriptions": ("subcategory", "Subscription"),
    "movies": ("subcategory", "Movies & events"), "education": ("category", "Education"), "books": ("subcategory", "Books"),
    "rent": ("subcategory", "Rent"), "utilities": ("category", "Accommodation & Utilities"), "health": ("category", "Healthcare"),
    "medicine": ("subcategory", "Pharmacy"), "amazon": ("merchant", "Amazon"), "flipkart": ("merchant", "Flipkart"),
}


def classify(q: str) -> str:
    t = q.lower()
    for name, pats in INTENTS:
        if any(re.search(p, t) for p in pats):
            return name
    return "overview"


def _price(q: str) -> float | None:
    from .amounts import price
    v = price(q)
    return v if v and v >= 50 else None  # tiny/unclear -> ask for the price instead of guessing


def gather(question: str, df, ctx: dict) -> tuple[str, dict]:
    """ctx carries pre-computed balance/safe-to-spend/recurring/goals."""
    intent = classify(question)
    months = sorted(df["month"].unique())
    cur = months[-1]
    facts: dict = {"as_of": str(A.as_of(df)), "current_month": cur, "statement_months": months}
    if intent in ("where_money", "overview"):
        facts["category_breakdown_current_month"] = A.category_breakdown(df, cur)[:6]
        facts["month_summary"] = A.monthly_summary(df)[-2:]
    if intent == "category_spend":
        t = question.lower()
        hit = next(((k, v) for k, v in sorted(CATEGORY_ALIASES.items(), key=lambda kv: -len(kv[0])) if k in t), None)
        sp = A.spend_df(df)
        if hit:
            field, val = hit[1]
            g = sp[sp[field] == val]
            facts["matched"] = {"field": field, "value": val}
            facts["by_month"] = [{"month": m, "total": round(x["amount"].sum(), 2), "count": int(len(x))} for m, x in g.groupby("month")]
            facts["all_period_total"] = round(g["amount"].sum(), 2)
            facts["transactions_matched"] = int(len(g))
        else:
            facts["category_breakdown_current_month"] = A.category_breakdown(df, cur)
    if intent == "why_change":
        facts["biggest_changes_vs_previous_month"] = A.biggest_changes(df)[:3]
    if intent in ("running_out", "where_money", "overview", "why_change"):
        sp = A.spend_df(df)
        cur_sp = sp[sp["month"] == cur]
        facts["top_subcategories_current_month"] = [
            {"subcategory": k, "total": round(float(g["amount"].sum()), 2), "count": int(len(g))}
            for k, g in sorted(cur_sp.groupby(cur_sp["subcategory"].fillna("Other")), key=lambda kv: -kv[1]["amount"].sum())[:6]]
    if intent == "running_out":
        facts["month_summary"] = A.monthly_summary(df)[-2:]
        facts["biggest_changes_vs_previous_month"] = A.biggest_changes(df)[:3]
        facts["recurring_payments"] = [{k: r[k] for k in ("merchant", "typical_amount", "cadence", "next_expected")} for r in ctx["recurring"]]
        facts["safe_to_spend"] = {k: v for k, v in ctx["safe_to_spend"].items() if k != "commitments_detail"} if ctx.get("safe_to_spend") else None
        facts["spending_by_week_of_month"] = _week_split(df, cur)
    if intent == "recurring":
        facts["recurring_payments"] = ctx["recurring"]
        facts["recurring_monthly_total"] = round(sum(r["typical_amount"] * (4.3 if r["cadence"] == "weekly" else 1) for r in ctx["recurring"]), 2)
    if intent in ("safe_to_spend", "afford"):
        facts["safe_to_spend"] = {k: v for k, v in ctx["safe_to_spend"].items() if k != "commitments_detail"} if ctx.get("safe_to_spend") else None
        p = _price(question)
        if intent == "afford" and p and ctx.get("safe_to_spend"):
            facts["purchase_check"] = A.what_if_purchase(p, ctx["safe_to_spend"])
        elif intent == "afford":
            facts["purchase_check"] = "No price found in the question - ask the user for the price."
    if intent == "forecast":
        facts["forecast"] = ctx.get("forecast")
    if intent == "goals":
        facts["goals"] = ctx.get("goals")
    if intent == "health":
        facts["month_summary"] = A.monthly_summary(df)[-2:]
        facts["safe_to_spend"] = {k: v for k, v in ctx["safe_to_spend"].items() if k != "commitments_detail"} if ctx.get("safe_to_spend") else None
        facts["forecast"] = ctx.get("forecast")
    if ctx.get("health"):
        facts["money_health"] = ctx["health"]
    facts["balance"] = ctx.get("balance")
    if ctx.get("goal_status"):
        facts["goals_progress"] = ctx["goal_status"]  # deadlines the user wants to hit; advice must protect them
    if ctx.get("profile"):
        facts["about_you"] = ctx["profile"]  # declared by the user in their profile (personalises tone and advice)
    return intent, facts


def _week_split(df, month):
    sp = A.spend_df(df)
    g = sp[sp["month"] == month]
    w = ((g["date"].dt.day - 1) // 7 + 1).clip(upper=4)
    return {f"days_{(k-1)*7+1}_{'end' if k == 4 else k*7}": round(v, 2) for k, v in g.groupby(w)["amount"].sum().items()}


def template_answer(intent: str, f: dict) -> str:
    inr = lambda x: f"−₹{abs(x):,.0f}" if x < 0 else f"₹{x:,.0f}"  # noqa: E731
    if intent in ("where_money", "overview") and f.get("category_breakdown_current_month"):
        top = f["category_breakdown_current_month"][:3]
        return (f"In {f['current_month']}, most of your spending went to " +
                ", ".join(f"{c['category']} ({inr(c['total'])}, {c['share_pct']}%)" for c in top) + ".")
    if intent == "category_spend" and "matched" in f:
        bm = "; ".join(f"{m['month']}: {inr(m['total'])} across {m['count']} transaction{'s' if m['count'] != 1 else ''}" for m in f["by_month"]) or "no matching transactions"
        return f"Spending on {f['matched']['value']}: {bm}. Total over the statement: {inr(f['all_period_total'])}."
    if intent == "why_change" and f.get("biggest_changes_vs_previous_month"):
        c = f["biggest_changes_vs_previous_month"][0]
        return A.describe_change(c)
    h = f.get("money_health") or {}
    if intent == "health" or (intent == "running_out" and h and not h.get("is_running_out")):
        txt = h.get("headline", "")
        if intent == "running_out":
            txt = "Good news: your statement shows you're **not** running out of money. " + txt
        if h.get("strengths"):
            txt += "\n\n**What the data shows**\n" + "\n".join(f"- {s[0].upper() + s[1:]}" for s in h["strengths"])
        if h.get("watch_outs"):
            txt += "\n\n**Worth watching**\n" + "\n".join(f"- {s[0].upper() + s[1:]}" for s in h["watch_outs"])
        if h.get("status") == "healthy":
            txt += "\n\n**What you could do**\n1. Move part of the surplus to your goals as soon as money arrives, before it gets spent."
        return txt
    if intent == "running_out":
        ms = f["month_summary"][-1]
        s = f.get("safe_to_spend") or {}
        txt = f"In {ms['month']} you received {inr(ms['inflow_total'])} (income {inr(ms['income'])}) and spent {inr(ms['outflow'])}."
        if f.get("biggest_changes_vs_previous_month"):
            c = f["biggest_changes_vs_previous_month"][0]
            txt += f" The biggest change was {c['category']} ({'+' if c['change'] >= 0 else ''}{inr(c['change'])} vs last month)."
        if s:
            txt += f" After commitments, savings and buffer, your safe-to-spend is {inr(s['safe_to_spend'])}."
        return txt
    if intent == "recurring":
        rs = f.get("recurring_payments") or []
        if not rs:
            return "I didn't detect any regular recurring payments yet."
        return "Recurring payments detected: " + ", ".join(f"{r['merchant']} {inr(r['typical_amount'])} {r['cadence']} (next ~{r['next_expected']})" for r in rs) + ". Review whether each is still useful."
    if intent in ("safe_to_spend", "afford"):
        s = f.get("safe_to_spend")
        if not s:
            return "I need your balance to calculate safe-to-spend. Add an opening balance in Settings or upload a statement with a balance column."
        pend = f" − pending {inr(s['pending_debits'])}" if s.get("pending_debits") else ""
        short = f" You're {inr(s['shortfall'])} short of covering everything." if s.get("shortfall") else ""
        base = (f"Safe-to-spend {s.get('period', '')}: {inr(s['safe_to_spend'])} (balance {inr(s['available_balance'])}{pend} − commitments "
                f"{inr(s['upcoming_commitments'])} − goal savings {inr(s['planned_savings'])} − buffer {inr(s['emergency_buffer'])}).{short}")
        pc = f.get("purchase_check")
        if isinstance(pc, dict):
            return base + " " + pc["note"]
        if isinstance(pc, str):
            return base + " Tell me the price and I'll check it."
        return base
    if intent == "forecast" and f.get("forecast"):
        fc = f["forecast"]
        return f"Projected month-end balance: {inr(fc['projected_month_end_balance'])} (likely range {inr(fc['range_80pct'][0])}–{inr(fc['range_80pct'][1])}). " + " ".join(fc["assumptions"][:2])
    if intent == "goals":
        g = f.get("goals") or []
        if not g:
            return "You haven't set any savings goals yet. Add one in the Planner."
        return " ".join(f"{x['name']}: {x['plan']['progress_pct']}% saved, {inr(x['plan']['remaining'])} to go." for x in g)
    return "I can help with where your money goes, why spending changed, recurring payments, safe-to-spend, forecasts and goals."


_NUM_RE = re.compile(r"(₹\s*)?(-?\d[\d,]*(?:\.\d+)?)\s*(%)?")


def _fact_numbers(obj, out: set):
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(round(abs(float(obj)), 0))
        return out
    if isinstance(obj, dict):
        for v in obj.values():
            _fact_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _fact_numbers(v, out)
    elif isinstance(obj, str):
        for m in _NUM_RE.finditer(obj):
            try:
                out.add(round(abs(float(m.group(2).replace(",", ""))), 0))
            except ValueError:
                pass
    return out


def unverified_numbers(text: str, facts: dict, question: str = "") -> list[str]:
    """Every ₹ amount, and any other number >= 100, in the answer must appear in FACTS (±1 rounding).
    Small counts/percentages under 100 are allowed (they can't be checked reliably), dates are skipped."""
    allowed = _fact_numbers(facts, set()) | _fact_numbers(question, set())
    bad = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(2).replace(",", "")
        try:
            v = round(abs(float(raw)), 0)
        except ValueError:
            continue
        rupee = bool(m.group(1))
        if not rupee and v < 100:
            continue
        if not rupee and 2000 <= v <= 2100 and len(raw) == 4:  # a year
            continue
        if not any(abs(v - a) <= 1 for a in allowed):
            bad.append(m.group(0).strip())
    return bad


def answer(question: str, df, ctx: dict) -> dict:
    intent, facts = gather(question, df, ctx)
    fallback = template_answer(intent, facts)
    used_llm, text, check = False, fallback, {"unverified_numbers": [], "retried": False, "fell_back": False}
    if llm.available():
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"QUESTION: {question}\n\nFACTS: {json.dumps(facts, default=str)}"}]
        candidate, bad = None, ["(no answer)"]
        for attempt in range(3):
            try:
                candidate = llm.chat(msgs, max_tokens=450, temperature=0.2)
            except Exception:
                continue
            bad = unverified_numbers(candidate, facts, question)
            if not bad:
                break
            check["retried"] = True
            msgs = msgs[:2] + [{"role": "assistant", "content": candidate},
                               {"role": "user", "content": f"These figures are not in FACTS: {', '.join(bad[:8])}. Rewrite the whole answer "
                                                           "using only figures copied exactly from FACTS. Do not add, subtract, estimate or "
                                                           "approximate any amount; describe changes in words instead."}]
        check["unverified_numbers"] = bad if candidate else []
        if candidate and not bad:
            text, used_llm = candidate, True
        elif candidate:
            check["fell_back"] = True
    return {"intent": intent, "answer": text, "facts": facts, "llm_used": used_llm, "number_check": check,
            "grounding": "Numbers come from FinLens backend calculations shown in 'facts'; AI answers are checked against them."}
