"""Embed the catalogue into the vector store (LanceDB, numpy fallback).

Runs offline in the background: on 2 vCPU the bge-small encoder does ~40 texts/s,
so a 195k-product catalogue takes ~80 minutes. It is incremental: rows already
embedded (tracked in the `embeddings_done` side table) are skipped.

Usage:
    python demo/ingest/build_embeddings.py            # embed everything missing
    python demo/ingest/build_embeddings.py --limit 5000 --batch 128
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import connect  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch", type=int, default=128)
    args = ap.parse_args()

    from app import embeddings

    db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "app.db")
    conn = connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS embeddings_done(product_id TEXT PRIMARY KEY)")
    done = {r[0] for r in conn.execute("SELECT product_id FROM embeddings_done")}
    rows = conn.execute(
        "SELECT product_id FROM products WHERE in_stock=1 AND price IS NOT NULL ORDER BY rating_count DESC"
    ).fetchall()
    ids = [r[0] for r in rows if r[0] not in done]
    if args.limit:
        ids = ids[: args.limit]
    print(f"to embed: {len(ids)} (already done: {len(done)})", flush=True)
    if not ids:
        return
    t0 = time.time()
    CH = 2000
    for i in range(0, len(ids), CH):
        chunk = ids[i : i + CH]
        embeddings.embed_products(conn, chunk)
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings_done(product_id) VALUES(?)", [(x,) for x in chunk]
        )
        conn.commit()
        done_n = i + len(chunk)
        rate = done_n / max(time.time() - t0, 1e-6)
        print(f"  {done_n}/{len(ids)}  {rate:.1f}/s  eta {(len(ids)-done_n)/max(rate,1e-6)/60:.1f} min", flush=True)
    print("done")


if __name__ == "__main__":
    main()
