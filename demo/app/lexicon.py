"""Brand + category + Hinglish lexicon for the Aria retrieval core.

The lexicon is derived from the SQLite `products` table (brands, categories)
and merged with a small hand-written seed of synonyms and Hinglish/Indic
mappings.  It is persisted into the `lexicon` table (owned by the orchestrator's
DDL in `app/db.py`) so every module shares one deterministic vocabulary.

Layout of the returned mapping::

    {
        "brand":     {term_lower: value_display},   # "sony"  -> "Sony"
        "category":  {term_lower: value_display},   # "tv"    -> "TV"
        "synonym":  {term_lower: canonical},        # "notebook" -> "laptop"
        "hinglish": {term_lower: canonical},        # "khanai" -> "earphones"
    }

Everything here is offline-safe: `load_lexicon` never touches the network and
falls back to the in-memory seed when the DB is empty or the table is missing.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")

# Marketing junk that Amazon puts into the `categories` JSON — never a category.
_JUNK_CAT = re.compile(
    r"(free\b|new in\b|prime\b|shipping|delivery|sale\b|deal\b|discount|offer\b|"
    r"coupon|bestseller|best seller|top rated|guarantee|100%|featured|in stock|"
    r"limited|deal of the day|shop now|buy now|best of)",
    re.I,
)

# A usable category label is 1..3 words and short.
_MAX_CAT_WORDS = 3
_MAX_CAT_LEN = 42

# Amazon brand strings that are really common English words ("For", "New", ...).
# They would otherwise hijack every query ("gift for brother" -> brand "for").
BRAND_BLOCKLIST = {
    "for", "and", "the", "with", "new", "best", "top", "one", "two", "three",
    "four", "five", "six", "seven", "eight", "nine", "ten", "red", "blue",
    "green", "black", "white", "pink", "grey", "gray", "gold", "silver", "size",
    "set", "pack", "type", "pro", "max", "plus", "mini", "air", "hot", "sale",
    "free", "gift", "star", "love", "life", "home", "auto", "car", "case",
    "cover", "wall", "water", "power", "first", "last", "sun", "moon", "king",
    "queen", "iron", "steel", "wood", "glass", "digital", "smart", "true",
    "false", "more", "other", "all", "big", "small", "good", "nice", "cool",
    "easy", "fast", "high", "low", "soft", "hard", "pure", "fine", "yes", "no",
    "ok", "we", "my", "me", "it", "in", "on", "at", "to", "of", "by", "pc",
    "usb", "led", "hd", "tv", "ac", "dc", "cd", "dvd", "mp3", "mp4", "wi",
    "wow", "yes",
    # category words that some Amazon sellers use as their "brand"
    "laptop", "laptops", "phone", "phones", "headphone", "headphones",
    "earphone", "earphones", "earbud", "earbuds", "camera", "cameras", "watch",
    "watches", "tablet", "tablets", "printer", "mouse", "keyboard", "monitor",
    "monitors", "speaker", "speakers", "shoe", "shoes", "sneaker", "sneakers",
    "mixer", "fridge", "refrigerator", "microwave", "fan", "cooler", "kettle",
    "router", "cable", "charger", "vacuum", "iron", "toaster", "induction",
    "chimney", "geyser", "sandals", "chappal", "guitar", "piano", "drums",
    "violin", "tablet", "monitor", "router", "power bank", "washing machine",
    "television", "refrigerator", "notebook", "smartwatch", "soundbar",
}


def normalise(text: str | None) -> str:
    """Lowercase, strip currency/punctuation, collapse whitespace."""
    if not text:
        return ""
    s = str(text).lower()
    s = s.replace("₹", " ").replace("$", " ").replace("rs.", " ")
    s = s.replace("_", " ").replace("-", " ")
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s)
    return s.strip()


def is_usable_category(term: str) -> bool:
    """True when a category label is a plausible product category."""
    if not term:
        return False
    if len(term) > _MAX_CAT_LEN:
        return False
    if len(term.split()) > _MAX_CAT_WORDS:
        return False
    if _JUNK_CAT.search(term):
        return False
    return True


# --------------------------------------------------------------------------
# Hand-written seeds.  Value == canonical term used by the rest of the stack.
# --------------------------------------------------------------------------
SEED_SYNONYMS: dict[str, str] = {
    # laptops / notebooks
    "laptop": "laptop", "laptops": "laptop", "notebook": "laptop",
    "notebooks": "laptop", "ultrabook": "laptop", "lap top": "laptop",
    "macbook": "laptop", "notebook computer": "laptop",
    # audio
    "earbuds": "earphones", "earbud": "earphones", "buds": "earphones",
    "tws": "earphones", "earphone": "earphones", "earphones": "earphones",
    "in ear": "earphones", "in ear headphones": "earphones",
    "headphone": "headphones", "headphones": "headphones", "headset": "headphones",
    "head sets": "headphones", "over ear headphones": "headphones",
    "speaker": "speakers", "speakers": "speakers", "soundbar": "speakers",
    "sound bar": "speakers", "bluetooth speaker": "speakers",
    # phones
    "phone": "phone", "phones": "phone", "smartphone": "phone",
    "smartphones": "phone", "cellphone": "phone", "cell phone": "phone",
    "mobile": "phone", "mobiles": "phone", "mobile phone": "phone",
    "android phone": "phone", "iphone": "phone",
    # TV
    "tv": "tv", "tvs": "tv", "television": "tv", "televisions": "tv",
    "smart tv": "tv", "oled": "tv", "qled": "tv", "led tv": "tv",
    "led tv": "tv", "4k tv": "tv", "tv set": "tv",
    # appliances
    "fridge": "fridge", "fridges": "fridge", "refrigerator": "fridge",
    "refrigerators": "fridge", "refrigerator": "fridge",
    "washing machine": "washing machine", "washing machines": "washing machine",
    "washer": "washing machine", "washing machines": "washing machine",
    "mixer": "mixer", "grinder": "mixer", "mixie": "mixer",
    "mixer grinder": "mixer", "juicer": "mixer", "mixer grinder juicer": "mixer",
    "ac": "ac", "air conditioner": "ac", "airconditioner": "ac",
    "air conditioners": "ac", "airconditioning": "ac",
    "split ac": "ac", "window ac": "ac", "1 ac": "ac", "2 ac": "ac",
    "3 ac": "ac", "inverter ac": "ac", "air conditioner ac": "ac",
    "microwave": "microwave", "microwaves": "microwave", "oven": "microwave",
    "kettle": "kettle", "geyser": "geyser", "water heater": "geyser",
    "fan": "fan", "ceiling fan": "fan", "cooler": "cooler",
    "vacuum": "vacuum cleaner", "vacuum cleaner": "vacuum cleaner",
    "iron": "iron", "toaster": "toaster", "induction": "induction",
    "cooktop": "induction", "chimney": "chimney",
    # footwear / clothing
    "sneaker": "shoes", "sneakers": "shoes", "shoe": "shoes", "shoes": "shoes",
    "trainers": "shoes", "running shoes": "shoes", "sandals": "sandals",
    "sandal": "sandals", "slippers": "sandals", "chappal": "sandals",
    "chappals": "sandals", "flip flops": "sandals", "flipflops": "sandals",
    # computing peripherals
    "tablet": "tablet", "tablets": "tablet", "ipad": "tablet",
    "keyboard": "keyboard", "mouse": "mouse", "monitor": "monitor",
    "monitors": "monitor", "display": "monitor", "printer": "printer",
    "scanner": "printer", "router": "router", "wifi router": "router",
    "power bank": "power bank", "powerbank": "power bank", "charger": "charger",
    "cable": "cable", "usb": "cable",
    # cameras / wearables
    "camera": "camera", "cameras": "camera", "dslr": "camera",
    "watch": "watch", "watches": "watch", "smartwatch": "watch",
    "smart watch": "watch",
    # musical instruments
    "guitar": "guitar", "guitars": "guitar", "acoustic guitar": "guitar",
    "piano": "piano", "keyboard piano": "piano",
    "drum": "drums", "drums": "drums", "drum kit": "drums",
    "violin": "violin", "sitar": "guitar", "tabla": "drums",
}

# Hinglish / Indic token -> canonical concept.  Used for intent/entity
# extraction (never returned verbatim to the user).
SEED_HINGLISH: dict[str, str] = {
    "mithai": "sweets", "mithaai": "sweets", "mithayi": "sweets",
    "meethai": "sweets", "sweets": "sweets", "kaju katli": "sweets",
    "laddu": "sweets", "ladoo": "sweets", "barfi": "sweets",
    "kurti": "kurta", "kurtis": "kurta", "kurta": "kurta", "kurtas": "kurta",
    "chappal": "sandals", "chappals": "sandals", "jooti": "sandals",
    "khanai": "earphones", "khaney": "earphones",
    "mobile": "phone", "mobiles": "phone", "mobile phone": "phone",
    "sasta": "cheap", "saste": "cheap", "sasti": "cheap", "cheap": "cheap",
    "sasta": "cheap", "mehenga": "expensive", "mehenge": "expensive",
    "kam": "under", "se kam": "under", "kam se": "under",
    "zyada": "above", "se zyada": "above", "kam se zyada": "above",
    "gift": "gift", "uphaar": "gift", "tohfa": "gift", "topha": "gift",
    "bhai": "brother", "bhaiya": "brother", "bhaai": "brother",
    "behen": "sister", "behan": "sister", "didi": "sister",
    "maa": "mother", "mummy": "mother", "mom": "mother", "amma": "mother",
    "papa": "father", "dad": "father", "daddy": "father", "pita": "father",
    "mere": "my", "mera": "my", "meri": "my", "liye": "for", "ke liye": "for",
    "wala": "for", "wali": "for", "ke": "of", "ka": "of", "ki": "of",
    "accha": "good", "achha": "good", "badhiya": "good", "best": "best",
    "dikhao": "show", "dikha": "show", "dekh": "see", "batao": "tell",
    "kitna": "how much", "rate": "price", "daam": "price", "dam": "price",
    "chahiye": "want", "chahie": "want", "lena": "buy", "kharid": "buy",
    "do": "give", "dhoondho": "find", "batana": "tell", "kharidiye": "buy",
}

# Common brands so brand detection works even before the catalogue is ingested.
SEED_BRANDS: list[str] = [
    "sony", "samsung", "apple", "boat", "boAt", "jbl", "oneplus", "one plus",
    "xiaomi", "mi", "redmi", "realme", "oppo", "vivo", "motorola", "nokia",
    "google", "lg", "whirlpool", "ifb", "philips", "hp", "dell", "lenovo",
    "asus", "acer", "msi", "logitech", "bose", "sennheiser", "skullcandy",
    "noise", "ambrane", "voltas", "daikin", "blue star", "bluestar", "bajaj",
    "prestige", "pigeon", "butterfly", "usha", "havells", "crompton",
    "amazonbasics", "amazon basics", "croma", "duracell", "energizer",
    "seagate", "western digital", "sandisk", "transcend", "zebronics",
    "portronics", "hammer", "ptron", "crossbeats", "mivi", "fire-boltt",
    "fire boltt", "titan", "fastrack", "casio", "fossil", "gshock",
    "yama", "yamaha", "fender", "canon", "nikon", "gopro", "garmin",
    "nintendo", "playstation", "xbox", "seagate expansion",
]

# Attributes that are meaningful product facets.
SEED_ATTRIBUTES: list[str] = [
    "noise cancelling", "noise cancellation", "anc", "active noise cancelling",
    "wireless", "bluetooth", "gaming", "waterproof", "portable", "refurbished",
    "fast charging", "fast charging", "touchscreen", "mechanical", "rgb",
    "over ear", "on ear", "in ear", "dolby", "hdr", "oled", "qled", "4k",
    "full hd", "1080p", "5g", "4g", "with mic", "bass", "smart", "foldable",
]

_LOCK = threading.Lock()
_LEX_CACHE: dict[int, tuple[int, float, dict[str, dict[str, str]]]] = {}
_DEFAULT_LEX: dict[str, dict[str, str]] | None = None
LEX_TTL = 60.0  # seconds between lexicon re-checks (the table is rebuilt often)


def _seed_lexicon() -> dict[str, dict[str, str]]:
    """Build the in-memory fallback lexicon (no DB access)."""
    out: dict[str, dict[str, str]] = {
        "brand": {},
        "category": {},
        "synonym": {},
        "hinglish": {},
    }
    for b in SEED_BRANDS:
        n = normalise(b)
        if n:
            out["brand"][n] = n
    for syn, canon in SEED_SYNONYMS.items():
        out["synonym"][normalise(syn)] = normalise(canon)
        # canonical categories are also recognised as categories
        c = normalise(canon)
        if is_usable_category(c):
            out["category"][c] = c
    for h, canon in SEED_HINGLISH.items():
        out["hinglish"][normalise(h)] = normalise(canon)
    return out


def _empty_lexicon() -> dict[str, dict[str, str]]:
    return {"brand": {}, "category": {}, "synonym": {}, "hinglish": {}}


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------
def _persist(conn: sqlite3.Connection, rows: list[tuple[str, str, str, int]]) -> None:
    """Upsert lexicon rows.  `kind` may be a comma separated list."""
    if not rows:
        return
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO lexicon(term, kind, value, n_items) VALUES(?,?,?,?)",
            rows,
        )
        conn.commit()
    except sqlite3.Error as exc:  # table may not exist yet
        log.warning("lexicon persist skipped: %s", exc)


def build_lexicon(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Derive the lexicon from the `products` table + seeds and persist it.

    Returns the merged lexicon dict:
        {"brand": {...}, "category": {...}, "synonym": {...}, "hinglish": {...}}
    """
    lex = _seed_lexicon()
    # (term, kind) -> (term, kind, value, n_items)
    rows: dict[tuple[str, str], tuple[str, str, str, int]] = {}

    def add(kind: str, term: str, value: str | None = None, n: int = 0) -> None:
        t = normalise(term)
        v = (value or term or "").strip()
        if not t:
            return
        v_norm = normalise(v)
        prev = rows.get((t, kind))
        if prev is None or n > prev[3]:
            rows[(t, kind)] = (t, kind, v or v_norm, n)
        lex.setdefault(kind, {})[t] = v or v_norm

    # --- brands from the catalogue (authoritative) -------------------------
    try:
        cur = conn.execute(
            "SELECT brand, COUNT(*) AS n FROM products "
            "WHERE brand IS NOT NULL AND TRIM(brand) != '' GROUP BY brand"
        )
        for r in cur.fetchall():
            brand = (r["brand"] or "").strip()
            if not brand:
                continue
            n = int(r["n"] or 0)
            if normalise(brand) in BRAND_BLOCKLIST:
                continue
            add("brand", brand, brand, n)
    except sqlite3.Error as exc:
        log.warning("brand scan skipped: %s", exc)

    # --- categories: category_main + leaf tokens from the categories JSON ---
    try:
        cur = conn.execute(
            "SELECT category_main, COUNT(*) AS n FROM products "
            "WHERE category_main IS NOT NULL AND TRIM(category_main) != '' "
            "GROUP BY category_main"
        )
        for r in cur.fetchall():
            cat = (r["category_main"] or "").strip()
            if cat:
                add("category", cat, cat, int(r["n"] or 0))
    except sqlite3.Error as exc:
        log.warning("category_main scan skipped: %s", exc)

    try:
        cur = conn.execute(
            "SELECT DISTINCT j.value AS leaf FROM products p, json_each(p.categories) j "
            "WHERE p.categories IS NOT NULL AND json_valid(p.categories) "
            "AND j.value IS NOT NULL"
        )
        for r in cur.fetchall():
            leaf = (r["leaf"] or "").strip()
            if leaf and is_usable_category(normalise(leaf)):
                add("category", leaf, leaf, 0)
    except sqlite3.Error as exc:
        log.warning("category leaf scan skipped: %s", exc)

    # --- seeds (synonyms + hinglish) --------------------------------------
    for syn, canon in SEED_SYNONYMS.items():
        add("synonym", syn, canon)
    for h, canon in SEED_HINGLISH.items():
        add("hinglish", h, canon)

    # --- flatten to (term, kinds, value, n_items) --------------------------
    flat: dict[str, tuple[str, str, int]] = {}
    for (term, kind), (_t, _k, value, n) in rows.items():
        cur = flat.get(term)
        if cur is None:
            flat[term] = (kind, value, n)
        else:
            kinds = cur[0].split(",")
            if kind not in kinds:
                kinds.append(kind)
            flat[term] = (",".join(kinds), cur[1], max(cur[2], n))
    _persist(conn, [(t, k, v, n) for t, (k, v, n) in flat.items()])
    _invalidate_cache()
    return lex


def _invalidate_cache() -> None:
    with _LOCK:
        _LEX_CACHE.clear()


def _lexicon_rows(conn: sqlite3.Connection) -> int:
    try:
        r = conn.execute("SELECT COUNT(*) AS n FROM lexicon").fetchone()
        return int(r["n"] if r else 0)
    except sqlite3.Error:
        return -1


def load_lexicon(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Load the persisted lexicon; fall back to the seed if the DB is empty.

    Cached per connection; re-checked at most every `LEX_TTL` seconds (the
    ingest rebuilds the table while the server is running).
    """
    key = id(conn)
    now = time.time()
    with _LOCK:
        cached = _LEX_CACHE.get(key)
        if cached is not None and (now - cached[1]) < LEX_TTL:
            return cached[2]
    n_rows = _lexicon_rows(conn)
    with _LOCK:
        cached = _LEX_CACHE.get(key)
        if cached is not None and cached[0] == n_rows:
            _LEX_CACHE[key] = (n_rows, now, cached[2])
            return cached[2]

    lex = _empty_lexicon()
    try:
        cur = conn.execute("SELECT term, kind, value, n_items FROM lexicon")
        for r in cur.fetchall():
            term = normalise(r["term"])
            if not term:
                continue
            kinds = [k.strip() for k in (r["kind"] or "synonym").split(",") if k.strip()]
            value = (r["value"] or r["term"] or "").strip()
            n_items = int(r["n_items"] or 0)
            for kind in kinds:
                if kind == "brand" and n_items < 2 and term not in SEED_BRANDS:
                    # one-off brands are noise; skip (seeds are always kept)
                    continue
                if kind == "brand" and term in BRAND_BLOCKLIST:
                    continue
                if kind == "category" and not is_usable_category(term):
                    continue
                lex.setdefault(kind, {}).setdefault(term, value)
    except sqlite3.Error as exc:
        log.warning("lexicon table missing/unreadable (%s); using seed", exc)
        return _seed_lexicon()

    # Merge the seed on top so offline behaviour is always deterministic.
    # The seed synonym/hinglish mappings win for their terms (they are the
    # canonical concept map); the catalogue only contributes brands/categories.
    seed = _seed_lexicon()
    for kind, mapping in seed.items():
        for term, value in mapping.items():
            if kind in ("synonym", "hinglish"):
                lex.setdefault(kind, {})[term] = value
            else:
                lex.setdefault(kind, {}).setdefault(term, value)

    with _LOCK:
        _LEX_CACHE[key] = (n_rows, now, lex)
    return lex


def default_lexicon() -> dict[str, dict[str, str]]:
    """Lexicon derived from the default DB (best effort, cached, offline-safe).

    Used by `parse_constraints(text)` when no connection is supplied so brand
    detection works for *any* brand in the catalogue, not just the seed list.
    """
    global _DEFAULT_LEX
    with _LOCK:
        if _DEFAULT_LEX is not None:
            return _DEFAULT_LEX
    lex = _seed_lexicon()
    try:
        from app import db  # local import: avoids a circular import at module load

        if os.path.exists(db.DB_PATH):
            conn = db.connect(db.DB_PATH)
            try:
                loaded = load_lexicon(conn)
                if loaded:
                    lex = loaded
            finally:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
    except Exception as exc:  # noqa: BLE001 - never fail on a missing DB
        log.debug("default_lexicon fell back to seed: %s", exc)
    with _LOCK:
        _DEFAULT_LEX = lex
    return lex


def resolve_term(lex: dict[str, dict[str, str]], token: str) -> str:
    """Map a token through synonym/hinglish tables to its canonical concept.

    Returns the canonical concept, or the original token if unknown.
    """
    t = normalise(token)
    if not t:
        return ""
    for kind in ("synonym", "hinglish"):
        if t in lex.get(kind, {}):
            return lex[kind][t]
    return t


def category_aliases(lex: dict[str, dict[str, str]], category: str | None) -> list[str]:
    """All surface forms that may denote `category` (for a relaxed SQL filter)."""
    if not category:
        return []
    cat = normalise(category)
    aliases = {cat}
    for kind in ("synonym", "category"):
        for term, value in (lex.get(kind) or {}).items():
            if normalise(value) == cat and term not in aliases:
                aliases.add(term)
    return sorted(aliases)


# --------------------------------------------------------------------------
# Attribute surface forms.  Used to decide whether a *product* actually has
# the facet the user asked for ("noise cancelling" -> title says "ANC").
# --------------------------------------------------------------------------
ATTRIBUTE_ALIASES: dict[str, list[str]] = {
    "noise cancelling": ["noise cancelling", "noise cancellation", "active noise",
                         "noise cancel", "anc", "btnc", "anc enabled"],
    "noise cancellation": ["noise cancelling", "noise cancellation", "active noise",
                           "noise cancel", "anc", "btnc"],
    "anc": ["anc", "active noise", "noise cancelling", "noise cancellation"],
    "wireless": ["wireless", "cordless", "bluetooth", "wi fi", "wifi"],
    "bluetooth": ["bluetooth", "bt speaker"],
    "gaming": ["gaming", "gamer", "game", "rgb", "rtx", "graphics card"],
    "4k": ["4k", "uhd", "2160p", "3840x2160"],
    "full hd": ["full hd", "1080p", "fhd"],
    "1080p": ["1080p", "full hd", "fhd"],
    "fast charging": ["fast charging", "fast charge", "quick charge", "quickcharging"],
    "waterproof": ["waterproof", "water resistant", "water proof", "ipx"],
    "dolby": ["dolby", "dolby atmos", "dts"],
    "hdr": ["hdr", "high dynamic range"],
    "oled": ["oled", "oled tv"],
    "qled": ["qled", "quantum"],
    "touchscreen": ["touch screen", "touchscreen", "touch display"],
    "with mic": ["microphone", "with mic", "mic", "built in mic", "builtin mic"],
    "bass": ["bass", "deep bass", "extra bass"],
    "smart": ["smart", "smart tv", "android", "app"],
    "foldable": ["foldable", "folding"],
    "refurbished": ["refurbished", "renewed", "open box", "openbox"],
    "portable": ["portable", "folding", "travel"],
    "mechanical": ["mechanical", "mechanical keyboard"],
    "fast": ["fast"], "sasta": ["cheap"], "cheap": ["cheap"],
}


def attribute_aliases(attribute: str) -> list[str]:
    """Surface forms (normalised) that indicate a product has `attribute`."""
    a = normalise(attribute)
    if not a:
        return []
    out = [a]
    for key, vals in ATTRIBUTE_ALIASES.items():
        if normalise(key) == a:
            out.extend(normalise(v) for v in vals)
    return list(dict.fromkeys(x for x in out if x))


def _selftest() -> None:
    from app import db  # type: ignore  (same package dir)

    conn = db.connect()
    lex = build_lexicon(conn)
    print("[lexicon] brands=%d categories=%d synonyms=%d hinglish=%d"
          % (len(lex["brand"]), len(lex["category"]),
             len(lex["synonym"]), len(lex["hinglish"])))
    for tok in ("khanai", "mithai", "chappal", "tws", "ac", "mobile", "saste",
                "notebook", "refrigerator", "grinder", "television", "sneaker"):
        print(f"[lexicon]   {tok:12s} -> {resolve_term(lex, tok)}")
    print("[lexicon] selftest OK")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    if "--selftest" in sys.argv:
        _selftest()
