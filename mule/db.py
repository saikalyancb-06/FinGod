"""Institutional store - physically separate from FinLens (different DB, key and secret)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from shared.config import MULE_DATA_KEY, MULE_DATABASE_URL
from shared.dbutil import make_engine
from shared.security import Cipher

engine = make_engine(MULE_DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
cipher = Cipher(MULE_DATA_KEY)


class Base(DeclarativeBase):
    pass


class Investigator(Base):
    __tablename__ = "investigators"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))  # analyst | lead
    password_hash: Mapped[str] = mapped_column(String(200))


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    uploaded_by: Mapped[str] = mapped_column(String(80))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    encrypted_file: Mapped[bytes] = mapped_column(LargeBinary)
    report: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="processing")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    rings: Mapped[list] = mapped_column(JSON, default=list)
    live: Mapped[bool] = mapped_column(default=False)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(20), index=True)
    real_enc: Mapped[bytes] = mapped_column(LargeBinary)        # restricted: encrypted real identifier
    features: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON)
    model_score: Mapped[float] = mapped_column(Float)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    txn_id: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    src: Mapped[str] = mapped_column(String(20), index=True)   # alias
    dst: Mapped[str] = mapped_column(String(20), index=True)   # alias
    amount: Mapped[float] = mapped_column(Float)
    channel: Mapped[str] = mapped_column(String(20))


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    account: Mapped[str] = mapped_column(String(20), index=True)  # alias
    priority: Mapped[str] = mapped_column(String(10))
    model_score: Mapped[float] = mapped_column(Float)
    rule_families: Mapped[list] = mapped_column(JSON)
    explanation: Mapped[list] = mapped_column(JSON)
    linked_alerts: Mapped[list] = mapped_column(JSON, default=list)
    ring_ids: Mapped[list] = mapped_column(JSON, default=list)
    pattern_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="open")
    assigned_to: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CaseNote(Base):
    __tablename__ = "case_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str] = mapped_column(String(120))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


def init_db():
    Base.metadata.create_all(engine)


init_db()
