You are a staff engineer / architect. Task: design the **end-to-end technical architecture** for an agentic shopping prototype that a small team can build in ~2 weeks on ONE Linux VM (2 vCPU, 8 GB RAM), then write it up so it can be implemented directly.

Deliverables:
1. **Product surface definition**: what the demo actually does, end to end, in concrete user stories:
   - Text chat, voice (ASR), image upload (visual search / "show me similar"), multilingual/vernacular query (Hinglish example).
   - Conversational product search → constraint extraction → candidate generation → ranking → **answer with citations to real product attributes/reviews** → compare 2-3 items on user-chosen axes → cart → checkout (mock) → order → post-purchase (track/return).
   - "Buy for Me" style: find an item NOT in the catalogue, on the open web, and transact via an agent (with sandbox/mock, no real money).
   - Personalization: user history → re-ranking + explanation.
2. **Reference architecture** with concrete components and the data flow:
   - Ingestion/ETL (raw → normalised catalogue), entity resolution & attribute normalisation, dedupe/variant grouping.
   - **Retrieval stack**: lexical (SQLite FTS5 / BM25), dense (sentence-transformers embeddings + LanceDB), image embeddings (CLIP), hybrid fusion (RRF), cross-encoder/LLM reranker, catalogue-graph traversal (also-bought/also-viewed, category parents), attribute-based filtering (structured → where the agent extracts constraints into a structured query).
   - **Agent layer**: tool-calling loop, tool catalogue (search_products, get_product, get_reviews, compare, cart ops, web_search for external sellers, checkout), guardrails (price/claim verification, hallucination checks, budget constraints, brand/authorisation checks), session state & memory, streaming.
   - **Personalisation**: user profile vector, behaviour features, re-ranking, cold-start handling.
   - **Evaluation harness**: retrieval metrics (Recall@k, nDCG@k, MRR), answer quality (groundedness/attribution, faithfulness), task success rate on agentic shopping tasks, latency & cost per session; a golden test set built from the shopping-queries dataset.
   - **Observability**: trace per turn (tools called, tokens, latency, citations), logging schema.
   - **Safety/policy**: what an agentic shopper must never do (misrepresent stock, invent reviews, fake discounts), and how to enforce with a verification pass.
3. **Detailed technology choices** for the VM-constrained prototype: exact libraries + versions, why, and what breaks. Cover: SQLite + FTS5 vs Postgres, LanceDB vs FAISS vs pgvector, embedding model choice for CPU (all-MiniLM-L6-v2 / bge-small / e5 / gte-small — benchmark them), reranker (cross-encoder small), LLM access (local? API keys available on this box? design a provider-agnostic client with a mock/offline fallback), ASR (whisper.cpp/small?), OCR, CLIP image embeddings, framework (FastAPI + plain React/vanilla UI vs Next), and caching.
4. **Data model**: tables for products, variants, attributes, reviews, users, events, orders, carts, and the derived indexes (embeddings table, FTS table, graph edges) with SQL DDL.
5. **Repo layout** and a **build order** (day-by-day, ~10 working days) with acceptance criteria per day, and **risk register** with mitigations. Include fallback plans for "no internet at runtime", "no LLM API key", "dataset download too big".
6. **Cost/latency budget**: per-turn latency target, token usage, and how to keep a demo session under N seconds.

Be opinionated and specific (exact numbers, exact package names). Output: write `/home/sumit/agentic-shopping/research/04-architecture.md` — 3500-5000 words, with ASCII diagrams, DDL, and the day-by-day plan. End with a 10-line summary.
