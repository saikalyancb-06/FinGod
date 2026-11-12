"""Engine factory shared by both products' (separate) stores."""
from __future__ import annotations

from sqlalchemy import create_engine, event


def make_engine(url: str):
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")   # readers don't block writers
            cur.execute("PRAGMA busy_timeout=15000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True)
