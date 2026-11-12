"""Data utilities: validation, schema mapping and date/amount normalization.

Used by both products' ingestion layers.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

# Column-name synonyms seen in Indian bank statement exports.
COLUMN_SYNONYMS: dict[str, list[str]] = {
    "date": ["date", "txn date", "transaction date", "value date", "posting date", "tran date"],
    "description": ["description", "narration", "particulars", "details", "remarks", "transaction details"],
    "type": ["type", "dr/cr", "cr/dr", "debit/credit", "txn type", "transaction type"],
    "amount": ["amount", "txn amount", "transaction amount", "amount (inr)", "amount(inr)"],
    "debit": ["debit", "withdrawal", "withdrawal amt", "withdrawal amount", "debit amount", "dr"],
    "credit": ["credit", "deposit", "deposit amt", "deposit amount", "credit amount", "cr"],
    "balance": ["balance", "closing balance", "running balance", "available balance"],
    "status": ["status", "txn status"],
}


@dataclass
class ValidationReport:
    rows_in: int = 0
    rows_out: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    column_map: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def read_csv_bytes(content: bytes, max_bytes: int = 5 * 1024 * 1024) -> pd.DataFrame:
    if len(content) > max_bytes:
        raise ValueError(f"File too large ({len(content)} bytes); limit is {max_bytes} bytes")
    if not content.strip():
        raise ValueError("File is empty")
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(content), encoding=enc, dtype=str, skipinitialspace=True)
        except UnicodeDecodeError:
            continue
        except pd.errors.ParserError as exc:
            raise ValueError(f"Could not parse CSV: {exc}") from exc
    raise ValueError("Unsupported file encoding")


def map_columns(df: pd.DataFrame) -> dict[str, str]:
    """Return {canonical_name: original_column}."""
    lower = {c.strip().lower(): c for c in df.columns}
    mapping: dict[str, str] = {}
    for canon, options in COLUMN_SYNONYMS.items():
        for opt in options:
            if opt in lower:
                mapping[canon] = lower[opt]
                break
    return mapping


_AMOUNT_RE = re.compile(r"[^\d.\-]")


def parse_amount(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "-"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = _AMOUNT_RE.sub("", s.replace("₹", "").replace(",", ""))
    if s in {"", "-", "."}:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -abs(v) if negative else v


def parse_dates(series: pd.Series, default_year: int | None = None) -> pd.Series:
    """Parse mixed date formats; supports '01 Nov' style by adding a default year."""
    s = series.astype(str).str.strip()
    out = pd.to_datetime(s, errors="coerce", dayfirst=True, format="mixed")
    missing = out.isna()
    if missing.any() and default_year:
        out.loc[missing] = pd.to_datetime(s[missing] + f" {default_year}", errors="coerce", dayfirst=True, format="mixed")
    return out


def normalize_type(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in {"cr", "credit", "c", "deposit", "in", "inflow"}:
        return "credit"
    if v in {"dr", "debit", "d", "withdrawal", "out", "outflow"}:
        return "debit"
    return None


def clean_description(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t
