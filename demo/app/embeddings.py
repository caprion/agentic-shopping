"""Embedding + vector-search layer for the Aria retrieval core.

Uses `BAAI/bge-small-en-v1.5` (384-dim, L2-normalised) for text and
`sentence-transformers/clip-ViT-B-32` as an optional visual encoder.  Vectors
are stored in a LanceDB table named `products` under `demo/data/lance`.  If
LanceDB (or a model) is unavailable, everything degrades to a numpy brute-force
matrix persisted as `.npy` — callers (`app/retrieval.py`) never need to know
which backend is active.

Models are lazy-loaded and cached in module-level singletons.  No import-time
network access.

Performance notes (measured on the 4-vCPU demo box):
  * The encoded text is deliberately SHORT: `title[:140] + brand + category_main
    + description[:160]`.  Long texts (the raw `features` blob) cost ~15x the
    encode time for no retrieval gain.
  * `model.max_seq_length = 128` — bge-small over 128 tokens is plenty for a
    product title and keeps the encoder in the fast path.
  * `torch.set_num_threads(min(4, os.cpu_count()))` so torch uses every core.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

TEXT_MODEL_NAME = "BAAI/bge-small-en-v1.5"
CLIP_MODEL_NAME = "sentence-transformers/clip-ViT-B-32"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

MAX_SEQ_LENGTH = 128
TITLE_CHARS = 140
DESC_CHARS = 160

# --------------------------------------------------------------------------
# module-level singletons (never load at import time)
# --------------------------------------------------------------------------
_TEXT_MODEL: Any = None
_CLIP_MODEL: Any = None
_LANCE_DB: Any = None
_LANCE_TABLE: Any = None
_TORCH_THREADS_SET = False

_ALLOWED_COLS = {"product_id", "title", "price", "rating", "brand", "category_main"}


def _lance_dir() -> str:
    # demo/data/lance, sibling of app.db
    from app.db import DB_PATH  # type: ignore

    return os.path.join(os.path.dirname(DB_PATH), "lance")


def _tune_threads() -> None:
    """Use every available core for torch (idempotent, best effort)."""
    global _TORCH_THREADS_SET
    if _TORCH_THREADS_SET:
        return
    _TORCH_THREADS_SET = True
    try:
        import torch

        n = max(1, min(4, os.cpu_count() or 1))
        torch.set_num_threads(n)
        log.info("torch threads set to %d", n)
    except Exception as exc:  # noqa: BLE001 - torch is optional at runtime
        log.debug("could not set torch threads: %s", exc)


def _load_sentence_transformer(name: str):
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(name)
    try:
        model.max_seq_length = MAX_SEQ_LENGTH
    except Exception as exc:  # noqa: BLE001
        log.debug("could not set max_seq_length on %s: %s", name, exc)
    return model


def get_text_model():
    """Lazy-loaded, cached bge-small-en-v1.5 sentence transformer (or None)."""
    global _TEXT_MODEL
    if _TEXT_MODEL is not None:
        return _TEXT_MODEL
    try:
        _tune_threads()
        _TEXT_MODEL = _load_sentence_transformer(TEXT_MODEL_NAME)
        log.info("text model loaded: %s (max_seq_length=%d)",
                 TEXT_MODEL_NAME, MAX_SEQ_LENGTH)
    except Exception as exc:  # noqa: BLE001 - offline / missing dep
        log.warning("text model unavailable (%s); dense search disabled", exc)
        _TEXT_MODEL = None
    return _TEXT_MODEL


def get_clip_model():
    """Lazy-loaded, cached clip-ViT-B-32 (optional; may be unavailable)."""
    global _CLIP_MODEL
    if _CLIP_MODEL is not None:
        return _CLIP_MODEL
    try:
        _tune_threads()
        _CLIP_MODEL = _load_sentence_transformer(CLIP_MODEL_NAME)
        log.info("clip model loaded: %s", CLIP_MODEL_NAME)
    except Exception as exc:  # noqa: BLE001
        log.warning("clip model unavailable (%s); image search disabled", exc)
        _CLIP_MODEL = None
    return _CLIP_MODEL


def encode_texts(texts: list[str], batch_size: int = 64) -> np.ndarray | None:
    """Encode a list of strings -> (n, 384) float32, L2-normalised.  None if no model."""
    model = get_text_model()
    if model is None:
        return None
    texts = [t or "" for t in texts]
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    vecs = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return np.asarray(vecs, dtype=np.float32)


def encode_query(text: str) -> np.ndarray | None:
    """Encode a query string -> (384,) float32 L2-normalised (bge query prefix)."""
    model = get_text_model()
    if model is None:
        return None
    vec = model.encode(
        [QUERY_PREFIX + (text or "")],
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return np.asarray(vec[0], dtype=np.float32)


# --------------------------------------------------------------------------
# LanceDB table management (with numpy fallback)
# --------------------------------------------------------------------------
def _npy_paths():
    d = _lance_dir()
    return (os.path.join(d, "products_vectors.npy"),
            os.path.join(d, "products_ids.npy"),
            os.path.join(d, "products_meta.jsonl"))


def _load_numpy_vectors() -> tuple[np.ndarray, np.ndarray] | None:
    vp, ip, _ = _npy_paths()
    try:
        if os.path.getsize(vp) < 8 or os.path.getsize(ip) < 2:
            return None
    except OSError:
        return None
    if os.path.exists(vp) and os.path.exists(ip):
        try:
            vecs = np.load(vp)
            ids = np.load(ip, allow_pickle=True)
            if vecs.ndim != 2 or len(vecs) != len(ids):
                return None
            return vecs, ids
        except Exception as exc:  # noqa: BLE001
            log.warning("numpy vector load failed: %s", exc)
    return None


def _save_numpy_vectors(ids: list[str], vecs: np.ndarray) -> None:
    vp, ip, _ = _npy_paths()
    os.makedirs(os.path.dirname(vp), exist_ok=True)
    np.save(vp, vecs.astype(np.float32))
    np.save(ip, np.asarray(ids, dtype=object))


def _append_numpy_meta(records: list[dict]) -> None:
    """Append-only metadata log so the numpy fallback can still filter."""
    if not records:
        return
    _, _, mp = _npy_paths()
    os.makedirs(os.path.dirname(mp), exist_ok=True)
    try:
        with open(mp, "a", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("numpy meta append failed: %s", exc)


def _load_numpy_meta() -> dict[str, dict]:
    _, _, mp = _npy_paths()
    out: dict[str, dict] = {}
    if not os.path.exists(mp):
        return out
    try:
        with open(mp, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                pid = r.get("product_id")
                if pid:
                    out[str(pid)] = r
    except OSError as exc:
        log.warning("numpy meta load failed: %s", exc)
    return out


def _upsert_numpy(ids: list[str], vecs: np.ndarray, meta: list[dict]) -> None:
    existing = _load_numpy_vectors()
    if existing is None:
        _save_numpy_vectors(ids, vecs)
    else:
        old_vecs, old_ids = existing
        idx = {pid: i for i, pid in enumerate(old_ids.tolist())}
        all_ids = list(old_ids.tolist())
        all_vecs = list(old_vecs.astype(np.float32))
        for pid, vec in zip(ids, vecs):
            if pid in idx:
                all_vecs[idx[pid]] = vec.astype(np.float32)
            else:
                all_ids.append(pid)
                all_vecs.append(vec.astype(np.float32))
        _save_numpy_vectors(all_ids, np.asarray(all_vecs, dtype=np.float32))
    _append_numpy_meta(meta)


def _get_lance_table():
    global _LANCE_DB, _LANCE_TABLE
    if _LANCE_TABLE is not None:
        return _LANCE_TABLE
    try:
        import lancedb

        d = _lance_dir()
        os.makedirs(d, exist_ok=True)
        _LANCE_DB = lancedb.connect(d)
        try:
            _LANCE_TABLE = _LANCE_DB.open_table("products")
        except Exception:
            _LANCE_TABLE = None
    except Exception as exc:  # noqa: BLE001
        log.warning("lancedb unavailable (%s); numpy fallback active", exc)
        _LANCE_TABLE = None
    return _LANCE_TABLE


def _product_text(row: sqlite3.Row) -> str:
    """Short, retrieval-friendly text for a product.

    Deliberately short (title[:140] + brand + category + description[:160]):
    long inputs dominate encode time and do not improve recall.
    """
    title = (row["title"] or "")[:TITLE_CHARS]
    brand = (row["brand"] or "")[:40]
    cat = (row["category_main"] or "")[:40]
    desc = (row["description"] or "")[:DESC_CHARS]
    return " ".join(p for p in (title, brand, cat, desc) if p).strip()


def embed_products(conn: sqlite3.Connection, ids: list[str]) -> None:
    """Encode products and upsert them into the LanceDB `products` table."""
    if not ids:
        return
    ids = list(dict.fromkeys(ids))  # dedupe, keep order
    q = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT product_id,title,brand,category_main,price,rating,description "
        f"FROM products WHERE product_id IN ({q})",
        ids,
    ).fetchall()
    if not rows:
        log.warning("embed_products: no rows for %d ids", len(ids))
        return
    texts = [_product_text(r) for r in rows]
    vecs = encode_texts(texts)
    if vecs is None:
        log.warning("embed_products: no text model; storing zero vectors")
        vecs = np.zeros((len(rows), 384), dtype=np.float32)

    recs = []
    meta = []
    for r, vec in zip(rows, vecs):
        recs.append(
            {
                "product_id": r["product_id"],
                "vector": vec.tolist(),
                "title": (r["title"] or "")[:500],
                "price": float(r["price"]) if r["price"] is not None else 0.0,
                "rating": float(r["rating"]) if r["rating"] is not None else 0.0,
                "brand": r["brand"] or "",
                "category_main": r["category_main"] or "",
            }
        )
        meta.append(
            {
                "product_id": r["product_id"],
                "price": float(r["price"]) if r["price"] is not None else None,
                "rating": float(r["rating"]) if r["rating"] is not None else None,
                "brand": r["brand"] or "",
                "category_main": r["category_main"] or "",
            }
        )

    table = _get_lance_table()
    try:
        import pyarrow as pa

        data = pa.Table.from_pylist(recs)
        if table is None and _LANCE_DB is not None:
            # first time — create the table
            _LANCE_TABLE = _LANCE_DB.create_table(
                "products", data, mode="overwrite", metric="cosine"
            )
            log.info("lancedb created products table (%d rows)", len(recs))
            return
        if table is not None:
            table.merge_insert("product_id").when_matched_update_all() \
                .when_not_matched_insert_all().execute(data)
            log.info("lancedb upsert %d vectors", len(recs))
            return
    except Exception as exc:  # noqa: BLE001
        log.warning("lancedb upsert failed (%s); numpy fallback", exc)

    # numpy fallback
    _upsert_numpy([r["product_id"] for r in rows], vecs, meta)
    log.info("numpy upsert %d vectors", len(rows))


# --------------------------------------------------------------------------
# vector search
# --------------------------------------------------------------------------
def _sql_str(v: Any) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _where_from_filt(filt: dict | None) -> str | None:
    """Translate a filter dict into a LanceDB/SQL `where` expression.

    Supported keys: product_id, brand, category_main, price, rating plus
    `price_min`, `price_max`, `rating_min`, `rating_max`.
    """
    if not filt:
        return None
    parts: list[str] = []

    def add_in(col: str, vals) -> None:
        vals = [str(v) for v in vals if v is not None]
        if vals:
            parts.append(f"{col} IN ({','.join(_sql_str(v) for v in vals)})")

    for col in ("product_id", "brand", "category_main"):
        val = filt.get(col)
        if isinstance(val, (list, tuple, set)):
            add_in(col, val)
        elif val is not None:
            parts.append(f"{col} = {_sql_str(val)}")
    if filt.get("price") is not None:
        parts.append(f"price = {float(filt['price'])}")
    if filt.get("price_min") is not None:
        parts.append(f"price >= {float(filt['price_min'])}")
    if filt.get("price_max") is not None:
        parts.append(f"price <= {float(filt['price_max'])}")
    if filt.get("rating") is not None:
        parts.append(f"rating >= {float(filt['rating'])}")
    if filt.get("rating_min") is not None:
        parts.append(f"rating >= {float(filt['rating_min'])}")
    if filt.get("rating_max") is not None:
        parts.append(f"rating <= {float(filt['rating_max'])}")
    return " AND ".join(parts) or None


def _numpy_matches(meta: dict, filt: dict | None) -> bool:
    if not filt:
        return True
    for col in ("product_id", "brand", "category_main"):
        val = filt.get(col)
        if val is None:
            continue
        if isinstance(val, (list, tuple, set)):
            if str(meta.get(col, "")) not in {str(v) for v in val}:
                return False
        elif str(meta.get(col, "")) != str(val):
            return False
    price = meta.get("price")
    if filt.get("price_min") is not None and (price is None or price < float(filt["price_min"])):
        return False
    if filt.get("price_max") is not None and (price is None or price > float(filt["price_max"])):
        return False
    if filt.get("rating_min") is not None:
        r = meta.get("rating")
        if r is None or r < float(filt["rating_min"]):
            return False
    return True


def search_vectors(vec: np.ndarray, k: int = 20,
                   filt: dict | None = None) -> list[tuple[str, float]]:
    """Return top-k `(product_id, similarity)` for a query vector.

    `filt` is an optional dict of column -> value / list / bound
    (e.g. {"price_max": 5000, "brand": ["sony"]}).  Similarity is cosine
    similarity in [-1, 1] regardless of backend.
    """
    if vec is None or k <= 0:
        return []
    where = _where_from_filt(filt)
    table = _get_lance_table()
    if table is not None:
        try:
            q = table.search(np.asarray(vec, dtype=np.float32).tolist()).limit(k)
            if where:
                q = q.where(where)
            res = q.to_list()
            return [(r["product_id"], float(1.0 - r.get("_distance", 0.0))) for r in res]
        except Exception as exc:  # noqa: BLE001
            log.warning("lancedb search failed (%s); numpy fallback", exc)

    # numpy brute-force fallback
    loaded = _load_numpy_vectors()
    if loaded is None:
        return []
    matrix, ids = loaded
    matrix = matrix.astype(np.float32)
    v = np.asarray(vec, dtype=np.float32).reshape(1, -1)
    if matrix.shape[1] != v.shape[1]:
        log.warning("numpy vector dim mismatch (%s vs %s)", matrix.shape[1], v.shape[1])
        return []
    sims = (matrix @ v.T).ravel()
    order = np.argsort(-sims)[: max(k * 5, 50)]
    meta = _load_numpy_meta() if filt else {}
    out: list[tuple[str, float]] = []
    for i in order:
        pid = str(ids[i])
        if filt and meta and not _numpy_matches(meta.get(pid, {}), filt):
            continue
        out.append((pid, float(sims[i])))
        if len(out) >= k:
            break
    return out


# --------------------------------------------------------------------------
def _selftest() -> None:
    import time

    if get_text_model() is None:
        print("[embeddings] text model unavailable — skipping similarity check")
        return
    samples = [
        "wireless noise cancelling headphones",
        "bluetooth earbuds with microphone",
        "4k smart television with HDR",
    ]
    vecs = encode_texts(samples)
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            sim = float(np.dot(vecs[i], vecs[j]))
            print(f"[embeddings] sim({i},{j}) = {sim:.4f}  "
                  f"'{samples[i]}'  vs  '{samples[j]}'")

    # throughput probe on realistic (short) product text
    from app import db  # type: ignore

    conn = db.connect()
    rows = conn.execute(
        "SELECT product_id,title,brand,category_main,price,rating,description "
        "FROM products WHERE price IS NOT NULL LIMIT 64"
    ).fetchall()
    if rows:
        texts = [_product_text(r) for r in rows]
        avg = sum(len(t) for t in texts) / len(texts)
        t0 = time.perf_counter()
        encode_texts(texts, batch_size=64)
        dt = time.perf_counter() - t0
        print(f"[embeddings] avg_text_chars={avg:.0f}  "
              f"throughput={len(texts)/dt:.1f} texts/s ({dt*1000:.0f} ms for {len(texts)})")
    print("[embeddings] selftest OK")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    if "--selftest" in sys.argv:
        _selftest()
