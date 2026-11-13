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
    time: Mapped[str | None] = mapped_column(String(8), nullable=True)  # HH:MM[:SS] when known (SMS / live)
    source: Mapped[str | None] = mapped_column(String(12), nullable=True)  # statement | sms | manual | demo

    def as_dict(self) -> dict:
        return {
            "id": self.id, "uid": self.uid, "date": self.date, "description": cipher.decrypt(self.description_enc).decode(),
            "type": self.type, "amount": self.amount, "balance": self.balance, "status": self.status,
            "merchant": self.merchant, "merchant_key": self.merchant_key, "category": self.category,
            "subcategory": self.subcategory, "category_source": self.category_source, "credit_kind": self.credit_kind,
            "is_essential": self.is_essential, "excluded": self.excluded, "note": self.note, "row": self.row,
            "time": self.time, "source": self.source or "statement",
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
    start_saved: Mapped[float | None] = mapped_column(Float, nullable=True)  # saved amount when the goal was set (for pace)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Settings(Base):
    __tablename__ = "settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    emergency_buffer: Mapped[float] = mapped_column(Float, default=500.0)
    opening_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    manual_commitments: Mapped[list] = mapped_column(JSON, default=list)  # [{"name","amount","due"}]
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Profile(Base):
    __tablename__ = "profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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


class Friend(Base):
    __tablename__ = "friends"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    emoji: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SplitEntry(Base):
    """A shared expense or a settlement. `paid_by` is 0 for me or a friend id; `shares` maps
    "me"/friend-id -> amount owed for this entry. A settlement is just an entry where one
    person pays and the other holds the whole share."""
    __tablename__ = "split_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12), default="expense")  # expense | settlement
    title: Mapped[str] = mapped_column(String(120))
    date: Mapped[datetime] = mapped_column(Date)
    total: Mapped[float] = mapped_column(Float)
    paid_by: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[dict] = mapped_column(JSON, default=dict)
    txn_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NudgeDismissal(Base):
    __tablename__ = "nudge_dismissals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    nudge_id: Mapped[str] = mapped_column(String(160))


class CreditStep(Base):
    __tablename__ = "credit_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[str] = mapped_column(String(40))
    done_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GuardSettings(Base):
    """Spend Guard personalisation (tone, needs/wants, limits, caps, lenses, events...). See guard.defaults()."""
    __tablename__ = "guard_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NatureRule(Base):
    """The user's own need/want rules: scope merchant | subcategory | category."""
    __tablename__ = "nature_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(12))
    key: Mapped[str] = mapped_column(String(120))
    nature: Mapped[str] = mapped_column(String(8))  # need | want | flex


class GuardAlert(Base):
    __tablename__ = "guard_alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    txn_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24))  # want | cap | limit | shield | fraud | duplicate | scam | charge
    severity: Mapped[str] = mapped_column(String(8))  # info | warn | alert
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="new")  # new | seen | confirmed | disputed | done
    quiet: Mapped[bool] = mapped_column(Boolean, default=False)  # raised during quiet hours: no pop-up
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WishItem(Base):
    """'Think twice' list: a want parked for a cool-off period, with price tracking."""
    __tablename__ = "wish_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    price: Mapped[float] = mapped_column(Float)
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    check_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(12), default="waiting")  # waiting | bought | skipped
    best_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    price_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Reflection(Base):
    """'Worth it?' answers for past wants; teaches the guard what this user regrets."""
    __tablename__ = "reflections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    txn_id: Mapped[int] = mapped_column(Integer, index=True)
    verdict: Mapped[str] = mapped_column(String(8))  # worth | regret
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JarEntry(Base):
    """Want-tax and round-up amounts the user pledged to move to a goal."""
    __tablename__ = "jar_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    txn_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(12))  # want_tax | roundup
    amount: Mapped[float] = mapped_column(Float)
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="pending")  # pending | moved | skipped
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(engine)
    # lightweight migrations for columns added after a store was created
    from sqlalchemy import inspect, text
    cols = {c["name"] for c in inspect(engine).get_columns("goals")}
    if "start_saved" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE goals ADD COLUMN start_saved FLOAT"))
    tcols = {c["name"] for c in inspect(engine).get_columns("transactions")}
    with engine.begin() as conn:
        if "time" not in tcols:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN time VARCHAR(8)"))
        if "source" not in tcols:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN source VARCHAR(12)"))


init_db()  # idempotent; ensures tables exist for any importer (API, scripts, tests)


def dumps(o) -> str:
    return json.dumps(o, default=str)
