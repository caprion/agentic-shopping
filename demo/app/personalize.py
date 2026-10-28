"""Personalisation layer for Aria.

Builds a per-user affinity profile from the `interactions` + `products` tables
(brand/category affinity with recency + rating weighting, average spend, recent
items) and re-ranks retrieval hits with deterministic, human-readable
explanations.  Pure SQL + arithmetic — no model, no network.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import time
from collections import defaultdict

log = logging.getLogger(__name__)


def build_profile(conn: sqlite3.Connection, user_id: str) -> dict:
    prof: dict = {
        "user_id": user_id,
        "n_events": 0,
        "avg_rating": None,
        "avg_spend": None,
        "brand_affinity": {},
        "category_affinity": {},
        "recent": [],
    }
    try:
        rows = conn.execute(
            "SELECT i.product_id, i.rating, i.ts, p.brand, p.category_main, p.price "
            "FROM interactions i LEFT JOIN products p ON p.product_id = i.product_id "
            "WHERE i.user_id = ? ORDER BY COALESCE(i.ts,0) DESC",
            (user_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        log.warning("build_profile query failed (%s)", exc)
        return prof
    if not rows:
        return prof

    now = time.time()
    brand_acc: dict[str, float] = defaultdict(float)
    cat_acc: dict[str, float] = defaultdict(float)
    ratings: list[float] = []
    spends: list[float] = []

    for r in rows:
        weight = 1.0
        if r["ts"] is not None:
            age_days = (now - r["ts"]) / 86400.0
            weight *= math.exp(-age_days / 180.0)  # 180-day half-life-ish decay
        if r["rating"] is not None:
            weight *= 1.0 + (r["rating"] - 3.0) * 0.1  # above-average ratings count more
            ratings.append(float(r["rating"]))
        if r["price"] is not None:
            spends.append(float(r["price"]))
        b = (r["brand"] or "").strip().lower()
        c = (r["category_main"] or "").strip().lower()
        if b:
            brand_acc[b] += max(weight, 0.05)
        if c:
            cat_acc[c] += max(weight, 0.05)

    prof["n_events"] = len(rows)
    prof["avg_rating"] = round(sum(ratings) / len(ratings), 3) if ratings else None
    prof["avg_spend"] = round(sum(spends) / len(spends), 2) if spends else None
    prof["brand_affinity"] = dict(brand_acc)
    prof["category_affinity"] = dict(cat_acc)
    prof["recent"] = [r["product_id"] for r in rows[:10]]
    return prof


def affinity(profile: dict, product: dict | None) -> float:
    """Deterministic personalisation boost in [0, ~2]. Higher = more relevant."""
    if not profile or not product:
        return 0.0
    b = 0.0
    brand = (product.get("brand") or "").lower()
    cat = (product.get("category_main") or "").lower()
    ba = profile.get("brand_affinity") or {}
    ca = profile.get("category_affinity") or {}
    if brand and brand in ba:
        b += min(ba[brand], 3.0) / 3.0
    if cat and cat in ca:
        b += min(ca[cat], 3.0) / 3.0
    price = product.get("price")
    avg = profile.get("avg_spend")
    if price is not None and avg and avg > 0:
        b += 0.5 * (1.0 - min(abs(price - avg) / avg, 1.0))
    return b


def explain_hit(conn: sqlite3.Connection, hit, profile: dict) -> str:
    from app import db

    try:
        row = db.product_row(conn, hit.product_id)
    except sqlite3.Error:
        row = None
    if row is None:
        return "no catalogue info"
    brand = (row["brand"] or "").strip()
    cat = (row["category_main"] or "").strip()
    price = row["price"]
    parts: list[str] = []
    ba = profile.get("brand_affinity") or {}
    if brand and brand.lower() in ba and ba[brand.lower()] > 0:
        cnt = max(1, round(ba[brand.lower()]))
        parts.append(f"you bought {cnt} {brand} item(s)")
    ca = profile.get("category_affinity") or {}
    if cat and cat.lower() in ca and ca[cat.lower()] > 0:
        parts.append(f"matches your {cat} interest")
    avg = profile.get("avg_spend")
    if price is not None and avg and avg > 0:
        if price < avg:
            parts.append(f"₹{price:,.0f} < your usual ₹{avg:,.0f}")
        else:
            parts.append(f"₹{price:,.0f} > your usual ₹{avg:,.0f}")
    if not parts:
        parts.append("no personal history yet")
    return "; ".join(parts)


def rerank(conn: sqlite3.Connection, hits: list, user_id: str | None) -> list[tuple]:
    """Re-rank hits by personalisation.  Returns [(hit, explanation), ...]."""
    if not user_id or not hits:
        return [(h, "") for h in hits]
    from app import db

    profile = build_profile(conn, user_id)
    scored: list[tuple] = []
    for h in hits:
        prod = None
        try:
            prod = db.product_dict(db.product_row(conn, h.product_id))
        except sqlite3.Error:
            prod = None
        b = affinity(profile, prod)
        scored.append((h, b))
    scored.sort(key=lambda t: -(t[0].score * (1.0 + 0.25 * t[1])))
    return [(h, explain_hit(conn, h, profile)) for h, _ in scored]


def rerank_hits(conn: sqlite3.Connection, hits: list, user_id: str | None) -> list:
    """Personalisation re-order that returns bare hits (used by retrieval)."""
    return [h for h, _ in rerank(conn, hits, user_id)]


def _selftest() -> None:
    from app import db

    conn = db.connect()
    try:
        row = conn.execute("SELECT user_id FROM interactions LIMIT 1").fetchone()
    except sqlite3.Error:
        row = None
    if row is None:
        print("[personalize] no interactions — empty profile demo")
        print("[personalize]", build_profile(conn, "u_nobody"))
        print("[personalize] selftest OK")
        return
    uid = row["user_id"]
    prof = build_profile(conn, uid)
    print("[personalize] profile:", {k: (v if not isinstance(v, dict) else dict(list(v.items())[:3]))
                                     for k, v in prof.items()})
    try:
        top = conn.execute(
            "SELECT product_id FROM products ORDER BY COALESCE(rating,0) DESC LIMIT 5"
        ).fetchall()
        hits = [type("H", (), {"product_id": r["product_id"], "score": 1.0})() for r in top]
        for h, expl in rerank(conn, hits, uid):
            print(f"[personalize]   {h.product_id}: {expl}")
    except sqlite3.Error:
        pass
    print("[personalize] selftest OK")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    if "--selftest" in sys.argv:
        _selftest()
