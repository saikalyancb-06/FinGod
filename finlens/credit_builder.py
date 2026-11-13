"""Credit-builder path for first-time borrowers (students and new earners with no / thin CIBIL history).

The plan is personalised from the profile and statement (savings decide the FD size for a secured
card, a real recurring bill is suggested for the card, utilisation limits are in rupees). The
timeline re-runs the built-in lender model with the credit history the user would have after
following the plan, so the user sees approval odds rising. Score milestones are illustrative
assumptions, labelled as such, not a prediction from bureau data.
"""
from __future__ import annotations

from . import lender
from .profile import declared_income

# Typical CIBIL path with on-time payments and low utilisation (illustrative; bureaus don't publish a formula)
NEW_SCORE_PATH = {0: None, 6: 700, 12: 730, 24: 760}
GAIN = {0: 0, 6: 15, 12: 30, 24: 50}


def _stage(prof: dict) -> tuple[str, str]:
    score = prof.get("cibil_score")
    hist = float(prof.get("credit_history_years") or 0)
    if not score and hist < 0.5:
        return "new", "No credit history yet: lenders can't see how you repay, so even small loans get declined or priced high."
    if hist < 2 or (score and float(score) < 700):
        return "thin", "Your credit file is young: a year or two of spotless repayments moves you into the 'easy approval' range."
    if score and float(score) >= 750:
        return "established", "You already have a strong profile. Keep doing what you're doing and avoid unnecessary applications."
    return "building", "You're building nicely: low utilisation and on-time payments will keep lifting your score."


def plan(prof: dict, df=None, ctx: dict | None = None, done: set | None = None) -> dict:
    prof = prof or {}
    done = done or set()
    ctx = ctx or {}
    stage, stage_text = _stage(prof)
    student = prof.get("occupation") in ("student", "both")
    bal = (ctx.get("balance") or {}).get("balance")
    sts = (ctx.get("safe_to_spend") or {}).get("safe_to_spend")
    income = declared_income(prof)
    if df is not None and not df.empty:
        inc = df[(df["type"] == "credit") & (df["credit_kind"] == "income")]
        if len(inc):
            income = float(inc.groupby("month")["amount"].sum().mean())
    # FD for a secured card: from money that isn't needed this month, never the whole balance
    spare = float(sts) if bal is not None and sts is not None else None  # money not reserved for bills/goals/buffer
    fd = 10000.0
    save_first = False
    if spare is not None:
        fd = max(2000.0, min(25000.0, round(spare * 0.6, -3)))
        save_first = spare * 0.6 < 2000  # can't fund the smallest FD without touching bills/goals
    limit = round(fd * 0.85, -2)
    util_cap = round(limit * 0.3, -2)
    bill = next((r for r in ctx.get("recurring", []) if 50 <= r["typical_amount"] <= util_cap), None)

    steps = [
        {"id": "check_report", "month": 0, "icon": "🔎", "title": "Check your free credit report",
         "why": "See whether a file exists and that nothing wrong is on it (wrong loans, wrong name or PAN).",
         "how": "Every bureau (CIBIL, Experian, Equifax, CRIF) gives one free full report a year. Use only official bureau sites.",
         "skip": stage == "established"},
        {"id": "fd_card", "month": 0, "icon": "💳", "title": f"Get a secured credit card against a ₹{fd:,.0f} FD",
         "why": "A card backed by your own fixed deposit is approved without any credit history, and every on-time bill builds your file.",
         "how": (f"Open an FD of about ₹{fd:,.0f} with your bank and ask for an FD-backed card (limit is usually 80-90%, so about ₹{limit:,.0f}). "
                 + (f"You can safely spend only ₹{spare:,.0f} right now, so first set aside ₹2,000 over the next weeks; don't take it from money for bills or goals." if save_first else
                    f"Sized as 60% of what you can safely spend right now (₹{spare:,.0f}), so your bills and goals stay covered." if spare else
                    "Use money you won't need for at least a year.")
                 + " The FD keeps earning interest."),
         "skip": stage == "established"},
        {"id": "one_bill", "month": 1, "icon": "🔁", "title": (f"Put {bill['merchant']} (₹{bill['typical_amount']:,.0f}) on the card" if bill
                                                           else "Put one small regular bill on the card"),
         "why": "Small, predictable use shows lenders a steady repayment pattern, which is exactly what builds a score.",
         "how": (f"You already pay {bill['merchant']} every {'week' if bill['cadence'] == 'weekly' else 'month'}: switch it to the card. " if bill else
                 "A phone recharge or streaming subscription is ideal. ") + "Don't use the card for things you couldn't pay from your account today."},
        {"id": "autopay", "month": 1, "icon": "⚙️", "title": "Turn on autopay for the FULL bill",
         "why": "One missed payment can cost 50-100 points and stays on file for years. Paying only the minimum costs 36-42% interest a year.",
         "how": "In your card app, set autopay from your savings account for the 'total amount due', not the minimum."},
        {"id": "utilisation", "month": 1, "icon": "📉", "title": f"Keep card spending under ₹{util_cap:,.0f} a month",
         "why": "Using less than 30% of your limit is one of the biggest score factors.",
         "how": f"That's 30% of an approximate ₹{limit:,.0f} limit. If you need to spend more in a month, pay part of it before the statement date."},
        {"id": "no_apps", "month": 0, "icon": "✋", "title": "Don't apply for several loans or cards at once",
         "why": "Each application is a 'hard enquiry'. Many in a few months look like credit hunger and pull the score down.",
         "how": "Space applications at least 6 months apart. Checking your own score is a soft enquiry and never hurts."},
        {"id": "six_months", "month": 6, "icon": "🌱", "title": "6 months of on-time payments: your first score appears",
         "why": "Bureaus usually need about 6 months of history to generate a score.",
         "how": "Check your free report again. With clean history first scores are typically in the high 600s to 700s."},
        {"id": "upgrade", "month": 12, "icon": "🚀", "title": "After 12 months: ask for a regular card or limit increase",
         "why": "With a year of clean history most banks offer an unsecured card. A higher limit at the same spend lowers utilisation.",
         "how": "Keep the first card open even if you stop using it much: the age of your oldest account helps your score."},
    ]
    if student:
        steps.insert(3, {"id": "student", "month": 1, "icon": "🎓", "title": "Student? Mind any education loan",
                         "why": "If a parent co-signed an education loan, interest-only payments during the course also build your history.",
                         "how": "Even small on-time EMIs during the moratorium count. Never skip one.", "optional": True})
    steps = [{**s, "done": s["id"] in done} for s in steps if not s.get("skip")]

    timeline, model_note = [], None
    if lender.available() and income > 0:
        base_score = float(prof["cibil_score"]) if prof.get("cibil_score") else None
        base_hist = float(prof.get("credit_history_years") or 0)
        # pick a loan size where credit history actually matters for this user: the smallest of a few
        # realistic amounts that isn't already a near-certain approval today
        principal = round(max(20000.0, min(200000.0, income * 2)), -3)
        today_score = float(prof["cibil_score"]) if prof.get("cibil_score") else None
        for mult in (2, 4, 6, 10):
            cand = round(max(20000.0, min(1500000.0, income * mult)), -3)
            r0 = lender.assess(df, prof, {"principal": cand, "months": 12 if mult <= 2 else 36, "annual_rate_pct": 14,
                                          "product": "Personal Loan", "credit_history_years": base_hist, "cibil_score": today_score})
            principal, term = cand, (12 if mult <= 2 else 36)
            if (r0.get("approval_probability") or 0) < 0.85:
                break
        for m in (0, 6, 12, 24):
            if base_score:
                score = min(850.0, base_score + GAIN[m])
            else:
                score = NEW_SCORE_PATH[m]
            form = {"principal": principal, "months": term, "annual_rate_pct": 14, "product": "Personal Loan",
                    "credit_history_years": base_hist + m / 12, "cibil_score": score}
            r = lender.assess(df, prof, form)
            if r.get("approval_probability") is None:
                timeline = []
                model_note = r.get("message")
                break
            timeline.append({"month": m, "label": "Today" if m == 0 else f"+{m} months", "credit_score": score,
                             "score_note": "assumed 650 (no score yet)" if score is None else ("your score" if m == 0 else "illustrative"),
                             "credit_history_years": round(base_hist + m / 12, 1),
                             "approval": r["approval_probability"], "band": r["status"]})
        loan_txt = f"₹{principal:,.0f} personal loan over {term} months"
    else:
        loan_txt = None
        model_note = "Add your income (profile or statement) to see how approval odds change over time."
    done_n = sum(1 for s in steps if s["done"])
    return {"stage": stage, "stage_text": stage_text, "steps": steps, "progress": {"done": done_n, "total": len(steps)},
            "fd_amount": fd, "card_limit": limit, "utilisation_cap": util_cap, "timeline": timeline, "timeline_loan": loan_txt,
            "model_note": model_note,
            "assumptions": ["Score milestones are illustrative of a clean repayment record, not a bureau prediction.",
                            "Approval odds come from the built-in lender model with longer credit history and the milestone score; "
                            "income and existing debts are kept as they are today.",
                            ("FD size is 60% of your current safe-to-spend (min ₹2,000, max ₹25,000)."
                            + (" Right now that's below ₹2,000, so the plan starts with saving it up." if save_first else ""))],
            "caveat": "Education, not financial advice. FinLens never applies for credit for you."}
