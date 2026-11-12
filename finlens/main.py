"""FinLens API (B2C). Run: python -m uvicorn finlens.main:app --port 8001"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select

from shared import config, llm
from shared.audit import setup_logging
from shared.security import hash_password, make_token, read_token, verify_password

from . import analytics as A
from . import assistant, credit, smart_spending
from .categorizer import CATEGORIES
from .db import CategoryOverride, Consent, Goal, SessionLocal, Settings, SpendSession, Statement, Txn, cipher, init_db
from .ingestion import parse_statement

log = setup_logging("finlens")
app = FastAPI(title="FinLens API", version="1.0", description="Personal financial intelligence (prototype, decision support only).")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
init_db()

PURPOSES = {"analytics": "Analyse my uploaded statement to show spending insights, forecasts and the assistant.",
            "smart_spending": "Use a limited summary of my typical spending to personalise recommendations and estimate savings.",
            "credit": "Use my cash-flow data to estimate loan affordability (separate from spending analytics)."}


# ---------------- auth ----------------
def current_user(authorization: str = Header(default="")) -> int:
    tok = authorization.removeprefix("Bearer ").strip()
    body = read_token(tok, config.FINLENS_SECRET)
    if not body or body.get("aud") != "finlens":
        raise HTTPException(401, "Not signed in to FinLens")
    return int(body["sub"])


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@app.post("/api/auth/register")
def register(body: RegisterIn):
    from .db import User
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == body.email.lower())):
            raise HTTPException(409, "An account with this email already exists")
        u = User(email=body.email.lower(), name=body.name, password_hash=hash_password(body.password))
        db.add(u)
        db.commit()
        db.add(Settings(user_id=u.id, emergency_buffer=500.0, manual_commitments=[]))
        db.commit()
        return {"token": make_token({"sub": u.id, "aud": "finlens", "role": "consumer"}, config.FINLENS_SECRET), "name": u.name}


@app.post("/api/auth/login")
def login(body: LoginIn):
    from .db import User
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == body.email.lower()))
        if not u or not verify_password(body.password, u.password_hash):
            raise HTTPException(401, "Invalid email or password")
        return {"token": make_token({"sub": u.id, "aud": "finlens", "role": "consumer"}, config.FINLENS_SECRET), "name": u.name}


def _consents(db, uid) -> dict:
    rows = db.scalars(select(Consent).where(Consent.user_id == uid).order_by(Consent.granted_at)).all()
    state = {p: False for p in PURPOSES}
    for r in rows:
        state[r.purpose] = r.granted and r.revoked_at is None
    return state


def require_consent(db, uid, purpose):
    if not _consents(db, uid).get(purpose):
        raise HTTPException(403, f"Consent required for '{purpose}': {PURPOSES[purpose]}")


@app.get("/api/me")
def me(uid: int = Depends(current_user)):
    from .db import User
    with SessionLocal() as db:
        u = db.get(User, uid)
        s = db.get(Settings, uid)
        n = db.query(Txn).filter(Txn.user_id == uid).count()
        return {"id": u.id, "name": u.name, "email": u.email, "consents": _consents(db, uid), "purposes": PURPOSES,
                "settings": {"emergency_buffer": s.emergency_buffer, "opening_balance": s.opening_balance,
                             "manual_commitments": s.manual_commitments or [], "location": s.location or config.DEFAULT_LOCATION},
                "transactions": n}


class ConsentIn(BaseModel):
    purpose: str
    granted: bool


@app.post("/api/consents")
def set_consent(body: ConsentIn, uid: int = Depends(current_user)):
    if body.purpose not in PURPOSES:
        raise HTTPException(400, "Unknown purpose")
    with SessionLocal() as db:
        for r in db.scalars(select(Consent).where(Consent.user_id == uid, Consent.purpose == body.purpose, Consent.revoked_at.is_(None))):
            r.revoked_at = datetime.utcnow()
        if body.granted:
            db.add(Consent(user_id=uid, purpose=body.purpose, granted=True))
        db.commit()
        return _consents(db, uid)


class SettingsIn(BaseModel):
    emergency_buffer: float | None = Field(default=None, ge=0)
    opening_balance: float | None = None
    manual_commitments: list[dict] | None = None
    location: str | None = None


@app.put("/api/settings")
def put_settings(body: SettingsIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        s = db.get(Settings, uid)
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(s, k, v)
        db.commit()
    return me(uid)


# ---------------- data ----------------
def _overrides(db, uid) -> dict[str, str]:
    return {o.merchant_key: o.category for o in db.scalars(select(CategoryOverride).where(CategoryOverride.user_id == uid))}


@app.post("/api/statements")
async def upload_statement(file: UploadFile = File(...), uid: int = Depends(current_user)):
    content = await file.read()
    fname = (file.filename or "").lower()
    if not fname.endswith((".csv", ".pdf")):
        raise HTTPException(400, "Please upload a CSV or PDF statement")
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        try:
            parsed_from = "csv"
            csv_bytes = content
            if fname.endswith(".pdf"):
                from .pdf_statement import pdf_to_csv_bytes
                csv_bytes, parsed_from = pdf_to_csv_bytes(content)
                parsed_from = f"pdf/{parsed_from}"
            recs, report = parse_statement(csv_bytes, _overrides(db, uid), default_year=date.today().year)
            report["parsed_from"] = parsed_from
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        st = Statement(user_id=uid, filename=Path(file.filename).name, encrypted_file=cipher.encrypt(content), report=json.loads(json.dumps(report, default=str)))
        db.add(st)
        db.flush()
        existing = {t.uid for t in db.scalars(select(Txn).where(Txn.user_id == uid))}
        added = 0
        for r in recs:
            if r["uid"] in existing:
                continue
            db.add(Txn(user_id=uid, statement_id=st.id, uid=r["uid"], date=r["date"], description_enc=cipher.encrypt(r["description"]),
                       type=r["type"], amount=r["amount"], balance=r["balance"], status=r["status"], merchant=r["merchant"],
                       merchant_key=r["merchant_key"], category=r["category"], subcategory=r["subcategory"],
                       category_source=r["category_source"], credit_kind=r["credit_kind"], is_essential=r["is_essential"],
                       excluded=r["excluded"], note=r["note"], row=r["row"]))
            added += 1
        db.commit()
        report["transactions_added"] = added
        report["statement_id"] = st.id
        return report


@app.get("/api/statements")
def list_statements(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        return [{"id": s.id, "filename": s.filename, "uploaded_at": s.uploaded_at, "report": s.report}
                for s in db.scalars(select(Statement).where(Statement.user_id == uid).order_by(Statement.uploaded_at.desc()))]


@app.delete("/api/data")
def delete_my_data(uid: int = Depends(current_user)):
    """Remove statements, transactions, goals, corrections and chat sessions. Account and consent history remain."""
    with SessionLocal() as db:
        for M in (Txn, Statement, Goal, CategoryOverride, SpendSession):
            db.execute(delete(M).where(M.user_id == uid))
        db.commit()
    return {"deleted": True}


@app.delete("/api/account")
def delete_account(uid: int = Depends(current_user)):
    from .db import User
    with SessionLocal() as db:
        for M in (Txn, Statement, Goal, CategoryOverride, SpendSession, Consent, Settings):
            db.execute(delete(M).where(M.user_id == uid))
        db.execute(delete(User).where(User.id == uid))
        db.commit()
    return {"deleted": True}


def load_df(db, uid):
    rows = db.scalars(select(Txn).where(Txn.user_id == uid).order_by(Txn.date, Txn.row)).all()
    if not rows:
        raise HTTPException(404, "No transactions yet - upload a statement first")
    return A.to_df([r.as_dict() for r in rows])


def _context(db, uid, df) -> dict:
    s = db.get(Settings, uid)
    bal = A.current_balance(df, s.opening_balance)
    rec = A.detect_recurring(df)
    ref = A.as_of(df)
    import calendar
    month_end = date(ref.year, ref.month, calendar.monthrange(ref.year, ref.month)[1])
    commits = A.upcoming_commitments(rec, ref, month_end)
    for c in s.manual_commitments or []:
        commits.append({"merchant": c.get("name", "Commitment"), "due": c.get("due"), "amount": float(c.get("amount", 0)), "source": "added by you"})
    goals = _goals(db, uid, df)
    planned = sum(g["monthly_contribution"] or 0 for g in goals)
    pending = float(df[(df["status"] == "pending") & (df["type"] == "debit")]["amount"].sum())
    sts = A.safe_to_spend(bal["balance"], commits, planned, s.emergency_buffer, pending) if bal["balance"] is not None else None
    fc = A.forecast_month_end(df, bal["balance"], rec) if bal["balance"] is not None else None
    return {"balance": bal, "recurring": rec, "safe_to_spend": sts, "forecast": fc, "goals": goals}


def _capacity(df) -> float:
    ms = A.monthly_summary(df)
    if len(ms) > 1 and df["date"].max().day < 25:
        ms = ms[:-1]
    return max(0.0, sum(m["income"] - m["outflow"] for m in ms) / max(1, len(ms)))


def _discretionary(df) -> float:
    ms = A.monthly_summary(df)
    if len(ms) > 1 and df["date"].max().day < 25:
        ms = ms[:-1]
    return sum(m["discretionary"] for m in ms) / max(1, len(ms))


def _goals(db, uid, df=None) -> list[dict]:
    have = df is not None and not df.empty
    cap = _capacity(df) if have else 0.0
    disc = _discretionary(df) if have else 0.0
    out = []
    for g in db.scalars(select(Goal).where(Goal.user_id == uid).order_by(Goal.created_at)):
        plan = A.goal_plan(g.target, g.saved, g.target_date, cap, date.today(), disc)
        contrib = g.monthly_contribution if g.monthly_contribution is not None else plan.get("required_monthly")
        out.append({"id": g.id, "name": g.name, "target": g.target, "saved": g.saved, "target_date": g.target_date,
                    "monthly_contribution": contrib, "plan": plan})
    return out


@app.get("/api/transactions")
def transactions(month: str | None = None, category: str | None = None, q: str | None = None, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        rows = [r.as_dict() for r in db.scalars(select(Txn).where(Txn.user_id == uid).order_by(Txn.date.desc(), Txn.row.desc()))]
    if month:
        rows = [r for r in rows if str(r["date"])[:7] == month]
    if category:
        rows = [r for r in rows if r["category"] == category]
    if q:
        rows = [r for r in rows if q.lower() in r["description"].lower()]
    return rows


class CategoryFix(BaseModel):
    category: str
    apply_to_merchant: bool = True


@app.patch("/api/transactions/{tid}/category")
def fix_category(tid: int, body: CategoryFix, uid: int = Depends(current_user)):
    allowed = set(CATEGORIES) | {"Income", "Refund", "Own transfer", "Borrowed", "Transfers in", "Uncertain"}
    if body.category not in allowed:
        raise HTTPException(400, f"Category must be one of {sorted(allowed)}")
    from .categorizer import is_essential
    credit_map = {"Income": "income", "Refund": "refund", "Own transfer": "own_transfer", "Borrowed": "borrowed", "Transfers in": "p2p_in", "Uncertain": "uncertain"}
    with SessionLocal() as db:
        t = db.get(Txn, tid)
        if not t or t.user_id != uid:
            raise HTTPException(404, "Transaction not found")
        targets = [t]
        if body.apply_to_merchant:
            targets = db.scalars(select(Txn).where(Txn.user_id == uid, Txn.merchant_key == t.merchant_key, Txn.type == t.type)).all()
            o = db.scalar(select(CategoryOverride).where(CategoryOverride.user_id == uid, CategoryOverride.merchant_key == t.merchant_key))
            if o:
                o.category = body.category
            else:
                db.add(CategoryOverride(user_id=uid, merchant_key=t.merchant_key, category=body.category))
        for x in targets:
            if x.category != body.category:
                x.subcategory = None  # old subcategory no longer applies
            x.category = body.category
            x.category_source = "user"
            if x.type == "credit":
                x.credit_kind = credit_map.get(body.category, "uncertain")
            else:
                x.is_essential = is_essential(body.category, x.subcategory)
        db.commit()
        return {"updated": len(targets)}


@app.get("/api/categories")
def categories():
    return {"spending": CATEGORIES + ["Uncertain"], "credit": ["Income", "Refund", "Own transfer", "Borrowed", "Transfers in", "Uncertain"]}


@app.get("/api/dashboard")
def dashboard(month: str | None = None, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        df = load_df(db, uid)
        ctx = _context(db, uid, df)
    months = sorted(df["month"].unique())
    m = month or months[-1]
    ch = A.biggest_changes(df)
    insight = assistant.template_answer("why_change", {"biggest_changes_vs_previous_month": ch}) if ch else None
    if ch and ch[0].get("by_subcategory"):
        s = ch[0]["by_subcategory"][0]
        insight = (f"Your {ch[0]['category'].lower()} spending is up ₹{ch[0]['change']:,.0f} versus the same days last month. "
                   f"Most of it is {s['category'].lower()}: {s['previous_count']} → {s['current_count']} orders while the average order "
                   f"stayed around ₹{s['current_avg']:,.0f}." if s["main_driver"] == "count" and ch[0]["change"] > 0 else insight)
    return {
        "as_of": str(A.as_of(df)), "months": months, "month": m,
        "balance": ctx["balance"], "safe_to_spend": ctx["safe_to_spend"], "forecast": ctx["forecast"],
        "monthly": A.monthly_summary(df), "categories": A.category_breakdown(df, m),
        "recurring": ctx["recurring"], "changes": ch[:4], "insight": insight,
        "uncertain_count": int((df["category"] == "Uncertain").sum()), "goals": ctx["goals"],
        "daily": [{"date": str(d.date()), "spend": round(v, 2)} for d, v in A.spend_df(df[df["month"] == m]).groupby("date")["amount"].sum().items()],
        "heatmap": [{"date": str(d.date()), "spend": round(v, 2), "count": int(c)} for (d, v), c in zip(
            A.spend_df(df).groupby("date")["amount"].sum().items(), A.spend_df(df).groupby("date")["amount"].count().values)],
        "period": [str(df["date"].min().date()), str(A.as_of(df))],
    }


@app.get("/api/forecast")
def forecast(include_income: bool = False, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        df = load_df(db, uid)
        ctx = _context(db, uid, df)
    if ctx["balance"]["balance"] is None:
        raise HTTPException(422, "Balance unknown: add an opening balance in settings")
    return A.forecast_month_end(df, ctx["balance"]["balance"], ctx["recurring"], include_income)


class WhatIf(BaseModel):
    price: float = Field(gt=0)


@app.post("/api/whatif")
def whatif(body: WhatIf, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        df = load_df(db, uid)
        ctx = _context(db, uid, df)
    if not ctx["safe_to_spend"]:
        raise HTTPException(422, "Balance unknown")
    return A.what_if_purchase(body.price, ctx["safe_to_spend"])


# ---------------- goals ----------------
class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target: float = Field(gt=0)
    saved: float = Field(default=0, ge=0)
    target_date: date | None = None
    monthly_contribution: float | None = Field(default=None, ge=0)


@app.get("/api/goals")
def get_goals(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        df = None
        if db.query(Txn).filter(Txn.user_id == uid).count():
            df = load_df(db, uid)
        return {"goals": _goals(db, uid, df), "monthly_capacity_estimate": round(_capacity(df), 2) if df is not None else 0.0}


@app.post("/api/goals")
def add_goal(body: GoalIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        db.add(Goal(user_id=uid, **body.model_dump()))
        db.commit()
    return get_goals(uid)


@app.patch("/api/goals/{gid}")
def edit_goal(gid: int, body: GoalIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        g = db.get(Goal, gid)
        if not g or g.user_id != uid:
            raise HTTPException(404, "Goal not found")
        for k, v in body.model_dump().items():
            setattr(g, k, v)
        db.commit()
    return get_goals(uid)


@app.delete("/api/goals/{gid}")
def del_goal(gid: int, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        db.execute(delete(Goal).where(Goal.id == gid, Goal.user_id == uid))
        db.commit()
    return get_goals(uid)


# ---------------- Ask FinLens ----------------
class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)


@app.post("/api/ask")
def ask(body: AskIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        df = load_df(db, uid)
        ctx = _context(db, uid, df)
    return assistant.answer(body.question, df, ctx)


# ---------------- Smart Spending ----------------
class SpendMsg(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    session_id: int | None = None
    location: str | None = None


@app.post("/api/spending/message")
def spending_message(body: SpendMsg, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        sess = db.get(SpendSession, body.session_id) if body.session_id else None
        if not sess or sess.user_id != uid:
            sess = SpendSession(user_id=uid, state={}, messages=[])
            db.add(sess)
            db.flush()
        state = smart_spending.update_state(sess.state or {}, body.text)
        msgs = list(sess.messages or []) + [{"role": "user", "text": body.text}]
        q = smart_spending.next_question(state)
        payload = {"session_id": sess.id, "state": {k: v for k, v in state.items() if not k.startswith("_")}}
        if q:
            msgs.append({"role": "assistant", "text": q})
            payload.update(reply=q, done=False)
        else:
            db.commit()  # release the write lock before the (slow) search, which writes its own cache rows
            df = None
            if _consents(db, uid).get("smart_spending") and db.query(Txn).filter(Txn.user_id == uid).count():
                df = load_df(db, uid)
            s = db.get(Settings, uid)
            loc = body.location or (s.location if s else None) or config.DEFAULT_LOCATION
            res = smart_spending.run_search(state, loc, df)
            n = len(res["results"])
            reply = (f"Here are your top {n} options for {res['query']}" + (f" around ₹{state['budget']:,.0f}" if state.get("budget") else "") + "."
                     if n else "I couldn't find matching listings within that budget. Try a different budget or item.")
            if df is None:
                reply += " (Enable the Smart Spending consent to see savings estimates based on your typical spending.)"
            msgs.append({"role": "assistant", "text": reply, "results": res})
            payload.update(reply=reply, done=True, results=res, location=loc)
            state = {"category": state.get("category")}  # keep category for a quick follow-up search
        sess.state, sess.messages, sess.updated_at = state, msgs, datetime.utcnow()
        db.commit()
        return payload


@app.post("/api/spending/reset")
def spending_reset(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        db.execute(delete(SpendSession).where(SpendSession.user_id == uid))
        db.commit()
    return {"reply": smart_spending.QUESTIONS["category"]}


# ---------------- Alternative Credit Intelligence ----------------
class CreditIn(BaseModel):
    principal: float = Field(gt=0, le=5_000_000)
    annual_rate_pct: float = Field(ge=0, le=60)
    months: int = Field(gt=0, le=360)
    declared_obligations: float = Field(default=0, ge=0)


@app.post("/api/credit/assess")
def credit_assess(body: CreditIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        require_consent(db, uid, "credit")
        df = load_df(db, uid)
    return credit.assess(df, body.principal, body.annual_rate_pct, body.months, body.declared_obligations)


class ScoreIn(BaseModel):
    discretionary_cut_pct: float = Field(default=0, ge=0, le=100)
    income_increase_pct: float = Field(default=0, ge=0, le=200)
    monthly_investment: float = Field(default=0, ge=0)


@app.post("/api/credit/score")
def credit_score(body: ScoreIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        require_consent(db, uid, "credit")
        df = load_df(db, uid)
    return credit.score_with_what_if(df, body.discretionary_cut_pct, body.income_increase_pct, body.monthly_investment)


class LenderIn(BaseModel):
    principal: float = Field(gt=0)
    months: int = Field(gt=0, le=360)
    no_of_dependents: int = Field(default=0, ge=0, le=20)
    education: str = Field(default="Graduate", pattern="^(Graduate|Not Graduate)$")
    self_employed: str = Field(default="No", pattern="^(Yes|No)$")
    income_annum: float | None = Field(default=None, ge=0)
    residential_assets: float = Field(default=0, ge=0)
    commercial_assets: float = Field(default=0, ge=0)
    luxury_assets: float = Field(default=0, ge=0)
    bank_assets: float = Field(default=0, ge=0)


@app.post("/api/credit/lender-model")
def lender_model(body: LenderIn, uid: int = Depends(current_user)):
    """Optional bridge to the team's existing loan-approval model API (CredWise). Shown only as a
    separate, caveated signal: it predicts historical approval decisions, not repayment."""
    import httpx
    with SessionLocal() as db:
        require_consent(db, uid, "credit")
        df = load_df(db, uid)
    payload = credit.lender_model_payload(df, body.principal, body.months, body.model_dump())
    caveat = ("This external model follows the Kaggle loan-approval schema, so its label is past approve/reject decisions, "
              "not actual repayment. It may reproduce historical lending bias. It does not change the FinLens affordability status.")
    if not config.LOAN_API_BASE_URL:
        return {"configured": False, "payload": payload, "caveat": caveat,
                "message": "Set LOAN_API_BASE_URL in .env to connect the existing CredWise loan model (POST /api/loan/predict)."}
    try:
        r = httpx.post(f"{config.LOAN_API_BASE_URL.rstrip('/')}/api/loan/predict", json=payload, timeout=20)
        r.raise_for_status()
        return {"configured": True, "payload": payload, "response": r.json(), "caveat": caveat}
    except Exception as exc:
        return {"configured": True, "payload": payload, "error": f"Loan model API unreachable: {str(exc)[:160]}", "caveat": caveat}


@app.get("/api/credit/emi")
def credit_emi(principal: float = Query(gt=0), annual_rate_pct: float = Query(ge=0, le=60), months: int = Query(gt=0, le=360)):
    e = credit.emi(principal, annual_rate_pct, months)
    return {"emi": round(e, 2), "total_payment": round(e * months, 2), "total_interest": round(e * months - principal, 2),
            "formula": "EMI = P × r × (1+r)^n / ((1+r)^n − 1), r = annual rate / 12 / 100",
            "schedule": credit.amortization(principal, annual_rate_pct, months)}


# ---------------- meta ----------------
@app.get("/api/meta")
def meta():
    cm = config.ARTIFACT_DIR / "credit_metrics.json"
    return {"product": "FinLens", "llm": {"provider": "Groq", "model": config.GROQ_MODEL, "configured": llm.available()},
            "search": {"provider": "SerpAPI", "configured": bool(config.SERP_API_KEY)},
            "credit_model": json.loads(cm.read_text()) if cm.exists() else None,
            "positioning": "Prototype decision-support application. Not a registered Account Aggregator, lender or adviser."}


@app.get("/api/sample-statement")
def sample_statement():
    return FileResponse(config.ROOT / "data" / "samples" / "finlens_sample_statement.csv", filename="finlens_sample_statement_SYNTHETIC.csv")
