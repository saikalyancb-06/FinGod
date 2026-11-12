"""FinLens store (separate database from the institutional system)."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from shared.config import FINLENS_DATA_KEY, FINLENS_DATABASE_URL
from shared.dbutil import make_engine
from shared.security import Cipher

engine = make_engine(FINLENS_DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
cipher = Cipher(FINLENS_DATA_KEY)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(40))  # analytics | smart_spending | credit
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Statement(Base):
    __tablename__ = "statements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    encrypted_file: Mapped[bytes] = mapped_column(LargeBinary)
    report: Mapped[dict] = mapped_column(JSON)


class Txn(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("statements.id", ondelete="CASCADE"))
    uid: Mapped[str] = mapped_column(String(32))
    date: Mapped[datetime] = mapped_column(Date)
    description_enc: Mapped[bytes] = mapped_column(LargeBinary)
    type: Mapped[str] = mapped_column(String(10))
    amount: Mapped[float] = mapped_column(Float)
    balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(12))
    merchant: Mapped[str | None] = mapped_column(String(120), nullable=True)
    merchant_key: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))
    subcategory: Mapped[str | None] = mapped_column(String(60), nullable=True)
    category_source: Mapped[str] = mapped_column(String(12))
    credit_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_essential: Mapped[bool] = mapped_column(Boolean)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    row: Mapped[int] = mapped_column(Integer)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "uid": self.uid, "date": self.date, "description": cipher.decrypt(self.description_enc).decode(),
            "type": self.type, "amount": self.amount, "balance": self.balance, "status": self.status,
            "merchant": self.merchant, "merchant_key": self.merchant_key, "category": self.category,
            "subcategory": self.subcategory, "category_source": self.category_source, "credit_kind": self.credit_kind,
            "is_essential": self.is_essential, "excluded": self.excluded, "note": self.note, "row": self.row,
        }


class CategoryOverride(Base):
    __tablename__ = "category_overrides"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    merchant_key: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))


class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    target: Mapped[float] = mapped_column(Float)
    saved: Mapped[float] = mapped_column(Float, default=0.0)
    target_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    monthly_contribution: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Settings(Base):
    __tablename__ = "settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    emergency_buffer: Mapped[float] = mapped_column(Float, default=500.0)
    opening_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    manual_commitments: Mapped[list] = mapped_column(JSON, default=list)  # [{"name","amount","due"}]
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)


class SpendSession(Base):
    __tablename__ = "spending_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SearchCache(Base):
    __tablename__ = "search_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(engine)


init_db()  # idempotent; ensures tables exist for any importer (API, scripts, tests)


def dumps(o) -> str:
    return json.dumps(o, default=str)
