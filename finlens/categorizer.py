"""Merchant identification and transaction categorization.

Rule-based keyword matching (transparent and easy to audit), plus user
corrections that override rules for the same merchant. Anything not matched
is labelled 'Uncertain' rather than guessed.
"""
from __future__ import annotations

import re

CATEGORIES = [
    "Food", "Transportation", "Shopping", "Entertainment", "Education",
    "Accommodation & Utilities", "Healthcare", "Transfers & Other",
]
ESSENTIAL = {"Accommodation & Utilities", "Healthcare", "Education", "Transportation"}

# merchant keyword -> (canonical merchant, category, subcategory)
MERCHANTS: list[tuple[str, str, str, str]] = [
    # Food
    ("swiggy", "Swiggy", "Food", "Food delivery"), ("zomato", "Zomato", "Food", "Food delivery"),
    ("food delivery", "Food delivery", "Food", "Food delivery"), ("eatsure", "EatSure", "Food", "Food delivery"),
    ("domino", "Domino's", "Food", "Food delivery"), ("pizza hut", "Pizza Hut", "Food", "Restaurant"),
    ("mcdonald", "McDonald's", "Food", "Restaurant"), ("kfc", "KFC", "Food", "Restaurant"),
    ("starbucks", "Starbucks", "Food", "Cafe"), ("cafe", "Cafe", "Food", "Cafe"), ("coffee", "Cafe", "Food", "Cafe"),
    ("restaurant", "Restaurant", "Food", "Restaurant"), ("canteen", "Canteen", "Food", "Restaurant"),
    ("mess", "Mess", "Food", "Restaurant"), ("biryani", "Biryani outlet", "Food", "Restaurant"),
    ("bigbasket", "BigBasket", "Food", "Groceries"), ("blinkit", "Blinkit", "Food", "Groceries"),
    ("zepto", "Zepto", "Food", "Groceries"), ("instamart", "Swiggy Instamart", "Food", "Groceries"),
    ("grocery", "Grocery store", "Food", "Groceries"), ("dmart", "DMart", "Food", "Groceries"),
    ("supermarket", "Supermarket", "Food", "Groceries"),
    # Transportation
    ("metro", "Metro", "Transportation", "Metro"), ("bmtc", "BMTC", "Transportation", "Bus"),
    ("bus", "Bus", "Transportation", "Bus"), ("uber", "Uber", "Transportation", "Taxi"),
    ("ola", "Ola", "Transportation", "Taxi"), ("rapido", "Rapido", "Transportation", "Taxi"),
    ("taxi", "Taxi", "Transportation", "Taxi"), ("auto", "Auto", "Transportation", "Taxi"),
    ("petrol", "Fuel", "Transportation", "Fuel"), ("fuel", "Fuel", "Transportation", "Fuel"),
    ("hpcl", "HPCL", "Transportation", "Fuel"), ("indian oil", "Indian Oil", "Transportation", "Fuel"),
    ("parking", "Parking", "Transportation", "Parking"), ("irctc", "IRCTC", "Transportation", "Train"),
    ("fastag", "FASTag", "Transportation", "Tolls"),
    # Shopping
    ("amazon", "Amazon", "Shopping", "Online retail"), ("flipkart", "Flipkart", "Shopping", "Online retail"),
    ("myntra", "Myntra", "Shopping", "Clothing"), ("ajio", "AJIO", "Shopping", "Clothing"),
    ("meesho", "Meesho", "Shopping", "Online retail"), ("nykaa", "Nykaa", "Shopping", "Beauty"),
    ("clothing", "Clothing store", "Shopping", "Clothing"), ("decathlon", "Decathlon", "Shopping", "Sports"),
    ("croma", "Croma", "Shopping", "Electronics"), ("reliance digital", "Reliance Digital", "Shopping", "Electronics"),
    ("electronics", "Electronics store", "Shopping", "Electronics"), ("zara", "Zara", "Shopping", "Clothing"),
    ("h&m", "H&M", "Shopping", "Clothing"), ("westside", "Westside", "Shopping", "Clothing"),
    # Entertainment
    ("netflix", "Netflix", "Entertainment", "Subscription"), ("spotify", "Spotify", "Entertainment", "Subscription"),
    ("hotstar", "Disney+ Hotstar", "Entertainment", "Subscription"), ("prime video", "Prime Video", "Entertainment", "Subscription"),
    ("youtube", "YouTube Premium", "Entertainment", "Subscription"), ("subscription", "Subscription", "Entertainment", "Subscription"),
    ("bookmyshow", "BookMyShow", "Entertainment", "Movies & events"), ("pvr", "PVR", "Entertainment", "Movies & events"),
    ("inox", "INOX", "Entertainment", "Movies & events"), ("steam", "Steam", "Entertainment", "Gaming"),
    ("playstation", "PlayStation", "Entertainment", "Gaming"), ("gaming", "Gaming", "Entertainment", "Gaming"),
    # Education
    ("udemy", "Udemy", "Education", "Courses"), ("coursera", "Coursera", "Education", "Courses"),
    ("college fee", "College fees", "Education", "Fees"), ("tuition", "Tuition", "Education", "Fees"),
    ("exam fee", "Exam fees", "Education", "Fees"), ("book", "Books", "Education", "Books"),
    ("stationery", "Stationery", "Education", "Materials"), ("xerox", "Photocopy", "Education", "Materials"),
    # Accommodation & utilities
    ("rent", "Rent", "Accommodation & Utilities", "Rent"), ("hostel", "Hostel", "Accommodation & Utilities", "Hostel"),
    ("pg ", "PG accommodation", "Accommodation & Utilities", "Rent"), ("electricity", "Electricity", "Accommodation & Utilities", "Electricity"),
    ("bescom", "BESCOM", "Accommodation & Utilities", "Electricity"), ("jio", "Jio", "Accommodation & Utilities", "Mobile/Internet"),
    ("airtel", "Airtel", "Accommodation & Utilities", "Mobile/Internet"), ("internet", "Internet", "Accommodation & Utilities", "Mobile/Internet"),
    ("broadband", "Broadband", "Accommodation & Utilities", "Mobile/Internet"), ("recharge", "Mobile recharge", "Accommodation & Utilities", "Mobile/Internet"),
    ("water bill", "Water", "Accommodation & Utilities", "Water"), ("gas", "Gas", "Accommodation & Utilities", "Gas"),
    # Healthcare
    ("pharmacy", "Pharmacy", "Healthcare", "Pharmacy"), ("apollo", "Apollo", "Healthcare", "Pharmacy"),
    ("medplus", "MedPlus", "Healthcare", "Pharmacy"), ("1mg", "Tata 1mg", "Healthcare", "Pharmacy"),
    ("hospital", "Hospital", "Healthcare", "Medical"), ("clinic", "Clinic", "Healthcare", "Medical"),
    ("doctor", "Doctor", "Healthcare", "Medical"), ("medical", "Medical", "Healthcare", "Medical"),
    # Wants that people most often regret (used by Spend Guard)
    ("lenskart", "Lenskart", "Shopping", "Accessories"), ("sunglass", "Sunglasses", "Shopping", "Accessories"),
    ("titan eye", "Titan Eye+", "Shopping", "Accessories"), ("ray-ban", "Ray-Ban", "Shopping", "Accessories"),
    ("rayban", "Ray-Ban", "Shopping", "Accessories"), ("fastrack", "Fastrack", "Shopping", "Accessories"),
    ("titan", "Titan", "Shopping", "Accessories"), ("watch", "Watch store", "Shopping", "Accessories"),
    ("boat", "boAt", "Shopping", "Gadgets"), ("apple store", "Apple", "Shopping", "Gadgets"), ("oneplus", "OnePlus", "Shopping", "Gadgets"),
    ("earbuds", "Earbuds", "Shopping", "Gadgets"), ("headphone", "Headphones", "Shopping", "Gadgets"),
    ("tanishq", "Tanishq", "Shopping", "Jewellery"), ("jewel", "Jewellery", "Shopping", "Jewellery"),
    ("perfume", "Perfume", "Shopping", "Beauty"), ("salon", "Salon", "Shopping", "Grooming"), ("spa", "Spa", "Shopping", "Grooming"),
    ("wine", "Wine shop", "Entertainment", "Alcohol & smoking"), ("liquor", "Liquor store", "Entertainment", "Alcohol & smoking"),
    ("beer", "Beer", "Entertainment", "Alcohol & smoking"), ("pub", "Pub", "Entertainment", "Alcohol & smoking"),
    ("bar", "Bar", "Entertainment", "Alcohol & smoking"), ("cigarette", "Cigarettes", "Entertainment", "Alcohol & smoking"),
    ("tobacco", "Tobacco", "Entertainment", "Alcohol & smoking"),
    ("dream11", "Dream11", "Entertainment", "Betting & fantasy"), ("mpl", "MPL", "Entertainment", "Betting & fantasy"),
    ("my11circle", "My11Circle", "Entertainment", "Betting & fantasy"), ("rummy", "Rummy", "Entertainment", "Betting & fantasy"),
    ("betting", "Betting", "Entertainment", "Betting & fantasy"), ("lottery", "Lottery", "Entertainment", "Betting & fantasy"),
    ("chai", "Chai stall", "Food", "Cafe"),
    # Bank charges and fees (before obligations so "ATM WDL CHG" is a charge, not a withdrawal)
    ("sms alert", "Bank charges", "Transfers & Other", "Bank charges"), ("chgs", "Bank charges", "Transfers & Other", "Bank charges"),
    ("chg", "Bank charges", "Transfers & Other", "Bank charges"), ("charges", "Bank charges", "Transfers & Other", "Bank charges"),
    ("penalty", "Bank charges", "Transfers & Other", "Bank charges"), ("min bal", "Bank charges", "Transfers & Other", "Bank charges"),
    ("non maint", "Bank charges", "Transfers & Other", "Bank charges"), ("late fee", "Bank charges", "Transfers & Other", "Bank charges"),
    ("annual fee", "Bank charges", "Transfers & Other", "Bank charges"), ("convenience fee", "Bank charges", "Transfers & Other", "Bank charges"),
    ("surcharge", "Bank charges", "Transfers & Other", "Bank charges"),
    # Obligations
    ("emi", "Loan EMI", "Transfers & Other", "Loan repayment"), ("loan", "Loan repayment", "Transfers & Other", "Loan repayment"),
    ("credit card", "Credit card bill", "Transfers & Other", "Card repayment"),
    ("atm", "ATM withdrawal", "Transfers & Other", "Cash withdrawal"), ("cash withdrawal", "ATM withdrawal", "Transfers & Other", "Cash withdrawal"),
]

INCOME_WORDS = ["salary", "allowance", "stipend", "payroll", "scholarship", "wages", "pocket money", "internship"]
REFUND_WORDS = ["refund", "cashback", "reversal", "reversed", "rev ", "chargeback"]
OWN_TRANSFER_WORDS = ["self", "own account", "own a/c", "transfer from savings", "sweep", "fd closure", "to self"]
BORROW_WORDS = ["loan disbursal", "borrowed", "loan credit", "advance from"]
P2P_WORDS = ["upi from", "received from", "neft from", "imps from", "transfer from"]
P2P_OUT_WORDS = ["upi to", "sent to", "transfer to", "neft to", "imps to", "paid to"]

_UPI_NOISE = re.compile(r"\b(upi|neft|imps|rtgs|pos|ecom|ach|nach|txn|ref|no|id|vpa|p2p|p2m)\b|[/\-_@#:*]|\d{4,}", re.I)


_PATTERNS: dict[str, re.Pattern] = {}


def _match(kw: str, text: str) -> bool:
    """Short keywords must be whole words (so 'ola' != 'cola', 'bus' != 'business');
    longer ones only need a word start (so 'domino' matches "domino's")."""
    p = _PATTERNS.get(kw)
    if p is None:
        k = re.escape(kw.strip())
        p = re.compile(rf"(?<![a-z0-9]){k}s?(?![a-z0-9])" if len(kw.strip()) <= 4 else rf"(?<![a-z0-9]){k}")
        _PATTERNS[kw] = p
    return bool(p.search(text))


def merchant_key(description: str) -> str:
    t = _UPI_NOISE.sub(" ", description.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return " ".join(t.split()[:3]) or description.lower()[:20]


def categorize(description: str, txn_type: str, overrides: dict[str, str] | None = None) -> dict:
    """Return merchant, category, subcategory, source, and (for credits) credit_kind."""
    d = f" {description.lower()} "
    key = merchant_key(description)
    out = {"merchant": None, "merchant_key": key, "category": "Uncertain", "subcategory": None,
           "category_source": "uncertain", "credit_kind": None}

    if txn_type == "credit":
        if any(w in d for w in REFUND_WORDS):
            kind = "refund"
        elif any(w in d for w in OWN_TRANSFER_WORDS):
            kind = "own_transfer"
        elif any(w in d for w in BORROW_WORDS):
            kind = "borrowed"
        elif any(w in d for w in INCOME_WORDS):
            kind = "income"
        elif any(w in d for w in P2P_WORDS):
            kind = "p2p_in"
        else:
            kind = "uncertain"
        out["credit_kind"] = kind
        out["category"] = {"income": "Income", "refund": "Refund", "own_transfer": "Own transfer",
                           "borrowed": "Borrowed", "p2p_in": "Transfers in"}.get(kind, "Uncertain")
        out["category_source"] = "rule" if kind != "uncertain" else "uncertain"
        out["merchant"] = key.title()
        if overrides and key in overrides:
            out["category"] = overrides[key]
            out["category_source"] = "user"
            out["credit_kind"] = {"Income": "income", "Refund": "refund", "Own transfer": "own_transfer",
                                  "Borrowed": "borrowed", "Transfers in": "p2p_in"}.get(overrides[key], "uncertain")
        return out

    for kw, name, cat, sub in MERCHANTS:
        if _match(kw, d):
            out.update(merchant=name, category=cat, subcategory=sub, category_source="rule")
            break
    else:
        if any(w in d for w in OWN_TRANSFER_WORDS):
            out.update(merchant="Own account", category="Transfers & Other", subcategory="Own-account transfer", category_source="rule")
        elif any(w in d for w in P2P_OUT_WORDS):
            out.update(merchant=key.title(), category="Transfers & Other", subcategory="Person-to-person", category_source="rule")
        else:
            out["merchant"] = key.title()
    if overrides and key in overrides:
        out["category"] = overrides[key]
        out["category_source"] = "user"
    return out


def is_essential(category: str, subcategory: str | None) -> bool:
    if category in ESSENTIAL:
        return True
    if category == "Food" and subcategory == "Groceries":
        return True
    if subcategory in {"Loan repayment", "Card repayment"}:
        return True
    return False
