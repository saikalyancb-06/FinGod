# Cash-flow Repayment-Risk Model — Model Card

**Version:** credit-lr-202611130007 · trained 2026-11-13 00:07:49

## Purpose and limits
A supporting signal inside FinLens Alternative Credit Intelligence. The primary
output is the **deterministic affordability calculation** (disposable income, EMI
headroom, stress tests). The model's risk band is shown alongside, never alone.

* **No existing loan model or dataset was available**, so this model was trained on synthetic data.
* **Target:** simulated "missed two or more instalments within 12 months", a repayment-outcome label rather than past approval decisions.
* **It does not prove default prediction.** The data is synthetic, so real-world performance is unknown.
* It never guarantees approval. Lender underwriting is separate.
* It never uses any AML / Money Mule signal. The two systems share no data.

**Deployed model:** logistic_regression (selected by held-out PR-AUC; the simpler model won on this data).

## Validation (temporal: train months 1-18, test months 19-24 (drift injected))
| Model | ROC-AUC | PR-AUC | Brier |
|---|---|---|---|
| XGBoost | 0.7786 | 0.26 | 0.0493 |
| Logistic regression | 0.7974 | 0.2874 | 0.0485 |
| Constant (train event rate) | 0.5 | 0.0581 | 0.055 |

Event rate: train 0.0433, test 0.0581 (drift visible).

Calibration (XGBoost, quantile bins): `[{"mean_predicted": 0.004, "observed": 0.003}, {"mean_predicted": 0.007, "observed": 0.016}, {"mean_predicted": 0.012, "observed": 0.025}, {"mean_predicted": 0.017, "observed": 0.027}, {"mean_predicted": 0.026, "observed": 0.041}, {"mean_predicted": 0.039, "observed": 0.07}, {"mean_predicted": 0.065, "observed": 0.072}, {"mean_predicted": 0.176, "observed": 0.21}]`

## Features
`avg_monthly_income, income_cv, essential_ratio, discretionary_ratio, obligation_ratio, savings_rate, balance_to_income, cashflow_trend, months_history, proposed_emi_ratio`: all are derived from the user's own consented statement (see `finlens/credit_features.py`).
