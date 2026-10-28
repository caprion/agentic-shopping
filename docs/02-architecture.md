# 02 — Architecture

How Aria is put together, and why each piece is the way it is. The long-form design rationale (with the
technology trade-offs and a 10-day build order) is in
[`../research/04-architecture.md`](../research/04-architecture.md); this document describes **what we
actually built**.

```
 browser (vanilla JS, no build step, no CDN)
   │  SSE: token | tool | citation | done
   ▼
 FastAPI (1 uvicorn worker)                         demo/app/main.py
   ├── /api/chat  /api/voice  /api/image
   ├── /api/cart  /api/checkout  /api/product/{id}  /api/session/{id}
   ▼
 Agent loop (ReAct)                                 demo/app/agent.py
   MAX_TOOL_CALLS = 8 · WALL_CLOCK_S = 60
   ├── LLMClient  (deepseek | minimax | openai | MockLLM)      app/llm.py
   ├── tools (12)                                       app/tools.py
   └── guardrails (middleware, pre + post)             app/guardrails.py
   ▼
 Retrieval core                                    demo/app/retrieval.py
   constraint parsing (deterministic) + lexicon    app/lexicon.py
   SQL pre-filter → FTS5 BM25 + dense ANN + graph → RRF(k=60) → cross-encoder → personalisation
   ▼
 Storage                                           demo/app/db.py
   SQLite (WAL) + FTS5 → products, reviews, interactions, users, product_edges,
                        sessions, carts, orders, traces, eval_queries, lexicon
   LanceDB → product embeddings (numpy .npy fallback)
```

## 1. Storage: one SQLite file, one vector table

`demo/app/db.py` is the single source of truth for the schema: `products`, `product_variants`, `reviews`,
`users`, `interactions`, `product_edges`, `sessions`, `carts`, `cart_items`, `orders`, `order_items`,
`traces`, `lexicon`, `eval_queries`. Olist's real transactions live in separate `olist_*` tables so a
Brazilian order is never confused with an Amazon product.

Why SQLite: one file, zero ops, WAL, and **FTS5 gives BM25 natively**. At 195k products the entire
catalogue is ~1 GB on disk and queries are milliseconds. Postgres + pgvector would add a daemon and an ops
surface for no benefit at this scale — and the DDL is deliberately Postgres-portable for when it matters.

Vectors live in **LanceDB** (embedded, on-disk ANN) with a **numpy brute-force fallback** persisted as
`.npy`, so the retrieval layer never needs to know which backend is live.

## 2. Ingestion (offline, idempotent)

`demo/ingest/build_db.py` has one stage per source: `products`, `products_jsonl`, `reviews`,
`interactions`, `olist`, `eval`, `lexicon`, `fts`. Each stage is idempotent and re-runnable.

Two decisions worth calling out:

* **Entity = `parent_asin`, variant = `asin`.** Amazon's raw metadata mixes the two; grouping by the
  parent gives one product entity with many variants (colour, size, storage) — which is what a shopper
  means by "a product".
* **Co-purchase graph from real baskets.** For each of the 1.39M users we take their purchased items and
  emit edges with weights = number of shared baskets. That single table is what makes the third retrieval
  channel (graph expansion) work, and it is derived from *real* behaviour, not a heuristic.

Embedding is a separate, resumable job (`demo/ingest/build_embeddings.py`): it tracks completed product
ids in an `embeddings_done` table, so it can be stopped and restarted at will. On a 4-vCPU box the
`bge-small-en-v1.5` encoder does ≈40 texts/s single-process — the honest number to plan against.

## 3. Retrieval: three channels, fused

`app/retrieval.py` implements the query path:

1. **Constraint parsing** (`parse_constraints`) is **deterministic**: regex + a lexicon built from the
   catalogue (brands, categories) + a hand-written synonym/Hinglish map. It extracts
   `price_min/price_max`, `rating_min`, `brand`, `category`, `attributes`, negations ("no plastic",
   "not Samsung") and a free-text remainder.
   *Why not the LLM?* Because it must be **fast, free, reproducible, and offline-safe**. The LLM is
   reserved for phrasing the answer; the *filter* is code.
2. **SQL pre-filter** — price/rating/brand/category/in-stock, indexed.
3. **Three candidate generators**, run in parallel:
   * **Lexical** — SQLite FTS5 `MATCH` with BM25 ranking (top-200).
   * **Dense** — LanceDB ANN on the `bge-small` embedding (top-200).
   * **Graph** — `product_edges` expansion from the user's recent items (top-50).
4. **Fusion** — Reciprocal Rank Fusion with `k=60`. RRF needs no score calibration, which is exactly why it
   is the right choice when fusing a BM25 score with a cosine similarity with a graph weight.
5. **Rerank** — `cross-encoder/ms-marco-MiniLM-L-6-v2` over the top-50, then top-10. Degrades to "no
   rerank" if the model is unavailable (and the run says so).
6. **Personalisation re-rank** — blends the rerank score with a user-profile affinity term and returns a
   **deterministic explanation** ("you bought 2 Soundcore items; $99.99 is below your usual $120").

Every stage degrades independently: no embeddings → lexical only; no cross-encoder → no rerank; no
user → popularity prior. The demo never dies because a model is missing.

## 4. The agent loop

`app/agent.py` is a plain ReAct loop, model-agnostic:

```
while time < 60s and tool_calls < 8:
    response = llm.complete(messages, tools=TOOLS)
    if response has tool_calls:
        for call in response.tool_calls:
            result, citations = run_tool(conn, call.name, call.args, session)   # never raises
            messages.append(observation)
    else:
        break
answer = response.text
answer, flags = guardrails.verify(conn, answer, citations)     # pre-done
yield {"type": "done", "answer": answer, "citations": citations, "flags": flags, ...}
```

* **12 tools**: `search_products`, `get_product`, `get_reviews`, `compare_products`,
  `similar_products`, `add_to_cart`, `remove_from_cart`, `view_cart`, `checkout_cart`, `track_order`,
  `get_price_history`, `web_search_external`.
* **Tool results are compact and cited.** A fact is never returned without a citation:
  `{"product_id", "field", "value", "quote"}` for product fields, `{"product_id", "review_id", "value",
  "quote"}` for review evidence. The answer uses inline markers `[p1]`, `[r12]` that map back to them.
* **The generator is resumable** — session state and traces are persisted in a `finally` block, so a
  client that disconnects mid-stream still leaves a consistent session.
* **Provider fallback**: `deepseek` → `minimax` → `openai` → `MockLLM`. `MockLLM` is not a stub that
  invents content — it copies price, rating and title verbatim out of the tool result, so an offline demo
  still produces grounded output.

## 5. Guardrails as middleware

`app/guardrails.py` runs **before and after** the tools, not inside the prompt:

| Guard | Trigger | Action |
|---|---|---|
| `PromptGuard` | request asks for a real payment / real-world side effect | refuse, explain that checkout is a mock |
| `BudgetGuard` | cart total > session cap (default **$2,000**) | refuse checkout, flag `budget_exceeded` |
| `StockGuard` | answer claims in-stock for a product whose row says otherwise | strip/flag the claim |
| `ClaimVerifier` | final answer contains a price, rating or %-off | re-reads the DB and checks each one; mismatches are flagged and corrected |
| `CitationGate` | factual answer with zero citations | rejected |

The principle, which is the single most transferable idea in this repo:

> **A prompt is a request; a middleware check is a control.** Put the invariants where they cannot be
> forgotten — outside the model's discretion.

## 6. Evaluation

`app/eval.py` + `demo/ingest/build_db.py --stage eval` give:

* **Retrieval**: Recall@k, nDCG@k, MRR on the Amazon Shopping Queries (ESC) gold labels, comparing
  lexical / dense / hybrid / hybrid+constraints.
* **Constraint parsing**: precision per field on a hand-written set (English + code-mixed).
* **Agent**: task success, groundedness, guardrail flags, latency and tokens per turn — including
  adversarial turns (unsatisfiable request, over-budget request, false premise, no reviews).
* **Traces**: every turn writes to the `traces` table (`tool`, `args`, `result`, tokens, latency, flags),
  so the eval can be recomputed from the logs rather than re-run.

Results are in [03-evaluation.md](03-evaluation.md).

## 7. The frontend

`demo/ui/` is vanilla JS + CSS, no build step, no CDN, no external fonts — it must work on a plane.
Three regions:

* **Chat** — streaming tokens, tool-call chips ("`search_products` → 12 hits"), inline citation pills that
  expand to the source row/review, product cards with image, price, rating and an add-to-cart button.
* **Cart / checkout** — items, total, budget bar, mock checkout producing an order id and ETA.
* **System panel** — the transparency surface: tools called, inferred constraints, citations count,
  latency, tokens, guardrail flags.

The system panel is not decoration. It is how a reviewer sees that the answers are *derived*, not *asserted*.

## 8. Failure modes we designed for

| Failure | Handling |
|---|---|
| No network at demo time | `MockLLM` + lexical-only retrieval + every outbound call returns a typed error the agent can survive |
| No LLM API key | same fallback path, flagged `llm_fallback` in the trace and the UI |
| Missing embedding model | lexical + graph channels only; the run says which channels were active |
| Catalogue does not satisfy the request | agent must use external search and label the result as external, or say it cannot find it |
| Request over budget | `BudgetGuard` refuses; the agent explains and offers alternatives |
| VM restart mid-build | ingestion and embedding are resumable; the agent contract survives, only the agents die |
