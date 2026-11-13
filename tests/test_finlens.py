import math

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from finlens import analytics as A
from finlens import credit
from finlens.categorizer import categorize
from finlens.ingestion import parse_statement
from finlens.main import app

SAMPLE = (ROOT / "data" / "samples" / "finlens_sample_statement.csv").read_bytes()


# ---------- calculations ----------
def test_emi_matches_formula():
    p, rate, n = 100000, 12.0, 12
    r = rate / 1200
    expected = p * r * (1 + r) ** n / ((1 + r) ** n - 1)
    assert math.isclose(credit.emi(p, rate, n), expected, rel_tol=1e-12)
    assert round(credit.emi(p, rate, n), 2) == 8884.88
    assert credit.emi(12000, 0, 12) == 1000


def test_amortization_closes_to_zero():
    sched = credit.amortization(50000, 14, 24)
    assert sched[-1]["balance"] == 0
    assert math.isclose(sum(r["principal"] for r in sched), 50000, abs_tol=0.05)


def test_principal_inverse_of_emi():
    e = credit.emi(75000, 15, 18)
    assert math.isclose(credit.principal_for_emi(e, 15, 18), 75000, rel_tol=1e-9)


def test_safe_to_spend_blueprint_example():
    s = A.safe_to_spend(5800, [{"amount": 1500}], 800, 500, 0)
    assert s["safe_to_spend"] == 3000


# ---------- ingestion ----------
def test_ingestion_dedupes_reversal_and_uncertain():
    recs, rep = parse_statement(SAMPLE)
    assert rep["rows_in"] - rep["rows_out"] == 1  # one exact duplicate removed
    excluded = [r for r in recs if r["excluded"]]
    assert len(excluded) == 2  # Amazon debit + refund netted
    assert any(r["category"] == "Uncertain" for r in recs)
    pending = [r for r in recs if r["status"] == "pending"]
    assert len(pending) == 1


def test_credit_is_not_automatically_income():
    recs, _ = parse_statement(SAMPLE)
    kinds = {r["description"]: r["credit_kind"] for r in recs if r["type"] == "credit"}
    assert kinds["AMAZON REFUND ELECTRONICS ORDER"] == "refund"
    assert kinds["TRANSFER FROM SAVINGS A/C - SELF"] == "own_transfer"
    assert kinds["UPI FROM RAHUL K - TRIP SHARE"] == "p2p_in"
    assert kinds["NEFT CR MONTHLY ALLOWANCE FROM PARENT"] == "income"


def test_debit_credit_layout_gives_same_totals():
    a, _ = parse_statement(SAMPLE)
    b, _ = parse_statement((ROOT / "data" / "samples" / "finlens_sample_statement_debitcredit.csv").read_bytes())
    assert len(a) == len(b)
    assert sum(r["amount"] for r in a) == pytest.approx(sum(r["amount"] for r in b))


def test_spending_totals_are_traceable():
    recs, _ = parse_statement(SAMPLE)
    df = A.to_df(recs)
    for m in A.monthly_summary(df):
        cats = A.category_breakdown(df, m["month"])
        assert sum(c["total"] for c in cats) == pytest.approx(m["outflow"], abs=0.01)
        assert m["essential"] + m["discretionary"] == pytest.approx(m["outflow"], abs=0.01)


def test_month_to_date_change_and_recurring():
    df = A.to_df(parse_statement(SAMPLE)[0])
    ch = A.biggest_changes(df)[0]
    assert ch["category"] == "Food" and "month-to-date" in ch["comparison"]
    assert ch["by_subcategory"][0]["category"] == "Food delivery"
    rec = {r["merchant"] for r in A.detect_recurring(df)}
    assert {"Netflix", "Spotify", "Rent", "Jio"} <= rec


def test_categorization_quality():
    lab = pd.read_csv(ROOT / "data" / "samples" / "labelled_transactions.csv")
    pred = [categorize(r.description, r.type)["category"] for r in lab.itertuples()]
    acc = sum(p == e for p, e in zip(pred, lab.expected_category)) / len(lab)
    assert acc >= 0.9, list(zip(lab.description, pred, lab.expected_category))


# ---------- API ----------
@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": "student@example.com", "name": "Test Student", "password": "test-pass-123"})
    assert r.status_code == 200
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    return c


def test_upload_requires_consent(client):
    r = client.post("/api/statements", files={"file": ("s.csv", SAMPLE, "text/csv")})
    assert r.status_code == 403


def test_full_flow(client):
    assert client.post("/api/consents", json={"purpose": "analytics", "granted": True}).json()["analytics"]
    r = client.post("/api/statements", files={"file": ("s.csv", SAMPLE, "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["transactions_added"] == 132
    # re-upload is idempotent
    assert client.post("/api/statements", files={"file": ("s.csv", SAMPLE, "text/csv")}).json()["transactions_added"] == 0
    d = client.get("/api/dashboard").json()
    assert d["balance"]["balance"] == 5856.0
    assert d["safe_to_spend"]["safe_to_spend"] >= 0
    assert d["insight"] and "delivery" in d["insight"].lower()
    a = client.post("/api/ask", json={"question": "Why am I running out of money?"}).json()
    assert a["intent"] == "running_out" and a["facts"]["month_summary"]
    a = client.post("/api/ask", json={"question": "Can I afford headphones for ₹2,000?"}).json()
    assert a["intent"] == "afford" and "purchase_check" in a["facts"]
    a = client.post("/api/ask", json={"question": "How much did I spend on food delivery?"}).json()
    assert a["facts"]["matched"]["value"] == "Food delivery"


def test_category_correction_applies_to_merchant(client):
    tx = client.get("/api/transactions", params={"category": "Uncertain"}).json()
    tid = next(t["id"] for t in tx if t["type"] == "debit")
    r = client.patch(f"/api/transactions/{tid}/category", json={"category": "Shopping", "apply_to_merchant": True})
    assert r.json()["updated"] >= 1
    assert not [t for t in client.get("/api/transactions", params={"category": "Uncertain"}).json() if t["type"] == "debit"]


def test_goals(client):
    g = client.post("/api/goals", json={"name": "Laptop", "target": 40000, "saved": 5000, "target_date": "2027-06-30"}).json()
    plan = g["goals"][0]["plan"]
    assert plan["remaining"] == 35000 and plan["required_monthly"] > 0 and plan["alternatives"]


def test_credit_needs_separate_consent(client):
    body = {"principal": 30000, "annual_rate_pct": 14, "months": 12}
    assert client.post("/api/credit/assess", json=body).status_code == 403
    client.post("/api/consents", json={"purpose": "credit", "granted": True})
    r = client.post("/api/credit/assess", json=body).json()
    assert r["status"] in {"eligible", "conditional", "not_affordable_at_these_terms", "insufficient_information"}
    assert len(r["stress_tests"]) == 5 and "not a loan approval" in r["disclaimer"]
    assert r["requested"]["emi"] == round(credit.emi(30000, 14, 12), 2)


def test_smart_spending_conversation_demo_mode(client):
    r = client.post("/api/spending/message", json={"text": "I want to order biryani"}).json()
    assert not r["done"] and "budget" in r["reply"].lower()
    sid = r["session_id"]
    r = client.post("/api/spending/message", json={"text": "chicken biryani around ₹200", "session_id": sid}).json()
    assert not r["done"]
    r = client.post("/api/spending/message", json={"text": "delivery is fine", "session_id": sid}).json()
    assert r["done"], r
    res = r["results"]
    assert res["mode"] == "demo" and res["notice"]
    assert 1 <= len(res["results"]) <= 5
    assert all(x["is_demo"] and x["link"] and x["retrieved_at"] for x in res["results"])
    assert all(x["price"] <= 250 for x in res["results"])


def test_tokens_do_not_cross_products(client):
    from mule.main import app as mule_app
    m = TestClient(mule_app)
    assert m.get("/api/me", headers={"Authorization": client.headers["Authorization"]}).status_code == 401


def test_delete_my_data(client):
    assert client.delete("/api/data").json()["deleted"]
    assert client.get("/api/dashboard").status_code == 404


# ---------- CredWise-derived features ----------
def test_cashflow_score_formula():
    s = credit.cashflow_score(income=20000, expenses=12000, discretionary=4000)
    # 600 + 200*0.4=80 + 0 + 180*(0.6-0.6)=0 - 0 = 680
    assert s["score"] == 680 and s["band"] == "medium risk"
    assert credit.cashflow_score(0, 100, 0)["score"] is None


def test_pdf_statements_parse():
    from finlens.pdf_statement import pdf_to_csv_bytes
    csv, how = pdf_to_csv_bytes((ROOT / "data" / "samples" / "finlens_sample_statement_SYNTHETIC.pdf").read_bytes())
    recs, _ = parse_statement(csv)
    assert how.startswith("bank-lines") and len(recs) >= 130
    csv, how = pdf_to_csv_bytes((ROOT / "data" / "samples" / "upi_app_statement_SYNTHETIC.pdf").read_bytes())
    recs, _ = parse_statement(csv)
    assert how.startswith("upi-app") and [r["type"] for r in recs] == ["debit", "credit", "debit", "debit"]


def test_score_and_lender_bridge_endpoints():
    c = TestClient(app)
    tok = c.post("/api/auth/register", json={"email": "score@example.com", "name": "S", "password": "test-pass-123"}).json()["token"]
    c.headers["Authorization"] = "Bearer " + tok
    for p in ("analytics", "credit"):
        c.post("/api/consents", json={"purpose": p, "granted": True})
    pdf = (ROOT / "data" / "samples" / "finlens_sample_statement_SYNTHETIC.pdf").read_bytes()
    r = c.post("/api/statements", files={"file": ("s.pdf", pdf, "application/pdf")}).json()
    assert r["parsed_from"].startswith("pdf/bank-lines")
    s = c.post("/api/credit/score", json={"discretionary_cut_pct": 30}).json()
    assert s["what_if"]["score"] >= s["current"]["score"]
    lm = c.post("/api/credit/lender-model", json={"principal": 30000, "months": 12}).json()
    assert lm["available"] and lm["status"] in {"likely", "borderline", "unlikely"} and 0 <= lm["approval_probability"] <= 1
    assert lm["factors"] and len(lm["credit_score_curve"]) == 6 and "not an approval" in lm["caveat"]


def test_table_pdf_uses_pymupdf_or_pdfplumber():
    from finlens.pdf_statement import _pdfplumber_extract, _pymupdf_extract, pdf_to_csv_bytes
    raw = (ROOT / "data" / "samples" / "table_statement_SYNTHETIC.pdf").read_bytes()
    assert _pymupdf_extract(raw)[1] and _pdfplumber_extract(raw)[1]
    csv, how = pdf_to_csv_bytes(raw)
    recs, _ = parse_statement(csv)
    assert how.startswith("table/") and len(recs) >= 39


def test_verifier_rejects_invented_and_approximate_figures():
    from finlens.assistant import unverified_numbers
    facts = {"a": 13083.0, "b": [417.0, 2542.0]}
    assert unverified_numbers("You spent **₹13,083** and kept ₹417, up ₹2,542.", facts) == []
    bad = unverified_numbers("Together ₹5,592 extra; trimming saves ≈₹1,300 (~₹2,000).", facts)
    assert {"₹5,592", "₹1,300", "₹2,000"} <= set(bad)


def test_credit_band_uses_lending_club_model():
    df = A.to_df(parse_statement(SAMPLE)[0])
    r = credit.assess(df, 30000, 14, 12)
    sig = r["model_signal"]
    assert sig and "lc" in sig["model_version"] and sig["band"] in {"lower", "moderate", "higher"}
    assert 0 < sig["observed_default_rate_in_band"] < 1
    lo = credit.assess(df, 5000, 12, 6)["model_signal"]["modelled_probability"]
    hi = credit.assess(df, 300000, 12, 60)["model_signal"]["modelled_probability"]
    assert hi > lo  # a much larger, longer loan for the same income must look riskier
