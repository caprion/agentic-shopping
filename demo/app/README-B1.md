# B1 — Retrieval & Personalisation

Owns `lexicon.py`, `embeddings.py`, `retrieval.py`, `personalize.py`.

**Self-tests**: `python -m app.retrieval --selftest` (constraints + top-5 per
query; "DB empty — skipping live check" on an empty DB); same flag works for
`lexicon`, `embeddings`, `personalize`.

**Design**: deterministic regex + lexicon parsing (no LLM); SQL pre-filter →
FTS5 bm25-200 + LanceDB ANN-200 + `product_edges` graph-50 → RRF (k=60) →
cross-encoder rerank (`ms-marco-MiniLM-L-6-v2`, optional) → category/attribute/
accessory re-weighting → personalisation → explicit sort.

**Fallbacks**: no model/LanceDB → lexical-only; bad query → SQL fallback,
never raises. Vectors: LanceDB `products`, else numpy `.npy`.

**Caveats**: dense index partly built (28k/195k); catalogue prices are raw USD,
so "₹"/"$" thresholds apply numerically; warm `hybrid_search` ≈0.5–4 s (under
load), first call ≈45 s (model load — call `retrieval.warmup(conn)` at boot).
