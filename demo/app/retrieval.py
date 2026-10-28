"""Serving-side retrieval core for Aria.

Deterministic constraint parsing + hybrid (lexical FTS5 + dense ANN + graph)
retrieval fused with Reciprocal Rank Fusion and, when downloadable, a
cross-encoder rerank and optional personalisation re-rank.

Everything degrades gracefully: if embeddings, LanceDB, the FTS index or the
cross-encoder are missing, we fall back to lexical-only search and never raise
on a malformed query.
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.lexicon import normalise  # noqa: F401  (re-exported convenience)
from app import lexicon as _lexicon
from app import embeddings as _emb

log = logging.getLogger(__name__)

RRF_K = 60

# --------------------------------------------------------------------------
# data types
# --------------------------------------------------------------------------
@dataclass
class Hit:
    product_id: str
    score: float
    lexical_rank: int | None = None
    dense_rank: int | None = None
    bm25: float | None = None


# --------------------------------------------------------------------------
# deterministic constraint parsing
# --------------------------------------------------------------------------
_ATTRIBUTES = {
    "noise cancelling", "noise cancellation", "wireless", "bluetooth",
    "4k", "5g", "gaming", "smart", "waterproof", "portable", "refurbished",
    "new", "used", "with mic", "mic", "wifi", "android", "ios", "bass",
    "over ear", "on ear", "in ear", "anc", "fast charging", "5.1", "7.1",
    "dolby", "hdr", "oled", "qled", "led", "full hd", "hd", "1080p",
    "touchscreen", "mechanical", "rgb", "ergonomic",
}

_SORT_PHRASES = {
    "cheapest": "price_asc", "lowest price": "price_asc", "budget": "price_asc",
    "price low to high": "price_asc", "low to high": "price_asc",
    "most expensive": "price_desc", "price high to low": "price_desc",
    "high to low": "price_desc",
    "highest rated": "rating_desc", "best rated": "rating_desc",
    "top rated": "rating_desc", "highest rating": "rating_desc",
    "popular": "popularity", "best selling": "popularity", "trending": "popularity",
}

_STOPWORDS = {
    "the", "a", "an", "for", "with", "and", "or", "of", "in", "on", "at",
    "my", "me", "i", "you", "we", "they", "to", "is", "are", "be", "it",
    "this", "that", "these", "those", "please", "want", "need", "buy",
    "looking", "find", "show", "give", "get", "some", "any", "good",
    "best", "under", "below", "above", "over", "less", "than", "more",
    "atleast", "least", "upto", "within", "max", "min", "price", "cost",
    "rate", "rating", "star", "stars", "rupees", "rs", "gift",
    "lakh", "lac", "thousand", "between", "and", "to", "up",
    "maximum", "minimum", "aur",
    # relations / family
    "brother", "sister", "mother", "father", "mom", "dad", "mommy",
    "papa", "bhai", "bhaiya", "bhaai", "behen", "behan", "didi", "maa",
    "mummy", "son", "daughter", "wife", "husband", "friend", "kids",
    "kid", "baby", "nana", "nani", "dada", "dadi",
    # Hinglish filler
    "mere", "mera", "meri", "ke", "ka", "ki", "liye", "wala", "wali",
    "se", "kam", "zyada", "chahiye", "chahie", "lena", "kharid", "batao",
    "dikhao", "dikha", "dekh", "dhoondho", "batana", "accha", "achha",
    "badhiya", "sasta", "saste", "sasti", "mehenga", "sasta", "daam",
    "do", "kitna", "kya", "hai", "hain",
}


def _num(token: str) -> float | None:
    t = (token or "").replace(",", "").replace(" ", "").lower()
    if not t:
        return None
    mult = 1.0
    for suffix, m in (("lakh", 100000.0), ("lac", 100000.0), ("thousand", 1000.0),
                      ("k", 1000.0), ("l", 100000.0)):
        if t.endswith(suffix):
            mult = m
            t = t[: -len(suffix)]
            break
    try:
        return float(t) * mult
    except ValueError:
        return None


_NUM_BODY = r"[\d][\d,\.]*"
_CUR = r"[₹$]?\s*"
_UNIT = r"(?:k|lakh|lac|l|thousand)?"
_AMT = r"([\d][\d,\.]*\s*(?:k|lakh|lac|l|thousand)?)"


def _parse_constraints(text: str, lex: dict[str, dict[str, str]]) -> dict:
    out: dict[str, Any] = {
        "free_text": "",
        "brand": [],
        "price_min": None,
        "price_max": None,
        "rating_min": None,
        "category": None,
        "attributes": {},
        "sort": None,
    }
    if not text or not text.strip():
        return out
    low = text.strip().lower()
    low = re.sub(r"\s+", " ", low)

    # ---- rating (before price so "above 4.5 stars" is not read as money) ----
    m = re.search(
        r"(?:at\s+least|above|over|more\s+than|minimum|min|rating\s*(?:>=|≥))\s*"
        r"([\d.]+)\s*\+?\s*(?:stars?|ratings?)", low)
    if m:
        out["rating_min"] = float(m.group(1))
    else:
        m = re.search(r"([\d.]+)\s*\+?\s*(?:stars?|ratings?)", low)
        if m:
            out["rating_min"] = float(m.group(1))
    # strip star/rating mentions so price regexes don't mistake them for money
    low = re.sub(r"[\d.]+\s*\+?\s*(?:stars?|ratings?)\b", " ", low)

    # ---- price ranges ----
    m = re.search(
        r"between\s+" + _CUR + _AMT + r"\s*(?:and|to|aur|-)\s*"
        + _CUR + _AMT, low)
    if m:
        out["price_min"] = _num(m.group(1))
        out["price_max"] = _num(m.group(2))
    else:
        m = re.search(
            _CUR + _AMT + r"\s*(?:to|-)\s*"
            + _CUR + _AMT, low)
        if m:
            out["price_min"] = _num(m.group(1))
            out["price_max"] = _num(m.group(2))

    # ---- "under / less than / max" -> price_max ----
    if out["price_max"] is None:
        m = re.search(
            r"(?:under|below|less\s+than|upto|up\s+to|within|max(?:imum)?|"
            r"kam\s+se|se\s+kam)\s+" + _CUR + _AMT, low)
        if m:
            out["price_max"] = _num(m.group(1))
        else:
            m = re.search(
                _AMT + r"\s*(?:se\s+kam|kam)", low)
            if m:
                out["price_max"] = _num(m.group(1))

    # ---- "above / more than / at least" -> price_min ----
    if out["price_min"] is None:
        m = re.search(
            r"(?:above|over|more\s+than|at\s+least|min(?:imum)?|"
            r"zyada|se\s+zyada)\s+" + _CUR + _AMT, low)
        if m:
            out["price_min"] = _num(m.group(1))
        else:
            m = re.search(
                _AMT + r"\s*(?:se\s+zyada|zyada)", low)
            if m:
                out["price_min"] = _num(m.group(1))

    # ---- bare budget: "₹3000", "1.5 lakh" with no direction keyword ----
    if out["price_max"] is None and out["price_min"] is None:
        m = re.search(r"(?:[₹$]\s*|(?<=\s)rs\s+)" + _AMT, low)
        if m:
            out["price_max"] = _num(m.group(1))
        else:
            m = re.search(r"([\d][\d,\.]*\s*(?:lakh|lac))\b", low)
            if m:
                out["price_max"] = _num(m.group(1))

    # ---- negations ("not Samsung", "no plastic"); "noise cancelling" is not one ----
    brands = lex.get("brand", {})
    syn_cat: dict[str, str] = {}
    syn_cat.update(lex.get("synonym", {}))
    syn_cat.update(lex.get("category", {}))
    neg_brands: list[str] = []
    neg_terms: list[str] = []
    neg_re = re.compile(
        r"\b(not|no|without|avoid|don'?t|nahi|nahin)\b"
        r"\s+([a-z0-9]+(?:\s+(?!(?:not|no|without|avoid|don'?t|nahi|nahin)\b)"
        r"[a-z0-9]+)?)")
    spans: list[tuple[int, int]] = []
    for m in neg_re.finditer(low):
        words = m.group(2).split()
        one = words[0]
        two = " ".join(words[:2])
        keep = 1
        if two in brands:
            neg_brands.append(brands[two])
            keep = 2
        elif two in _ATTRIBUTES or two in syn_cat:
            neg_terms.append(two)
            keep = 2
        elif one in brands:
            neg_brands.append(brands[one])
        else:
            neg_terms.append(one)
        spans.append((m.start(), m.start(2) + len(" ".join(words[:keep]))))
    if spans:
        chars = list(low)
        for s, e in spans:
            for i in range(s, min(e, len(chars))):
                chars[i] = " "
        low = "".join(chars)
    if neg_brands:
        out["attributes"]["not_brand"] = list(dict.fromkeys(neg_brands))
    if neg_terms:
        out["attributes"]["not_attribute"] = list(dict.fromkeys(neg_terms))
        out["attributes"]["negations"] = list(dict.fromkeys(neg_terms))

    # ---- sort ----
    for phrase, sort_val in _SORT_PHRASES.items():
        if re.search(r"\b" + re.escape(phrase) + r"\b", low):
            out["sort"] = sort_val
            break

    # ---- token stream for entity / attribute / free-text extraction -------
    toks = [t for t in re.split(r"[\s\-]+", low.strip()) if t]
    n = len(toks)
    removed = [False] * n

    def consume(phrase: str) -> bool:
        """If `phrase` (space-joined) appears as a contiguous token span, mark it."""
        parts = phrase.split()
        for i in range(n - len(parts) + 1):
            if any(removed[i:i + len(parts)]):
                continue
            if toks[i:i + len(parts)] == parts:
                for j in range(i, i + len(parts)):
                    removed[j] = True
                return True
        return False

    # ---- attributes FIRST (so "noise cancelling" beats the "noise" brand) ----
    for attr in sorted(_ATTRIBUTES, key=len, reverse=True):
        if consume(attr):
            out["attributes"][normalise(attr)] = True

    # ---- brands (longest match first) ----
    for size in (3, 2, 1):
        for i in range(n - size + 1):
            if any(removed[i:i + size]):
                continue
            phrase = " ".join(toks[i:i + size])
            if phrase in brands:
                b = brands[phrase]
                if b not in out["brand"]:
                    out["brand"].append(b)
                for j in range(i, i + size):
                    removed[j] = True

    # ---- category (synonym -> canonical category) ----
    for size in (3, 2, 1):
        if out["category"] is not None:
            break
        for i in range(n - size + 1):
            if any(removed[i:i + size]):
                continue
            phrase = " ".join(toks[i:i + size])
            canon = syn_cat.get(phrase)
            if canon:
                out["category"] = canon
                for j in range(i, i + size):
                    removed[j] = True
                break

    # ---- Hinglish intent (gift, cheap, expensive) ----
    hing = lex.get("hinglish", {})
    for size in (2, 1):
        for i in range(n - size + 1):
            if any(removed[i:i + size]):
                continue
            phrase = " ".join(toks[i:i + size])
            val = hing.get(phrase)
            if val in ("gift", "cheap", "expensive"):
                if val == "cheap" and out["sort"] is None:
                    out["sort"] = "price_asc"
                elif val == "expensive" and out["sort"] is None:
                    out["sort"] = "price_desc"
                elif val == "gift":
                    out["attributes"]["gift"] = True
                for j in range(i, i + size):
                    removed[j] = True

    # ---- free text = remaining meaningful tokens ----
    keep = []
    for i, t in enumerate(toks):
        if removed[i]:
            continue
        if t in _STOPWORDS or len(t) < 2:
            continue
        if re.match(r"^[₹$]?[\d]", t):  # leftover numbers/currency
            continue
        keep.append(t)
    out["free_text"] = " ".join(keep).strip()
    return out


def parse_constraints(text: str, lex: dict[str, dict[str, str]] | None = None) -> dict:
    """Parse a natural-language query into structured constraints (deterministic).

    Optional `lex` lets callers inject a DB-derived lexicon; default is the
    in-memory seed (offline-safe, brand list limited to common brands).
    """
    if lex is None:
        lex = _lexicon._seed_lexicon()
    return _parse_constraints(text, lex)


# --------------------------------------------------------------------------
# low-level search primitives
# --------------------------------------------------------------------------
_FTS_STOP = {"and", "or", "not", "near", "the", "a", "an", "of", "in", "on",
             "for", "to", "with", "at", "by", "is", "are", "be", "it"}


def _sanitise_fts(text: str) -> str | None:
    """Turn free text into a safe FTS5 MATCH expression (quoted tokens only)."""
    if not text:
        return None
    toks = []
    for t in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if t in _FTS_STOP or len(t) < 2:
            continue
        if t.isdigit():
            continue
        toks.append('"' + t + '"')
    return " ".join(toks) or None


def _fts_table_exists(conn: sqlite3.Connection) -> bool:
    try:
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products_fts'"
        ).fetchone()
        return r is not None
    except sqlite3.Error:
        return False


def _fts_top(conn: sqlite3.Connection, text: str, limit: int = 200,
             candidate: set[str] | None = None) -> list[tuple[str, float]]:
    match = _sanitise_fts(text)
    if not match or not _fts_table_exists(conn):
        return []
    try:
        rows = conn.execute(
            "SELECT product_id, bm25(products_fts) AS bm25 "
            "FROM products_fts WHERE products_fts MATCH ? ORDER BY bm25 LIMIT ?",
            (match, limit * 3),
        ).fetchall()
        out = []
        for r in rows:
            if candidate is not None and r["product_id"] not in candidate:
                continue
            out.append((r["product_id"], float(r["bm25"])))
            if len(out) >= limit:
                break
        return out
    except sqlite3.Error as exc:
        log.warning("FTS query failed (%s)", exc)
        return []


def _dense_top(query: str, k: int = 200,
               candidate: set[str] | None = None) -> list[tuple[str, float]]:
    vec = _emb.encode_query(query)
    if vec is None:
        return []
    try:
        res = _emb.search_vectors(vec, k=max(k * 3, 50))
    except Exception as exc:  # noqa: BLE001
        log.warning("dense search failed (%s)", exc)
        return []
    out = []
    for pid, score in res:
        if candidate is not None and pid not in candidate:
            continue
        out.append((pid, float(score)))
        if len(out) >= k:
            break
    return out


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where_clause(constraints: dict) -> tuple[str, list[Any]]:
    """Cheap, index-friendly WHERE clause (brand/price/rating/stock).

    Category is intentionally excluded — it is applied as a refinement in
    `_sql_prefilter` so a `title LIKE '%cat%'` full scan never runs over the
    whole catalogue.
    """
    conds: list[str] = []
    params: list[Any] = []
    brands = constraints.get("brand") or []
    if brands:
        ors = []
        for b in brands:
            ors.append("brand LIKE ? ESCAPE '\\'")
            params.append(_like_escape(str(b).lower()))
        conds.append("(" + " OR ".join(ors) + ")")
    if constraints.get("price_min") is not None:
        conds.append("price >= ?")
        params.append(float(constraints["price_min"]))
    if constraints.get("price_max") is not None:
        conds.append("price <= ?")
        params.append(float(constraints["price_max"]))
    if constraints.get("rating_min") is not None:
        conds.append("rating >= ?")
        params.append(float(constraints["rating_min"]))
    conds.append("in_stock = 1")
    return " AND ".join(conds), params


def _filter_category(conn: sqlite3.Connection, ids: set[str], cat: str) -> set[str]:
    """Refine a candidate set by category using cheap in-Python substring checks."""
    if not ids or not cat:
        return ids
    c = str(cat).lower()
    q = ",".join("?" * len(ids))
    try:
        rows = conn.execute(
            f"SELECT product_id, title, category_main, categories FROM products "
            f"WHERE product_id IN ({q})", list(ids)).fetchall()
    except sqlite3.Error as exc:
        log.warning("category refinement failed (%s)", exc)
        return ids
    keep = set()
    for r in rows:
        hay = (r["title"] or "") + " " + (r["category_main"] or "") + " " + (r["categories"] or "")
        if c in hay.lower():
            keep.add(r["product_id"])
    return keep


def _sql_prefilter(conn: sqlite3.Connection, constraints: dict) -> set[str] | None:
    try:
        where, params = _where_clause(constraints)
        if where.strip() == "in_stock = 1":
            ids = None  # no cheap filter -> rely on FTS/dense
        else:
            rows = conn.execute(f"SELECT product_id FROM products WHERE {where}", params).fetchall()
            ids = {r["product_id"] for r in rows}
            if not ids:
                log.info("SQL pre-filter matched 0 rows; relaxing filter")
                return None
            cat = constraints.get("category")
            if cat and len(ids) < 50000:
                ids = _filter_category(conn, ids, cat)
                if not ids:
                    log.info("category refinement matched 0; relaxing")
                    return None
        return ids
    except sqlite3.Error as exc:
        log.warning("SQL pre-filter failed (%s)", exc)
        return None


def _sql_fallback(conn: sqlite3.Connection, constraints: dict, k: int) -> list[Hit]:
    """Last-resort result when no lexical/dense/graph signal exists."""
    try:
        where, params = _where_clause(constraints)
        rows = conn.execute(
            f"SELECT product_id FROM products WHERE {where} "
            f"ORDER BY COALESCE(rating,0) DESC, COALESCE(popularity,0) DESC, "
            f"COALESCE(price,1e18) ASC LIMIT ?", params + [k]).fetchall()
        ids = {r["product_id"] for r in rows}
        cat = constraints.get("category")
        if cat and ids:
            ids = _filter_category(conn, ids, cat)
        ordered = [pid for pid in (r["product_id"] for r in rows) if pid in ids]
        return [Hit(product_id=pid, score=0.0, lexical_rank=None,
                    dense_rank=None, bm25=None) for pid in ordered]
    except sqlite3.Error as exc:
        log.warning("SQL fallback failed (%s)", exc)
        return []


def _product_titles(conn: sqlite3.Connection, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    q = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(f"SELECT product_id, title FROM products WHERE product_id IN ({q})", ids).fetchall()
        return {r["product_id"]: (r["title"] or "") for r in rows}
    except sqlite3.Error:
        return {}


# cross-encoder singleton
_CE: Any = None


def _get_cross_encoder():
    global _CE
    if _CE is not None:
        return _CE
    try:
        from sentence_transformers import CrossEncoder

        _CE = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        log.info("cross-encoder loaded")
    except Exception as exc:  # noqa: BLE001
        log.warning("cross-encoder unavailable (%s); skipping rerank", exc)
        _CE = None
    return _CE


def graph_expand(conn: sqlite3.Connection, product_ids: list[str],
                 k: int = 30) -> list[Hit]:
    """Expand a set of seed products through `product_edges` (co_purchase etc.)."""
    if not product_ids:
        return []
    try:
        seeds = set(product_ids)
        q = ",".join("?" for _ in product_ids)
        rows = conn.execute(
            f"SELECT a, b, kind, weight FROM product_edges "
            f"WHERE a IN ({q}) OR b IN ({q})",
            list(product_ids) + list(product_ids),
        ).fetchall()
        kind_w = {"co_purchase": 2.0, "also_bought": 1.5, "co_view": 1.0}
        scores: dict[str, float] = {}
        for r in rows:
            nb = r["b"] if r["a"] in seeds else r["a"]
            if nb in seeds:
                continue
            w = float(r["weight"] or 1.0) * kind_w.get(r["kind"], 1.0)
            scores[nb] = scores.get(nb, 0.0) + w
        ordered = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
        return [Hit(product_id=pid, score=w, lexical_rank=None, dense_rank=None, bm25=None)
                for pid, w in ordered]
    except sqlite3.Error as exc:
        log.warning("graph_expand failed (%s)", exc)
        return []


def _rrf(lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for lst in lists:
        for rank, pid in enumerate(lst):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _cross_encode_rerank(conn: sqlite3.Connection, query: str,
                         ranked: list[tuple[str, float]]) -> list[tuple[str, float]]:
    ce = _get_cross_encoder()
    if ce is None or not ranked:
        return ranked
    topn = 20
    ids = [pid for pid, _ in ranked[:topn]]
    titles = _product_titles(conn, ids)
    pairs = [(query, titles.get(pid, "")) for pid in ids]
    try:
        scores = ce.predict(pairs)
        logits = np.asarray(scores, dtype=np.float32)
        probs = 1.0 / (1.0 + np.exp(-logits))
        merged = []
        for (pid, rrf), p in zip(ranked[:topn], probs):
            merged.append((pid, float(rrf * 0.5 + float(p) * 0.5)))
        merged.sort(key=lambda kv: -kv[1])
        # re-append any beyond topn unchanged
        merged += ranked[topn:]
        return merged
    except Exception as exc:  # noqa: BLE001
        log.warning("cross-encoder predict failed (%s)", exc)
        return ranked


def _personalise(conn: sqlite3.Connection, hits: list[Hit],
                 user_id: str | None) -> list[Hit]:
    if not user_id or not hits:
        return hits
    try:
        import personalize

        return personalize.rerank_hits(conn, hits, user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("personalisation failed (%s)", exc)
        return hits


# --------------------------------------------------------------------------
# the main entry point
# --------------------------------------------------------------------------
def hybrid_search(conn: sqlite3.Connection, query: str,
                  constraints: dict | None = None, k: int = 20,
                  user_id: str | None = None) -> list[Hit]:
    """Hybrid retrieval: SQL pre-filter -> FTS5 + dense ANN + graph -> RRF ->
    cross-encoder rerank -> personalisation.  Never raises; degrades to lexical."""
    try:
        lex = _lexicon.load_lexicon(conn)
    except Exception:  # noqa: BLE001
        lex = _lexicon._seed_lexicon()
    if constraints is None:
        constraints = parse_constraints(query, lex=lex)
    try:
        free_text = constraints.get("free_text") or ""
        search_text = free_text.strip() or query.strip()
        candidate = _sql_prefilter(conn, constraints)

        lexical = _fts_top(conn, search_text, limit=200, candidate=candidate)
        dense = _dense_top(search_text, k=200, candidate=candidate)
        seed_ids = [pid for pid, _ in (lexical + dense)[:50]]
        graph = graph_expand(conn, list(dict.fromkeys(seed_ids)), k=50)
        if candidate is not None:
            graph = [h for h in graph if h.product_id in candidate]

        lexical_rank = {pid: i for i, (pid, _) in enumerate(lexical)}
        dense_rank = {pid: i for i, (pid, _) in enumerate(dense)}
        bm25 = {pid: v for pid, v in lexical}

        fused = _rrf(
            [[pid for pid, _ in lexical],
             [pid for pid, _ in dense],
             [h.product_id for h in graph]], k=RRF_K)

        ranked = sorted(fused.items(), key=lambda kv: -kv[1])
        if not ranked:
            return _sql_fallback(conn, constraints, k)
        ranked = _cross_encode_rerank(conn, search_text, ranked)

        hits = []
        for pid, score in ranked[: max(k, 25)]:
            hits.append(Hit(
                product_id=pid,
                score=score,
                lexical_rank=lexical_rank.get(pid),
                dense_rank=dense_rank.get(pid),
                bm25=bm25.get(pid),
            ))
        hits = _personalise(conn, hits, user_id)
        return hits[:k]
    except Exception as exc:  # noqa: BLE001 — never raise on bad input
        log.exception("hybrid_search failed (%s); falling back to lexical", exc)
        return _lexical_only(conn, query, k)


def _lexical_only(conn: sqlite3.Connection, query: str, k: int) -> list[Hit]:
    """Absolute fallback: FTS only, then top-k by price/popularity if no FTS."""
    lexical = _fts_top(conn, query, limit=k)
    if lexical:
        return [Hit(product_id=pid, score=-1.0 / (i + 1),
                    lexical_rank=i, dense_rank=None, bm25=v)
                for i, (pid, v) in enumerate(lexical)]
    try:
        rows = conn.execute(
            "SELECT product_id FROM products ORDER BY COALESCE(popularity,0) DESC, "
            "COALESCE(rating,0) DESC LIMIT ?", (k,)).fetchall()
        return [Hit(product_id=r["product_id"], score=0.0, lexical_rank=None,
                    dense_rank=None, bm25=None) for r in rows]
    except sqlite3.Error:
        return []


# --------------------------------------------------------------------------
# catalogue helpers
# --------------------------------------------------------------------------
def get_product(conn: sqlite3.Connection, product_id: str) -> dict | None:
    from app import db

    try:
        return db.product_dict(db.product_row(conn, product_id))
    except sqlite3.Error as exc:
        log.warning("get_product failed (%s)", exc)
        return None


def get_reviews(conn: sqlite3.Connection, product_id: str, limit: int = 5,
                min_rating: float | None = None) -> list[dict]:
    try:
        q = "SELECT review_id, rating, title, text, helpful, ts FROM reviews WHERE product_id=?"
        params: list[Any] = [product_id]
        if min_rating is not None:
            q += " AND rating >= ?"
            params.append(min_rating)
        q += " ORDER BY COALESCE(helpful,0) DESC, COALESCE(ts,0) DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            text = (r["text"] or "").strip()
            excerpt = text[:220] + ("…" if len(text) > 220 else "")
            out.append({
                "review_id": r["review_id"],
                "rating": r["rating"],
                "title": r["title"],
                "text": excerpt,
            })
        return out
    except sqlite3.Error as exc:
        log.warning("get_reviews failed (%s)", exc)
        return []


def similar_products(conn: sqlite3.Connection, product_id: str,
                     k: int = 8) -> list[dict]:
    from app import db

    try:
        scores: dict[str, float] = {}
        for kind, w in (("co_purchase", 2.0), ("also_bought", 1.5), ("co_view", 1.0)):
            rows = conn.execute(
                "SELECT a, b, weight FROM product_edges WHERE (a=? OR b=?) AND kind=?",
                (product_id, product_id, kind)).fetchall()
            for r in rows:
                nb = r["b"] if r["a"] == product_id else r["a"]
                scores[nb] = scores.get(nb, 0.0) + float(r["weight"] or 0.0) * w
        prod = db.product_row(conn, product_id)
        if prod:
            match = _sanitise_fts(prod["title"] or "")
            if match and _fts_table_exists(conn):
                rows = conn.execute(
                    "SELECT product_id, bm25(products_fts) AS bm25 FROM products_fts "
                    "WHERE products_fts MATCH ? AND product_id != ? "
                    "ORDER BY bm25 LIMIT ?", (match, product_id, k * 3)).fetchall()
                for i, r in enumerate(rows):
                    scores[r["product_id"]] = scores.get(r["product_id"], 0.0) + 1.0 / (i + 1)
        out = []
        for pid, _ in sorted(scores.items(), key=lambda kv: -kv[1]):
            if pid == product_id:
                continue
            d = db.product_dict(db.product_row(conn, pid))
            if d:
                out.append(d)
            if len(out) >= k:
                break
        return out
    except sqlite3.Error as exc:
        log.warning("similar_products failed (%s)", exc)
        return []


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
_CONN: sqlite3.Connection | None = None


def get_connection(path: str | None = None) -> sqlite3.Connection:
    """Single cached SQLite connection per process (check_same_thread=False)."""
    global _CONN
    if _CONN is None:
        from app import db

        _CONN = db.connect(path)
        try:
            db.init_schema(_CONN)
            db.fts_schema(_CONN)
        except sqlite3.Error:
            pass
    return _CONN


def _selftest() -> None:
    from app import db

    conn = get_connection()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
    except sqlite3.Error:
        n = 0
    if n == 0:
        print("DB empty — skipping live check")
        return

    _lexicon.build_lexicon(conn)
    queries = [
        "noise cancelling headphones under 5000 with 4.5 stars",
        "sony tv",
        "gaming laptop under 80000",
        "gift for brother under 3000",
        "saste earphones for mom",
    ]
    for q in queries:
        cons = parse_constraints(q)
        hits = hybrid_search(conn, q, k=5)
        print(f"\nQUERY: {q!r}")
        print(f"  constraints: {cons}")
        if not hits:
            print("  (no hits)")
            continue
        for h in hits:
            p = get_product(conn, h.product_id)
            if p:
                print(f"  {h.product_id}  ₹{p.get('price')}  ★{p.get('rating')}  "
                      f"{str(p.get('title'))[:60]!r}")
            else:
                print(f"  {h.product_id}")
    print("\n[retrieval] selftest OK")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    if "--selftest" in sys.argv:
        _selftest()
