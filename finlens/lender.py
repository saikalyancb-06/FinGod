"""Built-in lender-style approval estimate (replaces the unavailable CredWise API).

Inputs are assembled from the user's statement (income, EMIs, balance), profile (age, occupation,
years working, credit details) and the loan form. Output: approval likelihood, factors that help or
hurt (per-applicant SHAP contributions), a credit-score sensitivity curve and the largest loan that
keeps approval likely. An estimate of lender behaviour, never an approval.
"""
from __future__ import annotations

from functools import lru_cache

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

from shared.config import ARTIFACT_DIR

from . import credit
from .credit_features import extract

ASSUMED_SCORE = 650  # typical first-time borrower when the user doesn't know their CIBIL
LIKELY, BORDERLINE = 0.70, 0.40


@lru_cache(maxsize=1)
def _model():
    p = ARTIFACT_DIR / "lender_model.joblib"
    return joblib.load(p) if p.exists() else None


def available() -> bool:
    return _model() is not None


def _occupation(prof: dict) -> tuple[str, str | None]:
    occ = (prof or {}).get("occupation")
    salary = float((prof or {}).get("salary") or 0)
    pocket = float((prof or {}).get("pocket_money") or 0) + float((prof or {}).get("stipend") or 0)
    if occ == "student":
        return "Student", None
    if occ == "both":
        return ("Employed" if salary > pocket else "Student"), None
    if occ == "between_jobs":
        return "Student", "Between jobs is treated like a student applicant (the training data has no unemployed category)."
    if (prof or {}).get("self_employed"):
        return "Self-Employed", None
    return "Employed", None


def _row(m, base: dict) -> pd.DataFrame:
    r = {f: 0.0 for f in m["features"]}
    for k in m["numeric"]:
        r[k] = float(base[k])
    r[f"occ_{base['occupation']}"] = 1.0
    r[f"prod_{base['product']}"] = 1.0
    r[f"intent_{base['intent']}"] = 1.0
    return pd.DataFrame([r])[m["features"]]


def _predict(m, base: dict) -> float:
    return float(m["model"].predict_proba(_row(m, base))[0, 1])


def _contributions(m, base: dict) -> list[dict]:
    dm = xgb.DMatrix(_row(m, base))
    contrib = m["model"].get_booster().predict(dm, pred_contribs=True)[0][:-1]
    out = []
    for f, c in zip(m["features"], contrib):
        if abs(c) < 0.05:
            continue
        if f.startswith(("occ_", "prod_", "intent_")):
            if base.get({"occ": "occupation", "prod": "product", "intent": "intent"}[f.split("_")[0]]) != f.split("_", 1)[1]:
                continue
            label = {"occ": "Occupation", "prod": "Product", "intent": "Loan purpose"}[f.split("_")[0]] + f": {f.split('_', 1)[1]}"
            value = None
        else:
            label = m["labels"].get(f, f)
            value = base[f]
        out.append({"factor": label, "value": None if value is None else round(float(value), 3), "effect": round(float(c), 3),
                    "direction": "helps" if c > 0 else "hurts"})
    return sorted(out, key=lambda x: -abs(x["effect"]))[:8]


def assess(df, prof: dict, form: dict) -> dict:
    m = _model()
    if not m:
        return {"available": False, "message": "Lender model not trained. Run: python -m ml.train_lender_model"}
    prof = prof or {}
    principal, months, rate = float(form["principal"]), int(form["months"]), float(form.get("annual_rate_pct") or 14)
    emi = credit.emi(principal, rate, months)

    monthly_income, income_source, balance, obligations = 0.0, None, None, 0.0
    if df is not None and not df.empty:
        fx = extract(df)
        monthly_income = fx["features"]["avg_monthly_income"]
        obligations = fx["detail"]["existing_obligations_monthly"]
        bal = df[df["balance"].notna()]["balance"]
        balance = float(bal.iloc[-1]) if len(bal) else None
        income_source = "statement"
    if monthly_income <= 0:
        from .profile import declared_income
        monthly_income = declared_income(prof)
        obligations = float(prof.get("emi") or 0)
        income_source = "profile" if monthly_income > 0 else None
    if monthly_income <= 0:
        return {"available": True, "status": "insufficient_information",
                "message": "Add your income in your profile or upload a statement so we can estimate lender approval."}
    annual = monthly_income * 12
    occupation, occ_note = _occupation(prof)
    score_given = form.get("cibil_score") or prof.get("cibil_score")
    existing_debt = form.get("existing_debt_outstanding")
    if existing_debt in (None, ""):
        existing_debt = obligations * 12  # rough outstanding balance if the user hasn't told us
    assumed = []
    savings = form.get("savings")
    if savings in (None, ""):
        if balance is not None:
            savings = balance
        else:
            savings = 0.0
            assumed.append("savings unknown: treated as ₹0 (enter your savings to improve the estimate)")
    if not prof.get("occupation"):
        assumed.append(f"occupation not in your profile: assumed {occupation}")
    if not prof.get("age"):
        assumed.append(f"age not in your profile: assumed {20 if occupation == 'Student' else 26}")
    if not prof.get("years_working") and occupation != "Student":
        assumed.append("years in work not in your profile: assumed 0")
    base = {
        "credit_score": min(850.0, float(score_given)) if score_given else float(ASSUMED_SCORE),
        "credit_history_years": float(form.get("credit_history_years") if form.get("credit_history_years") is not None else prof.get("credit_history_years") or 0),
        "years_employed": float(prof.get("years_working") or 0),
        "age": float(prof.get("age") or (20 if occupation == "Student" else 26)),
        "defaults_on_file": 1.0 if form.get("past_default") else 0.0,
        "delinquencies_last_2yrs": float(form.get("late_payments_2y") or 0),
        "derogatory_marks": float(form.get("negative_records") or 0),
        "debt_to_income_ratio": float(existing_debt) / annual,
        "loan_to_income_ratio": principal / annual,
        "payment_to_income_ratio": emi * 12 / annual,
        "savings_to_income": float(savings or 0) / annual,
        "occupation": occupation, "product": form.get("product") or "Personal Loan", "intent": form.get("purpose") or ("Education" if occupation == "Student" else "Personal"),
    }
    outside = []
    for f, (lo, hi) in m["ranges"].items():
        if f in base and not (lo <= base[f] <= hi):
            if base[f] > 0:  # a zero (e.g. no debt) is simply raised to the model's floor, not "outside the range"
                outside.append(m["labels"].get(f, f))
            base[f] = min(max(base[f], lo), hi)
    p = _predict(m, base)
    band = "likely" if p >= LIKELY else "borderline" if p >= BORDERLINE else "unlikely"

    curve = [{"credit_score": sc, "approval": round(_predict(m, {**base, "credit_score": float(sc)}), 3)} for sc in (550, 600, 650, 700, 750, 800)]
    # largest loan (same term & rate) that keeps approval >= LIKELY
    lo_p, hi_p = 0.0, principal
    if p < LIKELY:
        for _ in range(25):
            mid = (lo_p + hi_p) / 2
            e = credit.emi(max(mid, 1), rate, months)
            pm = _predict(m, {**base, "loan_to_income_ratio": mid / annual, "payment_to_income_ratio": e * 12 / annual})
            lo_p, hi_p = (mid, hi_p) if pm >= LIKELY else (lo_p, mid)
        max_likely_loan = round(lo_p, -2) if lo_p >= 1000 else None
    else:
        max_likely_loan = None
    needed_score = next((c["credit_score"] for c in curve if c["approval"] >= LIKELY), None) if p < LIKELY else None
    tips = []
    if max_likely_loan:
        tips.append(f"Borrowing about ₹{max_likely_loan:,.0f} instead of ₹{principal:,.0f} (same {months} months) would make approval likely.")
    if needed_score and (not score_given or needed_score > float(score_given)):
        tips.append(f"A credit score of about {needed_score} or higher would make approval likely at the current amount.")
    if base["debt_to_income_ratio"] > 0.3:
        tips.append("Paying down existing debt first would lift your chances (lenders weigh debt-to-income heavily).")
    if base["credit_history_years"] < 1:
        tips.append("A short credit history weighs against you: a small secured card or a credit-builder product, paid on time, helps build it.")
    return {
        "available": True, "status": band, "approval_probability": round(p, 3), "model_version": m["version"],
        "inputs": {"monthly_income": round(monthly_income, 2), "income_source": income_source, "emi": round(emi, 2),
                   "occupation": occupation, "credit_score_used": base["credit_score"], "credit_score_assumed": not bool(score_given),
                   "product": base["product"], "purpose": base["intent"],
                   "ratios": {k: round(base[k], 3) for k in ("debt_to_income_ratio", "loan_to_income_ratio", "payment_to_income_ratio", "savings_to_income")}},
        "factors": _contributions(m, base), "credit_score_curve": curve, "max_likely_loan": max_likely_loan, "tips": tips,
        "notes": [n for n in [occ_note,
                              (f"No credit score given: assumed {ASSUMED_SCORE} (typical first-time borrower). See the curve for other scores."
                               if not score_given else None),
                              (f"Outside the training range and capped: {', '.join(outside)}." if outside else None),
                              ("Assumed: " + "; ".join(assumed) + "." if assumed else None)] if n],
        "caveat": "Estimate of how a lender might decide, learned from 50,000 realistic (synthetic) US & Canada applications. "
                  "It is not an approval, can reflect lender bias, and does not change the FinLens affordability status.",
    }
