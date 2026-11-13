"""Live money input without bank passwords.

* parse(): reads the transaction SMS Indian banks send for every UPI / card / NEFT / ATM movement
  (paste one or many). Extracts amount, debit/credit, counterparty, balance, date and time.
  Collect-request / OTP style messages are recognised as possible scams and never booked.
* DemoDay: a realistic "live day" for the signed-in user (built from their own merchants and
  profile) that emits bank-style SMS one at a time, so every Spend Guard feature can be seen live.
"""
from __future__ import annotations

import hashlib
import random
import re
from datetime import date, datetime, timedelta

_AMT = r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)"
_DEBIT = re.compile(r"\b(debited|spent|sent|paid|withdrawn|purchase|deducted|debit)\b", re.I)
_CREDIT = re.compile(r"\b(credited|received|deposited|refund(?:ed)?|credit)\b", re.I)
_SCAM = re.compile(r"(requested|collect request|request(?:ing)? money|approve (?:only|the)|enter (?:your )?(?:upi )?pin to receive|"
                   r"won|lottery|kyc (?:update|expired)|share (?:the )?otp)", re.I)
_BAL = re.compile(r"(?:avl\.?\s*bal(?:ance)?|available\s+balance|a/c\s+bal(?:ance)?|bal(?:ance)?)\s*(?:is|:|-)?\s*(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_LIMIT = re.compile(r"avl\.?\s*(?:lmt|limit)", re.I)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_PARTY = [
    re.compile(r"\bto\s+vpa\s+([a-z0-9._-]+)@", re.I),
    re.compile(r"\bat\s+([A-Za-z0-9 &'._-]{2,40}?)\s+(?:on|via|using|ref|txn|\.|$)", re.I),
    re.compile(r"\bto\s+([A-Za-z0-9 &'._-]{2,40}?)\s+(?:on|via|ref|upi|txn|\(|\.|-|$)", re.I),
    re.compile(r"\b(?:from|by)\s+(?:neft|imps|upi|rtgs)?\s*(?:from\s+)?([A-Za-z0-9 &'._-]{2,40}?)\s*(?:on|via|ref|\.|-|avl|$)", re.I),
    re.compile(r"\binfo:?\s*(?:upi/)?([A-Za-z0-9 &'._/-]{2,40})", re.I),
]


def _date(text: str, today: date) -> date:
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{1,2})[-/ ]([A-Za-z]{3})[a-z]*[-/ ](\d{2,4})\b", text)
    if m and m.group(2).lower() in _MONTHS:
        y = int(m.group(3)); y = y + 2000 if y < 100 else y
        return date(y, _MONTHS[m.group(2).lower()], int(m.group(1)))
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b", text)
    if m:
        y = int(m.group(3)); y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return today


def _time(text: str) -> str | None:
    m = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)(?:[:.]([0-5]\d))?\s*(am|pm)?\b", text, re.I)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(4):
        h = h % 12 + (12 if m.group(4).lower() == "pm" else 0)
    return f"{h:02d}:{mi:02d}"


def _party(text: str) -> str | None:
    for p in _PARTY:
        m = p.search(text)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" .-/")
            name = re.sub(r"^(a/c|ac|acct|your|xx+\d*)\b.*", "", name, flags=re.I).strip()
            if name and not re.fullmatch(r"[x*\d ]+", name, re.I):
                return name[:40]
    return None


def split_messages(blob: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", blob or "") if p.strip()]
    if len(parts) == 1 and blob.count("\n") >= 1:
        lines = [x.strip() for x in blob.splitlines() if x.strip()]
        if all(re.search(_AMT, x, re.I) for x in lines):  # one SMS per line
            return lines
    return parts


def parse(text: str, today: date | None = None) -> dict:
    """One SMS -> {"kind": "txn"|"scam"|"ignored", ...}."""
    today = today or date.today()
    t = " ".join((text or "").split())
    strong = re.search(r"has requested|requested money|collect request|on approval|approve only|enter (?:your )?(?:upi )?pin to receive", t, re.I)
    if strong or (_SCAM.search(t) and not re.search(r"\b(debited|credited|spent)\b", t, re.I)):
        amt = re.search(_AMT, t, re.I)
        return {"kind": "scam", "text": t, "amount": float(amt.group(1).replace(",", "")) if amt else None,
                "reason": "This looks like a payment request or a prize/KYC message. Approving a collect request or entering your UPI PIN "
                          "SENDS money; you never need a PIN to receive money."}
    m = re.search(_AMT, t, re.I)
    is_debit, is_credit = _DEBIT.search(t), _CREDIT.search(t)
    if not m or not (is_debit or is_credit) or re.search(r"\botp\b", t, re.I):
        return {"kind": "ignored", "text": t, "reason": "No completed debit or credit found (OTPs, offers and balance messages are skipped)."}
    # "debited ... and credited to a/c X" is a debit; decide by whichever verb comes first
    typ = "debit" if is_debit and (not is_credit or is_debit.start() < is_credit.start()) else "credit"
    amount = float(m.group(1).replace(",", ""))
    bal = None
    if not _LIMIT.search(t):
        b = _BAL.search(t)
        if b:
            bal = float(b.group(1).replace(",", ""))
    party = _party(t)
    channel = ("ATM" if re.search(r"\batm\b|withdraw", t, re.I) else "Card" if re.search(r"\bcard\b", t, re.I)
               else "NEFT" if re.search(r"\bneft\b", t, re.I) else "IMPS" if re.search(r"\bimps\b", t, re.I) else "UPI")
    if party and channel == "Card" and typ == "debit":
        desc = f"POS {party.upper()}"  # card purchase at a merchant
    else:
        desc = (f"{channel} {'TO' if typ == 'debit' else 'FROM'} {party.upper()}" if party and party.upper() != channel
                else f"{channel} {typ.upper()}")
    if channel == "ATM" and typ == "debit":
        desc = "ATM CASH WITHDRAWAL"
    if re.search(r"charge|chg|fee|penalty", t, re.I):
        desc = re.sub(r"\s+", " ", re.sub(r"(?i).*?((?:sms|atm|min\.?|annual|late|non)[a-z .]*?(?:charges?|chgs?|fee|penalty)).*", r"\1", t)).upper()[:60] or desc
    d = _date(t, today)
    iso = re.search(r"\b\d{4}-\d{2}-\d{2}[: T](\d{2}:\d{2})", t)
    tm = iso.group(1) if iso else _time(re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b", " ", t))
    uid = hashlib.sha1(f"sms|{t}".encode()).hexdigest()[:16]
    return {"kind": "txn", "uid": uid, "date": d, "time": tm, "type": typ, "amount": amount, "balance": bal,
            "description": desc, "party": party, "channel": channel, "text": t}


# ---------------- demo "live day" ----------------
_DEMO_SCRIPT = [
    # (hour, minute, kind, sms template, amount range)  kind: need | flex | want | charge | dup | fraud | scam | income
    (8, 10, "need", "Rs.{a} debited from A/c XX4821 on {d} {t} to VPA bmrcl.metro@okaxis (UPI Ref 4{r}). Avl Bal Rs {b} -SBI", (30, 60)),
    (8, 55, "flex", "Sent Rs.{a} From HDFC Bank A/C *4821 To CHAI POINT On {d} {t} Ref 3{r}. Avl Bal Rs {b}", (40, 90)),
    (11, 30, "charge", "Rs.{a} debited from A/c XX4821 on {d} {t} towards SMS ALERT CHARGES for the quarter. Avl Bal Rs {b}", (15, 25)),
    (13, 5, "need", "Rs.{a} debited from A/c XX4821 on {d} {t} to VPA college.canteen@ybl (UPI Ref 7{r}). Avl Bal Rs {b}", (70, 140)),
    (15, 40, "want", "INR {a} spent on SBI Debit Card XX4821 at LENSKART SUNGLASSES on {d} {t}. Avl Bal Rs {b}", (1199, 1999)),
    (15, 41, "dup", "INR {a} spent on SBI Debit Card XX4821 at LENSKART SUNGLASSES on {d} {t}. Avl Bal Rs {b}", None),
    (17, 20, "income", "Dear Customer, Rs {a} credited to your A/c XX4821 on {d} {t} by UPI from RAHUL K - movie share. Avl Bal Rs {b}", (200, 300)),
    (18, 45, "want", "Rs.{a} debited from A/c XX4821 on {d} {t} to VPA dream11@icici (UPI Ref 2{r}). Avl Bal Rs {b}", (300, 600)),
    (20, 30, "flex", "Rs.{a} debited from A/c XX4821 on {d} {t} to VPA swiggy@icici (UPI Ref 9{r}). Avl Bal Rs {b}", (280, 520)),
    (21, 15, "scam", "PRIYA S has requested money from you on Google Pay. Rs {a} will be debited from your account on approval. Approve only if you know them.", (1500, 3000)),
    (23, 50, "want", "INR {a} spent on SBI Debit Card XX4821 at AMAZON on {d} {t}. Avl Bal Rs {b}", (499, 1299)),
    (23, 58, "fraud", "INR {a} spent on SBI Debit Card XX4821 at QUICKPAY GLOBAL SERVICES on {d} {t}. Avl Bal Rs {b}", (2400, 4800)),
]


class DemoDay:
    """Deterministic per (user, day) so the demo is repeatable; amounts adapt to the user's balance."""

    def __init__(self, uid: int, day: date, step: int, balance: float | None):
        self.rng = random.Random(f"{uid}-{day}-demo")
        self.day, self.step = day, step
        self.balance = balance if balance is not None else 4500.0
        self._last_amount = None

    @staticmethod
    def length() -> int:
        return len(_DEMO_SCRIPT)

    def sms(self) -> tuple[str, str] | None:
        if self.step >= len(_DEMO_SCRIPT):
            return None
        rng = self.rng
        amounts = []
        for h, mi, kind, tpl, rngs in _DEMO_SCRIPT:  # replay amounts so a duplicate repeats the previous one
            a = amounts[-1] if kind == "dup" else round(rng.uniform(*rngs)) if rngs else 0
            amounts.append(a)
        h, mi, kind, tpl, _ = _DEMO_SCRIPT[self.step]
        a = amounts[self.step]
        day = self.day + timedelta(days=1) if h < 6 else self.day
        if kind not in ("income", "scam", "dup"):
            a = max(20, min(a, round(self.balance * 0.6)))  # the demo never spends money the user doesn't have
        bal = self.balance + (a if kind == "income" else 0 if kind == "scam" else -a)
        txt = tpl.format(a=f"{a:,.2f}", d=day.strftime("%d-%m-%y"), t=f"{h:02d}:{mi:02d}:{rng.randint(0, 59):02d}",
                         r=rng.randint(10 ** 8, 10 ** 9), b=f"{max(bal, 0):,.2f}")
        return txt, kind
