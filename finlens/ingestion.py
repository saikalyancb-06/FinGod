"""Statement ingestion pipeline:
CSV -> validation & schema mapping -> date/amount/type normalization ->
duplicate, reversal & refund handling -> merchant identification & categorization.
"""
from __future__ import annotations

import hashlib
from datetime import date

import pandas as pd

from shared.normalize import ValidationReport, clean_description, map_columns, normalize_type, parse_amount, parse_dates, read_csv_bytes

from .categorizer import categorize, is_essential


def parse_statement(content: bytes, overrides: dict[str, str] | None = None, default_year: int | None = None) -> tuple[list[dict], dict]:
    rep = ValidationReport()
    df = read_csv_bytes(content)
    rep.rows_in = len(df)
    cmap = map_columns(df)
    rep.column_map = cmap
    if "date" not in cmap or "description" not in cmap:
        raise ValueError(f"Statement needs Date and Description columns. Found: {list(df.columns)}")
    if not ("amount" in cmap or "debit" in cmap or "credit" in cmap):
        raise ValueError("Statement needs an Amount column, or Debit/Credit columns")

    dates = parse_dates(df[cmap["date"]], default_year=default_year or date.today().year)
    recs = []
    for i, row in df.iterrows():
        d = dates.iloc[i]
        desc = clean_description(row[cmap["description"]])
        if pd.isna(d):
            rep.errors.append(f"Row {i+2}: unreadable date '{row[cmap['date']]}' - skipped")
            continue
        typ, amt = None, None
        if "debit" in cmap or "credit" in cmap:
            dr = parse_amount(row[cmap["debit"]]) if "debit" in cmap else None
            cr = parse_amount(row[cmap["credit"]]) if "credit" in cmap else None
            if dr:
                typ, amt = "debit", abs(dr)
            elif cr:
                typ, amt = "credit", abs(cr)
        if amt is None and "amount" in cmap:
            a = parse_amount(row[cmap["amount"]])
            if a is not None:
                t = normalize_type(row[cmap["type"]]) if "type" in cmap else None
                if t is None:
                    t = "debit" if a < 0 else "credit" if "type" not in cmap else None
                typ, amt = t, abs(a)
        if typ is None or amt is None or amt == 0:
            rep.errors.append(f"Row {i+2}: could not determine amount/type - skipped")
            continue
        status = "posted"
        if "status" in cmap and str(row[cmap["status"]]).strip().lower() in {"pending", "authorised", "authorized", "hold"}:
            status = "pending"
        bal = parse_amount(row[cmap["balance"]]) if "balance" in cmap else None
        recs.append({"date": d.date(), "description": desc, "type": typ, "amount": round(float(amt), 2),
                     "balance": bal, "status": status, "row": i + 2})

    # --- duplicates (identical date/description/type/amount/balance) ---
    seen, deduped, dupes = set(), [], 0
    for r in recs:
        k = (r["date"], r["description"].lower(), r["type"], r["amount"], r["balance"])
        if k in seen:
            dupes += 1
            continue
        seen.add(k)
        deduped.append(r)
    if dupes:
        rep.warnings.append(f"{dupes} exact duplicate row(s) removed")

    # --- categorize ---
    for r in deduped:
        c = categorize(r["description"], r["type"], overrides)
        r.update(c)
        r["is_essential"] = r["type"] == "debit" and is_essential(c["category"], c["subcategory"])
        r["excluded"] = False
        r["note"] = None
        r["uid"] = hashlib.sha1(f"{r['date']}|{r['description']}|{r['type']}|{r['amount']}|{r['row']}".encode()).hexdigest()[:16]

    # --- reversal pairing: a reversal/refund credit matching a debit of same amount within 7 days ---
    debits = [r for r in deduped if r["type"] == "debit"]
    for cr in [r for r in deduped if r["type"] == "credit" and r["credit_kind"] == "refund"]:
        for db in debits:
            if db["excluded"] or db["amount"] != cr["amount"]:
                continue
            if 0 <= (cr["date"] - db["date"]).days <= 7 and (cr["merchant_key"].split()[:1] == db["merchant_key"].split()[:1] or "revers" in cr["description"].lower()):
                db["excluded"] = cr["excluded"] = True
                db["note"] = f"Reversed by credit on {cr['date']}"
                cr["note"] = f"Reversal of debit on {db['date']}"
                break
    rev = sum(1 for r in deduped if r["excluded"]) // 2
    if rev:
        rep.warnings.append(f"{rev} debit/reversal pair(s) netted out of spending totals")
    unc = sum(1 for r in deduped if r["category"] == "Uncertain")
    if unc:
        rep.warnings.append(f"{unc} transaction(s) labelled Uncertain - please review and correct")
    rep.rows_out = len(deduped)
    if not deduped:
        raise ValueError("No valid transactions found. " + "; ".join(rep.errors[:5]))
    return sorted(deduped, key=lambda r: (r["date"], r["row"])), rep.as_dict()
