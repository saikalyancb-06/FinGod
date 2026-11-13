"""Spend Guard: the personal layer that watches every rupee as it leaves the account.

Everything here is computed (deterministic) and personalised:
  * need / want: the user's own rules > profile (diet, orders per week, budget per meal, occupation)
    > their own history (5th delivery this week, second clothing buy this month, unusual meal size)
    > what they told us they regretted before
  * cost lenses: goal days, hours of work, days of pocket money, % of safe-to-spend, or a unit the user
    picks ("movie tickets", "plates of biryani")
  * coach tone: gentle | straight | tough love; quiet hours; sensitivity; minimum alert amount

No LLM is involved, so no figure can be invented. Text is translated by the API layer when needed.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import quote

import pandas as pd

from . import analytics as A
from . import goals as G

NATURE_LABEL = {"need": "Need", "flex": "Depends", "want": "Want", "charge": "Fee"}
SUB_NATURE = {
    "Groceries": "need", "Rent": "need", "Hostel": "need", "Electricity": "need", "Mobile/Internet": "need", "Water": "need",
    "Gas": "need", "Pharmacy": "need", "Medical": "need", "Fees": "need", "Books": "need", "Materials": "need", "Courses": "need",
    "Metro": "need", "Bus": "need", "Train": "need", "Fuel": "need", "Tolls": "need", "Parking": "flex",
    "Loan repayment": "need", "Card repayment": "need", "Cash withdrawal": "flex", "Person-to-person": "flex",
    "Food delivery": "flex", "Restaurant": "flex", "Cafe": "flex", "Taxi": "flex",
    "Clothing": "flex", "Online retail": "flex", "Sports": "flex", "Grooming": "flex", "Beauty": "want", "Electronics": "want",
    "Accessories": "want", "Gadgets": "want", "Jewellery": "want", "Subscription": "want", "Movies & events": "want",
    "Gaming": "want", "Alcohol & smoking": "want", "Betting & fantasy": "want", "Bank charges": "charge",
}
CAT_NATURE = {"Accommodation & Utilities": "need", "Healthcare": "need", "Education": "need", "Transportation": "need",
              "Food": "flex", "Shopping": "want", "Entertainment": "want", "Transfers & Other": "flex", "Uncertain": "flex"}
BILL_SUBS = {"Rent", "Hostel", "Electricity", "Mobile/Internet", "Water", "Gas", "Fees", "Loan repayment", "Card repayment"}
SUB_GROUPS = {  # for the settings grid
    "Food": ["Groceries", "Restaurant", "Food delivery", "Cafe"],
    "Getting around": ["Metro", "Bus", "Train", "Fuel", "Taxi", "Parking", "Tolls"],
    "Home & bills": ["Rent", "Hostel", "Electricity", "Mobile/Internet", "Water", "Gas"],
    "Health & study": ["Pharmacy", "Medical", "Fees", "Books", "Materials", "Courses"],
    "Shopping": ["Clothing", "Online retail", "Sports", "Electronics", "Gadgets", "Accessories", "Beauty", "Grooming", "Jewellery"],
    "Fun": ["Movies & events", "Subscription", "Gaming", "Alcohol & smoking", "Betting & fantasy"],
    "Money moves": ["Cash withdrawal", "Person-to-person", "Loan repayment", "Card repayment"],
}

FESTIVALS = {  # approximate dates; the user can edit them
    "diwali": ("Diwali", "🪔", ["2026-11-08", "2027-10-29", "2028-10-17"], (2000, 6000)),
    "christmas": ("Christmas", "🎄", ["2026-12-25", "2027-12-25", "2028-12-25"], (1000, 4000)),
    "new_year": ("New Year", "🎆", ["2026-12-31", "2027-12-31"], (1000, 3000)),
    "sankranti": ("Makar Sankranti / Pongal", "🪁", ["2027-01-14", "2028-01-15"], (500, 2000)),
    "holi": ("Holi", "🎨", ["2027-03-22", "2028-03-11"], (700, 2500)),
    "eid": ("Eid al-Fitr", "🌙", ["2027-03-10", "2028-02-27"], (1500, 5000)),
    "ugadi": ("Ugadi", "🌿", ["2027-04-07", "2028-03-27"], (700, 2500)),
    "raksha_bandhan": ("Raksha Bandhan", "🧵", ["2027-08-17", "2028-08-05"], (700, 2500)),
    "onam": ("Onam", "🌼", ["2027-09-12", "2028-08-31"], (1000, 3000)),
    "ganesh": ("Ganesh Chaturthi", "🐘", ["2027-09-04", "2028-08-23"], (700, 2500)),
    "durga_puja": ("Durga Puja / Dussehra", "🏹", ["2027-10-09", "2028-09-27"], (1000, 4000)),
}
LANG_FESTIVALS = {"kn": ["ugadi", "diwali", "ganesh"], "hi": ["diwali", "holi", "raksha_bandhan"], "en": ["diwali", "christmas", "new_year"]}
OTT = {"video": ["netflix", "hotstar", "prime", "sonyliv", "zee5", "jiocinema"], "music": ["spotify", "youtube", "jiosaavn", "gaana", "apple music", "wynk"]}
FEE_HINTS = {
    "sms": "SMS alert charges: most banks let you switch to free app notifications or e-mail alerts.",
    "atm": "ATM charges: use your own bank's ATM within the free monthly limit, or pay by UPI.",
    "min": "Minimum-balance penalty: keep the minimum, or ask for a zero-balance (BSBDA) account.",
    "late": "Late fees: set autopay for the full due amount a few days before the due date.",
    "annual": "Annual card fee: many banks waive it if you ask or spend above a limit.",
    "convenience": "Convenience fees: pay from the provider's own app/site or by UPI where it's free.",
}
TONES = {
    "gentle": {"want_title": "Small check-in: {amt} at {m}", "want_lead": "This one looks like a want",
               "end": "Totally fine if it was planned. 💛", "limit": "You've used today's comfortable amount. Maybe slow down till tomorrow? 🌙"},
    "straight": {"want_title": "{amt} at {m} is a want", "want_lead": "Want, not need",
                 "end": "", "limit": "Today's limit is used up."},
    "tough": {"want_title": "{amt} on {m}?", "want_lead": "That's a want",
              "end": "Was it really worth it? 🤨", "limit": "Limit crossed. Wallet's closed for today. 🔒"},
}


# ---------------- settings ----------------
def defaults(prof: dict | None) -> dict:
    prof = prof or {}
    occ = prof.get("occupation")
    student = occ in ("student", "both", "between_jobs")
    earns = float(prof.get("salary") or 0) > 0
    pr = set(prof.get("priorities") or [])
    lang = (prof.get("language") or "en")[:2]
    strict = bool(pr & {"save", "smarter", "purchase"})
    return {
        "tone": "gentle" if student else "straight",
        "sensitivity": "strict" if strict else "balanced",
        "min_alert": 150 if student else 300,
        "quiet_start": "23:30", "quiet_end": "07:00",
        "notify_browser": True, "voice_summary": True, "summary_time": "21:30",
        "daily_limit_mode": "auto", "daily_limit": 0, "rollover": True,
        "caps": {},
        "cost_lens": (["goal", "pocket", "room"] if student and not earns else ["goal", "work", "room"]),
        "my_unit": {"name": "plates of biryani", "price": 220} if student else {"name": "movie tickets", "price": 300},
        "work_hours_month": 176,
        "delivery_per_week": int(prof.get("orders_per_week") or (3 if student else 4)),
        "meal_comfort": float(prof.get("food_budget_per_meal") or 0),
        "clothing_need_max": 1500 if student else 3000,
        "flex_check_above": 1000 if student else 2500,
        "cooloff_amount": 800 if student else 2000, "cooloff_hours": 48,
        "want_tax_pct": 10, "roundup_to": 10 if student else 0, "jar_goal_id": None,
        "emergency_months": 1 if student else (6 if prof.get("self_employed") else 3), "other_savings": 0,
        "festivals": [], "festival_budgets": {}, "events": [], "reserve_events": True,
        "suggested_festivals": LANG_FESTIVALS.get(lang, LANG_FESTIVALS["en"]),
        "buddy": {"name": "", "share": "totals"},
        "owed_remind_days": 7, "leak_max": 250,
        "fraud_alerts": True, "big_new_merchant": 2000,
        "subs_review": {},
    }


_SETTING_TYPES = {
    "tone": ("enum", list(TONES)), "sensitivity": ("enum", ["relaxed", "balanced", "strict"]),
    "min_alert": ("num", 0, 100000), "quiet_start": ("time",), "quiet_end": ("time",),
    "notify_browser": ("bool",), "voice_summary": ("bool",), "summary_time": ("time",),
    "daily_limit_mode": ("enum", ["auto", "fixed"]), "daily_limit": ("num", 0, 1e6), "rollover": ("bool",),
    "caps": ("dict_num",), "cost_lens": ("list", ["goal", "work", "pocket", "room", "unit", "daily"]),
    "my_unit": ("unit",), "work_hours_month": ("num", 20, 400), "delivery_per_week": ("num", 0, 50),
    "meal_comfort": ("num", 0, 10000), "clothing_need_max": ("num", 0, 1e6), "flex_check_above": ("num", 0, 1e6),
    "cooloff_amount": ("num", 0, 1e7), "cooloff_hours": ("num", 1, 24 * 30),
    "want_tax_pct": ("num", 0, 100), "roundup_to": ("num", 0, 1000), "jar_goal_id": ("id",),
    "emergency_months": ("num", 0, 24), "other_savings": ("num", 0, 1e9),
    "festivals": ("list", list(FESTIVALS)), "festival_budgets": ("dict_num",), "events": ("events",), "reserve_events": ("bool",),
    "buddy": ("buddy",), "owed_remind_days": ("num", 1, 90), "leak_max": ("num", 20, 2000),
    "fraud_alerts": ("bool",), "big_new_merchant": ("num", 100, 1e7), "subs_review": ("dict_str",),
}


def merge_settings(stored: dict | None, prof: dict | None, patch: dict | None = None) -> dict:
    out = {**defaults(prof), **{k: v for k, v in (stored or {}).items() if k in _SETTING_TYPES or k.startswith("_")}}
    for k, v in (patch or {}).items():
        spec = _SETTING_TYPES.get(k)
        if not spec:
            continue
        t = spec[0]
        try:
            if t == "enum" and v in spec[1]:
                out[k] = v
            elif t == "num":
                out[k] = min(max(float(v or 0), spec[1]), spec[2])
            elif t == "bool":
                out[k] = bool(v)
            elif t == "time" and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(v)):
                out[k] = str(v)
            elif t == "list":
                out[k] = [x for x in (v or []) if x in spec[1]][:12]
            elif t == "dict_num":
                out[k] = {str(a)[:60]: float(b) for a, b in (v or {}).items() if float(b or 0) > 0}
            elif t == "dict_str":
                out[k] = {str(a)[:120]: str(b)[:20] for a, b in (v or {}).items()}
            elif t == "unit":
                out[k] = {"name": str(v.get("name") or "")[:40], "price": max(1.0, float(v.get("price") or 1))} if v else None
            elif t == "id":
                out[k] = int(v) if v not in (None, "") else None
            elif t == "buddy":
                out[k] = {"name": str(v.get("name") or "")[:40], "share": v.get("share") if v.get("share") in ("totals", "detailed") else "totals"}
            elif t == "events":
                evs = []
                for e in (v or [])[:30]:
                    d = date.fromisoformat(str(e["date"]))
                    evs.append({"id": str(e.get("id") or f"ev{len(evs) + 1}")[:20], "name": str(e["name"])[:60], "date": str(d),
                                "amount": max(0.0, float(e.get("amount") or 0)), "emoji": str(e.get("emoji") or "📌")[:4],
                                "yearly": bool(e.get("yearly"))})
                out[k] = evs
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
    return out


def _mins(hhmm: str | None) -> int | None:
    if not hhmm:
        return None
    h, m = hhmm[:5].split(":")
    return int(h) * 60 + int(m)


def in_quiet(time_str: str | None, s: dict) -> bool:
    t, a, b = _mins(time_str), _mins(s["quiet_start"]), _mins(s["quiet_end"])
    if t is None or a is None or b is None:
        return False
    return (a <= t or t < b) if a > b else (a <= t < b)


# ---------------- need / want ----------------
class Classifier:
    """Decides need / flex / want / charge for each debit, explaining why, using the user's rules,
    profile, history and regrets."""

    def __init__(self, df: pd.DataFrame, settings: dict, rules: list[dict], regrets: dict, prof: dict | None):
        self.s, self.prof = settings, prof or {}
        self.rules = {(r["scope"], r["key"]): r["nature"] for r in rules}
        self.regrets = regrets  # key -> {"regret": n, "worth": n}
        sp = A.spend_df(df) if df is not None and not df.empty else pd.DataFrame()
        self.sp = sp
        self.med = {}
        if len(sp):
            for sub, g in sp.groupby(sp["subcategory"].fillna("")):
                self.med[sub] = float(g["amount"].median())

    def _week_count(self, t: dict, sub: str) -> int:
        sp = self.sp
        if not len(sp):
            return 1
        d = pd.Timestamp(t["date"])
        wk = d - pd.Timedelta(days=d.weekday())
        rows = sp[(sp["subcategory"] == sub) & (sp["date"] >= wk) & (sp["date"] <= d)]
        before = rows[(rows["date"] < d) | (rows["row"] <= t.get("row", 10 ** 9))]
        return max(1, len(before))

    def classify(self, t: dict) -> dict | None:
        if t.get("type") != "debit" or t.get("excluded") or (t.get("subcategory") or "") == "Own-account transfer":
            return None
        mk, sub, cat, amt = t.get("merchant_key") or "", t.get("subcategory") or "", t.get("category") or "", float(t["amount"])
        for scope, key in (("merchant", mk), ("subcategory", sub), ("category", cat)):
            if (scope, key) in self.rules:
                n = self.rules[(scope, key)]
                return {"nature": n, "reason": f"your rule for {key if scope != 'merchant' else (t.get('merchant') or key)}", "source": "you"}
        base = SUB_NATURE.get(sub) or CAT_NATURE.get(cat, "flex")
        if base == "charge":
            return {"nature": "charge", "reason": "bank fee or penalty", "source": "auto"}
        nature, reason = base, {"need": "an everyday essential", "want": "nice to have, not essential", "flex": "could be either"}[base]
        s = self.s
        if sub == "Food delivery":
            n, k = self._week_count(t, sub), int(s["delivery_per_week"])
            nature, reason = (("want", f"delivery order #{n} this week (your comfortable number is {k})") if n > k
                              else ("need", f"delivery #{n} of your {k} a week"))
        elif sub == "Restaurant":
            comfort = s.get("meal_comfort") or self.med.get(sub) or 250
            nature, reason = (("want", f"₹{amt:,.0f} is more than twice your usual ₹{comfort:,.0f} meal") if amt > 2 * comfort
                              else ("need", "a normal meal for you"))
        elif sub == "Cafe":
            usual = self.med.get(sub) or 120
            nature, reason = (("want", f"₹{amt:,.0f} is a pricey cafe visit (you usually spend ₹{usual:,.0f})") if amt > max(300, 2.5 * usual)
                              else ("need", "an everyday chai / coffee"))
        elif sub == "Taxi":
            tm = _mins(t.get("time"))
            if tm is not None and (tm >= 21 * 60 or tm < 6 * 60):
                nature, reason = "need", "late-night ride: safety first"
            elif amt > 2 * (self.med.get(sub) or 150):
                nature, reason = "want", "a much longer or pricier ride than you usually take"
            else:
                nature, reason = "need", "your usual kind of ride"
        elif sub == "Clothing":
            recent = 0
            if len(self.sp):
                d = pd.Timestamp(t["date"])
                rows = self.sp[(self.sp["subcategory"] == "Clothing") & (self.sp["date"] < d) & (self.sp["date"] >= d - pd.Timedelta(days=30))]
                recent = len(rows)
            if amt > s["clothing_need_max"]:
                nature, reason = "want", f"over your ₹{s['clothing_need_max']:,.0f} everyday-clothing line"
            elif recent:
                nature, reason = "want", "a second clothing buy within a month"
            else:
                nature, reason = "need", "a modest clothing buy"
        elif base == "flex" and amt >= s["flex_check_above"]:
            reason = f"₹{amt:,.0f} could be a need or a want: worth a second look"
        # what the user told us they regret / value
        r = self.regrets.get(mk) or self.regrets.get(sub) or {}
        if r.get("regret", 0) >= 2 and r.get("regret", 0) > r.get("worth", 0) and nature != "need":
            nature, reason = "want", reason + f"; you said you regretted this {r['regret']} times"
        trusted = r.get("worth", 0) >= 3 and r.get("regret", 0) == 0
        return {"nature": nature, "reason": reason, "source": "learned" if r else "auto", "trusted": trusted}


def annotate(df: pd.DataFrame, clf: Classifier) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    res = [clf.classify(r) for r in df.to_dict("records")]
    df["nature"] = [x["nature"] if x else None for x in res]
    df["nature_reason"] = [x["reason"] if x else None for x in res]
    return df


# ---------------- cost lenses ----------------
def lenses(amount: float, s: dict, prof: dict | None, room: dict | None, goals: list, daily: dict | None = None) -> list[str]:
    prof, room = prof or {}, room or {}
    out = []
    for lens in s.get("cost_lens") or []:
        if lens == "goal":
            pg = G.primary(goals or [])
            gi = G.impact(amount, pg) if pg else None
            if gi and gi["days"] >= 0.5:
                out.append(f"{gi['days']:.0f} day{'s' if round(gi['days']) != 1 else ''} of saving for your {pg['name']}")
        elif lens == "work" and float(prof.get("salary") or 0) > 0:
            hrs = amount / (float(prof["salary"]) / s["work_hours_month"])
            out.append(f"{hrs:.1f} hours of your work" if hrs < 10 else f"{hrs / 8:.1f} working days")
        elif lens == "pocket":
            pm = sum(float(prof.get(k) or 0) for k in ("pocket_money", "stipend", "scholarship", "part_time"))
            if pm > 0 and amount / (pm / 30.44) >= 0.5:
                out.append(f"{amount / (pm / 30.44):.1f} days of your monthly money")
        elif lens == "room" and room.get("safe_to_spend") and amount / room["safe_to_spend"] >= 0.05:
            out.append(f"{amount / room['safe_to_spend'] * 100:.0f}% of what you can safely spend {room.get('period', '')}".strip())
        elif lens == "unit" and s.get("my_unit") and s["my_unit"].get("price"):
            n = amount / s["my_unit"]["price"]
            if n >= 0.5:
                out.append(f"{n:.1f} {s['my_unit']['name']}")
        elif lens == "daily" and daily and daily.get("limit"):
            out.append(f"{amount / daily['limit'] * 100:.0f}% of today's limit")
    return out[:3]


def _inr(v: float) -> str:
    return f"−₹{abs(v):,.0f}" if v < 0 else f"₹{v:,.0f}"


# ---------------- daily limit (traffic light) ----------------
def flexible_spend(rows: pd.DataFrame, rec_keys: set) -> pd.DataFrame:
    """Spending that the daily limit covers: everything except fixed bills and detected recurring payments."""
    if rows is None or rows.empty:
        return rows
    sp = A.spend_df(rows)
    keep = ~sp["subcategory"].fillna("").isin(BILL_SUBS) & ~sp["merchant_key"].isin(rec_keys)
    if "nature" in sp:
        keep &= ~sp["nature"].isin(["need", "charge"])
    return sp[keep]


def daily_status(df: pd.DataFrame, s: dict, room: dict | None, ctx: dict, day: date | None = None, base_daily: float = 0.0) -> dict:
    day = day or A.as_of(df)
    rec_keys = {r["merchant_key"] for r in ctx.get("recurring", [])}
    today_rows = df[df["date"].dt.date == day]
    flex = flexible_spend(today_rows, rec_keys)
    spent = float(flex["amount"].sum()) if flex is not None and len(flex) else 0.0
    room = room or {}
    sts, days_left = room.get("safe_to_spend"), max(1, int(room.get("days_left") or 1))
    if s["daily_limit_mode"] == "fixed" and s.get("daily_limit"):
        limit, basis = float(s["daily_limit"]), "your fixed daily limit"
    elif sts is not None:
        limit = (sts + spent) / days_left
        basis = f"what you can safely spend {room.get('period', '')}, spread over {days_left} day{'s' if days_left != 1 else ''}"
        if not s["rollover"] and base_daily > 0:
            limit, basis = min(limit, base_daily), "your usual daily room (no rollover)"
    else:
        limit, basis = (base_daily or None), "your average spare money per day"
    pct = (spent / limit * 100) if limit else None
    color = "grey" if pct is None else "green" if pct < 70 else "amber" if pct < 100 else "red"
    by_nature = {}
    if flex is not None and len(flex) and "nature" in flex:
        by_nature = {k: round(float(v), 2) for k, v in flex.groupby(flex["nature"].fillna("flex"))["amount"].sum().items()}
    return {"date": str(day), "spent": round(spent, 2), "limit": round(limit, 2) if limit else None,
            "left": round(limit - spent, 2) if limit else None, "pct": round(pct, 1) if pct is not None else None,
            "color": color, "basis": basis, "by_nature": by_nature, "rollover": s["rollover"]}


# ---------------- "where did my money go today?" ----------------
def explain_day(df: pd.DataFrame, day: date, s: dict, name: str = "") -> dict:
    rows = df[df["date"].dt.date == day].copy()
    if rows.empty:
        return {"date": str(day), "empty": True, "speech": f"No transactions on {day:%d %b}."}
    rows["_t"] = rows["time"].fillna("") if "time" in rows else ""
    rows = rows.sort_values(["_t", "row"])
    first, last = rows.iloc[0], rows[rows["balance"].notna()]
    start_bal = None
    if pd.notna(first.get("balance")):
        start_bal = float(first["balance"]) + (float(first["amount"]) if first["type"] == "debit" else -float(first["amount"]))
    end_bal = float(last.iloc[-1]["balance"]) if len(last) else None
    deb = rows[(rows["type"] == "debit") & (~rows["excluded"])]
    cre = rows[rows["type"] == "credit"]
    items = [{"time": r.get("time"), "merchant": r["merchant"], "amount": float(r["amount"]), "type": r["type"],
              "nature": r.get("nature"), "reason": r.get("nature_reason"), "category": r["category"], "id": int(r["id"])} for _, r in rows.iterrows()]
    tot = {k: round(float(deb[deb.get("nature") == k]["amount"].sum()), 2) if "nature" in deb else 0.0 for k in ("need", "flex", "want", "charge")}
    spent = float(deb["amount"].sum())
    top = deb.sort_values("amount", ascending=False).head(3)
    parts = ", ".join(f"{_inr(r['amount'])} {r['merchant']}" for _, r in top.iterrows())
    who = f"{name}, " if name else ""
    speech = f"{who}on {day:%d %b} you spent {_inr(spent)}" + (f": {parts}" if parts else "") + "."
    if tot["want"]:
        speech += f" {_inr(tot['want'])} of it was wants."
    if tot["charge"]:
        speech += f" {_inr(tot['charge'])} went on bank fees."
    if len(cre):
        speech += f" {_inr(float(cre['amount'].sum()))} came in."
    if start_bal is not None and end_bal is not None:
        speech += f" Your balance went from {_inr(start_bal)} to {_inr(end_bal)}."
    return {"date": str(day), "empty": False, "start_balance": start_bal, "end_balance": end_bal, "spent": round(spent, 2),
            "received": round(float(cre["amount"].sum()), 2), "totals": tot, "items": items, "speech": speech,
            "biggest": items and max((i for i in items if i["type"] == "debit"), key=lambda i: i["amount"], default=None)}


# ---------------- caps ----------------
def caps_status(df: pd.DataFrame, s: dict, month: str | None = None) -> list[dict]:
    if not s.get("caps") or df is None or df.empty:
        return []
    month = month or A.as_of(df).strftime("%Y-%m")
    sp = A.spend_df(df)
    sp = sp[sp["month"] == month]
    out = []
    for key, cap in s["caps"].items():
        rows = sp[(sp["category"] == key) | (sp["subcategory"] == key)]
        spent = float(rows["amount"].sum())
        pct = spent / cap * 100 if cap else 0
        out.append({"key": key, "cap": cap, "spent": round(spent, 2), "pct": round(pct, 1), "left": round(cap - spent, 2),
                    "level": 100 if pct >= 100 else 80 if pct >= 80 else 50 if pct >= 50 else 0})
    return sorted(out, key=lambda c: -c["pct"])


# ---------------- bill shield ----------------
def shield(ctx: dict, s: dict, balance: float | None) -> dict:
    sts = ctx.get("safe_to_spend") or {}
    bills = [c for c in sts.get("commitments_detail", []) if c.get("amount")]
    need = round(sum(float(c["amount"]) for c in bills), 2)
    if balance is None:
        return {"known": False, "bills": bills, "protected": need}
    free = round(balance - (sts.get("pending_debits") or 0) - need, 2)
    return {"known": True, "bills": bills, "protected": need, "balance": balance, "free_after_bills": free, "at_risk": free < 0,
            "period": sts.get("period"),
            "text": (f"{_inr(need)} is protected for {len(bills)} upcoming bill{'s' if len(bills) != 1 else ''}; {_inr(free)} is free to use."
                     if free >= 0 else f"Your balance is {_inr(-free)} short of the bills due {sts.get('period', 'soon')}. "
                                       "A failed auto-debit usually costs ₹250–₹750 in bounce charges.")}


# ---------------- alerts for a new live payment ----------------
def on_new(t: dict, df: pd.DataFrame, ctx: dict, room: dict | None, prof: dict, s: dict, clf: Classifier, goals: list,
           base_daily: float = 0.0, before_daily: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Alerts + jar pledges for one new transaction (already in df). Returns (alerts, jar_entries)."""
    alerts, jar = [], []
    tone = TONES[s["tone"]]
    first = (prof.get("name") or "").split(" ")[0] if prof.get("name") else ""
    if t["type"] != "debit":
        return alerts, jar
    c = clf.classify(t) or {}
    nature, amt, m = c.get("nature"), float(t["amount"]), t.get("merchant") or "a merchant"
    quiet = in_quiet(t.get("time"), s)
    daily = daily_status(df, s, room, ctx, pd.Timestamp(t["date"]).date(), base_daily)
    lens = lenses(amt, s, prof, room, goals, daily)

    # fraud / duplicates first: always loud
    if s.get("fraud_alerts"):
        sp = A.spend_df(df)
        same = sp[(sp["merchant_key"] == t["merchant_key"]) & (sp["amount"] == amt) & (sp["date"] == pd.Timestamp(t["date"])) & (sp["id"] != t["id"])]
        if len(same):
            tm, tm2 = _mins(t.get("time")), [_mins(x) for x in same.get("time", pd.Series(dtype=object)).tolist()]
            close = tm is None or any(x is not None and abs(tm - x) <= 15 for x in tm2)
            if close:
                alerts.append({"kind": "duplicate", "severity": "alert", "title": f"Charged twice? {_inr(amt)} at {m}",
                               "body": f"Two identical payments of {_inr(amt)} to {m} on {pd.Timestamp(t['date']):%d %b}. If you paid only once, "
                                       "raise it with the merchant or your bank today; duplicate charges are usually reversed when reported quickly.",
                               "data": {"actions": ["confirm", "dispute"]}})
        seen_before = ((sp["merchant_key"] == t["merchant_key"]) | (sp["merchant"] == t.get("merchant"))) & (sp["id"] != t["id"])
        med = float(sp["amount"].median()) if len(sp) else 0
        odd = in_quiet(t.get("time"), s)
        new = not seen_before.any()
        if (new and amt >= max(s["big_new_merchant"], 3 * med)) or (odd and new and amt >= 500):
            why = []
            if not seen_before.any():
                why.append("a merchant you've never paid before")
            if odd:
                why.append(f"at {t['time']}, inside your quiet hours ({s['quiet_start']}–{s['quiet_end']})")
            alerts.append({"kind": "fraud", "severity": "alert", "title": f"Was this you? {_inr(amt)} at {m}",
                           "body": f"This payment is {' and '.join(why)}. If it wasn't you, act now.", "data": {"actions": ["confirm", "dispute"]}})
    serious = any(a["kind"] in ("duplicate", "fraud") for a in alerts)
    # need / want
    sens = s["sensitivity"]
    threshold = max(s["min_alert"], 1000) if sens == "relaxed" else s["min_alert"] if sens == "balanced" else max(50, s["min_alert"] / 2)
    if serious:
        pass  # the fraud / duplicate alert says what matters; no need-or-want nagging on top
    elif nature == "want" and amt >= threshold and not c.get("trusted"):
        body = f"{tone['want_lead']}: {c['reason']}."
        if lens:
            body += f" It cost {', '.join(lens)}."
        r = clf.regrets.get(t.get("merchant_key")) or {}
        if r.get("regret"):
            body += f" You regretted {r['regret']} earlier buy{'s' if r['regret'] != 1 else ''} like this."
        if tone["end"]:
            body += " " + tone["end"]
        alerts.append({"kind": "want", "severity": "warn", "title": tone["want_title"].format(amt=_inr(amt), m=m),
                       "body": (f"{first}, " + body[0].lower() + body[1:]) if first and s["tone"] != "straight" else body,
                       "data": {"nature": nature, "reason": c["reason"], "lenses": lens,
                                "actions": ["need", "regret", "worth"]}})
    elif nature == "flex" and sens == "strict" and amt >= s["flex_check_above"]:
        alerts.append({"kind": "check", "severity": "info", "title": f"{_inr(amt)} at {m}: need or want?",
                       "body": f"{c['reason'][0].upper() + c['reason'][1:]}. Tell FinLens once and it remembers for {m}.",
                       "data": {"actions": ["need", "want"], "lenses": lens}})
    elif nature == "charge":
        alerts.append({"kind": "charge", "severity": "info", "title": f"Bank fee: {_inr(amt)}",
                       "body": "A fee or penalty, money you got nothing for. " + _fee_hint(t.get("description", "")), "data": {}})
    # daily limit crossing
    if not serious and daily.get("limit") and before_daily and before_daily.get("color") != daily["color"] and daily["color"] in ("amber", "red"):
        alerts.append({"kind": "limit", "severity": "warn" if daily["color"] == "amber" else "alert",
                       "title": f"Today: {_inr(daily['spent'])} of {_inr(daily['limit'])}",
                       "body": (tone["limit"] if daily["color"] == "red" else f"You've used {daily['pct']:.0f}% of today's comfortable amount."),
                       "data": {"daily": daily}})
    # caps crossing 50 / 80 / 100 %
    for cs in caps_status(df, s):
        if t.get("category") != cs["key"] and t.get("subcategory") != cs["key"]:
            continue
        before = (cs["spent"] - amt) / cs["cap"] * 100
        for lvl in (100, 80, 50):
            if before < lvl <= cs["pct"]:
                alerts.append({"kind": "cap", "severity": "alert" if lvl == 100 else "warn",
                               "title": f"{cs['key']}: {lvl}% of your {_inr(cs['cap'])} cap",
                               "body": (f"{_inr(cs['spent'])} spent this month; " +
                                        (f"{_inr(cs['left'])} left." if cs["left"] > 0 else f"{_inr(-cs['left'])} over the cap.")), "data": {"cap": cs}})
                break
    # bill shield
    bal = t.get("balance")
    if bal is not None and not pd.isna(bal):
        sh = shield(ctx, s, float(bal))
        if sh.get("at_risk"):
            names = ", ".join(f"{b['merchant']} {_inr(b['amount'])}" for b in sh["bills"][:3])
            alerts.append({"kind": "shield", "severity": "alert", "title": "Bills at risk after this payment",
                           "body": f"Coming up: {names}. {sh['text']}", "data": {"shield": sh}})
    # want tax & round-ups (pledges the user confirms later)
    if serious:
        jar = []
    elif nature == "want" and s["want_tax_pct"] > 0:
        jar.append({"kind": "want_tax", "amount": round(amt * s["want_tax_pct"] / 100)})
    if s.get("roundup_to") and not serious:
        step = float(s["roundup_to"])
        up = round(math.ceil(amt / step) * step - amt, 2)
        if up > 0:
            jar.append({"kind": "roundup", "amount": up})
    for a in alerts:
        a["quiet"] = quiet and a["kind"] not in ("fraud", "duplicate", "shield")
        a["data"] = {**a.get("data", {}), "txn": {"merchant": m, "amount": amt, "date": str(t["date"])[:10], "time": t.get("time"), "nature": nature}}
    return alerts, [j for j in jar if j["amount"] > 0]


def _fee_hint(desc: str) -> str:
    d = (desc or "").lower()
    for k, v in FEE_HINTS.items():
        if k in d:
            return v
    return "Ask your bank what it was for; many fees are waived on request."


DISPUTE_STEPS = [
    "Block the card / UPI in your bank app right away (or call the number on the back of your card).",
    "Call 1930 (National Cyber Crime Helpline) or report at cybercrime.gov.in: reporting within hours gives the best chance of a freeze.",
    "Tell your bank in writing (app, e-mail or branch) within 3 days: under RBI rules your liability for unauthorised transactions is zero if reported in time.",
    "Keep the SMS and a screenshot. Never share OTPs or your UPI PIN; you never need a PIN to receive money.",
]


# ---------------- insights ----------------
def leaks(df: pd.DataFrame, s: dict, goals: list, days: int = 60) -> list[dict]:
    ref = A.as_of(df)
    sp = A.spend_df(df)
    if "nature" in sp:
        sp = sp[~sp["nature"].isin(["need", "charge"]) | sp["subcategory"].isin(["Cafe", "Food delivery"])]
    sp = sp[sp["date"] > pd.Timestamp(ref - timedelta(days=days))]
    span = max(7, min(days, (ref - df["date"].min().date()).days + 1))
    out = []
    for key, g in sp[sp["amount"] <= s["leak_max"]].groupby("merchant"):
        if len(g) < 6:
            continue
        monthly = float(g["amount"].sum()) / span * 30.44
        out.append({"merchant": g["merchant"].iloc[-1], "count": int(len(g)), "avg": round(float(g["amount"].mean()), 2),
                    "monthly": round(monthly, 2), "yearly": round(monthly * 12, 2),
                    "half_saves": round(monthly * 6, 2),
                    "goal_days": (lambda pg: round(G.impact(monthly * 6, pg)["days"]) if pg and G.impact(monthly * 6, pg) else None)(G.primary(goals))})
    return sorted(out, key=lambda x: -x["monthly"])[:6]


def charges(df: pd.DataFrame, days: int = 90) -> dict:
    ref = A.as_of(df)
    sp = df[(df["type"] == "debit") & (df["date"] > pd.Timestamp(ref - timedelta(days=days)))]
    mask = (sp["subcategory"] == "Bank charges") | sp["description"].str.contains(r"\b(?:charges?|chgs?|penalty|late fee|annual fee|convenience fee|surcharge)\b", case=False, regex=True)
    rows = sp[mask]
    items = [{"date": str(r["date"].date()), "description": r["description"], "amount": float(r["amount"]), "tip": _fee_hint(r["description"])}
             for _, r in rows.sort_values("date", ascending=False).iterrows()]
    return {"total": round(float(rows["amount"].sum()), 2), "count": len(items), "days": days, "items": items[:12],
            "yearly_estimate": round(float(rows["amount"].sum()) / days * 365, 2) if len(rows) else 0.0}


def habits(df: pd.DataFrame) -> list[dict]:
    if "nature" not in df:
        return []
    w = df[(df["nature"] == "want") & (df["type"] == "debit")]
    if len(w) < 3:
        return []
    out = []
    total = float(w["amount"].sum())
    wk = w[w["date"].dt.weekday >= 5]
    share = float(wk["amount"].sum()) / total
    if share > 0.45:
        out.append({"id": "weekend", "icon": "🎉", "title": f"{share * 100:.0f}% of your wants happen at weekends",
                    "body": "Weekends are 2 of 7 days. Planning one fun thing per weekend (with a number) usually helps."})
    if "time" in w and w["time"].notna().any():
        tw = w[w["time"].notna()]
        late = tw[tw["time"].map(lambda x: _mins(x) >= 22 * 60 or _mins(x) < 5 * 60)]
        if len(tw) >= 3 and len(late) / len(tw) >= 0.3:
            out.append({"id": "late", "icon": "🌙", "title": f"{len(late) / len(tw) * 100:.0f}% of your wants are bought after 10 pm",
                        "body": "Late-night buys are the ones people regret most. Try adding them to the wishlist and deciding in the morning."})
    inc = df[(df["type"] == "credit") & (df["credit_kind"] == "income")]
    if len(inc):
        after = set()
        for d in inc["date"].dt.date:
            after |= {d + timedelta(days=i) for i in range(3)}
        in_window = w[w["date"].dt.date.isin(after)]
        days_total = max(1, (df["date"].max() - df["date"].min()).days + 1)
        rate_after = float(in_window["amount"].sum()) / max(1, len(after))
        rate_all = total / days_total
        if rate_after > 1.8 * rate_all and len(in_window) >= 2:
            out.append({"id": "payday", "icon": "💸", "title": f"Payday splurge: {rate_after / rate_all:.1f}× more wants in the 3 days after money arrives",
                        "body": "Move your goal saving the day money arrives (pay yourself first), so the splurge comes from what's left."})
    eom = w[w["date"].dt.day >= 24]
    if float(eom["amount"].sum()) / total > 0.4:
        out.append({"id": "month_end", "icon": "📅", "title": "Most of your wants come at the end of the month",
                    "body": "That's when money is tightest. A small weekly cap for the last week helps."})
    per_day = w.groupby(w["date"].dt.date).size()
    binge = per_day[per_day >= 3]
    if len(binge):
        out.append({"id": "binge", "icon": "🛍️", "title": f"{len(binge)} day{'s' if len(binge) != 1 else ''} with 3+ impulse buys",
                    "body": f"For example {binge.index[-1]:%d %b}. When you notice a second want in a day, pause the third."})
    top = w.groupby("merchant")["amount"].sum().sort_values(ascending=False).head(3)
    if len(top):
        out.append({"id": "top", "icon": "🎯", "title": "Your biggest wants: " + ", ".join(f"{k} ({_inr(v)})" for k, v in top.items()),
                    "body": "These are the places a small cap would make the biggest difference."})
    return out


def subscriptions(df: pd.DataFrame, ctx: dict, s: dict) -> dict:
    rec = [r for r in ctx.get("recurring", []) if (r.get("subcategory") == "Subscription" or any(k in r["merchant_key"] for g in OTT.values() for k in g))]
    items = []
    for r in rec:
        monthly = r["typical_amount"] * (4.33 if r["cadence"] == "weekly" else 1)
        group = next((g for g, ks in OTT.items() if any(k in r["merchant_key"] for k in ks)), None)
        items.append({"merchant": r["merchant"], "merchant_key": r["merchant_key"], "monthly": round(monthly, 2), "yearly": round(monthly * 12, 2),
                      "next": r["next_expected"], "group": group, "decision": s.get("subs_review", {}).get(r["merchant_key"])})
    dup = [g for g, n in Counter(i["group"] for i in items if i["group"]).items() if n >= 2]
    ref = A.as_of(df)
    trials = df[(df["type"] == "debit") & ((df["amount"] <= 5) | df["description"].str.contains(r"trial|mandate|autopay", case=False, regex=True))
                & (df["date"] > pd.Timestamp(ref - timedelta(days=45)))]
    tl = [{"date": str(r["date"].date()), "merchant": r["merchant"], "amount": float(r["amount"]),
           "note": "A ₹1–₹5 charge or e-mandate usually means a free trial or autopay was set up. Note when it turns paid and cancel before if you don't need it."}
          for _, r in trials.iterrows()]
    cancel = [i for i in items if i["decision"] == "cancel"]
    return {"items": items, "monthly_total": round(sum(i["monthly"] for i in items), 2), "yearly_total": round(sum(i["yearly"] for i in items), 2),
            "duplicates": [{"group": g, "merchants": [i["merchant"] for i in items if i["group"] == g],
                            "text": f"You pay for {sum(1 for i in items if i['group'] == g)} {g} apps. Keeping one and rotating monthly saves the rest."} for g in dup],
            "trials": tl, "cancel_saves_yearly": round(sum(i["yearly"] for i in cancel), 2)}


def emergency(df: pd.DataFrame, ctx: dict, s: dict, prof: dict) -> dict:
    from .profile import declared_fixed_costs
    ms = A.monthly_summary(df)
    full = ms[:-1] if len(ms) > 1 and df["date"].max().day < 25 else ms
    ess = sum(m["essential"] for m in full[-3:]) / max(1, len(full[-3:])) if full else 0.0
    if ess <= 0:
        ess = declared_fixed_costs(prof or {})
    bal = (ctx.get("balance") or {}).get("balance")
    commits = (ctx.get("safe_to_spend") or {}).get("upcoming_commitments") or 0
    liquid = max(0.0, (bal or 0) - commits) + float(s.get("other_savings") or 0)
    target = s["emergency_months"]
    months = liquid / ess if ess else None
    need = max(0.0, target * ess - liquid) if ess else None
    return {"liquid": round(liquid, 2), "monthly_essentials": round(ess, 2), "months_covered": round(months, 1) if months is not None else None,
            "target_months": target, "gap": round(need, 2) if need is not None else None,
            "plan": (f"Saving {_inr(need / 6)} a month for 6 months closes the gap." if need else "You're at your target. 🎉") if need is not None else None,
            "status": None if months is None else "good" if months >= target else "low" if months < max(0.5, target / 2) else "building"}


def events(df: pd.DataFrame | None, s: dict, prof: dict, today: date) -> dict:
    student = (prof or {}).get("occupation") in ("student", "both", "between_jobs")
    out = []
    for key in s.get("festivals", []):
        name, emoji, dates, (lo, hi) = FESTIVALS[key]
        d = next((date.fromisoformat(x) for x in dates if date.fromisoformat(x) >= today), None)
        if not d:
            continue
        amount = float(s.get("festival_budgets", {}).get(key) or (lo if student else hi))
        last = None
        if df is not None and not df.empty:
            past = next((date.fromisoformat(x) for x in reversed(dates) if date.fromisoformat(x) < today), None)
            if past and df["date"].min().date() <= past - timedelta(days=10) and df["date"].max().date() >= past + timedelta(days=2):
                sp = A.spend_df(df)
                win = sp[(sp["date"] >= pd.Timestamp(past - timedelta(days=10))) & (sp["date"] <= pd.Timestamp(past + timedelta(days=2)))]
                base = float(sp["amount"].sum()) / max(1, (df["date"].max() - df["date"].min()).days + 1) * 13
                extra = float(win["amount"].sum()) - base
                if extra > 200:
                    last = f"Around {name} {past.year} you spent {_inr(extra)} more than a normal fortnight."
        out.append({"id": key, "name": name, "emoji": emoji, "date": str(d), "amount": amount, "festival": True, "approx": True, "last_time": last})
    for e in s.get("events", []):
        d = date.fromisoformat(e["date"])
        while e.get("yearly") and d < today:
            d = d.replace(year=d.year + 1)
        if d >= today:
            out.append({**e, "date": str(d), "festival": False})
    for e in out:
        months = max(1.0, (date.fromisoformat(e["date"]) - today).days / 30.44)
        e["days_left"] = (date.fromisoformat(e["date"]) - today).days
        e["monthly"] = round(e["amount"] / months, 2)
    out.sort(key=lambda e: e["date"])
    reserve = round(sum(e["monthly"] for e in out if e["days_left"] <= 365), 2) if s.get("reserve_events") else 0.0
    return {"items": out, "monthly_reserve": reserve, "suggested": [k for k in s.get("suggested_festivals", []) if k not in s.get("festivals", [])],
            "catalog": {k: {"name": v[0], "emoji": v[1]} for k, v in FESTIVALS.items()}}


def streaks(df: pd.DataFrame, base_daily: float, reflections: list[dict], jar_moved: float, wish_skipped: int) -> dict:
    if df is None or df.empty or "nature" not in df:
        return {"badges": []}
    start, end = df["date"].min().date(), A.as_of(df)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    w = df[(df["nature"] == "want") & (df["type"] == "debit")]
    want_days = set(w["date"].dt.date)
    flex = A.spend_df(df)
    flex = flex[~flex["subcategory"].fillna("").isin(BILL_SUBS)]
    per_day = flex.groupby(flex["date"].dt.date)["amount"].sum()

    def run(ok):
        cur = best = 0
        for d in days:
            cur = cur + 1 if ok(d) else 0
            best = max(best, cur)
        return cur, best

    nw_cur, nw_best = run(lambda d: d not in want_days)
    ul_cur, ul_best = run(lambda d: base_daily > 0 and float(per_day.get(d, 0)) <= base_daily)
    no_want_days = sum(1 for d in days if d not in want_days)
    regret_week = [r for r in reflections if r["created_at"] >= datetime.utcnow() - timedelta(days=7)]
    badges = [
        ("first_free", "🌱", "First want-free day", no_want_days >= 1, f"{no_want_days} want-free days so far"),
        ("streak3", "🔥", "3 want-free days in a row", nw_best >= 3, f"best run {nw_best}"),
        ("streak7", "🏅", "A full want-free week", nw_best >= 7, f"best run {nw_best}/7"),
        ("limit5", "🚦", "5 days in a row under your daily limit", ul_best >= 5, f"best run {ul_best}/5" if base_daily else "needs a daily limit"),
        ("honest", "🪞", "Rated 5 past wants honestly", len(reflections) >= 5, f"{len(reflections)}/5"),
        ("regret_free", "😌", "A week with no regrets", bool(regret_week) and all(r["verdict"] == "worth" for r in regret_week), "rate this week's wants"),
        ("jar", "🫙", "Moved money to your jar", jar_moved > 0, f"{_inr(jar_moved)} moved"),
        ("wishlist", "🧘", "Skipped something on your wishlist", wish_skipped > 0, f"{wish_skipped} skipped"),
    ]
    return {"no_want": {"current": nw_cur, "best": nw_best}, "under_limit": {"current": ul_cur, "best": ul_best},
            "want_free_days": no_want_days, "days": len(days),
            "badges": [{"id": i, "icon": ic, "title": t, "earned": e, "progress": p} for i, ic, t, e, p in badges]}


def tips(df: pd.DataFrame) -> list[dict]:
    ref = A.as_of(df)
    sp = A.spend_df(df)
    sp = sp[sp["date"] > pd.Timestamp(ref - timedelta(days=60))]
    span = max(7, min(60, (ref - df["date"].min().date()).days + 1))
    per_month = lambda x: x / span * 30.44  # noqa: E731
    out = []
    dl = sp[sp["subcategory"] == "Food delivery"]
    n = per_month(len(dl))
    if n >= 8:
        fee = 35 * n
        out.append({"id": "delivery", "icon": "🛵", "title": f"~{n:.0f} delivery orders a month",
                    "body": f"At roughly ₹35 of delivery + platform fees per order (an estimate), that's about {_inr(fee)}/month in fees alone. "
                            "If you keep ordering this often, a delivery membership can be cheaper; check its current price first. Ordering with a friend halves the fee.",
                    "saves_monthly": round(fee * 0.5, 2)})
    metro = sp[sp["subcategory"] == "Metro"]
    if per_month(len(metro)) >= 15:
        m_amt = per_month(float(metro["amount"].sum()))
        out.append({"id": "metro", "icon": "🚇", "title": f"{per_month(len(metro)):.0f} metro trips a month",
                    "body": f"A metro smart card typically gives a discount on every ride (about 5% in Bengaluru), roughly {_inr(m_amt * 0.05)}/month on your {_inr(m_amt)}.",
                    "saves_monthly": round(m_amt * 0.05, 2)})
    cabs = sp[(sp["subcategory"] == "Taxi") & (sp["amount"] <= 200)]
    if per_month(len(cabs)) >= 6:
        c_amt = per_month(float(cabs["amount"].sum()))
        out.append({"id": "cabs", "icon": "🚕", "title": f"{per_month(len(cabs)):.0f} short cab rides a month ({_inr(c_amt)})",
                    "body": "Bike taxis, autos or a bus/metro leg are often 40–60% cheaper for short trips.", "saves_monthly": round(c_amt * 0.4, 2)})
    cafe = sp[sp["subcategory"] == "Cafe"]
    if per_month(float(cafe["amount"].sum())) >= 1000:
        c_amt = per_month(float(cafe["amount"].sum()))
        out.append({"id": "cafe", "icon": "☕", "title": f"{_inr(c_amt)} a month at cafes",
                    "body": "Making coffee or chai at home on weekdays and keeping cafes for meeting friends often halves this.", "saves_monthly": round(c_amt / 2, 2)})
    atm = df[(df["type"] == "debit") & (df["subcategory"] == "Cash withdrawal") & (df["date"] > pd.Timestamp(ref - timedelta(days=60)))]
    if per_month(len(atm)) > 5:
        out.append({"id": "atm", "icon": "🏧", "title": f"{per_month(len(atm)):.0f} ATM withdrawals a month",
                    "body": "Banks charge after about 5 free ATM uses a month (fewer at other banks' ATMs). Fewer, larger withdrawals or UPI avoid the fee.", "saves_monthly": 0})
    rc = sp[(sp["subcategory"] == "Mobile/Internet") & (sp["amount"] < 400)]
    if per_month(len(rc)) >= 0.9 and len(rc) >= 2:
        r_amt = float(rc["amount"].median())
        out.append({"id": "recharge", "icon": "📱", "title": f"Monthly recharges of about {_inr(r_amt)}",
                    "body": "Long-validity (84- or 365-day) plans usually cost 10–20% less per month than 28-day packs; compare on your operator's app.",
                    "saves_monthly": round(r_amt * 0.12, 2)})
    return out


def owed(balances: list[dict], entries: list[dict], s: dict, today: date, me: str = "") -> list[dict]:
    out = []
    for f in balances:
        if f["balance"] <= 0.5:
            continue
        dates = [date.fromisoformat(e["date"]) for e in entries if e["kind"] == "expense" and e["paid_by"] == 0 and str(f["id"]) in e["shares"]]
        since = (today - min(dates)).days if dates else 0
        if since < s["owed_remind_days"]:
            continue
        msg = (f"Hi {f['name']}! Quick reminder about the ₹{f['balance']:,.0f} from our split"
               f"{' (' + str(min(dates).strftime('%d %b')) + ')' if dates else ''}. UPI whenever you can, thanks! 🙂" + (f" - {me}" if me else ""))
        out.append({"friend_id": f["id"], "name": f["name"], "amount": f["balance"], "days": since, "message": msg,
                    "whatsapp": "https://wa.me/?text=" + quote(msg)})
    return sorted(out, key=lambda x: -x["amount"])


def buddy_text(rep: dict, s: dict, name: str, wants: float, streak: dict | None) -> dict:
    share = s.get("buddy", {}).get("share", "totals")
    lines = [f"📒 {name or 'My'} FinLens week ({rep['period']['start']} → {rep['period']['end']})",
             f"Spent: {_inr(rep['spent'])}" + (f" ({'+' if rep['change'] > 0 else '−'}{_inr(abs(rep['change']))[1:] if rep['change'] < 0 else _inr(rep['change'])[1:]} vs last week)" if rep.get("change") is not None else ""),
             f"Wants: {_inr(wants)}", f"Want-free days: {streak['want_free_week'] if streak else '-'} / 7"]
    if share == "detailed":
        lines += [f"• {c['category']}: {_inr(c['spent'])}" for c in rep.get("categories", [])[:4]]
    for g in rep.get("goals", [])[:2]:
        lines.append(f"🎯 {g['name']}: {g['progress_pct']}% saved")
    if rep.get("win"):
        lines.append(f"🏆 {rep['win']}")
    lines.append("Keep me honest next week! 💪")
    txt = "\n".join(lines)
    return {"text": txt, "whatsapp": "https://wa.me/?text=" + quote(txt), "buddy": s.get("buddy", {}).get("name")}


def wants_summary(df: pd.DataFrame, month: str | None = None) -> dict:
    if df is None or df.empty or "nature" not in df:
        return {}
    month = month or A.as_of(df).strftime("%Y-%m")
    sp = A.spend_df(df)
    sp = sp[sp["month"] == month]
    by = {k: round(float(v), 2) for k, v in sp.groupby(sp["nature"].fillna("flex"))["amount"].sum().items()}
    top = sp[sp["nature"] == "want"].groupby("merchant")["amount"].sum().sort_values(ascending=False).head(5)
    return {"month": month, "by_nature": by, "top_wants": [{"merchant": k, "amount": round(float(v), 2)} for k, v in top.items()],
            "want_share_pct": round(by.get("want", 0) / max(1, sum(by.values())) * 100, 1)}
