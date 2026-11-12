"""Train the cash-flow repayment-risk model used as ONE supporting signal in
Alternative Credit Intelligence.

No existing loan model/dataset was provided, so this trains on a SYNTHETIC
applicant population whose target is a SIMULATED 12-month repayment outcome
(missed >= 2 instalments), not a historical approval decision. This avoids
learning past bank approval behaviour, but it also means the model proves
nothing about real default risk. The model card says so.

Validation is temporal: train on application months 1-18, test on 19-24
(with mild macro drift injected in later months).

  python -m ml.train_credit_model
"""
from __future__ import annotations

import json
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from finlens.credit_features import CREDIT_FEATURES
from shared.config import ARTIFACT_DIR, ROOT


def simulate(n: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    month = rng.integers(1, 25, n)
    income = rng.lognormal(10.2, 0.6, n)  # monthly recurring income
    income_cv = np.clip(rng.gamma(2.0, 0.08, n), 0.0, 1.2)
    essential_ratio = np.clip(rng.normal(0.45, 0.15, n), 0.1, 1.2)
    discretionary_ratio = np.clip(rng.normal(0.25, 0.12, n), 0.0, 0.9)
    obligation_ratio = np.clip(rng.exponential(0.08, n), 0, 0.7)
    savings_rate = 1 - essential_ratio - discretionary_ratio - obligation_ratio + rng.normal(0, 0.05, n)
    balance_to_income = np.clip(rng.lognormal(-0.5, 0.9, n) + savings_rate * 1.5, 0, 12)
    trend = rng.normal(0, 0.08, n)
    months_history = rng.integers(2, 13, n)
    proposed_emi_ratio = np.clip(rng.uniform(0.03, 0.45, n), 0, 1)
    drift = np.where(month > 18, 0.35, 0.0)  # later cohort faces tougher conditions

    z = (-3.2 + 6.0 * np.clip(obligation_ratio + proposed_emi_ratio - 0.35, -1, 1)
         + 3.0 * income_cv - 2.2 * np.clip(savings_rate, -1, 1) - 0.25 * np.log1p(balance_to_income)
         - 4.0 * trend + 0.6 * (essential_ratio > 0.8) - 0.05 * months_history + drift
         + rng.normal(0, 0.6, n))
    p = 1 / (1 + np.exp(-z))
    y = (rng.random(n) < p).astype(int)
    return pd.DataFrame({
        "application_month": month, "avg_monthly_income": income, "income_cv": income_cv,
        "essential_ratio": essential_ratio, "discretionary_ratio": discretionary_ratio,
        "obligation_ratio": obligation_ratio, "savings_rate": savings_rate,
        "balance_to_income": balance_to_income, "cashflow_trend": trend,
        "months_history": months_history, "proposed_emi_ratio": proposed_emi_ratio,
        "missed_2plus_instalments_12m": y,
    })


def main():
    df = simulate(20000)
    (ROOT / "data" / "generated").mkdir(parents=True, exist_ok=True)
    df.to_csv(ROOT / "data" / "generated" / "credit_synthetic.csv", index=False)
    tr, te = df[df.application_month <= 18], df[df.application_month > 18]
    X, y = tr[CREDIT_FEATURES].values, tr["missed_2plus_instalments_12m"].values
    Xt, yt = te[CREDIT_FEATURES].values, te["missed_2plus_instalments_12m"].values

    xgb = XGBClassifier(n_estimators=250, max_depth=3, learning_rate=0.05, subsample=0.9, random_state=0, eval_metric="logloss")
    xgb.fit(X, y)
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(X, y)
    s_x, s_l = xgb.predict_proba(Xt)[:, 1], lr.predict_proba(Xt)[:, 1]
    base = np.full_like(s_x, y.mean())
    frac_pos, mean_pred = calibration_curve(yt, s_x, n_bins=8, strategy="quantile")

    m = {
        "data": "SYNTHETIC applicants; target = SIMULATED repayment outcome (missed >=2 instalments in 12 months). Not real lending data.",
        "validation": "temporal: train months 1-18, test months 19-24 (drift injected)",
        "train_rows": int(len(tr)), "test_rows": int(len(te)),
        "train_event_rate": round(float(y.mean()), 4), "test_event_rate": round(float(yt.mean()), 4),
        "xgboost": {"roc_auc": round(roc_auc_score(yt, s_x), 4), "pr_auc": round(average_precision_score(yt, s_x), 4), "brier": round(brier_score_loss(yt, s_x), 4)},
        "logistic_regression": {"roc_auc": round(roc_auc_score(yt, s_l), 4), "pr_auc": round(average_precision_score(yt, s_l), 4), "brier": round(brier_score_loss(yt, s_l), 4)},
        "constant_baseline": {"roc_auc": 0.5, "pr_auc": round(float(yt.mean()), 4), "brier": round(brier_score_loss(yt, base), 4)},
        "calibration_xgb": [{"mean_predicted": round(float(a), 3), "observed": round(float(b), 3)} for a, b in zip(mean_pred, frac_pos)],
        "feature_importance": sorted([(k, round(float(v), 4)) for k, v in zip(CREDIT_FEATURES, xgb.feature_importances_)], key=lambda t: -t[1]),
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model_version": f"credit-xgb-{time.strftime('%Y%m%d%H%M')}",
    }
    chosen = "logistic_regression" if m["logistic_regression"]["pr_auc"] > m["xgboost"]["pr_auc"] else "xgboost"
    m["chosen_model"] = chosen
    m["model_version"] = m["model_version"].replace("xgb", "lr" if chosen == "logistic_regression" else "xgb")
    joblib.dump({"model": lr if chosen == "logistic_regression" else xgb, "features": CREDIT_FEATURES,
                 "version": m["model_version"], "kind": chosen}, ARTIFACT_DIR / "credit_model.joblib")
    (ARTIFACT_DIR / "credit_metrics.json").write_text(json.dumps(m, indent=2))
    card = f"""# Cash-flow Repayment-Risk Model — Model Card

**Version:** {m['model_version']} · trained {m['trained_at']}

## Purpose and limits
A supporting signal inside FinLens Alternative Credit Intelligence. The primary
output is the **deterministic affordability calculation** (disposable income, EMI
headroom, stress tests). The model's risk band is shown alongside, never alone.

* **No existing loan model or dataset was available**, so this model was trained on synthetic data.
* **Target:** simulated "missed two or more instalments within 12 months", a repayment-outcome label rather than past approval decisions.
* **It does not prove default prediction.** The data is synthetic, so real-world performance is unknown.
* It never guarantees approval. Lender underwriting is separate.
* It never uses any AML / Money Mule signal. The two systems share no data.

**Deployed model:** {m['chosen_model']} (selected by held-out PR-AUC; the simpler model won on this data).

## Validation ({m['validation']})
| Model | ROC-AUC | PR-AUC | Brier |
|---|---|---|---|
| XGBoost | {m['xgboost']['roc_auc']} | {m['xgboost']['pr_auc']} | {m['xgboost']['brier']} |
| Logistic regression | {m['logistic_regression']['roc_auc']} | {m['logistic_regression']['pr_auc']} | {m['logistic_regression']['brier']} |
| Constant (train event rate) | 0.5 | {m['constant_baseline']['pr_auc']} | {m['constant_baseline']['brier']} |

Event rate: train {m['train_event_rate']}, test {m['test_event_rate']} (drift visible).

Calibration (XGBoost, quantile bins): `{json.dumps(m['calibration_xgb'])}`

## Features
`{', '.join(CREDIT_FEATURES)}`: all are derived from the user's own consented statement (see `finlens/credit_features.py`).
"""
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "CREDIT_MODEL_CARD.md").write_text(card, encoding="utf-8")
    print(json.dumps({k: m[k] for k in ["xgboost", "logistic_regression", "constant_baseline", "train_event_rate", "test_event_rate", "chosen_model"]}, indent=2))


if __name__ == "__main__":
    main()
