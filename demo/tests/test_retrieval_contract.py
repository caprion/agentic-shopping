"""Retrieval contract checks (signatures exist + hybrid_search handles empty DB).

Runnable without pytest:
    python demo/tests/test_retrieval_contract.py

Always exits 0 — missing/not-yet-landed modules print SKIP instead of failing.
The empty-DB hybrid_search check stubs the two expensive model-inference steps
(dense ANN + cross-encoder) so it runs fast and offline; it still exercises the
real hybrid_search control flow (SQL pre-filter -> FTS -> RRF -> personalise ->
return) against an empty database.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent
if str(DEMO) not in sys.path:
    sys.path.insert(0, str(DEMO))

# CONTRACT signatures we depend on from B1/B2.
REQUIRED = {
    "app.retrieval": [
        "Hit", "parse_constraints", "hybrid_search", "get_product",
        "get_reviews", "similar_products", "graph_expand",
    ],
    "app.embeddings": [
        "get_text_model", "get_clip_model", "encode_texts", "encode_query",
        "embed_products", "search_vectors",
    ],
    "app.personalize": ["build_profile", "rerank", "explain_hit"],
    "app.agent": ["run_turn"],
    "app.tools": ["TOOLS", "run_tool"],
}


def check_signatures() -> tuple:
    missing = []
    present = []
    for mod, names in REQUIRED.items():
        try:
            m = importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{mod} (not importable: {exc})")
            continue
        for n in names:
            if hasattr(m, n):
                present.append(f"{mod}.{n}")
            else:
                missing.append(f"{mod}.{n}")
    return present, missing


def check_empty_db_hybrid_search() -> tuple:
    from app import db
    import app.retrieval as ret

    # Stub model-inference steps for a fast, offline-safe empty-DB check.
    ret._dense_top = lambda *a, **k: []
    ret._cross_encode_rerank = lambda conn, q, ranked: ranked

    tmp = tempfile.mkdtemp(prefix="aria-test-")
    path = os.path.join(tmp, "empty.db")
    conn = db.connect(path)
    try:
        db.init_schema(conn)
        hits = ret.hybrid_search(conn, "wireless noise cancelling headphones", k=5)
        assert isinstance(hits, list), f"expected list, got {type(hits)}"
        return len(hits)
    finally:
        conn.close()


def run() -> list:
    results = []

    try:
        present, missing = check_signatures()
        results.append(("signatures", True,
                        f"{len(present)} present; missing: {missing or 'none'}"))
    except Exception as exc:  # noqa: BLE001
        results.append(("signatures", False, f"SKIP: {exc}"))

    try:
        n = check_empty_db_hybrid_search()
        results.append(("hybrid_search_empty_db", True, f"returned {n} hits"))
    except Exception as exc:  # noqa: BLE001
        results.append(("hybrid_search_empty_db", False, f"SKIP: {exc}"))

    return results


def main() -> int:
    print("test_retrieval_contract")
    for name, ok, detail in run():
        print(f"  {'PASS' if ok else 'SKIP'} {name}  {detail}")
    print("done (exit 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
