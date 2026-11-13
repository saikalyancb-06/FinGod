"""Reading rupee amounts the way people type them: "₹1,200", "Rs 25k", "1.5 lakh", "2k", "3L", "1 cr"."""
from __future__ import annotations

import re

UNITS = {"k": 1e3, "thousand": 1e3, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
         "cr": 1e7, "crore": 1e7, "crores": 1e7}
_NUM = r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?|l|cr|crores?)?(?![a-z0-9])"
_AMOUNT = re.compile(r"(₹|rs\.?|inr)?\s*" + _NUM, re.I)
_BUDGET_CUE = re.compile(r"(?:₹|rs\.?|inr|under|below|around|about|budget(?:\s+(?:of|is))?|max(?:imum)?|upto|up to|within|less than)\s*(?:₹|rs\.?|inr)?\s*" + _NUM, re.I)


def _value(num: str, unit: str | None) -> float:
    return float(num.replace(",", "")) * UNITS.get((unit or "").lower(), 1.0)


def amounts(text: str) -> list[tuple[float, bool]]:
    """All amounts in the text as (value, explicit) where explicit = has ₹/Rs or a k/lakh/cr unit."""
    out = []
    for m in _AMOUNT.finditer(text):
        try:
            out.append((_value(m.group(2), m.group(3)), bool(m.group(1) or m.group(3))))
        except ValueError:
            continue
    return out


def price(text: str, min_plain: float = 100) -> float | None:
    """The price in a question like 'Can I afford a ₹1.5 lakh laptop?'. Prefers explicit amounts;
    a bare number counts only if it's at least `min_plain` (so 'iPhone 15' isn't ₹15)."""
    found = amounts(text)
    for v, explicit in found:
        if explicit:
            return v
    for v, _ in found:
        if v >= min_plain and not (2000 <= v <= 2100 and float(v).is_integer()):  # not a year
            return v
    return None


def budget(text: str) -> float | None:
    """A budget in a chat message: needs a cue (₹, under, around, budget...), a unit (2k), or the
    whole message being a number. 'for 4 people' and 'UK 10' are not budgets."""
    t = text.strip()
    m = _BUDGET_CUE.search(t)
    if m:
        return _value(m.group(1), m.group(2))
    whole = re.fullmatch(r"\s*(?:₹|rs\.?|inr)?\s*" + _NUM + r"\s*(?:rs|rupees|/-)?\s*", t, re.I)
    if whole:
        return _value(whole.group(1), whole.group(2))
    for m in _AMOUNT.finditer(t):
        if m.group(3):  # '2k shoes'
            return _value(m.group(2), m.group(3))
    return None


def party_size(text: str) -> int | None:
    m = re.search(r"(\d{1,2})\s*(?:people|persons|ppl|friends|guests|of us|pax)\b|(?:group of|we are|we're|table for)\s*(\d{1,2})\b", text.lower())
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    return n if 1 <= n <= 50 else None
