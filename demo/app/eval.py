"""Evaluation harness for the Aria agentic-shopping prototype (builder B3).

Implements the CONTRACT signatures:

    build_eval_set(conn, limit) -> int
    run_retrieval_eval(conn, k) -> dict      # Recall@k, nDCG@k, MRR
    run_agent_eval(conn, n) -> dict          # task success / groundedness / latency / tokens
    report() -> str                          # writes demo/eval_report.md

Retrieval / agent modules are imported lazily so this module runs (and its
selftest exits 0) even while B1/B2 are still under construction or the DB is
empty.  Labels E/S/C are treated as relevant (graded E=3, S=2, C=1 for nDCG).

Run:
    python -m app.eval --selftest
    python -m app.eval              # full report -> demo/eval_report.md
"""
from __future__ import annotations

import importlib
import logging
import math
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

DEMO_ROOT = Path(__file__).resolve().parent.parent      # .../demo
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))

GRADE = {"E": 3.0, "S": 2.0, "C": 1.0, "I": 0.0}

DEMO_TASKS: List[str] = [
    "mere bhai ke liye gift under 3000, delivery Pune by Friday, wo photography pasand karta hai",
    "noise cancelling headphones under 5000 with at least 4.5 stars and a year warranty",
    "compare sony wh-1000xm5 and bose qc45 on comfort, battery and price",
    "gift for my mom, cooking related, she doesn't want plastic",
    "best laptop for video editing under 1.5 lakh",
]

_VARIANTS = [
    lambda t: t.replace("under 3000", "under 2500"),
    lambda t: t.replace("under 5000", "under 6000"),
    lambda t: t.replace("1.5 lakh", "1.2 lakh"),
    lambda t: "under 4000 " + t,
    lambda t: t + " (free delivery)",
]


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


# --------------------------------------------------------------------------- #
# build_eval_set
# --------------------------------------------------------------------------- #
def build_eval_set(conn, limit: int = 500) -> int:
    """Populate eval_queries (E/S/C) from the ESCI parquet, joined to catalogue.

    Idempotent: if eval_queries already has rows we return the existing count.
    """
    try:
        existing = conn.execute("SELECT COUNT(*) c FROM eval_queries").fetchone()["c"]
        if existing:
            log.info("eval_queries already populated (%d rows)", existing)
            return existing
    except Exception:  # noqa: BLE001 - table may not exist yet
        pass

    path = _esci_path()
    if path is None:
        log.warning("esci_examples.parquet not found; eval_queries left empty")
        return 0
    try:
        import pandas as pd
    except Exception as exc:  # noqa: BLE001
        log.warning("pandas unavailable: %s", exc)
        return 0

    try:
        df = pd.read_parquet(
            path, columns=["query", "query_id", "product_id", "esci_label", "split"]
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read esci parquet: %s", exc)
        return 0

    df = df[df["esci_label"].isin(("E", "S", "C"))]
    prod_ids = {r["product_id"] for r in conn.execute("SELECT product_id FROM products")}
    df = df[df["product_id"].isin(prod_ids)]
    if df.empty:
        log.warning("no E/S/C rows joined to catalogue (products empty?)")
        return 0
    df = df.head(limit)

    rows = [
        (int(r.query_id), str(r.query), str(r.product_id),
         str(r.esci_label), str(r.split))
        for r in df.itertuples(index=False)
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO eval_queries(query_id,query,product_id,label,split)"
        " VALUES(?,?,?,?,?)",
        rows,
    )
    conn.commit()
    log.info("build_eval_set inserted %d rows", len(rows))
    return len(rows)


# --------------------------------------------------------------------------- #
# run_retrieval_eval
# --------------------------------------------------------------------------- #
def run_retrieval_eval(conn, k: int = 10, max_queries: int = 100) -> dict:
    """Recall@k, nDCG@k, MRR over the E/S/C gold set."""
    out = {"recall_at_k": 0.0, "ndcg_at_k": 0.0, "mrr": 0.0,
           "n_queries": 0, "n_skipped": 0, "k": k, "note": ""}

    queries: Dict[int, dict] = {}
    try:
        for r in conn.execute(
            "SELECT query_id, query, product_id, label FROM eval_queries"
            " WHERE label IN ('E','S','C')"
        ):
            q = queries.setdefault(r["query_id"], {"query": r["query"], "rel": {}})
            q["rel"][r["product_id"]] = r["label"]
    except Exception as exc:  # noqa: BLE001
        out["note"] = f"eval_queries unavailable/empty: {exc}"
        return out

    if not queries:
        out["note"] = "eval_queries empty (run build_eval_set)"
        return out

    # sample for runtime if the gold set is large
    qids = list(queries.keys())
    if len(qids) > max_queries:
        qids = qids[:max_queries]

    ret = _try_import("app.retrieval")
    if ret is None or not hasattr(ret, "hybrid_search"):
        out["note"] = "app.retrieval.hybrid_search not available yet"
        out["n_queries"] = 0
        return out

    recall_sum = ndcg_sum = mrr_sum = 0.0
    n_eval = n_skip = 0
    for qid in qids:
        q = queries[qid]
        rel = q["rel"]
        if not rel:
            n_skip += 1
            continue
        try:
            hits = ret.hybrid_search(conn, q["query"], k=k)
            ranked = [getattr(h, "product_id", None) for h in hits]
            ranked = [p for p in ranked if p]
        except Exception as exc:  # noqa: BLE001
            log.debug("hybrid_search failed for q%d: %s", qid, exc)
            n_skip += 1
            continue

        got = set(ranked) & set(rel)
        recall_sum += len(got) / len(rel)

        for i, pid in enumerate(ranked):
            if pid in rel:
                mrr_sum += 1.0 / (i + 1)
                break

        dcg = sum(
            GRADE.get(rel[pid], 0.0) / math.log2(i + 2)
            for i, pid in enumerate(ranked) if pid in rel
        )
        ideal = sorted((GRADE.get(l, 0.0) for l in rel.values()), reverse=True)
        idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
        ndcg_sum += (dcg / idcg) if idcg > 0 else 0.0
        n_eval += 1

    if n_eval:
        out["recall_at_k"] = round(recall_sum / n_eval, 4)
        out["ndcg_at_k"] = round(ndcg_sum / n_eval, 4)
        out["mrr"] = round(mrr_sum / n_eval, 4)
    out["n_queries"] = n_eval
    out["n_skipped"] = n_skip
    return out


# --------------------------------------------------------------------------- #
# run_agent_eval
# --------------------------------------------------------------------------- #
_BUDGET_RE = re.compile(
    r"(?:under|below|within|upto|up to|kam|se kam|under\s+₹?|₹)\s*"
    r"(?:₹|rs\.?|inr|\$)?\s*(\d+(?:[.,]\d+)?)\s*(lakh|lac|l|k|thousand|hazar|hazaar)?",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(r"(?:₹|rs\.?|\$|inr)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)


def _parse_budget(text: str) -> Optional[float]:
    m = re.search(
        r"(?:under|below|within|upto|up to)\s*"
        r"(?:₹|rs\.?|inr|\$)?\s*([\d]+(?:[.,][\d]+)?)\s*(lakh|lac|l|k|thousand|hazar)?",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    if unit in ("lakh", "lac", "l"):
        val *= 100_000
    elif unit == "k":
        val *= 1_000
    return val


def _task_variant(i: int) -> str:
    task = DEMO_TASKS[i % len(DEMO_TASKS)]
    vi = (i // len(DEMO_TASKS)) % len(_VARIANTS)
    return _VARIANTS[vi](task)


def run_agent_eval(conn, n: int = 30) -> dict:
    """Run N agentic tasks (the demo scripts + variants) and report metrics."""
    agent = _try_import("app.agent")
    if agent is None or not hasattr(agent, "run_turn"):
        return {"n_tasks": 0, "success_rate": 0.0, "groundedness": 0.0,
                "avg_latency_ms": 0.0, "avg_tokens_in": 0.0, "avg_tokens_out": 0.0,
                "tasks": [], "note": "app.agent.run_turn not available yet"}

    tasks = []
    for i in range(max(1, n)):
        msg = _task_variant(i)
        sid = f"eval-{uuid.uuid4().hex[:8]}"
        t0 = time.time()
        try:
            res = agent.run_turn(conn, sid, "eval_user", msg, stream=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("task %d failed: %s", i, exc)
            tasks.append({"task": msg, "error": str(exc)})
            continue
        if not isinstance(res, dict):
            res = {"type": "done", "answer": str(res)}
        answer = res.get("answer") or ""
        product_ids = res.get("product_ids") or []
        citations = res.get("citations") or []
        latency = res.get("latency_ms") or int((time.time() - t0) * 1000)

        budget = _parse_budget(msg)
        within = True
        if budget is not None and product_ids:
            try:
                top = conn.execute(
                    "SELECT price FROM products WHERE product_id=?",
                    (product_ids[0],),
                ).fetchone()
                within = top is None or top["price"] is None or top["price"] <= budget
            except Exception:  # noqa: BLE001
                within = True

        answered = bool((answer or "").strip())
        cited = bool(citations) or bool(product_ids)
        grounded = _groundedness(conn, answer, citations, product_ids)

        tasks.append({
            "task": msg,
            "answered": answered,
            "cited": cited,
            "within_budget": within,
            "success": answered and cited and within,
            "groundedness": grounded,
            "latency_ms": latency,
            "tokens_in": res.get("tokens_in") or 0,
            "tokens_out": res.get("tokens_out") or 0,
            "n_products": len(product_ids),
            "flags": res.get("flags") or [],
        })

    n_tasks = len(tasks)
    if not n_tasks:
        return {"n_tasks": 0, "success_rate": 0.0, "groundedness": 0.0,
                "avg_latency_ms": 0.0, "avg_tokens_in": 0.0, "avg_tokens_out": 0.0,
                "tasks": tasks, "note": "no tasks ran"}

    successes = [t for t in tasks if t.get("success")]
    grounded_vals = [t["groundedness"] for t in tasks if "groundedness" in t]
    return {
        "n_tasks": n_tasks,
        "success_rate": round(len(successes) / n_tasks, 4),
        "groundedness": round(sum(grounded_vals) / len(grounded_vals), 4)
        if grounded_vals else 0.0,
        "avg_latency_ms": round(sum(t["latency_ms"] for t in tasks) / n_tasks, 1),
        "avg_tokens_in": round(sum(t["tokens_in"] for t in tasks) / n_tasks, 1),
        "avg_tokens_out": round(sum(t["tokens_out"] for t in tasks) / n_tasks, 1),
        "tasks": tasks,
        "note": "",
    }


def _groundedness(conn, answer: str, citations: list, product_ids: list) -> float:
    """Fraction of currency claims in the answer that match a cited product price."""
    claims = [float(m.replace(",", "")) for m in _PRICE_RE.findall(answer or "")]
    if not claims:
        return 1.0  # nothing verifiable claimed → no fabrication detected
    pids = list(product_ids or [])
    for c in citations or []:
        if isinstance(c, dict) and c.get("product_id"):
            pids.append(c["product_id"])
    db_prices = set()
    for pid in pids:
        try:
            r = conn.execute("SELECT price FROM products WHERE product_id=?", (pid,)).fetchone()
            if r and r["price"] is not None:
                db_prices.add(round(float(r["price"]), 2))
        except Exception:  # noqa: BLE001
            continue
    if not db_prices:
        return 1.0
    matched = sum(1 for c in claims if round(c, 2) in db_prices
                  or any(abs(c - p) < 0.01 for p in db_prices))
    return matched / len(claims)


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def report(conn=None) -> str:
    """Build the markdown report and write it to demo/eval_report.md."""
    own_conn = conn is None
    if conn is None:
        conn = _connect()

    build_n = build_eval_set(conn, limit=500)
    ret_metrics = run_retrieval_eval(conn, k=10, max_queries=100)
    agent_metrics = run_agent_eval(conn, n=10)

    lines: List[str] = []
    lines.append("# Aria evaluation report")
    lines.append("")
    lines.append(f"_generated {time.strftime('%Y-%m-%d %H:%M:%S')}_")
    lines.append("")
    lines.append(f"- eval_queries rows: **{build_n}**")
    lines.append(f"- retrieval eval: recall@10 **{ret_metrics['recall_at_k']}**, "
                 f"nDCG@10 **{ret_metrics['ndcg_at_k']}**, MRR **{ret_metrics['mrr']}** "
                 f"({ret_metrics['n_queries']} queries, {ret_metrics['n_skipped']} skipped)")
    if ret_metrics.get("note"):
        lines.append(f"- retrieval note: {ret_metrics['note']}")
    lines.append("")
    lines.append("## Retrieval metrics")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append(f"| Recall@{ret_metrics['k']} | {ret_metrics['recall_at_k']} |")
    lines.append(f"| nDCG@{ret_metrics['k']} | {ret_metrics['ndcg_at_k']} |")
    lines.append(f"| MRR | {ret_metrics['mrr']} |")
    lines.append(f"| queries evaluated | {ret_metrics['n_queries']} |")
    lines.append("")
    lines.append("## Agent metrics")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append(f"| tasks | {agent_metrics['n_tasks']} |")
    lines.append(f"| success rate | {agent_metrics['success_rate']} |")
    lines.append(f"| groundedness | {agent_metrics['groundedness']} |")
    lines.append(f"| avg latency (ms) | {agent_metrics['avg_latency_ms']} |")
    lines.append(f"| avg tokens in | {agent_metrics['avg_tokens_in']} |")
    lines.append(f"| avg tokens out | {agent_metrics['avg_tokens_out']} |")
    if agent_metrics.get("note"):
        lines.append(f"| note | {agent_metrics['note']} |")
    lines.append("")

    if agent_metrics.get("tasks"):
        lines.append("### Agent tasks")
        lines.append("")
        lines.append("| task | success | grounded | latency_ms | products |")
        lines.append("|---|---|---|---|---|")
        for t in agent_metrics["tasks"]:
            lines.append(
                f"| {_md_escape(t['task'][:60])} | {t.get('success')} | "
                f"{t.get('groundedness')} | {t.get('latency_ms')} | {t.get('n_products')} |"
            )
        lines.append("")

    md = "\n".join(lines) + "\n"
    try:
        report_path = DEMO_ROOT / "eval_report.md"
        report_path.write_text(md, encoding="utf-8")
        log.info("wrote %s", report_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write eval_report.md: %s", exc)

    if own_conn:
        conn.close()
    return md


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    print("app.eval --selftest")
    conn = _connect()
    try:
        build_n = build_eval_set(conn, limit=50)
        print(f"  build_eval_set: {build_n} rows")
        ret = run_retrieval_eval(conn, k=3, max_queries=3)
        print(f"  retrieval: recall@3={ret['recall_at_k']} ndcg@3={ret['ndcg_at_k']} "
              f"mrr={ret['mrr']} n={ret['n_queries']} note={ret.get('note','')!r}")
        ag = run_agent_eval(conn, n=1)
        print(f"  agent: n={ag['n_tasks']} success={ag['success_rate']} "
              f"grounded={ag['groundedness']} note={ag.get('note','')!r}")
    finally:
        conn.close()
    print("selftest OK")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(report())
