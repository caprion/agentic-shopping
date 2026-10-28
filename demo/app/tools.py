"""Tool schemas + dispatcher for Aria (owned by B2).

`TOOLS` is a list of JSON-schema function declarations (``name``/``description``/
``parameters``). `run_tool(conn, name, args, session)` executes exactly one tool
and returns::

    {"ok": bool, "result": any, "citations": [ {...} ], "error": str|None}

Every factual claim (price / rating / discount / title / review quote) carries a
citation. Product citations use ``field``; review citations use ``review_id``:

    {"product_id", "field", "value", "quote"}            # a product fact
    {"product_id", "review_id", "value", "quote"}        # a review quote

B1's `retrieval`/`personalize` modules are imported lazily *inside* functions so
this module imports cleanly before they land. If they are missing, tools degrade
to plain SQLite lookups. A tool exception never propagates: it becomes
``{"ok": False, "error": ...}``.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import uuid

log = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.dirname(_APP_DIR)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

from app import db  # orchestrator-owned; pure sqlite, safe import

DEFAULT_BUDGET = 2_000.0  # $, session cap

AXES = ["price", "rating", "brand", "weight", "battery", "noise cancelling",
        "colour", "capacity", "warranty", "comfort"]
_AXIS_KEYWORDS = {
    "price": ["price", "cost", "cheap", "₹", "$", "inr", "budget", "rs"],
    "rating": ["rating", "stars", "star", "review"],
    "brand": ["brand", "sony", "apple", "samsung", "boat", "jbl", "bose"],
    "weight": ["weight", "grams", "gram", "kg", "light"],
    "battery": ["battery", "hours", "mah", "playtime", "charge"],
    "noise cancelling": ["noise canc", "anc", "noise"],
    "colour": ["colour", "color", "black", "white", "blue"],
    "capacity": ["capacity", "gb", "tb", "storage", "litre", "l "],
    "warranty": ["warranty", "year", "guarantee"],
    "comfort": ["comfort", "comfortable", "ergonomic", "cushion", "padding"],
}

# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------
def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props,
            "required": required or list(props.keys())}


def _string(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _int(desc: str) -> dict:
    return {"type": "integer", "description": desc}


TOOLS: list[dict] = [
    {"name": "search_products",
     "description": "Search the catalogue for products matching a natural-language query, applying any price/rating/brand constraints. Returns a compact list with id, title, brand, price, mrp, discount %, rating, rating_count, category, image_url and a one-line reason.",
     "parameters": _obj({"query": _string("Natural language query, e.g. 'noise cancelling headphones under 5000'"),
                         "k": _int("Max results (default 10)"),
                         "constraints": {"type": "object", "description": "Optional explicit constraints (price_min, price_max, rating_min, brand, category)"}},
                        required=["query"])},
    {"name": "get_product",
     "description": "Get full details (plus variants and review count) for one product_id.",
     "parameters": _obj({"product_id": _string("Catalogue product_id (parent_asin)")},
                        required=["product_id"])},
    {"name": "get_reviews",
     "description": "Get reviews for a product, each with review_id and a short quote for citation.",
     "parameters": _obj({"product_id": _string("Catalogue product_id"),
                         "limit": _int("Max reviews (default 5)"),
                         "min_rating": {"type": "number", "description": "Only reviews rated >= this"}},
                        required=["product_id"])},
    {"name": "compare_products",
     "description": "Compare 2+ products across axes extracted from the request (price, rating, brand, weight, battery, noise cancelling, colour, capacity, warranty). Returns a per-axis table with DB-sourced evidence.",
     "parameters": _obj({"product_ids": {"type": "array", "items": {"type": "string"}, "description": "2 or more product_ids to compare"},
                         "query": _string("The user's comparison request, used to pick axes")},
                        required=["product_ids"])},
    {"name": "similar_products",
     "description": "Find products similar to a given product_id.",
     "parameters": _obj({"product_id": _string("Catalogue product_id"),
                         "k": _int("Max results (default 8)")},
                        required=["product_id"])},
    {"name": "add_to_cart",
     "description": "Add a product to the session cart (mock). Returns updated cart + running total vs budget.",
     "parameters": _obj({"product_id": _string("Catalogue product_id"),
                         "qty": _int("Quantity (default 1)")},
                        required=["product_id"])},
    {"name": "remove_from_cart",
     "description": "Remove a product from the session cart (mock).",
     "parameters": _obj({"product_id": _string("Catalogue product_id")},
                        required=["product_id"])},
    {"name": "view_cart",
     "description": "Show the current session cart with items, prices and total vs budget.",
     "parameters": _obj({}, required=[])},
    {"name": "checkout_cart",
     "description": "Mock checkout of the session cart. Computes the total from catalogue prices only and refuses if it exceeds the session budget cap (default $2,000). No real payment happens.",
     "parameters": _obj({"ship_to": _string("Delivery label (mock)")}, required=[])},
    {"name": "track_order",
     "description": "Track a mock order by order_id.",
     "parameters": _obj({"order_id": _string("Order id returned by checkout_cart")},
                        required=["order_id"])},
    {"name": "get_price_history",
     "description": "Return a SIMULATED price series for a product (derived from list price, current price and popularity). The result is explicitly labelled 'simulated' — never present it as real history.",
     "parameters": _obj({"product_id": _string("Catalogue product_id")},
                        required=["product_id"])},
    {"name": "web_search_external",
     "description": "Search the live web (Tavily) for items NOT in the catalogue — powers the 'Buy for Me' flow. Returns external results with URLs. Fails gracefully (ok:false) when offline or key missing.",
     "parameters": _obj({"query": _string("Web search query"),
                         "max_results": _int("Max results (default 5)")},
                        required=["query"])},
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _short(s: object, n: int = 180) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def _cite(product_id, field, value, quote=None) -> dict:
    c: dict = {"product_id": product_id, "field": field, "value": value}
    if quote is not None:
        c["quote"] = _short(quote, 200)
    return c


def _review_cite(product_id, review_id, value, quote) -> dict:
    return {"product_id": product_id, "review_id": review_id, "value": value,
            "quote": _short(quote, 200)}


def _retrieval():
    # test hook: force the SQL fallback (used by B2's own selftest to stay
    # offline and fast, independent of B1's heavy model loading)
    if os.environ.get("ARIA_NO_RETRIEVAL") == "1":
        return None
    try:
        import app.retrieval as r  # noqa: F401
        return r
    except Exception as exc:  # noqa: BLE001
        log.debug("retrieval not importable yet: %s", exc)
        return None


def _safe_fetchall(conn, sql: str, params=()) -> list[dict]:
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.debug("query skipped (table may not exist): %s", exc)
        return []


def _discount_pct(price, mrp) -> float:
    if price and mrp and mrp > price:
        return round((1 - price / mrp) * 100, 1)
    return 0.0


def _product_facts(conn, pid: str, reason: str | None = None):
    """Return (compact dict, citations) for a product, or (None, [])."""
    row = db.product_row(conn, pid)
    if row is None:
        return None, []
    p = db.product_dict(row)
    price, mrp = p.get("price"), p.get("price_mrp")
    title = p.get("title") or ""
    disc = _discount_pct(price, mrp)
    compact = {
        "id": pid, "title": title, "brand": p.get("brand"),
        "price": price, "mrp": mrp, "discount": disc,
        "rating": p.get("rating"), "rating_count": p.get("rating_count"),
        "category": p.get("category_main"), "image_url": p.get("image_url"),
        "in_stock": bool(p.get("in_stock", 1)), "reason": reason,
    }
    cites = [
        _cite(pid, "title", title, title),
        _cite(pid, "price", price, title),
        _cite(pid, "rating", p.get("rating"), title),
        _cite(pid, "rating_count", p.get("rating_count"), title),
        _cite(pid, "discount", disc, title),
        _cite(pid, "brand", p.get("brand"), title),
        _cite(pid, "stock", bool(p.get("in_stock", 1)), title),
    ]
    cites = [c for c in cites if c.get("value") is not None]
    return compact, cites


_STOPWORDS = {"the", "a", "an", "for", "with", "under", "and", "or",
             "in", "of", "on", "at", "to", "se", "kam", "please", "show",
             "find", "me", "i", "want", "need", "some", "buy", "best"}


def _local_constraints(query: str) -> dict:
    """Lightweight deterministic constraint parser used only when B1's
    `retrieval.parse_constraints` is not yet importable."""
    q = (query or "").lower()
    c: dict = {}
    m = re.search(r"(?:under|below|less\s+than|max(?:imum)?)\s*(?:₹|rs\.?|inr|\$)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", q)
    if m:
        c["price_max"] = float(m.group(1).replace(",", ""))
    m = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*se\s*kam", q)
    if m and "price_max" not in c:
        c["price_max"] = float(m.group(1).replace(",", ""))
    m = re.search(r"(?:above|over|more\s+than|min(?:imum)?)\s*(?:₹|rs\.?|inr|\$)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", q)
    if m:
        c["price_min"] = float(m.group(1).replace(",", ""))
    m = re.search(r"(?:at\s+least|min(?:imum)?|above)\s*([0-9](?:\.[0-9]+)?)\s*(?:stars?|★)", q) \
        or re.search(r"([0-9](?:\.[0-9]+)?)\s*(?:stars?|★)", q)
    if m:
        c["rating_min"] = float(m.group(1))
    return c


def _hit_pid(h) -> str | None:
    if isinstance(h, str):
        return h
    if isinstance(h, dict):
        return h.get("product_id")
    return getattr(h, "product_id", None)


def _sql_search(conn, query: str, constraints: dict, k: int) -> list[str]:
    where, params = [], []
    if constraints.get("price_min") is not None:
        where.append("price >= ?"); params.append(constraints["price_min"])
    if constraints.get("price_max") is not None:
        where.append("price <= ?"); params.append(constraints["price_max"])
    if constraints.get("rating_min") is not None:
        where.append("rating >= ?"); params.append(constraints["rating_min"])
    brand = constraints.get("brand") or []
    if brand:
        where.append("lower(brand) IN (%s)" % ",".join("?" * len(brand)))
        params.extend([str(b).lower() for b in brand])
    cat = constraints.get("category")
    if cat:
        where.append("lower(category_main)=lower(?)"); params.append(cat)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    kw = (constraints.get("free_text") or query or "").strip()
    order = "popularity DESC, rating DESC"
    if kw:
        tokens = [t for t in re.split(r"[^a-z0-9]+", kw.lower()) if t
                  and t not in _STOPWORDS][:6]
        if tokens:
            cond = " OR ".join(
                "(title LIKE ? OR brand LIKE ? OR description LIKE ?)"
                for _ in tokens)
            sql = (f"SELECT product_id FROM products {w} AND ({cond}) "
                   f"ORDER BY {order} LIMIT ?")
            flat = []
            for t in tokens:
                flat += [f"%{t}%", f"%{t}%", f"%{t}%"]
            rows = _safe_fetchall(conn, sql, params + flat + [int(k)])
        else:
            sql = f"SELECT product_id FROM products {w} ORDER BY {order} LIMIT ?"
            rows = _safe_fetchall(conn, sql, params + [int(k)])
    else:
        sql = f"SELECT product_id FROM products {w} ORDER BY {order} LIMIT ?"
        rows = _safe_fetchall(conn, sql, params + [int(k)])
    return [r["product_id"] for r in rows]


def _sql_reviews(conn, pid: str, limit: int, min_rating) -> list[dict]:
    where, params = ["product_id=?"], [pid]
    if min_rating is not None:
        where.append("rating >= ?"); params.append(min_rating)
    sql = (f"SELECT review_id, rating, title, text, helpful FROM reviews "
           f"WHERE {' AND '.join(where)} ORDER BY helpful DESC, rating DESC LIMIT ?")
    return _safe_fetchall(conn, sql, params + [int(limit)])


def _sql_similar(conn, pid: str, k: int) -> list[str]:
    row = db.product_row(conn, pid)
    if row is None:
        return []
    sql = ("SELECT product_id FROM products WHERE product_id != ? AND "
           "(brand=? OR category_main=?) ORDER BY popularity DESC, rating DESC LIMIT ?")
    rows = _safe_fetchall(conn, sql, [pid, row["brand"], row["category_main"],
                                      int(k)])
    return [r["product_id"] for r in rows]


# --------------------------------------------------------------------------
# tool implementations — each returns (result, citations, error)
# --------------------------------------------------------------------------
def _t_search_products(conn, args, session):
    query = (args.get("query") or "").strip()
    k = int(args.get("k") or 10)
    explicit = args.get("constraints") or {}
    r = _retrieval()
    parsed = dict(explicit)
    if not parsed and r is not None and hasattr(r, "parse_constraints"):
        try:
            parsed = r.parse_constraints(query) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("parse_constraints failed: %s", exc)
    if not parsed:
        parsed = _local_constraints(query)
    hits: list = []
    if r is not None and hasattr(r, "hybrid_search"):
        try:
            hits = r.hybrid_search(conn, query, parsed, k, session.get("user_id")) or []
        except Exception as exc:  # noqa: BLE001
            log.warning("hybrid_search failed (%s); falling back to SQL", exc)
            hits = []
    if not hits:
        hits = _sql_search(conn, query, parsed, k)

    products, cites = [], []
    for h in hits:
        pid = _hit_pid(h)
        if not pid:
            continue
        score = getattr(h, "score", None) if not isinstance(h, (str, dict)) else None
        reason = f"matches your query (score {score:.2f})" if score is not None \
            else "matches your query"
        compact, cs = _product_facts(conn, pid, reason)
        if compact:
            products.append(compact)
            cites.extend(cs)
    return {"query": query, "count": len(products), "products": products}, cites, None


def _t_get_product(conn, args, session):
    pid = args.get("product_id")
    if not pid:
        return None, [], "product_id is required"
    compact, cites = _product_facts(conn, pid)
    if not compact:
        return None, [], f"product {pid} not found in catalogue"
    p = db.product_dict(db.product_row(conn, pid))
    variants = _safe_fetchall(
        conn, "SELECT variant_id, title, price, image_url, attributes "
              "FROM product_variants WHERE product_id=?", (pid,))
    for v in variants:
        v["attributes"] = db.jload(v.get("attributes"), {})
    reviews_count = _safe_fetchall(
        conn, "SELECT COUNT(*) AS n FROM reviews WHERE product_id=?", (pid,))
    detail = dict(compact)
    detail.update({
        "description": p.get("description"),
        "features": p.get("features"),
        "store": p.get("store"),
        "variants": variants,
        "reviews_count": reviews_count[0]["n"] if reviews_count else 0,
    })
    return detail, cites, None


def _t_get_reviews(conn, args, session):
    pid = args.get("product_id")
    limit = int(args.get("limit") or 5)
    min_rating = args.get("min_rating")
    if not pid:
        return None, [], "product_id is required"
    r = _retrieval()
    reviews = []
    if r is not None and hasattr(r, "get_reviews"):
        try:
            reviews = r.get_reviews(conn, pid, limit, min_rating) or []
        except Exception as exc:  # noqa: BLE001
            log.warning("retrieval.get_reviews failed: %s", exc)
    if not reviews:
        reviews = _sql_reviews(conn, pid, limit, min_rating)
    out, cites = [], []
    for rv in reviews:
        rid = rv.get("review_id")
        quote = _short(rv.get("text") or rv.get("title") or "", 180)
        out.append({"review_id": rid, "rating": rv.get("rating"),
                    "title": rv.get("title"), "quote": quote,
                    "helpful": rv.get("helpful")})
        cites.append(_review_cite(pid, rid, rv.get("rating"), quote))
    return {"product_id": pid, "count": len(out), "reviews": out}, cites, None


def _t_similar_products(conn, args, session):
    pid = args.get("product_id")
    k = int(args.get("k") or 8)
    if not pid:
        return None, [], "product_id is required"
    r = _retrieval()
    sims = []
    if r is not None and hasattr(r, "similar_products"):
        try:
            sims = r.similar_products(conn, pid, k) or []
        except Exception as exc:  # noqa: BLE001
            log.warning("retrieval.similar_products failed: %s", exc)
    if not sims:
        sims = _sql_similar(conn, pid, k)
    products, cites = [], []
    for s in sims:
        spid = _hit_pid(s)
        if not spid or spid == pid:
            continue
        compact, cs = _product_facts(conn, spid, "similar item")
        if compact:
            products.append(compact)
            cites.extend(cs)
    return {"product_id": pid, "count": len(products), "products": products}, \
        cites, None


def _extract_axes(query: str) -> list[str]:
    q = (query or "").lower()
    found = [a for a in AXES if any(k in q for k in _AXIS_KEYWORDS[a])]
    return found or ["price", "rating"]


def _find_attr(p: dict, keys: list[str]):
    feats = " ".join(str(f) for f in (p.get("features") or []))
    desc = p.get("description") or ""
    text = f"{feats} {desc}"
    tl = text.lower()
    for key in keys:
        idx = tl.find(key)
        if idx >= 0:
            snippet = text[max(0, idx - 12): idx + 48].strip()
            return snippet, snippet
    return None, None


def _axis_value(p: dict, axis: str):
    if axis == "price":
        return p.get("price"), p.get("title"), "price"
    if axis == "rating":
        return p.get("rating"), p.get("title"), "rating"
    if axis == "brand":
        return p.get("brand"), p.get("title"), "brand"
    val, quote = _find_attr(p, _AXIS_KEYWORDS.get(axis, [axis]))
    return val, quote, axis


def _t_compare_products(conn, args, session):
    pids = [x for x in (args.get("product_ids") or []) if x]
    query = args.get("query") or ""
    if len(pids) < 2:
        return None, [], "compare_products needs at least 2 product_ids"
    axes = _extract_axes(query)
    prods, missing = [], []
    for pid in pids:
        p = db.product_dict(db.product_row(conn, pid))
        if p is None:
            missing.append(pid)
        else:
            prods.append(p)
    table, cites = [], []
    for axis in axes:
        cells = []
        for p in prods:
            value, quote, field = _axis_value(p, axis)
            if value is None:
                cells.append({"product_id": p["product_id"], "value": None,
                              "citation": None})
                continue
            c = _cite(p["product_id"], field, value, quote)
            cites.append(c)
            cells.append({"product_id": p["product_id"], "value": value,
                          "citation": c})
        table.append({"axis": axis, "cells": cells})
    summary = [{"id": p["product_id"], "title": p["title"],
                "price": p["price"], "rating": p["rating"]} for p in prods]
    return {"axes": axes, "table": table, "products": summary,
            "missing": missing}, cites, None


# ---------------- cart / commerce ------------------------------------------
def _ensure_cart(conn, session) -> str:
    cart_id = session.get("cart_id")
    if cart_id:
        row = _safe_fetchall(conn, "SELECT cart_id FROM carts WHERE cart_id=?",
                             (cart_id,))
        if row:
            return cart_id
    cart_id = f"cart_{uuid.uuid4().hex[:12]}"
    _safe_fetchall(conn, "INSERT INTO carts(cart_id,session_id,user_id,status,"
                         "created_ts,updated_ts) VALUES(?,?,?,?,?,?)",
                   (cart_id, session.get("session_id"), session.get("user_id"),
                    "open", time.time(), time.time()))
    conn.commit()
    session["cart_id"] = cart_id
    return cart_id


def _cart_view(conn, session):
    cart_id = session.get("cart_id")
    budget = float(session.get("budget", DEFAULT_BUDGET))
    if not cart_id:
        return {"cart_id": None, "items": [], "total": 0, "budget": budget,
                "message": "cart is empty"}, []
    items = _safe_fetchall(
        conn, "SELECT ci.product_id, ci.qty, ci.unit_price, p.title, p.price "
              "FROM cart_items ci LEFT JOIN products p "
              "ON p.product_id = ci.product_id WHERE ci.cart_id=?", (cart_id,))
    total = round(sum((it["price"] if it["price"] is not None
                       else it["unit_price"] or 0) * (it["qty"] or 1)
                      for it in items), 2)
    cites = [_cite(it["product_id"], "price",
                   it["price"] if it["price"] is not None else it["unit_price"],
                   it.get("title")) for it in items
             if (it["price"] is not None or it["unit_price"] is not None)]
    return {"cart_id": cart_id, "items": items, "total": total,
            "budget": budget}, cites


def _t_add_to_cart(conn, args, session):
    pid = args.get("product_id")
    qty = max(1, int(args.get("qty") or 1))
    if not pid:
        return None, [], "product_id is required"
    row = db.product_row(conn, pid)
    if row is None:
        return None, [], f"product {pid} not found in catalogue"
    if not row["in_stock"]:
        return None, [], f"{row['title']} is out of stock"
    price = row["price"]
    cart_id = _ensure_cart(conn, session)
    existing = _safe_fetchall(conn, "SELECT qty FROM cart_items WHERE cart_id=? "
                                    "AND product_id=?", (cart_id, pid))
    if existing:
        _safe_fetchall(conn, "UPDATE cart_items SET qty=qty+?, unit_price=?, "
                             "added_ts=? WHERE cart_id=? AND product_id=?",
                       (qty, price, time.time(), cart_id, pid))
    else:
        _safe_fetchall(conn, "INSERT INTO cart_items(cart_id,product_id,qty,"
                             "unit_price,added_ts) VALUES(?,?,?,?,?)",
                       (cart_id, pid, qty, price, time.time()))
    conn.commit()
    view, cites = _cart_view(conn, session)
    view["added"] = {"product_id": pid, "title": row["title"], "qty": qty,
                     "price": price}
    cites.append(_cite(pid, "price", price, row["title"]))
    return view, cites, None


def _t_remove_from_cart(conn, args, session):
    pid = args.get("product_id")
    cart_id = session.get("cart_id")
    if not pid:
        return None, [], "product_id is required"
    if cart_id:
        _safe_fetchall(conn, "DELETE FROM cart_items WHERE cart_id=? AND "
                             "product_id=?", (cart_id, pid))
        conn.commit()
    view, cites = _cart_view(conn, session)
    view["removed"] = pid
    return view, cites, None


def _t_view_cart(conn, args, session):
    view, cites = _cart_view(conn, session)
    return view, cites, None


def _t_checkout_cart(conn, args, session):
    ship_to = args.get("ship_to") or "self"
    budget = float(session.get("budget", DEFAULT_BUDGET))
    cart_id = session.get("cart_id")
    if not cart_id:
        return {"status": "empty", "order_id": None, "total": 0, "budget": budget,
                "message": "cart is empty"}, [], None
    items = _safe_fetchall(
        conn, "SELECT ci.product_id, ci.qty, p.price, p.title FROM cart_items ci "
              "LEFT JOIN products p ON p.product_id=ci.product_id "
              "WHERE ci.cart_id=?", (cart_id,))
    if not items:
        return {"status": "empty", "order_id": None, "total": 0, "budget": budget,
                "message": "cart is empty"}, [], None
    total = round(sum((it["price"] or 0) * (it["qty"] or 1) for it in items), 2)
    cites = [_cite(it["product_id"], "price", it["price"], it.get("title"))
             for it in items if it.get("price") is not None]
    if total > budget:
        return {"status": "refused", "order_id": None, "total": total,
                "budget": budget,
                "reason": (f"Checkout total ${total:,.2f} exceeds session budget "
                           f"${budget:,.2f} — remove items or raise the cap"),
                "cart": items}, cites, None
    order_id = f"ord_{uuid.uuid4().hex[:12]}"
    eta_days = 4
    _safe_fetchall(conn, "INSERT INTO orders(order_id,session_id,user_id,total,"
                         "status,ship_to,eta_days,created_ts) VALUES(?,?,?,?,?,?,?,?)",
                   (order_id, session.get("session_id"), session.get("user_id"),
                    total, "confirmed", ship_to, eta_days, time.time()))
    for it in items:
        _safe_fetchall(conn, "INSERT INTO order_items(order_id,product_id,qty,"
                             "unit_price) VALUES(?,?,?,?)",
                       (order_id, it["product_id"], it["qty"], it["price"]))
    _safe_fetchall(conn, "UPDATE carts SET status='checked_out', updated_ts=? "
                         "WHERE cart_id=?", (time.time(), cart_id))
    conn.commit()
    return {"status": "confirmed", "order_id": order_id, "total": total,
            "budget": budget, "eta_days": eta_days, "ship_to": ship_to,
            "note": "mock order — no payment taken", "cart": items}, cites, None


def _t_track_order(conn, args, session):
    oid = args.get("order_id")
    if not oid:
        return None, [], "order_id is required"
    rows = _safe_fetchall(conn, "SELECT * FROM orders WHERE order_id=?", (oid,))
    if not rows:
        return None, [], f"order {oid} not found"
    row = rows[0]
    items = _safe_fetchall(
        conn, "SELECT oi.product_id, oi.qty, oi.unit_price, p.title "
              "FROM order_items oi LEFT JOIN products p "
              "ON p.product_id=oi.product_id WHERE oi.order_id=?", (oid,))
    cites = [_cite(it["product_id"], "price", it["unit_price"], it.get("title"))
             for it in items if it.get("unit_price") is not None]
    return {"order_id": oid, "status": row["status"], "total": row["total"],
            "eta_days": row["eta_days"], "ship_to": row["ship_to"],
            "note": "mock order tracking", "items": items}, cites, None


def _month_label(back: int) -> str:
    today = datetime.date.today()
    y, m = today.year, today.month - back
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _simulate_series(pid: str, price, mrp, months: int = 12) -> list[dict]:
    seed = int(hashlib.sha256(str(pid).encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    high = mrp if (mrp and price and mrp > price) else (price or 1000) * 1.15
    low = price or (high * 0.6)
    series = []
    for i in range(months):
        t = i / (months - 1)
        base = high - (high - low) * t
        noise = (rng.random() - 0.5) * 0.06 * high
        series.append({"date": _month_label(months - 1 - i),
                       "price": round(max(0.0, base + noise), 2),
                       "simulated": True})
    return series


def _t_get_price_history(conn, args, session):
    pid = args.get("product_id")
    if not pid:
        return None, [], "product_id is required"
    row = db.product_row(conn, pid)
    if row is None:
        return None, [], f"product {pid} not found in catalogue"
    series = _simulate_series(pid, row["price"], row["price_mrp"])
    cites = [_cite(pid, "price", row["price"], row["title"]),
             _cite(pid, "price_mrp", row["price_mrp"], row["title"])]
    return {"product_id": pid, "current_price": row["price"],
            "mrp": row["price_mrp"], "label": "simulated", "series": series,
            "note": ("Price history is SIMULATED (derived from list price, current "
                     "price and popularity) — it is NOT real historical data.")},\
        cites, None


def _t_web_search_external(conn, args, session):
    query = (args.get("query") or "").strip()
    max_results = int(args.get("max_results") or 5)
    if not query:
        return None, [], "query is required"
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return None, [], ("external web search unavailable: TAVILY_API_KEY is not "
                          "set in the environment")
    try:
        import requests
    except Exception:  # noqa: BLE001
        return None, [], "external web search unavailable: requests not installed"
    try:
        resp = requests.post("https://api.tavily.com/search",
                             json={"api_key": key, "query": query,
                                   "search_depth": "basic",
                                   "max_results": max_results},
                             timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("tavily search failed: %s", exc)
        return None, [], f"external web search unavailable: {type(exc).__name__}"
    results = [{"title": r.get("title"), "url": r.get("url"),
                "content": _short(r.get("content") or r.get("raw_content"), 300),
                "score": r.get("score")}
               for r in data.get("results") or []]
    cites = [{"product_id": r.get("url"), "field": "external_url",
              "value": r.get("title"), "quote": _short(r.get("content"), 160)}
             for r in results if r.get("url")]
    return {"query": query, "source": "tavily", "external": True,
            "answer": data.get("answer"), "results": results}, cites, None


_TOOL_FNS = {
    "search_products": _t_search_products,
    "get_product": _t_get_product,
    "get_reviews": _t_get_reviews,
    "compare_products": _t_compare_products,
    "similar_products": _t_similar_products,
    "add_to_cart": _t_add_to_cart,
    "remove_from_cart": _t_remove_from_cart,
    "view_cart": _t_view_cart,
    "checkout_cart": _t_checkout_cart,
    "track_order": _t_track_order,
    "get_price_history": _t_get_price_history,
    "web_search_external": _t_web_search_external,
}


def run_tool(conn, name: str, args: dict, session: dict) -> dict:
    args = args or {}
    session = session or {}
    fn = _TOOL_FNS.get(name)
    if fn is None:
        return {"ok": False, "result": None, "citations": [],
                "error": f"unknown tool: {name}"}
    try:
        result, citations, error = fn(conn, args, session)
    except Exception as exc:  # noqa: BLE001 — a tool must never kill the turn
        log.exception("tool %s raised", name)
        return {"ok": False, "result": None, "citations": [],
                "error": f"{type(exc).__name__}: {exc}"}
    if error:
        return {"ok": False, "result": result, "citations": citations,
                "error": error}
    return {"ok": True, "result": result, "citations": citations, "error": None}


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def _selftest() -> None:
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "aria_tools_selftest.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = db.connect(tmp)
    db.init_schema(conn)
    conn.execute("INSERT INTO products(product_id,title,brand,category_main,"
                 "price,price_mrp,rating,rating_count,in_stock,popularity) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)",
                 ("B0SELFTEST1", "Sony WH-1000XM5 Noise Cancelling Headphones",
                  "Sony", "Headphones", 348.0, 499.0, 4.6, 1200, 1, 5.0))
    conn.execute("INSERT INTO products(product_id,title,brand,category_main,"
                 "price,price_mrp,rating,rating_count,in_stock,popularity) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)",
                 ("B0SELFTEST2", "Soundcore Anker Life P2 True Wireless Earbuds",
                  "Soundcore", "Earbuds", 39.99, 59.99, 4.2, 8000, 1, 3.0))
    conn.execute("INSERT INTO reviews(product_id,user_id,rating,title,text,"
                 "helpful) VALUES(?,?,?,?,?,?)",
                 ("B0SELFTEST1", "u1", 5, "Great ANC",
                  "Excellent noise cancelling and comfort for the price.", 10))
    conn.commit()
    session = {"session_id": "s1", "user_id": None, "cart_id": None,
               "budget": 2000}
    for name, args in [
        ("search_products", {"query": "noise cancelling headphones under 200"}),
        ("get_reviews", {"product_id": "B0SELFTEST1"}),
        ("compare_products", {"product_ids": ["B0SELFTEST1", "B0SELFTEST2"],
                              "query": "price and battery"}),
        ("add_to_cart", {"product_id": "B0SELFTEST1", "qty": 2}),
        ("view_cart", {}),
        ("checkout_cart", {"ship_to": "self"}),
        ("track_order", {"order_id": ""}),
        ("get_price_history", {"product_id": "B0SELFTEST1"}),
    ]:
        res = run_tool(conn, name, args, session)
        print(f"\n=== {name} ===  ok={res['ok']} error={res['error']}")
        print(json.dumps(res["result"], ensure_ascii=False)[:600])
        print("citations:", len(res["citations"]))
    # track the real order id from checkout
    conn.commit()
    conn.close()
    print("\nselftest OK")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    if "--selftest" in sys.argv:
        _selftest()
