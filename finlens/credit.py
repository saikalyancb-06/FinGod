"""Alternative Credit Intelligence: cash-flow affordability, EMI simulation, stress tests.

Primary output = deterministic affordability. The ML risk band is a supporting
signal (see docs/CREDIT_MODEL_CARD.md). No AML data is ever used here.
"""
from __future__ import annotations

from functools import lru_cache

import joblib
import numpy as np

from shared.config import ARTIFACT_DIR

from .credit_features import CREDIT_FEATURES, extract

MAX_EMI_SHARE_OF_INCOME = 0.40     # total obligations incl. new EMI should stay under 40% of stable income
MAX_EMI_SHARE_OF_SURPLUS = 0.60    # new EMI should use at most 60% of monthly surplus


def emi(principal: float, annual_rate_pct: float, months: int) -> float:
    if months <= 0:
        raise ValueError("tenure must be positive")
    r = annual_rate_pct / 12 / 100
    if r == 0:
        return principal / months
    f = (1 + r) ** months
    return principal * r * f / (f - 1)


def principal_for_emi(emi_amt: float, annual_rate_pct: float, months: int) -> float:
    r = annual_rate_pct / 12 / 100
    if r == 0:
        return emi_amt * months
    f = (1 + r) ** months
    return emi_amt * (f - 1) / (r * f)


def amortization(principal: float, annual_rate_pct: float, months: int) -> list[dict]:
    e = emi(principal, annual_rate_pct, months)
    r = annual_rate_pct / 12 / 100
    bal, rows = principal, []
    for k in range(1, months + 1):
        interest = bal * r
        princ = e - interest
        bal = max(bal - princ, 0.0)
        rows.append({"month": k, "emi": round(e, 2), "interest": round(interest, 2), "principal": round(princ, 2), "balance": round(bal, 2)})
    return rows


@lru_cache(maxsize=1)
def _model():
    p = ARTIFACT_DIR / "credit_model.joblib"
    return joblib.load(p) if p.exists() else None


def _risk_band(feats: dict) -> dict | None:
    m = _model()
    if not m:
        return None
    x = np.array([[feats[k] for k in CREDIT_FEATURES]])
    p = float(m["model"].predict_proba(x)[0, 1])
    band = "lower" if p < 0.05 else "moderate" if p < 0.15 else "higher"
    return {"modelled_probability": round(p, 3), "band": band, "model_version": m["version"],
            "label_meaning": "Simulated probability of missing 2+ instalments in 12 months (synthetic training data).",
            "caveat": "Supporting signal only. Not validated on real repayment data; not a credit decision."}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def cashflow_score(income: float, expenses: float, discretionary: float, investment: float = 0.0) -> dict:
    """Transparent rule-based cash-flow score (300-850), ported from the team's CredWise
    project (reference/cred-wise/src/app/lib/creditScoreApi.ts). Not a bureau score and not ML.
    Inputs are monthly: recurring income, total spending, discretionary spending, investments."""
    if income <= 0:
        return {"score": None, "band": "insufficient information", "factors": [],
                "note": "No recurring income identified, so a cash-flow score can't be computed."}
    savings = max(income - expenses, 0.0)
    rs, re_, rd, rv = savings / income, expenses / income, discretionary / income, investment / income
    f = [
        ("Savings rate", _clamp(200 * rs, 0, 120), f"You keep {rs*100:.0f}% of income after spending."),
        ("Investments", _clamp(150 * rv, 0, 80), f"Investments are {rv*100:.0f}% of income." if rv else "No investments detected in the statement."),
        ("Spending vs 60% of income", _clamp(180 * (0.6 - re_), -120, 120), f"Spending is {re_*100:.0f}% of income (reference point 60%)."),
        ("Discretionary above 30%", -_clamp(220 * max(0.0, rd - 0.3), 0, 100), f"Discretionary spending is {rd*100:.0f}% of income (penalised above 30%)."),
    ]
    score = int(round(_clamp(600 + sum(v for _, v, _ in f), 300, 850)))
    band = "very low risk" if score >= 780 else "low risk" if score >= 700 else "medium risk" if score >= 600 else "high risk"
    return {"score": score, "band": band, "base": 600,
            "factors": [{"factor": n, "points": round(v, 1), "explanation": e} for n, v, e in f],
            "inputs": {"monthly_income": round(income, 2), "monthly_spending": round(expenses, 2),
                       "monthly_discretionary": round(discretionary, 2), "monthly_investment": round(investment, 2)},
            "method": "600 + clamp(200·savings_rate, 0..120) + clamp(150·investment_rate, 0..80) + clamp(180·(0.6 − spend_ratio), −120..120) − clamp(220·max(0, discretionary_ratio − 0.3), 0..100), clamped to 300–850",
            "source": "Rule-based formula from the CredWise project. Not a credit-bureau score and not a lending decision."}


def score_with_what_if(df, expense_cut_pct: float = 0.0, income_increase_pct: float = 0.0, investment: float = 0.0) -> dict:
    fx = extract(df)
    f = fx["features"]
    inc = f["avg_monthly_income"]
    ess, dis = inc * f["essential_ratio"], inc * f["discretionary_ratio"]
    obl = fx["detail"]["existing_obligations_monthly"]
    spend = ess + dis
    base = cashflow_score(inc, spend, dis, investment)
    cut = expense_cut_pct / 100
    wi = cashflow_score(inc * (1 + income_increase_pct / 100), spend - dis * cut, dis * (1 - cut), investment)
    return {"current": base, "what_if": wi, "what_if_inputs": {"discretionary_cut_pct": expense_cut_pct, "income_increase_pct": income_increase_pct},
            "existing_obligations_monthly": obl, "months_used": fx["detail"]["months_used"]}


def lender_model_payload(df, principal: float, months: int, extra: dict) -> dict:
    """Payload for the CredWise external loan-approval API (Kaggle loan-approval schema)."""
    inc = extract(df)["features"]["avg_monthly_income"]
    return {
        "no_of_dependents": int(extra.get("no_of_dependents", 0)),
        "education": extra.get("education", "Graduate"),
        "self_employed": extra.get("self_employed", "No"),
        "income_annum": round(float(extra.get("income_annum") or inc * 12), 2),
        "loan_amount": float(principal),
        "loan_term": int(months),
        "residential_assets": float(extra.get("residential_assets", 0)),
        "commercial_assets": float(extra.get("commercial_assets", 0)),
        "luxury_assets": float(extra.get("luxury_assets", 0)),
        "bank_assets": float(extra.get("bank_assets", 0)),
    }


def assess(df, principal: float, annual_rate_pct: float, months: int, declared_obligations: float = 0.0) -> dict:
    e = emi(principal, annual_rate_pct, months)
    fx = extract(df, proposed_emi=e, declared_obligations=declared_obligations)
    f, d = fx["features"], fx["detail"]
    missing, supports, limits = [], [], []

    income = f["avg_monthly_income"]
    if f["months_history"] < 2:
        missing.append("At least 2 complete months of transactions are needed.")
    if income <= 0:
        missing.append("No recurring income identified. Credits like refunds, own-account transfers and borrowed money are not counted as income.")

    essential = income * f["essential_ratio"]
    discretionary = income * f["discretionary_ratio"]
    obligations = d["existing_obligations_monthly"]
    disposable = income - essential - obligations
    surplus = income - essential - discretionary - obligations
    cap_income = MAX_EMI_SHARE_OF_INCOME * income - obligations
    cap_surplus = MAX_EMI_SHARE_OF_SURPLUS * max(disposable, 0) if surplus <= 0 else MAX_EMI_SHARE_OF_SURPLUS * max(surplus, disposable * 0.5)
    affordable_emi = max(0.0, min(cap_income, cap_surplus))

    def scenario(name, inc_mult=1.0, ess_mult=1.0, rate_add=0.0):
        inc_s = income * inc_mult
        ess_s = essential * ess_mult
        e_s = emi(principal, annual_rate_pct + rate_add, months)
        disp_s = inc_s - ess_s - obligations
        head = disp_s - e_s
        return {"scenario": name, "income": round(inc_s, 2), "essential": round(ess_s, 2), "emi": round(e_s, 2),
                "disposable_after_emi": round(head, 2), "emi_share_of_income": round(e_s / inc_s, 3) if inc_s else None,
                "passes": bool(head > 0 and inc_s > 0 and (e_s + obligations) / inc_s <= 0.5)}

    stress = [
        scenario("Base case"),
        scenario("Income falls 20%", inc_mult=0.8),
        scenario("Essential costs rise 15%", ess_mult=1.15),
        scenario("Interest rate +2 pts", rate_add=2.0),
        scenario("Income -20% and costs +15%", inc_mult=0.8, ess_mult=1.15),
    ]

    if income > 0:
        (supports if f["income_cv"] < 0.15 else limits).append(
            f"Income variability across months is {f['income_cv']*100:.0f}% ({'stable' if f['income_cv'] < 0.15 else 'variable'}).")
        (supports if f["savings_rate"] > 0.1 else limits).append(f"Average monthly surplus is {f['savings_rate']*100:.0f}% of income.")
        (supports if f["obligation_ratio"] < 0.2 else limits).append(f"Existing obligations take {f['obligation_ratio']*100:.0f}% of income.")
        (supports if f["cashflow_trend"] >= 0 else limits).append(
            f"Net cash flow is {'improving' if f['cashflow_trend'] >= 0 else 'deteriorating'} over the period analysed.")
        (supports if e <= affordable_emi else limits).append(
            f"Requested EMI ₹{e:,.0f} vs estimated affordable EMI ₹{affordable_emi:,.0f}.")

    passes = sum(s["passes"] for s in stress)
    if missing:
        status = "insufficient_information"
    elif e <= affordable_emi and stress[0]["passes"] and passes >= 4:
        status = "eligible"
    elif e <= affordable_emi * 1.25 and stress[0]["passes"]:
        status = "conditional"
    else:
        status = "not_affordable_at_these_terms"

    suggestions = []
    if affordable_emi > 0:
        for t in (12, 24, 36):
            suggestions.append({"tenure_months": t, "max_principal": round(principal_for_emi(affordable_emi, annual_rate_pct, t), -2),
                                "emi": round(affordable_emi, 2)})

    return {
        "status": status,
        "status_meaning": {
            "eligible": "Estimated affordable under base and most stress scenarios.",
            "conditional": "Close to the affordability limit. A smaller amount or longer tenure would be safer.",
            "not_affordable_at_these_terms": "The EMI exceeds estimated repayment capacity at these terms.",
            "insufficient_information": "Not enough reliable data to estimate affordability.",
        }[status],
        "requested": {"principal": principal, "annual_rate_pct": annual_rate_pct, "months": months, "emi": round(e, 2),
                      "total_interest": round(e * months - principal, 2)},
        "cash_flow": {"avg_monthly_income": round(income, 2), "essential": round(essential, 2), "discretionary": round(discretionary, 2),
                      "existing_obligations": round(obligations, 2), "disposable_income": round(disposable, 2),
                      "monthly_surplus": round(surplus, 2), "income_volatility_cv": round(f["income_cv"], 3),
                      "cashflow_trend": round(f["cashflow_trend"], 3), "average_balance": d["average_balance"],
                      "months_used": d["months_used"], "monthly_income": d["monthly_income"]},
        "affordable_emi": round(affordable_emi, 2),
        "affordability_rule": f"min({MAX_EMI_SHARE_OF_INCOME:.0%} of income - existing obligations, {MAX_EMI_SHARE_OF_SURPLUS:.0%} of monthly surplus)",
        "illustrative_options": suggestions,
        "stress_tests": stress,
        "supporting_factors": supports, "limiting_factors": limits, "missing_information": missing,
        "model_signal": _risk_band(f) if not missing else None,
        "disclaimer": "Estimate for decision support only. It is not a loan approval; lender underwriting and regulatory checks remain separate. No fraud or AML data is used.",
    }
