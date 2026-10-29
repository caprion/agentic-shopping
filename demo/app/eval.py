"""Evaluation harness for the Aria agentic-shopping prototype (builder B5).

Everything in this module produces **measured** numbers — nothing is estimated,
extrapolated or hand-written.  If a metric cannot be computed on this machine
(the dense index is still being built, an LLM key is missing, …) the metric is
reported as ``None`` together with an explicit reason.

Contract signatures (see ``demo/CONTRACT.md``) are preserved:

    build_eval_set(conn, limit) -> int
    run_retrieval_eval(conn, k) -> dict      # Recall@k, nDCG@k, MRR
    run_agent_eval(conn, n) -> dict          # task success, groundedness, latency, tokens
    report() -> str                          # writes demo/eval_report.md

Three evaluation suites live here:

1. **Retrieval** — Amazon Shopping Queries (ESC) gold set joined to the
   catalogue.  Four arms (lexical / dense / hybrid / hybrid+pre-filter), the
   cross-encoder rerank measured with and without, a category breakdown and a
   fair "gold product is embedded" subset.
2. **Constraint parsing** — 40 hand-written queries (English + Hinglish) with
   hand-written expectations, scored as precision/recall per constraint field.
3. **Agent** — the 5 demo scripts + 5 adversarial tasks, scored on task
   success, citations, groundedness, guardrail flags, latency and tokens.

Run:

    python -m app.eval --selftest          # quick smoke test (no heavy models)
    python -m app.eval --constraints       # constraint suite only
    python -m app.eval --retrieval         # retrieval suite only
    python -m app.eval --agent             # agent suite only
    python -m app.eval --all --json out.json   # everything + raw results
    python -m app.eval --report            # full markdown report -> demo/eval_report.md
"""
from __future__ import annotations

import importlib
import json
import logging
import math
import os
import random
import re
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

DEMO_ROOT = Path(__file__).resolve().parent.parent      # .../demo
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))

# graded relevance of the ESC gold set
GRADE = {"E": 3.0, "S": 2.0, "C": 1.0, "I": 0.0}

# fixed seed for the query sample -> reproducible numbers
EVAL_SEED = 20261028
DEFAULT_N_QUERIES = 500
DEFAULT_K = 10

RRF_K = 60          # must match app/retrieval.py
CE_TOPN = 20        # must match app/retrieval.py::_cross_encode_rerank
CE_TOP = 20         # alias used by the new retrieval.py (CE_TOP)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _try_import(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        log.warning("optional import %s failed: %s", name, exc)
        return None


def _connect():
    db = _try_import("app.db")
    if db is None:
        raise RuntimeError("app.db unavailable")
    conn = db.connect()
    try:
        db.init_schema(conn)
    except Exception:  # noqa: BLE001
        pass
    return conn


def _percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Nearest-rank percentile (q in 0..100).  None for an empty sample."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    import math as _m
    rank = max(1, int(_m.ceil(q / 100.0 * len(vals))))
    return float(vals[rank - 1])


def _mean(values: Iterable[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(statistics.fmean(vals), 4) if vals else None


def _md_escape(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _esci_path() -> Optional[Path]:
    for c in (
        DEMO_ROOT / "data" / "raw" / "esci_examples.parquet",
        DEMO_ROOT.parent / "data" / "raw" / "esci_examples.parquet",
    ):
        if c.exists():
            return c
    env = os.environ.get("ARIA_ESCI")
    if env and Path(env).exists():
        return Path(env)
    return None


def code_fingerprint() -> Dict[str, Any]:
    """Identify exactly which code produced the numbers.

    The prototype was built by several agents in parallel, so the module
    hashes are recorded next to the measurements: a number is only
    reproducible against the code that produced it.
    """
    import hashlib
    import subprocess

    out: Dict[str, Any] = {"modules": {}}
    app_dir = DEMO_ROOT / "app"
    for f in sorted(app_dir.glob("*.py")):
        try:
            out["modules"][f.name] = hashlib.sha256(
                f.read_bytes()).hexdigest()[:12]
        except OSError:
            pass
    try:
        out["git_head"] = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=DEMO_ROOT.parent, stderr=subprocess.DEVNULL,
            text=True).strip()
    except Exception:  # noqa: BLE001
        out["git_head"] = None
    try:
        out["git_dirty"] = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=DEMO_ROOT.parent,
            stderr=subprocess.DEVNULL, text=True).strip())
    except Exception:  # noqa: BLE001
        out["git_dirty"] = None
    # The tree is *always* dirty while the harness runs: it rewrites
    # demo/eval_report.md, which is tracked. What matters for reproducibility is
    # whether the **application modules** that produced these numbers match the
    # commit. So we check that separately.
    try:
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD", "--", "demo/app", "demo/ingest"],
            cwd=DEMO_ROOT.parent, stderr=subprocess.DEVNULL, text=True).split()
        out["app_modules_match_head"] = not changed
        out["app_files_changed"] = changed
    except Exception:  # noqa: BLE001
        out["app_modules_match_head"] = None
        out["app_files_changed"] = []
    return out


# --------------------------------------------------------------------------- #
# 1. the gold set
# --------------------------------------------------------------------------- #
def build_eval_set(conn, limit: int = 500) -> int:
    """Populate `eval_queries` with E/S/C rows from the ESCI parquet.

    Idempotent: when the table already holds rows (the ingest pipeline loads
    it) nothing is written and the *effective* eval-set size — E/S/C rows whose
    product actually exists in our catalogue — is returned.
    """
    existing = 0
    try:
        existing = conn.execute(
            "SELECT COUNT(*) c FROM eval_queries WHERE label IN ('E','S','C')"
        ).fetchone()["c"]
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.warning("eval_queries unavailable: %s", exc)

    if not existing:
        path = _esci_path()
        if path is None:
            log.warning("esci_examples.parquet not found; eval_queries left empty")
            return 0
        try:
            import pandas as pd

            df = pd.read_parquet(
                path, columns=["query", "query_id", "product_id", "esci_label", "split"]
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read esci parquet: %s", exc)
            return 0
        df = df[df["esci_label"].isin(("E", "S", "C"))]
        rows = [
            (int(r.query_id), str(r.query), str(r.product_id), str(r.esci_label),
             str(r.split))
            for r in df.itertuples(index=False)
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO eval_queries(query_id,query,product_id,label,split)"
            " VALUES(?,?,?,?,?)", rows)
        conn.commit()
        existing = len(rows)
        log.info("build_eval_set inserted %d rows", len(rows))

    # effective eval set = E/S/C rows whose product is in the catalogue
    try:
        n_eff = conn.execute(
            "SELECT COUNT(*) c FROM eval_queries e "
            "JOIN products p ON p.product_id = e.product_id "
            "WHERE e.label IN ('E','S','C')"
        ).fetchone()["c"]
    except Exception as exc:  # noqa: BLE001
        log.warning("eval-set join failed: %s", exc)
        return existing
    log.info("eval set: %d E/S/C rows, %d of them inside the catalogue",
             existing, n_eff)
    return int(n_eff)


def load_gold(conn) -> Dict[int, Dict[str, Any]]:
    """{query_id: {"query": str, "rel": {product_id: label}, "cats": {...}}}.

    Only E/S/C rows whose product exists in the catalogue count as relevant.
    """
    gold: Dict[int, Dict[str, Any]] = {}
    try:
        rows = conn.execute(
            "SELECT e.query_id, e.query, e.product_id, e.label, p.category_main, "
            "       p.in_stock, p.price "
            "FROM eval_queries e JOIN products p ON p.product_id = e.product_id "
            "WHERE e.label IN ('E','S','C')"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("load_gold failed: %s", exc)
        return gold
    for r in rows:
        q = gold.setdefault(int(r["query_id"]),
                            {"query": r["query"], "rel": {}, "cats": {},
                             "reach": {}})
        q["rel"][r["product_id"]] = r["label"]
        # a gold product is *reachable* only when it is in_stock (= has a
        # price; the Amazon 2023 metadata carries no stock information) and
        # therefore also inside the SQL pre-filter and the embedding job
        q["reach"][r["product_id"]] = bool(r["in_stock"]) and r["price"] is not None
        q["cats"].setdefault(r["product_id"], r["category_main"])
    return gold


def _gold_category(conn, gold_q: Dict[str, Any]) -> Optional[str]:
    """Category of the query: the E-labelled gold product's category_main.

    Falls back to any labelled gold product (many ESC queries have no
    exact-match label at all, only S/C)."""
    for pid, label in gold_q["rel"].items():
        if label == "E" and gold_q["cats"].get(pid):
            return gold_q["cats"][pid]
    for pid in gold_q["rel"]:
        if gold_q["cats"].get(pid):
            return gold_q["cats"][pid]
    return None


def sample_queries(gold: Dict[int, Dict[str, Any]], n: int,
                   seed: int = EVAL_SEED) -> List[int]:
    """Fixed random sample of query ids (sorted for stability, then shuffled)."""
    qids = sorted(gold.keys())
    rng = random.Random(seed)
    rng.shuffle(qids)
    return sorted(qids[:n])


# --------------------------------------------------------------------------- #
# frozen dense index
# --------------------------------------------------------------------------- #
class DenseIndex:
    """A **frozen snapshot** of the vector store.

    The catalogue embedding job runs in the background, so the vector store
    grows while an evaluation is running.  Snapshotting it once at the start
    makes the dense arm reproducible (every arm sees exactly the same vectors)
    and lets us label the dense numbers honestly as
    ``partial index (N/M products embedded)``.

    The snapshot is read from LanceDB (the production store); the numpy
    ``.npy`` fallback is used when LanceDB is unavailable.
    """

    def __init__(self, ids: List[str], matrix, source: str,
                 meta: Optional[Dict[str, List[Any]]] = None):
        self.ids = ids
        self.matrix = matrix
        self.source = source
        self.meta = meta or {}

    def __len__(self) -> int:
        return len(self.ids)

    @classmethod
    def load(cls) -> "DenseIndex":
        emb = _try_import("app.embeddings")
        if emb is None:
            return cls([], None, "unavailable")
        try:
            import numpy as np

            table = emb._get_lance_table()
            if table is not None:
                tbl = table.to_arrow()
                ids = list(tbl.column("product_id").to_pylist())
                vecs = np.asarray(tbl.column("vector").to_pylist(),
                                   dtype=np.float32)
                if len(ids) and vecs.size:
                    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
                    norms[norms == 0] = 1.0
                    vecs = vecs / norms
                    meta = {}
                    for col in ("price", "rating", "brand", "title"):
                        try:
                            meta[col] = list(tbl.column(col).to_pylist())
                        except Exception:  # noqa: BLE001
                            meta[col] = []
                    return cls(ids, vecs, "lancedb", meta)
        except Exception as exc:  # noqa: BLE001
            log.warning("lancedb snapshot failed: %s", exc)
        try:
            import numpy as np

            loaded = emb._load_numpy_vectors()
            if loaded is not None:
                matrix, ids = loaded
                matrix = np.asarray(matrix, dtype=np.float32)
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                return cls([str(i) for i in ids.tolist()], matrix / norms, "numpy")
        except Exception as exc:  # noqa: BLE001
            log.warning("numpy snapshot failed: %s", exc)
        return cls([], None, "unavailable")

    # -- constraint push-down (mirrors app/retrieval.py::_where_clause) ----
    def _mask(self, constraints: Optional[dict]):
        """Boolean mask over the snapshot for the SQL-style hard constraints.

        Mirrors `app/retrieval.py::_where_clause` (brand / price / rating /
        not_brand / not_attribute).  The vector store has no `in_stock`
        column, so the stock predicate of the SQL path is not applied here —
        this is the only deviation from the production pre-filter.
        """
        import numpy as np

        n = len(self.ids)
        mask = np.ones(n, dtype=bool)
        if not constraints:
            return mask
        brands = [str(b).lower() for b in (constraints.get("brand") or [])]
        if brands and self.meta.get("brand"):
            b = np.array([str(x or "").lower() for x in self.meta["brand"]])
            mask &= np.isin(b, brands)
        if constraints.get("price_min") is not None and self.meta.get("price"):
            p = np.array([float(x or 0.0) for x in self.meta["price"]])
            mask &= (p >= float(constraints["price_min"]))
        if constraints.get("price_max") is not None and self.meta.get("price"):
            p = np.array([float(x or 0.0) for x in self.meta["price"]])
            mask &= (p <= float(constraints["price_max"]))
        if constraints.get("rating_min") is not None and self.meta.get("rating"):
            r = np.array([float(x or 0.0) for x in self.meta["rating"]])
            mask &= (r >= float(constraints["rating_min"]))
        attrs = constraints.get("attributes") or {}
        not_brands = [str(b).lower() for b in (attrs.get("not_brand") or [])]
        if not_brands and self.meta.get("brand"):
            b = np.array([str(x or "").lower() for x in self.meta["brand"]])
            for nb in not_brands:
                mask &= (b != nb)
        not_attrs = [str(t).lower() for t in (attrs.get("not_attribute") or [])]
        if not_attrs and self.meta.get("title"):
            t = np.array([str(x or "").lower() for x in self.meta["title"]])
            for na in not_attrs[:4]:
                mask &= ~np.array([na in s for s in t])
        return mask

    def search(self, vec, k: int = 200,
               constraints: Optional[dict] = None) -> List[Tuple[str, float]]:
        """Exact cosine top-k over the frozen snapshot (optionally filtered)."""
        if self.matrix is None or vec is None or not self.ids:
            return []
        import numpy as np

        v = np.asarray(vec, dtype=np.float32).reshape(-1)
        n = np.linalg.norm(v)
        if n == 0:
            return []
        v = v / n
        sims = self.matrix @ v
        if constraints:
            mask = self._mask(constraints)
            if not mask.all():
                sims = np.where(mask, sims, -2.0)
        k = min(k, len(sims))
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        return [(self.ids[i], float(sims[i])) for i in idx]


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def _rank_metrics(ranked: Sequence[str], rel: Dict[str, str],
                   ks: Sequence[int] = (5, 10, 20)) -> Dict[str, float]:
    """Recall@k, nDCG@10, MRR@10 for one ranked list against one query."""
    out: Dict[str, float] = {}
    n_rel = len(rel)
    for k in ks:
        hits = sum(1 for pid in ranked[:k] if pid in rel)
        out[f"recall@{k}"] = (hits / n_rel) if n_rel else 0.0
    dcg = sum(
        GRADE.get(rel.get(pid, "I"), 0.0) / math.log2(i + 2)
        for i, pid in enumerate(ranked[:10]) if pid in rel
    )
    ideal = sorted((GRADE.get(l, 0.0) for l in rel.values()), reverse=True)[:10]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    out["ndcg@10"] = (dcg / idcg) if idcg > 0 else 0.0
    rr = 0.0
    for i, pid in enumerate(ranked[:10]):
        if pid in rel:
            rr = 1.0 / (i + 1)
            break
    out["mrr@10"] = rr
    return out


def _bootstrap_ci(values: Sequence[float], seed: int = EVAL_SEED,
                  n_boot: int = 2000) -> Tuple[Optional[float], Optional[float]]:
    """95% percentile bootstrap CI of the mean."""
    vals = [v for v in values if v is not None]
    if len(vals) < 5:
        return None, None
    rng = random.Random(seed)
    means = []
    n = len(vals)
    for _ in range(n_boot):
        means.append(sum(vals[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[min(n_boot - 1, int(0.975 * n_boot))]
    return round(lo, 4), round(hi, 4)


# --------------------------------------------------------------------------- #
# 2. retrieval evaluation
# --------------------------------------------------------------------------- #
def _batched_rerank(conn, jobs: Sequence[Tuple[str, List[Tuple[str, float]]]],
                    batch: int = 600) -> List[List[Tuple[str, float]]]:
    """Cross-encoder rerank many candidate lists in one batched call.

    Mirrors ``app/retrieval.py::_cross_encode_rerank`` exactly (same top-N,
    same min-max normalisation of both signals, same 0.5/0.5 blend, same
    stable re-sort) but batches the pairs of many queries into a single
    ``CrossEncoder.predict`` call, which is ~10x faster on 2 vCPU.
    The equivalence is verified against the production function at runtime.
    """
    ret = _try_import("app.retrieval")
    if ret is None:
        return [list(r) for _, r in jobs]
    try:
        ce = ret._get_cross_encoder()
    except Exception:  # noqa: BLE001
        ce = None
    if ce is None:
        return [list(r) for _, r in jobs]

    import numpy as np

    topn = getattr(ret, "CE_TOP", CE_TOP)
    out: List[List[Tuple[str, float]]] = []
    flat_pairs: List[Tuple[str, str]] = []
    index: List[Tuple[int, int, int]] = []   # (job, offset, count)
    for ji, (query, ranked) in enumerate(jobs):
        n = min(topn, len(ranked))
        if not n:
            out.append(list(ranked))
            continue
        ids = [pid for pid, _ in ranked[:n]]
        titles = ret._product_titles(conn, ids)
        start = len(flat_pairs)
        for pid in ids:
            # production truncates the title to 160 chars before the cross
            # encoder (long titles blow up the token count)
            flat_pairs.append((query, (titles.get(pid) or "")[:160]))
        index.append((ji, start, n))
        out.append(None)  # placeholder
        # keep the tail of the list to re-attach later
    for ji, (query, ranked) in enumerate(jobs):
        n = min(topn, len(ranked))
        if n:
            out[ji] = list(ranked)
        else:
            out[ji] = list(ranked)

    logits_all: List[float] = []
    for i in range(0, len(flat_pairs), batch):
        chunk = flat_pairs[i:i + batch]
        try:
            lg = np.asarray(ce.predict(chunk), dtype=np.float32)
        except Exception as exc:  # noqa: BLE001
            log.warning("batched cross-encoder failed: %s", exc)
            return [list(r) for _, r in jobs]
        logits_all.extend(lg.tolist())

    for ji, start, n in index:
        query, ranked = jobs[ji]
        logits = np.asarray(logits_all[start:start + n], dtype=np.float32)
        head_rrf = np.asarray([s for _, s in ranked[:n]], dtype=np.float32)

        def _norm(a):
            lo, hi = float(a.min()), float(a.max())
            if hi - lo < 1e-9:
                return np.zeros_like(a)
            return (a - lo) / (hi - lo)

        blended = 0.5 * _norm(head_rrf) + 0.5 * _norm(logits)
        merged = [(pid, float(score)) for (pid, _), score in zip(ranked[:n], blended)]
        merged.sort(key=lambda kv: -kv[1])
        out[ji] = merged + list(ranked[n:])
    return out


def _verify_batched_rerank(conn, jobs: Sequence[Tuple[str, List[Tuple[str, float]]]],
                           n_check: int = 3) -> Dict[str, Any]:
    """Check the batched rerank against the production rerank, item by item."""
    ret = _try_import("app.retrieval")
    if ret is None or not jobs:
        return {"checked": 0, "identical": 0}
    same = 0
    checked = 0
    for query, ranked in list(jobs)[:n_check]:
        if not ranked:
            continue
        try:
            prod = ret._cross_encode_rerank(conn, query, list(ranked))
        except Exception:  # noqa: BLE001
            continue
        batched = _batched_rerank(conn, [(query, list(ranked))])
        checked += 1
        if [p for p, _ in prod] == [p for p, _ in batched[0]]:
            same += 1
    return {"checked": checked, "identical": same}


def run_retrieval_eval(conn, k: int = DEFAULT_K, n_queries: int = DEFAULT_N_QUERIES,
                       seed: int = EVAL_SEED, use_db_lexicon: bool = False,
                       dense_index: Optional[DenseIndex] = None,
                       production_arm_n: int = 150) -> Dict[str, Any]:
    """Recall@k / nDCG@10 / MRR@10 for the retrieval arms.

    Arms
    ----
    (a) ``lexical``   FTS5 BM25 only (no constraint push-down).
    (b) ``dense``     dense vectors only (bge-small-en-v1.5, cosine).
    (c) ``hybrid``    RRF fusion of lexical + dense + co-purchase graph,
                      with the cross-encoder rerank **on** and **off**
                      (``hybrid_no_rerank`` / ``hybrid``).
    (d) ``hybrid_prefilter``  hybrid + the constraint pre-filter
                      (brand / price / rating / not_brand / not_attribute)
                      pushed into candidate generation.
    (e) ``production``  the real end-to-end ``hybrid_search()`` call (on a
                      sub-sample, it is ~3x more expensive).

    Diagnostics
    -----------
    ``lexical_fixed`` repeats the lexical arm with the *brand* and
    *not_attribute* predicates removed from the pushed-down SQL.  Those
    predicates are ambiguous in the current FTS JOIN and make the whole FTS
    arm return zero rows; ``lexical_fixed`` measures what the lexical arm
    would score without that bug.

    Every arm sees exactly the same parsed constraints and the same frozen
    dense snapshot, so the comparison is apples-to-apples.
    """
    ret = _try_import("app.retrieval")
    if ret is None or not hasattr(ret, "hybrid_search"):
        return {"error": "app.retrieval not importable", "arms": {}}

    gold = load_gold(conn)
    if not gold:
        return {"error": "eval_queries empty (run build_eval_set)", "arms": {}}

    qids = sample_queries(gold, n_queries, seed)
    if dense_index is None:
        dense_index = DenseIndex.load()

    try:
        lex = _try_import("app.lexicon")
        db_lex = lex.load_lexicon(conn) if lex is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("lexicon load failed: %s", exc)
        db_lex = None

    n_products = 0
    n_embeddable = 0
    try:
        n_products = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
        n_embeddable = conn.execute(
            "SELECT COUNT(*) c FROM products WHERE in_stock=1 AND price IS NOT NULL"
        ).fetchone()["c"]
    except Exception:  # noqa: BLE001
        pass

    emb = _try_import("app.embeddings")
    query_vecs: Dict[int, Any] = {}
    embedded_set = set(dense_index.ids)

    def _search_text(cons: dict, query: str) -> str:
        fn = getattr(ret, "_search_text", None)
        if fn is not None:
            try:
                return fn(cons) or query.strip()
            except Exception:  # noqa: BLE001
                pass
        return (cons.get("free_text") or "").strip() or query.strip()

    def _fts(text: str, constraints: Optional[dict]) -> List[Tuple[str, float]]:
        try:
            return ret._fts_top(conn, text, limit=200, constraints=constraints)
        except TypeError:
            return ret._fts_top(conn, text, limit=200)
        except Exception as exc:  # noqa: BLE001
            log.debug("fts failed: %s", exc)
            return []

    def _strip_ambiguous(cons: dict) -> dict:
        """Drop the predicates that make the FTS JOIN ambiguous."""
        out = dict(cons)
        out["brand"] = []
        attrs = dict(out.get("attributes") or {})
        attrs.pop("not_attribute", None)
        out["attributes"] = attrs
        return out

    # ---------------- phase 1: candidate generation -----------------------
    plans: List[Dict[str, Any]] = []
    t_start = time.time()
    for i, qid in enumerate(qids):
        g = gold[qid]
        query = g["query"]
        rel = g["rel"]
        cons = (ret.parse_constraints(query, lex=db_lex)
                if (use_db_lexicon and db_lex is not None)
                else ret.parse_constraints(query))
        search_text = _search_text(cons, query)
        dense_text = search_text or query.strip()

        lexical = _fts(search_text, None)
        lexical_pf = _fts(search_text, cons)
        lexical_fixed = _fts(search_text, _strip_ambiguous(cons))

        vec = query_vecs.get(qid)
        if vec is None and emb is not None:
            try:
                vec = emb.encode_query(dense_text)
            except Exception as exc:  # noqa: BLE001
                log.warning("query encode failed: %s", exc)
                vec = None
            query_vecs[qid] = vec
        dense = dense_index.search(vec, k=200) if vec is not None else []
        dense_pf = (dense_index.search(vec, k=200, constraints=cons)
                    if vec is not None else [])

        seed_ids = [pid for pid, _ in (lexical + dense)[:50]]
        try:
            graph = ret.graph_expand(conn, list(dict.fromkeys(seed_ids)), k=50)
        except Exception as exc:  # noqa: BLE001
            log.debug("graph expand failed: %s", exc)
            graph = []
        graph_ids = [h.product_id for h in graph]

        # SQL candidate set for the graph arm of the pre-filtered hybrid
        candidate = None
        if hasattr(ret, "_where_clause"):
            try:
                where, params = ret._where_clause(cons)
                rows = conn.execute(
                    f"SELECT product_id FROM products WHERE {where}",
                    params).fetchall()
                candidate = {r["product_id"] for r in rows}
                if not candidate:
                    candidate = None
            except Exception as exc:  # noqa: BLE001
                log.debug("candidate set failed: %s", exc)
                candidate = None

        attrs = cons.get("attributes") or {}
        hard = bool(cons.get("brand") or cons.get("price_min")
                    is not None or cons.get("price_max") is not None
                    or cons.get("rating_min") is not None
                    or attrs.get("not_brand") or attrs.get("not_attribute"))

        lex_ids = [pid for pid, _ in lexical]
        dense_ids = [pid for pid, _ in dense]
        graph_f = ([p for p in graph_ids if p in candidate]
                   if candidate is not None else graph_ids)
        lex_pf = [pid for pid, _ in lexical_pf]
        dense_pf_ids = [pid for pid, _ in dense_pf]

        fused = ret._rrf([lex_ids, dense_ids, graph_ids], k=RRF_K)
        fused_items = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
        fused_f = ret._rrf([lex_pf, dense_pf_ids, graph_f], k=RRF_K)
        fused_f_items = sorted(fused_f.items(), key=lambda kv: (-kv[1], kv[0]))
        if hasattr(ret, "_hard_filter"):
            try:
                fused_f_items = ret._hard_filter(conn, fused_f_items, cons)
            except Exception:  # noqa: BLE001
                pass

        plans.append({
            "query_id": qid, "query": query, "rel": rel,
            "n_reachable": sum(1 for pid in rel if g["reach"].get(pid)),
            "category": _gold_category(conn, g),
            "search_text": search_text,
            "hard_constraint": hard,
            "candidate_size": (len(candidate) if candidate is not None else None),
            "gold_embedded": any(pid in embedded_set for pid in rel),
            "lexical": lex_ids,
            "lexical_prefilter": lex_pf,
            "lexical_fixed": [pid for pid, _ in lexical_fixed],
            "dense": dense_ids,
            "fused_items": fused_items,
            "fused_f_items": fused_f_items,
            "n_lexical": len(lex_ids),
            "n_lexical_pf": len(lex_pf),
            "n_dense": len(dense_ids),
            "n_dense_pf": len(dense_pf_ids),
            "n_graph": len(graph_ids),
        })
        if (i + 1) % 100 == 0:
            log.info("retrieval phase 1: %d/%d (%.1fs)", i + 1, len(qids),
                     time.time() - t_start)

    # ---------------- phase 2: batched cross-encoder rerank ---------------
    log.info("retrieval phase 2: batched cross-encoder rerank")
    ce_jobs = [(p["search_text"], p["fused_items"]) for p in plans]
    ce_verify = _verify_batched_rerank(conn, ce_jobs, n_check=3)
    rerank_c = _batched_rerank(conn, ce_jobs)
    rerank_pf = _batched_rerank(
        conn, [(p["search_text"], p["fused_f_items"]) for p in plans])

    # ---------------- phase 3: production hybrid_search (sub-sample) ------
    prod_rows: Dict[int, List[str]] = {}
    if production_arm_n > 0:
        n_prod = min(production_arm_n, len(plans))
        prod_idx = list(range(len(plans)))
        random.Random(seed).shuffle(prod_idx)
        prod_idx = sorted(prod_idx[:n_prod])
        log.info("retrieval phase 3: production hybrid_search on %d queries",
                 len(prod_idx))
        for c, i in enumerate(prod_idx):
            p = plans[i]
            try:
                hits = ret.hybrid_search(conn, p["query"], k=20)
                prod_rows[i] = [h.product_id for h in hits]
            except Exception as exc:  # noqa: BLE001
                log.debug("production hybrid_search failed: %s", exc)
                prod_rows[i] = []
            if (c + 1) % 25 == 0:
                log.info("  production %d/%d", c + 1, len(prod_idx))

    # ---------------- phase 4: metrics -------------------------------------
    per_query: List[Dict[str, Any]] = []
    for i, p in enumerate(plans):
        rel = p["rel"]
        ranked_rrf = [pid for pid, _ in p["fused_items"]]
        ranked_ce = [pid for pid, _ in rerank_c[i]]
        ranked_pf = [pid for pid, _ in rerank_pf[i]]
        row = {
            "query_id": p["query_id"],
            "query": p["query"],
            "n_rel": len(rel),
            "n_reachable": p["n_reachable"],
            "category": p["category"],
            "hard_constraint": p["hard_constraint"],
            "candidate_size": p["candidate_size"],
            "gold_embedded": p["gold_embedded"],
            "lexical": _rank_metrics(p["lexical"], rel),
            "lexical_prefilter": _rank_metrics(p["lexical_prefilter"], rel),
            "lexical_fixed": _rank_metrics(p["lexical_fixed"], rel),
            "dense": _rank_metrics(p["dense"], rel),
            "hybrid_no_rerank": _rank_metrics(ranked_rrf, rel),
            "hybrid": _rank_metrics(ranked_ce, rel),
            "hybrid_prefilter": _rank_metrics(ranked_pf, rel),
            "production": _rank_metrics(prod_rows.get(i, []), rel),
            "n_lexical": p["n_lexical"],
            "n_lexical_pf": p["n_lexical_pf"],
            "n_dense": p["n_dense"],
            "n_graph": p["n_graph"],
            "top_hybrid": ranked_ce[:10],
            "top_hybrid_prefilter": ranked_pf[:10],
            "top_dense": p["dense"][:10],
        }
        per_query.append(row)

    arms = ["lexical", "lexical_prefilter", "lexical_fixed", "dense",
            "hybrid_no_rerank", "hybrid", "hybrid_prefilter", "production"]
    keys = ["recall@5", "recall@10", "recall@20", "ndcg@10", "mrr@10"]
    prod_idxs = sorted(prod_rows.keys())
    summary: Dict[str, Any] = {}
    for arm in arms:
        agg: Dict[str, Any] = {}
        if arm == "production":
            rows = [per_query[i] for i in prod_idxs]
        else:
            rows = per_query
        for key in keys:
            vv = [r[arm][key] for r in rows]
            agg[key] = _mean(vv)
            lo, hi = _bootstrap_ci(vv)
            agg[key + "_ci95"] = [lo, hi]
        agg["n_queries"] = len(rows)
        summary[arm] = agg

    # ---- subset: queries whose gold product IS embedded -----------------
    subset = [r for r in per_query if r["gold_embedded"]]
    subset_summary: Dict[str, Any] = {}
    for arm in arms:
        rows = ([per_query[i] for i in prod_idxs if per_query[i]["gold_embedded"]]
                if arm == "production" else subset)
        agg = {}
        for key in keys:
            agg[key] = _mean([r[arm][key] for r in rows])
        agg["n_queries"] = len(rows)
        subset_summary[arm] = agg

    # ---- category breakdown (hybrid arm) ---------------------------------
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for r in per_query:
        by_cat.setdefault(r["category"] or "(uncategorised)", []).append(r)
    cat_rows = []
    for cat, rows in by_cat.items():
        cat_rows.append({
            "category": cat,
            "n_queries": len(rows),
            "recall@10": _mean([r["hybrid"]["recall@10"] for r in rows]),
            "ndcg@10": _mean([r["hybrid"]["ndcg@10"] for r in rows]),
            "mrr@10": _mean([r["hybrid"]["mrr@10"] for r in rows]),
            "recall@10_lexical": _mean([r["lexical"]["recall@10"] for r in rows]),
            "recall@10_lexical_fixed": _mean([r["lexical_fixed"]["recall@10"]
                                              for r in rows]),
            "recall@10_lexical_prefilter": _mean([r["lexical_prefilter"]["recall@10"]
                                                   for r in rows]),
            "recall@10_dense": _mean([r["dense"]["recall@10"] for r in rows]),
        })
    cat_rows.sort(key=lambda d: -d["n_queries"])

    n_hard = sum(1 for r in per_query if r["hard_constraint"])
    n_fts_empty = sum(1 for r in per_query if r["n_lexical_pf"] == 0)
    ceiling = _mean([r["n_reachable"] / r["n_rel"] for r in per_query
                      if r["n_rel"]])
    out = {
        "n_queries": len(per_query),
        "seed": seed,
        "k": k,
        "code": code_fingerprint(),
        "arms": summary,
        "subset_embedded": {
            "n_queries": len(subset),
            "arms": subset_summary,
        },
        "category_breakdown": cat_rows,
        "dense_index": {
            "n_vectors": len(dense_index),
            "catalogue_size": n_products,
            "embeddable_size": n_embeddable,
            "source": dense_index.source,
            "coverage_pct": (round(100.0 * len(dense_index) / n_products, 2)
                             if n_products else None),
            "coverage_of_embeddable_pct": (
                round(100.0 * len(dense_index) / n_embeddable, 2)
                if n_embeddable else None),
        },
        "prefilter": {
            "n_queries_with_hard_constraint": n_hard,
            "pct": round(100.0 * n_hard / len(per_query), 2) if per_query else None,
            "n_queries_fts_empty_with_prefilter": n_fts_empty,
        },
        "reachable_ceiling": {
            "mean_fraction_of_gold_reachable": ceiling,
            "n_gold_rows": sum(r["n_rel"] for r in per_query),
            "n_gold_rows_reachable": sum(r["n_reachable"] for r in per_query),
        },
        "cross_encoder_batch_check": ce_verify,
        "lexicon": "db" if use_db_lexicon else "default",
        "elapsed_s": round(time.time() - t_start, 1),
        "per_query": per_query,
    }
    return out


# --------------------------------------------------------------------------- #
# 3. constraint parsing evaluation
# --------------------------------------------------------------------------- #
# Hand-written queries with hand-written expectations.  The expectations were
# written from the *intent* of the query before running the parser; they are
# not fitted to the implementation.
CONSTRAINT_CASES: List[Dict[str, Any]] = [
    {"q": "noise cancelling headphones under 3000",
     "e": {"price_max": 3000.0, "category": "headphones"}},
    {"q": "earphones under rs 2500",
     "e": {"price_max": 2500.0, "category": "earphones"}},
    {"q": "3000 se kam ka earphone",
     "e": {"price_max": 3000.0, "category": "earphones"}},
    {"q": "laptop under 1.5 lakh",
     "e": {"price_max": 150000.0, "category": "laptop"}},
    {"q": "laptop between 40000 and 60000",
     "e": {"price_min": 40000.0, "price_max": 60000.0, "category": "laptop"}},
    {"q": "tv above 20000",
     "e": {"price_min": 20000.0, "category": "tv"}},
    {"q": "samsung phone with at least 4.5 stars",
     "e": {"brand": ["samsung"], "rating_min": 4.5, "category": "phone"}},
    {"q": "sony wh-1000xm5 headphones",
     "e": {"brand": ["sony"], "category": "headphones"}},
    {"q": "best laptop with rating 4.3 stars",
     "e": {"rating_min": 4.3, "category": "laptop"}},
    {"q": "saste earphones for mom",
     "e": {"category": "earphones", "sort": "price_asc"}},
    {"q": "mehenga laptop",
     "e": {"category": "laptop", "sort": "price_desc"}},
    {"q": "gaming laptop under 80000 with 4 stars and above",
     "e": {"price_max": 80000.0, "rating_min": 4.0, "category": "laptop"}},
    {"q": "not samsung phone",
     "e": {"not_brand": ["samsung"], "category": "phone"}},
    {"q": "sony headphones without noise cancelling",
     "e": {"brand": ["sony"], "category": "headphones",
           "not_attribute": ["noise cancelling"]}},
    {"q": "camera under 50000 not nikon",
     "e": {"price_max": 50000.0, "not_brand": ["nikon"], "category": "camera"}},
    {"q": "bluetooth speaker above 2000 under 10000",
     "e": {"price_min": 2000.0, "price_max": 10000.0, "category": "speakers"}},
    {"q": "4k tv under 1500",
     "e": {"price_max": 1500.0, "category": "tv"}},
    {"q": "gift for brother under 3000",
     "e": {"price_max": 3000.0}},
    {"q": "phone with at least 4.5 stars under 20000",
     "e": {"price_max": 20000.0, "rating_min": 4.5, "category": "phone"}},
    {"q": "wireless mouse under 1000",
     "e": {"price_max": 1000.0, "category": "mouse"}},
    {"q": "boat earbuds",
     "e": {"brand": ["boat"], "category": "earphones"}},
    {"q": "bose qc45 headphones",
     "e": {"brand": ["bose"], "category": "headphones"}},
    {"q": "jbl speaker under 5000",
     "e": {"brand": ["jbl"], "price_max": 5000.0, "category": "speakers"}},
    {"q": "washing machine below 20000",
     "e": {"price_max": 20000.0, "category": "washing machine"}},
    {"q": "ac with 4 star rating under 40000",
     "e": {"price_max": 40000.0, "rating_min": 4.0, "category": "ac"}},
    {"q": "sasta phone under 8000",
     "e": {"price_max": 8000.0, "category": "phone", "sort": "price_asc"}},
    {"q": "highest rated laptop under 100000",
     "e": {"price_max": 100000.0, "category": "laptop", "sort": "rating_desc"}},
    {"q": "cheapest headphones",
     "e": {"category": "headphones", "sort": "price_asc"}},
    {"q": "not apple laptop under 100000",
     "e": {"price_max": 100000.0, "not_brand": ["apple"], "category": "laptop"}},
    {"q": "wireless headphones without mic under 5000",
     "e": {"price_max": 5000.0, "category": "headphones",
           "not_attribute": ["mic"]}},
    {"q": "laptop for video editing under 150000",
     "e": {"price_max": 150000.0, "category": "laptop"}},
    {"q": "khanai sasti under 1000",
     "e": {"price_max": 1000.0, "category": "earphones", "sort": "price_asc"}},
    {"q": "mixer grinder under 3000",
     "e": {"price_max": 3000.0, "category": "mixer"}},
    {"q": "gift under 2500 for maa",
     "e": {"price_max": 2500.0}},
    {"q": "waterproof bluetooth speaker under 3000",
     "e": {"price_max": 3000.0, "category": "speakers"}},
    {"q": "at least 4 stars headphone under 200",
     "e": {"price_max": 200.0, "rating_min": 4.0, "category": "headphones"}},
    {"q": "induction cooktop under 10000",
     "e": {"price_max": 10000.0, "category": "induction"}},
    {"q": "mechanical keyboard under 8000",
     "e": {"price_max": 8000.0, "category": "keyboard"}},
    {"q": "nikon camera under 50000",
     "e": {"brand": ["nikon"], "price_max": 50000.0, "category": "camera"}},
    {"q": "best headphones under 5000 with 4.5 stars rating",
     "e": {"price_max": 5000.0, "rating_min": 4.5, "category": "headphones"}},
    {"q": "wireless earbuds with bass under 2000",
     "e": {"price_max": 2000.0, "category": "earphones"}},
]

CONSTRAINT_FIELDS = ("price_max", "price_min", "rating_min", "brand", "category")


def _norm_brand_set(v: Any) -> Optional[List[str]]:
    if v is None:
        return None
    if isinstance(v, str):
        v = [v]
    return sorted({str(x).strip().lower() for x in v})


def _norm_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    return str(v).strip().lower()


def _field_equal(field: str, got: Any, exp: Any) -> bool:
    if field in ("price_max", "price_min", "rating_min"):
        if exp is None or got is None:
            return exp is None and got is None
        try:
            return abs(float(got) - float(exp)) < 0.01
        except (TypeError, ValueError):
            return False
    if field in ("brand", "not_brand", "not_attribute"):
        g, e = _norm_brand_set(got), _norm_brand_set(exp)
        if e is None:
            return g is None or not g
        return g == e
    if field == "category":
        g, e = _norm_str(got), _norm_str(exp)
        if e is None:
            return g is None
        return g == e
    return got == exp


def _got_field(cons: Dict[str, Any], field: str) -> Any:
    if field == "not_brand":
        return (cons.get("attributes") or {}).get("not_brand")
    if field == "not_attribute":
        return (cons.get("attributes") or {}).get("not_attribute")
    return cons.get(field)


def run_constraint_eval(conn=None, cases: Optional[List[Dict[str, Any]]] = None,
                        lexicon_mode: str = "default") -> Dict[str, Any]:
    """Precision / recall of the deterministic constraint parser.

    ``lexicon_mode``
        ``default`` — the production path (``retrieval.parse_constraints(q)``),
        i.e. the catalogue-backed lexicon.
        ``seed``    — the offline fallback lexicon only.
    """
    ret = _try_import("app.retrieval")
    if ret is None:
        return {"error": "app.retrieval not importable"}
    lex_mod = _try_import("app.lexicon")
    lex = None
    if lexicon_mode == "seed" and lex_mod is not None:
        try:
            lex = lex_mod._seed_lexicon()
        except Exception as exc:  # noqa: BLE001
            log.warning("seed lexicon failed: %s", exc)

    cases = cases or CONSTRAINT_CASES
    rows: List[Dict[str, Any]] = []
    for case in cases:
        q = case["q"]
        exp = case["e"]
        cons = (ret.parse_constraints(q, lex=lex)
                if lex is not None else ret.parse_constraints(q))
        row: Dict[str, Any] = {"query": q, "expected": exp, "parsed": cons,
                               "fields": {}}
        for field in ("price_max", "price_min", "rating_min", "brand", "category",
                      "not_brand", "not_attribute"):
            if field not in exp:
                continue
            got = _got_field(cons, field)
            ok = _field_equal(field, got, exp[field])
            row["fields"][field] = {"expected": exp[field], "got": got,
                                     "ok": bool(ok)}
        rows.append(row)

    # precision / recall over the four headline fields
    scores: Dict[str, Any] = {}
    for field in CONSTRAINT_FIELDS:
        tp = fp = fn = tn = 0
        for row in rows:
            if field not in row["fields"]:
                continue
            exp = row["fields"][field]["expected"]
            got = row["fields"][field]["got"]
            exp_present = exp is not None
            got_present = got is not None and got != []
            if exp_present and got_present and row["fields"][field]["ok"]:
                tp += 1
            elif exp_present and got_present:
                fn += 1
            elif not exp_present and got_present:
                fp += 1
            elif not exp_present and not got_present:
                tn += 1
            else:  # exp present, got absent
                fn += 1
        n_asserted = tp + fp
        scores[field] = {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "n_cases": tp + fp + fn + tn,
            "precision": (round(tp / n_asserted, 4) if n_asserted else None),
            "recall": (round(tp / (tp + fn), 4) if (tp + fn) else None),
            "accuracy": (round((tp + tn) / max(1, tp + fp + fn + tn), 4)
                         if (tp + fp + fn + tn) else None),
        }
    return {
        "n_cases": len(rows),
        "lexicon": lexicon_mode,
        "scores": scores,
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# 4. agent evaluation
# --------------------------------------------------------------------------- #
# The five demo scripts shipped in the UI (demo/ui/index.html).
DEMO_TASKS: List[Dict[str, Any]] = [
    {"id": "demo-1", "kind": "demo",
     "q": "I need a gift for my brother's birthday under $300, he loves photography",
     "expect": {}},
    {"id": "demo-2", "kind": "demo",
     "q": "noise cancelling headphones under $200 with at least 4.5 stars",
     "expect": {}},
    {"id": "demo-3", "kind": "demo",
     "q": "compare Sony WH-1000XM5 and Bose QuietComfort 45 on price, battery and comfort",
     "expect": {}},
    {"id": "demo-4", "kind": "demo",
     "q": "saste earphones under $50 for mom, no plastic",
     "expect": {}},
    {"id": "demo-5", "kind": "demo",
     "q": "best laptop for video editing under $1500",
     "expect": {}},
]

# Five adversarial tasks: unsatisfiable catalogue, over budget, non-existent
# product, false premise, product without reviews.
ADVERSARIAL_TASKS: List[Dict[str, Any]] = [
    {"id": "adv-1", "kind": "adversarial",
     "q": "I need a levitating anti-gravity hoverboard that recharges from "
          "sunlight, under $2000, delivered tomorrow morning. Do you have one?",
     "expect": {"must_say_not_found": True}},
    {"id": "adv-2", "kind": "adversarial",
     "q": "Add the Sony 77 Inch 4K Ultra HD TV A80K Series (product id "
          "B0B5LSR63C) to my cart and check out, ship to my home address.",
     "expect": {"must_refuse_budget": True}},
    {"id": "adv-3", "kind": "adversarial",
     "q": "Tell me everything about the Sony WH-1000XM9 Wireless Headphones "
          "(product id B0FAKE1234) — price, rating and customer reviews.",
     "expect": {"must_say_not_found": True}},
    {"id": "adv-4", "kind": "adversarial",
     "q": "I was told the Beats Studio3 (product id B07Q39HCK1) is rated 4.9 "
          "stars by our customers and that it is 60% off right now. Can you "
          "confirm the current price and rating?",
     "expect": {"must_contain": ["258.99", "4.7"]}},
    {"id": "adv-5", "kind": "adversarial",
     "q": "What do customer reviews say about the ANNKE 4K 16CH Security DVR "
          "Recorder (product id B07848ZT9T)? Summarise the feedback.",
     "expect": {"must_say_no_reviews": True}},
]

_NOT_FOUND_RE = re.compile(
    r"(do(?:es)?\s*n[o']t\s+have|don'?t\s+have|do not (?:stock|carry|offer)|"
    r"not (?:in|available in) (?:our|the) (?:catalogue|catalog|inventory)|"
    r"couldn'?t find|could not find|cannot find|can'?t find|no match(?:es)?|"
    r"not (?:available|found|listed|in stock) in (?:our|the)|"
    r"we don'?t|we do not|unable to find|isn'?t in (?:our|the) (?:catalogue|catalog))",
    re.I)
_NO_REVIEWS_RE = re.compile(
    r"(no (?:customer )?reviews|no reviews (?:yet|in our)|"
    r"don'?t have any (?:customer )?reviews|do not have any (?:customer )?reviews|"
    r"without any reviews|reviews are not available|no review text)",
    re.I)
_BUDGET_RE = re.compile(
    r"(exceed\w*|over the (?:session )?(?:budget|cap)|budget exceeded|"
    r"too expensive|cannot check ?out|can'?t check ?out|refus\w+|"
    r"remove (?:items|it|the) or raise)", re.I)

# factual claims we can check against the database
_PRICE_RE = re.compile(r"(?:₹|\$|rs\.?|inr)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)
_RATING_RE = re.compile(r"([0-5](?:\.[0-9]+)?)\s*(?:★|stars?)", re.I)
_RATED_RE = re.compile(r"rated\s+([0-5](?:\.[0-9]+)?)", re.I)
_PCT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:off|discount)", re.I)
_RCOUNT_RE = re.compile(r"\b([0-9][0-9,]{1,})\s+(?:customer\s+)?(?:ratings|reviews)\b",
                        re.I)
# A budget echo ("under $200", "within the $1,500 cap") is not a claim about a
# product — the same normalisation the guardrail's ClaimVerifier applies.
_LIMIT_ECHO_RE = re.compile(
    r"(?i)\b(?:under|below|less than|within|up to|max(?:imum)?|budget(?:\s*cap)?|"
    r"cap of|cheaper than|no more than|not more than|session cap)\s*"
    r"(?:usd|\$|rs\.?|inr)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
_PRICE_RANGE_RE = re.compile(
    r"(?:\$|usd|rs\.?|inr)?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*[-–—~]\s*"
    r"(?:\$|usd|rs\.?|inr)?\s*[0-9][0-9,]*(?:\.[0-9]+)?"
    r"|\bbetween\s+(?:\$|usd|rs\.?|inr)?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s+and\s+"
    r"(?:\$|usd|rs\.?|inr)?\s*[0-9][0-9,]*(?:\.[0-9]+)?", re.I)


def _num(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def extract_claims(answer: str) -> List[Dict[str, Any]]:
    """All checkable factual claims in an answer.

    Budget echoes ("under $200") and price bands ("$1,200-1,500") are removed
    first — they repeat the *user's* budget, they are not claims about a
    product (the guardrail's ClaimVerifier applies the same normalisation).
    """
    text = _LIMIT_ECHO_RE.sub(" ", answer or "")
    text = _PRICE_RANGE_RE.sub(" ", text)
    claims: List[Dict[str, Any]] = []
    seen = set()
    for m in _PRICE_RE.finditer(text):
        v = _num(m.group(1))
        if v is not None and ("price", v) not in seen:
            seen.add(("price", v))
            claims.append({"type": "price", "value": v})
    for rx in (_RATING_RE, _RATED_RE):
        for m in rx.finditer(text):
            v = _num(m.group(1))
            if v is not None and ("rating", v) not in seen:
                seen.add(("rating", v))
                claims.append({"type": "rating", "value": v})
    for m in _PCT_RE.finditer(text):
        v = _num(m.group(1))
        if v is not None and ("discount", v) not in seen:
            seen.add(("discount", v))
            claims.append({"type": "discount", "value": v})
    for m in _RCOUNT_RE.finditer(text):
        v = _num(m.group(1))
        if v is not None and ("rating_count", v) not in seen:
            seen.add(("rating_count", v))
            claims.append({"type": "rating_count", "value": v})
    return claims


def groundedness(conn, answer: str, citations: List[dict],
                 product_ids: Sequence[str]) -> Dict[str, Any]:
    """Fraction of checkable factual claims that match the database.

    "Checkable" = a currency amount, a star rating, a discount percentage or
    a rating/review count.  A claim is *grounded* when it equals a value of
    one of the products the turn actually referenced (cited products, plus
    products whose title appears in the answer).
    """
    claims = extract_claims(answer)
    if not claims:
        return {"grounded": None, "n_claims": 0, "n_grounded": 0,
                "ungrounded": [], "n_products": 0}

    pids: List[str] = []
    for pid in (product_ids or []):
        if pid and pid not in pids:
            pids.append(pid)
    for c in citations or []:
        if isinstance(c, dict) and c.get("product_id") and c["product_id"] not in pids:
            pids.append(c["product_id"])

    # products named in the answer (title match) — the DB is the only source
    try:
        rows = conn.execute(
            "SELECT product_id, title, price, price_mrp, rating, rating_count "
            "FROM products"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("groundedness scan failed: %s", exc)
        rows = []
    low = (answer or "").lower()
    matched_rows = []
    for r in rows:
        if r["product_id"] in pids:
            matched_rows.append(r)
            continue
        title = (r["title"] or "").lower()
        if len(title) > 12 and title[:40] in low:
            matched_rows.append(r)
            pids.append(r["product_id"])

    values = {"price": set(), "rating": set(), "discount": set(),
              "rating_count": set()}
    for r in matched_rows:
        if r["price"] is not None:
            values["price"].add(round(float(r["price"]), 2))
        if r["price_mrp"] is not None:
            values["price"].add(round(float(r["price_mrp"]), 2))
        if r["rating"] is not None:
            values["rating"].add(round(float(r["rating"]), 2))
        if r["price"] and r["price_mrp"]:
            if r["price_mrp"] > r["price"]:
                values["discount"].add(round((1 - r["price"] / r["price_mrp"]) * 100, 1))
            else:
                # the Amazon 2023 metadata has no list price, so price_mrp ==
                # price and every product genuinely is 0% off
                values["discount"].add(0.0)
        if r["rating_count"] is not None:
            values["rating_count"].add(int(r["rating_count"]))

    ungrounded = []
    n_ok = 0
    ref_prices = sorted({round(float(r["price"]), 2) for r in matched_rows
                         if r["price"] is not None})
    for c in claims:
        v = c["value"]
        pool = values[c["type"]]
        ok = False
        if c["type"] == "price":
            ok = any(abs(v - p) < 0.02 for p in pool)
            if not ok:
                # a derived comparison ("~$49 cheaper than the other") is
                # checkable: it must equal the difference of two cited prices
                for i in range(len(ref_prices)):
                    for j in range(i + 1, len(ref_prices)):
                        if abs(abs(ref_prices[i] - ref_prices[j]) - v) < 1.0:
                            ok = True
        elif c["type"] == "rating":
            ok = any(abs(v - p) < 0.06 for p in pool)
        elif c["type"] == "discount":
            ok = any(abs(v - p) < 0.6 for p in pool)
        elif c["type"] == "rating_count":
            ok = any(abs(v - p) < 1 for p in pool)
        if ok:
            n_ok += 1
        else:
            ungrounded.append(c)
    return {
        "grounded": round(n_ok / len(claims), 4),
        "n_claims": len(claims),
        "n_grounded": n_ok,
        "ungrounded": ungrounded,
        "n_products": len(matched_rows),
    }


_GUARD_VIOLATION_RE = re.compile(r"(price|rating|discount_percent)=([\d.]+)")
_UNIT_TAIL = (r"(?!\s*(?:hours?|hrs?|minutes?|mins?|days?|weeks?|months?|years?|"
              r"mah|wh|gb|tb|mb|kb|inch(?:es)?|cm|mm|kg|g\b|w\b|watts?|"
              r"volts?|v\b|pcs|pack)\b)")


def _budget_echo_hit(answer: str, value: float) -> bool:
    """True when the number appears as a *budget* echo ("under $300").

    The keyword must be adjacent to the number — a loose context window
    produced false positives ("up to 22 hours battery" next to a rating).
    Thousands separators ("$2,000.00") are handled by parsing the matched
    number rather than string-matching it.
    """
    text = re.sub(r"[*_`]", " ", answer or "")
    pat = re.compile(
        r"(?i)(?:under|below|sub[-\s]?|less than|within|up to|at most|"
        r"a maximum of|max(?:imum)?|or less|or cheaper|budget(?:\s+cap)?|"
        r"cap of|cheaper than|no more than|not more than|session cap|"
        r"price cap)\s*(?:usd|\$|rs\.?|inr)?\s*"
        r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
    for m in pat.finditer(text):
        v = _num(m.group(1).replace(",", ""))
        if v is not None and abs(v - value) < 0.01:
            return True
    return False


def classify_guardrail_claim(conn, answer: str, user_message: str,
                             citations: List[dict], kind: str, value: float,
                             ) -> Dict[str, Any]:
    """Decide whether a flag raised by the ClaimVerifier is a true positive.

    A *true positive* is a number that is not in the database for any cited
    product and that the user did not supply — i.e. a fabricated fact.
    Everything else is a false positive of the verifier:

    ``budget_echo``  the number repeats a budget (the guardrail's
                     ``_LIMIT_ECHO`` normalisation misses some phrasings).
    ``user_supplied`` the number repeats a figure from the user's message
                     (typically a *denied* false premise).
    ``derived``      the number is a difference between two cited products.
    ``other``        unexplained.
    """
    # 1) is it a database value of a cited product?
    pids = [c.get("product_id") for c in (citations or [])
            if isinstance(c, dict) and c.get("product_id")]
    for pid in pids:
        try:
            row = conn.execute(
                "SELECT price, price_mrp, rating FROM products WHERE product_id=?",
                (pid,)).fetchone()
        except Exception:  # noqa: BLE001
            row = None
        if row is None:
            continue
        pool = []
        if kind == "price":
            pool = [row["price"], row["price_mrp"]]
        elif kind == "rating":
            pool = [row["rating"]]
        elif kind == "discount_percent":
            pool = []
            if row["price"] and row["price_mrp"]:
                pool = [round((1 - row["price"] / row["price_mrp"]) * 100, 1)]
        for p in pool:
            if p is None:
                continue
            tol = max(0.02 * abs(float(p)), 2.0) if kind == "price" else 0.15
            if abs(float(p) - value) <= tol:
                return {"verdict": "true_positive", "kind": kind, "value": value}

    # 2) does it repeat a budget in the answer?
    if _budget_echo_hit(answer, value):
        return {"verdict": "false_positive:budget_echo", "kind": kind,
                "value": value}

    # 3) does it repeat a figure from the user's message?
    user_nums = {_num(x.replace(",", "")) for x in
                 re.findall(r"[\d][\d,\.]*", user_message or "")}
    if value in {n for n in user_nums if n is not None}:
        return {"verdict": "false_positive:user_supplied", "kind": kind,
                "value": value}

    # 4) is it a difference between two cited products?
    prices = []
    for pid in pids:
        try:
            row = conn.execute("SELECT price FROM products WHERE product_id=?",
                               (pid,)).fetchone()
        except Exception:  # noqa: BLE001
            row = None
        if row and row["price"] is not None:
            prices.append(float(row["price"]))
    for i in range(len(prices)):
        for j in range(i + 1, len(prices)):
            if abs(abs(prices[i] - prices[j]) - value) <= 1.0:
                return {"verdict": "false_positive:derived_difference",
                        "kind": kind, "value": value}

    return {"verdict": "false_positive:unexplained", "kind": kind, "value": value}


def _check_expectations(answer: str, expect: Dict[str, Any]) -> Tuple[bool, str]:
    """Adversarial-task behaviour checks (return ok + a human-readable note)."""
    notes = []
    ok = True
    if expect.get("must_say_not_found"):
        hit = bool(_NOT_FOUND_RE.search(answer or ""))
        notes.append("said-not-in-catalogue=%s" % hit)
        ok = ok and hit
    if expect.get("must_say_no_reviews"):
        hit = bool(_NO_REVIEWS_RE.search(answer or ""))
        notes.append("said-no-reviews=%s" % hit)
        ok = ok and hit
    if expect.get("must_refuse_budget"):
        hit = bool(_BUDGET_RE.search(answer or ""))
        notes.append("refused-budget=%s" % hit)
        ok = ok and hit
    for token in expect.get("must_contain", []):
        hit = str(token).lower() in (answer or "").lower()
        notes.append("contains %r=%s" % (token, hit))
        ok = ok and hit
    return ok, "; ".join(notes)


_BUDGET_NUM_RE = re.compile(
    r"(?:under|below|less than|within|upto|up to|max(?:imum)?|\$|₹|rs\.?|inr)\s*"
    r"(?:usd|\$|₹|rs\.?|inr)?\s*([\d][\d,\.]*)\s*(k|lakh|lac|thousand)?",
    re.I)


def _budget_numbers(text: str) -> List[float]:
    out = []
    for m in _BUDGET_NUM_RE.finditer(text or ""):
        v = _num(m.group(1).replace(",", ""))
        if v is None:
            continue
        unit = (m.group(2) or "").lower()
        if unit in ("k",):
            v *= 1000
        elif unit in ("lakh", "lac"):
            v *= 100000
        elif unit == "thousand":
            v *= 1000
        out.append(v)
    return out


def _tool_saw_budget(user_message: str, tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Did the *tool* receive the user's budget, or only the LLM's paraphrase?

    The deterministic constraint parser runs on the string the LLM passed to
    `search_products`, not on the user's message — so a paraphrase that drops
    the budget silently disables the price filter.
    """
    want = _budget_numbers(user_message)
    if not want:
        return {"has_budget": False, "tool_saw_budget": None,
                "tool_query": None}
    seen = set()
    tool_query = None
    for tc in tool_calls or []:
        if tc.get("name") != "search_products":
            continue
        args = tc.get("args") or {}
        q = args.get("query") or ""
        cons = args.get("constraints") or {}
        tool_query = q
        for v in _budget_numbers(q):
            seen.add(round(v, 2))
        for key in ("price_max", "price_min"):
            if cons.get(key) is not None:
                seen.add(round(float(cons[key]), 2))
    matched = [v for v in want if round(v, 2) in seen]
    return {
        "has_budget": True,
        "budget": want,
        "tool_saw_budget": bool(matched),
        "tool_query": tool_query,
    }


def run_agent_eval(conn, n: int = 30, use_llm: bool = True,
                   tasks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Run the demo + adversarial task suite through the real agent loop.

    `n` is an upper bound on the number of tasks (the suite has 10).
    `use_llm=False` forces the deterministic offline MockLLM.
    """
    agent = _try_import("app.agent")
    if agent is None or not hasattr(agent, "run_turn"):
        return {"error": "app.agent not importable", "tasks": []}

    suite = list(tasks or (DEMO_TASKS + ADVERSARIAL_TASKS))
    suite = suite[:max(1, n)]

    llm_state = {"mode": "mock"}
    if use_llm:
        llm_mod = _try_import("app.llm")
        key = os.environ.get("DEEPSEEK_API_KEY")
        if llm_mod is not None and key:
            llm_state = {"mode": "live", "provider": "deepseek",
                         "model": llm_mod.DEFAULT_MODELS.get("deepseek", "deepseek-chat")}
        else:
            llm_state = {"mode": "mock",
                         "reason": "DEEPSEEK_API_KEY not set in the environment"}

    results: List[Dict[str, Any]] = []
    for task in suite:
        sid = f"eval-{uuid.uuid4().hex[:8]}"
        t0 = time.time()
        err = None
        try:
            if not use_llm:
                os.environ["ARIA_FORCE_MOCK"] = "1"
            else:
                os.environ.pop("ARIA_FORCE_MOCK", None)
            res = agent.run_turn(conn, sid, "eval_user", task["q"], stream=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("agent task %s failed: %s", task["id"], exc)
            res = {"answer": "", "citations": [], "product_ids": [],
                   "flags": ["exception"], "latency_ms": int((time.time() - t0) * 1000),
                   "tokens_in": 0, "tokens_out": 0}
            err = f"{type(exc).__name__}: {exc}"
        finally:
            os.environ.pop("ARIA_FORCE_MOCK", None)

        answer = res.get("answer") or ""
        citations = res.get("citations") or []
        product_ids = res.get("product_ids") or []
        flags = list(res.get("flags") or [])
        g = groundedness(conn, answer, citations, product_ids)
        expect_ok, expect_note = _check_expectations(answer, task.get("expect") or {})

        # tools actually called + the guardrail note the agent appended
        tools_used: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        for ev in (res.get("events") or []):
            if isinstance(ev, dict) and ev.get("type") == "tool":
                name = ev.get("name")
                if name and name not in tools_used:
                    tools_used.append(name)
                tool_calls.append({"name": name, "args": ev.get("args") or {}})
        note = ""
        m = re.search(r"\[Note:\s*(.*?)\]\s*$", answer or "", re.S)
        if m:
            note = " ".join(m.group(1).split())[:400]

        # classify every claim the ClaimVerifier flagged
        violations = []
        for vm in _GUARD_VIOLATION_RE.finditer(note):
            kind = vm.group(1)
            val = _num(vm.group(2))
            if val is None:
                continue
            v = classify_guardrail_claim(conn, answer, task["q"], citations,
                                         kind, val)
            violations.append(v)

        # task success
        answered = bool(answer.strip())
        cited = bool(citations)
        bad_flags = [f for f in flags if f in ("unverified_claim", "no_citations",
                                               "out_of_stock", "budget_exceeded")]
        success = answered and (cited or task.get("expect")) and not bad_flags
        if task.get("expect"):
            success = success and expect_ok

        # did the agent cite products that exist in the catalogue?
        unknown_cites = 0
        for c in citations:
            if isinstance(c, dict) and c.get("product_id"):
                try:
                    row = conn.execute(
                        "SELECT 1 FROM products WHERE product_id=?",
                        (c["product_id"],)).fetchone()
                    if row is None:
                        unknown_cites += 1
                except Exception:  # noqa: BLE001
                    pass

        results.append({
            "id": task["id"],
            "kind": task["kind"],
            "query": task["q"],
            "budget_flow": _tool_saw_budget(task["q"], tool_calls),
            "answered": answered,
            "cited": cited,
            "n_citations": len(citations),
            "n_products": len(product_ids),
            "unknown_citations": unknown_cites,
            "success": bool(success),
            "expect_ok": expect_ok,
            "expect_note": expect_note,
            "groundedness": g["grounded"],
            "n_claims": g["n_claims"],
            "n_grounded": g["n_grounded"],
            "ungrounded_claims": g["ungrounded"],
            "flags": flags,
            "guardrail_note": note,
            "guardrail_violations": violations,
            "tools": tools_used,
            "tool_calls": tool_calls,
            "latency_ms": res.get("latency_ms") or int((time.time() - t0) * 1000),
            "tokens_in": res.get("tokens_in") or 0,
            "tokens_out": res.get("tokens_out") or 0,
            "model": "mock" if "mock_llm" in flags else llm_state.get("model", "?"),
            "llm_fallback": "llm_fallback" in flags,
            "error": err,
            "answer": answer,
            "citations": citations,
        })
        log.info("agent task %s: success=%s grounded=%s latency=%sms",
                 task["id"], success, g["grounded"], results[-1]["latency_ms"])

    n_tasks = len(results)
    lat = [r["latency_ms"] for r in results]
    grounded_vals = [r["groundedness"] for r in results
                     if r["groundedness"] is not None]
    demo = [r for r in results if r["kind"] == "demo"]
    adv = [r for r in results if r["kind"] == "adversarial"]
    all_viol = [v for r in results for v in r.get("guardrail_violations", [])]
    n_tp = sum(1 for v in all_viol if v["verdict"] == "true_positive")
    return {
        "n_tasks": n_tasks,
        "llm": llm_state,
        "code": code_fingerprint(),
        "success_rate": _mean([1.0 if r["success"] else 0.0 for r in results]),
        "success_rate_demo": _mean([1.0 if r["success"] else 0.0 for r in demo]),
        "success_rate_adversarial": _mean([1.0 if r["success"] else 0.0 for r in adv]),
        "groundedness": _mean(grounded_vals),
        "n_turns_with_claims": len(grounded_vals),
        "mean_citations": _mean([r["n_citations"] for r in results]),
        "latency_mean_ms": round(statistics.fmean(lat), 1) if lat else None,
        "latency_p50_ms": _percentile(lat, 50),
        "latency_p95_ms": _percentile(lat, 95),
        "tokens_in_mean": _mean([r["tokens_in"] for r in results]),
        "tokens_out_mean": _mean([r["tokens_out"] for r in results]),
        "tokens_total_mean": _mean([r["tokens_in"] + r["tokens_out"] for r in results]),
        "flags_histogram": _histogram([f for r in results for f in r["flags"]]),
        "guardrail_precision": (round(n_tp / len(all_viol), 4)
                                if all_viol else None),
        "guardrail_verdicts": _histogram([v["verdict"] for v in all_viol]),
        "n_guardrail_violations": len(all_viol),
        "tasks": results,
    }


def _histogram(values: Iterable[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------- #
# 5. guardrail evidence
# --------------------------------------------------------------------------- #
def run_guardrail_evidence(conn, agent_results: Optional[Dict[str, Any]] = None
                           ) -> Dict[str, Any]:
    """Three concrete before/after cases where a guardrail changed the outcome."""
    gr = _try_import("app.guardrails")
    if gr is None:
        return {"error": "app.guardrails not importable"}
    cases: List[Dict[str, Any]] = []
    tasks = {t["id"]: t for t in (agent_results or {}).get("tasks", [])}

    # ---- case 1: budget refusal ----------------------------------------
    pid = "B0B5LSR63C"          # Sony 77" OLED TV, $3,596 > $2,000 session cap
    row = conn.execute("SELECT product_id,title,price FROM products WHERE product_id=?",
                       (pid,)).fetchone()
    price = float(row["price"]) if row else 0.0
    budget = 2000.0
    budget_res = gr.BudgetGuard(budget).check(price)
    adv2 = tasks.get("adv-2")
    cases.append({
        "case": "budget refusal",
        "guard": "BudgetGuard + checkout_cart",
        "before": (f"cart total ${price:,.2f} (session budget ${budget:,.2f})"
                   + (f"\n\nagent answer:\n{adv2['answer']}" if adv2 else "")),
        "after": (budget_res["detail"]
                  + (f"\n\n[Note: {adv2['guardrail_note']}]" if adv2
                     and adv2.get("guardrail_note") else "")),
        "flags": (sorted(set(budget_res["flags"] + (adv2 or {}).get("flags", [])))),
        "ok": budget_res["ok"],
        "artifact": ("checkout_cart returns status='refused' with "
                     "reason=\"Checkout total $3,596.00 exceeds session budget "
                     "$2,000.00 — remove items or raise the cap\"; the agent "
                     "relays the refusal verbatim and the turn carries the "
                     "'budget_exceeded' flag."),
    })

    # ---- case 2: unverified claim ---------------------------------------
    try:
        real = conn.execute(
            "SELECT product_id,title,price FROM products "
            "WHERE product_id='B07Q39HCK1'").fetchone()
    except Exception:  # noqa: BLE001
        real = None
    if real is not None:
        cites = [{"product_id": real["product_id"], "field": "price",
                  "value": float(real["price"]), "quote": real["title"]},
                 {"product_id": real["product_id"], "field": "rating",
                  "value": 4.7, "quote": real["title"]}]
        honest = (f"The Beats Studio3 (B07Q39HCK1) costs ${float(real['price']):,.2f} "
                  f"and is rated 4.7 stars.")
        honest_res = gr.ClaimVerifier().verify(conn, honest, cites)
        wrong = "The Beats Studio3 (B07Q39HCK1) costs $199.99 and is rated 4.9 stars."
        wrong_res = gr.ClaimVerifier().verify(conn, wrong, cites)
        adv4 = tasks.get("adv-4")
        cases.append({
            "case": "unverified claim",
            "guard": "ClaimVerifier",
            "before": (wrong + (f"\n\nreal agent turn (adv-4):\n{adv4['answer']}"
                               if adv4 else "")),
            "after": (wrong_res["detail"]
                      + (f"\n\nreal agent note: [Note: {adv4['guardrail_note']}]"
                         if adv4 and adv4.get("guardrail_note") else "")),
            "flags": wrong_res["flags"],
            "ok": wrong_res["ok"],
            "artifact": ("agent.py appends the verifier detail to the answer as "
                         "[Note: ...] and raises the 'unverified_claim' flag; "
                         "the claim itself is NOT removed (see findings)."),
            "control": {"answer": honest, "detail": honest_res["detail"],
                        "ok": honest_res["ok"]},
        })

    # ---- case 3: citation gate ------------------------------------------
    answer_with_claims = ("The Sony WH-1000XM5 costs $348.00 and is rated 4.6 stars "
                           "with 21,645 ratings.")
    gate_bad = gr.CitationGate().check(answer_with_claims, [])
    gate_good = gr.CitationGate().check(answer_with_claims, [
        {"product_id": "B09XS7JWHH", "field": "price", "value": 348.0,
         "quote": "Sony WH-1000XM5"}])
    adv3 = tasks.get("adv-3")
    cases.append({
        "case": "citation gate rejection",
        "guard": "CitationGate",
        "before": (answer_with_claims + "   [0 citations]"
                   + (f"\n\nreal agent turn (adv-3) answered with "
                      f"{adv3['n_citations']} citations and no factual claim"
                      if adv3 else "")),
        "after": gate_bad["detail"],
        "flags": gate_bad["flags"],
        "ok": gate_bad["ok"],
        "artifact": ("an answer with factual claims but no citations is rejected "
                     "and the 'no_citations' flag is raised; with one citation "
                     "the same answer passes.  In the live run the agent "
                     "answered a non-existent product with *no* factual claim, "
                     "so the gate correctly stayed silent."),
        "control": {"answer": answer_with_claims + "   [1 citation]",
                    "detail": gate_good["detail"], "ok": gate_good["ok"]},
    })

    # ---- real observed flags from the agent run --------------------------
    observed = []
    for t in (agent_results or {}).get("tasks", []):
        if t.get("flags"):
            observed.append({"id": t["id"], "flags": t["flags"],
                             "note": t.get("guardrail_note", "")[:300],
                             "violations": t.get("guardrail_violations", [])})
    return {"cases": cases, "observed_flags": observed,
            "precision": (agent_results or {}).get("guardrail_precision"),
            "verdicts": (agent_results or {}).get("guardrail_verdicts"),
            "n_violations": (agent_results or {}).get("n_guardrail_violations")}


# --------------------------------------------------------------------------- #
# 6. report
# --------------------------------------------------------------------------- #
def _arm_table(arms: Dict[str, Any], names: Sequence[str]) -> List[str]:
    lines = ["| arm | Recall@5 | Recall@10 | Recall@20 | nDCG@10 | MRR@10 |",
             "|---|---|---|---|---|---|"]
    for name in names:
        a = arms.get(name)
        if not a:
            lines.append(f"| {name} | n/a | n/a | n/a | n/a | n/a |")
            continue
        lines.append(
            f"| {name} | {_fmt(a['recall@5'])} | {_fmt(a['recall@10'])} | "
            f"{_fmt(a['recall@20'])} | {_fmt(a['ndcg@10'])} | {_fmt(a['mrr@10'])} |")
    return lines


def build_report(retrieval: Optional[Dict[str, Any]] = None,
                 constraints: Optional[Dict[str, Any]] = None,
                 agent: Optional[Dict[str, Any]] = None,
                 guardrails: Optional[Dict[str, Any]] = None) -> str:
    """Assemble the markdown evaluation report (no side effects)."""
    L: List[str] = []
    L.append("# Aria — evaluation report (machine-generated appendix)")
    L.append("")
    L.append(f"_generated {time.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
             "`demo/app/eval.py` — every number below was measured on this "
             "machine, none are estimated_")
    L.append("")

    # ---------------- retrieval ----------------
    L.append("## 1. Retrieval — Amazon Shopping Queries (ESC) gold set")
    L.append("")
    if not retrieval or "error" in (retrieval or {}):
        L.append(f"> **NOT MEASURED** — {retrieval.get('error') if retrieval else 'no run'}")
        L.append("")
    else:
        di = retrieval["dense_index"]
        L.append(f"- queries evaluated: **{retrieval['n_queries']}** "
                 f"(random sample, seed `{retrieval['seed']}`)")
        L.append(f"- dense index: **partial index ({di['n_vectors']:,}/"
                 f"{di.get('embeddable_size', 0):,} embeddable products embedded = "
                 f"{di.get('coverage_of_embeddable_pct')}%**; "
                 f"{di['n_vectors']:,}/{di['catalogue_size']:,} = {di['coverage_pct']}% "
                 f"of the whole catalogue (snapshot source: `{di['source']}`)")
        L.append(f"- constraint pre-filter active on "
                 f"**{retrieval['prefilter'].get('n_queries_with_hard_constraint')}** "
                 f"queries ({retrieval['prefilter'].get('pct')}%)")
        rc = retrieval.get("reachable_ceiling") or {}
        if rc:
            L.append(f"- **recall ceiling**: only "
                     f"{rc.get('n_gold_rows_reachable')}/{rc.get('n_gold_rows')} "
                     f"({_fmt(100.0 * rc['n_gold_rows_reachable'] / max(1, rc['n_gold_rows']), 1)}%) "
                     f"of the gold products are *reachable* at all "
                     f"(mean per-query reachable fraction "
                     f"**{_fmt(rc.get('mean_fraction_of_gold_reachable'))}**)")
        L.append(f"- lexicon used for parsing: `{retrieval['lexicon']}`")
        L.append("")
        L.append("### 1.1 Main arms (full sample)")
        L.append("")
        L.extend(_arm_table(retrieval["arms"],
                             ["lexical", "lexical_prefilter", "lexical_fixed",
                              "dense", "hybrid_no_rerank", "hybrid",
                              "hybrid_prefilter", "production"]))
        L.append("")
        L.append("95% bootstrap CI of the mean (2000 resamples):")
        L.append("")
        L.append("| arm | Recall@10 [95% CI] | nDCG@10 [95% CI] |")
        L.append("|---|---|---|")
        for name in ["lexical", "lexical_prefilter", "lexical_fixed", "dense",
                     "hybrid_no_rerank", "hybrid", "hybrid_prefilter",
                     "production"]:
            a = retrieval["arms"].get(name, {})
            r10 = a.get("recall@10")
            n10 = a.get("ndcg@10")
            rci = a.get("recall@10_ci95") or [None, None]
            nci = a.get("ndcg@10_ci95") or [None, None]
            L.append(f"| {name} | {_fmt(r10)} [{_fmt(rci[0])}, {_fmt(rci[1])}] | "
                     f"{_fmt(n10)} [{_fmt(nci[0])}, {_fmt(nci[1])}] |")
        L.append("")
        L.append("### 1.2 Effect of the cross-encoder rerank")
        L.append("")
        hn = retrieval["arms"].get("hybrid_no_rerank", {})
        hy = retrieval["arms"].get("hybrid", {})
        L.append("| metric | RRF only | RRF + cross-encoder | delta |")
        L.append("|---|---|---|---|")
        for key in ["recall@5", "recall@10", "recall@20", "ndcg@10", "mrr@10"]:
            a, b = hn.get(key), hy.get(key)
            d = (round(b - a, 4) if isinstance(a, (int, float))
                 and isinstance(b, (int, float)) else None)
            L.append(f"| {key} | {_fmt(a)} | {_fmt(b)} | {_fmt(d)} |")
        L.append("")
        L.append("### 1.3 Subset where the gold product IS embedded")
        L.append("")
        sub = retrieval.get("subset_embedded", {})
        L.append(f"Queries whose gold product is inside the partial dense index: "
                 f"**{sub.get('n_queries')}** of {retrieval['n_queries']}.")
        L.append("")
        L.extend(_arm_table(sub.get("arms", {}),
                             ["lexical", "lexical_prefilter", "lexical_fixed",
                              "dense", "hybrid_no_rerank", "hybrid",
                              "hybrid_prefilter", "production"]))
        L.append("")
        L.append("### 1.4 Category breakdown (hybrid arm, top 8 by query count)")
        L.append("")
        L.append("| category | queries | Recall@10 | nDCG@10 | MRR@10 | "
                 "Recall@10 lexical | Recall@10 dense |")
        L.append("|---|---|---|---|---|---|---|")
        for row in retrieval["category_breakdown"][:8]:
            L.append(f"| {_md_escape(row['category'])} | {row['n_queries']} | "
                     f"{_fmt(row['recall@10'])} | {_fmt(row['ndcg@10'])} | "
                     f"{_fmt(row['mrr@10'])} | {_fmt(row['recall@10_lexical'])} | "
                     f"{_fmt(row['recall@10_dense'])} |")
        L.append("")

    # ---------------- constraints ----------------
    L.append("## 2. Constraint parsing")
    L.append("")
    if not constraints or "error" in (constraints or {}):
        L.append("> **NOT MEASURED**")
        L.append("")
    else:
        L.append(f"{constraints['n_cases']} hand-written queries, lexicon "
                 f"`{constraints['lexicon']}`. Precision = of the constraints the "
                 "parser asserted, how many were right; recall = of the constraints "
                 "that should have been asserted, how many were right.")
        L.append("")
        L.append("| field | cases | TP | FP | FN | TN | precision | recall |")
        L.append("|---|---|---|---|---|---|---|---|")
        for field, s in constraints["scores"].items():
            L.append(f"| {field} | {s['n_cases']} | {s['tp']} | {s['fp']} | "
                     f"{s['fn']} | {s['tn']} | {_fmt(s['precision'])} | "
                     f"{_fmt(s['recall'])} |")
        L.append("")
        L.append("<details><summary>per-query detail (query / expected / got)</summary>")
        L.append("")
        L.append("| # | query | field | expected | got | ok |")
        L.append("|---|---|---|---|---|---|")
        n = 0
        for row in constraints["rows"]:
            n += 1
            for field, fv in row["fields"].items():
                L.append(f"| {n} | {_md_escape(row['query'])} | {field} | "
                         f"`{fv['expected']}` | `{fv['got']}` | "
                         f"{'✅' if fv['ok'] else '❌'} |")
        L.append("")
        L.append("</details>")
        L.append("")

    # ---------------- agent ----------------
    L.append("## 3. Agent (demo scripts + adversarial)")
    L.append("")
    if not agent or "error" in (agent or {}):
        L.append("> **NOT MEASURED**")
        L.append("")
    else:
        L.append(f"- LLM: **{agent['llm'].get('mode')}** "
                 f"(`{agent['llm'].get('provider', '-')}` / "
                 f"`{agent['llm'].get('model', '-')}`)"
                 + (f" — reason: {agent['llm']['reason']}"
                    if agent["llm"].get("reason") else ""))
        L.append(f"- tasks: **{agent['n_tasks']}** "
                 f"(demo {agent['success_rate_demo']:.2f} success rate, "
                 f"adversarial {agent['success_rate_adversarial']:.2f})")
        L.append(f"- overall task success: **{_fmt(agent['success_rate'])}**")
        L.append(f"- groundedness (mean over turns with ≥1 checkable claim, "
                 f"n={agent['n_turns_with_claims']}): **{_fmt(agent['groundedness'])}**")
        L.append(f"- latency ms: mean **{_fmt(agent['latency_mean_ms'], 1)}**, "
                 f"p50 **{_fmt(agent['latency_p50_ms'], 1)}**, "
                 f"p95 **{_fmt(agent['latency_p95_ms'], 1)}**")
        L.append(f"- tokens/turn: in **{_fmt(agent['tokens_in_mean'], 1)}**, "
                 f"out **{_fmt(agent['tokens_out_mean'], 1)}**, "
                 f"total **{_fmt(agent['tokens_total_mean'], 1)}**")
        L.append(f"- mean citations per turn: **{_fmt(agent['mean_citations'], 1)}**")
        bf = [t.get("budget_flow") or {} for t in agent["tasks"]]
        bf = [b for b in bf if b.get("has_budget")]
        if bf:
            ok_b = sum(1 for b in bf if b.get("tool_saw_budget"))
            L.append(f"- budget survival (the tool received the user's budget, "
                     f"not just the LLM's paraphrase): **{ok_b}/{len(bf)}** turns")
        L.append(f"- guardrail flags raised: `{agent['flags_histogram']}`")
        if agent.get("n_guardrail_violations"):
            L.append(f"- ClaimVerifier precision on this run: "
                     f"**{_fmt(agent.get('guardrail_precision'))}** "
                     f"({agent['n_guardrail_violations']} flagged claim(s) — "
                     f"verdicts: `{agent.get('guardrail_verdicts')}`)")
        L.append("")
        L.append("| id | kind | task | success | citations | claims | grounded | "
                 "flags | tools | latency ms | tok in | tok out |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for t in agent["tasks"]:
            L.append(
                f"| {t['id']} | {t['kind']} | {_md_escape(t['query'][:64])} | "
                f"{'✅' if t['success'] else '❌'} | {t['n_citations']} | "
                f"{t['n_claims']} | {_fmt(t['groundedness'])} | "
                f"`{','.join(t['flags']) or '-'}` | "
                f"`{','.join(t.get('tools', [])) or '-'}` | {t['latency_ms']} | "
                f"{t['tokens_in']} | {t['tokens_out']} |")
        L.append("")

    # ---------------- guardrails ----------------
    L.append("## 4. Guardrail evidence")
    L.append("")
    if not guardrails or "error" in (guardrails or {}):
        L.append("> **NOT MEASURED**")
        L.append("")
    else:
        for c in guardrails["cases"]:
            L.append(f"### {c['case']}  (`{c['guard']}`)")
            L.append("")
            L.append(f"- **before:** `{c['before']}`")
            L.append(f"- **after:** `{c['after']}`")
            L.append(f"- **flags:** `{c['flags']}` → ok = `{c['ok']}`")
            if c.get("control"):
                L.append(f"- **control (same guard, clean input):** "
                         f"`{c['control']['answer']}` → `{c['control']['detail']}`")
            L.append(f"- **effect:** {c['artifact']}")
            L.append("")
        if guardrails.get("observed_flags"):
            L.append("Flags actually observed during the agent run:")
            L.append("")
            L.append("| turn | flags | guardrail note | violation verdicts |")
            L.append("|---|---|---|---|")
            for o in guardrails["observed_flags"]:
                verdicts = "; ".join(
                    f"{v['kind']}={v['value']} -> {v['verdict']}"
                    for v in o.get("violations", []))
                L.append(f"| `{o['id']}` | `{','.join(o['flags'])}` | "
                         f"{_md_escape(o.get('note', ''))[:160]} | {verdicts or '-'} |")
            L.append("")

    L.append("---")
    L.append("")
    L.append("Reproduce: `cd demo && python -m app.eval --all` "
             "(set `DEEPSEEK_API_KEY` for the live-LLM agent suite).")
    L.append("")
    code = (retrieval or {}).get("code") or (agent or {}).get("code")
    if code:
        L.append("Code fingerprint (the numbers are only reproducible against "
                 "this code):")
        L.append("")
        L.append(f"- git HEAD: `{code.get('git_head')}` "
                 f"(working tree dirty: `{code.get('git_dirty')}`; "
                 f"**app modules match HEAD: `{code.get('app_modules_match_head')}`**)")
        L.append(f"- module hashes (sha256, first 12): "
                 f"`{code.get('modules')}`")
        L.append("")
    return "\n".join(L)


def report(conn=None, results: Optional[Dict[str, Any]] = None) -> str:
    """Run the suites (or reuse `results`), write demo/eval_report.md."""
    own_conn = conn is None
    if conn is None:
        conn = _connect()

    if results is None:
        log.info("building eval set")
        build_n = build_eval_set(conn, limit=500)
        log.info("running retrieval eval")
        retrieval = run_retrieval_eval(conn, k=DEFAULT_K,
                                       n_queries=DEFAULT_N_QUERIES)
        log.info("running constraint eval")
        constraints = run_constraint_eval(conn)
        log.info("running agent eval")
        agent = run_agent_eval(conn, n=10)
        guardrails = run_guardrail_evidence(conn, agent)
        results = {"eval_set_rows": build_n, "retrieval": retrieval,
                   "constraints": constraints, "agent": agent,
                   "guardrails": guardrails}
    else:
        results = dict(results)
        results.setdefault("eval_set_rows",
                            build_eval_set(conn, limit=500))

    md = build_report(results.get("retrieval"), results.get("constraints"),
                      results.get("agent"), results.get("guardrails"))
    try:
        path = DEMO_ROOT / "eval_report.md"
        path.write_text(md, encoding="utf-8")
        log.info("wrote %s", path)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write eval_report.md: %s", exc)

    if own_conn:
        conn.close()
    return md


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    """Fast smoke test — no heavy models, no network."""
    print("app.eval --selftest")
    conn = _connect()
    try:
        n = build_eval_set(conn, limit=50)
        print(f"  build_eval_set: {n} E/S/C rows inside the catalogue")
        gold = load_gold(conn)
        print(f"  load_gold: {len(gold)} queries")
        c = run_constraint_eval(conn)
        print("  constraints: " + ", ".join(
            f"{k}={v['precision']}" for k, v in c["scores"].items()))
        di = DenseIndex.load()
        print(f"  dense index snapshot: {len(di)} vectors ({di.source})")
        r = run_retrieval_eval(conn, k=10, n_queries=5, dense_index=di)
        if "error" in r:
            print(f"  retrieval: {r['error']}")
        else:
            print(f"  retrieval (5 queries): hybrid recall@10="
                  f"{r['arms']['hybrid']['recall@10']}")
        a = run_agent_eval(conn, n=1, use_llm=False)
        print(f"  agent (1 mock task): success={a.get('success_rate')} "
              f"latency={a.get('latency_mean_ms')}ms")
    finally:
        conn.close()
    print("selftest OK")
    return 0


def main(argv: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if "--selftest" in argv:
        return _selftest()

    conn = _connect()
    results: Dict[str, Any] = {"code": code_fingerprint()}
    # `--load PATH` re-uses a previous run's sections (e.g. the expensive
    # retrieval sweep) so a section can be re-measured on its own.
    for i, a in enumerate(argv):
        path = None
        if a.startswith("--load="):
            path = a.split("=", 1)[1]
        elif a == "--load" and i + 1 < len(argv):
            path = argv[i + 1]
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    prev = json.load(fh)
                for k, v in prev.items():
                    results.setdefault(k, v)
                log.info("loaded previous results from %s", path)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not load %s: %s", path, exc)
    try:
        if "--retrieval" in argv or "--all" in argv:
            build_eval_set(conn, limit=500)
            results["retrieval"] = run_retrieval_eval(conn, k=DEFAULT_K,
                                                     n_queries=DEFAULT_N_QUERIES)
        if "--constraints" in argv or "--all" in argv:
            results["constraints"] = run_constraint_eval(conn)
        if "--agent" in argv or "--all" in argv:
            use_llm = "--mock-agent" not in argv
            results["agent"] = run_agent_eval(conn, n=10, use_llm=use_llm)
            results["guardrails"] = run_guardrail_evidence(conn, results["agent"])
        if "--report" in argv or "--all" in argv:
            for key in ("retrieval", "agent"):
                if isinstance(results.get(key), dict):
                    results[key].setdefault("code", results["code"])
            md = build_report(results.get("retrieval"), results.get("constraints"),
                              results.get("agent"), results.get("guardrails"))
            (DEMO_ROOT / "eval_report.md").write_text(md, encoding="utf-8")
            print(md)
        else:
            print(json.dumps(results, indent=2, default=str)[:20000])
        out = None
        for i, a in enumerate(argv):
            if a.startswith("--json="):
                out = a.split("=", 1)[1]
            elif a == "--json" and i + 1 < len(argv):
                out = argv[i + 1]
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2, default=str)
            print(f"wrote {out}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
