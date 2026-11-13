"""Splitting expenses with friends: balances, suggestions from the statement, and the
"only my share counts" adjustment applied to analytics."""
from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

ME = "me"
SPLITTABLE = {"Food", "Entertainment", "Travel", "Shopping", "Transport", "Groceries"}


def even_shares(total: float, people: list[str]) -> dict[str, float]:
    n = len(people)
    base = round(total / n, 2)
    shares = {p: base for p in people}
    shares[people[0]] = round(total - base * (n - 1), 2)  # rounding remainder to the first person
    return shares


def balances(entries: list[dict], friends: list[dict]) -> list[dict]:
    """Positive = friend owes me; negative = I owe the friend."""
    net = defaultdict(float)
    count = defaultdict(int)
    for e in entries:
        payer = str(e["paid_by"])
        sh = {str(k): float(v) for k, v in (e["shares"] or {}).items()}
        if payer == "0":
            for k, v in sh.items():
                if k not in (ME, "0"):
                    net[k] += v
                    count[k] += 1
        else:
            net[payer] -= sh.get(ME, 0.0)
            count[payer] += 1
    out = []
    for f in friends:
        b = round(net.get(str(f["id"]), 0.0), 2)
        out.append({**f, "balance": b, "entries": count.get(str(f["id"]), 0),
                    "status": "owes you" if b > 0.5 else "you owe" if b < -0.5 else "settled"})
    return sorted(out, key=lambda r: -abs(r["balance"]))


_P2P = re.compile(r"\b(?:TO|FROM)\s+([A-Z][A-Z]+(?:\s+[A-Z])?)\b")


def person_from(desc: str) -> str | None:
    m = _P2P.search(desc.upper())
    if not m:
        return None
    return " ".join(w.capitalize() for w in m.group(1).split())


def suggestions(df: pd.DataFrame, linked: set[int], friends: list[dict], limit: int = 8) -> list[dict]:
    """Transactions that look shared: money to/from people, and big group-style spends."""
    if df is None or df.empty:
        return []
    names = {f["name"].lower().split()[0]: f for f in friends}
    out = []
    recent = df[~df["id"].isin(linked)].sort_values("date", ascending=False)
    for r in recent.itertuples():
        reason, who = None, person_from(r.description)
        friend = names.get((who or "").lower().split()[0]) if who else None
        if r.type == "credit" and r.credit_kind == "p2p_in":
            reason = f"{who or 'Someone'} sent you money. Was it paying you back?"
            kind = "settlement_in"
        elif r.type == "debit" and (r.subcategory or "") == "Person-to-person":
            reason = f"You sent money to {who or 'a person'}. A loan or your share of something?"
            kind = "settlement_out"
        elif r.type == "debit" and r.category in SPLITTABLE and r.amount >= 600 and not r.excluded:
            reason = f"{r.category} payment of ₹{r.amount:,.0f}. Shared with friends?"
            kind = "expense"
        else:
            continue
        label = (f"{'From' if r.type == 'credit' else 'To'} {who}" if who and kind != "expense" else r.merchant)
        out.append({"txn_id": int(r.id), "date": str(r.date.date()), "description": r.description, "merchant": label,
                    "amount": float(r.amount), "type": r.type, "kind": kind, "reason": reason, "person": who,
                    "friend_id": friend["id"] if friend else None})
        if len(out) >= limit:
            break
    return out


def apply_my_share(df: pd.DataFrame, entries: list[dict]) -> pd.DataFrame:
    """Linked expenses count only my share in analytics; linked settlements stop looking like income/spend."""
    if df is None or df.empty or not entries:
        return df
    df = df.copy()
    df["full_amount"] = df["amount"]
    by_txn = {e["txn_id"]: e for e in entries if e.get("txn_id")}
    for i, row in df[df["id"].isin(by_txn)].iterrows():
        e = by_txn[row["id"]]
        if e["kind"] == "expense" and row["type"] == "debit":
            mine = float((e["shares"] or {}).get(ME, 0.0))
            df.at[i, "amount"] = mine
            if mine <= 0:
                df.at[i, "excluded"] = True
        elif e["kind"] == "settlement":
            if row["type"] == "credit":
                df.at[i, "credit_kind"] = "settlement"
            else:
                df.at[i, "excluded"] = True  # repaying a friend is not new spending
    return df


# ---------------- payback detection ----------------
_FRAC = {2: "half", 3: "⅔", 4: "¾", 5: "⅘", 6: "⅚"}


def paybacks(df: pd.DataFrame, linked: set[int], dismissed: set[str] | None = None, window: int = 10, days: int = 14) -> list[dict]:
    """After an unusually large payment, look at the next `window` incoming person-to-person transfers
    (within `days`). If some of them add up to the friends' part of an even split (1/2, 2/3, 3/4 ... of
    the bill) - or several people each sent about the same 1/k share - they were probably paying the
    user back. Returns suggestions the user confirms with one tap."""
    from itertools import combinations
    if df is None or df.empty:
        return []
    dismissed = dismissed or set()
    debits = df[(df["type"] == "debit") & (~df["excluded"]) & (df["subcategory"].fillna("") != "Own-account transfer")]
    if debits.empty:
        return []
    usual = float(debits["amount"].median())
    big = debits[(debits["amount"] >= max(500.0, 2.5 * usual)) & (~debits["category"].isin(["Accommodation & Utilities", "Education", "Transfers & Other"]))
                 & (~debits["id"].isin(linked))]
    credits = df[(df["type"] == "credit") & (df["credit_kind"].isin(["p2p_in", "uncertain"])) & (~df["id"].isin(linked))].sort_values(["date", "row"])
    used: set[int] = set()
    out = []
    for d in big.sort_values("date", ascending=False).itertuples():
        if f"payback_{d.id}" in dismissed:
            continue
        after = credits[(credits["date"] >= d.date) & (credits["date"] <= d.date + pd.Timedelta(days=days)) & (~credits["id"].isin(used))]
        after = after[(after["date"] > d.date) | (after["row"] > d.row)].head(window)
        if after.empty:
            continue
        best = None
        cand = list(after.itertuples())
        for size in range(1, min(5, len(cand)) + 1):
            for combo in combinations(cand, size):
                s = sum(c.amount for c in combo)
                if s > d.amount * 1.02 or s < d.amount * 0.3:
                    continue
                for k in range(2, 7):
                    target = d.amount * (k - 1) / k
                    err = abs(s - target) / target
                    each = d.amount / k
                    # each payer close to one share (several friends) or one payer covering the whole friends' part
                    even = all(abs(c.amount - each) / each < 0.08 for c in combo) or size == 1
                    if err < 0.05 and even and size <= k - 1:
                        score = err + 0.02 * abs((k - 1) - size)
                        if not best or score < best[0]:
                            best = (score, combo, k, s)
        if not best:
            continue
        _, combo, k, s = best
        used |= {int(c.id) for c in combo}
        payers = [{"txn_id": int(c.id), "amount": float(c.amount), "date": str(c.date.date()), "description": c.description,
                   "person": person_from(c.description)} for c in combo]
        names = [p["person"] or "someone" for p in payers]
        who = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
        out.append({
            "kind": "payback", "txn_id": int(d.id), "date": str(d.date.date()), "merchant": d.merchant, "description": d.description,
            "amount": float(d.amount), "credits": payers, "total_back": round(s, 2), "split_ways": k, "my_share": round(d.amount - s, 2),
            "reason": (f"You paid ₹{d.amount:,.0f} at {d.merchant} on {d.date:%d %b}, well above your usual ₹{usual:,.0f}. In the next few days "
                       f"₹{s:,.0f} came in from {who}: that's {_FRAC.get(k, f'{k - 1}/{k}')} of the bill, as if it was split {k} ways. "
                       f"Were they paying you back?"),
        })
    return out
