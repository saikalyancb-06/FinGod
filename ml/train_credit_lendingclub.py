"""Train the repayment-risk model on REAL loan outcomes (Lending Club 2007-2018).

Data (python -m ml.download_datasets --only lending / --only homecredit):
  Lending Club accepted loans 2007-2018Q4 (~2.26M loans, CC0)    -> train/test (time split)
  Home Credit application_train (~307k applicants)               -> cross-dataset check

Label: 1 = Charged Off / Default (real repayment failure), 0 = Fully Paid.
Loans still Current/Late are excluded (outcome unknown).

Deployed model ("finlens-mappable"): only scale-free affordability ratios that
FinLens can compute from a statement + the requested loan:
    emi_ratio          requested EMI / monthly income
    obligation_ratio   existing debt payments / monthly income   (LC: dti/100)
    total_debt_ratio   emi_ratio + obligation_ratio
    term_months        36 or 60 (any term in FinLens; clipped to the training range)
Interest rate and LC grade are deliberately EXCLUDED, and so is loan_to_income: EMI + loan size + term
reconstruct the interest rate, i.e. Lending Club's own risk grade (hidden leakage). FinLens users pick
the rate freely, so a model that leans on it would be misleading. Risk is constrained to be
non-decreasing in every feature.

A "bureau benchmark" model with credit-bureau fields (FICO, utilisation, inquiries, ...)
is also trained ONLY to report how much signal FinLens cannot see.

Split: train on loans issued 2007-2014, test on 2015 (out-of-time).

  python -m ml.train_credit_lendingclub
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

from shared.config import ARTIFACT_DIR, ROOT

LC = ROOT / "data" / "external" / "lending_club" / "accepted_2007_to_2018Q4.csv"
HC_DIR = ROOT / "data" / "external" / "home_credit"
FEATURES = ["emi_ratio", "obligation_ratio", "total_debt_ratio", "term_months"]
# NOTE: loan_to_income is deliberately NOT a feature: together with emi_ratio and term it reconstructs the
# interest rate, which Lending Club sets from its own risk grade (hidden label leakage, AUC 0.715 -> 0.672 honest).
BUREAU = ["loan_to_income", "fico_range_low", "revol_util", "inq_last_6mths", "delinq_2yrs", "pub_rec", "open_acc", "total_acc",
          "mort_acc", "emp_years", "home_own", "home_mortgage"]
BAD = {"Charged Off", "Default", "Does not meet the credit policy. Status:Charged Off"}
GOOD = {"Fully Paid", "Does not meet the credit policy. Status:Fully Paid"}


def load_lc() -> pd.DataFrame:
    cols = ["loan_amnt", "term", "installment", "annual_inc", "dti", "issue_d", "loan_status", "fico_range_low",
            "revol_util", "inq_last_6mths", "delinq_2yrs", "pub_rec", "open_acc", "total_acc", "mort_acc",
            "emp_length", "home_ownership", "application_type"]
    t0 = time.time()
    df = pd.read_csv(LC, usecols=cols, engine="pyarrow")
    print(f"read {len(df):,} Lending Club rows in {time.time()-t0:.0f}s")
    df = df[df["loan_status"].isin(BAD | GOOD)]
    df = df[(df["application_type"].fillna("Individual") == "Individual") & (df["annual_inc"] > 1000) & df["installment"].notna()]
    df["y"] = df["loan_status"].isin(BAD).astype(np.int8)
    df["year"] = pd.to_datetime(df["issue_d"], format="%b-%Y", errors="coerce").dt.year
    mi = df["annual_inc"] / 12.0
    df["emi_ratio"] = (df["installment"] / mi).clip(0, 3)
    df["obligation_ratio"] = (df["dti"] / 100.0).clip(0, 3)
    df["total_debt_ratio"] = df["emi_ratio"] + df["obligation_ratio"]
    df["loan_to_income"] = (df["loan_amnt"] / df["annual_inc"]).clip(0, 10)
    df["term_months"] = df["term"].astype(str).str.extract(r"(\d+)")[0].astype(float)
    df["emp_years"] = df["emp_length"].astype(str).str.extract(r"(\d+)")[0].astype(float)
    df["home_own"] = (df["home_ownership"] == "OWN").astype(float)
    df["home_mortgage"] = (df["home_ownership"] == "MORTGAGE").astype(float)
    print(f"finished loans used: {len(df):,} (default rate {df['y'].mean()*100:.1f}%), years {int(df['year'].min())}-{int(df['year'].max())}")
    return df


def load_home_credit() -> pd.DataFrame | None:
    files = sorted(HC_DIR.rglob("application_train*/*.parquet")) if HC_DIR.exists() else []
    if not files:
        return None
    hc = pd.concat([pd.read_parquet(f, columns=["TARGET", "AMT_INCOME_TOTAL", "AMT_CREDIT", "AMT_ANNUITY"]) for f in files])
    hc = hc.dropna()
    hc = hc[hc["AMT_INCOME_TOTAL"] > 0]
    mi = hc["AMT_INCOME_TOTAL"] / 12.0
    out = pd.DataFrame({
        "emi_ratio": (hc["AMT_ANNUITY"] / 12.0 / mi).clip(0, 3),  # Home Credit AMT_ANNUITY is the yearly annuity
        "obligation_ratio": np.nan,                             # not available at application in this table
        "loan_to_income": (hc["AMT_CREDIT"] / hc["AMT_INCOME_TOTAL"]).clip(0, 10),
        "term_months": (hc["AMT_CREDIT"] / (hc["AMT_ANNUITY"] / 12.0)).clip(6, 360),
        "y": hc["TARGET"].astype(np.int8),
    })
    out["total_debt_ratio"] = out["emi_ratio"]
    return out


def block(y, s):
    frac, mean = calibration_curve(y, s, n_bins=10, strategy="quantile")
    return {"roc_auc": round(roc_auc_score(y, s), 4), "pr_auc": round(average_precision_score(y, s), 4),
            "brier": round(brier_score_loss(y, s), 4), "base_rate": round(float(np.mean(y)), 4),
            "calibration": [{"predicted": round(float(a), 3), "observed": round(float(b), 3)} for a, b in zip(mean, frac)]}


def main():
    t_start = time.time()
    df = load_lc()
    tr, te = df[df["year"] <= 2014], df[df["year"] == 2015]
    print(f"train 2007-2014: {len(tr):,} loans · test 2015: {len(te):,} loans")
    params = dict(n_estimators=400, max_depth=5, learning_rate=0.05, subsample=0.8, colsample_bytree=0.9,
                  min_child_weight=200, tree_method="hist", n_jobs=12, random_state=0, eval_metric="logloss")
    # risk may only rise with higher EMI/debt/loan-to-income ratios and longer terms (financially sensible, no odd extrapolation)
    mono = dict(monotone_constraints="(" + ",".join("1" for _ in FEATURES) + ")")
    ranges = {f: [float(df[f].quantile(0.005)), float(df[f].quantile(0.995))] for f in FEATURES}

    xgb = XGBClassifier(**params, **mono).fit(tr[FEATURES], tr["y"])
    s = xgb.predict_proba(te[FEATURES])[:, 1]
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500)).fit(tr[FEATURES].fillna(0), tr["y"])
    s_lr = lr.predict_proba(te[FEATURES].fillna(0))[:, 1]
    bench_feats = FEATURES + BUREAU
    bench = XGBClassifier(**params).fit(tr[bench_feats], tr["y"])
    s_b = bench.predict_proba(te[bench_feats])[:, 1]
    res = {
        "deployed_xgboost_finlens_features": block(te["y"], s),
        "logistic_regression_finlens_features": block(te["y"], s_lr),
        "benchmark_xgboost_with_bureau_fields": block(te["y"], s_b),
        "constant_baseline": {"roc_auc": 0.5, "pr_auc": round(float(te["y"].mean()), 4),
                              "brier": round(brier_score_loss(te["y"], np.full(len(te), tr["y"].mean())), 4)},
    }
    # risk bands from observed default rates on the out-of-time test year
    q = np.quantile(s, [0.33, 0.67])
    bands = {"lower": float(q[0]), "moderate": float(q[1])}
    te_b = pd.cut(s, [-1, q[0], q[1], 2], labels=["lower", "moderate", "higher"])
    res["observed_default_rate_by_band_2015"] = {str(k): round(float(v), 4) for k, v in te.assign(b=te_b).groupby("b", observed=True)["y"].mean().items()}

    hc = load_home_credit()
    if hc is not None:
        res["cross_dataset_home_credit"] = {**block(hc["y"], xgb.predict_proba(hc[FEATURES])[:, 1]), "rows": int(len(hc)),
                                            "note": "different lender/country; obligation_ratio unavailable (missing)"}

    # final deployed model: all finished loans 2007-2015 (2016+ excluded: outcomes still immature/biased)
    full = df[df["year"] <= 2015]
    final = XGBClassifier(**params, **mono).fit(full[FEATURES], full["y"])
    imp = sorted(zip(FEATURES, final.feature_importances_.tolist()), key=lambda t: -t[1])
    meta = {
        "model_version": f"credit-xgb-lc-{time.strftime('%Y%m%d%H%M')}", "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chosen_model": "xgboost", "data": "Lending Club accepted loans 2007-2018Q4 (real loans, real outcomes; CC0)",
        "label": "Charged Off / Default (1) vs Fully Paid (0); unfinished loans excluded",
        "features": FEATURES, "train_rows": int(len(tr)), "test_rows": int(len(te)), "final_rows": int(len(full)),
        "validation": "out-of-time: train issue years 2007-2014, test 2015", "risk_band_cutoffs": bands,
        "monotonic_constraints": "risk non-decreasing in every feature", "training_ranges_p0.5_p99.5": ranges,
        "results": res, "feature_importance": [(k, round(v, 4)) for k, v in imp], "train_minutes": round((time.time()-t_start)/60, 1),
    }
    joblib.dump({"model": final, "features": FEATURES, "feature_set": "lc_ratios_v1", "version": meta["model_version"],
                 "bands": bands, "kind": "xgboost", "ranges": ranges}, ARTIFACT_DIR / "credit_model.joblib")
    (ARTIFACT_DIR / "credit_metrics.json").write_text(json.dumps(meta, indent=2))
    write_card(meta)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "calibration"} if isinstance(v, dict) else v
                      for k, v in res.items()}, indent=2))


def write_card(m):
    r = m["results"]
    def row(name, k):
        x = r[k]
        return f"| {name} | {x['roc_auc']} | {x['pr_auc']} | {x['brier']} |"
    hc = r.get("cross_dataset_home_credit")
    card = f"""# Repayment-Risk Model — Model Card

**Version:** {m['model_version']} · trained {m['trained_at']} · {m['train_minutes']} min

## Data and label
* **{m['data']}**. Train: {m['train_rows']:,} loans issued 2007–2014; out-of-time test: {m['test_rows']:,} loans issued 2015;
  final model refit on {m['final_rows']:,} finished loans (2007–2015).
* **Label:** {m['label']} — an actual **repayment outcome**, not a past approval decision.

## Features (what FinLens can actually supply)
`{', '.join(m['features'])}` — scale-free ratios computed from the user's statement and requested loan.
Interest rate, Lending Club grade **and loan-to-income** are excluded: EMI ÷ loan size at a given term reveals the
interest rate, which Lending Club sets from its own risk grade (hidden leakage — with it the model scored 0.715,
honest score without it is reported below). Risk is constrained to be **non-decreasing** in every feature, and app
inputs are clipped to the training range (0.5–99.5th percentile).

## Out-of-time results (2015)
| Model | ROC-AUC | PR-AUC | Brier |
|---|---|---|---|
{row('**Deployed** XGBoost (monotonic), FinLens features', 'deployed_xgboost_finlens_features')}
{row('Logistic regression, FinLens features', 'logistic_regression_finlens_features')}
{row('Benchmark XGBoost + credit-bureau fields (not deployable)', 'benchmark_xgboost_with_bureau_fields')}
{row('Constant baseline', 'constant_baseline')}

Observed 2015 default rate by risk band: `{json.dumps(r['observed_default_rate_by_band_2015'])}`
{f"Cross-dataset check on Home Credit ({hc['rows']:,} applicants, different lender/country): ROC-AUC {hc['roc_auc']}, PR-AUC {hc['pr_auc']} (base rate {hc['base_rate']})." if hc else ""}

Calibration (deployed, quantile bins): `{json.dumps(r['deployed_xgboost_finlens_features']['calibration'])}`

## How it is used in FinLens
A **supporting risk band** (lower / moderate / higher, cut at the 33rd/67th percentiles of 2015 scores) next to the
deterministic affordability calculation, which remains the primary output. It never approves or rejects a loan and
never uses AML data.

## Limitations
* US peer-to-peer loans (2007–2015) — borrower population, products and economy differ from Indian students/young
  professionals; treat the band as indicative only.
* Only affordability ratios are used, so discrimination is modest by design; the bureau benchmark shows the gap.
* Lending Club only lists *accepted* loans (selection bias: rejected applicants have no outcomes).
"""
    (ROOT / "docs" / "CREDIT_MODEL_CARD.md").write_text(card, encoding="utf-8")


if __name__ == "__main__":
    main()
