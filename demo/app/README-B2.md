# B2 — Agent & Tools

Owns `llm.py`, `tools.py`, `agent.py`, `guardrails.py`. Imports against B1 are
lazy; tools degrade to SQLite when retrieval is missing.

## Tools (12)
search_products, get_product, get_reviews, compare_products, similar_products,
add_to_cart, remove_from_cart, view_cart, checkout_cart, track_order,
get_price_history, web_search_external.

## Events B3 consumes
- `token`: `{type, text}`
- `tool`: `{type, name, args, tool_call_id}`
- `citation`: `{type, citation:{product_id, field|review_id, value, quote, marker}}`
- `done`: `{type, answer, citations, product_ids, flags, latency_ms, tokens_in, tokens_out}`

## Citations
Products: `{product_id, field, value, quote}` · reviews: `{product_id, review_id,
value, quote}`. Answers cite inline `[p1]` / `[r12]` matching each citation `marker`.

## B3 must handle
`ok:false` tool results (agent still answers); `llm_fallback` / `mock_llm` flags
(offline); `status:"refused"` checkout over the $2,000 session cap; simulated price history;
`run_turn(stream=True)` returns `{"type":"stream","events":<generator>}`.

Self-test: `python -m app.<module> --selftest`
