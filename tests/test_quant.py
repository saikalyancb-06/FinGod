"""Quantitative layer: risk engine, uncertainty, priority, money trail, transaction risk, Monte Carlo, Expected Loss."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from finlens import credit
from finlens.main import app as fin_app
from mule import risk_engine as RE
from mule.main import app as mule_app


@pytest.fixture(scope="module")
def mule():
    c = TestClient(mule_app)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": "analyst", "password": "analyst-demo-2026"}).json()["token"]
    r = c.post("/api/datasets", files={"file": ("d.csv", (ROOT / "data" / "samples" / "mule_demo_transactions.csv").read_bytes(), "text/csv")})
    return c, r.json()["dataset_id"]


def test_risk_profiles_are_decomposed_and_honest(mule):
    c, ds = mule
    rk = c.get(f"/api/datasets/{ds}/risk?flagged_only=true").json()
    a = rk["accounts"][0]
    assert abs(sum(a["contributions"].values()) - a["composite"]) < 0.2  # the score is exactly its parts
    u = a["uncertainty"]
    assert {"probability", "conformal_set", "out_of_distribution", "data_sufficiency", "evidence_strength", "confidence"} <= set(u)
    labels = {x["label"] for x in c.get(f"/api/datasets/{ds}/risk").json()["accounts"]}
    assert labels <= {"High risk / high confidence", "High risk / low confidence", "Moderate risk", "Low risk", "Insufficient evidence"}
    # an account with fewer than 3 transactions is never presented as a confident call
    for x in rk["accounts"]:
        if x["uncertainty"]["transactions"] < 3:
            assert x["label"] == "Insufficient evidence"


def test_priority_queue_and_weights(mule):
    c, ds = mule
    al = c.get(f"/api/datasets/{ds}/alerts").json()
    pr = [(x["risk"] or {}).get("priority", 0) for x in al]
    assert pr == sorted(pr, reverse=True)
    assert c.put(f"/api/datasets/{ds}/risk-weights", json={"ml": 0, "account": 0, "network": 0, "transaction": 0}).status_code == 400
    assert c.put(f"/api/datasets/{ds}/risk-weights", json={"ml": 0.1, "account": 0.4, "network": 0.4, "transaction": 0.1}).status_code == 200
    assert c.get(f"/api/datasets/{ds}/risk").json()["weights"]["account"] == 0.4


def test_trace_is_time_respecting():
    t0 = pd.Timestamp("2026-09-01 10:00")
    df = pd.DataFrame([("T1", t0, "A", "B", 10000.0), ("T2", t0 + pd.Timedelta(minutes=5), "B", "C", 9800.0),
                       ("T3", t0 + pd.Timedelta(minutes=9), "C", "D", 9600.0), ("T0", t0 - pd.Timedelta(hours=2), "B", "Z", 500.0)],
                      columns=["txn_id", "timestamp", "src", "dst", "amount"])
    out = RE.trace(df, "A", "down", 4)
    assert "Z" not in out["levels"]  # B->Z happened before the money reached B
    assert out["layering_chains"] and out["layering_chains"][0]["path"] == ["A", "B", "C", "D"]
    assert "does not prove" in out["disclaimer"]


def test_transaction_risk_flags_outliers_and_structuring():
    t0 = pd.Timestamp("2026-09-01 12:00")
    rows = [(f"N{i}", t0 + pd.Timedelta(days=i), "A", f"M{i}", 300.0 + i) for i in range(10)]
    rows += [("BIG", t0 + pd.Timedelta(days=11), "A", "X", 48000.0)]
    t = RE.transaction_scores(pd.DataFrame(rows, columns=["txn_id", "timestamp", "src", "dst", "amount"]))
    big = t[t["txn_id"] == "BIG"].iloc[0]
    assert big["risk"] > 0.8 and "unusual amount" in big["signals"] and "structuring band" in big["signals"]
    assert t[t["txn_id"] == "N3"].iloc[0]["risk"] < 0.1


def test_fp_analysis_and_model_comparison(mule):
    c, ds = mule
    fp = c.get(f"/api/datasets/{ds}/fp-analysis").json()
    assert fp["alerts"] > 0 and "suppression_policy" in fp
    cmp = c.get("/api/model/comparison")
    if cmp.status_code == 200:  # artifact produced by ml/compare_mule_models.py
        r = cmp.json()["results"]
        assert {"logistic_regression", "random_forest", "xgboost_deployed", "isolation_forest"} <= set(r["inr_synthetic_test"]["models"])


def test_monte_carlo_and_expected_loss():
    c = TestClient(fin_app)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/register", json={"email": "quant@example.com", "name": "Q", "password": "test-pass-123"}).json()["token"]
    for p in ("analytics", "credit"):
        c.post("/api/consents", json={"purpose": p, "granted": True})
    c.post("/api/statements", files={"file": ("s.csv", (ROOT / "data" / "samples" / "finlens_sample_statement.csv").read_bytes(), "text/csv")})
    r = c.get("/api/risklab").json()
    assert r["available"] and 0 <= r["p_run_out"] <= 1 and r["cvar95"] <= r["var95"] <= r["end_balance"]["p50"]
    f = r["fan"][-1]
    assert f["p5"] <= f["p25"] <= f["p50"] <= f["p75"] <= f["p95"]
    assert r == c.get("/api/risklab").json()  # seeded: reproducible
    lower = c.get("/api/risklab?cut_wants_pct=100").json()
    assert lower["p_run_out"] <= r["p_run_out"]  # spending less can only help
    el = credit.expected_loss(0.2, 30000, 36)
    if el:
        assert abs(el["expected_loss"] - 0.2 * el["lgd"] * el["ead"]) < 0.01
