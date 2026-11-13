"""Regression tests for the consistency audit: outputs that used to contradict themselves."""
from datetime import date, timedelta

import pandas as pd
from fastapi.testclient import TestClient

from conftest import ROOT
from finlens import analytics as A
from finlens import goals as G
from finlens.amounts import budget, party_size, price
from finlens.main import app
from mule.features import compute_features

SAMPLE = (ROOT / "data" / "samples" / "finlens_sample_statement.csv").read_bytes()


def _user(email: str, statement: bytes) -> TestClient:
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/register", json={"email": email, "name": "Audit", "password": "test-pass-123"}).json()["token"]
    for p in ("analytics", "credit", "smart_spending"):
        c.post("/api/consents", json={"purpose": p, "granted": True})
    assert c.post("/api/statements", files={"file": ("s.csv", statement, "text/csv")}).status_code == 200
    return c


def test_amounts_read_like_people_write_them():
    assert price("Can I afford a ₹1.5 lakh laptop?") == 150000
    assert price("a phone for Rs 25k") == 25000
    assert price("the iPhone 15") is None  # model number, not a price
    assert budget("running shoes under 2k") == 2000 and budget("UK 10") is None
    assert budget("biryani for 4 people around 1200") == 1200 and party_size("biryani for 4 people around 1200") == 4


def test_goal_monthly_need_is_never_inflated():
    today = date(2026, 11, 13)
    soon = {"name": "Trip", "target": 10000, "saved": 2000, "target_date": today + timedelta(days=3)}
    assert G.pace(soon, today)["required_monthly"] == 8000  # the whole remainder, not 4x it
    late = {**soon, "target_date": today - timedelta(days=10)}
    p = G.pace(late, today)
    assert p["status"] == "overdue" and p["required_monthly"] is None and p["overdue_days"] == 10
    plan = A.goal_plan(10000, 2000, today + timedelta(days=3), 5000, today)
    assert plan["required_monthly"] == 8000 and plan["months_to_target"] == 1


def test_achieved_goal_reserves_nothing_and_health_is_honest():
    c = _user("audit1@example.com", SAMPLE)
    before = c.get("/api/dashboard").json()["safe_to_spend"]["safe_to_spend"]
    c.post("/api/goals", json={"name": "Done", "target": 1000, "saved": 1500, "monthly_contribution": 3000})
    d = c.get("/api/dashboard").json()
    assert d["safe_to_spend"]["safe_to_spend"] == before and d["safe_to_spend"]["planned_savings"] == 0
    h = d["health"]
    assert not any("safe-to-spend covers" in s for s in h["strengths"])  # 11 days of room for 18 days is a watch-out
    assert d["safe_to_spend"]["period"]  # the label Home shows comes from the API


def test_health_unknown_without_any_signal():
    rows = "Date,Description,Type,Amount\n" + "\n".join(f"0{d} Nov 2026,SWIGGY ORDER,Debit,300" for d in range(1, 5)) + "\n"
    c = _user("audit2@example.com", rows.encode())
    h = c.get("/api/money-health").json()
    assert h["status"] in ("unknown", "tight") and not h["status"] == "healthy"


def test_forecast_short_history_and_income_already_received():
    c = _user("audit3@example.com", SAMPLE)
    f = c.get("/api/forecast?include_income=true").json()
    assert f["expected_income"] == 0 and f["start_balance"] == 5856.0  # Nov income already in the balance
    rows = "Date,Description,Type,Amount,Balance\n" + "\n".join(
        f"0{d} Nov 2026,CAFE COFFEE DAY,Debit,150,{5000 - 150 * d}" for d in range(1, 9)) + "\n"
    c2 = _user("audit4@example.com", rows.encode())
    assert "last-8-day" in c2.get("/api/forecast").json()["assumptions"][0]


def test_two_orders_a_week_apart_are_not_a_subscription():
    df = A.to_df([{"id": i, "uid": str(i), "date": d, "description": "SWIGGY", "type": "debit", "amount": 450.0, "balance": None,
                   "status": "posted", "merchant": "Swiggy", "merchant_key": "swiggy", "category": "Food", "subcategory": "Food delivery",
                   "category_source": "rule", "credit_kind": None, "is_essential": False, "excluded": False, "note": None, "row": i}
                  for i, d in enumerate([date(2026, 11, 1), date(2026, 11, 8)])])
    assert A.detect_recurring(df) == []


def test_weekly_report_outside_statement_and_change_wording():
    c = _user("audit5@example.com", SAMPLE)
    r = c.get("/api/report/weekly?end=2026-07-20").json()
    assert r["no_data"] and "starts on" in r["message"]
    a = c.post("/api/ask", json={"question": "Why did my spending go up?"}).json()["answer"]
    assert "stayed around" not in a or "average rose" not in a
    f = c.post("/api/ask", json={"question": "What will my month-end balance be?"}).json()
    assert f["intent"] == "forecast"
    s = c.post("/api/ask", json={"question": "How much is safe to spend?"}).json()["answer"]
    assert "pending" in s  # the subtraction now adds up


def test_credit_counts_emis_once():
    rows = ["Date,Description,Type,Amount"]
    for m in ("08", "09", "10"):
        rows += [f"01/{m}/2026,SALARY ACME,Credit,50000", f"02/{m}/2026,RENT HOUSE,Debit,15000", f"05/{m}/2026,HDFC LOAN EMI,Debit,10000",
                 f"06/{m}/2026,BIGBASKET GROCERY,Debit,5000", f"10/{m}/2026,AMAZON SHOPPING,Debit,7000"]
    c = _user("audit6@example.com", ("\n".join(rows) + "\n").encode())
    r = c.post("/api/credit/assess", json={"principal": 50000, "annual_rate_pct": 14, "months": 12}).json()
    assert r["cash_flow"]["monthly_surplus"] == 13000 and r["status"] == "eligible"


def test_pass_through_share_is_not_double_counted():
    t0 = pd.Timestamp("2026-09-01 10:00")
    rows = [(f"I{i}", f"S{i}", "M", 10000.0, t0 + pd.Timedelta(minutes=i)) for i in range(10)] + [("O1", "M", "X", 10000.0, t0 + pd.Timedelta(minutes=20))]
    df = pd.DataFrame(rows, columns=["txn_id", "src", "dst", "amount", "timestamp"])
    df["channel"] = "UPI"
    ev = compute_features(df).evidence["M"]
    assert "rapid_pass_through" not in ev["rules"]  # only 10% of ₹1,00,000 left within the hour
