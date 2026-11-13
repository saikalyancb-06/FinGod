"""Spend Guard API: live input (SMS paste, quick add, demo day), need/want alerts, daily limit,
caps, bill shield, fraud checks, wishlist, regret check, jar, insights and personal settings.
Included by finlens.main (imported at the end of main, so main's helpers already exist)."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from shared import config, sarvam

from . import analytics as A
from . import guard as GD
from . import nudges as nudges_mod
from . import room as room_mod
from . import sms as SMS
from . import split as split_mod
from .categorizer import categorize, is_essential
from .db import (Goal, GuardAlert, GuardSettings, JarEntry, NatureRule, Reflection, SessionLocal, Statement, Txn, WishItem, cipher)
from .main import (_context, _friends, _profile, _split_entries, _tr_fields, current_user, load_df, require_consent, _overrides)

router = APIRouter(prefix="/api/guard", tags=["spend-guard"])
LIVE_FILE = "Live: SMS, quick add & demo"


# ---------------- state ----------------
def _settings(db, uid, prof=None) -> dict:
    row = db.get(GuardSettings, uid)
    return GD.merge_settings(row.data if row else None, prof if prof is not None else _profile(db, uid))


def _save_settings(db, uid, data: dict):
    row = db.get(GuardSettings, uid)
    if not row:
        row = GuardSettings(user_id=uid, data={})
        db.add(row)
    row.data, row.updated_at = data, datetime.utcnow()


def _rules(db, uid) -> list[dict]:
    return [{"id": r.id, "scope": r.scope, "key": r.key, "nature": r.nature}
            for r in db.scalars(select(NatureRule).where(NatureRule.user_id == uid).order_by(NatureRule.id))]


def _reflections(db, uid) -> list[dict]:
    rows = db.execute(select(Reflection, Txn).join(Txn, Txn.id == Reflection.txn_id).where(Reflection.user_id == uid)).all()
    return [{"txn_id": r.txn_id, "verdict": r.verdict, "created_at": r.created_at, "merchant_key": t.merchant_key,
             "subcategory": t.subcategory, "merchant": t.merchant} for r, t in rows]


def _regrets(refl: list[dict]) -> dict:
    out: dict = {}
    for r in refl:
        for k in (r["merchant_key"], r["subcategory"]):
            if k:
                out.setdefault(k, {"regret": 0, "worth": 0})[r["verdict"]] += 1
    return out


def _base_daily(df, ctx) -> float:
    """Usual spare money per day: average monthly income minus bills and goal savings."""
    ms = A.monthly_summary(df)
    full = ms[:-1] if len(ms) > 1 and df["date"].max().day < 25 else ms
    if not full:
        return 0.0
    inc = sum(m["income"] for m in full) / len(full)
    sp = A.spend_df(df)
    bills = sp[sp["subcategory"].fillna("").isin(GD.BILL_SUBS) & sp["month"].isin([m["month"] for m in full])]
    bill_m = float(bills["amount"].sum()) / len(full)
    goal_m = sum(float(g.get("monthly_contribution") or 0) for g in ctx.get("goals", []) if not g["pace"].get("done"))
    return max(0.0, inc - bill_m - goal_m) / 30.44


class _Bundle:
    """Everything the guard needs for one user, loaded once per request."""

    def __init__(self, db, uid):
        self.uid = uid
        self.prof = _profile(db, uid)
        from .db import User
        self.name = (db.get(User, uid).name or "").split(" ")[0]
        self.prof_named = {**self.prof, "name": self.name}
        self.s = _settings(db, uid, self.prof)
        self.rules = _rules(db, uid)
        self.refl = _reflections(db, uid)
        self.regrets = _regrets(self.refl)
        self.has_txns = db.query(Txn).filter(Txn.user_id == uid).count() > 0
        self.df = load_df(db, uid) if self.has_txns else None
        self.ctx = _context(db, uid, self.df) if self.has_txns else {"goals": [], "recurring": []}
        self.room = room_mod.compute(self.df, self.ctx, self.prof, goals=self.ctx.get("goals")) if self.has_txns else None
        self.clf = GD.Classifier(self.df, self.s, self.rules, self.regrets, self.prof)
        self.adf = GD.annotate(self.df, self.clf) if self.has_txns else None
        self.base_daily = _base_daily(self.df, self.ctx) if self.has_txns else 0.0
        self.lang = (self.prof.get("language") or "en")[:2]
        self.style = self.prof.get("language_style") or "formal"


def _tr(items: list[dict], keys: tuple, b: _Bundle):
    return _tr_fields(items, keys, b.lang, b.style)


def _alert_dict(a: GuardAlert) -> dict:
    return {"id": a.id, "txn_id": a.txn_id, "kind": a.kind, "severity": a.severity, "title": a.title, "body": a.body,
            "data": a.data or {}, "status": a.status, "quiet": a.quiet, "created_at": a.created_at}


# ---------------- live ingest ----------------
def _live_statement(db, uid) -> Statement:
    st = db.scalar(select(Statement).where(Statement.user_id == uid, Statement.filename == LIVE_FILE))
    if not st:
        st = Statement(user_id=uid, filename=LIVE_FILE, encrypted_file=cipher.encrypt(b""), report={"source": "live"})
        db.add(st)
        db.flush()
    return st


def ingest(uid: int, recs: list[dict], source: str) -> list[dict]:
    """Book live transactions, then run every guard check on each. recs: date, time, type, amount,
    description, balance (optional), uid (dedupe key)."""
    new_ids = []
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        st = _live_statement(db, uid)
        existing = {t for t in db.scalars(select(Txn.uid).where(Txn.user_id == uid))}
        last = db.scalars(select(Txn).where(Txn.user_id == uid, Txn.balance.is_not(None)).order_by(Txn.date.desc(), Txn.row.desc())).first()
        bal = last.balance if last else None
        row_no = (db.scalar(select(func.max(Txn.row)).where(Txn.user_id == uid)) or 0) + 1
        ov = _overrides(db, uid)
        for r in recs:
            if r["uid"] in existing:
                new_ids.append(("duplicate", r))
                continue
            c = categorize(r["description"], r["type"], ov)
            if r.get("balance") is not None:
                bal = r["balance"]
            elif bal is not None:
                bal = round(bal - r["amount"] if r["type"] == "debit" else bal + r["amount"], 2)
            t = Txn(user_id=uid, statement_id=st.id, uid=r["uid"], date=r["date"], description_enc=cipher.encrypt(r["description"].encode()),
                    type=r["type"], amount=float(r["amount"]), balance=bal, status="posted", merchant=c["merchant"] or r["description"].title(),
                    merchant_key=c["merchant_key"], category=c["category"], subcategory=c["subcategory"], category_source=c["category_source"],
                    credit_kind=c["credit_kind"], is_essential=is_essential(c["category"], c["subcategory"]), excluded=False,
                    note=None, row=row_no, time=r.get("time"), source=source)
            row_no += 1
            db.add(t)
            db.flush()
            existing.add(r["uid"])
            new_ids.append(("new", t.id))
        db.commit()
    results = []
    for kind, x in new_ids:
        if kind == "duplicate":
            results.append({"status": "already recorded", "description": x["description"], "amount": x["amount"]})
            continue
        with SessionLocal() as db:
            b = _Bundle(db, uid)
            t = b.adf[b.adf["id"] == x].iloc[0].to_dict()
            t["date"] = pd.Timestamp(t["date"])
            prev = b.adf[b.adf["id"] != x]
            before = GD.daily_status(prev, b.s, b.room, b.ctx, t["date"].date(), b.base_daily) if len(prev) else None
            if before and t["type"] == "debit":  # the room before this payment was larger by its amount
                before = GD.daily_status(prev, b.s, {**(b.room or {}), "safe_to_spend": ((b.room or {}).get("safe_to_spend") or 0) + float(t["amount"])},
                                         b.ctx, t["date"].date(), b.base_daily)
            alerts, jar = GD.on_new(t, b.adf, b.ctx, b.room, b.prof_named, b.s, b.clf, b.ctx.get("goals", []), b.base_daily, before)
            _tr(alerts, ("title", "body"), b)
            saved = []
            for a in alerts:
                ga = GuardAlert(user_id=uid, txn_id=x, kind=a["kind"], severity=a["severity"], title=a["title"], body=a["body"],
                                data=a.get("data", {}), quiet=a.get("quiet", False))
                db.add(ga)
                db.flush()
                saved.append(_alert_dict(ga))
            goal_id = b.s.get("jar_goal_id") or ((GD.G.primary(b.ctx.get("goals", [])) or {}).get("id"))
            for j in jar:
                db.add(JarEntry(user_id=uid, txn_id=x, kind=j["kind"], amount=j["amount"], goal_id=goal_id))
            db.commit()
            c = b.clf.classify(t) or {}
            results.append({"status": "recorded", "txn_id": x, "merchant": t["merchant"], "amount": float(t["amount"]), "type": t["type"],
                            "date": str(t["date"].date()), "time": t.get("time"), "category": t["category"], "nature": c.get("nature"),
                            "reason": c.get("reason"), "alerts": saved, "jar": jar})
    return results


class SmsIn(BaseModel):
    text: str = Field(min_length=5, max_length=8000)


@router.post("/sms")
def paste_sms(body: SmsIn, uid: int = Depends(current_user)):
    msgs = SMS.split_messages(body.text)[:30]
    parsed = [SMS.parse(m) for m in msgs]
    out, recs = [], []
    for p in parsed:
        if p["kind"] == "txn":
            recs.append(p)
        elif p["kind"] == "scam":
            with SessionLocal() as db:
                ga = GuardAlert(user_id=uid, kind="scam", severity="alert", title="⚠️ Don't approve this request",
                                body=p["reason"] + (f" Amount asked: ₹{p['amount']:,.0f}." if p.get("amount") else ""),
                                data={"actions": ["dispute"], "text": p["text"][:300]})
                db.add(ga)
                db.commit()
                out.append({"status": "scam warning", "alerts": [_alert_dict(ga)]})
        else:
            out.append({"status": "skipped", "reason": p["reason"], "text": p["text"][:120]})
    if recs:
        out = ingest(uid, recs, "sms") + out
    return {"results": out, "parsed": len(parsed)}


class QuickIn(BaseModel):
    amount: float = Field(gt=0, le=10_000_000)
    what: str = Field(min_length=1, max_length=80)
    type: str = Field(default="debit", pattern="^(debit|credit)$")
    when: datetime | None = None


@router.post("/quick")
def quick_add(body: QuickIn, uid: int = Depends(current_user)):
    when = body.when or datetime.now()
    rec = {"uid": hashlib.sha1(f"quick|{uid}|{when.isoformat()}|{body.what}|{body.amount}".encode()).hexdigest()[:16],
           "date": when.date(), "time": when.strftime("%H:%M"), "type": body.type, "amount": body.amount,
           "description": f"{'PAID' if body.type == 'debit' else 'RECEIVED'} {body.what.upper()}", "balance": None}
    return {"results": ingest(uid, [rec], "manual")}


@router.post("/demo/next")
def demo_next(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        require_consent(db, uid, "analytics")
        s = _settings(db, uid)
        st = s.get("_demo") or {}
        last = db.scalars(select(Txn).where(Txn.user_id == uid).order_by(Txn.date.desc(), Txn.row.desc())).first()
        latest = last.date if last else date.today()
        if not st.get("day"):
            st = {"day": str(max(date.today(), latest + timedelta(days=1)) if last and latest >= date.today() else date.today()), "step": 0}
        bal_row = db.scalars(select(Txn).where(Txn.user_id == uid, Txn.balance.is_not(None)).order_by(Txn.date.desc(), Txn.row.desc())).first()
        demo = SMS.DemoDay(uid, date.fromisoformat(st["day"]), int(st["step"]), bal_row.balance if bal_row else None)
        nxt = demo.sms()
        if not nxt:
            return {"done": True, "step": st["step"], "total": SMS.DemoDay.length()}
        text, kind = nxt
        st["step"] = int(st["step"]) + 1
        s["_demo"] = st
        _save_settings(db, uid, s)
        db.commit()
    p = SMS.parse(text, date.fromisoformat(st["day"]))
    if p["kind"] == "txn":
        p["uid"] = hashlib.sha1(f"demo|{uid}|{st['day']}|{st['step']}".encode()).hexdigest()[:16]
        res = ingest(uid, [p], "demo")
    else:
        res = paste_sms(SmsIn(text=text), uid)["results"]
    return {"done": st["step"] >= SMS.DemoDay.length(), "step": st["step"], "total": SMS.DemoDay.length(), "sms": text, "kind": kind, "results": res}


@router.post("/demo/reset")
def demo_reset(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        ids = [t.id for t in db.scalars(select(Txn).where(Txn.user_id == uid, Txn.source == "demo"))]
        if ids:
            db.execute(delete(GuardAlert).where(GuardAlert.user_id == uid, GuardAlert.txn_id.in_(ids)))
            db.execute(delete(JarEntry).where(JarEntry.user_id == uid, JarEntry.txn_id.in_(ids)))
            db.execute(delete(Reflection).where(Reflection.user_id == uid, Reflection.txn_id.in_(ids)))
            db.execute(delete(Txn).where(Txn.id.in_(ids)))
        db.execute(delete(GuardAlert).where(GuardAlert.user_id == uid, GuardAlert.kind == "scam"))
        s = _settings(db, uid)
        s.pop("_demo", None)
        _save_settings(db, uid, s)
        db.commit()
    return {"removed": len(ids)}


# ---------------- alerts ----------------
@router.get("/alerts")
def list_alerts(since_id: int = 0, limit: int = 50, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        rows = db.scalars(select(GuardAlert).where(GuardAlert.user_id == uid, GuardAlert.id > since_id)
                          .order_by(GuardAlert.id.desc()).limit(min(limit, 200))).all()
        unread = db.query(GuardAlert).filter(GuardAlert.user_id == uid, GuardAlert.status == "new").count()
        return {"alerts": [_alert_dict(a) for a in rows], "unread": unread}


@router.post("/alerts/read-all")
def read_all(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        for a in db.scalars(select(GuardAlert).where(GuardAlert.user_id == uid, GuardAlert.status == "new")):
            a.status = "seen"
        db.commit()
    return {"ok": True}


class AlertAction(BaseModel):
    action: str = Field(pattern="^(seen|confirm|dispute|done|need|want|regret|worth)$")


@router.post("/alerts/{aid}")
def act_on_alert(aid: int, body: AlertAction, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        a = db.get(GuardAlert, aid)
        if not a or a.user_id != uid:
            raise HTTPException(404, "Alert not found")
        out = {"id": aid, "action": body.action}
        if body.action in ("need", "want") and a.txn_id:
            t = db.get(Txn, a.txn_id)
            _upsert_rule(db, uid, "merchant", t.merchant_key, "need" if body.action == "need" else "want")
            out["rule"] = f"{t.merchant} is now always a {body.action} for you"
        if body.action in ("regret", "worth") and a.txn_id:
            _reflect(db, uid, a.txn_id, body.action)
        if body.action == "dispute":
            out["steps"] = GD.DISPUTE_STEPS
        a.status = {"seen": "seen", "confirm": "confirmed", "dispute": "disputed"}.get(body.action, "done")
        db.commit()
        return out


# ---------------- rules & settings ----------------
def _upsert_rule(db, uid, scope, key, nature):
    r = db.scalar(select(NatureRule).where(NatureRule.user_id == uid, NatureRule.scope == scope, NatureRule.key == key))
    if r:
        r.nature = nature
    else:
        db.add(NatureRule(user_id=uid, scope=scope, key=key, nature=nature))


class RuleIn(BaseModel):
    scope: str = Field(pattern="^(merchant|subcategory|category)$")
    key: str = Field(min_length=1, max_length=120)
    nature: str = Field(pattern="^(need|flex|want|default)$")


@router.post("/rules")
def set_rule(body: RuleIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        if body.nature == "default":
            db.execute(delete(NatureRule).where(NatureRule.user_id == uid, NatureRule.scope == body.scope, NatureRule.key == body.key))
        else:
            _upsert_rule(db, uid, body.scope, body.key, body.nature)
        db.commit()
        return {"rules": _rules(db, uid)}


@router.get("/settings")
def get_settings(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        prof = _profile(db, uid)
        s = _settings(db, uid, prof)
        goals = [{"id": g.id, "name": g.name} for g in db.scalars(select(Goal).where(Goal.user_id == uid))]
        rules = _rules(db, uid)
    d = GD.defaults(prof)
    grid = {grp: [{"key": sub, "default": GD.SUB_NATURE.get(sub, "flex"),
                   "mine": next((r["nature"] for r in rules if r["scope"] == "subcategory" and r["key"] == sub), None)} for sub in subs]
            for grp, subs in GD.SUB_GROUPS.items()}
    return {"settings": {k: v for k, v in s.items() if not k.startswith("_")}, "defaults": d, "grid": grid, "goals": goals,
            "merchant_rules": [r for r in rules if r["scope"] == "merchant"],
            "festivals": {k: {"name": v[0], "emoji": v[1], "next": next((x for x in v[2] if x >= str(date.today())), None)} for k, v in GD.FESTIVALS.items()},
            "why_defaults": _why_defaults(prof)}


def _why_defaults(prof: dict) -> list[str]:
    out = []
    occ = prof.get("occupation")
    if occ in ("student", "both", "between_jobs"):
        out.append("You're a student, so the coach is gentle, costs are shown in days of your monthly money and the emergency-fund target is 1 month.")
    elif occ:
        out.append("You're working, so costs are shown in hours of your work and the emergency-fund target is 3 months.")
    if prof.get("orders_per_week"):
        out.append(f"You said you order food about {prof['orders_per_week']} times a week: more than that counts as a want.")
    if prof.get("food_budget_per_meal"):
        out.append(f"A meal above 2× your ₹{prof['food_budget_per_meal']:,.0f} per meal is treated as a treat (want).")
    if set(prof.get("priorities") or []) & {"save", "smarter", "purchase"}:
        out.append("You want to save / spend smarter, so sensitivity starts at strict.")
    return out


@router.put("/settings")
def put_settings(body: dict, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        prof = _profile(db, uid)
        cur = db.get(GuardSettings, uid)
        stored = dict(cur.data) if cur else {}
        merged = GD.merge_settings(stored, prof, body)
        # store only what differs from the profile-based defaults, so defaults keep following the profile
        d = GD.defaults(prof)
        keep = {k: v for k, v in merged.items() if k.startswith("_") or d.get(k) != v}
        _save_settings(db, uid, keep)
        db.commit()
    return get_settings(uid)


# ---------------- today ----------------
@router.get("/today")
def today(day: date | None = None, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        b = _Bundle(db, uid)
        if not b.has_txns:
            return {"empty": True}
        d = day or A.as_of(b.df)
        daily = GD.daily_status(b.adf, b.s, b.room, b.ctx, d, b.base_daily)
        exp = GD.explain_day(b.adf, d, b.s, b.name)
        jar = _jar_summary(db, uid)
        alerts = [_alert_dict(a) for a in db.scalars(select(GuardAlert).where(GuardAlert.user_id == uid).order_by(GuardAlert.id.desc()).limit(8))]
        wish_due = db.query(WishItem).filter(WishItem.user_id == uid, WishItem.status == "waiting", WishItem.check_at <= datetime.utcnow()).count()
        pend = _pending_reflections(b)
        bal = (b.ctx.get("balance") or {}).get("balance")
    out = {"daily": daily, "explain": exp, "shield": GD.shield(b.ctx, b.s, bal), "caps": GD.caps_status(b.adf, b.s),
           "jar": jar, "alerts": alerts, "wishlist_due": wish_due, "reflect": pend[:3], "wants_month": GD.wants_summary(b.adf),
           "settings": {k: b.s[k] for k in ("tone", "voice_summary", "summary_time", "notify_browser", "quiet_start", "quiet_end")},
           "demo": (b.s.get("_demo") or {}), "demo_total": SMS.DemoDay.length()}
    if b.lang != "en":
        out["explain"]["speech"] = sarvam.translate(out["explain"]["speech"], b.lang, "en", b.style)
    return out


def _pending_reflections(b: _Bundle) -> list[dict]:
    if b.adf is None:
        return []
    done = {r["txn_id"] for r in b.refl}
    ref = A.as_of(b.adf)
    w = b.adf[(b.adf["nature"] == "want") & (b.adf["date"] >= pd.Timestamp(ref - timedelta(days=14))) & (~b.adf["id"].isin(done))]
    w = w.sort_values(["date", "row"], ascending=False)  # newest first: easiest to remember
    return [{"txn_id": int(r["id"]), "merchant": r["merchant"], "amount": float(r["amount"]), "date": str(r["date"].date()),
             "reason": r["nature_reason"]} for _, r in w.iterrows()]


# ---------------- regret check ----------------
def _reflect(db, uid, txn_id: int, verdict: str):
    r = db.scalar(select(Reflection).where(Reflection.user_id == uid, Reflection.txn_id == txn_id))
    if r:
        r.verdict, r.created_at = verdict, datetime.utcnow()
    else:
        db.add(Reflection(user_id=uid, txn_id=txn_id, verdict=verdict))


class ReflectIn(BaseModel):
    txn_id: int
    verdict: str = Field(pattern="^(worth|regret)$")


@router.post("/reflect")
def reflect(body: ReflectIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        t = db.get(Txn, body.txn_id)
        if not t or t.user_id != uid:
            raise HTTPException(404, "Transaction not found")
        _reflect(db, uid, body.txn_id, body.verdict)
        db.commit()
        refl = _reflections(db, uid)
    r = _regrets(refl).get(t.merchant_key, {})
    note = (f"Noted. You've regretted {r['regret']} buy{'s' if r['regret'] != 1 else ''} at {t.merchant}; "
            "FinLens will treat it as a want and warn you a little louder." if body.verdict == "regret" and r.get("regret", 0) >= 2 else
            f"Noted. After 3 'worth it' answers for {t.merchant}, FinLens stops nagging about it." if body.verdict == "worth" else "Noted.")
    return {"ok": True, "learned": r, "note": note}


# ---------------- pre-purchase check ----------------
class CheckIn(BaseModel):
    item: str = Field(min_length=1, max_length=120)
    price: float = Field(gt=0, le=10_000_000)


@router.post("/check")
def before_you_pay(body: CheckIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        b = _Bundle(db, uid)
    c = categorize(body.item, "debit")
    fake = {"type": "debit", "amount": body.price, "merchant_key": c["merchant_key"], "merchant": c["merchant"] or body.item,
            "category": c["category"], "subcategory": c["subcategory"], "date": pd.Timestamp(A.as_of(b.df) if b.has_txns else date.today()),
            "row": 10 ** 9, "time": datetime.now().strftime("%H:%M")}
    cl = b.clf.classify(fake) or {"nature": "flex", "reason": "could be either"}
    daily = GD.daily_status(b.adf, b.s, b.room, b.ctx, None, b.base_daily) if b.has_txns else {}
    lens = GD.lenses(body.price, b.s, b.prof, b.room, b.ctx.get("goals", []), daily)
    bal = (b.ctx.get("balance") or {}).get("balance")
    sh = GD.shield(b.ctx, b.s, (bal - body.price) if bal is not None else None)
    reasons, verdict = [], "go"
    if cl["nature"] == "want":
        verdict = "wait" if body.price >= b.s["cooloff_amount"] else "ok_treat"
        reasons.append(f"It's a want: {cl['reason']}.")
    elif cl["nature"] == "need":
        reasons.append(f"Looks like a need: {cl['reason']}.")
    else:
        reasons.append(f"Could be either: {cl['reason']}.")
        if body.price >= b.s["cooloff_amount"]:
            verdict = "wait"
    if daily.get("left") is not None and body.price > daily["left"]:
        reasons.append(f"It's more than what's left for today ({GD._inr(daily['left'])})." if daily["left"] > 0
                       else "You've already used today's comfortable amount.")
        verdict = "wait" if verdict != "go" or cl["nature"] != "need" else verdict
    if sh.get("at_risk"):
        reasons.append("After this, your upcoming bills wouldn't be covered.")
        verdict = "stop"
    if bal is not None and body.price > bal:
        reasons.append(f"Your balance is only {GD._inr(bal)}.")
        verdict = "stop"
    title = {"go": "✅ Fine to go ahead", "ok_treat": "🙂 A small treat, fine if it's planned", "wait": "⏳ Sleep on it",
             "stop": "🛑 Not right now"}[verdict]
    out = {"verdict": verdict, "title": title, "nature": cl["nature"], "reasons": reasons, "lenses": lens,
           "cooloff_hours": b.s["cooloff_hours"], "category": c["category"], "subcategory": c["subcategory"]}
    if b.lang != "en":
        out["reasons"] = [sarvam.translate(x, b.lang, "en", b.style) for x in reasons]
    return out


# ---------------- wishlist ----------------
def _wish_dict(w: WishItem, b: _Bundle | None = None) -> dict:
    now = datetime.utcnow()
    d = {"id": w.id, "name": w.name, "price": w.price, "link": w.link, "category": w.category, "added_at": w.added_at,
         "check_at": w.check_at, "status": w.status, "best_price": w.best_price, "best_link": w.best_link,
         "price_checked_at": w.price_checked_at, "due": w.status == "waiting" and w.check_at <= now,
         "hours_left": max(0, round((w.check_at - now).total_seconds() / 3600, 1)) if w.status == "waiting" else 0}
    if b:
        d["lenses"] = GD.lenses(w.best_price or w.price, b.s, b.prof, b.room, b.ctx.get("goals", []))
    return d


class WishIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    price: float = Field(gt=0, le=10_000_000)
    link: str | None = Field(default=None, max_length=500)


@router.get("/wishlist")
def wishlist(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        b = _Bundle(db, uid)
        rows = db.scalars(select(WishItem).where(WishItem.user_id == uid).order_by(WishItem.status, WishItem.check_at)).all()
        items = [_wish_dict(w, b) for w in rows]
    saved = sum(i["price"] for i in items if i["status"] == "skipped")
    return {"items": items, "saved_by_skipping": round(saved, 2), "cooloff_hours": b.s["cooloff_hours"]}


@router.post("/wishlist")
def add_wish(body: WishIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        s = _settings(db, uid)
        c = categorize(body.name, "debit")
        w = WishItem(user_id=uid, name=body.name, price=body.price, link=body.link, category=c["subcategory"] or c["category"],
                     check_at=datetime.utcnow() + timedelta(hours=float(s["cooloff_hours"])))
        db.add(w)
        db.commit()
        return _wish_dict(w)


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(bought|skipped|waiting)$")


@router.post("/wishlist/{wid}/decide")
def decide_wish(wid: int, body: DecideIn, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        w = db.get(WishItem, wid)
        if not w or w.user_id != uid:
            raise HTTPException(404, "Item not found")
        w.status, w.decided_at = body.decision, datetime.utcnow()
        db.commit()
        return _wish_dict(w)


@router.delete("/wishlist/{wid}")
def del_wish(wid: int, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        w = db.get(WishItem, wid)
        if not w or w.user_id != uid:
            raise HTTPException(404, "Item not found")
        db.delete(w)
        db.commit()
    return {"deleted": wid}


@router.post("/wishlist/{wid}/check-price")
def check_price(wid: int, uid: int = Depends(current_user)):
    from . import search as S
    with SessionLocal() as db:
        w = db.get(WishItem, wid)
        if not w or w.user_id != uid:
            raise HTTPException(404, "Item not found")
        name = w.name
    res = S.search("shopping", name, config.DEFAULT_LOCATION)
    priced = [r for r in res["results"] if r.get("price")]
    best = min(priced, key=lambda r: r["price"]) if priced else None
    with SessionLocal() as db:
        w = db.get(WishItem, wid)
        if best:
            w.best_price, w.best_link = float(best["price"]), best.get("link")
        w.price_checked_at = datetime.utcnow()
        db.commit()
        out = _wish_dict(w)
    out["mode"], out["notice"], out["best_title"] = res["mode"], res.get("notice"), (best or {}).get("title")
    return out


# ---------------- jar (want tax + round-ups) ----------------
def _jar_summary(db, uid) -> dict:
    rows = db.scalars(select(JarEntry).where(JarEntry.user_id == uid)).all()
    pending = [r for r in rows if r.status == "pending"]
    return {"pending": round(sum(r.amount for r in pending), 2), "pending_count": len(pending),
            "moved": round(sum(r.amount for r in rows if r.status == "moved"), 2),
            "by_kind": {k: round(sum(r.amount for r in pending if r.kind == k), 2) for k in ("want_tax", "roundup")}}


class JarMoveIn(BaseModel):
    goal_id: int | None = None


@router.post("/jar/move")
def jar_move(body: JarMoveIn, uid: int = Depends(current_user)):
    """The user moved the pending amount to savings themselves; FinLens records it against a goal."""
    with SessionLocal() as db:
        rows = db.scalars(select(JarEntry).where(JarEntry.user_id == uid, JarEntry.status == "pending")).all()
        total = round(sum(r.amount for r in rows), 2)
        if not rows:
            raise HTTPException(400, "Nothing waiting in the jar")
        gid = body.goal_id or rows[-1].goal_id
        g = db.get(Goal, gid) if gid else None
        if g and g.user_id == uid:
            g.saved = round(g.saved + total, 2)
        for r in rows:
            r.status, r.goal_id = "moved", (g.id if g else None)
        db.commit()
        return {"moved": total, "goal": g.name if g else None, "jar": _jar_summary(db, uid)}


@router.post("/jar/skip")
def jar_skip(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        for r in db.scalars(select(JarEntry).where(JarEntry.user_id == uid, JarEntry.status == "pending")):
            r.status = "skipped"
        db.commit()
        return _jar_summary(db, uid)


# ---------------- insights ----------------
@router.get("/insights")
def insights(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        b = _Bundle(db, uid)
        if not b.has_txns:
            return {"empty": True}
        entries = _split_entries(db, uid)
        friends = _friends(db, uid)
        jar = _jar_summary(db, uid)
        skipped = db.query(WishItem).filter(WishItem.user_id == uid, WishItem.status == "skipped").count()
    goals = b.ctx.get("goals", [])
    ref = A.as_of(b.df)
    out = {
        "leaks": GD.leaks(b.adf, b.s, goals), "charges": GD.charges(b.df), "habits": GD.habits(b.adf),
        "subscriptions": GD.subscriptions(b.df, b.ctx, b.s), "emergency": GD.emergency(b.df, b.ctx, b.s, b.prof),
        "events": GD.events(b.df, b.s, b.prof, ref), "tips": GD.tips(b.df),
        "streaks": GD.streaks(b.adf, b.base_daily, b.refl, jar["moved"], skipped),
        "owed": GD.owed(split_mod.balances(entries, friends), entries, b.s, ref, b.name),
        "wants_month": GD.wants_summary(b.adf), "base_daily": round(b.base_daily, 2),
        "learned": [{"key": k, **v} for k, v in b.regrets.items()],
    }
    if b.lang != "en":
        _tr(out["habits"], ("title", "body"), b)
        _tr(out["tips"], ("title", "body"), b)
    return out


class SubDecision(BaseModel):
    decision: str = Field(pattern="^(keep|cancel|)$")


@router.post("/subscriptions/{merchant_key}")
def sub_decision(merchant_key: str, body: SubDecision, uid: int = Depends(current_user)):
    with SessionLocal() as db:
        cur = db.get(GuardSettings, uid)
        data = dict(cur.data) if cur else {}
        rv = dict(data.get("subs_review") or {})
        if body.decision:
            rv[merchant_key] = body.decision
        else:
            rv.pop(merchant_key, None)
        data["subs_review"] = rv
        _save_settings(db, uid, data)
        db.commit()
    return {"ok": True}


@router.get("/buddy")
def buddy(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        b = _Bundle(db, uid)
        if not b.has_txns:
            raise HTTPException(404, "No transactions yet")
    rep = nudges_mod.weekly_report(b.df, b.ctx)
    if rep.get("no_data"):
        raise HTTPException(404, rep["message"])
    start = pd.Timestamp(rep["period"]["start"])
    w = b.adf[(b.adf["nature"] == "want") & (b.adf["date"] >= start)]
    days = {d.date() for d in pd.date_range(start, pd.Timestamp(rep["period"]["end"]))}
    free = len(days - set(w["date"].dt.date))
    return GD.buddy_text(rep, b.s, b.name, float(w["amount"].sum()), {"want_free_week": free})


# ---------------- transactions with need/want ----------------
@router.get("/natures")
def natures(uid: int = Depends(current_user)):
    with SessionLocal() as db:
        b = _Bundle(db, uid)
    if not b.has_txns:
        return {}
    return {str(int(r["id"])): {"nature": r["nature"], "reason": r["nature_reason"]} for _, r in b.adf[b.adf["nature"].notna()].iterrows()}


def guard_nudges(db, uid) -> list[dict]:
    """Spend Guard items for the Home 'For you' feed (same shape as nudges.compute)."""
    b = _Bundle(db, uid)
    if not b.has_txns:
        return []
    out = []
    ref = A.as_of(b.df)
    for p in _pending_reflections(b)[:1]:
        out.append({"id": f"reflect_{p['txn_id']}", "severity": "info", "icon": "🪞", "title": f"Was {p['merchant']} worth ₹{p['amount']:,.0f}?",
                    "body": "One tap teaches FinLens what you really value.", "action": {"label": "Answer", "link": "/finlens/guard"}})
    due = db.query(WishItem).filter(WishItem.user_id == uid, WishItem.status == "waiting", WishItem.check_at <= datetime.utcnow()).count()
    if due:
        out.append({"id": f"wish_due_{ref}", "severity": "info", "icon": "🧘", "title": f"{due} wishlist item{'s' if due != 1 else ''} ready to decide",
                    "body": "The cool-off is over. Still want it?", "action": {"label": "Decide", "link": "/finlens/guard?tab=wishlist"}})
    ch = GD.charges(b.df)
    if ch["total"] >= 50:
        out.append({"id": f"charges_{ref:%Y-%m}", "severity": "warn", "icon": "🧾", "title": f"₹{ch['total']:,.0f} in bank fees in {ch['days']} days",
                    "body": ch["items"][0]["tip"] if ch["items"] else "", "action": {"label": "See fees", "link": "/finlens/guard?tab=insights"}})
    lk = GD.leaks(b.adf, b.s, b.ctx.get("goals", []))
    if lk:
        x = lk[0]
        out.append({"id": f"leak_{x['merchant']}_{ref:%Y-%m}", "severity": "info", "icon": "💧", "title": f"Small leak: {x['merchant']} ≈ ₹{x['monthly']:,.0f}/month",
                    "body": f"{x['count']} small payments in 2 months. Halving it saves about ₹{x['half_saves']:,.0f} a year.",
                    "action": {"label": "See leaks", "link": "/finlens/guard?tab=insights"}})
    em = GD.emergency(b.df, b.ctx, b.s, b.prof)
    if em.get("status") == "low":
        out.append({"id": f"emergency_{ref:%Y-%m}", "severity": "warn", "icon": "🛟", "title": f"Emergency fund: {em['months_covered']} of {em['target_months']} months",
                    "body": em["plan"] or "", "action": {"label": "Plan it", "link": "/finlens/guard?tab=insights"}})
    for e in GD.events(b.df, b.s, b.prof, ref)["items"]:
        if 0 <= e["days_left"] <= 45:
            out.append({"id": f"event_{e['id']}_{e['date']}", "severity": "info", "icon": e.get("emoji") or "📌",
                        "title": f"{e['name']} in {e['days_left']} days",
                        "body": f"Budget {GD._inr(e['amount'])}: set aside {GD._inr(e['monthly'])} a month (you've planned it in Spend Guard).",
                        "action": {"label": "Plan", "link": "/finlens/guard?tab=plan"}})
    for o in GD.owed(split_mod.balances(_split_entries(db, uid), _friends(db, uid)), _split_entries(db, uid), b.s, ref, b.name)[:2]:
        out.append({"id": f"owed_{o['friend_id']}_{ref:%Y-%W}", "severity": "info", "icon": "🤝", "title": f"{o['name']} owes you ₹{o['amount']:,.0f} ({o['days']} days)",
                    "body": "Send a friendly reminder?", "action": {"label": "Remind", "link": "/finlens/split"}})
    return out
