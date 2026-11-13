"""Money Mule Detection API (B2B). Run: python -m uvicorn mule.main:app --port 8002

Roles: analyst (review, notes, status up to 'escalated'), lead (final dispositions,
reveal restricted identifiers, export). Every data access is audit-logged.
The system never freezes accounts or labels anyone a criminal; humans decide.
"""
from __future__ import annotations

import asyncio
import io
import json
import time
from datetime import datetime

import networkx as nx
import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select

from shared import config, llm
from shared.audit import setup_logging
from shared.security import hash_password, make_token, read_token, verify_password

from . import live
from .db import Account, Alert, AuditLog, CaseNote, Dataset, Investigator, SessionLocal, Transaction, cipher, init_db
from .detection import analyze, load_model
from .features import SchemaError, normalize_transactions

log = setup_logging("mule")
app = FastAPI(title="Money Mule Detection API", version="1.0",
              description="Institutional investigation support (prototype). Alerts are reasons to review, not findings of wrongdoing.")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
init_db()

STATUSES = {
    "open": "New alert, not yet reviewed",
    "in_review": "An investigator is reviewing the evidence",
    "escalated": "Referred to a lead investigator",
    "closed_no_concern": "Reviewed - activity explained / no further action",
    "closed_reported": "Reviewed - referred for reporting under the institution's process",
}
LEAD_ONLY = {"closed_no_concern", "closed_reported"}


def _seed():
    import os
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Investigator)):
            return
        db.add(Investigator(username="analyst", name="Demo Analyst", role="analyst",
                            password_hash=hash_password(os.getenv("MULE_ANALYST_PASSWORD", "analyst-demo-2026"))))
        db.add(Investigator(username="lead", name="Demo Lead Investigator", role="lead",
                            password_hash=hash_password(os.getenv("MULE_LEAD_PASSWORD", "lead-demo-2026"))))
        db.commit()


_seed()


class Who(BaseModel):
    username: str
    role: str
    name: str


def current(authorization: str = Header(default="")) -> Who:
    body = read_token(authorization.removeprefix("Bearer ").strip(), config.MULE_SECRET)
    if not body or body.get("aud") != "institution":
        raise HTTPException(401, "Not signed in to the institutional portal")
    return Who(username=body["sub"], role=body["role"], name=body.get("name", body["sub"]))


def audit(db, who: Who, action: str, target: str, **detail):
    db.add(AuditLog(actor=who.username, action=action, target=target, detail=detail))


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginIn):
    with SessionLocal() as db:
        u = db.scalar(select(Investigator).where(Investigator.username == body.username))
        if not u or not verify_password(body.password, u.password_hash):
            db.add(AuditLog(actor=body.username[:80], action="login_failed", target="auth", detail={}))
            db.commit()
            raise HTTPException(401, "Invalid credentials")
        db.add(AuditLog(actor=u.username, action="login", target="auth", detail={}))
        db.commit()
        return {"token": make_token({"sub": u.username, "aud": "institution", "role": u.role, "name": u.name}, config.MULE_SECRET),
                "role": u.role, "name": u.name}


@app.get("/api/me")
def me(who: Who = Depends(current)):
    return who


# ---------------- ingestion & analysis ----------------
def _persist_analysis(db, ds: Dataset, df_real: pd.DataFrame, alias: dict[str, str]):
    """(Re)analyse the dataset's full transaction set and upsert accounts/alerts.
    Existing alerts keep their status, notes, assignment and review history."""
    t0 = time.perf_counter()
    for a in sorted(set(df_real["src"]) | set(df_real["dst"])):
        if a not in alias:
            alias[a] = f"ACC-{len(alias) + 1:05d}"
    df_alias = df_real.assign(src=df_real["src"].map(alias), dst=df_real["dst"].map(alias))
    out = analyze(df_alias)
    feats = out["features"]
    existing = {a.alias: a for a in db.scalars(select(Account).where(Account.dataset_id == ds.id))}
    for real, al in alias.items():
        if al not in feats.index:
            continue
        row = existing.get(al) or Account(dataset_id=ds.id, alias=al, real_enc=cipher.encrypt(real))
        row.features = {k: float(v) for k, v in feats.loc[al].items()}
        row.evidence = json.loads(json.dumps(out["evidence"][al], default=str))
        row.model_score = float(out["scores"][al])
        if al not in existing:
            db.add(row)
    old = {a.account: a for a in db.scalars(select(Alert).where(Alert.dataset_id == ds.id))}
    for a in out["alerts"]:
        if a["account"] in old:
            x = old.pop(a["account"])
            for k, v in a.items():
                setattr(x, k, v)
            x.updated_at = datetime.utcnow()
        else:
            db.add(Alert(dataset_id=ds.id, **a))
    for x in old.values():  # no longer triggered: drop only untouched alerts
        if x.status == "open" and not db.scalar(select(func.count()).select_from(CaseNote).where(CaseNote.alert_id == x.id)):
            db.delete(x)
    al_ = out["alerts"]
    prev_eval = (ds.summary or {}).get("evaluation")
    ds.rings = out["rings"]
    ds.summary = {
        "accounts": int(len(feats)), "transactions": int(len(df_real)), "alerts": len(al_),
        "by_priority": pd.Series([a["priority"] for a in al_]).value_counts().to_dict() if al_ else {},
        "model_version": out["model_version"], "threshold": out["threshold"],
        "rule_hits": pd.Series([f for a in al_ for f in a["rule_families"]]).value_counts().to_dict() if al_ else {},
        "fraud_rings": len(out["rings"]), "suppressed_merchant_alerts": out["suppressed_merchant_alerts"],
        "processing_time_seconds": round(time.perf_counter() - t0, 3),
        "model_applicable": out["model_applicable"], "model_note": out["model_note"],
    }
    if prev_eval:
        changed = prev_eval.get("alerts_at_evaluation") != len(al_)
        ds.summary["evaluation"] = {**prev_eval, "stale": bool(changed or prev_eval.get("stale"))}
    return df_alias


@app.post("/api/datasets")
async def upload(file: UploadFile = File(...), name: str = Form(default=""), who: Who = Depends(current)):
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "File too large (50 MB limit)")
    try:
        df, report = normalize_transactions(content)
    except SchemaError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"Could not read file: {exc}")
    if len(df) < 10:
        raise HTTPException(422, "Too few valid transactions to analyse")
    with SessionLocal() as db:
        ds = Dataset(name=name or file.filename or "dataset", uploaded_by=who.username, encrypted_file=cipher.encrypt(content),
                     report=json.loads(json.dumps(report, default=str)), status="ready", summary={}, rings=[])
        db.add(ds)
        db.flush()
        df_alias = _persist_analysis(db, ds, df, {})
        db.bulk_save_objects([Transaction(dataset_id=ds.id, txn_id=r.txn_id, ts=r.timestamp.to_pydatetime(), src=r.src, dst=r.dst,
                                          amount=float(r.amount), channel=str(r.channel)[:20]) for r in df_alias.itertuples(index=False)])
        audit(db, who, "dataset_upload", f"dataset:{ds.id}", rows=int(len(df)), filename=file.filename)
        db.commit()
        return {"dataset_id": ds.id, "report": ds.report, "summary": ds.summary}


# ---------------- live ingest (mr-mule / MuleGuard live CSV feed) ----------------
class LiveIn(BaseModel):
    name: str = Field(default="Live feed", max_length=120)


@app.post("/api/datasets/live")
def create_live(body: LiveIn, who: Who = Depends(current)):
    with SessionLocal() as db:
        ds = Dataset(name=body.name, uploaded_by=who.username, encrypted_file=cipher.encrypt(b""), live=True, status="live",
                     report={"rows_in": 0, "rows_out": 0, "rows_dropped_invalid": 0, "rows_dropped_duplicate": 0, "source": "live feed"},
                     summary={"accounts": 0, "transactions": 0, "alerts": 0}, rings=[])
        db.add(ds)
        audit(db, who, "live_dataset_create", "dataset:new")
        db.commit()
        return {"dataset_id": ds.id}


class AppendIn(BaseModel):
    rows: list[str] = Field(min_length=1, max_length=5000)  # CSV lines: transaction_id,sender_id,receiver_id,amount,timestamp


def reanalyse(ds_id: int) -> dict:
    """Re-run detection on a stored dataset (e.g. after a detection change). Analyst work is kept."""
    with SessionLocal() as db:
        ds = db.get(Dataset, ds_id) or _404("Dataset")
        accts = db.scalars(select(Account).where(Account.dataset_id == ds_id)).all()
        alias = {cipher.decrypt(a.real_enc).decode(): a.alias for a in accts}
        rev = {v: k for k, v in alias.items()}
        rows = db.execute(select(Transaction.txn_id, Transaction.ts, Transaction.src, Transaction.dst, Transaction.amount, Transaction.channel)
                          .where(Transaction.dataset_id == ds_id)).all()
        if len(rows) < 3:
            return {"dataset_id": ds_id, "skipped": True}
        df = pd.DataFrame(rows, columns=["txn_id", "timestamp", "src", "dst", "amount", "channel"])
        df["src"], df["dst"] = df["src"].map(rev), df["dst"].map(rev)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.dropna(subset=["src", "dst"]).sort_values("timestamp").reset_index(drop=True)
        _persist_analysis(db, ds, df, alias)
        db.commit()
        return {"dataset_id": ds_id, "alerts": ds.summary.get("alerts"), "model_applicable": ds.summary.get("model_applicable")}


@app.post("/api/datasets/{ds_id}/reanalyse")
def reanalyse_endpoint(ds_id: int, who: Who = Depends(current)):
    with SessionLocal() as db:
        audit(db, who, "reanalyse", f"dataset:{ds_id}")
        db.commit()
    return reanalyse(ds_id)


def append_lines(ds_id: int, lines: list[str], actor: str) -> dict:
    """Append CSV rows (transaction_id,sender_id,receiver_id,amount,timestamp) to a dataset, re-analyse it,
    and report which alerts are new or escalated. Shared by the REST endpoint and the live simulator."""
    header = "transaction_id,sender_id,receiver_id,amount,timestamp"
    lines = [r.strip() for r in lines if r and r.strip() and not r.strip().lower().startswith("transaction_id")]
    if not lines:
        raise ValueError("No rows")
    new, rep = normalize_transactions((header + "\n" + "\n".join(lines)).encode())
    order = {"low": 1, "medium": 2, "high": 3}
    with SessionLocal() as db:
        ds = db.get(Dataset, ds_id) or _404("Dataset")
        before = {a.account: a.priority for a in db.scalars(select(Alert).where(Alert.dataset_id == ds_id))}
        accts = db.scalars(select(Account).where(Account.dataset_id == ds_id)).all()
        alias = {cipher.decrypt(a.real_enc).decode(): a.alias for a in accts}
        alias.update(ds.report.get("pending_alias", {}))
        rev = {v: k for k, v in alias.items()}
        old = db.execute(select(Transaction.txn_id, Transaction.ts, Transaction.src, Transaction.dst, Transaction.amount, Transaction.channel)
                         .where(Transaction.dataset_id == ds_id)).all()
        seen = {t[0] for t in old}
        new = new[~new["txn_id"].isin(seen)]
        old_df = pd.DataFrame(old, columns=["txn_id", "timestamp", "src", "dst", "amount", "channel"])
        old_df["src"], old_df["dst"] = old_df["src"].map(rev), old_df["dst"].map(rev)
        parts = [x for x in (old_df, new[["txn_id", "timestamp", "src", "dst", "amount", "channel"]]) if len(x)]
        full = pd.concat(parts, ignore_index=True) if parts else new.iloc[0:0]
        full["timestamp"] = pd.to_datetime(full["timestamp"])
        full = full.sort_values("timestamp").reset_index(drop=True)
        for a in sorted(set(new["src"]) | set(new["dst"])):
            alias.setdefault(a, f"ACC-{len(alias) + 1:05d}")
        if len(full) >= 3:
            _persist_analysis(db, ds, full, alias)
        db.bulk_save_objects([Transaction(dataset_id=ds_id, txn_id=r.txn_id, ts=r.timestamp.to_pydatetime(), src=alias[r.src], dst=alias[r.dst],
                                          amount=float(r.amount), channel=str(r.channel)[:20]) for r in new.itertuples(index=False)])
        rpt = dict(ds.report)
        rpt["rows_in"] = rpt.get("rows_in", 0) + rep["rows_in"]
        rpt["rows_out"] = rpt.get("rows_out", 0) + len(new)
        rpt["rows_dropped_invalid"] = rpt.get("rows_dropped_invalid", 0) + rep["rows_dropped_invalid"]
        if len(full) < 3:  # not analysed yet: remember aliases so they stay stable
            rpt["pending_alias"] = {k: v for k, v in alias.items()}
        else:
            rpt.pop("pending_alias", None)
        ds.report = rpt
        db.add(AuditLog(actor=actor, action="live_append", target=f"dataset:{ds_id}", detail={"rows": int(len(new))}))
        db.commit()
        fresh = []
        for a in db.scalars(select(Alert).where(Alert.dataset_id == ds_id)):
            if a.account not in before or order.get(a.priority, 0) > order.get(before[a.account], 0):
                fresh.append({"id": a.id, "account": a.account, "priority": a.priority, "model_score": round(a.model_score, 3),
                              "rule_families": a.rule_families, "headline": (a.explanation or [""])[0],
                              "escalated": a.account in before})
        fresh.sort(key=lambda x: -order.get(x["priority"], 0))
        rows_out = [{"txn_id": r.txn_id, "ts": str(r.timestamp), "src": alias[r.src], "dst": alias[r.dst], "amount": round(float(r.amount), 2)}
                    for r in new.tail(12).itertuples(index=False)]
        return {"added": int(len(new)), "summary": ds.summary, "new_alerts": fresh, "rows": rows_out}


@app.post("/api/datasets/{ds_id}/append")
def append_rows(ds_id: int, body: AppendIn, who: Who = Depends(current)):
    try:
        out = append_lines(ds_id, body.rows, who.username)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    live.hub.broadcast(ds_id, {"type": "batch", **out})
    return out


@app.get("/api/datasets/{ds_id}/rings")
def rings(ds_id: int, who: Who = Depends(current)):
    with SessionLocal() as db:
        ds = db.get(Dataset, ds_id) or _404("Dataset")
        audit(db, who, "view_rings", f"dataset:{ds_id}")
        db.commit()
        return ds.rings or []


@app.get("/api/datasets/{ds_id}/export.json")
def export_json(ds_id: int, who: Who = Depends(current)):
    """MuleGuard-compatible export: suspicious_accounts, fraud_rings, summary (aliased identifiers)."""
    with SessionLocal() as db:
        ds = db.get(Dataset, ds_id) or _404("Dataset")
        al = db.scalars(select(Alert).where(Alert.dataset_id == ds_id)).all()
        audit(db, who, "export_json", f"dataset:{ds_id}")
        db.commit()
    order = {"high": 0, "medium": 1, "low": 2}
    sus = sorted(al, key=lambda a: (order.get(a.priority, 3), -a.model_score))
    return {
        "suspicious_accounts": [{"account_id": a.account,
                                 "suspicion_score": round(max(a.pattern_score or 0, a.model_score * 100 if a.model_reliable is not False else 0), 1),
                                 "pattern_score": a.pattern_score, "model_score": a.model_score if a.model_reliable is not False else None,
                                 "model_used": a.model_reliable is not False, "priority": a.priority, "priority_reason": a.priority_reason,
                                 "detected_patterns": a.rule_families, "ring_id": (a.ring_ids or [None])[0], "review_status": a.status} for a in sus],
        "fraud_rings": [{"ring_id": r["ring_id"], "member_accounts": r["member_accounts"], "pattern_type": r["pattern_type"], "risk_score": r["risk_score"]}
                        for r in (ds.rings or [])],
        "summary": {"total_accounts_analyzed": ds.summary.get("accounts", 0), "suspicious_accounts_flagged": len(al),
                    "fraud_rings_detected": len(ds.rings or []), "processing_time_seconds": ds.summary.get("processing_time_seconds")},
        "notice": "Identifiers are dataset aliases. Alerts are reasons for human review, not findings of wrongdoing.",
    }


@app.get("/api/datasets")
def datasets(who: Who = Depends(current)):
    with SessionLocal() as db:
        return [{"id": d.id, "name": d.name, "uploaded_by": d.uploaded_by, "uploaded_at": d.uploaded_at, "summary": d.summary, "report": d.report, "live": d.live}
                for d in db.scalars(select(Dataset).order_by(Dataset.uploaded_at.desc()))]


@app.delete("/api/datasets/{ds_id}")
def delete_dataset(ds_id: int, who: Who = Depends(current)):
    if who.role != "lead":
        raise HTTPException(403, "Only a lead investigator can delete datasets")
    with SessionLocal() as db:
        for M in (Transaction, Account):
            db.execute(delete(M).where(M.dataset_id == ds_id))
        ids = [a.id for a in db.scalars(select(Alert).where(Alert.dataset_id == ds_id))]
        if ids:
            db.execute(delete(CaseNote).where(CaseNote.alert_id.in_(ids)))
        db.execute(delete(Alert).where(Alert.dataset_id == ds_id))
        db.execute(delete(Dataset).where(Dataset.id == ds_id))
        audit(db, who, "dataset_delete", f"dataset:{ds_id}")
        db.commit()
    return {"deleted": True}


@app.get("/api/datasets/{ds_id}/overview")
def overview(ds_id: int, who: Who = Depends(current)):
    with SessionLocal() as db:
        ds = db.get(Dataset, ds_id) or _404("Dataset")
        st = dict(db.execute(select(Alert.status, func.count()).where(Alert.dataset_id == ds_id).group_by(Alert.status)).all())
        tx = db.execute(select(func.date(Transaction.ts), func.count(), func.sum(Transaction.amount))
                        .where(Transaction.dataset_id == ds_id).group_by(func.date(Transaction.ts)).order_by(func.date(Transaction.ts))).all()
        audit(db, who, "view_overview", f"dataset:{ds_id}")
        db.commit()
        return {"dataset": {"id": ds.id, "name": ds.name, "uploaded_at": ds.uploaded_at, "report": ds.report}, "summary": ds.summary,
                "status_counts": st, "daily_volume": [{"date": str(d), "count": c, "amount": round(a or 0, 2)} for d, c, a in tx],
                "statuses": STATUSES}


def _404(what):
    raise HTTPException(404, f"{what} not found")


@app.get("/api/datasets/{ds_id}/alerts")
def alerts(ds_id: int, q: str | None = None, priority: str | None = None, status: str | None = None, rule: str | None = None,
           who: Who = Depends(current)):
    with SessionLocal() as db:
        stmt = select(Alert).where(Alert.dataset_id == ds_id)
        if q:
            stmt = stmt.where(Alert.account.ilike(f"%{q}%"))
        if priority:
            stmt = stmt.where(Alert.priority == priority)
        if status:
            stmt = stmt.where(Alert.status == status)
        rows = db.scalars(stmt).all()
        if rule:
            rows = [a for a in rows if rule in a.rule_families]
        order = {"high": 0, "medium": 1, "low": 2}
        rows.sort(key=lambda a: (order.get(a.priority, 3), -a.model_score))
        audit(db, who, "search_alerts", f"dataset:{ds_id}", q=q, priority=priority, status=status, rule=rule)
        db.commit()
        return [_alert_dict(a) for a in rows]


def _alert_dict(a: Alert) -> dict:
    return {"id": a.id, "dataset_id": a.dataset_id, "account": a.account, "priority": a.priority, "model_score": a.model_score,
            "rule_families": a.rule_families, "explanation": a.explanation, "linked_alerts": a.linked_alerts, "status": a.status,
            "ring_ids": a.ring_ids or [], "pattern_score": a.pattern_score,
            "priority_reason": a.priority_reason, "model_reliable": a.model_reliable if a.model_reliable is not None else True,
            "status_meaning": STATUSES.get(a.status), "assigned_to": a.assigned_to, "reviewed_by": a.reviewed_by,
            "created_at": a.created_at, "updated_at": a.updated_at}


@app.get("/api/alerts/{alert_id}")
def alert_detail(alert_id: int, who: Who = Depends(current)):
    with SessionLocal() as db:
        a = db.get(Alert, alert_id) or _404("Alert")
        acct = db.scalar(select(Account).where(Account.dataset_id == a.dataset_id, Account.alias == a.account))
        notes = db.scalars(select(CaseNote).where(CaseNote.alert_id == alert_id).order_by(CaseNote.created_at)).all()
        audit(db, who, "view_alert", f"alert:{alert_id}")
        db.commit()
        m = load_model()
        return {**_alert_dict(a), "features": acct.features, "evidence": acct.evidence,
                "threshold": m["threshold"] if m else None,
                "notes": [{"author": n.author, "text": n.text, "created_at": n.created_at} for n in notes],
                "disclaimer": "This alert is a reason to review evidence. It is not proof of criminal activity. No automatic action has been taken."}


@app.get("/api/datasets/{ds_id}/accounts/{alias}")
def account_detail(ds_id: int, alias: str, who: Who = Depends(current)):
    with SessionLocal() as db:
        acct = db.scalar(select(Account).where(Account.dataset_id == ds_id, Account.alias == alias)) or _404("Account")
        txs = db.scalars(select(Transaction).where(Transaction.dataset_id == ds_id, or_(Transaction.src == alias, Transaction.dst == alias))
                         .order_by(Transaction.ts)).all()
        alert = db.scalar(select(Alert).where(Alert.dataset_id == ds_id, Alert.account == alias))
        audit(db, who, "view_account", f"dataset:{ds_id}/{alias}")
        db.commit()
    incoming = [t for t in txs if t.dst == alias]
    outgoing = [t for t in txs if t.src == alias]

    def cp(rows, key):
        d: dict = {}
        for t in rows:
            k = getattr(t, key)
            x = d.setdefault(k, {"account": k, "count": 0, "total": 0.0})
            x["count"] += 1
            x["total"] = round(x["total"] + t.amount, 2)
        return sorted(d.values(), key=lambda r: -r["total"])

    tl = [{"txn_id": t.txn_id, "ts": t.ts, "direction": "in" if t.dst == alias else "out",
           "counterparty": t.src if t.dst == alias else t.dst, "amount": t.amount, "channel": t.channel} for t in txs]
    with SessionLocal() as db:
        applicable = (db.get(Dataset, ds_id).summary or {}).get("model_applicable", True)
    return {"account": alias, "features": acct.features, "evidence": acct.evidence, "model_score": acct.model_score,
            "model_applicable": applicable,
            "alert": _alert_dict(alert) if alert else None, "timeline": tl,
            "senders": cp(incoming, "src"), "receivers": cp(outgoing, "dst"),
            "totals": {"in": round(sum(t.amount for t in incoming), 2), "out": round(sum(t.amount for t in outgoing), 2)}}


@app.get("/api/datasets/{ds_id}/graph")
def graph(ds_id: int, account: str | None = None, hops: int = Query(default=2, ge=1, le=3), limit: int = Query(default=160, le=600),
          who: Who = Depends(current)):
    """Ego network around an account, or (no account) all alerted accounts plus direct counterparties."""
    with SessionLocal() as db:
        rows = db.execute(select(Transaction.src, Transaction.dst, func.count(), func.sum(Transaction.amount), func.min(Transaction.ts), func.max(Transaction.ts))
                          .where(Transaction.dataset_id == ds_id).group_by(Transaction.src, Transaction.dst)).all()
        al = {a.account: a for a in db.scalars(select(Alert).where(Alert.dataset_id == ds_id))}
        scores = dict(db.execute(select(Account.alias, Account.model_score).where(Account.dataset_id == ds_id)).all())
        audit(db, who, "view_graph", f"dataset:{ds_id}", account=account)
        db.commit()
    G = nx.DiGraph()
    for s, d, c, amt, t0, t1 in rows:
        G.add_edge(s, d, count=c, amount=round(amt, 2), first=str(t0), last=str(t1))
    if account:
        if account not in G:
            _404("Account")
        U = G.to_undirected(as_view=True)
        dist = nx.single_source_shortest_path_length(U, account, cutoff=hops)
        nodes = sorted(dist, key=lambda n: (dist[n], -(n in al), -G.degree(n)))[:limit]
    else:
        # Overview: alerted accounts + their strongest direct counterparties. Only flows that touch an
        # alerted account are drawn, so unrelated alerts stay as separate clusters (no "hairball").
        order = {"high": 0, "medium": 1, "low": 2}
        seeds = sorted(al, key=lambda a: (order.get(al[a].priority, 3), -al[a].model_score))
        nodes, keep_edges = [], set()
        per_seed = 12
        # each non-alerted counterparty is drawn once, attached to its strongest alerted account,
        # so shared merchants/people don't weld unrelated clusters into one ball
        best_link: dict[str, tuple[float, tuple]] = {}
        for s in seeds:
            for v in G.successors(s):
                if v not in al and (v not in best_link or G[s][v]["amount"] > best_link[v][0]):
                    best_link[v] = (G[s][v]["amount"], (s, v))
            for u in G.predecessors(s):
                if u not in al and (u not in best_link or G[u][s]["amount"] > best_link[u][0]):
                    best_link[u] = (G[u][s]["amount"], (u, s))
        owned: dict[str, list] = {}
        for v, (amt, e) in best_link.items():
            seed = e[0] if e[0] in al else e[1]
            owned.setdefault(seed, []).append((amt, v, e))
        for s in seeds:
            if len(nodes) >= limit:
                break
            if s not in nodes:
                nodes.append(s)
            for amt, v, e in sorted(owned.get(s, []), key=lambda x: -x[0])[:per_seed]:
                if len(nodes) >= limit:
                    break
                nodes.append(v)
                keep_edges.add(e)
        # flows between two alerted accounts are drawn when both belong to the same detected ring
        # (a real pattern); other cross-links stay visible in each account's own ego view
        with SessionLocal() as db:
            ds = db.get(Dataset, ds_id)
            ring_rows = ds.rings or []
        ring_of: dict[str, set] = {}
        for r in ring_rows:
            for m in r["member_accounts"]:
                ring_of.setdefault(m, set()).add(r["ring_id"])
        for r in ring_rows:  # make sure ring members are on the canvas
            for m in r["member_accounts"]:
                if m in al and m not in nodes and len(nodes) < limit:
                    nodes.append(m)
        for u, v in G.edges():
            if u in al and v in al and u in nodes and v in nodes and ring_of.get(u, set()) & ring_of.get(v, set()):
                keep_edges.add((u, v))
    ns = set(nodes)
    edge_list = ([(u, v) for u, v in G.edges() if u in ns and v in ns] if account
                 else [e for e in keep_edges if e[0] in ns and e[1] in ns])
    return {
        "nodes": [{"id": n, "alerted": n in al, "priority": al[n].priority if n in al else None, "score": round(scores.get(n, 0.0), 3),
                   "status": al[n].status if n in al else None, "focus": n == account,
                   "in_amount": round(sum(G[u][n]["amount"] for u in G.predecessors(n)), 2),
                   "out_amount": round(sum(G[n][v]["amount"] for v in G.successors(n)), 2)} for n in nodes],
        "edges": [{"source": u, "target": v, **G[u][v]} for u, v in edge_list],
        "mode": "ego" if account else "clusters",
        "truncated": len(ns) >= limit,
    }


# ---------------- investigation workflow ----------------
class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@app.post("/api/alerts/{alert_id}/notes")
def add_note(alert_id: int, body: NoteIn, who: Who = Depends(current)):
    with SessionLocal() as db:
        a = db.get(Alert, alert_id) or _404("Alert")
        db.add(CaseNote(alert_id=a.id, author=who.username, text=body.text))
        if a.status == "open":
            a.status, a.assigned_to = "in_review", who.username
        a.updated_at = datetime.utcnow()
        audit(db, who, "add_note", f"alert:{alert_id}")
        db.commit()
    return alert_detail(alert_id, who)


class StatusIn(BaseModel):
    status: str
    reason: str = Field(min_length=3, max_length=2000)


@app.post("/api/alerts/{alert_id}/status")
def set_status(alert_id: int, body: StatusIn, who: Who = Depends(current)):
    if body.status not in STATUSES:
        raise HTTPException(400, f"Status must be one of {list(STATUSES)}")
    if body.status in LEAD_ONLY and who.role != "lead":
        raise HTTPException(403, "Only a lead investigator can record a final disposition")
    with SessionLocal() as db:
        a = db.get(Alert, alert_id) or _404("Alert")
        old = a.status
        a.status, a.updated_at = body.status, datetime.utcnow()
        a.assigned_to = a.assigned_to or who.username
        if body.status in LEAD_ONLY:
            a.reviewed_by = who.username
        db.add(CaseNote(alert_id=a.id, author=who.username, text=f"[Status {old} → {body.status}] {body.reason}"))
        audit(db, who, "set_status", f"alert:{alert_id}", old=old, new=body.status)
        db.commit()
    return alert_detail(alert_id, who)


@app.post("/api/datasets/{ds_id}/accounts/{alias}/reveal")
def reveal(ds_id: int, alias: str, body: NoteIn, who: Who = Depends(current)):
    """Restricted identifier lookup: lead only, reason required, audit-logged."""
    if who.role != "lead":
        raise HTTPException(403, "Only a lead investigator can reveal account identifiers")
    with SessionLocal() as db:
        acct = db.scalar(select(Account).where(Account.dataset_id == ds_id, Account.alias == alias)) or _404("Account")
        audit(db, who, "reveal_identifier", f"dataset:{ds_id}/{alias}", reason=body.text)
        db.commit()
        return {"alias": alias, "account_identifier": cipher.decrypt(acct.real_enc).decode()}


@app.post("/api/alerts/{alert_id}/summary")
def ai_summary(alert_id: int, who: Who = Depends(current)):
    d = alert_detail(alert_id, who)
    facts = {"account": d["account"], "priority": d["priority"], "priority_reason": d.get("priority_reason"),
             "rules": d["evidence"].get("rules", {}), "explanation": d["explanation"], "linked_alerts": d["linked_alerts"][:10]}
    if d.get("model_reliable") is not False:
        facts.update(model_score=round(d["model_score"], 3), threshold=round(d["threshold"], 3))
    else:
        facts["model_used"] = "No - the dataset is too small for the ML model; the alert comes from rules only."
    fallback = " ".join(d["explanation"]) + " These indicators warrant human review; they do not establish wrongdoing."
    if not llm.available():
        return {"summary": fallback, "llm_used": False}
    try:
        txt = llm.chat([
            {"role": "system", "content": "You summarise evidence for a bank fraud investigator. Use ONLY the facts given. "
             "Be neutral and precise; list what the evidence shows and what an investigator might verify next (e.g. KYC, customer contact, device data). "
             "Never state or imply the customer is guilty, a criminal or a mule; never recommend freezing accounts. Under 150 words."},
            {"role": "user", "content": json.dumps(facts, default=str)}], max_tokens=350, temperature=0.2)
        return {"summary": txt, "llm_used": True}
    except Exception:
        return {"summary": fallback, "llm_used": False}


@app.get("/api/alerts/{alert_id}/report", response_class=PlainTextResponse)
def report(alert_id: int, who: Who = Depends(current)):
    d = alert_detail(alert_id, who)
    acc = account_detail(d["dataset_id"], d["account"], who)
    with SessionLocal() as db:
        audit(db, who, "export_report", f"alert:{alert_id}")
        db.commit()
    lines = [f"# Investigation report - Alert #{d['id']} ({d['account']})", "",
             f"Generated {datetime.utcnow():%Y-%m-%d %H:%M} UTC by {who.username} ({who.role}).", "",
             "> This report documents indicators for human review. It is not a finding of criminal activity.", "",
             "## Alert", f"- Priority: **{d['priority']}**" + (f" ({d['priority_reason']})" if d.get("priority_reason") else ""),
             (f"- Model score: {d['model_score']:.2f} (threshold {d['threshold']:.2f})" if d.get("model_reliable") is not False
              else "- Model score: not used (dataset too small for the model; rules only)"),
             f"- Rule families: {', '.join(d['rule_families']) or 'none'}", f"- Status: **{d['status']}** - {d['status_meaning']}",
             f"- Assigned to: {d['assigned_to'] or '-'} · Final review by: {d['reviewed_by'] or '-'}",
             f"- Linked alerted accounts (≤2 hops): {', '.join(d['linked_alerts']) or 'none'}", "", "## Evidence"]
    lines += [f"- {e}" for e in d["explanation"]]
    lines += ["", "## Flow totals", f"- Total in: ₹{acc['totals']['in']:,.2f}", f"- Total out: ₹{acc['totals']['out']:,.2f}", "",
              "## Top counterparties (in)"] + [f"- {c['account']}: {c['count']} txns, ₹{c['total']:,.2f}" for c in acc["senders"][:8]]
    lines += ["", "## Top counterparties (out)"] + [f"- {c['account']}: {c['count']} txns, ₹{c['total']:,.2f}" for c in acc["receivers"][:8]]
    lines += ["", "## Transaction trail", "| Time | Dir | Counterparty | Amount | Channel |", "|---|---|---|---|---|"]
    lines += [f"| {t['ts']} | {t['direction']} | {t['counterparty']} | ₹{t['amount']:,.2f} | {t['channel']} |" for t in acc["timeline"][:200]]
    lines += ["", "## Case notes"] + [f"- {n['created_at']:%Y-%m-%d %H:%M} **{n['author']}**: {n['text']}" for n in d["notes"]]
    return "\n".join(lines)


# ---------------- evaluation against known labels (synthetic tests) ----------------
def _store_eval(db, ds: Dataset, labels: dict[str, int]):
    alerts_ = {a.account: a for a in db.scalars(select(Alert).where(Alert.dataset_id == ds.id))}
    all_accts = [a.alias for a in db.scalars(select(Account).where(Account.dataset_id == ds.id))]
    accts = [a for a in all_accts if a in labels]
    if not accts:
        raise HTTPException(400, "None of the account ids in the labels file match this dataset")
    res = {"labelled_accounts": len(accts), "unlabelled_accounts": len(all_accts) - len(accts)}
    for name, pred in {"any_alert": lambda a: a in alerts_,
                       "medium_or_high": lambda a: a in alerts_ and alerts_[a].priority in ("high", "medium"),
                       "high_only": lambda a: a in alerts_ and alerts_[a].priority == "high"}.items():
        tp = sum(1 for a in accts if pred(a) and labels.get(a) == 1)
        fp = sum(1 for a in accts if pred(a) and labels.get(a, 0) == 0)
        fn = sum(1 for a in accts if not pred(a) and labels.get(a) == 1)
        tn = len(accts) - tp - fp - fn
        res[name] = {"precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                     "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else None, "tp": tp, "fp": fp, "fn": fn, "tn": tn}
    from sklearn.metrics import average_precision_score
    sc = {a.alias: a.model_score for a in db.scalars(select(Account).where(Account.dataset_id == ds.id))}
    y = [labels.get(a, 0) for a in accts]
    if 0 < sum(y) < len(y):
        res["model_pr_auc"] = round(float(average_precision_score(y, [sc[a] for a in accts])), 4)
    s = dict(ds.summary)
    s["evaluation"] = {**res, "alerts_at_evaluation": len(alerts_),
                       "note": "Measured against labels supplied with this (synthetic) dataset, on labelled accounts only."}
    ds.summary = s
    db.commit()
    return s["evaluation"]


@app.post("/api/datasets/{ds_id}/evaluate")
async def evaluate(ds_id: int, file: UploadFile = File(...), who: Who = Depends(current)):
    """Upload a labels CSV (account,is_mule) for a synthetic test file to measure precision/recall/FPR."""
    lab = pd.read_csv(io.BytesIO(await file.read()), dtype=str)
    cols = {c.lower(): c for c in lab.columns}
    if "account" not in cols or not ({"is_mule", "label"} & set(cols)):
        raise HTTPException(422, "Labels file needs columns: account, is_mule")
    lc = cols.get("is_mule") or cols.get("label")
    with SessionLocal() as db:
        ds = db.get(Dataset, ds_id) or _404("Dataset")
        alias = {cipher.decrypt(a.real_enc).decode(): a.alias for a in db.scalars(select(Account).where(Account.dataset_id == ds_id))}
        labels = {alias[r[cols["account"]]]: int(str(r[lc]).strip() in {"1", "true", "True"}) for _, r in lab.iterrows() if r[cols["account"]] in alias}
        audit(db, who, "evaluate", f"dataset:{ds_id}")
        return _store_eval(db, ds, labels)


@app.get("/api/audit")
def audit_log(limit: int = Query(default=200, le=1000), who: Who = Depends(current)):
    if who.role != "lead":
        raise HTTPException(403, "Only a lead investigator can view the audit log")
    with SessionLocal() as db:
        return [{"at": r.at, "actor": r.actor, "action": r.action, "target": r.target, "detail": r.detail}
                for r in db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(limit))]


@app.get("/api/meta")
def meta():
    mm = config.ARTIFACT_DIR / "mule_metrics.json"
    return {"product": "Money Mule Detection", "statuses": STATUSES, "model_metrics": json.loads(mm.read_text()) if mm.exists() else None,
            "llm": {"provider": "Groq", "configured": llm.available()},
            "positioning": "Prototype investigation support. Does not freeze accounts or make determinations about people."}


@app.get("/api/sample-dataset", response_class=PlainTextResponse)
def sample():
    return (config.ROOT / "data" / "samples" / "mule_demo_transactions.csv").read_text()


# ---------------- live streaming (built-in simulator + WebSocket push) ----------------
class LiveStartIn(BaseModel):
    name: str = Field(default="", max_length=120)
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    auto_patterns: bool = True


def _sim_append(ds_id: int, rows: list[str]) -> dict:
    return append_lines(ds_id, rows, "live-simulator")


@app.post("/api/live/start")
def live_start(body: LiveStartIn, who: Who = Depends(current)):
    with SessionLocal() as db:
        ds = Dataset(name=body.name or f"Live stream {datetime.now():%d %b %H:%M}", uploaded_by=who.username, encrypted_file=cipher.encrypt(b""),
                     live=True, status="live", report={"rows_in": 0, "rows_out": 0, "rows_dropped_invalid": 0, "rows_dropped_duplicate": 0,
                                                       "source": "built-in simulator"},
                     summary={"accounts": 0, "transactions": 0, "alerts": 0}, rings=[])
        db.add(ds)
        audit(db, who, "live_start", "dataset:new", speed=body.speed)
        db.commit()
        ds_id = ds.id
    sim = live.Simulator(ds_id, _sim_append, speed=body.speed, auto_patterns=body.auto_patterns)
    live.sims[ds_id] = sim
    sim.start()
    return {"dataset_id": ds_id, "status": "running", "patterns": live.PATTERNS}


def _sim(ds_id: int) -> "live.Simulator":
    sim = live.sims.get(ds_id)
    if not sim:
        raise HTTPException(404, "No simulator running for this dataset")
    return sim


@app.post("/api/live/{ds_id}/stop")
def live_stop(ds_id: int, who: Who = Depends(current)):
    _sim(ds_id).stop()
    return {"stopped": True}


class InjectIn(BaseModel):
    pattern: str


@app.post("/api/live/{ds_id}/inject")
def live_inject(ds_id: int, body: InjectIn, who: Who = Depends(current)):
    if body.pattern not in live.PATTERNS:
        raise HTTPException(400, f"pattern must be one of {list(live.PATTERNS)}")
    sim = _sim(ds_id)
    if sim.status != "running":
        raise HTTPException(409, "Simulator is not running")
    with SessionLocal() as db:
        audit(db, who, "live_inject", f"dataset:{ds_id}", pattern=body.pattern)
        db.commit()
    return sim.inject(body.pattern)


class ManualTxIn(BaseModel):
    sender: str = Field(min_length=2, max_length=40)
    receiver: str = Field(min_length=2, max_length=40)
    amount: float = Field(gt=0, le=100_000_000)


@app.post("/api/live/{ds_id}/tx")
def live_manual_tx(ds_id: int, body: ManualTxIn, who: Who = Depends(current)):
    """mr-mule style: an investigator types a transaction; it is analysed immediately."""
    if body.sender == body.receiver:
        raise HTTPException(422, "Sender and receiver must differ")
    sim = live.sims.get(ds_id)
    ts = sim.clock if sim else datetime.now()
    row = f"MANUAL{ds_id}_{int(time.time() * 1000)},{body.sender},{body.receiver},{body.amount:.2f},{ts:%Y-%m-%d %H:%M:%S}"
    out = append_lines(ds_id, [row], who.username)
    live.hub.broadcast(ds_id, {"type": "batch", **out})
    for a in out.get("new_alerts", []):
        live.hub.broadcast(ds_id, {"type": "alert", "dataset_id": ds_id, **a})
    return out


@app.get("/api/live/{ds_id}/status")
def live_status(ds_id: int, who: Who = Depends(current)):
    sim = live.sims.get(ds_id)
    return {"running": bool(sim and sim.status == "running"), "status": sim.status if sim else "none",
            "sent": sim.sent if sim else 0, "injected": sim.injected if sim else [], "patterns": live.PATTERNS,
            "sim_clock": str(sim.clock) if sim else None, "recent": list(live.hub.recent.get(ds_id, []))[-10:]}


@app.websocket("/ws/live/{ds_id}")
async def live_ws(websocket: WebSocket, ds_id: int, token: str = ""):
    body = read_token(token, config.MULE_SECRET)
    if not body or body.get("aud") != "institution":
        await websocket.close(code=4401)
        return
    await websocket.accept()
    loop = asyncio.get_running_loop()
    live.hub.add(ds_id, websocket, loop)
    sim = live.sims.get(ds_id)
    await websocket.send_json({"type": "hello", "dataset_id": ds_id, "running": bool(sim and sim.status == "running"),
                               "patterns": live.PATTERNS})
    try:
        while True:
            await websocket.receive_text()  # keep-alive pings from the client
    except WebSocketDisconnect:
        pass
    finally:
        live.hub.remove(ds_id, websocket)


@app.get("/api/datasets/{ds_id}/trail")
def money_trail(ds_id: int, account: str, hops: int = Query(default=2, ge=1, le=3), limit: int = Query(default=250, le=1000),
                who: Who = Depends(current)):
    """Time-ordered transactions inside an account's ego network, for the money-trail replay."""
    g = graph(ds_id, account=account, hops=hops, limit=90, who=who)
    ids = {n["id"] for n in g["nodes"]}
    with SessionLocal() as db:
        rows = db.execute(select(Transaction.ts, Transaction.src, Transaction.dst, Transaction.amount)
                          .where(Transaction.dataset_id == ds_id, Transaction.src.in_(ids), Transaction.dst.in_(ids))
                          .order_by(Transaction.ts)).all()
    # keep the transactions closest in time to the focus account's own activity
    own = [r for r in rows if account in (r[1], r[2])]
    if own and len(rows) > limit:
        t0, t1 = own[0][0], own[-1][0]
        rows = sorted(rows, key=lambda r: 0 if t0 <= r[0] <= t1 else min(abs((r[0] - t0).total_seconds()), abs((r[0] - t1).total_seconds())))[:limit]
        rows.sort(key=lambda r: r[0])
    return [{"ts": str(t), "source": s, "target": d, "amount": round(a, 2)} for t, s, d, a in rows[:limit]]
