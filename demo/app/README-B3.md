# B3 — Serving & UI

Owns `main.py`, `eval.py`, `../ui/**`, `../tests/**`.

**Run**: `python demo/run.py` (uvicorn :8000, single worker) or
`python -m app.main --selftest`.

**Endpoints**: `/api/chat` (SSE or JSON), `/api/voice`, `/api/image`,
`/api/product/{id}` (+ `/api/products/{id}` alias), `/api/cart`,
`/api/checkout`, `/api/session/{id}`, `/api/health`. Static UI at `/`.

**SSE events consumed** from `agent.run_turn`: `token`, `tool`, `citation`,
`done`. `stream=True` returns `{"type":"stream","events":<gen>}`; this layer
unwraps it. B1/B2 modules are imported lazily inside handlers, so this module
imports before they land; missing modules degrade to a lexical fallback.

**Eval**: `build_eval_set`, `run_retrieval_eval` (Recall@k/nDCG@k/MRR),
`run_agent_eval`, `report()` → `demo/eval_report.md`.

**Tests**: `python demo/tests/run_all.py` (no pytest; SKIP ≠ fail).
