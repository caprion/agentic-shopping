"""SQLite schema + helpers for the Aria agentic-shopping prototype.

Single source of truth for the data model. Both the ingestion pipeline and the
serving layer import from here. SQLite + FTS5 in WAL mode; vector embeddings
live in LanceDB (see app/embeddings.py).

All money is stored in INR (integer paise-free rupees as REAL) to keep the demo
India-flavoured. `price_mrp` is the "list price" used for discount computation.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Iterable

DB_PATH = os.environ.get(
    "ARIA_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "app.db"),
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- ---------------------------------------------------------------- catalogue
CREATE TABLE IF NOT EXISTS products (
    product_id     TEXT PRIMARY KEY,      -- parent_asin (stable entity id)
    title          TEXT NOT NULL,
    brand          TEXT,
    category_main  TEXT,
    categories     TEXT,                  -- JSON list
    price          REAL,
    price_mrp      REAL,
    rating         REAL,
    rating_count   INTEGER,
    description    TEXT,
    features       TEXT,                  -- JSON list
    image_url      TEXT,
    store          TEXT,
    in_stock       INTEGER DEFAULT 1,
    discount       REAL,                  -- 1 - price/price_mrp
    popularity     REAL DEFAULT 0,
    first_seen     REAL
);
CREATE INDEX IF NOT EXISTS idx_products_brand   ON products(brand);
CREATE INDEX IF NOT EXISTS idx_products_cat     ON products(category_main);
CREATE INDEX IF NOT EXISTS idx_products_price   ON products(price);
CREATE INDEX IF NOT EXISTS idx_products_rating  ON products(rating);
CREATE INDEX IF NOT EXISTS idx_products_pop     ON products(popularity);

CREATE TABLE IF NOT EXISTS product_variants (
    variant_id  TEXT PRIMARY KEY,          -- asin
    product_id  TEXT NOT NULL REFERENCES products(product_id),
    title       TEXT,
    price       REAL,
    image_url   TEXT,
    attributes  TEXT,                     -- JSON dict (colour, size, storage...)
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
CREATE INDEX IF NOT EXISTS idx_variants_product ON product_variants(product_id);

-- ------------------------------------------------------------------ reviews
CREATE TABLE IF NOT EXISTS reviews (
    review_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL,
    user_id    TEXT,
    rating     REAL,
    title      TEXT,
    text       TEXT,
    ts         REAL,
    helpful    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id);
CREATE INDEX IF NOT EXISTS idx_reviews_user    ON reviews(user_id);

-- --------------------------------------------------- users & interactions
CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    n_events    INTEGER DEFAULT 0,
    avg_spend   REAL DEFAULT 0,
    last_ts     REAL,
    created_ts  REAL
);

CREATE TABLE IF NOT EXISTS interactions (
    user_id    TEXT NOT NULL,
    product_id TEXT NOT NULL,
    rating     REAL,
    ts         REAL
);
CREATE INDEX IF NOT EXISTS idx_inter_user  ON interactions(user_id);
CREATE INDEX IF NOT EXISTS idx_inter_prod  ON interactions(product_id);

-- ----------------------------------------------------------- product graph
CREATE TABLE IF NOT EXISTS product_edges (
    a       TEXT NOT NULL,
    b       TEXT NOT NULL,
    kind    TEXT NOT NULL,                 -- co_purchase | co_view | also_bought
    weight  REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_edges_a ON product_edges(a);
CREATE INDEX IF NOT EXISTS idx_edges_b ON product_edges(b);

-- ------------------------------------------------------- session & commerce
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    user_id      TEXT,
    state        TEXT,                     -- JSON: constraints, cart, memory
    created_ts   REAL,
    updated_ts   REAL
);

CREATE TABLE IF NOT EXISTS carts (
    cart_id    TEXT PRIMARY KEY,
    session_id TEXT,
    user_id    TEXT,
    status     TEXT DEFAULT 'open',        -- open | checked_out | abandoned
    created_ts REAL,
    updated_ts REAL
);

CREATE TABLE IF NOT EXISTS cart_items (
    cart_id    TEXT,
    product_id TEXT,
    qty        INTEGER DEFAULT 1,
    unit_price REAL,
    added_ts   REAL
);
CREATE INDEX IF NOT EXISTS idx_cart_items ON cart_items(cart_id);

CREATE TABLE IF NOT EXISTS orders (
    order_id   TEXT PRIMARY KEY,
    session_id TEXT,
    user_id    TEXT,
    total      REAL,
    status     TEXT DEFAULT 'confirmed',
    ship_to    TEXT,
    eta_days   INTEGER,
    created_ts REAL
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id   TEXT,
    product_id TEXT,
    qty        INTEGER,
    unit_price REAL
);

-- ------------------------------------------------------------------ traces
CREATE TABLE IF NOT EXISTS traces (
    trace_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    turn_id     TEXT,
    ts          REAL,
    step        TEXT,                      -- llm | tool | guardrail | done
    tool        TEXT,
    tool_args   TEXT,
    result      TEXT,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    latency_ms  INTEGER,
    model       TEXT,
    citations   TEXT,
    flags       TEXT
);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);

-- ------------------------------------------------------------------ lexicon
CREATE TABLE IF NOT EXISTS lexicon (
    term      TEXT PRIMARY KEY,            -- normalised token, e.g. "wireless earbuds"
    kind      TEXT,                        -- brand | category | attribute | facet
    value     TEXT,
    n_items   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS eval_queries (
    query_id   INTEGER,
    query      TEXT,
    product_id TEXT,
    label      TEXT,                       -- E | S | C | I
    split      TEXT,
    PRIMARY KEY (query_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_eval_split ON eval_queries(split);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def fts_schema(conn: sqlite3.Connection) -> None:
    """FTS5 index over catalogue text (bm25 built in)."""
    conn.executescript(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
            product_id UNINDEXED,
            title,
            brand,
            category_main,
            description,
            features,
            tokenize='porter unicode61'
        );
        """
    )
    conn.commit()


def rebuild_fts(conn: sqlite3.Connection) -> int:
    """Rebuild the FTS index from products. Returns row count."""
    fts_schema(conn)
    conn.execute("DELETE FROM products_fts")
    cur = conn.execute(
        "SELECT product_id, title, brand, category_main, description, features FROM products"
    )
    rows = cur.fetchall()
    conn.executemany(
        "INSERT INTO products_fts(product_id,title,brand,category_main,description,features)"
        " VALUES(?,?,?,?,?,?)",
        [(r["product_id"], r["title"] or "", r["brand"] or "", r["category_main"] or "",
          r["description"] or "", r["features"] or "") for r in rows],
    )
    conn.commit()
    return len(rows)


def now() -> float:
    return time.time()


def jdump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def jload(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


def product_row(conn: sqlite3.Connection, product_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()


def product_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    d["categories"] = jload(d.get("categories"), [])
    d["features"] = jload(d.get("features"), [])
    d["discount"] = d.get("discount") or (
        round(1 - d["price"] / d["price_mrp"], 4)
        if d.get("price") and d.get("price_mrp") and d["price_mrp"] > d["price"]
        else 0.0
    )
    return d


def chunked(it: Iterable, n: int):
    buf = []
    for x in it:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf
