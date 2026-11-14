"""FinLens API (B2C). Run: python -m uvicorn finlens.main:app --port 8001"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from datetime import date as Day
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select

from shared import config, llm, sarvam
from shared.audit import setup_logging
from shared.security import hash_password, make_token, read_token, verify_password

from . import analytics as A
from . import assistant, credit, lender, smart_spending
from . import goals as goals_mod
from .categorizer import CATEGORIES
from . import profile as PR
from . import room as room_mod
from .db import (CategoryOverride, Consent, CreditStep, Friend, Goal, NudgeDismissal, Profile, SessionLocal, Settings, SpendSession,
                 SplitEntry, Statement, Txn, cipher, init_db)
from . import credit_builder, nudges as nudges_mod, split as split_mod
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
                "transactions": n, "profile": _profile(db, uid)}


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
        from .db import GuardSettings, NatureRule, GuardAlert, WishItem, Reflection, JarEntry
        for M in (Txn, Statement, Goal, CategoryOverride, SpendSession, Profile, GuardSettings, NatureRule, GuardAlert, WishItem, Reflection, JarEntry):
            db.execute(delete(M).where(M.user_id == uid))
        db.commit()
    return {"deleted": True}


@app.delete("/api/account")
def delete_account(uid: int = Depends(current_user)):
    from .db import User
    with SessionLocal() as db:
        from .db import GuardSettings, NatureRule, GuardAlert, WishItem, Reflection, JarEntry
        for M in (Txn, Statement, Goal, CategoryOverride, SpendSession, Profile, Consent, Settings, Friend, SplitEntry,
                  NudgeDismissal, CreditStep, GuardSettings, NatureRule, GuardAlert, WishItem, Reflection, JarEntry):
            db.execute(delete(M).where(M.user_id == uid))
        db.execute(delete(User).where(User.id == uid))
        db.commit()
    return {"deleted": True}


def _split_entries(db, uid) -> list[dict]:
    return [{"id": e.id, "kind": e.kind, "title": e.title, "date": str(e.date), "total": e.total, "paid_by": e.paid_by,
             "shares": e.shares or {}, "txn_id": e.txn_id}
            for e in db.scalars(select(SplitEntry).where(SplitEntry.user_id == uid).order_by(SplitEntry.date.desc(), SplitEntry.id.desc()))]


def load_df(db, uid, my_share: bool = True):
    rows = db.scalars(select(Txn).where(Txn.user_id == uid).order_by(Txn.date, Txn.row)).all()
    if not rows:
        raise HTTPException(404, "No transactions yet - upload a statement first")
    df = A.to_df([r.as_dict() for r in rows])
    # shared bills count only my share; money friends paid back isn't income
    return split_mod.apply_my_share(df, _split_entries(db, uid)) if my_share else df


def _context(db, uid, df) -> dict:
    s = db.get(Settings, uid)
    bal = A.current_balance(df, s.opening_balance)
    rec = A.detect_recurring(df)
    ref = A.as_of(df)
    # safe-to-spend lasts until the next money day (profile) or month end - the same horizon Smart Spending uses
    prof_row = db.get(Profile, uid)
    money_day = PR.merge(prof_row.data if prof_row else None, {}).get("money_day")
    days_left, period = room_mod._days_until_money(ref, money_day)
    horizon_end = ref + timedelta(days=days_left)
    commits = A.upcoming_commitments(rec, ref, horizon_end)
    for c in s.manual_commitments or []:
        commits.append({"merchant": c.get("name", "Commitment"), "due": c.get("due"), "amount": float(c.get("amount", 0)), "source": "added by you"})
    goals = _goals(db, uid, df)
    planned = sum(g["monthly_contribution"] or 0 for g in goals if not g["pace"].get("done"))  # achieved goals reserve nothing
    # festivals / personal events the user planned in Spend Guard reserve their monthly set-aside too
    from .db import GuardSettings
    gs = db.get(GuardSettings, uid)
    event_reserve = 0.0
    if gs and (gs.data.get("festivals") or gs.data.get("events")):
        from . import guard as GD
        prof_d = PR.merge(prof_row.data if prof_row else None, {})
        event_reserve = GD.events(None, GD.merge_settings(gs.data, prof_d), prof_d, ref)["monthly_reserve"]
        planned += event_reserve
    pending = float(df[(df["status"] == "pending") & (df["type"] == "debit")]["amount"].sum())
    sts = A.safe_to_spend(bal["balance"], commits, planned, s.emergency_buffer, pending) if bal["balance"] is not None else None
    fc = A.forecast_month_end(df, bal["balance"], rec) if bal["balance"] is not None else None
    if sts:
        sts.update(period=period, horizon_end=str(horizon_end), days_left=days_left, event_reserve=round(event_reserve, 2))
    health = A.money_health(df, sts, fc, goals)
    return {"balance": bal, "recurring": rec, "safe_to_spend": sts, "forecast": fc, "goals": goals, "health": health}


def _months_for_averages(df):
    """Complete months when possible; a single partial month is scaled up to a full month."""
    ms = A.monthly_summary(df)
    scale = 1.0
    if len(ms) > 1 and df["date"].max().day < 25:
        ms = ms[:-1]
    elif len(ms) == 1:
        covered = (df["date"].max() - df["date"].min()).days + 1
        scale = 30.44 / max(7, min(covered, 30.44))
    return ms, scale


def _surplus(df) -> float:
    ms, scale = _months_for_averages(df)
    return sum(m["income"] - m["outflow"] for m in ms) / max(1, len(ms)) * scale


def _capacity(df) -> float:
    return max(0.0, _surplus(df))


def _discretionary(df) -> float:
    ms, scale = _months_for_averages(df)
    return sum(m["discretionary"] for m in ms) / max(1, len(ms)) * scale


def _goals(db, uid, df=None) -> list[dict]:
    have = df is not None and not df.empty
    cap = _capacity(df) if have else 0.0
    disc = _discretionary(df) if have else 0.0
    out = []
    for g in db.scalars(select(Goal).where(Goal.user_id == uid).order_by(Goal.created_at)):
        plan = A.goal_plan(g.target, g.saved, g.target_date, cap, date.today(), disc)
        rec = {"id": g.id, "name": g.name, "target": g.target, "saved": g.saved, "target_date": g.target_date,
               "created_at": g.created_at, "start_saved": g.start_saved}
        rec["pace"] = goals_mod.pace(rec)
        if rec["pace"].get("done"):
            contrib = None
        elif g.monthly_contribution is not None:
            contrib = g.monthly_contribution
        else:
            contrib = rec["pace"].get("required_monthly") if g.target_date else plan.get("required_monthly")
        rec.update(monthly_contribution=contrib, plan=plan)
        out.append(rec)
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
    insight = A.describe_change(ch[0]) if ch else None
    if ch and ch[0].get("by_subcategory"):
        s = ch[0]["by_subcategory"][0]
        if s["category"] != ch[0]["category"] and abs(s["change"]) >= 0.5 * abs(ch[0]["change"]):
            insight += f" Most of it is {s['category'].lower()} ({s['previous_count']} → {s['current_count']} payments)."
    return {
        "as_of": str(A.as_of(df)), "months": months, "month": m,
        "balance": ctx["balance"], "safe_to_spend": ctx["safe_to_spend"], "forecast": ctx["forecast"], "health": ctx["health"],
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
        return {"goals": _goals(db, uid, df), "monthly_capacity_estimate": round(_capacity(df), 2) if df is not None else 0.0,
                "avg_monthly_surplus": round(_surplus(df), 2) if df is not None else None}


@app.post("/api/goals")
def add_goal(body: GoalIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        db.add(Goal(user_id=uid, start_saved=body.saved, **body.model_dump()))
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


class DepositIn(BaseModel):
    amount: float = Field(gt=0, le=10_000_000)


@app.post("/api/goals/{gid}/deposit")
def goal_deposit(gid: int, body: DepositIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        g = db.get(Goal, gid)
        if not g or g.user_id != uid:
            raise HTTPException(404, "Goal not found")
        g.saved = round(float(g.saved) + body.amount, 2)
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
    language: str | None = None  # en | hi | kn (defaults to the profile language)


@app.post("/api/ask")
def ask(body: AskIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        df = load_df(db, uid)
        ctx = _context(db, uid, df)
        prof_full = _profile(db, uid)
        ctx["profile"] = PR.summary(prof_full)
        ctx["goal_status"] = [{"goal": g["name"], "target": g["target"], "saved": g["saved"],
                               "target_date": str(g["target_date"]) if g["target_date"] else None,
                               "status": g["pace"].get("status"), "needs_per_month": g["pace"].get("required_monthly"),
                               "days_left": g["pace"].get("days_left"),
                               "behind_by": round(-g["pace"]["gap"], 2) if g["pace"].get("gap", 0) < 0 else 0}
                              for g in ctx.get("goals", [])]
    lang = (body.language or prof_full.get("language") or "en")[:2]
    style = prof_full.get("language_style") or "formal"
    question_en, detected = sarvam.to_english(body.question)
    out = assistant.answer(question_en, df, ctx)
    out["language"] = lang
    if lang != "en":
        out["answer_en"] = out["answer"]
        out["answer"] = sarvam.translate_markdown(out["answer"], lang, style)
    if question_en != body.question:
        out["question_en"] = question_en
    return out


# ---------------- Voice & language (Sarvam AI) ----------------
@app.post("/api/voice/stt")
async def voice_stt(file: UploadFile = File(...), language: str = Form(default="auto"), uid: int = Depends(current_user)):
    if not sarvam.available():
        raise HTTPException(503, "Voice needs SARVAM_API_KEY in .env")
    audio = await file.read()
    if len(audio) > 8 * 1024 * 1024:
        raise HTTPException(413, "Recording too long")
    try:
        out = sarvam.stt(audio, file.filename or "audio.wav", None if language == "auto" else language)
    except sarvam.SarvamError as exc:
        raise HTTPException(502, str(exc))
    return out


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    language: str = "en"


@app.post("/api/voice/tts")
def voice_tts(body: TTSIn, uid: int = Depends(current_user)):
    if not sarvam.available():
        raise HTTPException(503, "Voice needs SARVAM_API_KEY in .env")
    try:
        return {"audios": sarvam.tts(body.text, body.language), "format": "wav"}
    except sarvam.SarvamError as exc:
        raise HTTPException(502, str(exc))


# ---------------- Smart Spending ----------------
class SpendMsg(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    language: str | None = None
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
        prof = _profile(db, uid)
        lang = (body.language or prof.get("language") or "en")[:2]
        style = prof.get("language_style") or "formal"
        tr = (lambda t: sarvam.translate(t, lang, "en", style)) if lang != "en" else (lambda t: t)
        text_en, _ = sarvam.to_english(body.text)
        df, room = _spending_room(db, uid, prof)
        state = smart_spending.update_state(sess.state or {}, text_en, prof, PR.summary(prof))
        msgs = list(sess.messages or []) + [{"role": "user", "text": body.text}]
        q = smart_spending.next_question(state, room, prof)
        payload = {"session_id": sess.id, "state": {k: v for k, v in state.items() if not k.startswith("_")}, "room": room}
        if q:
            q = tr(q)
            quick = state.pop("_quick", None) if state.get("_asked") == "budget_confirm" else None
            state.pop("_quick", None)
            msgs.append({"role": "assistant", "text": q})
            payload.update(reply=q, done=False, quick_replies=quick, confirm=state.get("_asked") == "budget_confirm")
        else:
            db.commit()  # release the write lock before the (slow) search, which writes its own cache rows
            s = db.get(Settings, uid)
            loc = body.location or PR.location(prof, (s.location if s else None) or config.DEFAULT_LOCATION)
            res = smart_spending.run_search(state, loc, df, room)
            res["room"] = room
            n = len(res["results"])
            party = int(state.get("party") or 1)
            budget_txt = ""
            if state.get("budget"):
                budget_txt = (f" for {party} people (about ₹{float(state['budget']) / party:,.0f} each)" if party > 1
                              else f" around ₹{float(state['budget']):,.0f}")
            reply = (f"Here are your top {n} options for {res['query']} near {loc}{budget_txt}."
                     if n else "I couldn't find matching listings within that budget. Try a different budget or item.")
            if state.get("_confirmed") and room and room.get("safe_to_spend") is not None and state["_confirmed"] > room["safe_to_spend"]:
                reply += (f" You confirmed this budget, but it's more than your safe-to-spend (₹{room['safe_to_spend']:,.0f}), "
                          "so check each price against what you can afford.")
            if df is None:
                reply += " (Enable the Smart Spending consent to see savings estimates based on your typical spending.)"
            if lang != "en":
                reply = tr(reply)
                for r in res["results"]:
                    r["why"] = tr(r.get("why") or "")
                    if r.get("goal_impact"):
                        r["goal_impact"]["text"] = tr(r["goal_impact"]["text"])
            msgs.append({"role": "assistant", "text": reply, "results": res})
            payload.update(reply=reply, done=True, results=res, location=loc)
            state = {"category": state.get("category")}  # keep category for a quick follow-up search
        sess.state, sess.messages, sess.updated_at = state, msgs, datetime.utcnow()
        db.commit()
        return payload


def _profile(db, uid) -> dict:
    row = db.get(Profile, uid)
    return PR.merge(row.data if row else None, {})


def _spending_room(db, uid, prof: dict):
    """Statement data is used only with the Smart Spending consent; otherwise the declared profile."""
    df, ctx = None, None
    if _consents(db, uid).get("smart_spending") and db.query(Txn).filter(Txn.user_id == uid).count():
        df = load_df(db, uid)
        ctx = _context(db, uid, df)
    goals = ctx.get("goals") if ctx else _goals(db, uid, None)
    return df, room_mod.compute(df, ctx, prof, goals=goals)


@app.get("/api/profile")
def get_profile(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        return {"profile": _profile(db, uid), "options": PR.options()}


@app.put("/api/profile")
def put_profile(body: dict, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        row = db.get(Profile, uid)
        data = PR.merge(row.data if row else None, body)
        if row:
            row.data, row.updated_at = data, datetime.utcnow()
        else:
            db.add(Profile(user_id=uid, data=data))
        s = db.get(Settings, uid)
        if s and (body.get("city") or body.get("area")):
            s.location = PR.location(data, config.DEFAULT_LOCATION)
        if s and body.get("emergency_buffer") is not None:
            s.emergency_buffer = float(data["emergency_buffer"])
        db.commit()
        return {"profile": data, "options": PR.options()}


@app.get("/api/spending/room")
def spending_room(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        prof = _profile(db, uid)
        _, room = _spending_room(db, uid, prof)
    room["suggested"] = {c: room_mod.suggested_budget(room, c, prof) for c in ("food", "fashion", "shopping")}
    return room


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
    principal: float = Field(gt=0, le=10_000_000)
    months: int = Field(gt=0, le=360)
    annual_rate_pct: float = Field(default=14, ge=0, le=60)
    purpose: str = Field(default="", pattern="^(|Personal|Education|Medical|Business|Home Improvement|Debt Consolidation)$")
    product: str = Field(default="Personal Loan", pattern="^(Personal Loan|Credit Card|Line of Credit)$")
    cibil_score: float | None = Field(default=None, ge=300, le=900)
    credit_history_years: float | None = Field(default=None, ge=0, le=50)
    past_default: bool = False
    late_payments_2y: int = Field(default=0, ge=0, le=20)
    negative_records: int = Field(default=0, ge=0, le=10)
    existing_debt_outstanding: float | None = Field(default=None, ge=0)
    savings: float | None = Field(default=None, ge=0)


@app.post("/api/credit/lender-model")
def lender_model(body: LenderIn, uid: int = Depends(current_user)):
    """Built-in lender-style approval estimate (trained in ml/train_lender_model.py). If LOAN_API_BASE_URL
    is set, the team's original CredWise API is also called and shown alongside, for comparison."""
    with SessionLocal() as db:
        require_consent(db, uid, "credit")
        df = load_df(db, uid) if db.query(Txn).filter(Txn.user_id == uid).count() else None
        prof = _profile(db, uid)
    out = lender.assess(df, prof, body.model_dump())
    if config.LOAN_API_BASE_URL and df is not None:
        import httpx
        payload = credit.lender_model_payload(df, body.principal, body.months, {})
        try:
            r = httpx.post(f"{config.LOAN_API_BASE_URL.rstrip('/')}/api/loan/predict", json=payload, timeout=20)
            r.raise_for_status()
            out["external_credwise"] = {"response": r.json(), "payload": payload}
        except Exception as exc:
            out["external_credwise"] = {"error": f"CredWise API unreachable: {str(exc)[:120]}"}
    return out


@app.get("/api/credit/emi")
def credit_emi(principal: float = Query(gt=0), annual_rate_pct: float = Query(ge=0, le=60), months: int = Query(gt=0, le=360)):
    e = credit.emi(principal, annual_rate_pct, months)
    return {"emi": round(e, 2), "total_payment": round(e * months, 2), "total_interest": round(e * months - principal, 2),
            "formula": "EMI = P × r × (1+r)^n / ((1+r)^n − 1), r = annual rate / 12 / 100",
            "schedule": credit.amortization(principal, annual_rate_pct, months)}


# ---------------- meta ----------------
# ---------------- Nudges & weekly report ----------------
def _tr_fields(items: list[dict], keys: tuple, lang: str, style: str):
    if lang == "en":
        return items
    for it in items:
        for k in keys:
            if it.get(k):
                it[k] = sarvam.translate(it[k], lang, "en", style)
    return items


@app.get("/api/nudges")
def get_nudges(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        if not db.query(Txn).filter(Txn.user_id == uid).count():
            return []
        df = load_df(db, uid)
        ctx = _context(db, uid, df)
        prof = _profile(db, uid)
        room = room_mod.compute(df, ctx, prof, goals=ctx["goals"])
        dismissed = {d.nudge_id for d in db.scalars(select(NudgeDismissal).where(NudgeDismissal.user_id == uid))}
    out = nudges_mod.compute(df, ctx, prof, room, dismissed)
    with SessionLocal() as db:
        from .guard_routes import guard_nudges
        out += [n for n in guard_nudges(db, uid) if n["id"] not in dismissed]
    with SessionLocal() as db:
        linked = {e["txn_id"] for e in _split_entries(db, uid) if e.get("txn_id")}
        raw = load_df(db, uid, my_share=False)  # paybacks look at the statement as-is
    for pb in split_mod.paybacks(raw, linked, dismissed)[:2]:
        names = [c["person"] or "a friend" for c in pb["credits"]]
        out.insert(0, {"id": f"payback_{pb['txn_id']}", "severity": "info", "icon": "🤝",
                       "title": f"Did {' & '.join(names)} pay you back for {pb['merchant']}?",
                       "body": pb["reason"], "action": {"label": "Review", "link": "/finlens/split"}})
    h = ctx.get("health") or {}
    if h and h["status"] != "unknown" and f"health_{h['status']}_{A.as_of(df):%Y-%W}" not in dismissed:
        sev = {"healthy": "good", "steady": "info", "tight": "warn", "at_risk": "alert"}[h["status"]]
        body = h["headline"] + (" " + h["strengths"][0][0].upper() + h["strengths"][0][1:] + "." if h["status"] in ("healthy", "steady") and h["strengths"]
                                else " " + h["watch_outs"][0][0].upper() + h["watch_outs"][0][1:] + "." if h["watch_outs"] else "")
        out.insert(0 if sev in ("alert", "warn") else len(out), {
            "id": f"health_{h['status']}_{A.as_of(df):%Y-%W}", "severity": sev,
            "icon": {"healthy": "💚", "steady": "🙂", "tight": "⚠️", "at_risk": "🚨"}[h["status"]],
            "title": f"Money health: {h['label']}", "body": body,
            "action": {"label": "Ask FinLens", "link": "/finlens/ask?q=How am I doing this month?"}})
    lang = (prof.get("language") or "en")[:2]
    return _tr_fields(out, ("title", "body"), lang, prof.get("language_style") or "formal")


@app.post("/api/nudges/{nid}/dismiss")
def dismiss_nudge(nid: str, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        db.add(NudgeDismissal(user_id=uid, nudge_id=nid[:160]))
        db.commit()
    return {"dismissed": nid}


@app.get("/api/report/weekly")
def weekly_report(end: date | None = None, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        df = load_df(db, uid)
        ctx = _context(db, uid, df)
        prof = _profile(db, uid)
    rep_ = nudges_mod.weekly_report(df, ctx, end)
    rep_["health"] = ctx.get("health") if rep_.get("is_latest_week") else None
    rep_["name"] = prof.get("name")
    lang = (prof.get("language") or "en")[:2]
    if lang != "en":
        style = prof.get("language_style") or "formal"
        for k in ("win", "focus"):
            if rep_.get(k):
                rep_[k] = sarvam.translate(rep_[k], lang, "en", style)
        if rep_.get("health"):
            rep_["health"] = {**rep_["health"], "headline": sarvam.translate(rep_["health"]["headline"], lang, "en", style)}
    return rep_


# ---------------- Split with friends ----------------
def _friends(db, uid) -> list[dict]:
    return [{"id": f.id, "name": f.name, "emoji": f.emoji or "🙂"}
            for f in db.scalars(select(Friend).where(Friend.user_id == uid).order_by(Friend.name))]


@app.get("/api/split")
def split_overview(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        friends = _friends(db, uid)
        entries = _split_entries(db, uid)
        df = load_df(db, uid, my_share=False) if db.query(Txn).filter(Txn.user_id == uid).count() else None
    bal = split_mod.balances(entries, friends)
    names = {str(f["id"]): f["name"] for f in friends}
    for e in entries:
        e["paid_by_name"] = "You" if e["paid_by"] == 0 else names.get(str(e["paid_by"]), "?")
        e["my_share"] = float(e["shares"].get("me", 0))
        e["people"] = ["You" if k == "me" else names.get(str(k), "?") for k in e["shares"]]
    linked = {e["txn_id"] for e in entries if e.get("txn_id")}
    with SessionLocal() as db:
        dismissed = {d.nudge_id for d in db.scalars(select(NudgeDismissal).where(NudgeDismissal.user_id == uid))}
    pb = split_mod.paybacks(df, linked, dismissed)
    in_pb = {c["txn_id"] for p in pb for c in p["credits"]} | {p["txn_id"] for p in pb}
    sugg = [x for x in split_mod.suggestions(df, linked | in_pb, friends)]
    return {"friends": bal, "entries": entries, "paybacks": pb, "suggestions": sugg,
            "owed_to_you": round(sum(b["balance"] for b in bal if b["balance"] > 0), 2),
            "you_owe": round(-sum(b["balance"] for b in bal if b["balance"] < 0), 2)}


class FriendIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    emoji: str | None = Field(default=None, max_length=8)


@app.post("/api/split/friends")
def add_friend(body: FriendIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        existing = db.scalars(select(Friend).where(Friend.user_id == uid)).all()
        for f in existing:
            if f.name.strip().lower() == body.name.strip().lower():
                return {"id": f.id, "name": f.name, "emoji": f.emoji}
        f = Friend(user_id=uid, name=body.name.strip(), emoji=body.emoji)
        db.add(f)
        db.commit()
        return {"id": f.id, "name": f.name, "emoji": f.emoji}


@app.delete("/api/split/friends/{fid}")
def delete_friend(fid: int, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        f = db.get(Friend, fid)
        if not f or f.user_id != uid:
            raise HTTPException(404, "Friend not found")
        involved = [e for e in db.scalars(select(SplitEntry).where(SplitEntry.user_id == uid))
                    if e.paid_by == fid or str(fid) in (e.shares or {})]
        if involved:
            raise HTTPException(409, "This friend has shared expenses. Delete or settle them first.")
        db.delete(f)
        db.commit()
    return {"deleted": fid}


class EntryIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    total: float = Field(gt=0, le=10_000_000)
    date: Day | None = None
    paid_by: int = 0  # 0 = me, otherwise a friend id
    with_friends: list[int] = Field(default_factory=list)  # people sharing (besides me)
    include_me: bool = True
    shares: dict[str, float] | None = None  # custom split {"me": x, "<friend id>": y}; overrides even split
    txn_id: int | None = None


def _check_people(db, uid, ids):
    mine = {f.id for f in db.scalars(select(Friend).where(Friend.user_id == uid))}
    bad = [i for i in ids if i not in mine]
    if bad:
        raise HTTPException(400, f"Unknown friend id(s): {bad}")


@app.post("/api/split/entries")
def add_entry(body: EntryIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        _check_people(db, uid, body.with_friends + ([body.paid_by] if body.paid_by else []))
        if body.shares:
            shares = {k: round(float(v), 2) for k, v in body.shares.items() if float(v) > 0}
            if abs(sum(shares.values()) - body.total) > 1:
                raise HTTPException(400, f"Shares add up to ₹{sum(shares.values()):,.2f}, not the total ₹{body.total:,.2f}")
            _check_people(db, uid, [int(k) for k in shares if k != "me"])
        else:
            people = (["me"] if body.include_me else []) + [str(i) for i in body.with_friends]
            if len(people) < 2:
                raise HTTPException(400, "Pick at least one friend to split with")
            shares = split_mod.even_shares(body.total, people)
        if body.paid_by == 0 and set(shares) == {"me"}:
            raise HTTPException(400, "Nobody else shares this expense")
        if body.paid_by and "me" not in shares:
            raise HTTPException(400, "A friend's payment only matters here if you share it")
        d = body.date
        if body.txn_id:
            t = db.get(Txn, body.txn_id)
            if not t or t.user_id != uid:
                raise HTTPException(404, "Transaction not found")
            if db.scalars(select(SplitEntry).where(SplitEntry.user_id == uid, SplitEntry.txn_id == body.txn_id)).first():
                raise HTTPException(409, "This transaction is already split")
            d = d or t.date
        e = SplitEntry(user_id=uid, kind="expense", title=body.title, date=d or date.today(), total=body.total,
                       paid_by=body.paid_by, shares=shares, txn_id=body.txn_id)
        db.add(e)
        db.commit()
        return {"id": e.id, "shares": shares}


class SettleIn(BaseModel):
    friend_id: int
    amount: float = Field(gt=0, le=10_000_000)
    direction: str = Field(pattern="^(they_paid|i_paid)$")
    date: Day | None = None
    txn_id: int | None = None
    note: str | None = Field(default=None, max_length=80)
    earlier: bool = False  # the payment squares up something from before FinLens tracked it (no open balance)


@app.post("/api/split/settle")
def settle(body: SettleIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        _check_people(db, uid, [body.friend_id])
        f = db.get(Friend, body.friend_id)
        d = body.date
        if body.txn_id:
            t = db.get(Txn, body.txn_id)
            if not t or t.user_id != uid:
                raise HTTPException(404, "Transaction not found")
            d = d or t.date
        if body.earlier and body.direction == "i_paid":
            # my share of something they paid earlier: it's my own spending, nothing left to settle
            e = SplitEntry(user_id=uid, kind="expense", title=body.note or f"My share (paid to {f.name})", date=d or date.today(),
                           total=body.amount, paid_by=0, shares={"me": body.amount}, txn_id=body.txn_id)
            db.add(e)
            db.commit()
            return {"id": e.id}
        if body.earlier:
            db.add(SplitEntry(user_id=uid, kind="expense", title=f"Earlier expense {f.name} shared", date=d or date.today(),
                              total=body.amount, paid_by=0, shares={str(body.friend_id): body.amount}))
        if body.direction == "they_paid":
            paid_by, shares = body.friend_id, {"me": body.amount}
            title = body.note or f"{f.name} paid you back"
        else:
            paid_by, shares = 0, {str(body.friend_id): body.amount}
            title = body.note or f"You paid {f.name}"
        e = SplitEntry(user_id=uid, kind="settlement", title=title, date=d or date.today(), total=body.amount,
                       paid_by=paid_by, shares=shares, txn_id=body.txn_id)
        db.add(e)
        db.commit()
        return {"id": e.id}


@app.delete("/api/split/entries/{eid}")
def delete_entry(eid: int, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        e = db.get(SplitEntry, eid)
        if not e or e.user_id != uid:
            raise HTTPException(404, "Entry not found")
        db.delete(e)
        db.commit()
    return {"deleted": eid}


class PaybackIn(BaseModel):
    debit_txn_id: int
    credit_txn_ids: list[int] = Field(min_length=1, max_length=10)
    names: dict[str, str] | None = None  # credit txn id -> friend name, when the statement doesn't say who


@app.post("/api/split/payback")
def confirm_payback(body: PaybackIn, uid: int = Depends(current_user)):
    """User confirmed 'yes, they were paying me back': record the bill as split (my share = bill - paybacks)
    and each incoming transfer as that friend settling up. Balances end at zero; only my share counts as
    spending and the paybacks don't count as income."""
    with SessionLocal() as db:
        d = db.get(Txn, body.debit_txn_id)
        cs = [db.get(Txn, i) for i in body.credit_txn_ids]
        if not d or d.user_id != uid or d.type != "debit" or any(c is None or c.user_id != uid or c.type != "credit" for c in cs):
            raise HTTPException(404, "Transaction not found")
        taken = {e.txn_id for e in db.scalars(select(SplitEntry).where(SplitEntry.user_id == uid))}
        if d.id in taken or any(c.id in taken for c in cs):
            raise HTTPException(409, "Already recorded")
        back = sum(c.amount for c in cs)
        if back >= d.amount * 1.02:
            raise HTTPException(400, "Paybacks are larger than the bill")
        friends = {f.name.lower(): f for f in db.scalars(select(Friend).where(Friend.user_id == uid))}
        per_friend: dict[int, list] = {}
        for i, c in enumerate(cs):
            nm = ((body.names or {}).get(str(c.id)) or split_mod.person_from(cipher.decrypt(c.description_enc).decode()) or f"Friend {i + 1}").strip()[:60]
            f = friends.get(nm.lower())
            if not f:
                f = Friend(user_id=uid, name=nm, emoji="🙂")
                db.add(f)
                db.flush()
                friends[nm.lower()] = f
            per_friend.setdefault(f.id, []).append(c)
        shares = {"me": round(d.amount - back, 2), **{str(fid): round(sum(c.amount for c in lst), 2) for fid, lst in per_friend.items()}}
        db.add(SplitEntry(user_id=uid, kind="expense", title=d.merchant or "Shared bill", date=d.date, total=d.amount,
                          paid_by=0, shares=shares, txn_id=d.id))
        for fid, lst in per_friend.items():
            nm = db.get(Friend, fid).name
            for c in lst:
                db.add(SplitEntry(user_id=uid, kind="settlement", title=f"{nm} paid you back", date=c.date, total=c.amount,
                                  paid_by=fid, shares={"me": c.amount}, txn_id=c.id))
        db.commit()
    return {"recorded": True, "my_share": shares["me"], "friends": len(per_friend)}


@app.post("/api/split/payback/{txn_id}/dismiss")
def dismiss_payback(txn_id: int, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        db.add(NudgeDismissal(user_id=uid, nudge_id=f"payback_{txn_id}"))
        db.commit()
    return {"dismissed": txn_id}


# ---------------- Credit builder ----------------
@app.get("/api/credit/builder")
def get_credit_builder(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        prof = _profile(db, uid)
        df, ctx = None, {}
        if _consents(db, uid).get("analytics") and db.query(Txn).filter(Txn.user_id == uid).count():
            df = load_df(db, uid)
            ctx = _context(db, uid, df)
        done = {c.step_id for c in db.scalars(select(CreditStep).where(CreditStep.user_id == uid))}
    out = credit_builder.plan(prof, df, ctx, done)
    lang = (prof.get("language") or "en")[:2]
    if lang != "en":
        _tr_fields(out["steps"], ("title", "why", "how"), lang, prof.get("language_style") or "formal")
        out["stage_text"] = sarvam.translate(out["stage_text"], lang, "en", prof.get("language_style") or "formal")
    return out


@app.post("/api/credit/builder/{step_id}/toggle")
def toggle_credit_step(step_id: str, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        row = db.scalars(select(CreditStep).where(CreditStep.user_id == uid, CreditStep.step_id == step_id[:40])).first()
        if row:
            db.delete(row)
            state = False
        else:
            db.add(CreditStep(user_id=uid, step_id=step_id[:40]))
            state = True
        db.commit()
    return {"step": step_id, "done": state}


@app.get("/api/money-health")
def money_health(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        if not db.query(Txn).filter(Txn.user_id == uid).count():
            return None
        df = load_df(db, uid)
        return _context(db, uid, df)["health"]


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


# ---------------- Risk Lab: Monte Carlo cash-flow simulation ----------------
@app.get("/api/risklab")
def risklab(days: int | None = Query(default=None, ge=7, le=120), cut_wants_pct: float = Query(default=0, ge=0, le=100),
            paths: int = Query(default=10000, ge=1000, le=50000), seed: int = 42, uid: int = Depends(current_user)):
    from . import risklab as RL
    from .guard_routes import _Bundle
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        if not db.query(Txn).filter(Txn.user_id == uid).count():
            raise HTTPException(404, "No transactions yet - upload a statement first")
        b = _Bundle(db, uid)
        buffer = db.get(Settings, uid).emergency_buffer
    out = RL.simulate(b.df, b.ctx, b.ctx.get("goals", []), buffer, days, cut_wants_pct, paths, seed, b.adf["nature"])
    if out.get("available") and cut_wants_pct == 0 and out["inputs"]["want_share"] > 0:
        alt = RL.simulate(b.df, b.ctx, [], buffer, days, 30, paths, seed, b.adf["nature"])
        out["what_if_cut_30"] = {"p_run_out": alt["p_run_out"], "var95": alt["var95"], "p50_end": alt["end_balance"]["p50"]}
    return out


# ---------------- Spend Guard (live alerts, need/want, limits...) ----------------
from .guard_routes import router as guard_router  # noqa: E402  (imported last: it uses the helpers above)

app.include_router(guard_router)
