"""Brand + category + Hinglish lexicon for the Aria retrieval core.

The lexicon is derived from the SQLite `products` table (brands, categories)
and merged with a small hand-written seed of synonyms and Hinglish/Indic
mappings.  It is persisted into the `lexicon` table (owned by the orchestrator's
DDL in `app/db.py`) so every module shares one deterministic vocabulary.

Everything here is offline-safe: `load_lexicon` never touches the network and
falls back to the in-memory seed when the DB is empty or the table is missing.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from collections import defaultdict
from typing import Any

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


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


# --------------------------------------------------------------------------
# Hand-written seeds.  Value == canonical term used by the rest of the stack.
# --------------------------------------------------------------------------
SEED_SYNONYMS: dict[str, str] = {
    # laptops / notebooks
    "laptop": "laptop", "laptops": "laptop", "notebook": "laptop",
    "notebooks": "laptop", "ultrabook": "laptop",
    # audio
    "earbuds": "earphones", "earbud": "earphones", "buds": "earphones",
    "tws": "earphones", "earphone": "earphones", "earphones": "earphones",
    "headphone": "headphones", "headphones": "headphones", "headset": "headphones",
    "speaker": "speakers", "speakers": "speakers", "soundbar": "speakers",
    # phones
    "phone": "phone", "phones": "phone", "smartphone": "phone",
    "smartphones": "phone", "cellphone": "phone", "cell phone": "phone",
    "mobile": "phone", "mobiles": "phone",
    # TV
    "tv": "tv", "tvs": "tv", "television": "tv", "televisions": "tv",
    "smart tv": "tv", "oled": "tv", "qled": "tv", "led tv": "tv",
    # appliances
    "fridge": "fridge", "fridges": "fridge", "refrigerator": "fridge",
    "refrigerators": "fridge",
    "washing machine": "washing machine", "washing machines": "washing machine",
    "washer": "washing machine",
    "mixer": "mixer", "grinder": "mixer", "mixie": "mixer",
    "mixer grinder": "mixer", "juicer": "mixer",
    "ac": "ac", "air conditioner": "ac", "airconditioner": "ac",
    "air conditioners": "ac", "airconditioning": "ac",
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
    "chappals": "sandals", "flip flops": "sandals",
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
}

# Hinglish / Indic token -> canonical English concept.  These are used for
# intent/entity extraction (not returned verbatim to users).
SEED_HINGLISH: dict[str, str] = {
    "mithai": "sweets", "mithaai": "sweets", "mithayi": "sweets",
    "sweets": "sweets", "kaju katli": "sweets", "laddu": "sweets",
    "kurti": "kurta", "kurtis": "kurta", "kurta": "kurta", "kurtas": "kurta",
    "chappal": "sandals", "chappals": "sandals",
    "khanai": "earphones",
    "mobile": "phone", "mobiles": "phone",
    "sasta": "cheap", "saste": "cheap", "sasti": "cheap", "cheap": "cheap",
    "mehenga": "expensive", "kam": "under", "se kam": "under",
    "zyada": "above", "se zyada": "above", "kam se": "under",
    "gift": "gift", "uphaar": "gift", "tohfa": "gift", "topha": "gift",
    "bhai": "brother", "bhaiya": "brother", "bhaai": "brother",
    "behen": "sister", "behan": "sister", "didi": "sister",
    "maa": "mother", "mummy": "mother", "mummy": "mother", "mom": "mother",
    "papa": "father", "dad": "father", "daddy": "father",
    "mere": "my", "mera": "my", "meri": "my", "liye": "for", "ke liye": "for",
    "wala": "for", "wali": "for", "ke": "of", "ka": "of", "ki": "of",
    "accha": "good", "achha": "good", "badhiya": "good", "best": "best",
    "dikhao": "show", "dikha": "show", "dekh": "see", "batao": "tell",
    "kitna": "how much", "rate": "price", "daam": "price",
    "chahiye": "want", "chahie": "want", "lena": "buy", "kharid": "buy",
    "do": "give", "dhoondho": "find", "batana": "tell",
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
]


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
        out["category"][normalise(canon)] = normalise(canon)
    for h, canon in SEED_HINGLISH.items():
        out["hinglish"][normalise(h)] = normalise(canon)
    return out


def _persist(conn: sqlite3.Connection, rows: list[tuple[str, str, str, int]]) -> None:
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
        {"brand": {norm: canonical}, "category": {norm: canonical},
         "synonym": {norm: canonical}, "hinglish": {norm: canonical}}
    """
    lex = _seed_lexicon()
    rows: list[tuple[str, str, str, int]] = []

    def add(kind: str, term: str, value: str, n: int = 0) -> None:
        term = normalise(term)
        value = normalise(value)
        if not term or not value:
            return
        lex[kind][term] = value
        rows.append((term, kind, value, n))

    # --- brands from the catalogue (authoritative) -------------------------
    try:
        cur = conn.execute(
            "SELECT brand, COUNT(*) AS n FROM products "
            "WHERE brand IS NOT NULL AND TRIM(brand) != '' GROUP BY brand"
        )
        for r in cur.fetchall():
            brand = normalise(r["brand"])
            if brand:
                lex["brand"][brand] = brand
                rows.append((brand, "brand", brand, r["n"]))
    except sqlite3.Error as exc:
        log.warning("brand scan skipped: %s", exc)

    # --- categories: category_main + leaf tokens from the categories JSON ---
    try:
        cur = conn.execute(
            "SELECT category_main, categories FROM products "
            "WHERE category_main IS NOT NULL AND TRIM(category_main) != ''"
        )
        for r in cur.fetchall():
            cat = normalise(r["category_main"])
            if cat:
                lex["category"][cat] = cat
                rows.append((cat, "category", cat, 0))
            cats = r["categories"]
            if cats:
                try:
                    import json

                    items = json.loads(cats)
                    for leaf in items:
                        leaf_n = normalise(leaf)
                        if leaf_n and len(leaf_n) > 1:
                            lex["category"][leaf_n] = leaf_n
                            rows.append((leaf_n, "category", leaf_n, 0))
                except (ValueError, TypeError):
                    pass
    except sqlite3.Error as exc:
        log.warning("category scan skipped: %s", exc)

    # --- seeds (synonyms + hinglish) --------------------------------------
    for syn, canon in SEED_SYNONYMS.items():
        add("synonym", syn, canon)
    for h, canon in SEED_HINGLISH.items():
        add("hinglish", h, canon)

    _persist(conn, rows)
    return lex


def load_lexicon(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Load the persisted lexicon; fall back to the seed if the DB is empty."""
    lex: dict[str, dict[str, str]] = defaultdict(dict)
    try:
        cur = conn.execute("SELECT term, kind, value FROM lexicon")
        for r in cur.fetchall():
            kind = r["kind"] or "synonym"
            if kind not in lex:
                lex[kind] = {}
            lex[kind][r["term"]] = r["value"]
    except sqlite3.Error as exc:
        log.warning("lexicon table missing/unreadable (%s); using seed", exc)
        return _seed_lexicon()

    # Merge seed on top so offline behaviour is always deterministic.
    seed = _seed_lexicon()
    for kind, mapping in seed.items():
        for term, value in mapping.items():
            lex.setdefault(kind, {})
            lex[kind].setdefault(term, value)
    return dict(lex)


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


def _selftest() -> None:
    from app import db  # type: ignore  (same package dir)

    conn = db.connect()
    lex = build_lexicon(conn)
    print("[lexicon] brands=%d categories=%d synonyms=%d hinglish=%d"
          % (len(lex["brand"]), len(lex["category"]),
             len(lex["synonym"]), len(lex["hinglish"])))
    for tok in ("khanai", "mithai", "chappal", "tws", "ac", "mobile", "saste"):
        print(f"[lexicon]   {tok:10s} -> {resolve_term(lex, tok)}")
    print("[lexicon] selftest OK")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    if "--selftest" in sys.argv:
        _selftest()
