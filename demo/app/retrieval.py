"""Serving-side retrieval core for Aria.

Deterministic constraint parsing + hybrid (lexical FTS5 + dense ANN + graph)
retrieval fused with Reciprocal Rank Fusion and, when downloadable, a
cross-encoder rerank and optional personalisation re-rank.

Pipeline (see CONTRACT.md)::

    SQL pre-filter ─┐
    FTS5 bm25 top-200 ─┼─► RRF (k=60) ─► cross-encoder rerank ─► personalise
    dense ANN top-200 ┤                                        └─► explicit sort
    graph expand top-50 ─┘

Everything degrades gracefully: if embeddings, LanceDB, the FTS index or the
cross-encoder are missing, we fall back to lexical-only search and never raise
on a malformed query.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from app import embeddings as _emb
from app import lexicon as _lexicon
from app.lexicon import normalise  # noqa: F401  (re-exported convenience)

log = logging.getLogger(__name__)

RRF_K = 60
FTS_TOP = 200
DENSE_TOP = 200
GRAPH_TOP = 50
CE_TOP = 15

# Stage timings of the last hybrid_search call (ms) — handy for the API.
LAST_TIMINGS: dict[str, float] = {}


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
    "noise cancelling", "noise cancellation", "active noise cancelling", "anc",
    "wireless", "bluetooth", "4k", "5g", "4g", "gaming", "smart", "waterproof",
    "portable", "refurbished", "new", "used", "with mic", "mic", "wifi",
    "android", "ios", "bass", "over ear", "on ear", "in ear", "fast charging",
    "5.1", "7.1", "dolby", "hdr", "oled", "qled", "led", "full hd", "hd",
    "1080p", "touchscreen", "mechanical", "rgb", "ergonomic", "foldable",
    "stereo", "surround sound", "voice control", "alexa", "google assistant",
}

_SORT_PHRASES = {
    "cheapest": "price_asc", "lowest price": "price_asc", "budget": "price_asc",
    "price low to high": "price_asc", "low to high": "price_asc",
    "saste": "price_asc", "sasta": "price_asc", "sasti": "price_asc",
    "most expensive": "price_desc", "price high to low": "price_desc",
    "high to low": "price_desc",
    "highest rated": "rating_desc", "best rated": "rating_desc",
    "top rated": "rating_desc", "highest rating": "rating_desc",
    "popular": "popularity", "best selling": "popularity", "trending": "popularity",
    "top selling": "popularity", "best": "rating_desc", "top": "popularity",
}

_STOPWORDS = {
    "the", "a", "an", "for", "with", "and", "or", "of", "in", "on", "at",
    "inch", "inches", "size", "cm", "mm", "gb", "tb", "ram", "rom", "volts",
    "watt", "mah", "inch.",
    "my", "me", "i", "you", "we", "they", "to", "is", "are", "be", "it",
    "this", "that", "these", "those", "please", "want", "need", "buy",
    "looking", "find", "show", "give", "get", "some", "any", "good",
    "best", "under", "below", "above", "over", "less", "than", "more",
    "atleast", "least", "upto", "within", "max", "min", "price", "cost",
    "rate", "rating", "star", "stars", "rupees", "rs", "gift",
    "lakh", "lac", "thousand", "between", "to", "up",
    "maximum", "minimum", "aur", "but", "also", "plus",
    # relations / family
    "brother", "sister", "mother", "father", "mom", "dad", "mummy",
    "papa", "bhai", "bhaiya", "bhaai", "behen", "behan", "didi", "maa",
    "son", "daughter", "wife", "husband", "friend", "friends", "kids",
    "kid", "baby", "nana", "nani", "dada", "dadi", "wife",
    # relatives / intent verbs
    "who", "whom", "whose", "love", "loves", "loved", "like", "likes",
    "liked", "prefer", "prefers", "use", "used", "uses", "using",
    "wear", "wears", "wearing", "search", "looking for", "for my",
    # Hinglish filler
    "mere", "mera", "meri", "ke", "ka", "ki", "liye", "wala", "wali",
    "se", "kam", "zyada", "chahiye", "chahie", "lena", "kharid", "batao",
    "dikhao", "dikha", "dekh", "dhoondho", "batana", "accha", "achha",
    "badhiya", "sasta", "saste", "sasti", "mehenga", "daam",
    "do", "kitna", "kya", "hai", "hain", "wali", "waala",
}

_NEG_MARKERS = (r"not|no|without|avoid|except|other than|besides|excluding|"
                r"don'?t|dont|nahi|nahin")

# Hinglish targets that are relation / filler words: consumed but never turned
# into a facet ("mere bhai ke liye" -> my / brother / for).
_HINGLISH_FILLER = {
    "my", "for", "of", "brother", "sister", "mother", "father", "son",
    "daughter", "wife", "husband", "friend", "good", "best", "show", "see",
    "tell", "how much", "price", "want", "buy", "give", "find", "under",
    "above", "expensive",
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


_CUR = r"[₹$]?\s*"
_AMT = r"([\d][\d,\.]*\s*(?:k|lakh|lac|l|thousand)?)"


def _norm_query(text: str) -> str:
    """Lowercase + strip currency, but KEEP dots/commas (4.5 must stay 4.5)."""
    s = str(text or "").lower()
    s = s.replace("₹", " ").replace("$", " ").replace("rs.", " ")
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def _default_constraints() -> dict:
    return {
        "free_text": "",
        "brand": [],
        "price_min": None,
        "price_max": None,
        "rating_min": None,
        "category": None,
        "attributes": {},
        "sort": None,
    }


def _parse_constraints(text: str, lex: dict[str, dict[str, str]]) -> dict:
    out = _default_constraints()
    if not text or not text.strip():
        return out
    low = _norm_query(text)

    # ---- rating (before price so "above 4.5 stars" is not read as money) ----
    m = re.search(
        r"(?:at\s+least|above|over|more\s+than|minimum|min|rating\s*(?:>=|≥))\s*"
        r"([\d.]+)\s*\+?\s*(?:stars?|ratings?|★)", low)
    if m:
        out["rating_min"] = float(m.group(1))
    else:
        m = re.search(r"([\d.]+)\s*\+?\s*(?:stars?|ratings?|★)", low)
        if m:
            out["rating_min"] = float(m.group(1))
    # strip star/rating mentions so price regexes don't mistake them for money
    low = re.sub(r"[\d.]+\s*\+?\s*(?:stars?|ratings?|★)\b", " ", low)

    # ---- price ranges -----------------------------------------------------
    m = re.search(r"between\s+" + _CUR + _AMT + r"\s*(?:and|to|aur|-|—)\s*"
                  + _CUR + _AMT, low)
    if m:
        out["price_min"] = _num(m.group(1))
        out["price_max"] = _num(m.group(2))
    else:
        m = re.search(_CUR + _AMT + r"\s*(?:to|-|—)\s*" + _CUR + _AMT, low)
        if m:
            out["price_min"] = _num(m.group(1))
            out["price_max"] = _num(m.group(2))

    # ---- "under / less than / max" -> price_max ----------------------------
    if out["price_max"] is None:
        m = re.search(
            r"(?:under|below|less\s+than|upto|up\s+to|within|max(?:imum)?|"
            r"kam\s+se|se\s+kam)\s+" + _CUR + _AMT, low)
        if m:
            out["price_max"] = _num(m.group(1))
        else:
            m = re.search(_AMT + r"\s*(?:se\s+kam|kam)\b", low)
            if m:
                out["price_max"] = _num(m.group(1))

    # ---- "above / more than / at least" -> price_min -----------------------
    if out["price_min"] is None:
        m = re.search(
            r"(?:above|over|more\s+than|at\s+least|min(?:imum)?|"
            r"zyada|se\s+zyada)\s+" + _CUR + _AMT, low)
        if m:
            out["price_min"] = _num(m.group(1))
        else:
            m = re.search(_AMT + r"\s*(?:se\s+zyada|zyada)\b", low)
            if m:
                out["price_min"] = _num(m.group(1))

    # ---- bare budget: "₹3000", "1.5 lakh" with no direction keyword ------
    if out["price_max"] is None and out["price_min"] is None:
        m = re.search(r"(?:[₹$]\s*|(?<=\s)rs\s+)" + _AMT, low)
        if m:
            out["price_max"] = _num(m.group(1))
        else:
            m = re.search(r"([\d][\d,\.]*\s*(?:lakh|lac))\b", low)
            if m:
                out["price_max"] = _num(m.group(1))

    # ---- negations ("not Samsung", "no plastic") ----------------------------
    brands = lex.get("brand", {}) or {}
    syn_cat: dict[str, str] = {}
    syn_cat.update(lex.get("synonym", {}) or {})
    syn_cat.update(lex.get("category", {}) or {})
    attrs = lex.get("attribute", {}) or {}
    neg_re = re.compile(
        r"\b(?:" + _NEG_MARKERS + r")\b\s+"
        r"([a-z0-9]+(?:\s+(?!(?:" + _NEG_MARKERS + r")\b)[a-z0-9]+)?)")
    neg_brands: list[str] = []
    neg_terms: list[str] = []
    neg_cats: list[str] = []
    chars = list(low)
    for m in neg_re.finditer(low):
        words = m.group(1).split()
        if not words:
            continue
        one = words[0]
        two = " ".join(words[:2])
        keep = 1
        if two in brands:
            neg_brands.append(brands[two])
            keep = 2
        elif two in _ATTRIBUTES or two in syn_cat or two in attrs:
            neg_terms.append(two)
            keep = 2
        elif one in brands:
            neg_brands.append(brands[one])
        else:
            neg_terms.append(one)
            if one in syn_cat:
                neg_cats.append(syn_cat[one])
        for i in range(m.start(), min(m.start(1) + len(" ".join(words[:keep])), len(chars))):
            chars[i] = " "
    low = "".join(chars)
    if neg_brands:
        out["attributes"]["not_brand"] = list(dict.fromkeys(neg_brands))
    if neg_terms:
        out["attributes"]["not_attribute"] = list(dict.fromkeys(neg_terms))
        out["attributes"]["negations"] = list(dict.fromkeys(neg_terms))
    if neg_cats:
        out["attributes"]["not_category"] = list(dict.fromkeys(neg_cats))

    # ---- token stream for entity / attribute / free-text extraction -------
    toks = [t for t in re.split(r"[\s\-]+", low.strip()) if t]
    n = len(toks)
    removed = [False] * n

    def consume(phrase: str) -> bool:
        """If `phrase` appears as a contiguous token span, mark it consumed."""
        parts = phrase.split()
        for i in range(n - len(parts) + 1):
            if any(removed[i:i + len(parts)]):
                continue
            if toks[i:i + len(parts)] == parts:
                for j in range(i, i + len(parts)):
                    removed[j] = True
                return True
        return False

    # ---- sort (consume the phrase so it never pollutes the free text) ------
    for phrase in sorted(_SORT_PHRASES, key=len, reverse=True):
        if consume(phrase):
            out["sort"] = _SORT_PHRASES[phrase]
            break

    # ---- attributes FIRST (so "noise cancelling" beats a "noise" brand) ----
    for attr in sorted(_ATTRIBUTES, key=len, reverse=True):
        if consume(attr):
            out["attributes"][normalise(attr)] = True

    # ---- brands (longest match first; never a relation/stop word) ----------
    seed_brands = {normalise(b) for b in _lexicon.SEED_BRANDS}
    for size in (3, 2, 1):
        for i in range(n - size + 1):
            if any(removed[i:i + size]):
                continue
            phrase = " ".join(toks[i:i + size])
            if phrase in _STOPWORDS:
                continue
            # a generic category word ("laptop") that some seller uses as a
            # brand must stay a category, not become a brand filter
            if phrase in syn_cat and phrase not in seed_brands:
                continue
            if phrase in brands:
                b = brands[phrase]
                if b not in out["brand"]:
                    out["brand"].append(b)
                for j in range(i, i + size):
                    removed[j] = True

    # ---- category (synonym / catalogue label -> canonical category) --------
    category_phrase = ""
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
                category_phrase = phrase
                for j in range(i, i + size):
                    removed[j] = True
                break

    # ---- Hinglish intent (gift, cheap, expensive) + Hinglish categories ----
    hing = lex.get("hinglish", {}) or {}
    for size in (3, 2, 1):
        for i in range(n - size + 1):
            if any(removed[i:i + size]):
                continue
            phrase = " ".join(toks[i:i + size])
            val = hing.get(phrase)
            if val is None:
                continue
            if val == "gift":
                out["attributes"]["gift"] = True
            elif val == "cheap":
                out["sort"] = out["sort"] or "price_asc"
            elif val == "expensive":
                out["sort"] = out["sort"] or "price_desc"
            elif val in _HINGLISH_FILLER:
                pass  # relation / filler word: consumed, no facet
            elif val in syn_cat and out["category"] is None:
                # "khanai" -> earphones, "kurti" -> kurta, "chappal" -> sandals
                out["category"] = syn_cat[val]
                category_phrase = phrase
            else:
                # unknown target (e.g. "mithai" -> "sweets"): keep it as a facet
                out["attributes"][normalise(val)] = True
                if out["category"] is None and normalise(val) in syn_cat:
                    out["category"] = syn_cat[normalise(val)]
                    category_phrase = phrase
            for j in range(i, i + size):
                removed[j] = True

    # ---- free text = remaining meaningful tokens ----------------------------
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
    # remember the surface form of the category (used for a relaxed SQL filter)
    if category_phrase:
        out["attributes"]["_category_phrase"] = category_phrase
    return out


def parse_constraints(text: str, lex: dict[str, dict[str, str]] | None = None) -> dict:
    """Parse a natural-language query into structured constraints (deterministic).

    Optional `lex` lets callers inject a DB-derived lexicon; the default is the
    catalogue-backed lexicon (falls back to the in-memory seed when the DB is
    empty or unreachable) — offline-safe, no LLM involved.
    """
    if lex is None:
        try:
            lex = _lexicon.default_lexicon()
        except Exception:  # noqa: BLE001
            lex = _lexicon._seed_lexicon()
    return _parse_constraints(text, lex)


# --------------------------------------------------------------------------
# low-level search primitives
# --------------------------------------------------------------------------
_FTS_STOP = {"and", "or", "not", "near", "the", "a", "an", "of", "in", "on",
             "for", "to", "with", "at", "by", "is", "are", "be", "it"}


def _fts_tokens(text: str) -> list[str]:
    """Tokens used for the FTS5 MATCH expression (operators dropped, quoted)."""
    toks: list[str] = []
    for t in re.findall(r"[A-Za-z0-9]+", (text or "").lower()):
        if t in _FTS_STOP or len(t) < 2 or t.isdigit():
            continue
        if t not in toks:
            toks.append(t)
    return toks


def _sanitise_fts(text: str) -> str | None:
    """Turn free text into a safe FTS5 MATCH expression (quoted tokens only)."""
    toks = _fts_tokens(text)
    if not toks:
        return None
    return " ".join('"' + t + '"' for t in toks)


# FTS availability is cached per connection ("pre-load the FTS5 setup once").
_FTS_READY: dict[int, bool] = {}


def _fts_ready(conn: sqlite3.Connection) -> bool:
    key = id(conn)
    if key in _FTS_READY:
        return _FTS_READY[key]
    ok = False
    try:
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products_fts'"
        ).fetchone()
        ok = r is not None
    except sqlite3.Error:
        ok = False
    _FTS_READY[key] = ok
    return ok


def warmup(conn: sqlite3.Connection) -> None:
    """Ensure the FTS5 index exists (tokenizer setup) once per process."""
    try:
        from app import db  # type: ignore

        db.fts_schema(conn)
    except Exception as exc:  # noqa: BLE001
        log.debug("fts warmup failed: %s", exc)
    _FTS_READY[id(conn)] = True


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where_clause(constraints: dict, *, include_stock: bool = True,
                  prefix: str = "") -> tuple[str, list[Any]]:
    """Cheap, index-friendly WHERE clause (brand/price/rating/stock/exclusions).

    `prefix` qualifies the column names (needed inside the FTS5 JOIN, where
    `brand` exists in both tables).

    Category is applied separately (as a refinement) so that no full-catalogue
    `title LIKE '%cat%'` scan is triggered.
    """
    conds: list[str] = []
    params: list[Any] = []
    brands = constraints.get("brand") or []
    if brands:
        ors = []
        for b in brands:
            ors.append(f"{prefix}brand LIKE ? ESCAPE '\\'")
            params.append(_like_escape(str(b).lower()))
        conds.append("(" + " OR ".join(ors) + ")")
    not_brands = (constraints.get("attributes") or {}).get("not_brand") or []
    for b in not_brands:
        conds.append(f"{prefix}brand NOT LIKE ? ESCAPE '\\'")
        params.append(_like_escape(str(b).lower()))
    if constraints.get("price_min") is not None:
        conds.append(f"{prefix}price >= ?")
        params.append(float(constraints["price_min"]))
    if constraints.get("price_max") is not None:
        conds.append(f"{prefix}price <= ?")
        params.append(float(constraints["price_max"]))
    if constraints.get("rating_min") is not None:
        conds.append(f"{prefix}rating >= ?")
        params.append(float(constraints["rating_min"]))
    not_attrs = (constraints.get("attributes") or {}).get("not_attribute") or []
    for t in not_attrs[:4]:
        conds.append(f"COALESCE({prefix}title,'') NOT LIKE ? ESCAPE '\\'")
        params.append(_like_escape("%" + str(t).lower() + "%"))
    if include_stock:
        conds.append(f"{prefix}in_stock = 1")
    return (" AND ".join(conds) if conds else "1=1"), params


def _category_tier(title: str, category_main: str, categories: str,
                  cat: str | list[str]) -> int:
    """How well does a product match the requested category?

    3 = the category is one of the catalogue category segments
    2 = the category is part of a segment ("Over-Ear Headphones")
    1 = the category only shows up in the title / as an accessory label
    0 = no match

    `cat` may be a list of aliases (e.g. "tv" + "televisions" + "tv & video");
    the best matching alias wins.
    """
    cats = [cat] if isinstance(cat, str) else list(cat or [])
    cats = [str(c or "").strip().lower() for c in cats if c]
    if not cats:
        return 0
    best = 0
    for c in cats:
        best = max(best, _category_tier_one(title, category_main, categories, c))
        if best == 3:
            break
    return best


def _category_tier_one(title: str, category_main: str, categories: str,
                       c: str) -> int:
    if not c:
        return 0
    blob = str(categories or "")
    try:
        import json as _json

        segs = _json.loads(blob) if blob else []
    except (ValueError, TypeError):
        segs = []
    if isinstance(segs, str):
        segs = [segs]
    segs = [str(s).strip().lower() for s in segs if str(s).strip()]

    def _match(s: str) -> int:
        if s == c or s == c + "s" or c == s + "s":
            return 3
        if s.startswith(c + " ") or s.endswith(" " + c) or s.startswith(c + " &") \
                or s.endswith("& " + c):
            return 2
        if c in s:
            return 1
        return 0

    # accessory segments are skipped, but they must not veto the *other*
    # segments ("Headphones, Earbuds & Accessories" -> "Over-Ear Headphones")
    best = 0
    for s in reversed(segs):
        if c in s and any(w in s for w in ("accessor", "replacement", "spare",
                                           "refurbished", "cover", "case ")):
            continue
        t = _match(s)
        if t:
            best = max(best, t)
        if best == 3:
            break
    if best:
        return best
    cm = str(category_main or "").strip().lower()
    if cm == c or cm == c + "s":
        return 3
    if c in cm:
        return 2
    if c in str(title or "").lower():
        return 1
    return 0


def _filter_category(conn: sqlite3.Connection, ids: list[str], cat: str) -> list[str]:
    """Refine a candidate list by category (segment aware)."""
    if not ids or not cat:
        return ids
    q = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"SELECT product_id, title, category_main, categories FROM products "
            f"WHERE product_id IN ({q})", list(ids)).fetchall()
    except sqlite3.Error as exc:
        log.warning("category refinement failed: %s", exc)
        return ids
    keep = []
    for r in rows:
        if _category_tier(r["title"], r["category_main"], r["categories"], cat) >= 1:
            keep.append(r["product_id"])
    return keep


def _fts_top(conn: sqlite3.Connection, text: str, limit: int = FTS_TOP,
             constraints: dict | None = None) -> list[tuple[str, float]]:
    """FTS5 bm25 top-`limit`, joined with the SQL pre-filter.

    Falls back gracefully: the AND-match is tried first, then a looser OR of
    the two most informative tokens; the SQL filter is relaxed (rating, then
    price) before giving up.
    """
    tokens = _fts_tokens(text)
    if not tokens or not _fts_ready(conn):
        return []
    constraints = constraints or {}
    strict = " ".join('"' + t + '"' for t in tokens)
    loose = " OR ".join('"' + t + '"' for t in tokens[:2])
    matches = [strict] + ([loose] if loose != strict else [])

    base = ("SELECT products_fts.product_id AS product_id, bm25(products_fts) AS bm25 "
            "FROM products_fts JOIN products p ON p.product_id = products_fts.product_id "
            "WHERE products_fts MATCH ?")
    where_variants: list[tuple[str, list[Any]]] = []
    w, p = _where_clause(constraints, prefix="p.")
    where_variants.append((w, list(p)))
    if constraints.get("rating_min") is not None:
        w2, p2 = _where_clause({**constraints, "rating_min": None}, prefix="p.")
        where_variants.append((w2, list(p2)))
    if constraints.get("price_max") is not None or constraints.get("price_min") is not None:
        w3, p3 = _where_clause({**constraints, "price_max": None, "price_min": None},
                               prefix="p.")
        where_variants.append((w3, list(p3)))

    for mi, match in enumerate(matches):
        for wi, (where, params) in enumerate(where_variants):
            sql = base + f" AND ({where}) ORDER BY bm25 LIMIT ?"
            try:
                rows = conn.execute(sql, [match] + list(params) + [limit]).fetchall()
            except sqlite3.Error as exc:
                log.warning("FTS query failed (%s)", exc)
                return []
            if rows:
                if mi or wi:
                    log.info("FTS relaxed (match=%d where=%d)", mi, wi)
                return [(r["product_id"], float(r["bm25"])) for r in rows]
    return []


def _dense_top(query: str, k: int = DENSE_TOP,
               constraints: dict | None = None) -> list[tuple[str, float]]:
    """Dense ANN top-k (LanceDB) with the SQL pre-filter pushed down."""
    vec = _emb.encode_query(query)
    if vec is None:
        return []
    constraints = constraints or {}
    filt: dict[str, Any] = {}
    if constraints.get("price_min") is not None:
        filt["price_min"] = float(constraints["price_min"])
    if constraints.get("price_max") is not None:
        filt["price_max"] = float(constraints["price_max"])
    if constraints.get("rating_min") is not None:
        filt["rating_min"] = float(constraints["rating_min"])
    try:
        res = _emb.search_vectors(vec, k=max(k * 3, 50), filt=filt or None)
    except Exception as exc:  # noqa: BLE001
        log.warning("dense search failed (%s)", exc)
        return []
    out = []
    for pid, score in res:
        out.append((str(pid), float(score)))
        if len(out) >= k:
            break
    return out


def _product_titles(conn: sqlite3.Connection, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    q = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"SELECT product_id, title FROM products WHERE product_id IN ({q})", ids
        ).fetchall()
        return {r["product_id"]: (r["title"] or "") for r in rows}
    except sqlite3.Error:
        return {}


# cross-encoder singleton (module level -> loaded at most once per process)
_CE: Any = None
_CE_FAILED = False


def _get_cross_encoder():
    global _CE, _CE_FAILED
    if _CE is not None:
        return _CE
    if _CE_FAILED:
        return None
    try:
        from sentence_transformers import CrossEncoder

        _CE = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        log.info("cross-encoder loaded")
    except Exception as exc:  # noqa: BLE001
        log.warning("cross-encoder unavailable (%s); skipping rerank", exc)
        _CE = None
        _CE_FAILED = True
    return _CE


def graph_expand(conn: sqlite3.Connection, product_ids: list[str],
                 k: int = 30) -> list[Hit]:
    """Expand a set of seed products through `product_edges` (co-purchase etc.)."""
    if not product_ids:
        return []
    try:
        seeds = set(product_ids)
        q = ",".join("?" for _ in product_ids)
        # two indexed lookups instead of one OR (which would full-scan edges)
        rows = conn.execute(
            f"SELECT a, b, kind, weight FROM product_edges WHERE a IN ({q})",
            list(product_ids),
        ).fetchall()
        rows += conn.execute(
            f"SELECT a, b, kind, weight FROM product_edges WHERE b IN ({q})",
            list(product_ids),
        ).fetchall()
        kind_w = {"co_purchase": 2.0, "also_bought": 1.5, "co_view": 1.0}
        scores: dict[str, float] = {}
        for r in rows:
            nb = r["b"] if r["a"] in seeds else r["a"]
            if nb in seeds:
                continue
            w = float(r["weight"] or 1.0) * kind_w.get(r["kind"], 1.0)
            scores[nb] = scores.get(nb, 0.0) + w
        ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [Hit(product_id=pid, score=w, lexical_rank=None, dense_rank=None,
                    bm25=None) for pid, w in ordered]
    except sqlite3.Error as exc:
        log.warning("graph_expand failed: %s", exc)
        return []


def _rrf(lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion."""
    scores: dict[str, float] = {}
    for lst in lists:
        for rank, pid in enumerate(lst):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _cross_encode_rerank(conn: sqlite3.Connection, query: str,
                         ranked: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Rerank the head of the fused list with a cross-encoder (optional)."""
    ce = _get_cross_encoder()
    if ce is None or not ranked:
        return ranked
    topn = min(CE_TOP, len(ranked))
    ids = [pid for pid, _ in ranked[:topn]]
    titles = _product_titles(conn, ids)
    # long titles blow up the token count -> the cross-encoder dominates the
    # latency; the first ~160 characters carry the product identity anyway.
    pairs = [(query, (titles.get(pid) or "")[:160]) for pid in ids]
    try:
        logits = np.asarray(ce.predict(pairs), dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        log.warning("cross-encoder predict failed (%s)", exc)
        return ranked
    head_rrf = np.asarray([s for _, s in ranked[:topn]], dtype=np.float32)
    # normalise both signals to [0,1] before blending so neither dominates
    def _norm(a: np.ndarray) -> np.ndarray:
        lo, hi = float(a.min()), float(a.max())
        if hi - lo < 1e-9:
            return np.zeros_like(a)
        return (a - lo) / (hi - lo)
    ce_norm = _norm(logits)
    rrf_norm = _norm(head_rrf)
    blended = 0.5 * rrf_norm + 0.5 * ce_norm
    merged = [(pid, float(score)) for (pid, _), score in zip(ranked[:topn], blended)]
    merged.sort(key=lambda kv: -kv[1])
    return merged + ranked[topn:]


def _apply_sort(conn: sqlite3.Connection, hits: list[Hit], sort: str | None,
                category: str | None = None) -> list[Hit]:
    """Apply an explicit sort request (stable; fused score breaks ties)."""
    if not hits or not sort:
        return hits
    ids = [h.product_id for h in hits]
    q = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"SELECT product_id, price, rating, popularity, title FROM products "
            f"WHERE product_id IN ({q})", ids).fetchall()
    except sqlite3.Error:
        return hits
    meta = {r["product_id"]: r for r in rows}
    if sort in ("price_asc", "price_desc"):
        # a pure price sort would put cheap accessories first ("earphone case"
        # at Rs.3 above a real pair of earphones) -> drop obvious accessories
        # when enough real products remain.
        real = [h for h in hits
                if not _looks_like_accessory(
                    meta.get(h.product_id, {"title": ""})["title"], category)]
        if len(real) >= 5:
            hits = real
    if sort == "price_asc":
        key = lambda h: (meta[h.product_id]["price"] if h.product_id in meta and
                         meta[h.product_id]["price"] is not None else 1e18, -h.score)
    elif sort == "price_desc":
        key = lambda h: (-(meta[h.product_id]["price"] if h.product_id in meta and
                           meta[h.product_id]["price"] is not None else 0.0), -h.score)
    elif sort == "rating_desc":
        key = lambda h: (-(meta[h.product_id]["rating"] if h.product_id in meta and
                           meta[h.product_id]["rating"] is not None else 0.0), -h.score)
    elif sort == "popularity":
        key = lambda h: (-(meta[h.product_id]["popularity"] if h.product_id in meta and
                           meta[h.product_id]["popularity"] is not None else 0.0), -h.score)
    else:
        return hits
    return sorted(hits, key=key)


def _personalise(conn: sqlite3.Connection, hits: list[Hit],
                 user_id: str | None) -> list[Hit]:
    """Optional personalisation re-rank (see app/personalize.py)."""
    if not user_id or not hits:
        return hits
    try:
        from app import personalize

        return personalize.rerank_hits(conn, hits, user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("personalisation failed: %s", exc)
        return hits


# --------------------------------------------------------------------------
# the main entry point
# --------------------------------------------------------------------------
def hybrid_search(conn: sqlite3.Connection, query: str,
                  constraints: dict | None = None, k: int = 20,
                  user_id: str | None = None) -> list[Hit]:
    """Hybrid retrieval: SQL pre-filter -> FTS5 + dense ANN + graph -> RRF ->
    cross-encoder rerank -> personalisation -> explicit sort.

    Never raises; degrades to lexical-only (then to a SQL ranking) on failure.
    """
    t0 = time.perf_counter()
    stages: dict[str, float] = {}
    try:
        lex = _lexicon.load_lexicon(conn)
    except Exception:  # noqa: BLE001
        lex = _lexicon._seed_lexicon()
    stages["lexicon_ms"] = (time.perf_counter() - t0) * 1000
    if constraints is None:
        constraints = parse_constraints(query, lex=lex)
    else:
        merged = _default_constraints()
        merged.update({k: v for k, v in constraints.items() if v is not None})
        constraints = merged
    aliases = _lexicon.category_aliases(lex, constraints.get("category"))

    try:
        search_text = _search_text(constraints, aliases)
        dense_text = search_text
        fts_query = _fts_text(constraints) or search_text

        t = time.perf_counter()
        lexical = _fts_top(conn, fts_query, limit=FTS_TOP, constraints=constraints)
        stages["fts_ms"] = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        dense = _dense_top(dense_text, k=DENSE_TOP, constraints=constraints)
        stages["dense_ms"] = (time.perf_counter() - t) * 1000

        # category refinement on the lexical candidates (cheap, in-Python)
        cat = constraints.get("category")
        if cat and lexical:
            refined = _filter_category(conn, [pid for pid, _ in lexical],
                                       ([cat] + aliases))
            if not refined:
                relaxed = _filter_category(conn, [pid for pid, _ in lexical], [cat])
                refined = relaxed
            if refined:
                keep = set(refined)
                lexical = [(pid, v) for pid, v in lexical if pid in keep]
            else:
                lexical = []

        seed_ids = [pid for pid, _ in (lexical + dense)[:50]]
        t = time.perf_counter()
        graph = graph_expand(conn, list(dict.fromkeys(seed_ids)), k=GRAPH_TOP)
        stages["graph_ms"] = (time.perf_counter() - t) * 1000

        # a weak/empty lexical channel is complemented by popular in-catalogue
        # items that satisfy the constraints (pure category queries)
        sql_channel: list[str] = []
        if len(lexical) < 5:
            sql_channel = _sql_candidates(conn, constraints, aliases, limit=50)

        lexical_rank = {pid: i for i, (pid, _) in enumerate(lexical)}
        dense_rank = {pid: i for i, (pid, _) in enumerate(dense)}
        bm25 = {pid: v for pid, v in lexical}

        fused = _rrf(
            [[pid for pid, _ in lexical],
             [pid for pid, _ in dense],
             [h.product_id for h in graph],
             sql_channel], k=RRF_K)
        if not fused:
            return _sql_fallback(conn, constraints, k)

        ranked = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
        t = time.perf_counter()
        ranked = _hard_filter(conn, ranked, constraints)
        stages["filter_ms"] = (time.perf_counter() - t) * 1000
        if not ranked:
            return _sql_fallback(conn, constraints, k)

        t = time.perf_counter()
        ranked = _cross_encode_rerank(conn, search_text or dense_text, ranked)
        stages["ce_ms"] = (time.perf_counter() - t) * 1000
        if constraints.get("category"):
            ranked = _category_preference(conn, ranked, constraints["category"],
                                          aliases)
        ranked = _attribute_preference(conn, ranked, constraints)

        hits = [
            Hit(product_id=pid, score=score,
                lexical_rank=lexical_rank.get(pid),
                dense_rank=dense_rank.get(pid),
                bm25=bm25.get(pid))
            for pid, score in ranked[: max(k, 25)]
        ]
        hits = _personalise(conn, hits, user_id)
        hits = _apply_sort(conn, hits, constraints.get("sort"),
                           constraints.get("category"))
        stages["total_ms"] = (time.perf_counter() - t0) * 1000
        global LAST_TIMINGS
        LAST_TIMINGS = stages
        log.debug("hybrid_search(%r) -> %d hits in %.0f ms %s", query, len(hits),
                  stages["total_ms"], stages)
        return hits[:k]
    except Exception as exc:  # noqa: BLE001 — never raise on bad input
        log.exception("hybrid_search failed (%s); falling back to lexical", exc)
        return _lexical_only(conn, query, k)


def _search_text(constraints: dict, extra_aliases: list[str] | None = None) -> str:
    """Lexical/dense query text built from the *meaningful* parts only.

    Relation words ("for my brother"), intent words ("gift") and constraint
    fragments are dropped so they cannot pollute the lexical match.
    """
    parts: list[str] = []
    ft = (constraints.get("free_text") or "").strip()
    if ft:
        parts.append(ft)
    for b in constraints.get("brand") or []:
        parts.append(str(b))
    if constraints.get("category"):
        parts.append(str(constraints["category"]))
    attrs = constraints.get("attributes") or {}
    phrase = attrs.get("_category_phrase")
    if phrase:
        parts.append(str(phrase))
    # NB: category aliases are deliberately NOT added to the query text — an
    # extra token makes the FTS AND-match too strict ("head sets" ...)
    for k, v in attrs.items():
        if k.startswith("not_") or k in ("negations", "gift", "_category_phrase"):
            continue
        if v is True:
            parts.append(k.replace("_", " "))
    # dedupe case-insensitively, keep order
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        key = p.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(p.strip())
    return " ".join(out).strip()


def _fts_text(constraints: dict) -> str:
    """Text used for the FTS5 channel.

    Deliberately *without* the category synonym aliases: FTS5 matches the
    quoted tokens with AND, so an extra alias phrase ("head sets") would
    collapse recall to almost nothing.
    """
    parts: list[str] = []
    ft = (constraints.get("free_text") or "").strip()
    if ft:
        parts.append(ft)
    for b in constraints.get("brand") or []:
        parts.append(str(b))
    if constraints.get("category"):
        parts.append(str(constraints["category"]))
    attrs = constraints.get("attributes") or {}
    for k, v in attrs.items():
        if k.startswith("not_") or k in ("negations", "gift", "_category_phrase"):
            continue
        if v is True:
            parts.append(k.replace("_", " "))
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        key = p.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(p.strip())
    return " ".join(out).strip()


def _attribute_preference(conn: sqlite3.Connection, ranked: list[tuple[str, float]],
                          constraints: dict) -> list[tuple[str, float]]:
    """Soft-prefer candidates whose text actually contains the requested facet.

    The lexical channel only guarantees that *some* token matched; a query
    like "noise cancelling headphones" must not return plain studio monitors.
    Candidates that state the facet (title / features / description) are
    boosted, the rest are damped — but only when enough candidates match, so
    a rare facet never empties the result set.
    """
    attrs = constraints.get("attributes") or {}
    wanted = [k for k, v in attrs.items()
              if v is True and not str(k).startswith("_")
              and k not in ("gift", "negations")]
    if not wanted or not ranked:
        return ranked
    alias_map = {a: _lexicon.attribute_aliases(a) for a in wanted}
    ids = [pid for pid, _ in ranked[:300]]
    q = ",".join("?" * len(ids))
    try:
        rows = conn.execute(
            f"SELECT product_id, title, features, description FROM products "
            f"WHERE product_id IN ({q})", ids).fetchall()
    except sqlite3.Error:
        return ranked
    if not rows:
        return ranked

    def _has(text: str, alias: str) -> bool:
        if alias in text:
            if len(alias) <= 4:  # short alias -> word boundary only
                return re.search(r"\b" + re.escape(alias) + r"\b", text) is not None
            return True
        return False

    matched: dict[str, int] = {}
    is_accessory: set[str] = set()
    cat = constraints.get("category")
    for r in rows:
        blob = normalise(" ".join([
            r["title"] or "", r["features"] or "", r["description"] or ""]))
        hits = 0
        for a, aliases in alias_map.items():
            if any(_has(blob, al) for al in aliases):
                hits += 1
        matched[r["product_id"]] = hits
        if _looks_like_accessory(r["title"], cat):
            is_accessory.add(r["product_id"])
    n_matched = sum(1 for pid, v in matched.items()
                     if v > 0 and pid not in is_accessory)
    if n_matched < 3:
        return ranked  # too few candidates state the facet -> do not distort
    out = []
    for pid, score in ranked:
        if pid in is_accessory:
            mult = 0.7
        else:
            mult = 1.06 if matched.get(pid, 0) else 0.94
        out.append((pid, score * mult))
    out.sort(key=lambda kv: -kv[1])
    return out


_ACCESSORY_TITLE_WORDS = (
    "cover", "case", "replacement", "spare", "skin", "sticker", "adapter",
    "charger", "cable", "remote", "protector", "strap", "holder", "stand",
    "compatible with", "fits ", "for macbook", "refurbished", "open box",
    "mount", "cooling pad", "cleaning kit", "ear pad", "earpad",
    "eartip", "ear tip", "tips", "sleeve", "pouch", "bag", "clip", "hook",
    "dock", "base", "screwdriver", "cleaning", "storage box", "wristband",
)


def _looks_like_accessory(title: str, category: str | None) -> bool:
    """Heuristic: is this product an accessory *of* the requested category?

    Examples: "Laptop Cooling Pad for 14-17 Inch Gaming Laptop",
    "Replacement Earpads for Bose 700", "Remote Control for All LCD TV".
    """
    t = str(title or "").lower()
    if not t:
        return False
    if any(w in t for w in _ACCESSORY_TITLE_WORDS):
        return True
    cat = str(category or "").strip().lower()
    if cat and " for " in t:
        head, _, tail = t.rpartition(" for ")
        if cat not in head and cat in tail:
            return True
    return False


def _category_preference(conn: sqlite3.Connection, ranked: list[tuple[str, float]],
                         category: str,
                         aliases: list[str] | None = None) -> list[tuple[str, float]]:
    """Soft-prefer candidates that really are in the requested category.

    Tier 2: the label appears in category_main / the category path
    Tier 1: the label only appears in the title (often a false positive)
    Tier 0: no match at all
    """
    if not ranked or not category:
        return ranked
    ids = [pid for pid, _ in ranked[:300]]
    q = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"SELECT product_id, title, category_main, categories FROM products "
            f"WHERE product_id IN ({q})", ids).fetchall()
    except sqlite3.Error:
        return ranked
    cats = [str(category).lower()] + [str(a).lower() for a in (aliases or [])]
    cats = list(dict.fromkeys(c for c in cats if c))
    meta = {r["product_id"]: r for r in rows}
    tiers: dict[int, list[tuple[str, float]]] = {3: [], 2: [], 1: [], 0: []}
    accessory: set[str] = set()
    for pid, score in ranked:
        row = meta.get(pid)
        tier = 3 if row is None else _category_tier(
            row["title"], row["category_main"], row["categories"], cats)
        tiers[tier].append((pid, score))
        if row is not None and _looks_like_accessory(row["title"], category):
            accessory.add(pid)
    strong = tiers[3] + tiers[2]
    weak = tiers[1]
    # never *drop* a candidate here: a wrong category judgement must not
    # remove a highly relevant product.  Only re-weight.
    keep = strong + weak + tiers[0]
    bonus = {3: 0.0015, 2: 0.0008, 1: 0.0, 0: -0.0020}
    mult = {3: 1.03, 2: 1.01, 1: 1.0, 0: 0.96}
    tier_of: dict[str, int] = {}
    for tier, items in tiers.items():
        for pid, _s in items:
            tier_of[pid] = tier
    # accessories of the requested category are damped (but never removed)
    n_real = sum(1 for pid, _ in keep if pid not in accessory)
    acc_mult = 0.6 if n_real >= 3 else 1.0
    scored = [(pid, s * mult.get(tier_of.get(pid, 1), 1.0)
                * (acc_mult if pid in accessory else 1.0)) for pid, s in keep]
    scored.sort(key=lambda kv: -kv[1])
    return scored


def _hard_filter(conn: sqlite3.Connection, ranked: list[tuple[str, float]],
                 constraints: dict) -> list[tuple[str, float]]:
    """Drop fused candidates that violate the hard constraints (price/brand/...)."""
    if not ranked:
        return ranked
    where, params = _where_clause(constraints)
    if where.strip() in ("1=1", "in_stock = 1"):
        return ranked
    ids = [pid for pid, _ in ranked[:300]]
    q = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"SELECT product_id FROM products WHERE {where} AND product_id IN ({q})",
            list(params) + list(ids)).fetchall()
    except sqlite3.Error as exc:
        log.warning("hard filter failed: %s", exc)
        return ranked
    keep = {r["product_id"] for r in rows}
    out = [(pid, s) for pid, s in ranked if pid in keep]
    return out or ranked


def _sql_candidates(conn: sqlite3.Connection, constraints: dict,
                    aliases: list[str] | None = None, limit: int = 50) -> list[str]:
    """Popular in-catalogue products that satisfy the constraints.

    Used as an extra retrieval channel when the lexical channel is empty
    (e.g. a pure category query like "tv" where bm25 favours accessories).
    """
    try:
        where, params = _where_clause(constraints)
        rows = conn.execute(
            f"SELECT product_id FROM products WHERE {where} "
            f"ORDER BY COALESCE(popularity,0) DESC, COALESCE(rating,0) DESC, "
            f"COALESCE(price,1e18) ASC LIMIT ?", params + [limit * 4]).fetchall()
        ids = [r["product_id"] for r in rows]
    except sqlite3.Error as exc:
        log.warning("SQL candidates failed: %s", exc)
        return []
    cat = constraints.get("category")
    if cat and ids:
        cats = [cat] + list(aliases or [])
        ids = _filter_category(conn, ids, cats)
    return ids[:limit]


def _sql_fallback(conn: sqlite3.Connection, constraints: dict, k: int) -> list[Hit]:
    """Last-resort result when no lexical/dense/graph signal exists."""
    try:
        where, params = _where_clause(constraints)
        rows = conn.execute(
            f"SELECT product_id FROM products WHERE {where} "
            f"ORDER BY COALESCE(popularity,0) DESC, COALESCE(rating,0) DESC, "
            f"COALESCE(price,1e18) ASC LIMIT ?", params + [k * 2]).fetchall()
        ids = [r["product_id"] for r in rows]
        cat = constraints.get("category")
        if cat and ids:
            refined = _filter_category(conn, ids, cat)
            if refined:
                ids = refined
        return [Hit(product_id=pid, score=0.0, lexical_rank=None,
                    dense_rank=None, bm25=None) for pid in ids[:k]]
    except sqlite3.Error as exc:
        log.warning("SQL fallback failed: %s", exc)
        return []


def _lexical_only(conn: sqlite3.Connection, query: str, k: int) -> list[Hit]:
    """Absolute fallback: FTS only, then top-k by popularity if no FTS."""
    lexical = _fts_top(conn, query, limit=k)
    if lexical:
        return [Hit(product_id=pid, score=-1.0 / (i + 1),
                    lexical_rank=i, dense_rank=None, bm25=v)
                for i, (pid, v) in enumerate(lexical)]
    try:
        rows = conn.execute(
            "SELECT product_id FROM products WHERE in_stock = 1 "
            "ORDER BY COALESCE(popularity,0) DESC, COALESCE(rating,0) DESC LIMIT ?",
            (k,)).fetchall()
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
    """Recent / helpful reviews for a product (excerpts, not full text)."""
    try:
        q = ("SELECT review_id, rating, title, text, helpful, ts FROM reviews "
             "WHERE product_id = ?")
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
    """Similar products: co-purchase graph edges + title (FTS) similarity."""
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
            if match and _fts_ready(conn):
                rows = conn.execute(
                    "SELECT product_id, bm25(products_fts) AS bm25 FROM products_fts "
                    "WHERE products_fts MATCH ? AND product_id != ? "
                    "ORDER BY bm25 LIMIT ?", (match, product_id, k * 3)).fetchall()
                for i, r in enumerate(rows):
                    scores[r["product_id"]] = scores.get(r["product_id"], 0.0) + 1.0 / (i + 1)
        out = []
        for pid, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
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
        except sqlite3.Error:
            pass
        warmup(_CONN)
    return _CONN


_SELFTEST_QUERIES = [
    "noise cancelling headphones under 5000 with 4.5 stars",
    "sony tv",
    "gaming laptop under 80000",
    "gift for brother under 3000",
    "saste earphones for mom",
]


def _selftest() -> None:
    from app import db

    conn = get_connection()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
    except sqlite3.Error:
        n = 0
    if not n:
        print("DB empty — skipping live check")
        return

    try:
        _lexicon.build_lexicon(conn)
    except Exception as exc:  # noqa: BLE001
        print(f"[retrieval] lexicon build failed ({exc}); continuing with seed")

    total = 0.0
    for q in _SELFTEST_QUERIES:
        cons = parse_constraints(q)
        t0 = time.perf_counter()
        hits = hybrid_search(conn, q, k=5)
        dt = (time.perf_counter() - t0) * 1000
        total += dt
        print(f"\nQUERY: {q!r}   ({dt:.0f} ms)")
        print(f"  constraints: {cons}")
        if not hits:
            print("  (no hits)")
            continue
        for h in hits:
            p = get_product(conn, h.product_id)
            if p:
                print(f"  {h.product_id}  ₹{p.get('price')}  ★{p.get('rating')}  "
                      f"score={h.score:.4f}  {str(p.get('title'))[:58]!r}")
            else:
                print(f"  {h.product_id}")
    print(f"\n[retrieval] mean hybrid_search latency: {total/len(_SELFTEST_QUERIES):.0f} ms")
    print("[retrieval] selftest OK")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    if "--selftest" in sys.argv:
        _selftest()
