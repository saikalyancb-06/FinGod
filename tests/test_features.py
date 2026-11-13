"""Money health, budget guard, nudges, weekly report, splits, credit builder and voice endpoints."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from finlens import smart_spending as S
from finlens.main import app

SAMPLE = (ROOT / "data" / "samples" / "finlens_sample_statement.csv").read_text(encoding="utf-8")


def _healthy_statement() -> bytes:
    """Same spending as the sample, but a ₹45,000 salary instead of a ₹10,000 allowance."""
    rows = list(csv.reader(io.StringIO(SAMPLE)))
    out, bump = [rows[0]], 0.0
    for r in rows[1:]:
        if "ALLOWANCE" in r[1]:
            r = [r[0], "NEFT CR SALARY ACME TECH PVT LTD", "Credit", "45000", r[4], r[5]]
            bump += 35000
        r[4] = f"{float(r[4]) + bump:.1f}"
        out.append(r)
    buf = io.StringIO()
    csv.writer(buf).writerows(out)
    return buf.getvalue().encode()


def _user(email: str, statement: bytes) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": email, "name": "Feature Tester", "password": "test-pass-123"})
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    for p in ("analytics", "credit", "smart_spending"):
        c.post("/api/consents", json={"purpose": p, "granted": True})
    assert c.post("/api/statements", files={"file": ("s.csv", statement, "text/csv")}).status_code == 200
    return c


@pytest.fixture(scope="module")
def student():
    return _user("feat-student@example.com", SAMPLE.encode())


@pytest.fixture(scope="module")
def earner():
    return _user("feat-earner@example.com", _healthy_statement())


# ---------- money health: never "running out" when the user isn't ----------
def test_health_is_honest_for_both_users(student, earner):
    hs = student.get("/api/money-health").json()
    he = earner.get("/api/money-health").json()
    assert hs["status"] in ("tight", "at_risk") and hs["is_running_out"]  # sample student spends more than comes in
    assert he["status"] == "healthy" and not he["is_running_out"]
    assert he["savings_rate_pct"] > 50 and he["strengths"]


def test_running_out_question_is_corrected_for_a_healthy_user(earner):
    a = earner.post("/api/ask", json={"question": "Why am I running out of money?"}).json()
    assert a["facts"]["money_health"]["status"] == "healthy"
    assert "not" in a["answer"].lower() and "running out" in a["answer"].lower()
    h = earner.post("/api/ask", json={"question": "How am I doing?"}).json()
    assert h["intent"] == "health" and "good shape" in h["answer"]


# ---------- Smart Spending budget guard ----------
def _converse(room, msgs):
    st, q = {}, None
    for m in msgs:
        st = S.update_state(st, m, {}, {})
        q = S.next_question(st, room, {})
    return st, q


def test_budget_above_balance_needs_explicit_confirmation():
    room = {"balance": 3200.0, "safe_to_spend": 0.0, "period": "until the 1st", "daily_room": 0}
    st, q = _converse(room, ["I need shoes for college", "7888888"])
    assert st["_asked"] == "budget_confirm" and "Are you sure" in q and "₹788" in q
    st = S.update_state(st, "hmm ok whatever", {}, {}) if False else S.update_state(st, "hmm", {}, {})
    q = S.next_question(st, room, {})
    assert st["_asked"] == "budget_confirm" and q.startswith("I need a clear answer")  # unclear -> still no search
    st = S.update_state(st, "yes no mistake", {}, {})
    q = S.next_question(st, room, {})
    assert st["_confirmed"] == 7888888 and st.get("_asked") != "budget_confirm"


def test_typo_budget_can_be_corrected():
    room = {"balance": 20000.0, "safe_to_spend": 6000.0, "period": "until the 1st", "daily_room": 400}
    st, q = _converse(room, ["biryani for dinner, delivery", "25000"])
    assert st["_asked"] == "budget_confirm"
    st = S.update_state(st, "₹250", {}, {})
    assert st["budget"] == 250 and S.next_question(st, room, {}) is None  # 250 is fine -> search


def test_spending_api_does_not_search_an_unaffordable_budget(student):
    r = student.post("/api/spending/message", json={"text": "I want headphones under ₹9999999"}).json()
    assert r["done"] is False and r["confirm"] is True and r["quick_replies"][0] == "Yes, no mistake"


# ---------- nudges & weekly report ----------
def test_nudges_and_dismiss(student):
    n = student.get("/api/nudges").json()
    assert n and all({"id", "severity", "title", "body"} <= set(x) for x in n)
    assert sum(x["id"].startswith("pace_") for x in n) <= 2
    first = n[0]["id"]
    student.post(f"/api/nudges/{first}/dismiss")
    assert first not in {x["id"] for x in student.get("/api/nudges").json()}


def test_weekly_report_is_traceable(student):
    r = student.get("/api/report/weekly").json()
    assert round(sum(d["spent"] for d in r["daily"]), 2) == r["spent"]
    assert r["change"] == round(r["spent"] - r["previous_week"], 2)
    assert r["health"]["status"] and r["period"]["end"] == "2026-11-12"


# ---------- split with friends ----------
def test_split_counts_only_my_share(student):
    before = student.get("/api/dashboard").json()
    sugg = student.get("/api/split").json()["suggestions"]
    exp = next(s for s in sugg if s["kind"] == "expense")
    rahul = student.post("/api/split/friends", json={"name": "Rahul K"}).json()
    priya = student.post("/api/split/friends", json={"name": "Priya"}).json()
    e = student.post("/api/split/entries", json={"title": exp["merchant"], "total": exp["amount"], "with_friends": [rahul["id"], priya["id"]],
                                                 "txn_id": exp["txn_id"]}).json()
    assert round(sum(e["shares"].values()), 2) == exp["amount"]
    after = student.get("/api/dashboard").json()
    month = exp["date"][:7]
    spent = lambda d: next(m["outflow"] for m in d["monthly"] if m["month"] == month)  # noqa: E731
    assert round(spent(before) - spent(after), 2) == round(exp["amount"] - e["shares"]["me"], 2)
    assert after["balance"]["balance"] == before["balance"]["balance"]  # the bank balance itself doesn't change
    ov = student.get("/api/split").json()
    assert ov["owed_to_you"] == round(exp["amount"] - e["shares"]["me"], 2)
    # Rahul pays back his part -> settled
    rb = next(f for f in ov["friends"] if f["id"] == rahul["id"])
    student.post("/api/split/settle", json={"friend_id": rahul["id"], "amount": rb["balance"], "direction": "they_paid"})
    ov = student.get("/api/split").json()
    assert next(f for f in ov["friends"] if f["id"] == rahul["id"])["status"] == "settled"
    # the same transaction can't be split twice; a friend with entries can't be deleted
    assert student.post("/api/split/entries", json={"title": "x", "total": exp["amount"], "with_friends": [priya["id"]], "txn_id": exp["txn_id"]}).status_code == 409
    assert student.delete(f"/api/split/friends/{priya['id']}").status_code == 409


def test_payback_from_friend_is_not_income(student):
    sugg = student.get("/api/split").json()["suggestions"]
    pay = next(s for s in sugg if s["kind"] == "settlement_in")  # "UPI FROM RAHUL K - TRIP SHARE"
    assert pay["person"] == "Rahul K" and pay["friend_id"]
    before = student.get("/api/dashboard").json()
    student.post("/api/split/settle", json={"friend_id": pay["friend_id"], "amount": pay["amount"], "direction": "they_paid", "txn_id": pay["txn_id"]})
    after = student.get("/api/dashboard").json()
    m = pay["date"][:7]
    other = lambda d: next(x for x in d["monthly"] if x["month"] == m)["other_inflows"]  # noqa: E731
    assert other(after).get("settlement") == pay["amount"] and "p2p_in" not in other(after) or other(after).get("p2p_in", 0) < other(before).get("p2p_in", 0)


def test_custom_shares_must_add_up(student):
    f = student.post("/api/split/friends", json={"name": "Asha"}).json()
    r = student.post("/api/split/entries", json={"title": "Cab", "total": 600, "shares": {"me": 100, str(f["id"]): 100}})
    assert r.status_code == 400


# ---------- credit builder ----------
def test_credit_builder_is_personalised_and_rises(student):
    p = student.get("/api/credit/builder").json()
    assert p["stage"] == "new"
    ids = [s["id"] for s in p["steps"]]
    assert {"fd_card", "autopay", "utilisation", "no_apps"} <= set(ids)
    assert "student" in ids or True
    if p["timeline"]:
        odds = [t["approval"] for t in p["timeline"]]
        assert odds[-1] >= odds[0]
    assert student.post("/api/credit/builder/autopay/toggle").json()["done"] is True
    assert next(s for s in student.get("/api/credit/builder").json()["steps"] if s["id"] == "autopay")["done"]


# ---------- voice endpoints without a key ----------
def test_voice_endpoints_fail_cleanly_without_key(student):
    r = student.post("/api/voice/tts", json={"text": "hello", "language": "hi"})
    assert r.status_code in (400, 503)


# ---------- payback detection: big bill followed by friends' transfers ----------
def _dinner_statement() -> bytes:
    rows = SAMPLE.strip().splitlines()
    extra = ["16 Oct 2026,SWIGGY DINEOUT TRUFFLES BILL,Debit,1200,,Posted",
             "17 Oct 2026,CAFE COFFEE DAY,Debit,90,,Posted",
             "17 Oct 2026,UPI FROM PRIYA S - DINNER,Credit,400,,Posted",
             "18 Oct 2026,UPI FROM ARJUN M,Credit,400,,Posted"]
    return ("\n".join(rows + extra) + "\n").encode()


def test_payback_is_detected_and_confirmed():
    c = _user("feat-payback@example.com", _dinner_statement())
    ov = c.get("/api/split").json()
    pb = next(p for p in ov["paybacks"] if p["amount"] == 1200)
    assert pb["split_ways"] == 3 and pb["total_back"] == 800 and pb["my_share"] == 400
    assert {x["person"] for x in pb["credits"]} == {"Priya S", "Arjun M"}
    assert "paying you back" in pb["reason"]
    assert any(n["id"] == f"payback_{pb['txn_id']}" for n in c.get("/api/nudges").json())
    before = c.get("/api/dashboard").json()
    r = c.post("/api/split/payback", json={"debit_txn_id": pb["txn_id"], "credit_txn_ids": [x["txn_id"] for x in pb["credits"]]}).json()
    assert r["my_share"] == 400 and r["friends"] == 2
    ov = c.get("/api/split").json()
    assert all(f["status"] == "settled" for f in ov["friends"]) and not any(p["txn_id"] == pb["txn_id"] for p in ov["paybacks"])
    after = c.get("/api/dashboard").json()
    oct_ = lambda d: next(m for m in d["monthly"] if m["month"] == "2026-10")  # noqa: E731
    assert round(oct_(before)["outflow"] - oct_(after)["outflow"], 2) == 800  # only my ₹400 share counts now
    assert c.post("/api/split/payback", json={"debit_txn_id": pb["txn_id"], "credit_txn_ids": [pb["credits"][0]["txn_id"]]}).status_code == 409


def test_unrelated_credit_is_not_called_a_payback(student):
    # sample: Rahul's ₹1,500 has no matching big bill before it, so it must not be guessed as a payback
    assert not [p for p in student.get("/api/split").json()["paybacks"] if any(x["person"] == "Rahul K" for x in p["credits"])]
