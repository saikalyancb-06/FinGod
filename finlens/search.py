"""Search/API layer for Smart Spending.

Live source: SerpAPI (Google Maps for restaurants, Google Shopping for products).
Results are cached for 6 hours and every listing carries its source link and
retrieval timestamp. If no SERP_API key is configured, or the live call fails,
a clearly-labelled DEMO catalog is used instead; it is never presented as live.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta

import httpx

from shared import config

from .db import SearchCache, SessionLocal

log = logging.getLogger("finlens.search")
CACHE_TTL = timedelta(hours=6)


def _price_from_text(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", str(s))]
    if not nums:
        return None
    return sum(nums[:2]) / len(nums[:2])  # midpoint for ranges like "₹200–400"


def _cache_get(key: str):
    with SessionLocal() as db:
        row = db.get(SearchCache, key)
        if row and datetime.utcnow() - row.fetched_at < CACHE_TTL:
            return json.loads(row.payload), row.fetched_at
    return None


def _cache_put(key: str, payload):
    try:
        with SessionLocal() as db:
            row = db.get(SearchCache, key)
            if row:
                row.payload, row.fetched_at = json.dumps(payload), datetime.utcnow()
            else:
                db.add(SearchCache(key=key, payload=json.dumps(payload)))
            db.commit()
    except Exception as exc:  # caching is best-effort; never lose live results over it
        log.warning("search cache write skipped: %s", exc)


def _serp(params: dict) -> tuple[dict, datetime]:
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    hit = _cache_get(key)
    if hit:
        return hit
    r = httpx.get("https://serpapi.com/search.json", params={**params, "api_key": config.SERP_API_KEY}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    _cache_put(key, data)
    return data, datetime.utcnow()


FOOD_TYPE_WORDS = ("restaurant", "food", "cafe", "café", "bakery", "eatery", "dhaba", "biryani", "pizza", "burger", "canteen",
                   "mess", "kitchen", "sweet", "juice", "dessert", "ice cream", "grill", "diner", "bistro", "takeaway", "meal",
                   "shawarma", "momo", "tiffin", "chaat", "snack", "coffee", "tea", "brewery", "pub", "bar")
_ZOMATO_ENTRY = re.compile(r"([A-Z0-9][^.;·|]{1,60}?)\.\s*(\d\.\d)\.\s*([^.;·|₹]{2,80}?)\.?\s*₹\s?([\d,]+)\s*for\s*(one|two)", re.I)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())[:24]


def _is_food_place(r: dict) -> bool:
    blob = " ".join([str(r.get("type") or "")] + [str(t) for t in (r.get("types") or [])]).lower()
    return any(w in blob for w in FOOD_TYPE_WORDS)


def search_zomato_prices(query: str, location: str) -> tuple[list[dict], list[dict]]:
    """One Google web search: Zomato delivery-listing snippets ('Name. 4.2. Cuisines. ₹300 for one')
    and the local pack (price range per person). Returns (zomato_entries, local_places)."""
    data, ts = _serp({"engine": "google", "q": f"{query} delivery {location} zomato", "gl": "in", "hl": "en", "num": 10})
    stamp = ts.isoformat(timespec="seconds") + "Z"
    entries, seen = [], set()
    for o in data.get("organic_results", []):
        if "zomato.com" not in str(o.get("link", "")) and "zomato" not in str(o.get("source", "")).lower():
            continue
        for m in _ZOMATO_ENTRY.finditer(o.get("snippet") or ""):
            name = m.group(1).strip(" ;·-")
            # drop a page heading glued to the first entry ("Koramangala Restaurants La Pino'z Pizza")
            name = re.split(r"\b(?:Restaurants|Bangalore|Bengaluru|Delivery)\b[,]?\s+", name)[-1].strip(" ;·-,")
            if _norm(name) in seen or len(name) < 3:
                continue
            seen.add(_norm(name))
            amt = float(m.group(4).replace(",", ""))
            per_one = amt / 2 if m.group(5).lower() == "two" else amt
            entries.append({
                "title": name, "price": per_one, "price_text": f"₹{amt:,.0f} for {m.group(5).lower()}",
                "price_basis": "Zomato average cost per person", "rating": float(m.group(2)), "reviews": None,
                "subtitle": m.group(3).strip(), "delivery": True, "thumbnail": None, "link": o.get("link"),
                "source": "Zomato listing via Google (SerpAPI)", "retrieved_at": stamp, "is_demo": False,
            })
    places = []
    lr = data.get("local_results")
    for p in (lr.get("places", []) if isinstance(lr, dict) else (lr or [])):
        places.append({"title": p.get("title"), "price_text": p.get("price"), "price": _price_from_text(p.get("price")),
                       "type": p.get("type"), "rating": p.get("rating"), "reviews": p.get("reviews")})
    return entries, places


def search_food(query: str, location: str, delivery: bool = False) -> list[dict]:
    maps = search_restaurants(query, location)
    try:
        zom, places = search_zomato_prices(query, location)
    except Exception as exc:  # price enrichment is best-effort
        log.info("zomato price lookup skipped: %s", exc)
        zom, places = [], []
    by_name = {_norm(z["title"]): z for z in zom}
    by_name.update({_norm(p["title"]): {**p, "price_basis": "Google price range per person"} for p in places if p.get("price")})
    merged, used = [], set()
    for r in maps:
        key = _norm(r["title"])
        hit = by_name.get(key) or next((v for k, v in by_name.items() if k and (k in key or key in k)), None)
        if hit and r.get("price") is None and hit.get("price"):
            r.update(price=hit["price"], price_text=hit["price_text"], price_basis=hit["price_basis"])
            if hit.get("delivery"):
                r["delivery"] = True
            used.add(_norm(hit["title"]))
        merged.append(r)
    names = {_norm(r["title"]) for r in merged}
    merged += [z for z in zom if _norm(z["title"]) not in names and _norm(z["title"]) not in used]
    return merged


def search_restaurants(query: str, location: str) -> list[dict]:
    data, ts = _serp({"engine": "google_maps", "type": "search", "q": f"{query} restaurants in {location}", "hl": "en", "gl": "in"})
    out = []
    for r in [x for x in data.get("local_results", []) if _is_food_place(x)][:20]:
        pid = r.get("place_id")
        link = (f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(r.get('title', ''))}&query_place_id={pid}"
                if pid else r.get("website"))
        price = _price_from_text(r.get("price"))
        out.append({
            "title": r.get("title"), "price": price,
            "price_text": r.get("price") or "Price not listed",
            "price_basis": "Approx. price per person from Google Maps" if price else "Not listed",
            "rating": r.get("rating"), "reviews": r.get("reviews"),
            "subtitle": r.get("type") or ", ".join(r.get("types", [])[:2]), "address": r.get("address"),
            "delivery": "delivery" in json.dumps(r.get("service_options", {})).lower() or None,
            "open_now": (r.get("open_state") or "").lower().startswith("open") if r.get("open_state") else None,
            "thumbnail": r.get("thumbnail"), "link": link, "source": "Google Maps via SerpAPI",
            "retrieved_at": ts.isoformat(timespec="seconds") + "Z", "is_demo": False,
        })
    return out


def search_products(query: str, location: str) -> list[dict]:
    data, ts = _serp({"engine": "google_shopping", "q": query, "location": "India" if "india" not in location.lower() else location,
                      "gl": "in", "hl": "en", "google_domain": "google.co.in"})
    out = []
    for r in data.get("shopping_results", [])[:30]:
        price = r.get("extracted_price") or _price_from_text(r.get("price"))
        out.append({
            "title": r.get("title"), "price": price, "price_text": r.get("price") or "Price not listed",
            "price_basis": f"Listed price at {r.get('source')}", "old_price": r.get("extracted_old_price"),
            "rating": r.get("rating"), "reviews": r.get("reviews"), "subtitle": r.get("source"),
            "delivery": r.get("delivery"), "extensions": r.get("extensions", []),
            "thumbnail": r.get("thumbnail"), "link": r.get("link") or r.get("product_link"),
            "source": f"{r.get('source', 'Seller')} via Google Shopping (SerpAPI)",
            "retrieved_at": ts.isoformat(timespec="seconds") + "Z", "is_demo": False,
        })
    return out


# ---------------- demo catalog (fallback, clearly labelled) ----------------
_DEMO = {
    "food": [
        ("Demo Biryani House", "Hyderabadi, Biryani", ["chicken biryani", "biryani", "mutton biryani"], 220, 4.4, 1200),
        ("Demo Dum Biryani Point", "Biryani", ["chicken biryani", "biryani", "veg biryani"], 180, 4.2, 640),
        ("Demo Andhra Meals", "Andhra, Biryani, Meals", ["biryani", "meals", "thali"], 160, 4.1, 380),
        ("Demo Donne Biryani", "Karnataka, Biryani", ["chicken biryani", "biryani", "donne biryani"], 199, 4.5, 2100),
        ("Demo Paradise Style Kitchen", "Biryani, Kebabs", ["chicken biryani", "biryani", "kebab"], 260, 4.3, 900),
        ("Demo Burger Junction", "Burgers, Fast food", ["burger", "fries"], 189, 4.3, 856),
        ("Demo Smash Burgers", "Burgers", ["burger", "chicken burger"], 175, 4.2, 629),
        ("Demo Pizza Corner", "Pizza, Italian", ["pizza", "pasta"], 249, 4.0, 1500),
        ("Demo Dosa Camp", "South Indian", ["dosa", "idli", "south indian"], 90, 4.6, 3200),
        ("Demo Noodle Bar", "Chinese, Asian", ["noodles", "fried rice", "momos"], 170, 4.1, 540),
        ("Demo Momo Hub", "Tibetan, Momos", ["momos", "momo"], 120, 4.4, 980),
        ("Demo Rolls Express", "Rolls, Kathi rolls", ["roll", "kathi roll", "wrap"], 130, 4.2, 700),
    ],
    "fashion": [
        ("Demo Canvas Sneakers (white)", "Casual shoes", ["shoes", "sneakers", "casual", "college"], 999, 4.2, 5400),
        ("Demo Running Shoes Lite", "Sports shoes", ["shoes", "running", "sports", "college"], 1499, 4.3, 8100),
        ("Demo Leather Derby", "Formal shoes", ["shoes", "formal", "office"], 1899, 4.0, 1300),
        ("Demo Slip-on Loafers", "Casual shoes", ["shoes", "loafers", "casual"], 799, 3.9, 2200),
        ("Demo High-top Sneakers", "Casual shoes", ["shoes", "sneakers", "streetwear"], 1299, 4.1, 3100),
        ("Demo Cotton Crew T-shirt", "T-shirts", ["t-shirt", "tshirt", "casual", "college"], 349, 4.2, 12000),
        ("Demo Oversized Graphic Tee", "T-shirts", ["t-shirt", "tshirt", "oversized", "streetwear"], 499, 4.0, 4300),
        ("Demo Slim Fit Jeans", "Jeans", ["jeans", "denim", "casual"], 1199, 4.1, 6700),
        ("Demo Denim Jacket", "Jackets", ["jacket", "denim"], 1599, 4.0, 900),
        ("Demo Kurta Set", "Ethnic wear", ["kurta", "ethnic", "festive"], 1099, 4.3, 2500),
    ],
    "shopping": [
        ("Demo Wireless Earbuds", "Audio", ["earbuds", "headphones", "wireless", "bluetooth"], 1299, 4.1, 25000),
        ("Demo Over-ear Headphones", "Audio", ["headphones", "over-ear", "wireless"], 2499, 4.3, 9000),
        ("Demo Wired Earphones", "Audio", ["earphones", "headphones", "wired"], 399, 4.0, 40000),
        ("Demo Laptop Backpack 30L", "Bags", ["backpack", "bag", "laptop"], 899, 4.2, 15000),
        ("Demo 10000mAh Power Bank", "Mobile accessories", ["power bank", "charger"], 999, 4.3, 30000),
        ("Demo Scientific Calculator", "Stationery", ["calculator", "scientific"], 650, 4.5, 7000),
        ("Demo Study Table Lamp", "Home", ["lamp", "desk lamp", "study"], 549, 4.0, 3500),
        ("Demo Smartwatch Basic", "Wearables", ["smartwatch", "watch", "fitness"], 1799, 3.9, 11000),
        ("Demo Steel Water Bottle 1L", "Home", ["bottle", "water bottle"], 399, 4.4, 18000),
        ("Demo Mechanical Keyboard", "Computer accessories", ["keyboard", "mechanical"], 2199, 4.2, 4100),
    ],
}


def demo_search(category: str, query: str) -> list[dict]:
    q = query.lower()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    items = []
    for title, sub, tags, price, rating, reviews in _DEMO.get(category, []):
        hit = any(t in q or q in t for t in tags) or any(w in " ".join(tags) for w in q.split() if len(w) > 3)
        if not hit:
            continue
        items.append({
            "title": title, "price": float(price), "price_text": f"₹{price}", "price_basis": "DEMO price (illustrative)",
            "rating": rating, "reviews": reviews, "subtitle": sub, "delivery": True if category == "food" else "Demo delivery",
            "thumbnail": None, "link": "https://www.google.com/search?q=" + urllib.parse.quote(f"{query} {sub}"),
            "source": "DEMO catalog - not a live listing", "retrieved_at": now, "is_demo": True, "tags": tags,
        })
    return items


def search(category: str, query: str, location: str, delivery: bool = False) -> dict:
    """Returns {'results': [...], 'mode': 'live'|'demo', 'notice': str|None}."""
    if config.SERP_API_KEY:
        try:
            res = search_food(query, location, delivery) if category == "food" else search_products(query, location)
            if res:
                return {"results": res, "mode": "live", "notice": None}
            notice = "Live search returned no results; showing DEMO catalog."
        except Exception as exc:  # network / quota / key error
            log.warning("SerpAPI failed: %s", exc)
            notice = f"Live search unavailable ({str(exc)[:80]}); showing DEMO catalog."
    else:
        notice = "SERP_API key not configured; showing DEMO catalog (illustrative, not live listings)."
    return {"results": demo_search(category, query), "mode": "demo", "notice": notice}
