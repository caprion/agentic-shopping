"""Gate: the hand-written evaluation doc must agree with the machine-generated report.

`demo/eval_report.md` is produced by `python -m app.eval --all --report`.
`docs/03-evaluation.md` is the narrative around it. If someone edits a number in
the narrative without re-running the evaluation (or re-syncing the tables), this
test fails.

The invariant checked here is the same one `demo/ingest/sync_docs_tables.py`
enforces: for each mapped position, the table in the doc equals the table in the
report.

Run standalone:  python demo/tests/test_docs_numbers.py
"""
from __future__ import annotations

import os
import re
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_ROOT = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(DEMO_ROOT)

REPORT = os.path.join(DEMO_ROOT, "eval_report.md")
DOC = os.path.join(REPO_ROOT, "docs", "03-evaluation.md")

RESULTS: list[tuple[str, bool, str]] = []

# (doc section prefix, table index inside the section, report table selector)
PLAN = (
    ("### 2.1", 0, "arms_main"),
    ("### 2.1", 1, "arms_ci"),
    ("### 2.2", 0, "ce_delta"),
    ("### 2.3", 0, "arms_subset"),
    ("### 2.4", 0, "categories"),
)

HEADERS = {
    "arms_main": "| arm | Recall@5 | Recall@10 | Recall@20 | nDCG@10 | MRR@10 |",
    "arms_ci": "| arm | Recall@10 [95% CI] | nDCG@10 [95% CI] |",
    "ce_delta": "| metric | RRF only | RRF + cross-encoder | delta |",
    "categories": "| category | queries |",
}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


def _sections(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"^(### .*)$", text, flags=re.M)
    out: list[tuple[str, str]] = [("", parts[0])]
    for i in range(1, len(parts) - 1, 2):
        out.append((parts[i].strip(), parts[i + 1]))
    return out


def _tables(body: str) -> list[list[str]]:
    tables, cur = [], []
    for line in body.split("\n"):
        if line.strip().startswith("|"):
            cur.append(line.strip())
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    return tables


def _report_tables(report: str) -> dict[str, list[str]]:
    all_tables = _tables(report)
    out: dict[str, list[str]] = {}
    for key, header in HEADERS.items():
        matches = [t for t in all_tables if t and t[0].startswith(header)]
        if matches:
            out[key] = matches[0]
    arms = [t for t in all_tables if t and t[0].startswith(HEADERS["arms_main"])]
    if len(arms) >= 2:
        out["arms_subset"] = arms[1]
    return out


def _doc_sections(doc: str) -> dict[str, str]:
    return {h.strip(): b for h, b in _sections(doc) if h}


def run() -> list[tuple[str, bool, str]]:
    """Return [(name, ok, detail)] — the shape run_all.py expects."""
    RESULTS.clear()
    if not os.path.exists(REPORT):
        return [("report_present", True, "SKIP: run python -m app.eval --all --report")]
    if not os.path.exists(DOC):
        return [("doc_present", True, "SKIP: docs/03-evaluation.md missing")]

    report = open(REPORT, encoding="utf-8").read()
    doc = open(DOC, encoding="utf-8").read()
    rtab = _report_tables(report)
    dsec = _doc_sections(doc)

    for sec_prefix, idx, key in PLAN:
        head = next((h for h in dsec if h.startswith(sec_prefix)), None)
        if head is None:
            check(f"{sec_prefix} present in the doc", False, "section not found")
            continue
        if key not in rtab:
            check(f"{key} present in the report", False, "table not found")
            continue
        dtabs = _tables(dsec[head])
        if len(dtabs) <= idx:
            check(f"{sec_prefix} table {idx} ({key})", False,
                  f"doc section has {len(dtabs)} table(s)")
            continue
        check(f"{sec_prefix} table {idx} matches the report ({key})",
              dtabs[idx] == rtab[key],
              "doc and report differ")

    # each mapped section must contain exactly the mapped number of tables
    expected_counts: dict[str, int] = {}
    for sec_prefix, idx, _key in PLAN:
        expected_counts[sec_prefix] = max(expected_counts.get(sec_prefix, 0), idx + 1)
    for sec_prefix, expected in expected_counts.items():
        head = next((h for h in dsec if h.startswith(sec_prefix)), None)
        if head is None:
            continue
        n = len(_tables(dsec[head]))
        check(f"{sec_prefix} has no stale duplicate tables",
              n == expected, f"doc section has {n} table(s), expected {expected}")

    # no stale duplicate arm tables in the narrative
    doc_arm_tables = [t for t in _tables(doc)
                      if t and t[0].startswith(HEADERS["arms_main"])]
    check("exactly two arm tables in the doc (main + subset)", len(doc_arm_tables) == 2,
          f"found {len(doc_arm_tables)}")

    # headline table in section 0 quotes the report's hybrid numbers
    m = re.search(r"hybrid \*\*Recall@10\*\* \(ESC gold set\) \| \*\*([0-9.]+)\*\*", doc)
    arms = rtab.get("arms_main", [])
    hybrid_row = next((r for r in arms if r and r.split("|")[1].strip() == "hybrid"), None)
    rep_r10 = hybrid_row.split("|")[3].strip() if hybrid_row else None
    check("headline Recall@10 matches the report",
          bool(m) and rep_r10 is not None and m.group(1) == rep_r10,
          f"doc={m.group(1) if m else None} report={rep_r10}")

    # README quotes the same row
    readme = os.path.join(REPO_ROOT, "README.md")
    if os.path.exists(readme):
        rtxt = open(readme, encoding="utf-8").read()
        rm = re.search(
            r"Hybrid \+ cross-encoder\*\* \| \*\*([0-9.]+)\*\* \| \*\*([0-9.]+)\*\* \| "
            r"([0-9.]+) \| \*\*([0-9.]+)\*\*", rtxt)
        rep_ndcg = hybrid_row.split("|")[5].strip() if hybrid_row else None
        check("README hybrid Recall@10 matches the report",
              bool(rm) and rep_r10 is not None and rm.group(2) == rep_r10,
              f"README={rm.groups() if rm else None} report={rep_r10}")
        check("README hybrid nDCG@10 matches the report",
              bool(rm) and rep_ndcg is not None and rm.group(4) == rep_ndcg,
              f"README={rm.group(4) if rm else None} report={rep_ndcg}")

    return list(RESULTS)


def main() -> int:
    results = run()
    failed = 0
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            failed += 1
    print(f"\nsummary: {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
