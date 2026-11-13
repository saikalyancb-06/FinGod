"""Three-layer detection: rules + ML + graph analytics -> evidence-linked alerts.

Scores with different meanings are NOT averaged. Priority comes from explicit,
documented rules over (a) the ML score vs its validated threshold and (b) the
number of independent rule families that fired:

  high    model >= threshold AND >= 2 rule families
  medium  model >= threshold OR  >= 2 rule families
  low     exactly 1 rule family AND model >= 0.5 x threshold
  (none)  otherwise - no alert

High-volume merchant profiles (MuleGuard's false-positive filter) only alert at
'high' priority. The MuleGuard-style pattern score (0-100) and fraud-ring risk
scores are shown alongside as separate, explainable indicators.

Model applicability: the ML features are relative to the dataset (amounts vs the dataset median,
PageRank x number of accounts, forwarding speed). On a tiny dataset - a handful of manual or mr-mule
payments - every account looks extreme and the model outputs ~1.0 for all of them. So the ML score
only counts towards priority when the dataset has >= MIN_TXNS transactions and >= MIN_ACCOUNTS
accounts. (No per-account minimum: pass-through mules often have just 2 transactions, and the model
was trained on such accounts.) Otherwise the rules decide
alone (>= 2 families -> medium, 1 family -> low) and the score is shown as "not reliable here".

An alert is a reason to review evidence, never proof of wrongdoing.
"""
from __future__ import annotations

from functools import lru_cache

import joblib
import networkx as nx
import numpy as np
import pandas as pd

from shared.config import ARTIFACT_DIR

from . import model_features, patterns
from .features import FEATURE_COLUMNS, compute_features, explain, rule_families


@lru_cache(maxsize=1)
def load_model():
    p = ARTIFACT_DIR / "mule_model.joblib"
    if not p.exists():
        return None
    return joblib.load(p)


MIN_TXNS, MIN_ACCOUNTS = 200, 30


def rules_only_priority(n_rules: int) -> str | None:
    return "medium" if n_rules >= 2 else "low" if n_rules == 1 else None


def priority(score: float, n_rules: int, thr: float) -> str | None:
    if score >= thr and n_rules >= 2:
        return "high"
    if score >= thr or n_rules >= 2:
        return "medium"
    if n_rules == 1 and score >= 0.5 * thr:
        return "low"
    return None


def analyze(df: pd.DataFrame) -> dict:
    """df uses alias ids already. Returns features, evidence, scores, alerts and rings."""
    res = compute_features(df)
    m = load_model()
    if m and m.get("feature_set") == "model_features_v1":
        # model trained on IBM AMLworld with the vectorised, scale-free feature set
        mf = model_features.compute(df).reindex(res.features.index).fillna(0)
        scores = m["model"].predict_proba(mf[m["features"]].to_numpy(np.float32))[:, 1]
        thr, version = float(m["threshold"]), m["version"]
    elif m:
        scores = m["model"].predict_proba(res.features[FEATURE_COLUMNS].values)[:, 1]
        thr, version = float(m["threshold"]), m["version"]
    else:
        scores, thr, version = np.zeros(len(res.features)), 1.0, "no-model (rules only)"
    score_map = dict(zip(res.features.index, scores.astype(float)))
    n_txns, n_accts = len(df), len(res.features)
    dataset_ok = n_txns >= MIN_TXNS and n_accts >= MIN_ACCOUNTS
    model_note = (None if dataset_ok else
                  f"The ML model isn't used to raise alerts on this dataset: it has {n_txns} transactions and {n_accts} accounts, "
                  f"but the model's features are relative to the whole dataset and need at least {MIN_TXNS} transactions and "
                  f"{MIN_ACCOUNTS} accounts to mean anything. Alerts here come from the rules only.")

    alerts = []
    suppressed = 0
    for acct in res.features.index:
        ev = res.evidence[acct]
        fams = rule_families(ev)
        reliable = dataset_ok
        pr = priority(score_map[acct], len(fams), thr) if reliable else rules_only_priority(len(fams))
        if not pr:
            continue
        if ev.get("high_volume_merchant") and pr != "high":
            suppressed += 1
            continue
        expl = explain(acct, ev)
        if not reliable:
            expl.append("The ML score was not used for this alert: the dataset is too small for the model's relative features.")
        elif score_map[acct] >= thr:
            expl.append(f"The ML classifier ({version}) scored this account {score_map[acct]:.2f}, above its validated threshold of {thr:.2f}.")
        why = {("high"): "model and ≥2 patterns agree",
               ("medium"): ("model only: no rule pattern confirms it" if reliable and score_map[acct] >= thr and not fams
                            else "model + 1 pattern (high needs 2 patterns)" if reliable and score_map[acct] >= thr and len(fams) == 1
                            else f"{len(fams)} patterns, model below its threshold" if reliable else f"{len(fams)} patterns (rules only)"),
               ("low"): "1 pattern, model moderately high" if reliable else "1 pattern (rules only)"}[pr]
        alerts.append({"account": acct, "priority": pr, "priority_reason": why, "model_score": round(score_map[acct], 4),
                       "model_reliable": reliable, "rule_families": fams, "explanation": expl, "pattern_score": ev.get("pattern_score", 0)})

    # graph layer: link alerts that sit within 2 hops of each other (possible ring)
    U = nx.Graph()
    for u, v in res.graph.edges():
        U.add_edge(u, v)
    flagged = {a["account"] for a in alerts}
    for a in alerts:
        near = nx.single_source_shortest_path_length(U, a["account"], cutoff=2)
        a["linked_alerts"] = sorted(x for x in near if x in flagged and x != a["account"])
        if len(a["linked_alerts"]) >= 2:
            a["explanation"].append(f"It is within two transfers of {len(a['linked_alerts'])} other flagged accounts, which suggests a connected network.")

    rings = patterns.build_rings(res.evidence, flagged)
    by_acct: dict[str, list[str]] = {}
    for r in rings:
        for mbr in r["member_accounts"]:
            by_acct.setdefault(mbr, []).append(r["ring_id"])
    for a in alerts:
        a["ring_ids"] = by_acct.get(a["account"], [])
    order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: (order[a["priority"]], -a["model_score"]))
    return {"features": res.features, "evidence": res.evidence, "scores": score_map, "threshold": thr,
            "model_version": version, "alerts": alerts, "rings": rings, "graph": res.graph,
            "suppressed_merchant_alerts": suppressed, "model_applicable": dataset_ok, "model_note": model_note}
