"""Profile, spending room, budget guard, goal pace/impact and the built-in lender model."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from finlens import analytics as A
from finlens import goals as G
from finlens import lender
from finlens import room as R
from finlens import smart_spending as S
from finlens.ingestion import parse_statement
from finlens.main import app

SAMPLE = (ROOT / "data" / "samples" / "finlens_sample_statement.csv").read_bytes()
STUDENT = {"occupation": "student", "age": 20, "living": "hostel", "city": "Bengaluru", "area": "Koramangala",
           "pocket_money": 10000, "stipend": 3500, "money_day": 1, "rent": 5000, "diet": "veg", "shoe_size": "9",
           "food_budget_per_meal": 200, "completed": True}


def test_goal_pace_statuses():
    today = date(2026, 11, 13)
    start = datetime(2026, 9, 13)
    g = {"name": "Laptop", "target": 40000, "saved": 5000, "start_saved": 0, "created_at": start, "target_date": date(2027, 3, 13)}
    p = G.pace(g, today)
    assert p["status"] == "behind" and p["expected_by_now"] > 5000 and p["required_monthly"] > 0
    g2 = {**g, "saved": 20000}
    assert G.pace(g2, today)["status"] == "ahead"
    assert G.pace({**g, "saved": 40000}, today)["status"] == "achieved"
    gi = G.impact(4000, {"name": "Laptop", "monthly_contribution": 7000, "pace": p})
    assert gi["days"] == pytest.approx(4000 / (7000 / 30.44), rel=0.01)


def test_room_from_profile_only_and_suggestion():
    room = R.compute(None, None, STUDENT, today=date(2026, 11, 13))
    assert room["source"] == "profile" and room["daily_room"] > 0 and room["days_left"] == 18
    sug = R.suggested_budget(room, "food", STUDENT)
    assert sug["cap"] and sug["cap"] <= 300 and any("₹200 per meal" in r for r in sug["reasons"])


def test_budget_guard_asks_then_accepts_group():
    room = R.compute(None, None, STUDENT, today=date(2026, 11, 13))
    st = {"category": "food", "item": "fast food", "budget": 4000.0, "delivery": True}
    q = S.next_question(st, room, STUDENT)
    assert q and st["_asked"] == "budget_check" and "₹4,000" in q
    st = S.update_state(st, "it's for 10 people", STUDENT)
    assert st["party"] == 10
    assert S.next_question(st, room, STUDENT) is None  # ₹400 each is acceptable-ish -> search proceeds
    st2 = {"category": "food", "item": "fast food", "budget": 4000.0, "delivery": True}
    S.next_question(st2, room, STUDENT)
    st2 = S.update_state(st2, "use that", STUDENT)
    assert st2["budget"] == st2["_suggested"] and S.next_question(st2, room, STUDENT) is None


def test_shorthand_and_profile_seeding():
    s = S.update_state({"category": "food", "_asked": "item"}, "fast", STUDENT)
    assert s["item"] == "fast food" and "veg" in s["preferences"]
    f = S.seed_from_profile({"category": "fashion", "item": "sneakers"}, STUDENT)
    assert f["size"] == "UK 9"


def test_lender_model_explains_and_is_monotonic():
    df = A.to_df(parse_statement(SAMPLE)[0])
    base = {"principal": 30000, "months": 12, "annual_rate_pct": 14}
    r = lender.assess(df, STUDENT, base)
    assert r["status"] in {"likely", "borderline", "unlikely"} and r["factors"]
    curve = [c["approval"] for c in r["credit_score_curve"]]
    assert curve == sorted(curve)  # higher credit score never lowers approval
    worse = lender.assess(df, STUDENT, {**base, "past_default": True})
    assert worse["approval_probability"] < r["approval_probability"]


def test_profile_api_and_personalised_room():
    c = TestClient(app)
    tok = c.post("/api/auth/register", json={"email": "prof@example.com", "name": "P", "password": "test-pass-123"}).json()["token"]
    c.headers["Authorization"] = "Bearer " + tok
    out = c.put("/api/profile", json={**STUDENT, "bogus_field": 1}).json()
    assert out["profile"]["occupation"] == "student" and "bogus_field" not in out["profile"]
    assert c.get("/api/me").json()["settings"]["location"] == "Koramangala, Bengaluru"
    c.post("/api/goals", json={"name": "Laptop", "target": 40000, "saved": 2000, "target_date": str(date.today() + timedelta(days=120))})
    g = c.get("/api/goals").json()["goals"][0]
    assert g["pace"]["status"] in {"on_track", "ahead"} and g["start_saved"] == 2000
    g = c.post(f"/api/goals/{g['id']}/deposit", json={"amount": 1000}).json()["goals"][0]
    assert g["saved"] == 3000
    room = c.get("/api/spending/room").json()
    assert room["source"] == "profile" and room["primary_goal"]["name"] == "Laptop"
    # ₹13,500 in − ₹5,000 rent − ~₹9,400/month for the laptop leaves nothing safe to spend,
    # so FinLens must not suggest a "comfortable" food budget
    assert room["safe_to_spend"] == 0 and room["suggested"]["food"]["cap"] is None
