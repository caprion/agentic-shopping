"""Embedding + vector-search layer for the Aria retrieval core.

Uses `BAAI/bge-small-en-v1.5` (384-dim, L2-normalised) for text and
`sentence-transformers/clip-ViT-B-32` as an optional visual encoder.  Vectors
are stored in a LanceDB table named `products` under `demo/data/lance`.  If
LanceDB (or a model) is unavailable, everything degrades to a numpy brute-force
matrix persisted as `.npy` — callers (`app/retrieval.py`) never need to know
which backend is active.

Models are lazy-loaded and cached in module-level singletons.  No import-time
network access.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

TEXT_MODEL_NAME = "BAAI/bge-small-en-v1.5"
CLIP_MODEL_NAME = "sentence-transformers/clip-ViT-B-32"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# --------------------------------------------------------------------------
# module-level singletons (never load at import time)
# --------------------------------------------------------------------------
_TEXT_MODEL: Any = None
_CLIP_MODEL: Any = None
_LANCE_DB: Any = None
_LANCE_TABLE: Any = None

_ALLOWED_COLS = {"product_id", "title", "price", "rating", "brand", "category_main"}


def _lance_dir() -> str:
    # demo/data/lance, sibling of app.db
    from app.db import DB_PATH  # type: ignore

    return os.path.join(os.path.dirname(DB_PATH), "lance")


def get_text_model():
    """Lazy-loaded, cached bge-small-en-v1.5 sentence transformer (or None)."""
    global _TEXT_MODEL
    if _TEXT_MODEL is not None:
        return _TEXT_MODEL
    try:
        from sentence_transformers import SentenceTransformer

        _TEXT_MODEL = SentenceTransformer(TEXT_MODEL_NAME)
        log.info("text model loaded: %s", TEXT_MODEL_NAME)
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
        from sentence_transformers import SentenceTransformer

        _CLIP_MODEL = SentenceTransformer(CLIP_MODEL_NAME)
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
    return os.path.join(d, "products_vectors.npy"), os.path.join(d, "products_ids.npy")


def _load_numpy_vectors() -> tuple[np.ndarray, np.ndarray] | None:
    vp, ip = _npy_paths()
    if os.path.exists(vp) and os.path.exists(ip):
        try:
            return np.load(vp), np.load(ip)
        except Exception as exc:  # noqa: BLE001
            log.warning("numpy vector load failed: %s", exc)
    return None


def _save_numpy_vectors(ids: list[str], vecs: np.ndarray) -> None:
    vp, ip = _npy_paths()
    os.makedirs(os.path.dirname(vp), exist_ok=True)
    np.save(vp, vecs.astype(np.float32))
    np.save(ip, np.asarray(ids, dtype=object))


def _upsert_numpy(ids: list[str], vecs: np.ndarray) -> None:
    existing = _load_numpy_vectors()
    if existing is None:
        _save_numpy_vectors(ids, vecs)
        return
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
    from app import db  # type: ignore

    parts = [row["title"] or "", row["brand"] or "", row["category_main"] or ""]
    desc = row["description"] or ""
    if desc:
        parts.append(desc[:400])
    feats = db.jload(row["features"], [])
    if feats:
        parts.append(" ".join(str(f) for f in feats[:8])[:400])
    return " ".join(p for p in parts if p).strip()


def embed_products(conn: sqlite3.Connection, ids: list[str]) -> None:
    """Encode products and upsert them into the LanceDB `products` table."""
    if not ids:
        return
    ids = list(dict.fromkeys(ids))  # dedupe, keep order
    q = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT product_id,title,brand,category_main,price,rating,description,features "
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
    _upsert_numpy([r["product_id"] for r in rows], vecs)
    log.info("numpy upsert %d vectors", len(rows))


def _where_from_filt(filt: dict | None) -> str | None:
    if not filt:
        return None
    parts = []
    for col, val in filt.items():
        if col not in _ALLOWED_COLS:
            continue
        if isinstance(val, (list, tuple, set)):
            vals = [str(v).replace("'", "''") for v in val]
            parts.append(f"{col} IN ({','.join(repr(v) for v in vals)})")
        elif isinstance(val, tuple) and len(val) == 2 and isinstance(val[0], str):
            parts.append(f"{col} {val[0]} {val[1]!r}")
        else:
            parts.append(f"{col} = {val!r}")
    return " AND ".join(parts) or None


def search_vectors(vec: np.ndarray, k: int = 20,
                   filt: dict | None = None) -> list[tuple[str, float]]:
    """Return top-k `(product_id, similarity)` for a query vector.

    `filt` is an optional dict of column -> value / (op, value) / [values].
    Similarity is in [0, 1] (cosine) regardless of backend.
    """
    if vec is None or k <= 0:
        return []
    table = _get_lance_table()
    where = _where_from_filt(filt)
    if table is not None:
        try:
            q = table.search(vec.tolist()).limit(k)
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
    sims = (matrix @ v.T).ravel()
    order = np.argsort(-sims)[: k * 5]
    out = []
    for i in order:
        out.append((str(ids[i]), float(sims[i])))
    return out[:k]


# --------------------------------------------------------------------------
def _selftest() -> None:
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
    print("[embeddings] selftest OK")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    if "--selftest" in sys.argv:
        _selftest()
