"""Train the money-mule account classifier on IBM AMLworld (Altman et al., NeurIPS 2023).

Data (downloaded by `python -m ml.download_datasets --only ibm`):
  train : HI-Medium   (~31M transactions, ~2M accounts)            -> fit + validation split
  test 1: HI-Small    (~5M transactions, separate generated world)  -> held-out evaluation
  test 2: LI-Small    (~7M transactions, LOW illicit ratio)         -> harder held-out evaluation

Label: an account is positive if it sends or receives at least one transaction
flagged `Is Laundering` (account-level, as used by mule-account detection).
Features: mule/model_features.py (vectorised, scale-free; identical in the app).

AMLworld is itself synthetic (a multi-agent simulation calibrated by IBM); no
public real bank AML data exists, so held-out-world results are reported honestly.

  python -m ml.train_mule_ibm [--train HI-Medium] [--trees 600]
"""
from __future__ import annotations

import argparse
import gc
import json
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from mule.model_features import MODEL_FEATURES, compute
from shared.config import ARTIFACT_DIR, ROOT

IBM = ROOT / "data" / "external" / "ibm_aml"
CACHE = ROOT / "data" / "external" / "features"
# Approximate 2022 USD rates; only used to put mixed currencies on one scale
# (features are relative to the dataset median, so small rate errors don't matter).
USD = {"US Dollar": 1.0, "Euro": 1.02, "UK Pound": 1.18, "Swiss Franc": 1.03, "Yuan": 0.145, "Yen": 0.0073,
       "Rupee": 0.0122, "Ruble": 0.016, "Shekel": 0.29, "Australian Dollar": 0.68, "Canadian Dollar": 0.75,
       "Mexican Peso": 0.05, "Brazil Real": 0.19, "Saudi Riyal": 0.266, "Bitcoin": 20000.0}


def _acct_ids(bank: pd.Series, acct: pd.Series) -> np.ndarray:
    codes, uniq = pd.factorize(acct, sort=False)
    as_int = np.array([int(x, 16) if all(c in "0123456789abcdefABCDEF" for c in x) else (hash(x) & ((1 << 36) - 1)) for x in uniq], dtype=np.int64)
    return (bank.to_numpy(dtype=np.int64) << 36) | as_int[codes]


def load_ibm(name: str) -> tuple[pd.DataFrame, pd.Series]:
    hits = sorted(IBM.rglob(f"{name}_Trans.csv"))
    if not hits:
        raise FileNotFoundError(f"{name}_Trans.csv not found under {IBM} (run python -m ml.download_datasets --only ibm)")
    f = hits[0]
    t0 = time.time()
    names = ["ts", "fb", "fa", "tb", "ta", "recv", "rcur", "paid", "cur", "fmt", "lab"]  # header repeats "Account"
    import pyarrow as pa
    from pyarrow import csv as pacsv
    keep = ["ts", "fb", "fa", "tb", "ta", "paid", "cur", "lab"]
    tbl = pacsv.read_csv(
        f, read_options=pacsv.ReadOptions(column_names=names, skip_rows=1, block_size=1 << 26),
        convert_options=pacsv.ConvertOptions(include_columns=keep, column_types={
            "ts": pa.string(), "fb": pa.int64(), "tb": pa.int64(), "fa": pa.string(), "ta": pa.string(),
            "paid": pa.float64(), "cur": pa.string(), "lab": pa.int8()}))
    raw = tbl.to_pandas()
    del tbl
    raw = raw[keep]
    print(f"  read {len(raw):,} rows from {f.name} in {time.time()-t0:.0f}s")
    rate = raw["cur"].map(USD).fillna(1.0).to_numpy()
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(raw["ts"], format="%Y/%m/%d %H:%M"),
        "src": _acct_ids(raw["fb"], raw["fa"]),
        "dst": _acct_ids(raw["tb"], raw["ta"]),
        "amount": raw["paid"].to_numpy(dtype=np.float64) * rate,
    })
    lab = raw["lab"].to_numpy().astype(bool)
    del raw
    gc.collect()
    pos = pd.unique(np.concatenate([df["src"].to_numpy()[lab], df["dst"].to_numpy()[lab]]))
    print(f"  laundering transactions: {lab.sum():,} ({lab.mean()*100:.3f}%), positive accounts: {len(pos):,}")
    return df, pd.Series(1, index=pos, dtype=np.int8)


def features_for(name: str) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / f"{name}_features.parquet"
    if cache.exists():
        print(f"[{name}] features from cache")
        return pd.read_parquet(cache)
    print(f"[{name}] loading + computing features")
    df, pos = load_ibm(name)
    t0 = time.time()
    F = compute(df)
    del df
    gc.collect()
    F["y"] = F.index.isin(pos.index).astype(np.int8)
    F.index = F.index.astype(np.int64)
    F.to_parquet(cache)
    print(f"  {len(F):,} accounts, {int(F['y'].sum()):,} positive ({F['y'].mean()*100:.2f}%), features in {time.time()-t0:.0f}s")
    return F


def synthetic_features(seeds, accounts: int = 1200, rings: int = 8) -> pd.DataFrame:
    """INR-style scenarios from ml/generate_mule_data.py (the app's own domain): Indian payment
    channels, salary/UPI/merchant behaviour, hard negatives and injected mule typologies."""
    from ml.generate_mule_data import generate
    frames = []
    for sd in seeds:
        tx, lab = generate(sd, accounts, rings)
        df = pd.DataFrame({"timestamp": pd.to_datetime(tx["timestamp"]), "src": tx["from_account"], "dst": tx["to_account"],
                           "amount": tx["amount"].astype(float)})
        F = compute(df)
        F["y"] = F.index.map(lab.set_index("account")["is_mule"]).fillna(0).astype(np.int8)
        F.index = [f"syn{sd}_{a}" for a in F.index]
        frames.append(F)
    return pd.concat(frames)


def metrics_at(y, s, thr):
    p = s >= thr
    tp = int((p & (y == 1)).sum()); fp = int((p & (y == 0)).sum()); fn = int((~p & (y == 1)).sum()); tn = int((~p & (y == 0)).sum())
    return {"precision": round(tp / (tp + fp), 4) if tp + fp else 0.0, "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
            "f1": round(2 * tp / (2 * tp + fp + fn), 4) if tp else 0.0,
            "false_positive_rate": round(fp / (fp + tn), 5) if fp + tn else 0.0, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def evaluate(name, y, s, thr, s_lr=None):
    out = {"accounts": int(len(y)), "positives": int(y.sum()), "positive_rate": round(float(y.mean()), 5),
           "xgboost": {"roc_auc": round(roc_auc_score(y, s), 4), "pr_auc": round(average_precision_score(y, s), 4),
                       "at_threshold": metrics_at(y, s, thr)},
           "random_baseline_pr_auc": round(float(y.mean()), 5)}
    # precision among the top-k ranked accounts (how investigators actually work a queue)
    order = np.argsort(-s)
    for k in (100, 1000):
        if len(y) >= k:
            out["xgboost"][f"precision_at_{k}"] = round(float(y[order[:k]].mean()), 4)
    if s_lr is not None:
        out["logistic_regression"] = {"roc_auc": round(roc_auc_score(y, s_lr), 4), "pr_auc": round(average_precision_score(y, s_lr), 4)}
    print(f"  {name}: PR-AUC {out['xgboost']['pr_auc']} (random {out['random_baseline_pr_auc']}), ROC-AUC {out['xgboost']['roc_auc']}, "
          f"P/R@thr {out['xgboost']['at_threshold']['precision']}/{out['xgboost']['at_threshold']['recall']}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="HI-Medium")
    ap.add_argument("--tests", default="HI-Small,LI-Small")
    ap.add_argument("--trees", type=int, default=800)
    ap.add_argument("--synthetic-seeds", type=int, default=10, help="INR-domain scenarios mixed into training (0 = IBM only)")
    ap.add_argument("--synthetic-weight", type=float, default=15.0)
    a = ap.parse_args()
    t_start = time.time()

    Ftr = features_for(a.train)
    Ftr["domain"] = "ibm"
    if a.synthetic_seeds:
        print(f"[synthetic INR] generating {a.synthetic_seeds} scenarios")
        Fs = synthetic_features(range(101, 101 + a.synthetic_seeds))
        Fs["domain"] = "inr"
        print(f"  {len(Fs):,} accounts, {int(Fs['y'].sum()):,} positive")
        Ftr = pd.concat([Ftr, Fs])
    X, y = Ftr[MODEL_FEATURES].to_numpy(np.float32), Ftr["y"].to_numpy()
    # up-weight the small INR domain so it matters next to ~1.7M IBM accounts
    w_all = np.where(Ftr["domain"].to_numpy() == "inr", a.synthetic_weight, 1.0).astype(np.float32)
    strat = (Ftr["domain"] + "_" + Ftr["y"].astype(str)).to_numpy()
    Xa, Xv, ya, yv, wa, wv, da, dv = train_test_split(X, y, w_all, Ftr["domain"].to_numpy(), test_size=0.2, stratify=strat, random_state=42)
    pos_w = float((ya == 0).sum() / max(1, (ya == 1).sum()))
    params = dict(n_estimators=a.trees, max_depth=7, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                  min_child_weight=5, tree_method="hist", eval_metric="aucpr", n_jobs=12, random_state=42,
                  scale_pos_weight=min(pos_w, 50.0))
    print(f"[train] {len(ya):,} accounts ({int(ya.sum()):,} positive) · validate on {len(yv):,}")
    t0 = time.time()
    xgb = XGBClassifier(**params, early_stopping_rounds=60)
    xgb.fit(Xa, ya, sample_weight=wa, eval_set=[(Xv, yv)], sample_weight_eval_set=[wv], verbose=False)
    best = int(xgb.best_iteration) + 1
    sv = xgb.predict_proba(Xv)[:, 1]
    p, r, th = precision_recall_curve(yv, sv, sample_weight=wv)
    f1 = 2 * p * r / np.clip(p + r, 1e-9, None)
    thr = float(th[int(np.argmax(f1[:-1]))])
    print(f"  fitted in {time.time()-t0:.0f}s, best iteration {best}, F1-optimal threshold {thr:.3f}")
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=300, class_weight="balanced")).fit(Xa, ya, logisticregression__sample_weight=wa)
    s_lr_v = lr.predict_proba(Xv)[:, 1]

    results = {}
    for dom, label in (("ibm", f"{a.train} validation (20%)"), ("inr", "synthetic INR validation (20%)")):
        mk = dv == dom
        if mk.any() and yv[mk].sum():
            results[f"validation_{dom}"] = evaluate(label, yv[mk], sv[mk], thr, s_lr_v[mk])
    # final model on all training accounts with the tuned number of trees
    final = XGBClassifier(**{**params, "n_estimators": best})
    final.fit(X, y, sample_weight=w_all, verbose=False)
    del Ftr, X, Xa, Xv
    gc.collect()

    for name in [t for t in a.tests.split(",") if t]:
        try:
            Ft = features_for(name)
        except FileNotFoundError as exc:
            print(f"  skip {name}: {exc}")
            continue
        Xt, yt = Ft[MODEL_FEATURES].to_numpy(np.float32), Ft["y"].to_numpy()
        results[f"heldout_{name}"] = evaluate(f"held-out {name}", yt, final.predict_proba(Xt)[:, 1], thr, lr.predict_proba(Xt)[:, 1])
        del Ft, Xt
        gc.collect()

    # unseen INR-domain scenario (seed 2; training used seeds 101+)
    Fh = synthetic_features([2], 900, 6)
    results["heldout_synthetic_INR_seed2"] = evaluate("held-out synthetic INR (seed 2)", Fh["y"].to_numpy(),
                                                      final.predict_proba(Fh[MODEL_FEATURES].to_numpy(np.float32))[:, 1], thr,
                                                      lr.predict_proba(Fh[MODEL_FEATURES].to_numpy(np.float32))[:, 1])
    # the app's own demo upload file (seed 3; never used for training or tuning)
    try:
        from mule.features import normalize_transactions
        demo, _ = normalize_transactions((ROOT / "data" / "samples" / "mule_demo_transactions.csv").read_bytes())
        Fd = compute(demo)
        labd = pd.read_csv(ROOT / "data" / "samples" / "mule_demo_labels_for_evaluation.csv").set_index("account")["is_mule"]
        yd = Fd.index.map(labd).fillna(0).astype(int).to_numpy()
        results["app_demo_synthetic_INR"] = evaluate("app demo (synthetic INR)", yd, final.predict_proba(Fd[MODEL_FEATURES].to_numpy(np.float32))[:, 1], thr)
    except Exception as exc:
        print("  demo check skipped:", exc)

    imp = sorted(zip(MODEL_FEATURES, final.feature_importances_.tolist()), key=lambda t: -t[1])
    meta = {
        "model_version": f"mule-xgb-ibm-{time.strftime('%Y%m%d%H%M')}", "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "training_data": f"IBM AMLworld {a.train} (Altman et al., NeurIPS 2023; CDLA-Sharing-1.0)"
                         + (f" + {a.synthetic_seeds} synthetic INR scenarios (weight {a.synthetic_weight})" if a.synthetic_seeds else ""),
        "label": "account participates in >=1 transaction flagged Is Laundering",
        "features": MODEL_FEATURES, "threshold": round(thr, 4), "trees": best, "params": {k: v for k, v in params.items() if k != "n_jobs"},
        "results": results, "feature_importance": [(k, round(v, 4)) for k, v in imp],
        "train_minutes": round((time.time() - t_start) / 60, 1),
    }
    joblib.dump({"model": final, "features": MODEL_FEATURES, "feature_set": "model_features_v1", "threshold": thr,
                 "version": meta["model_version"]}, ARTIFACT_DIR / "mule_model.joblib")
    (ARTIFACT_DIR / "mule_metrics.json").write_text(json.dumps(meta, indent=2))
    write_card(meta)
    print(json.dumps({k: v for k, v in meta.items() if k in ("model_version", "threshold", "trees", "train_minutes")}, indent=2))


def write_card(m):
    def row(name, r):
        x = r["xgboost"]; at = x["at_threshold"]
        lr = r.get("logistic_regression", {})
        return (f"| {name} | {r['accounts']:,} | {r['positives']:,} ({r['positive_rate']*100:.2f}%) | **{x['pr_auc']}** | {r['random_baseline_pr_auc']} | "
                f"{lr.get('pr_auc', '—')} | {x['roc_auc']} | {at['precision']} | {at['recall']} | {at['false_positive_rate']} | {x.get('precision_at_100', '—')} |")
    rows = "\n".join(row(k, v) for k, v in m["results"].items())
    card = f"""# Money Mule Classifier — Model Card

**Version:** {m['model_version']} · trained {m['trained_at']} · {m['train_minutes']} min

## Training data
* **{m['training_data']}** — a large multi-agent simulation of a financial ecosystem with injected
  laundering patterns (fan-in/out, gather-scatter, scatter-gather, cycles, bipartite, stacks, random).
  There is no public *real* bank AML dataset; AMLworld is the standard public benchmark.
* **Label:** {m['label']}.
* **Features:** `mule/model_features.py` — {len(m['features'])} vectorised, scale-free account features
  (counts/degrees, amounts relative to the dataset median, forwarding speed, daily fan-in/out,
  velocity, night share, reciprocity, PageRank, strongly-connected-component size). The **same code**
  computes features for uploads in the app, so there is no train/serve skew.
* XGBoost ({m['trees']} trees, depth 7, class-weighted), early-stopped on a 20 % stratified validation split;
  decision threshold = F1-optimal on that split ({m['threshold']}).

## Results
Held-out rows are **separate generated worlds** (different accounts, banks and patterns), not a random split.

| Evaluation set | Accounts | Positive accounts | PR-AUC | Random PR-AUC | LogReg PR-AUC | ROC-AUC | Precision @thr | Recall @thr | FPR @thr | Precision@100 |
|---|---|---|---|---|---|---|---|---|---|---|
{rows}

PR-AUC is the primary metric (labels are heavily imbalanced; accuracy is not reported).

## Top features
`{json.dumps(m['feature_importance'][:10])}`

## Limitations
* AMLworld is synthetic; results show the method works on a realistic benchmark, **not** real-bank performance.
* The app's INR demo data is a different domain (row above); thresholds must be re-validated on
  authorised institutional data before any use.
* The ML score is one of three evidence layers; alerts are prioritised by explicit rules and always need
  human review. The system never freezes accounts or labels people as criminals.
"""
    (ROOT / "docs" / "MULE_MODEL_CARD.md").write_text(card, encoding="utf-8")


if __name__ == "__main__":
    main()
