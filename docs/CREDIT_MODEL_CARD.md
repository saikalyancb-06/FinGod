# Repayment-Risk Model — Model Card

**Version:** credit-xgb-lc-202611131919 · trained 2026-11-13 19:19:38 · 0.4 min

## Data and label
* **Lending Club accepted loans 2007-2018Q4 (real loans, real outcomes; CC0)**. Train: 453,805 loans issued 2007–2014; out-of-time test: 375,145 loans issued 2015;
  final model refit on 828,950 finished loans (2007–2015).
* **Label:** Charged Off / Default (1) vs Fully Paid (0); unfinished loans excluded — an actual **repayment outcome**, not a past approval decision.

## Features (what FinLens can actually supply)
`emi_ratio, obligation_ratio, total_debt_ratio, term_months` — scale-free ratios computed from the user's statement and requested loan.
Interest rate, Lending Club grade **and loan-to-income** are excluded: EMI ÷ loan size at a given term reveals the
interest rate, which Lending Club sets from its own risk grade (hidden leakage — with it the model scored 0.715,
honest score without it is reported below). Risk is constrained to be **non-decreasing** in every feature, and app
inputs are clipped to the training range (0.5–99.5th percentile).

## Out-of-time results (2015)
| Model | ROC-AUC | PR-AUC | Brier |
|---|---|---|---|
| **Deployed** XGBoost (monotonic), FinLens features | 0.672 | 0.3513 | 0.151 |
| Logistic regression, FinLens features | 0.671 | 0.3521 | 0.1513 |
| Benchmark XGBoost + credit-bureau fields (not deployable) | 0.7239 | 0.4039 | 0.145 |
| Constant baseline | 0.5 | 0.2018 | 0.1621 |

Observed 2015 default rate by risk band: `{"lower": 0.1132, "moderate": 0.1649, "higher": 0.3284}`
Cross-dataset check on Home Credit (307,499 applicants, different lender/country): ROC-AUC 0.5196, PR-AUC 0.084 (base rate 0.0807).

Calibration (deployed, quantile bins): `[{"predicted": 0.086, "observed": 0.097}, {"predicted": 0.101, "observed": 0.111}, {"predicted": 0.113, "observed": 0.125}, {"predicted": 0.124, "observed": 0.139}, {"predicted": 0.138, "observed": 0.155}, {"predicted": 0.156, "observed": 0.172}, {"predicted": 0.181, "observed": 0.199}, {"predicted": 0.214, "observed": 0.245}, {"predicted": 0.269, "observed": 0.331}, {"predicted": 0.371, "observed": 0.446}]`

## How it is used in FinLens
A **supporting risk band** (lower / moderate / higher, cut at the 33rd/67th percentiles of 2015 scores) next to the
deterministic affordability calculation, which remains the primary output. It never approves or rejects a loan and
never uses AML data.

## Limitations
* US peer-to-peer loans (2007–2015) — borrower population, products and economy differ from Indian students/young
  professionals; treat the band as indicative only.
* Only affordability ratios are used, so discrimination is modest by design; the bureau benchmark shows the gap.
* Lending Club only lists *accepted* loans (selection bias: rejected applicants have no outcomes).
