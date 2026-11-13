"""Built-in lender-style approval model (replaces the unavailable CredWise external API).

Data: "Realistic Loan Approval Dataset (US & Canada)" by Parth Patel (Kaggle), 50,000 applications
      generated to mimic lender approval rules, via the KaoLEAF benchmark splits on Hugging Face
      (kaopanboonyuen/loan-approval-us-ca: train 35k / validation 5k / test 10k).
Label: loan_status (1 = approved, 0 = declined) — a lender DECISION, not repayment.

Features are scale-free so they transfer to INR applicants: credit score & history, employment,
age, product, purpose, adverse records, and ratios (debt/income, loan/income, payment/income,
savings/income). Interest rate is excluded (it is priced after the risk decision).
Monotonic constraints keep explanations sensible (e.g. a higher credit score never lowers approval).

  python -m ml.train_lender_model
"""
from __future__ import annotations

import json
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, average_precision_score, brier_score_loss, f1_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from shared.config import ARTIFACT_DIR, ROOT

SRC = ROOT / "data" / "external" / "loan_approval" / "kaopanboonyuen" / "data"
OCC = ["Employed", "Self-Employed", "Student"]
PRODUCTS = ["Credit Card", "Personal Loan", "Line of Credit"]
INTENTS = ["Personal", "Education", "Medical", "Business", "Home Improvement", "Debt Consolidation"]
NUMERIC = ["credit_score", "credit_history_years", "years_employed", "age", "defaults_on_file", "delinquencies_last_2yrs",
           "derogatory_marks", "debt_to_income_ratio", "loan_to_income_ratio", "payment_to_income_ratio", "savings_to_income"]
FEATURES = NUMERIC + [f"occ_{o}" for o in OCC] + [f"prod_{p}" for p in PRODUCTS] + [f"intent_{i}" for i in INTENTS]
# +1 = approval may only rise with the feature, -1 = only fall, 0 = free
MONO = {"credit_score": 1, "credit_history_years": 1, "years_employed": 1, "defaults_on_file": -1, "delinquencies_last_2yrs": -1,
        "derogatory_marks": -1, "debt_to_income_ratio": -1, "loan_to_income_ratio": -1, "payment_to_income_ratio": -1, "savings_to_income": 1}
LABELS = {"credit_score": "Credit score", "credit_history_years": "Length of credit history", "years_employed": "Years in work",
          "age": "Age", "defaults_on_file": "Past default on file", "delinquencies_last_2yrs": "Late payments (2 yrs)",
          "derogatory_marks": "Negative credit records", "debt_to_income_ratio": "Existing debt vs income",
          "loan_to_income_ratio": "Loan size vs income", "payment_to_income_ratio": "Repayment vs income",
          "savings_to_income": "Savings vs income"}


def featurise(d: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=d.index)
    for c in NUMERIC:
        if c == "savings_to_income":
            X[c] = (d["savings_assets"] / d["annual_income"]).clip(0, 5) if "savings_assets" in d else d[c]
        else:
            X[c] = d[c].astype(float)
    for o in OCC:
        X[f"occ_{o}"] = (d["occupation_status"] == o).astype(float)
    for p in PRODUCTS:
        X[f"prod_{p}"] = (d["product_type"] == p).astype(float)
    for i in INTENTS:
        X[f"intent_{i}"] = (d["loan_intent"] == i).astype(float)
    return X[FEATURES]


def block(y, s):
    frac, mean = calibration_curve(y, s, n_bins=10, strategy="quantile")
    return {"roc_auc": round(roc_auc_score(y, s), 4), "pr_auc": round(average_precision_score(y, s), 4),
            "accuracy": round(accuracy_score(y, s >= 0.5), 4), "f1": round(f1_score(y, s >= 0.5), 4),
            "brier": round(brier_score_loss(y, s), 4),
            "calibration": [{"predicted": round(float(a), 3), "observed": round(float(b), 3)} for a, b in zip(mean, frac)]}


def main():
    t0 = time.time()
    tr, va, te = (pd.read_parquet(SRC / f"{s}.parquet") for s in ("train", "validation", "test"))
    Xtr, Xva, Xte = featurise(tr), featurise(va), featurise(te)
    ytr, yva, yte = tr["loan_status"].values, va["loan_status"].values, te["loan_status"].values
    mono = "(" + ",".join(str(MONO.get(f, 0)) for f in FEATURES) + ")"
    params = dict(n_estimators=2000, max_depth=5, learning_rate=0.03, subsample=0.85, colsample_bytree=0.85, min_child_weight=5,
                  tree_method="hist", n_jobs=12, random_state=7, eval_metric="logloss", monotone_constraints=mono)
    xgb = XGBClassifier(**params, early_stopping_rounds=100).fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
    best = int(xgb.best_iteration) + 1
    s = xgb.predict_proba(Xte)[:, 1]
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(Xtr, ytr)
    res = {"xgboost_monotonic": block(yte, s), "logistic_regression": block(yte, lr.predict_proba(Xte)[:, 1]),
           "constant_baseline": {"roc_auc": 0.5, "accuracy": round(max(yte.mean(), 1 - yte.mean()), 4)}}
    for occ in OCC:
        m = te["occupation_status"].values == occ
        res[f"by_occupation_{occ}"] = {"n": int(m.sum()), "roc_auc": round(roc_auc_score(yte[m], s[m]), 4), "accuracy": round(accuracy_score(yte[m], s[m] >= 0.5), 4)}
    # final model on train+validation with the tuned tree count
    final = XGBClassifier(**{**params, "n_estimators": best}).fit(pd.concat([Xtr, Xva]), np.concatenate([ytr, yva]))
    ranges = {f: [float(pd.concat([Xtr, Xva])[f].quantile(0.005)), float(pd.concat([Xtr, Xva])[f].quantile(0.995))] for f in NUMERIC}
    imp = sorted(zip(FEATURES, final.feature_importances_.tolist()), key=lambda t: -t[1])
    meta = {"model_version": f"lender-xgb-{time.strftime('%Y%m%d%H%M')}", "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "data": "Realistic Loan Approval Dataset (US & Canada), Parth Patel / Kaggle, via KaoLEAF splits on Hugging Face — 50,000 synthetic applications mimicking lender rules",
            "label": "loan_status: 1 = approved, 0 = declined (lender decision, not repayment)",
            "rows": {"train": len(tr), "validation": len(va), "test": len(te)}, "approval_rate": round(float(tr["loan_status"].mean()), 4),
            "trees": best, "features": FEATURES, "monotonic": MONO, "ranges_p0.5_p99.5": ranges, "results": res,
            "feature_importance": [(k, round(v, 4)) for k, v in imp[:12]], "train_seconds": round(time.time() - t0, 1)}
    joblib.dump({"model": final, "features": FEATURES, "numeric": NUMERIC, "occupations": OCC, "products": PRODUCTS,
                 "intents": INTENTS, "labels": LABELS, "ranges": ranges, "version": meta["model_version"]}, ARTIFACT_DIR / "lender_model.joblib")
    (ARTIFACT_DIR / "lender_metrics.json").write_text(json.dumps(meta, indent=2))
    x, l = res["xgboost_monotonic"], res["logistic_regression"]
    occ_rows = "\n".join(f"| {o} | {res[f'by_occupation_{o}']['n']:,} | {res[f'by_occupation_{o}']['roc_auc']} | {res[f'by_occupation_{o}']['accuracy']} |" for o in OCC)
    card = f"""# Lender-Style Approval Model — Model Card

**Version:** {meta['model_version']} · trained {meta['trained_at']} · {best} trees

Built into FinLens to replace the team's unavailable CredWise loan-approval API (same purpose: "would a lender
likely approve this application?"), with explanations and improvement suggestions.

## Data and label
* {meta['data']}.
* Splits: {len(tr):,} train / {len(va):,} validation (early stopping) / **{len(te):,} untouched test**. Approval rate {meta['approval_rate']*100:.0f}%.
* **Label:** {meta['label']}. It learns *how lenders decide*, so it can also learn their biases; it is not a repayment model
  (that is the Lending Club repayment-risk band).

## Features (scale-free, so they transfer to INR applicants)
Credit score and history length, years in work, age, past defaults / late payments / negative records, debt-to-income,
loan-to-income, repayment-to-income, savings-to-income, occupation (incl. **Student**), product and loan purpose.
Interest rate is excluded (priced after the decision). **Monotonic constraints:** approval never falls when the credit
score, history, work years or savings rise, and never rises when debt, loan size, repayment burden or adverse records rise.

## Test results ({len(te):,} applications never used in training)
| Model | ROC-AUC | Accuracy | F1 | Brier |
|---|---|---|---|---|
| **XGBoost (monotonic, deployed)** | **{x['roc_auc']}** | **{x['accuracy']}** | {x['f1']} | {x['brier']} |
| Logistic regression | {l['roc_auc']} | {l['accuracy']} | {l['f1']} | {l['brier']} |
| Majority class | 0.5 | {res['constant_baseline']['accuracy']} | — | — |

| Occupation | Test applications | ROC-AUC | Accuracy |
|---|---|---|---|
{occ_rows}

Calibration (quantile bins): `{json.dumps(x['calibration'])}`

## How FinLens uses it
Inputs come from the user's statement (income, existing EMIs, balance), profile (age, occupation, years working) and the
loan form (amount, term, purpose, product, optional CIBIL score, credit history, adverse records). Output: approval
likelihood, the factors helping and hurting (per-applicant SHAP contributions), a credit-score sensitivity curve, and the
largest loan amount that keeps approval likely. It is an **estimate of lender behaviour, not an approval**; it never uses
AML data and never changes the FinLens affordability status.

## Limitations
* Synthetic data generated from lender-style rules (US & Canada); real Indian lenders, CIBIL scoring and products differ.
* CIBIL (300–900) is used on the dataset's 300–850 credit-score scale (values above 850 are capped).
* Approval ≠ affordability: always read it together with the FinLens affordability estimate.
"""
    (ROOT / "docs" / "LENDER_MODEL_CARD.md").write_text(card, encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "calibration"} for k, v in res.items()}, indent=2))
    print("top features", meta["feature_importance"][:8])


if __name__ == "__main__":
    main()
