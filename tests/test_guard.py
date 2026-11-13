"""Spend Guard: live SMS input, need/want personalisation, alerts, limits, jar, wishlist, events."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from finlens import sms as SMS
from finlens.main import app

SAMPLE = (ROOT / "data" / "samples" / "finlens_sample_statement.csv").read_bytes()


def _user(email: str, profile: dict | None = None) -> TestClient:
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/register", json={"email": email, "name": "Ananya Rao", "password": "test-pass-123"}).json()["token"]
    for p in ("analytics", "credit", "smart_spending"):
        c.post("/api/consents", json={"purpose": p, "granted": True})
    if profile:
        c.put("/api/profile", json={**profile, "completed": True})
    assert c.post("/api/statements", files={"file": ("s.csv", SAMPLE, "text/csv")}).status_code == 200
    return c


STUDENT = {"occupation": "student", "pocket_money": 10000, "stipend": 3500, "money_day": 1, "rent": 5000,
           "orders_per_week": 2, "food_budget_per_meal": 200, "priorities": ["save"]}


def test_bank_sms_formats():
    d = date(2026, 11, 13)
    p = SMS.parse("Rs.1,499.00 debited from A/c XX1234 on 13-11-26 to VPA lenskart@ybl (UPI Ref No 1). Avl Bal Rs 3,701.00", d)
    assert (p["type"], p["amount"], p["balance"], p["party"]) == ("debit", 1499.0, 3701.0, "lenskart")
    p = SMS.parse("INR 450.00 spent on HDFC Bank Card XX1234 at SWIGGY on 2026-11-13:21:14:05. Avl Lmt: INR 45,000", d)
    assert p["time"] == "21:14" and p["balance"] is None  # card limit is not a balance
    p = SMS.parse("Dear Customer, Rs 10,000.00 credited to your A/c XX1234 on 13-Nov-26 by NEFT from ACME TECH. Avl Bal: Rs 13,700", d)
    assert p["type"] == "credit" and p["balance"] == 13700
    assert SMS.parse("PRIYA has requested money from you on Google Pay. Rs 2,500 will be debited on approval.", d)["kind"] == "scam"
    assert SMS.parse("Your OTP for Rs 499 is 482910. Do not share.", d)["kind"] == "ignored"
    assert len(SMS.split_messages("Rs 10 debited ... \n\nRs 20 debited ...")) == 2


@pytest.fixture(scope="module")
def demo_day():
    c = _user("guard-demo@example.com", STUDENT)
    c.post("/api/goals", json={"name": "Goa trip", "target": 20000, "saved": 3000, "target_date": "2027-04-30"})
    c.put("/api/guard/settings", json={"caps": {"Shopping": 2500}})
    steps = []
    while True:
        r = c.post("/api/guard/demo/next").json()
        if "sms" not in r:
            break
        steps.append(r)
        if r["done"]:
            break
    return c, steps


def _alerts(steps, kind):
    return [a for s in steps for x in s["results"] for a in x.get("alerts", []) if a["kind"] == kind]


def test_demo_day_raises_the_right_alerts(demo_day):
    c, steps = demo_day
    assert len(steps) == SMS.DemoDay.length()
    by = {s["kind"]: s for s in steps}
    # needs stay silent: metro, chai, a normal canteen meal
    for k in ("need",):
        assert not any(x.get("alerts") for x in by[k]["results"])
    chai = next(s for s in steps if "CHAI POINT" in s["sms"])["results"][0]
    assert chai["nature"] == "need" and not chai["alerts"]  # a small chai is fine
    assert _alerts(steps, "duplicate") and _alerts(steps, "scam") and _alerts(steps, "fraud") and _alerts(steps, "charge")
    assert any("Shopping" in a["title"] for a in _alerts(steps, "cap"))
    wants = _alerts(steps, "want")
    assert any("Lenskart" in a["title"] for a in wants) and any("Dream11" in a["title"] for a in wants)
    swiggy = next(a for a in wants if "Swiggy" in a["title"])
    assert "your comfortable number is 2" in swiggy["body"]  # from the profile, not a global rule
    assert "Goa trip" in swiggy["body"]  # cost shown in the user's own goal
    lens = next(x for s in steps for x in s["results"] if x.get("merchant") == "Lenskart")
    assert lens["jar"]  # 10% want tax pledged


def test_today_explains_where_the_money_went(demo_day):
    c, _ = demo_day
    t = c.get("/api/guard/today").json()
    assert t["explain"]["speech"].startswith("Ananya, on 13 Nov you spent")
    assert t["explain"]["start_balance"] > t["explain"]["end_balance"]
    assert t["daily"]["color"] == "red" and "need" not in t["daily"]["by_nature"]  # the daily limit ignores needs
    assert t["jar"]["pending"] > 0 and t["reflect"]


def test_teaching_the_guard(demo_day):
    c, steps = demo_day
    a = _alerts(steps, "want")[0]
    r = c.post(f"/api/guard/alerts/{a['id']}", json={"action": "need"}).json()
    assert "always a need" in r["rule"]
    d = c.post(f"/api/guard/alerts/{_alerts(steps, 'fraud')[0]['id']}", json={"action": "dispute"}).json()
    assert any("1930" in x for x in d["steps"])
    pend = c.get("/api/guard/today").json()["reflect"]
    out = c.post("/api/guard/reflect", json={"txn_id": pend[0]["txn_id"], "verdict": "regret"}).json()
    assert out["ok"]


def test_before_you_pay_wishlist_and_jar(demo_day):
    c, _ = demo_day
    chk = c.post("/api/guard/check", json={"item": "Ray-Ban sunglasses", "price": 6500}).json()
    assert chk["verdict"] in ("stop", "wait") and chk["nature"] == "want" and chk["lenses"]
    w = c.post("/api/guard/wishlist", json={"name": "boAt earbuds", "price": 1499}).json()
    assert w["status"] == "waiting" and w["hours_left"] > 40
    c.post(f"/api/guard/wishlist/{w['id']}/decide", json={"decision": "skipped"})
    assert c.get("/api/guard/wishlist").json()["saved_by_skipping"] == 1499
    goal = c.get("/api/goals").json()["goals"][0]
    moved = c.post("/api/guard/jar/move", json={"goal_id": goal["id"]}).json()
    assert moved["moved"] > 0 and c.get("/api/goals").json()["goals"][0]["saved"] == round(goal["saved"] + moved["moved"], 2)


def test_insights_and_personal_defaults(demo_day):
    c, _ = demo_day
    ins = c.get("/api/guard/insights").json()
    assert ins["charges"]["total"] > 0 and ins["subscriptions"]["items"] and ins["emergency"]["target_months"] == 1
    assert all(l["merchant"] != "Metro" for l in ins["leaks"])  # commuting is a need, not a leak
    assert ins["streaks"]["badges"] and "diwali" in ins["events"]["suggested"]
    st = c.get("/api/guard/settings").json()
    assert st["settings"]["tone"] == "gentle" and st["settings"]["sensitivity"] == "strict"  # student who wants to save
    assert any("student" in w for w in st["why_defaults"])
    assert "Want-free days" in c.get("/api/guard/buddy").json()["text"]


def test_known_merchant_late_at_night_is_not_fraud():
    c = _user("guard-known@example.com", STUDENT)
    r = c.post("/api/guard/sms", json={"text": "Rs.1,200.00 debited from A/c XX1 on 12-11-26 23:45 to VPA myntra@ybl (UPI Ref 1). Avl Bal Rs 4,000.00"}).json()
    kinds = {a["kind"] for x in r["results"] for a in x.get("alerts", [])}
    assert "fraud" not in kinds  # the sample statement already has Myntra


def test_festival_plan_reserves_money():
    c = _user("guard-fest@example.com", STUDENT)
    before = c.get("/api/dashboard").json()["safe_to_spend"]["planned_savings"]
    c.put("/api/guard/settings", json={"festivals": ["christmas"], "festival_budgets": {"christmas": 3000}})
    sts = c.get("/api/dashboard").json()["safe_to_spend"]
    assert sts["event_reserve"] > 0 and sts["planned_savings"] == round(before + sts["event_reserve"], 2)
