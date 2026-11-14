"""Honest model comparison, probability calibration, conformal prediction and out-of-distribution
detection for the MuleScope account classifier.

Data (no overlap between roles):
  train        IBM AMLworld HI-Medium (cached features) + synthetic INR seeds 101-110   (same as the deployed model)
  validation   20% of training ACCOUNTS (entity split)  -> F1-optimal thresholds for LR / RF / IsolationForest
  calibration  synthetic INR seeds 201-203 (new)         -> isotonic calibration + split-conformal quantile
  test         IBM HI-Small and LI-Small (separate generated worlds) + synthetic INR seeds 301-302 (new)

Models: deployed XGBoost (loaded, not retrained), Logistic Regression, Random Forest (trained here on the same
features) and Isolation Forest (unsupervised anomaly baseline). PR-AUC is primary (labels ~1-2% positive).

Outputs: ml/artifacts/mule_model_comparison.json and ml/artifacts/mule_uncertainty.joblib
Usage:   python -m ml.compare_mule_models [--neg-sample 300000]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_curve, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.train_mule_ibm import metrics_at, synthetic_features
from mule.model_features import MODEL_FEATURES

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "external" / "features"
ART = ROOT / "ml" / "artifacts"
ALPHA = 0.10  # conformal miscoverage: prediction sets cover the true label ~90% of the time


def f1_threshold(y, s):
    p, r, t = precision_recall_curve(y, s)
    f1 = 2 * p * r / np.clip(p + r, 1e-9, None)
    i = int(np.nanargmax(f1[:-1])) if len(t) else 0
    return float(t[i]) if len(t) else 0.5


def pr_points(y, s, n=40):
    p, r, _ = precision_recall_curve(y, s)
    idx = np.unique(np.linspace(0, len(p) - 1, n).astype(int))
    return [{"recall": round(float(r[i]), 4), "precision": round(float(p[i]), 4)} for i in idx]


def reliability(y, s, bins=10):
    try:
        frac, mean = calibration_curve(y, s, n_bins=bins, strategy="quantile")
    except ValueError:
        return []
    return [{"predicted": round(float(a), 4), "observed": round(float(b), 4)} for a, b in zip(mean, frac)]


def ece(y, s, bins=10):
    q = np.quantile(s, np.linspace(0, 1, bins + 1))
    idx = np.clip(np.searchsorted(q, s, side="right") - 1, 0, bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            tot += m.mean() * abs(s[m].mean() - y[m].mean())
    return round(float(tot), 4)


def block(y, s, thr, prob=True):
    out = {"rows": int(len(y)), "positives": int(y.sum()), "base_rate": round(float(y.mean()), 5),
           "pr_auc": round(float(average_precision_score(y, s)), 4), "roc_auc": round(float(roc_auc_score(y, s)), 4),
           "threshold": round(float(thr), 4), "at_threshold": metrics_at(y, s, thr), "pr_curve": pr_points(y, s)}
    at = out["at_threshold"]
    at["false_negative_rate"] = round(at["fn"] / (at["fn"] + at["tp"]), 4) if at["fn"] + at["tp"] else 0.0
    if prob:
        out["brier"] = round(float(brier_score_loss(y, s)), 5)
    return out


def grid(y, s):
    """Confusion counts over thresholds: lets the UI compute expected cost for any cost ratio."""
    out = []
    for t in np.round(np.linspace(0.02, 0.98, 49), 2):
        p = s >= t
        out.append({"t": float(t), "tp": int((p & (y == 1)).sum()), "fp": int((p & (y == 0)).sum()),
                    "fn": int((~p & (y == 1)).sum()), "tn": int((~p & (y == 0)).sum())})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--neg-sample", type=int, default=300_000, help="negatives sampled from HI-Medium for LR/RF training")
    a = ap.parse_args()
    t0 = time.time()
    rng = np.random.default_rng(7)
    dep = joblib.load(ART / "mule_model.joblib")
    xgb, thr_x = dep["model"], float(dep["threshold"])

    print("[train] HI-Medium features + synthetic INR 101-110")
    hm = pd.read_parquet(CACHE / "HI-Medium_features.parquet")
    neg = hm[hm["y"] == 0]
    hm = pd.concat([hm[hm["y"] == 1], neg.iloc[rng.choice(len(neg), min(a.neg_sample, len(neg)), replace=False)]])
    syn_tr = synthetic_features(range(101, 111))
    tr = pd.concat([hm[MODEL_FEATURES + ["y"]], syn_tr[MODEL_FEATURES + ["y"]]])
    Xa, Xv, ya, yv = train_test_split(tr[MODEL_FEATURES].to_numpy(np.float32), tr["y"].to_numpy(), test_size=0.2,
                                      stratify=tr["y"], random_state=42)  # rows are accounts -> entity split
    print(f"  {len(Xa):,} train accounts, {len(Xv):,} validation accounts")

    models = {
        "logistic_regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, class_weight="balanced")),
        "random_forest": RandomForestClassifier(n_estimators=250, max_depth=14, min_samples_leaf=5, class_weight="balanced_subsample",
                                                n_jobs=-1, random_state=0),
    }
    scorers, thresholds = {}, {"xgboost_deployed": thr_x}
    for name, m in models.items():
        t1 = time.time()
        m.fit(Xa, ya)
        scorers[name] = (lambda mm: (lambda X: mm.predict_proba(X)[:, 1]))(m)
        thresholds[name] = f1_threshold(yv, scorers[name](Xv))
        print(f"  {name} fitted in {time.time() - t1:.0f}s")
    iso = IsolationForest(n_estimators=300, contamination="auto", random_state=0, n_jobs=-1).fit(Xa[ya == 0][:200_000])
    scorers["isolation_forest"] = lambda X: -iso.score_samples(X)  # higher = more anomalous (not a probability)
    thresholds["isolation_forest"] = f1_threshold(yv, scorers["isolation_forest"](Xv))
    scorers["xgboost_deployed"] = lambda X: xgb.predict_proba(X)[:, 1]

    print("[calibration] synthetic INR seeds 201-203")
    cal = synthetic_features(range(201, 204))
    Xc, yc = cal[MODEL_FEATURES].to_numpy(np.float32), cal["y"].to_numpy()
    sc = scorers["xgboost_deployed"](Xc)
    iso_cal = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999).fit(sc, yc)
    pc = iso_cal.predict(sc)
    nonconf = np.where(yc == 1, 1 - pc, pc)  # split-conformal: 1 - p(true class)
    n = len(nonconf)
    qhat = float(np.quantile(nonconf, min(1.0, np.ceil((n + 1) * (1 - ALPHA)) / n), method="higher"))
    # out-of-distribution: how unusual an account's feature vector is versus the training population
    ood = IsolationForest(n_estimators=200, random_state=1, n_jobs=-1).fit(Xa[rng.choice(len(Xa), min(150_000, len(Xa)), replace=False)])
    ood_cut = float(np.quantile(ood.score_samples(Xv), 0.01))  # bottom 1% of known accounts

    print("[test] HI-Small, LI-Small, synthetic INR 301-302")
    tests = {}
    for nm in ("HI-Small", "LI-Small"):
        f = pd.read_parquet(CACHE / f"{nm}_features.parquet")
        tests[f"ibm_{nm}"] = (f"IBM AMLworld {nm} (separate world, never trained on)", f[MODEL_FEATURES].to_numpy(np.float32), f["y"].to_numpy())
    te_syn = synthetic_features(range(301, 303))
    tests["inr_synthetic_test"] = ("Synthetic INR scenarios, seeds 301-302 (new accounts)", te_syn[MODEL_FEATURES].to_numpy(np.float32), te_syn["y"].to_numpy())

    results = {}
    for key, (label, X, y) in tests.items():
        results[key] = {"label": label, "models": {}}
        for name, f in scorers.items():
            s = f(X)
            results[key]["models"][name] = block(y, s, thresholds[name], prob=name != "isolation_forest")
        s = scorers["xgboost_deployed"](X)
        p = iso_cal.predict(s)
        results[key]["calibration_xgboost"] = {
            "raw": {"brier": round(float(brier_score_loss(y, s)), 5), "ece": ece(y, s), "curve": reliability(y, s)},
            "isotonic": {"brier": round(float(brier_score_loss(y, p)), 5), "ece": ece(y, p), "curve": reliability(y, p)},
        }
        sets_mule, sets_norm = p >= 1 - qhat, (1 - p) >= 1 - qhat
        covered = np.where(y == 1, sets_mule, sets_norm)
        results[key]["conformal"] = {"target_coverage": 1 - ALPHA, "empirical_coverage": round(float(covered.mean()), 4),
                                     "share_ambiguous": round(float((sets_mule & sets_norm).mean()), 4),
                                     "share_empty": round(float((~sets_mule & ~sets_norm).mean()), 4)}
        results[key]["ood_share"] = round(float((ood.score_samples(X) < ood_cut).mean()), 4)
        results[key]["threshold_grid_xgboost"] = grid(y, p)
        print(f"  {key}: " + ", ".join(f"{m} PR-AUC {r['pr_auc']}" for m, r in results[key]["models"].items()))

    meta = {
        "created": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - t0),
        "design": {"train": "IBM HI-Medium (all positives + sampled negatives) + synthetic INR seeds 101-110",
                   "validation": "20% of training accounts (entity split) for thresholds",
                   "calibration": "synthetic INR seeds 201-203 (isotonic regression + split-conformal, alpha=0.10)",
                   "test": "IBM HI-Small, IBM LI-Small (separate worlds) and synthetic INR seeds 301-302"},
        "thresholds": {k: round(v, 4) for k, v in thresholds.items()},
        "notes": ["All numbers are on benchmark/synthetic data; none are real-bank results.",
                  "Isolation Forest is unsupervised: its score is an anomaly score, not a probability (no Brier).",
                  "Calibration is fitted on the app's INR domain; IBM tests show how it transfers."],
        "results": results,
    }
    (ART / "mule_model_comparison.json").write_text(json.dumps(meta, indent=1))
    joblib.dump({"isotonic": iso_cal, "qhat": qhat, "alpha": ALPHA, "ood": ood, "ood_cut": ood_cut,
                 "version": f"mule-uncertainty-{time.strftime('%Y%m%d%H%M')}"}, ART / "mule_uncertainty.joblib")
    print(f"done in {time.time() - t0:.0f}s; qhat={qhat:.3f}")


if __name__ == "__main__":
    main()
