"""Build `demo/data/app.db` from the raw open datasets in `demo/data/raw/`.

Stages (each idempotent, runnable alone):
  products     Amazon Reviews-2023 metadata (parquet) -> products + product_variants
  reviews      Amazon Reviews-2023 reviews (jsonl)   -> reviews
  interactions Amazon 5-core ratings (csv)            -> interactions, users, product_edges, popularity
  olist        Olist Brazilian marketplace             -> olist_* tables (REAL transactions/customers)
  eval         Amazon Shopping Queries (ESC parquet)  -> eval_queries (golden retrieval set)
  fts          rebuild the FTS5 index
  lexicon      derive brand/category lexicon

Usage:
    python demo/ingest/build_db.py --stage all
    python demo/ingest/build_db.py --stage products --limit 40000
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import connect, init_schema, jdump, now, rebuild_fts  # noqa: E402

RAW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw")

# --------------------------------------------------------------------------- helpers

STORE_SUFFIXES = ("store", "amazon", "official", "official store", "inc", "llc", "ltd", "india", "co")
COLOR_SIZE_RE = re.compile(
    r"\b(black|white|silver|gold|blue|red|green|pink|grey|gray|navy|beige|rose|gold|"
    r"[\d]{1,3}\s?(gb|tb|mb|kg|g|ml|l|inch|inches|cm|mm|pack|pcs|piece)\b)",
    re.I,
)


def num(v, default=None):
    """Dataset values come as floats, ints, or the literal string 'None'."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() in {"none", "nan", "null"}:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def norm_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s[:20000]


def brand_from(title: str, store: str | None, categories: list[str]) -> str | None:
    """Brand = the leading token(s) of the title, validated against the store name."""
    if store:
        s = store.strip()
        if 2 <= len(s) <= 40:
            return s
    if not title:
        return None
    head = title.split()[0]
    if len(head) > 1 and head.isalpha():
        return head.capitalize()
    return None


def facets(title: str) -> dict:
    out = {}
    for m in COLOR_SIZE_RE.finditer(title or ""):
        v = m.group(1).strip().lower()
        if v.isdigit() or re.match(r"^\d+\s?(gb|tb|mb)$", v):
            out.setdefault("capacity", v)
        elif re.match(r"^\d+\s?(kg|g|ml|l)$", v):
            out.setdefault("capacity", v)
        elif re.match(r"^\d+\s?(inch|inches|cm|mm)$", v):
            out.setdefault("size", v)
        else:
            out.setdefault("color", v)
    return out


def clean_brand(b: str | None) -> str | None:
    if not b:
        return None
    b = b.strip(" -,")
    low = b.lower()
    for suf in STORE_SUFFIXES:
        if low.endswith(" " + suf):
            b = b[: -(len(suf) + 1)].strip()
            break
    return b or None


# --------------------------------------------------------------------------- stages

def stage_products(conn, limit: int | None = None) -> int:
    """Amazon Reviews-2023 metadata -> products (entity = parent_asin) + variants (asin)."""
    import pyarrow.parquet as pq

    files = [os.path.join(RAW, "meta_electronics_0.parquet")]
    prods, variants, seen = {}, {}, set()
    for path in files:
        if not os.path.exists(path):
            print(f"  ! missing {path}")
            continue
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=4096):
            for row in batch.to_pylist():
                pid = row.get("parent_asin")
                if not pid:
                    continue
                if pid in seen:
                    continue
                title = norm_text(row.get("title"))
                if not title:
                    continue
                price = num(row.get("price"))
                rating = num(row.get("average_rating"))
                rcount = int(num(row.get("rating_number"), 0) or 0)
                if price is None and (not rcount or rcount < 3):
                    continue
                seen.add(pid)
                imgs = row.get("images") or {}
                img = None
                if isinstance(imgs, dict):
                    for key in ("hi_res", "large", "thumb"):
                        lst = imgs.get(key) or []
                        if lst and lst[0]:
                            img = lst[0]
                            break
                elif isinstance(imgs, list):
                    for c in imgs:
                        if isinstance(c, dict):
                            for key in ("hi_res", "large", "thumb"):
                                lst = c.get(key) or []
                                if lst and lst[0]:
                                    img = lst[0]
                                    break
                        elif isinstance(c, str):
                            img = c
                            break
                        if img:
                            break
                cats = [c for c in (row.get("categories") or []) if c]
                desc_list = row.get("description") or []
                desc = norm_text(" ".join(desc_list[:3]))[:4000] if isinstance(desc_list, list) else norm_text(desc_list)
                feats = [norm_text(f)[:500] for f in (row.get("features") or [])][:12]
                store = row.get("store")
                prods[pid] = (
                    pid,
                    title,
                    clean_brand(brand_from(title, store, cats)),
                    row.get("main_category") or (cats[0] if cats else None),
                    jdump(cats),
                    price,
                    price,  # no real MRR in this dataset -> list == sell price
                    rating,
                    int(rcount or 0),
                    desc,
                    jdump(feats),
                    img,
                    store,
                    1 if price is not None else 0,
                    0.0,
                    0.0,
                    now(),
                )
                variants[(pid, img)] = (pid, img)
            if limit and len(prods) >= limit:
                break
        if limit and len(prods) >= limit:
            break

    conn.executemany(
        "INSERT OR REPLACE INTO products(product_id,title,brand,category_main,categories,price,"
        "price_mrp,rating,rating_count,description,features,image_url,store,in_stock,discount,"
        "popularity,first_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        list(prods.values()),
    )
    conn.commit()
    print(f"  products: {len(prods)}")
    return len(prods)


def stage_products_jsonl(conn, limit: int | None = None) -> int:
    """Additional product categories shipped as jsonl metadata (Musical Instruments, Industrial)."""
    files = [
        os.path.join(RAW, "meta_Musical_Instruments_subset.jsonl"),
        os.path.join(RAW, "meta_Industrial_and_Scientific_subset.jsonl"),
    ]
    prods = {}
    for path in files:
        if not os.path.exists(path):
            continue
        with open(path, errors="ignore") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pid = row.get("parent_asin")
                if not pid or pid in prods:
                    continue
                title = norm_text(row.get("title"))
                price = num(row.get("price"))
                rating = num(row.get("average_rating"))
                rcount = int(num(row.get("rating_number"), 0) or 0)
                if not title or (price is None and (not rcount or rcount < 3)):
                    continue
                imgs = row.get("images") or {}
                img = None
                if isinstance(imgs, dict):
                    for key in ("hi_res", "large", "thumb"):
                        lst = imgs.get(key) or []
                        if lst and lst[0]:
                            img = lst[0]
                            break
                elif isinstance(imgs, list):
                    for c in imgs:
                        if isinstance(c, dict):
                            for key in ("hi_res", "large", "thumb"):
                                lst = c.get(key) or []
                                if lst and lst[0]:
                                    img = lst[0]
                                    break
                        elif isinstance(c, str):
                            img = c
                            break
                        if img:
                            break
                cats = [c for c in (row.get("categories") or []) if c]
                desc_list = row.get("description") or []
                desc = norm_text(" ".join(desc_list[:3]))[:4000] if isinstance(desc_list, list) else norm_text(desc_list)
                feats = [norm_text(x)[:500] for x in (row.get("features") or [])][:12]
                prods[pid] = (
                    pid, title, clean_brand(brand_from(title, row.get("store"), cats)),
                    row.get("main_category") or (cats[0] if cats else None), jdump(cats),
                    price, price, rating, rcount, desc, jdump(feats), img, row.get("store"),
                    1 if price is not None else 0, 0.0, 0.0, now(),
                )
                if limit and len(prods) >= limit:
                    break
    conn.executemany(
        "INSERT OR REPLACE INTO products(product_id,title,brand,category_main,categories,price,"
        "price_mrp,rating,rating_count,description,features,image_url,store,in_stock,discount,"
        "popularity,first_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        list(prods.values()),
    )
    conn.commit()
    print(f"  products(jsonl categories): {len(prods)}")
    return len(prods)


def stage_variants(conn, limit: int | None = None) -> int:
    """Variants (asin-level) come from the raw jsonl metadata of the second category."""
    path = os.path.join(RAW, "meta_Musical_Instruments_subset.jsonl")
    rows = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pid = d.get("parent_asin")
                if not pid or not conn.execute(
                    "SELECT 1 FROM products WHERE product_id=?", (pid,)
                ).fetchone():
                    continue
                imgs = d.get("images") or {}
                img = None
                for key in ("hi_res", "large", "thumb"):
                    lst = imgs.get(key) or []
                    if lst and lst[0]:
                        img = lst[0]
                        break
                rows.append(
                    (
                        d.get("asin") or pid,
                        pid,
                        norm_text(d.get("title"))[:300],
                        num(d.get("price")),
                        img,
                        jdump(facets(d.get("title") or "")),
                    )
                )
    conn.executemany(
        "INSERT OR REPLACE INTO product_variants(variant_id,product_id,title,price,image_url,attributes)"
        " VALUES(?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    print(f"  variants: {len(rows)}")
    return len(rows)


def stage_reviews(conn, limit: int | None = None) -> int:
    """Amazon reviews -> reviews table (only for products we actually have)."""
    path = os.path.join(RAW, "reviews_electronics_part.jsonl")
    if not os.path.exists(path):
        print("  ! no reviews file")
        return 0
    have = {r[0] for r in conn.execute("SELECT product_id FROM products")}
    buf, total, bad = [], 0, 0
    with open(path, errors="ignore") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            pid = d.get("parent_asin") or d.get("asin")
            if pid not in have:
                continue
            text = norm_text(d.get("text"))[:4000]
            if not text:
                continue
            buf.append(
                (
                    pid,
                    d.get("user_id"),
                    float(d["rating"]) if d.get("rating") is not None else None,
                    norm_text(d.get("title"))[:300],
                    text,
                    (d.get("timestamp") or 0) / 1000.0,
                    int(d.get("helpful_vote") or 0),
                )
            )
            if len(buf) >= 20000:
                conn.executemany(
                    "INSERT INTO reviews(product_id,user_id,rating,title,text,ts,helpful)"
                    " VALUES(?,?,?,?,?,?,?)",
                    buf,
                )
                conn.commit()
                total += len(buf)
                buf = []
                if limit and total >= limit:
                    break
    if buf:
        conn.executemany(
            "INSERT INTO reviews(product_id,user_id,rating,title,text,ts,helpful) VALUES(?,?,?,?,?,?,?)",
            buf,
        )
        conn.commit()
        total += len(buf)
    print(f"  reviews: {total} (unparsable lines skipped: {bad})")
    return total


def stage_interactions(conn, limit: int | None = None) -> int:
    """5-core ratings -> interactions + users + co-purchase graph + popularity."""
    path = os.path.join(RAW, "ratings_electronics.csv")
    if not os.path.exists(path):
        print("  ! no ratings file")
        return 0
    have = {r[0] for r in conn.execute("SELECT product_id FROM products")}
    inter, user_stats = [], defaultdict(lambda: [0, 0.0, 0.0, 0])
    n = 0
    with open(path, errors="ignore") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 4:
                continue
            uid, pid, rating, ts = row[0], row[1], row[2], row[3]
            if pid not in have:
                continue
            try:
                r = float(rating)
                t = int(ts) / 1000.0
            except ValueError:
                continue
            inter.append((uid, pid, r, t))
            st = user_stats[uid]
            st[0] += 1
            st[1] += r
            st[2] = max(st[2], t)
            n += 1
            if limit and n >= limit:
                break
    conn.executemany("INSERT INTO interactions(user_id,product_id,rating,ts) VALUES(?,?,?,?)", inter)
    conn.executemany(
        "INSERT OR REPLACE INTO users(user_id,n_events,avg_spend,last_ts,created_ts) VALUES(?,?,?,?,?)",
        [
            (u, s[0], round(s[1] / s[0], 3) if s[0] else 0.0, s[2], now())
            for u, s in user_stats.items()
        ],
    )
    # popularity = interaction count per product
    pop = Counter(p for _, p, _, _ in inter)
    conn.executemany(
        "UPDATE products SET popularity=? WHERE product_id=?",
        [(float(c), p) for p, c in pop.items()],
    )
    # co-purchase graph from each user's basket
    by_user = defaultdict(list)
    for uid, pid, _, _ in inter:
        by_user[uid].append(pid)
    edges = Counter()
    for uid, ps in by_user.items():
        ps = list(dict.fromkeys(ps))
        if len(ps) > 12:
            ps = ps[:12]
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                edges[(ps[i], ps[j])] += 1
    conn.executemany(
        "INSERT INTO product_edges(a,b,kind,weight) VALUES(?,?,?,?)",
        [(a, b, "co_purchase", float(w)) for (a, b), w in edges.most_common(400000)],
    )
    conn.commit()
    print(f"  interactions: {len(inter)}  users: {len(user_stats)}  edges: {len(edges)}")
    return len(inter)


def stage_olist(conn, limit: int | None = None) -> int:
    """Olist (Brazilian marketplace) — REAL customers, orders, payments, reviews, sellers."""
    import csv as _csv

    d = os.path.join(RAW, "olist")
    if not os.path.isdir(d):
        print("  ! no olist dir")
        return 0
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS olist_customers(
            customer_id TEXT PRIMARY KEY, unique_id TEXT, city TEXT, state TEXT, zip TEXT,
            n_orders INTEGER, avg_spend REAL, total_spent REAL, top_categories TEXT, last_order_ts REAL);
        CREATE TABLE IF NOT EXISTS olist_orders(
            order_id TEXT PRIMARY KEY, customer_id TEXT, status TEXT, purchase_ts REAL,
            approved_ts REAL, delivered_ts REAL, estimated_days REAL, actual_days REAL,
            n_items INTEGER, total REAL, payment_type TEXT, payment_value REAL, n_installments INTEGER,
            review_score REAL, review_comment TEXT);
        CREATE TABLE IF NOT EXISTS olist_order_items(
            order_id TEXT, product_id TEXT, seller_id TEXT, product_category TEXT,
            item_price REAL, shipping_price REAL, qty INTEGER);
        CREATE TABLE IF NOT EXISTS olist_sellers(seller_id TEXT PRIMARY KEY, city TEXT, state TEXT, n_orders INTEGER);
        """
    )
    pay_by_order = {}
    try:
        with open(os.path.join(d, "olist_order_payments_dataset.csv")) as f:
            for row in _csv.DictReader(f):
                oid = row["order_id"]
                cur = pay_by_order.get(oid)
                val = num(row.get("payment_value"), 0.0)
                if cur is None or val > cur[2]:
                    pay_by_order[oid] = (row.get("payment_type"), int(num(row.get("payment_installments"), 0) or 0), val)
    except OSError:
        pass
    rev_by_order = {}
    try:
        with open(os.path.join(d, "olist_order_reviews_dataset.csv")) as f:
            for row in _csv.DictReader(f):
                rev_by_order[row["order_id"]] = (
                    num(row.get("review_score")),
                    (row.get("review_comment_message") or row.get("review_comment_title") or "")[:1500],
                )
    except OSError:
        pass
    items_by_order = defaultdict(list)
    try:
        with open(os.path.join(d, "olist_order_items_dataset.csv")) as f:
            for row in _csv.DictReader(f):
                items_by_order[row["order_id"]].append(
                    (
                        row["product_id"],
                        row.get("seller_id"),
                        row.get("product_category_name"),
                        num(row.get("price"), 0.0),
                        num(row.get("freight_value"), num(row.get("shipping_cost"), 0.0)),
                        int(num(row.get("order_item_qty"), 1) or 1),
                    )
                )
    except OSError:
        pass
    cust = {}
    try:
        with open(os.path.join(d, "olist_customers_dataset.csv")) as f:
            for row in _csv.DictReader(f):
                cust[row["customer_id"]] = (
                    row.get("customer_unique_id"), row.get("customer_city"), row.get("customer_state"),
                    row.get("customer_zip_code_prefix"),
                )
    except OSError:
        pass
    from datetime import datetime

    def ts(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            return None

    orders, cust_stats = [], defaultdict(lambda: {"n": 0, "spent": 0.0, "cats": Counter(), "last": 0.0})
    with open(os.path.join(d, "olist_orders_dataset.csv")) as f:
        for row in _csv.DictReader(f):
            oid = row["order_id"]
            items = items_by_order.get(oid, [])
            total = sum(i[3] * i[5] for i in items)
            pt, pi, pv = pay_by_order.get(oid, (None, 0, total))
            rs, rc = rev_by_order.get(oid, (None, None))
            est = row.get("estimated_delivery_date")
            eta = None
            if est:
                try:
                    e = datetime.strptime(est, "%Y-%m-%d").timestamp()
                    p = ts(row["order_purchase_timestamp"]) or 0
                    eta = (e - p) / 86400.0
                except Exception:
                    eta = None
            act = None
            delivered = row.get("order_delivered_customer_timestamp") or row.get("order_delivered_customer_date")
            if delivered:
                p = ts(row["order_purchase_timestamp"]) or 0
                act = (ts(delivered) - p) / 86400.0
            orders.append(
                (
                    oid, row["customer_id"], row.get("order_status"),
                    ts(row["order_purchase_timestamp"]), ts(row.get("order_approved_at")),
                    ts(delivered),
                    eta, act, len(items), total, pt, pv, pi, rs, rc,
                )
            )
            st = cust_stats[row["customer_id"]]
            st["n"] += 1
            st["spent"] += total
            st["last"] = max(st["last"], ts(row["order_purchase_timestamp"]) or 0)
            for i in items:
                if i[2]:
                    st["cats"][i[2]] += 1
    conn.executemany(
        "INSERT OR REPLACE INTO olist_orders(order_id,customer_id,status,purchase_ts,approved_ts,"
        "delivered_ts,estimated_days,actual_days,n_items,total,payment_type,payment_value,"
        "n_installments,review_score,review_comment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        orders,
    )
    conn.executemany(
        "INSERT OR REPLACE INTO olist_order_items(order_id,product_id,seller_id,product_category,"
        "item_price,shipping_price,qty) VALUES(?,?,?,?,?,?,?)",
        [(o[0],) + i for o in orders for i in items_by_order.get(o[0], [])],
    )
    crows = []
    for cid, (uid, city, state, zipc) in cust.items():
        st = cust_stats.get(cid)
        if not st:
            continue
        crows.append(
            (
                cid, uid, city, state, zipc, st["n"], round(st["spent"] / st["n"], 2), round(st["spent"], 2),
                jdump([c for c, _ in st["cats"].most_common(5)]), st["last"],
            )
        )
    conn.executemany(
        "INSERT OR REPLACE INTO olist_customers(customer_id,unique_id,city,state,zip,n_orders,"
        "avg_spend,total_spent,top_categories,last_order_ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
        crows,
    )
    conn.commit()
    print(f"  olist: orders={len(orders)} customers={len(crows)}")
    return len(orders)


def stage_eval(conn, limit: int | None = 5000) -> int:
    """Amazon Shopping Queries (ESC) -> eval_queries, restricted to products we have."""
    import pyarrow.parquet as pq

    path = os.path.join(RAW, "esci_examples.parquet")
    if not os.path.exists(path):
        print("  ! no esci file")
        return 0
    have = {r[0] for r in conn.execute("SELECT product_id FROM products")}
    tbl = pq.read_table(path)
    cols = {c: tbl.column(c).to_pylist() for c in ("query", "query_id", "product_id", "esci_label", "split")}
    rows = []
    for i in range(tbl.num_rows):
        pid = cols["product_id"][i]
        if pid not in have:
            continue
        rows.append(
            (
                cols["query_id"][i],
                (cols["query"][i] or "").strip(),
                pid,
                cols["esci_label"][i],
                cols["split"][i],
            )
        )
        if limit and len(rows) >= limit * 8:
            break
    conn.executemany("INSERT OR REPLACE INTO eval_queries(query_id,query,product_id,label,split) VALUES(?,?,?,?,?)", rows)
    conn.commit()
    nq = len({r[0] for r in rows})
    print(f"  eval_queries: {len(rows)} rows / {nq} queries (catalogue overlap)")
    return len(rows)


def stage_lexicon(conn, limit: int | None = None) -> int:
    brands = Counter()
    cats = Counter()
    for brand, cat in conn.execute("SELECT brand, category_main FROM products"):
        if brand:
            brands[brand] += 1
        if cat:
            cats[cat] += 1
    rows = [(b.lower(), "brand", b, c) for b, c in brands.most_common(4000) if c >= 3]
    rows += [(c.lower(), "category", c, n) for c, n in cats.most_common(2000) if n >= 5]
    conn.executemany("INSERT OR REPLACE INTO lexicon(term,kind,value,n_items) VALUES(?,?,?,?)", rows)
    conn.commit()
    print(f"  lexicon: {len(rows)} terms")
    return len(rows)


def stage_fts(conn, limit: int | None = None) -> int:
    n = rebuild_fts(conn)
    print(f"  fts: {n} rows indexed")
    return n


STAGES = {
    "products": stage_products,
    "products_jsonl": stage_products_jsonl,
    "variants": stage_variants,
    "reviews": stage_reviews,
    "interactions": stage_interactions,
    "olist": stage_olist,
    "eval": stage_eval,
    "lexicon": stage_lexicon,
    "fts": stage_fts,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "app.db")
    conn = connect(db)
    init_schema(conn)
    order = ["products", "products_jsonl", "variants", "reviews", "interactions", "olist", "eval", "lexicon", "fts"]
    if args.stage != "all":
        order = [s.strip() for s in args.stage.split(",")]
    for name in order:
        fn = STAGES.get(name)
        if not fn:
            print(f"unknown stage {name}")
            continue
        t0 = time.time()
        print(f"[{name}]")
        fn(conn, args.limit)
        print(f"  ({time.time() - t0:.1f}s)")
    conn.close()


if __name__ == "__main__":
    main()
