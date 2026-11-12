"""PDF statement support. Converts a PDF into the same CSV the ingestion pipeline reads.

Three strategies, tried in order:
  1. Table extraction (pdfplumber) when the PDF has a real table with a recognisable header.
  2. Line-based bank statement parsing: "<date> <description> <amount> [Cr/Dr] <balance>".
  3. UPI-app statements ("Paid to X" / "Received from Y" with ₹ amounts), with heuristics
     ported from the team's CredWise parser (reference/cred-wise/server/parse_bert_pdf.py).

Image-only (scanned) PDFs are not supported; the user is asked for a CSV instead.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

import pandas as pd

from shared.normalize import map_columns

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}[ -](?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[ ,-]+\d{2,4})\b", re.I)
AMT_RE = re.compile(r"(?:₹|rs\.?|inr)?\s*(-?\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})|-?\d+\.\d{1,2}|-?\d{1,3}(?:,\d{3})+)\s*(cr|dr)?\b", re.I)


def _pymupdf_extract(content: bytes) -> tuple[str, list]:
    """PyMuPDF: reading-order text (sort=True) and its table finder."""
    import fitz  # PyMuPDF
    text, tables = [], []
    with fitz.open(stream=content, filetype="pdf") as doc:
        if doc.needs_pass:
            raise ValueError("This PDF is password-protected. Please remove the password or export a CSV.")
        for page in doc:
            text.append(page.get_text("text", sort=True))
            try:
                for t in page.find_tables().tables:
                    rows = [[(c or "").strip() for c in r] for r in t.extract()]
                    if len(rows) > 1:
                        tables.append(rows)
            except Exception:
                pass
    return "\n".join(text), tables


def _pdfplumber_extract(content: bytes) -> tuple[str, list]:
    import pdfplumber
    text, tables = [], []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text.append(page.extract_text() or "")
            for t in page.extract_tables() or []:
                if t and len(t) > 1:
                    tables.append(t)
    return "\n".join(text), tables


def _text_and_tables(content: bytes) -> list[tuple[str, str, list]]:
    """Both engines; each one catches layouts the other misses."""
    out = []
    for name, fn in (("pymupdf", _pymupdf_extract), ("pdfplumber", _pdfplumber_extract)):
        try:
            t, tb = fn(content)
            out.append((name, t, tb))
        except ValueError:
            raise
        except Exception:
            continue
    if not out:
        raise ValueError("Could not open this PDF with PyMuPDF or pdfplumber.")
    return out


def _norm_date(s: str) -> str | None:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d %b, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s.replace("Sept", "Sep"), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _from_tables(tables) -> pd.DataFrame | None:
    frames = []
    for t in tables:
        header = [(c or "").strip() for c in t[0]]
        df = pd.DataFrame(t[1:], columns=header)
        cm = map_columns(df)
        if "date" in cm and "description" in cm and ({"amount"} & set(cm) or {"debit", "credit"} & set(cm)):
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def _from_bank_lines(text: str) -> pd.DataFrame | None:
    rows = []
    prev_bal = None
    for line in text.splitlines():
        line = line.strip()
        m = DATE_RE.match(line)
        if not m:
            continue
        d = _norm_date(m.group(1))
        if not d:
            continue
        rest = line[m.end():].strip()
        amts = list(AMT_RE.finditer(rest))
        if not amts:
            continue
        desc = rest[: amts[0].start()].strip(" -|")
        typ = None
        low = rest.lower()
        if re.search(r"\b(credit|cr)\b", low):
            typ = "Credit"
        elif re.search(r"\b(debit|dr)\b", low):
            typ = "Debit"
        vals = [float(a.group(1).replace(",", "")) for a in amts]
        amount, bal = (vals[-2], vals[-1]) if len(vals) >= 2 else (vals[0], None)
        if typ is None and bal is not None and prev_bal is not None:
            typ = "Credit" if bal > prev_bal else "Debit"
        if typ is None:
            typ = "Credit" if any(w in low for w in ("received", "deposit", "salary", "refund")) else "Debit"
        prev_bal = bal if bal is not None else prev_bal
        rows.append({"Date": d, "Description": re.sub(r"\b(credit|debit)\b", "", desc, flags=re.I).strip() or "Transaction",
                     "Type": typ, "Amount": abs(amount), "Balance": bal})
    return pd.DataFrame(rows) if len(rows) >= 3 else None


def _from_upi_app(text: str) -> pd.DataFrame | None:
    """CredWise-style: windows around 'Paid to' / 'Received from' lines, nearest date above, last ₹ amount."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    rows, cur_date = [], None
    for i, line in enumerate(lines):
        dm = DATE_RE.search(line)
        if dm and _norm_date(dm.group(1)):
            cur_date = _norm_date(dm.group(1))
        m = re.search(r"\b(paid to|received from|sent to)\s+(.+?)(?=\s+₹|\s+rs\.?|\s+\d{1,2}:\d{2}|$)", line, re.I)
        if not m:
            continue
        nxt = [lines[j] for j in range(i, min(len(lines), i + 6))]
        for k in range(1, len(nxt)):  # stop at the next transaction or next date header
            if re.search(r"\b(paid to|received from|sent to)\b", nxt[k], re.I) or (DATE_RE.match(nxt[k]) and not AMT_RE.search(nxt[k][DATE_RE.match(nxt[k]).end():])):
                nxt = nxt[:k]
                break
        window = " ".join(nxt)
        am = re.findall(r"(?:₹|rs\.?\s?)\s*([0-9][0-9,]*(?:\.\d{1,2})?)", window, re.I)
        if not am or not cur_date:
            continue
        rows.append({"Date": cur_date, "Description": f"UPI {m.group(1).title()} {m.group(2).strip()}",
                     "Type": "Credit" if m.group(1).lower() == "received from" else "Debit", "Amount": float(am[0].replace(",", ""))})
    return pd.DataFrame(rows) if rows else None


def pdf_to_csv_bytes(content: bytes) -> tuple[bytes, str]:
    try:
        engines = _text_and_tables(content)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not read PDF: {exc}") from exc
    if not any(t.strip() for _, t, _ in engines):
        raise ValueError("This PDF has no extractable text (it may be scanned). Please export a CSV from your bank instead.")
    # try every strategy on every engine; keep the result with the most transactions
    best = None
    for engine, text, tables in engines:
        for name, fn in (("table", lambda: _from_tables(tables)), ("bank-lines", lambda: _from_bank_lines(text)), ("upi-app", lambda: _from_upi_app(text))):
            try:
                df = fn()
            except Exception:
                df = None
            if df is not None and len(df) and (best is None or len(df) > len(best[0])):
                best = (df, f"{name}/{engine}")
    if best:
        return best[0].to_csv(index=False).encode(), best[1]
    raise ValueError("Couldn't find transactions in this PDF. Supported: statements with a transaction table, "
                     "dated statement lines with amounts, or UPI-app statements ('Paid to' / 'Received from').")
