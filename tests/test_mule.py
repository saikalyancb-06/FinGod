import pandas as pd
import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from mule.detection import priority
from mule.features import compute_features, normalize_transactions
from mule.main import app

DEMO = (ROOT / "data" / "samples" / "mule_demo_transactions.csv").read_bytes()


def _df(rows):
    return pd.DataFrame(rows, columns=["txn_id", "timestamp", "from", "to", "amount"])


def test_fan_in_and_pass_through_rules():
    t0 = pd.Timestamp("2026-09-01 10:00")
    rows = [(f"i{i}", t0 + pd.Timedelta(minutes=i), f"V{i}", "M", 10000) for i in range(10)]
    rows += [(f"o{i}", t0 + pd.Timedelta(minutes=20 + i), "M", f"X{i}", 30000) for i in range(3)]
    df, rep = normalize_transactions(_df(rows))
    ev = compute_features(df).evidence["M"]["rules"]
    assert ev["fan_in"]["distinct_senders"] == 10
    assert ev["rapid_pass_through"]["forwarded_within_60m_share"] >= 0.8


def test_temporal_cycle_rule():
    t0 = pd.Timestamp("2026-09-01 10:00")
    rows = [("a", t0, "A", "B", 50000), ("b", t0 + pd.Timedelta(hours=2), "B", "C", 49000), ("c", t0 + pd.Timedelta(hours=5), "C", "A", 48000)]
    df, _ = normalize_transactions(_df(rows))
    assert "circular_flow" in compute_features(df).evidence["A"]["rules"]
    # same edges but out of time order (> 72h) -> not a temporal cycle
    rows[2] = ("c", t0 - pd.Timedelta(days=5), "C", "A", 48000)
    df, _ = normalize_transactions(_df(rows))
    assert "circular_flow" not in compute_features(df).evidence["A"]["rules"]


def test_invalid_rows_dropped():
    rows = [("1", "2026-09-01", "A", "B", "100"), ("2", "not a date", "A", "B", "100"), ("3", "2026-09-01", "A", "A", "50"), ("4", "2026-09-01", "A", "B", "-5")]
    df, rep = normalize_transactions(_df(rows))
    assert rep["rows_out"] == 1 and rep["rows_dropped_invalid"] == 3


def test_priority_rules_do_not_average():
    assert priority(0.9, 2, 0.6) == "high"
    assert priority(0.9, 0, 0.6) == "medium"
    assert priority(0.1, 2, 0.6) == "medium"
    assert priority(0.35, 1, 0.6) == "low"
    assert priority(0.1, 1, 0.6) is None


@pytest.fixture(scope="module")
def analyst():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "analyst", "password": "analyst-demo-2026"})
    assert r.status_code == 200
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    return c


@pytest.fixture(scope="module")
def lead():
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": "lead", "password": "lead-demo-2026"}).json()["token"]
    return c


@pytest.fixture(scope="module")
def dataset(analyst):
    r = analyst.post("/api/datasets", files={"file": ("demo.csv", DEMO, "text/csv")}, data={"name": "Synthetic demo"})
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_and_alerts(analyst, dataset):
    s = dataset["summary"]
    assert s["alerts"] > 0 and s["accounts"] > 300
    al = analyst.get(f"/api/datasets/{dataset['dataset_id']}/alerts").json()
    assert al[0]["priority"] == "high" and al[0]["explanation"]
    assert all(a["account"].startswith("ACC-") for a in al)  # restricted identifiers never exposed by default


def test_evaluation_against_labels(analyst, dataset):
    labels = (ROOT / "data" / "samples" / "mule_demo_labels_for_evaluation.csv").read_bytes()
    ev = analyst.post(f"/api/datasets/{dataset['dataset_id']}/evaluate", files={"file": ("l.csv", labels, "text/csv")}).json()
    assert ev["medium_or_high"]["recall"] >= 0.7
    assert ev["medium_or_high"]["precision"] >= 0.5
    assert "false_positive_rate" in ev["any_alert"]


def test_graph_account_and_report(analyst, dataset):
    ds = dataset["dataset_id"]
    a = analyst.get(f"/api/datasets/{ds}/alerts").json()[0]
    g = analyst.get(f"/api/datasets/{ds}/graph", params={"account": a["account"]}).json()
    assert any(n["focus"] for n in g["nodes"]) and g["edges"]
    acc = analyst.get(f"/api/datasets/{ds}/accounts/{a['account']}").json()
    assert acc["timeline"] and acc["totals"]["in"] > 0
    rep = analyst.get(f"/api/alerts/{a['id']}/report").text
    assert "not a finding of criminal activity" in rep


def test_human_review_roles(analyst, lead, dataset):
    ds = dataset["dataset_id"]
    a = analyst.get(f"/api/datasets/{ds}/alerts").json()[0]
    assert analyst.post(f"/api/alerts/{a['id']}/notes", json={"text": "Checked KYC"}).json()["status"] == "in_review"
    assert analyst.post(f"/api/alerts/{a['id']}/status", json={"status": "closed_reported", "reason": "x" * 5}).status_code == 403
    assert analyst.post(f"/api/alerts/{a['id']}/status", json={"status": "escalated", "reason": "Needs lead"}).json()["status"] == "escalated"
    r = lead.post(f"/api/alerts/{a['id']}/status", json={"status": "closed_no_concern", "reason": "Customer explained activity"}).json()
    assert r["reviewed_by"] == "lead"
    assert analyst.post(f"/api/datasets/{ds}/accounts/{a['account']}/reveal", json={"text": "need"}).status_code == 403
    assert lead.post(f"/api/datasets/{ds}/accounts/{a['account']}/reveal", json={"text": "Case referral"}).json()["account_identifier"].startswith("AC")
    log = lead.get("/api/audit").json()
    assert any(x["action"] == "reveal_identifier" for x in log)
    assert analyst.get("/api/audit").status_code == 403


def test_institution_token_rejected_by_finlens(analyst):
    from finlens.main import app as fin
    assert TestClient(fin).get("/api/me", headers={"Authorization": analyst.headers["Authorization"]}).status_code == 401


# ---------- MuleGuard-derived detectors, rings, export, live ingest ----------
def test_shell_chain_detected():
    from mule import patterns
    t0 = pd.Timestamp("2026-09-01 10:00")
    rows = [("a", t0, "SRC", "S1", 90000), ("b", t0 + pd.Timedelta(minutes=10), "S1", "S2", 89000),
            ("c", t0 + pd.Timedelta(minutes=20), "S2", "S3", 88000), ("d", t0 + pd.Timedelta(minutes=30), "S3", "EXIT", 87000)]
    # give SRC and EXIT enough other activity so they are not "shells"
    rows += [(f"x{i}", t0 + pd.Timedelta(hours=i), "SRC", f"Z{i}", 100) for i in range(5)]
    rows += [(f"y{i}", t0 + pd.Timedelta(hours=i), f"W{i}", "EXIT", 100) for i in range(5)]
    df, _ = normalize_transactions(_df(rows))
    chains = patterns.shell_chains(df)
    assert ["SRC", "S1", "S2", "S3", "EXIT"] in chains
    ev = compute_features(df).evidence
    assert ev["S2"]["rules"]["shell_chain"]["stage"].startswith("stage")


def test_smurfing_and_ring_risk():
    from mule import patterns
    t0 = pd.Timestamp("2026-09-01 10:00")
    rows = [(f"s{i}", t0 + pd.Timedelta(hours=i), f"V{i}", "HUB", 500) for i in range(12)]
    rows += [(f"b{i}", t0 + pd.Timedelta(days=1, hours=i), f"P{i}", f"Q{i}", 50000) for i in range(12)]
    df, _ = normalize_transactions(_df(rows))
    sm = patterns.smurfing(df)
    assert sm["HUB"]["in"]["unique_counterparties"] >= 10
    assert patterns.ring_risk("cycle", 5) == 100 and patterns.ring_risk("smurfing_fan_in", 3) == 76
    assert patterns.account_pattern_score(["circular_flow", "shell_chain"]) == 45


def test_rings_and_json_export(analyst, dataset):
    ds = dataset["dataset_id"]
    rings = analyst.get(f"/api/datasets/{ds}/rings").json()
    assert any(r["pattern_type"] == "cycle" for r in rings)
    ex = analyst.get(f"/api/datasets/{ds}/export.json").json()
    assert set(ex) >= {"suspicious_accounts", "fraud_rings", "summary"}
    assert ex["summary"]["fraud_rings_detected"] == len(rings)
    assert all(a["account_id"].startswith("ACC-") for a in ex["suspicious_accounts"])


def test_live_append_mr_mule_schema(analyst):
    ds = analyst.post("/api/datasets/live", json={"name": "test live"}).json()["dataset_id"]
    rows = [f"TX_{i},ACC_A,ACC_{c},2500.00,2026-11-12 10:{i:02d}:00" for i, c in enumerate("BCDE")]
    r = analyst.post(f"/api/datasets/{ds}/append", json={"rows": rows}).json()
    assert r["added"] == 4 and r["summary"]["accounts"] == 5
    # cycle B->C->D->B arriving later
    more = ["TX_10,ACC_B,ACC_C,60000,2026-11-12 11:00:00", "TX_11,ACC_C,ACC_D,59000,2026-11-12 12:00:00", "TX_12,ACC_D,ACC_B,58000,2026-11-12 13:00:00"]
    r = analyst.post(f"/api/datasets/{ds}/append", json={"rows": more}).json()
    assert r["added"] == 3 and r["summary"]["fraud_rings"] >= 1
    # re-sending the same rows is idempotent
    assert analyst.post(f"/api/datasets/{ds}/append", json={"rows": more}).json()["added"] == 0


def test_deployed_model_is_ibm_trained_and_separates_demo_mules():
    from mule.detection import analyze, load_model
    m = load_model()
    assert m["feature_set"] == "model_features_v1" and "ibm" in m["version"]
    df, _ = normalize_transactions(DEMO)
    out = analyze(df)
    lab = pd.read_csv(ROOT / "data" / "samples" / "mule_demo_labels_for_evaluation.csv").set_index("account")["is_mule"]
    s = pd.Series(out["scores"])
    assert s[s.index.map(lab) == 1].mean() > 3 * s[s.index.map(lab) == 0].mean()


def test_live_simulator_streams_and_alerts_over_websocket(analyst):
    import time as _t
    from mule import live
    r = analyst.post("/api/live/start", json={"speed": 4, "auto_patterns": False}).json()
    ds = r["dataset_id"]
    tok = analyst.headers["Authorization"].split()[1]
    with analyst.websocket_connect(f"/ws/live/{ds}?token={tok}") as ws:
        assert ws.receive_json()["type"] == "hello"
        for p in ("fan_in_out", "pass_through"):
            assert analyst.post(f"/api/live/{ds}/inject", json={"pattern": p}).json()["pattern"] == p
        types, alerts = set(), []
        deadline = _t.time() + 40
        while _t.time() < deadline and not alerts:
            m = ws.receive_json()
            types.add(m["type"])
            if m["type"] == "alert":
                alerts.append(m)
        assert "batch" in types and alerts, types
        assert alerts[0]["account"].startswith("ACC-") and alerts[0]["priority"] in {"low", "medium", "high"}
    st = analyst.get(f"/api/live/{ds}/status").json()
    assert st["running"] and st["sent"] > 0 and len(st["injected"]) == 2
    out = analyst.post(f"/api/live/{ds}/tx", json={"sender": "AC1111111", "receiver": "AC2222222", "amount": 5000}).json()
    assert out["added"] == 1
    analyst.post(f"/api/live/{ds}/stop")
    acct = alerts[0]["account"]
    trail = analyst.get(f"/api/datasets/{ds}/trail", params={"account": acct}).json()
    assert trail and trail == sorted(trail, key=lambda x: x["ts"])


def test_model_is_not_trusted_on_tiny_datasets():
    """A handful of manual / mr-mule payments must not produce 'model only' alerts with score ~1.0:
    the model's features are relative to the dataset and meaningless with ~6 rows."""
    from mule.detection import analyze
    t0 = pd.Timestamp("2026-11-13 22:00")
    rows = [("T1", "A", "B", 5000, 0), ("T2", "B", "C", 4900, 2), ("T3", "C", "D", 4800, 4), ("T4", "D", "E", 4700, 6),
            ("T5", "E", "A", 4600, 8), ("T6", "A", "C", 3000, 10)]
    df = pd.DataFrame([(i, s, d, a, t0 + pd.Timedelta(minutes=m)) for i, s, d, a, m in rows],
                      columns=["txn_id", "src", "dst", "amount", "timestamp"])
    df["channel"] = "UPI"
    out = analyze(df)
    assert out["model_applicable"] is False and out["model_note"]
    assert not [a for a in out["alerts"] if not a["rule_families"]]  # nothing raised by the model alone
    # a real pattern on tiny data is still caught by the rules
    rows = [(f"F{i}", f"S{i}", "M", 9000, i * 3) for i in range(10)] + [("O1", "M", "X1", 40000, 40), ("O2", "M", "X2", 40000, 45)]
    df = pd.DataFrame([(i, s, d, a, t0 + pd.Timedelta(minutes=m)) for i, s, d, a, m in rows],
                      columns=["txn_id", "src", "dst", "amount", "timestamp"])
    df["channel"] = "UPI"
    m = next(a for a in analyze(df)["alerts"] if a["account"] == "M")
    assert m["priority"] == "medium" and not m["model_reliable"] and "rules only" in m["priority_reason"]
