"""Generate SYNTHETIC FinLens sample statements and a labelled categorization set.

  python -m ml.generate_finlens_samples

Outputs (data/samples/):
  finlens_sample_statement.csv          Date,Description,Type,Amount,Balance,Status  (student, Aug-Nov 2026)
  finlens_sample_statement_debitcredit.csv  same data in Debit/Credit column layout (tests schema mapping)
  labelled_transactions.csv             description,type,expected_category (for precision/recall/F1)
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import pandas as pd

from shared.config import ROOT

OUT = ROOT / "data" / "samples"
OPENING = 3500.0


def build(seed: int = 11) -> pd.DataFrame:
    rnd = random.Random(seed)
    rows: list[tuple] = []

    def add(d: date, desc: str, typ: str, amt: float, status: str = "Posted"):
        rows.append((d, desc, typ, round(amt, 2), status))

    months = [(2026, 8), (2026, 9), (2026, 10), (2026, 11)]
    end = date(2026, 11, 12)
    for (y, m) in months:
        first = date(y, m, 1)
        def d(day):  # noqa: E306
            return date(y, m, day)
        def ok(x):  # noqa: E306
            return x <= end
        if ok(d(1)): add(d(1), "NEFT CR MONTHLY ALLOWANCE FROM PARENT", "Credit", 10000)
        if ok(d(5)) and m >= 9: add(d(5), "NEFT CR INTERNSHIP STIPEND ACME LABS", "Credit", 3500)
        if ok(d(3)): add(d(3), "UPI/HOSTEL RENT/SUNRISE PG", "Debit", 5000)
        if ok(d(6)): add(d(6), "NETFLIX SUBSCRIPTION", "Debit", 199)
        if ok(d(12)): add(d(12), "SPOTIFY INDIA SUBSCRIPTION", "Debit", 119)
        if ok(d(10)): add(d(10), "JIO PREPAID RECHARGE", "Debit", 299)
        if ok(d(15)): add(d(15), "BESCOM ELECTRICITY SHARE UPI", "Debit", rnd.randint(380, 520))
        if ok(d(7)): add(d(7), "EMI CONSUMER DURABLE LOAN BAJAJ", "Debit", 1250)
        # metro twice a week-ish
        for day in range(2, 29, 3):
            if ok(d(day)): add(d(day), "BMRCL METRO CARD RECHARGE" if day % 2 else "METRO UPI TICKET", "Debit", rnd.choice([60, 60, 80, 100]))
        # food delivery: grows over months (the insight the demo explains)
        n_orders = {8: 5, 9: 6, 10: 7, 11: 9}[m]
        days = sorted(rnd.sample(range(1, 29 if m != 11 else 12), n_orders))
        for day in days:
            add(d(day), rnd.choice(["UPI/SWIGGY/ORDER", "UPI/ZOMATO/ORDER", "SWIGGY FOOD DELIVERY"]), "Debit", rnd.choice([229, 249, 259, 279, 289, 310]))
        for day in sorted(rnd.sample(range(1, 29 if m != 11 else 12), 4 if m != 11 else 2)):
            add(d(day), rnd.choice(["COLLEGE CANTEEN UPI", "CAFE COFFEE DAY", "CHAI POINT CAFE"]), "Debit", rnd.choice([40, 60, 90, 120, 150]))
        for day in sorted(rnd.sample(range(1, 29 if m != 11 else 12), 2)):
            add(d(day), rnd.choice(["BLINKIT GROCERY", "ZEPTO GROCERY", "BIGBASKET ORDER"]), "Debit", rnd.randint(250, 650))
        if ok(d(18)): add(d(18), rnd.choice(["UBER INDIA TRIP", "RAPIDO RIDE", "OLA CABS"]), "Debit", rnd.randint(120, 260))
        if ok(d(20)): add(d(20), "BOOKMYSHOW MOVIE", "Debit", rnd.choice([0, 250, 320]) or 280)
        if ok(d(9)): add(d(9), "APOLLO PHARMACY", "Debit", rnd.randint(90, 300))

    # one-offs
    add(date(2026, 8, 22), "COURSERA COURSE FEE", "Debit", 1499)
    add(date(2026, 9, 4), "MYNTRA CLOTHING ORDER", "Debit", 1200)
    add(date(2026, 9, 9), "AMAZON PAY ELECTRONICS ORDER", "Debit", 1899)
    add(date(2026, 9, 11), "AMAZON REFUND ELECTRONICS ORDER", "Credit", 1899)
    add(date(2026, 9, 24), "UPI FROM RAHUL K - TRIP SHARE", "Credit", 1500)
    add(date(2026, 10, 2), "TRANSFER FROM SAVINGS A/C - SELF", "Credit", 3000)
    add(date(2026, 10, 14), "DECATHLON SPORTS SHOES", "Debit", 1799)
    add(date(2026, 10, 19), "UPI TO ANANYA - BIRTHDAY GIFT", "Debit", 700)
    add(date(2026, 10, 26), "POS 4421 SHREE ENTERPRISES", "Debit", 640)  # deliberately uncertain
    add(date(2026, 11, 4), "AJIO CLOTHING STORE", "Debit", 1350)
    add(date(2026, 11, 8), "STEAM GAMING PURCHASE", "Debit", 499)
    add(date(2026, 11, 11), "UPI/SWIGGY/ORDER", "Debit", 289, status="Pending")

    df = pd.DataFrame(rows, columns=["date", "Description", "Type", "Amount", "Status"])
    df = df.sort_values(["date", "Type"], ascending=[True, True], kind="stable").reset_index(drop=True)
    bal, bals = OPENING, []
    for r in df.itertuples():
        if r.Status == "Posted":
            bal += r.Amount if r.Type == "Credit" else -r.Amount
        bals.append(round(bal, 2))
    df["Balance"] = bals
    df["Date"] = df["date"].map(lambda x: x.strftime("%d %b %Y"))
    df = df[["Date", "Description", "Type", "Amount", "Balance", "Status"]]
    # an exact duplicate export row (should be removed by ingestion)
    dup = df.iloc[[10]]
    df = pd.concat([df.iloc[:11], dup, df.iloc[11:]]).reset_index(drop=True)
    return df


LABELLED = [
    ("UPI/SWIGGY/ORDER 88213", "debit", "Food"), ("ZOMATO ONLINE ORDER", "debit", "Food"), ("CAFE COFFEE DAY", "debit", "Food"),
    ("BLINKIT GROCERY", "debit", "Food"), ("DOMINOS PIZZA ONLINE", "debit", "Food"), ("COLLEGE CANTEEN UPI", "debit", "Food"),
    ("BMRCL METRO CARD RECHARGE", "debit", "Transportation"), ("UBER INDIA TRIP", "debit", "Transportation"),
    ("OLA CABS RIDE", "debit", "Transportation"), ("HPCL PETROL PUMP", "debit", "Transportation"), ("IRCTC E-TICKET", "debit", "Transportation"),
    ("RAPIDO BIKE TAXI", "debit", "Transportation"), ("MYNTRA CLOTHING ORDER", "debit", "Shopping"), ("AMAZON PAY ORDER", "debit", "Shopping"),
    ("FLIPKART INTERNET PVT", "debit", "Shopping"), ("DECATHLON SPORTS", "debit", "Shopping"), ("CROMA ELECTRONICS", "debit", "Shopping"),
    ("NETFLIX SUBSCRIPTION", "debit", "Entertainment"), ("SPOTIFY INDIA", "debit", "Entertainment"), ("BOOKMYSHOW MOVIE", "debit", "Entertainment"),
    ("PVR CINEMAS", "debit", "Entertainment"), ("STEAM GAMING PURCHASE", "debit", "Entertainment"),
    ("COURSERA COURSE FEE", "debit", "Education"), ("UDEMY COURSE", "debit", "Education"), ("COLLEGE FEE PAYMENT", "debit", "Education"),
    ("SAPNA BOOK HOUSE", "debit", "Education"), ("HOSTEL RENT SUNRISE PG", "debit", "Accommodation & Utilities"),
    ("BESCOM ELECTRICITY", "debit", "Accommodation & Utilities"), ("JIO PREPAID RECHARGE", "debit", "Accommodation & Utilities"),
    ("AIRTEL BROADBAND BILL", "debit", "Accommodation & Utilities"), ("APOLLO PHARMACY", "debit", "Healthcare"),
    ("MEDPLUS PHARMACY", "debit", "Healthcare"), ("CITY CLINIC CONSULTATION", "debit", "Healthcare"),
    ("UPI TO ANANYA GIFT", "debit", "Transfers & Other"), ("ATM CASH WITHDRAWAL", "debit", "Transfers & Other"),
    ("EMI CONSUMER LOAN", "debit", "Transfers & Other"), ("COCA COLA VENDING", "debit", "Uncertain"),
    ("POS 4421 SHREE ENTERPRISES", "debit", "Uncertain"), ("BUSINESS CENTRE PRINTING", "debit", "Uncertain"),
    ("NEFT CR SALARY ACME", "credit", "Income"), ("MONTHLY ALLOWANCE FROM PARENT", "credit", "Income"),
    ("AMAZON REFUND", "credit", "Refund"), ("TRANSFER FROM SAVINGS A/C SELF", "credit", "Own transfer"),
    ("UPI FROM RAHUL TRIP SHARE", "credit", "Transfers in"), ("CASH DEPOSIT", "credit", "Uncertain"),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    df = build()
    df.to_csv(OUT / "finlens_sample_statement.csv", index=False)
    dc = df.copy()
    dc["Debit"] = dc.apply(lambda r: r["Amount"] if r["Type"] == "Debit" else "", axis=1)
    dc["Credit"] = dc.apply(lambda r: r["Amount"] if r["Type"] == "Credit" else "", axis=1)
    dc.rename(columns={"Date": "Txn Date", "Description": "Narration"})[["Txn Date", "Narration", "Debit", "Credit", "Balance", "Status"]] \
      .to_csv(OUT / "finlens_sample_statement_debitcredit.csv", index=False)
    pd.DataFrame(LABELLED, columns=["description", "type", "expected_category"]).to_csv(OUT / "labelled_transactions.csv", index=False)
    print(f"SYNTHETIC statement: {len(df)} rows -> {OUT}")


if __name__ == "__main__":
    main()
