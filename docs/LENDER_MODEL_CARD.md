# Lender-Style Approval Model — Model Card

**Version:** lender-xgb-202611131941 · trained 2026-11-13 19:41:06 · 1210 trees

Built into FinLens to replace the team's unavailable CredWise loan-approval API (same purpose: "would a lender
likely approve this application?"), with explanations and improvement suggestions.

## Data and label
* Realistic Loan Approval Dataset (US & Canada), Parth Patel / Kaggle, via KaoLEAF splits on Hugging Face — 50,000 synthetic applications mimicking lender rules.
* Splits: 35,000 train / 5,000 validation (early stopping) / **10,000 untouched test**. Approval rate 55%.
* **Label:** loan_status: 1 = approved, 0 = declined (lender decision, not repayment). It learns *how lenders decide*, so it can also learn their biases; it is not a repayment model
  (that is the Lending Club repayment-risk band).

## Features (scale-free, so they transfer to INR applicants)
Credit score and history length, years in work, age, past defaults / late payments / negative records, debt-to-income,
loan-to-income, repayment-to-income, savings-to-income, occupation (incl. **Student**), product and loan purpose.
Interest rate is excluded (priced after the decision). **Monotonic constraints:** approval never falls when the credit
score, history, work years or savings rise, and never rises when debt, loan size, repayment burden or adverse records rise.

## Test results (10,000 applications never used in training)
| Model | ROC-AUC | Accuracy | F1 | Brier |
|---|---|---|---|---|
| **XGBoost (monotonic, deployed)** | **0.9822** | **0.9216** | 0.9289 | 0.0533 |
| Logistic regression | 0.9425 | 0.8642 | 0.8777 | 0.0953 |
| Majority class | 0.5 | 0.5505 | — | — |

| Occupation | Test applications | ROC-AUC | Accuracy |
|---|---|---|---|
| Employed | 6,978 | 0.9818 | 0.9206 |
| Self-Employed | 2,013 | 0.9812 | 0.922 |
| Student | 1,009 | 0.987 | 0.9277 |

Calibration (quantile bins): `[{"predicted": 0.0, "observed": 0.0}, {"predicted": 0.001, "observed": 0.0}, {"predicted": 0.017, "observed": 0.016}, {"predicted": 0.137, "observed": 0.153}, {"predicted": 0.509, "observed": 0.517}, {"predicted": 0.85, "observed": 0.847}, {"predicted": 0.969, "observed": 0.974}, {"predicted": 0.994, "observed": 0.998}, {"predicted": 0.999, "observed": 1.0}, {"predicted": 1.0, "observed": 1.0}]`

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
