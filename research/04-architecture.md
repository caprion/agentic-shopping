# 04 — End-to-End Technical Architecture: Agentic Shopping Prototype

**Scope**: a ~2-week, one-VM prototype that a small team (2–3 engineers) can build and demo live. Target host: this box — **2 vCPU, 8 GB RAM, Ubuntu 24.04, Python 3.12.3, no GPU**. The prototype must survive a live demo with no internet, no LLM key, and a partial catalogue. Everything below is a concrete, implementable design, not a wishlist.

---

## 0. Decisions at a glance (the opinionated spine)

| Axis | Choice | Why (short) | What breaks if wrong |
|---|---|---|---|
| Storage | **SQLite + FTS5** (WAL), one file | Zero-ops, embedded, native BM25 | Concurrency beyond ~50 writes/s; no server-side geo |
| Vectors | **LanceDB** (embedded, on-disk ANN) | No service, disk-backed, shares Python process | API churn between 0.x versions — pin it |
| Text embedder | **BAAI/bge-small-en-v1.5** (English) + **intfloat/multilingual-e5-small** (Hinglish) | 384-dim, CPU-cheap, best quality/byte | English-only model mangles Devanagari code-switch |
| Image embedder | **CLIP ViT-B/32** (`sentence-transformers/clip-ViT-B-32`) | 512-dim, CPU ~40 ms/img | Zero-shot "similar" quality below ViT-L |
| Reranker | **cross-encoder/ms-marco-MiniLM-L-6-v2** | 80 MB, ~12 ms/pair | Too slow if fed >100 candidates |
| LLM | **Remote (DeepSeek/MiniMax), provider-agnostic client + MockLLM fallback** | Keys already on box; mock keeps demo alive offline | Any provider-specific lock-in kills the offline fallback |
| ASR | **whisper.cpp `ggml-base` multilingual (142 MB)** | Handles Hinglish, CPU real-time-ish | English-only models drop Hindi product names |
| OCR | **RapidOCR** (onnxruntime) | ~100 ms/img, no Paddle/Tesseract pain | Dense scans slow it; fine for price tags/screenshots |
| Web | **FastAPI + Vite React SPA**, SSE streaming | One Python process + static files | Next.js would add a Node runtime + RAM we don't have |
| Cache | **LRU + disk + LanceDB-as-cache** | Embeddings computed once, never re-encoded | Memory blow-up if cache misses at request time |

---

## 1. Product surface definition (what the demo *does*)

The demo is **"Aria"**, an India-flavoured conversational shopping agent. It lives in one browser tab. Below are the end-to-end user stories the build must satisfy, in priority order. Every story has a concrete **demo script** (these double as eval tasks).

### Personas (from research/05)
- **P1 — "Arjun"** (Tier-2, price-sensitive): *"mere bhai ke liye gift under ₹3000, delivery Pune by Friday, wo photography pasand karta hai."*
- **P2 — "Priya"** (value+quality): *"noise-cancelling headphones under ₹5000 with at least 4.5 stars and a year warranty."*
- **P3 — "Rohan"** (visual): uploads a photo of a sneaker → *"show me similar under ₹4000."*
- **P4 — "Meera"** (comparer): *"compare these three washing machines on warranty, energy rating, and price."*
- **P5 — "Vikram"** (Buy-for-Me): *"find me a Japanese chef's knife (Gyuto 210mm), not in your catalogue, under ₹6000."*

### US-1 · Text chat (the spine)
Given a free-text query, Aria extracts structured constraints, retrieves, ranks, and answers with a **ranked list of ≤5 products**, each with **citations** — a `[P#id]` tag and quoted attribute/review spans. Clicks on a citation open the product card and highlight the source attribute or review line.

### US-2 · Voice (ASR)
User taps mic → browser MediaRecorder captures 16 kHz mono WAV (≤8 s) → POST `/voice` → whisper.cpp transcribes → transcript is fed into the *same* chat pipeline. The transcript is shown verbatim (so ASR errors are visible, not silently corrected) and Aria answers in the user's language.

### US-3 · Image upload (visual search + OCR)
Upload → two parallel paths: (a) **CLIP** embed → ANN over product images → "visually similar"; (b) **RapidOCR** → extract brand/model/price text → feed into structured search. Result merges both. *"Show me similar under ₹4000"* uses the visual path + a price filter.

### US-4 · Vernacular / Hinglish
Code-mixed queries (`"bhai ke liye gift ₹3000 ke andar"`, `"accha wala mixer grinder"`) are routed to the multilingual embedder + the LLM is instructed to reply in the user's registered language (Hindi/Hinglish/English). No separate translation step — the models are multilingual natively.

### US-5 · Conversational search → constraints → candidates → ranking → cited answer
This is the core loop (detailed in §2). The user can refine in follow-up turns (*"kuch sasta dikhao"* → price ceiling drops 25% → re-run). Aria **says what it changed** ("I filtered to <₹3000 and brand in {…}") — transparency is the demo's trust signal.

### US-6 · Compare 2–3 items on user-chosen axes
`compare_products([a,b,c], axes=["warranty","energy_rating","price"])` returns a side-by-side table **built only from DB attributes**, with a one-line verdict per axis. No invented numbers — if an attribute is missing, the cell says "not listed", never a guess.

### US-7 · Cart → checkout (mock) → order → post-purchase
`add_to_cart` → `view_cart` → `checkout_cart(payment_method="mock")` creates an `orders` row with `status="placed"` and a mock payment id. Then `track_order` (mock statuses advance on a timer) and `return_order(reason)` complete the loop. **No real money, no real gateway.**

### US-8 · Buy for Me (item NOT in catalogue)
Aria calls `web_search_external(query, budget)` (Tavily/Parallel), fetches candidate pages, extracts price/attributes, verifies the seller is in a tiny allowlist of sandbox merchants, and "transacts" against a **mock checkout page** inside a Playwright sandbox with a fake card. The result is an order with `source="external"` and a clear "SIMULATED — no real charge" banner. This is the demo's *differentiator* moment.

### US-9 · Personalization
On login (or after 3 events), Aria builds a **user profile vector** from history; every ranking is re-scored with a personalization term and the top result carries an explanation: *"Ranked #1 because you bought 2x Sony audio last month and it's ₹2,499 (under your usual ₹3,000)."* Cold-start: generic ranking + popularity prior, with explicit "tell me what you like" prompts.

**Success criterion for the whole demo**: a cold visitor can complete P1→US-7 (gift under ₹3000 → cited answer → cart → mock checkout → track) in **< 90 seconds**, live, on this VM.

---

## 2. Reference architecture

```
                          ┌──────────────────────────────────────────────────────────┐
                          │                     BROWSER (Vite React SPA)              │
                          │   chat UI · mic (MediaRecorder) · image upload · cards    │
                          └───────────────▲───────────────────────────┬────────────────┘
                                          │ SSE (tokens) + JSON        │ POST /chat /voice /image /cart
                          ┌───────────────┴───────────────────────────▼────────────────┐
                          │                 FastAPI (uvicorn, 1 worker)                 │
                          │  ┌───────────┐ ┌────────────┐ ┌──────────────────────────┐  │
                          │  │ /voice    │ │ /image     │ │ /chat   SSE stream       │  │
                          │  │ whisper   │ │ CLIP+OCR   │ │  → AgentLoop             │  │
                          │  └───────────┘ └────────────┘ └───────────┬──────────────┘  │
                          └────────────────────────────────────────────┼───────────────┘
                                                                    ┌──▼───────────────────────────┐
                                                                    │        AGENT LAYER            │
                                                                    │  plan→tool→observe loop (≤8)  │
                                                                    │  tools/ · guardrails/ · mem/  │
                                                                    │  LLMClient (DeepSeek|MiniMax  │
                                                                    │  | MockLLM)                   │
                                                                    └───┬───────────────┬───────────┘
                                        tool calls ┌───────────────┬────▼────┐   ┌─────▼──────────────┐
                                        ┌──────────▼───────────┐  │ RETRIEVAL│   │ CATALOGUE + GRAPH │
                                        │ web_search_external  │  │  hybrid  │   │  SQLite + FTS5    │
                                        │ (Tavily/Parallel)    │  │  RRF →   │   │  LanceDB (vectors)│
                                        │ Playwright sandbox   │  │  cross-  │   │  product_edges    │
                                        └──────────────────────┘  │  encoder │   └───────────────────┘
                                                                   │  rerank  │
                                                                   └──────────┘
```

### 2.1 Ingestion / ETL (raw → normalized)
Sources (see research/03): **Olist** (orders/customers/reviews — the transaction spine) + **Amazon Reviews 2023** (McAuley) for product/review/attribute corpus + **Fashion Product Images (Myntra)** for image-rich SKUs + a small hand-seeded category taxonomy.
Pipeline (offline, idempotent, run by `make ingest`):
1. **Download** → `data/raw/` (subset-first, see §5 fallbacks).
2. **Normalize**: lowercase, strip units ("₹3000"/"Rs 3000"/"3000"→ one numeric), map currencies, unify brand spellings via a small alias table, parse size/colour/storage out of titles into a facet.
3. **Entity resolution** (blocked, not O(n²)): block by `category_id + brand`; within a block, compare normalized-title bigram Jaccard (>0.6) **and** bge-small cosine (>0.92) → merge into one `product_id` (keep both source URLs). Variant grouping: same entity, differing only in a single facet → assign shared `product_group_id`.
4. **Dedupe**: keep the richest record (most attributes, most reviews) as canonical; write a `dedupe_map` for audit.
5. **Embed**: encode title+description (bge-small **and** multilingual-e5) and first image (CLIP) → LanceDB. Compute price/discount/rating columns.
6. **Graph**: build `product_edges` from co-purchase/co-view (Olist order co-occurrence), category parent links, and variant-of links.

### 2.2 Retrieval stack (per query)
1. **Constraint extraction** — the LLM (or a deterministic fallback) emits a `SearchQuery` (JSON, validated by Pydantic): `{free_text, brand, category, price_min, price_max, rating_min, attributes{...}, must, exclude, sort, k}`. This is the "structured query" the whole stack consumes.
2. **Pre-filter** — SQL `WHERE` on price/rating/stock/category/attributes (indexed columns). Both lexical and dense paths respect it (dense does filter-then-ANN or post-filter).
3. **Candidate generation** (three parallel sources):
   - **Lexical**: SQLite **FTS5** `MATCH` → BM25 → top-200.
   - **Dense**: LanceDB ANN on the query embedding (matching model) → top-200 (cosine).
   - **Graph**: traverse `product_edges` (also-bought/also-viewed, category siblings) from the user's recently-interacted products → top-100.
4. **Hybrid fusion** — **Reciprocal Rank Fusion (RRF)** with `k=60` merges the three lists → top-100. RRF needs no score calibration, which is why it beats linear-blend for a 2-week build.
5. **Rerank** — cross-encoder scores top-50 (query, product-title+attributes+top-review) → top-10.
6. **Personalization re-rank** — blend cross-encoder score with user-profile-vector cosine + behaviour features (category affinity, recency) → final top-5, each with an explanation string.

### 2.3 Agent layer
- **Loop**: classic ReAct `plan → call tool → observe → …` with a hard cap of **8 tool calls/turn** and **60 s** wall-clock (then it must answer with what it has). Tools are declared as JSON-schema functions; the loop is model-agnostic.
- **Tool catalogue** (all validated + audited): `search_products`, `get_product`, `get_reviews`, `compare_products`, `get_price_history`, `add_to_cart`, `remove_from_cart`, `view_cart`, `checkout_cart`, `track_order`, `return_order`, `web_search_external`, `web_fetch_external`.
- **Guardrails** (a middleware every tool call passes through, pre **and** post):
  - *Budget*: hard session cap (default ₹5000), reject cart total > cap.
  - *Stock/price truth*: stock status and price may only come from DB; LLM text is checked against the row.
  - *Brand/authorisation*: only in-stock items from allowlisted sellers; no grey-market claims.
  - *Hallucination check*: post-answer, a `verify_citations` pass re-reads the DB for every cited claim; unverifiable claims are stripped or flagged.
- **Session state & memory**: `sessions` row + an in-process `SessionState` (constraints, cart ref, last products) serialized to JSON; memory of past turns kept in the prompt (rolling window, last ~6 turns) + user profile persisted.
- **Streaming**: SSE emits `{type: "token"|"tool"|"citation"|"done"}` so the UI shows tokens as they arrive and the tool being called in real time.

### 2.4 Personalization
`user_profile = α·(mean of item vectors from history) + (1-α)·(recency-weighted item vectors)`, α=0.3; plus scalar features (avg spend, category affinity, preferred brand) in a small feature row. Cold start: α→0 and features default to population priors. Re-ranking is a linear blend with a fixed, logged weight, so explanations are deterministic ("because you bought …").

### 2.5 Evaluation harness (offline + live)
- **Golden set**: ~500 shopping-intent queries from Amazon **Shopping Queries Dataset (ESC)** + 50 hand-written India agentic tasks (the §1 personas), each with expected end-state (correct product id, stayed in budget, correct axis comparison).
- **Retrieval metrics** (`ranx`): **Recall@10, nDCG@10, MRR** on the query→product labels.
- **Answer quality**: **groundedness** = fraction of cited claims verified true against DB; **citation precision/recall**; **faithfulness** = NLI (cross-encoder) between answer and retrieved context; **attribute accuracy** (price/brand/rating exact-match).
- **Task success rate**: deterministic end-state checker (did the final cart match the golden answer, within budget?) — no LLM-judge required, keeps it reproducible.
- **Cost/latency**: tokens-in/out, `$`/session, p50/p95 per stage, from the trace table.

### 2.6 Observability
A `traces` table + **structlog** JSON logs. Per turn: `session_id, turn_id, ts, step, tool, tool_args, tool_result_summary, tokens_in, tokens_out, latency_ms, llm_model, citations, guardrail_flags, status`. Skip OpenTelemetry — one VM, one process; a `traces` table + `jq` is enough and zero infra.

### 2.7 Safety / policy — what Aria must **never** do
1. Misrepresent stock ("in stock" when `stock_status=0`).
2. Invent reviews or attributes not in the DB.
3. Fake discounts (discount is computed `1 - price/mrp`, never LLM text).
4. Claim a price/rating not in the row.
5. Execute any real payment or non-idempotent external action (`dry_run` flag blocks it).
6. Exceed the session budget.
7. Recommend a product without a verifiable citation.
**Enforcement** = the guardrail middleware + the post-answer `verify_citations` pass (re-read DB, regex-check prices/brands, NLI faithfulness), run on **every** answer before it streams its final `done`.

---

## 3. Technology choices (exact, with trade-offs)

### 3.1 SQLite + FTS5 **vs** Postgres
**Pick SQLite.** One file, zero ops, WAL mode, `-mmap`, reads at ~100k rows/s — trivially enough for ≤100k products. FTS5 gives BM25 natively with `bm25()` ranking and `NEAR`/prefix. Postgres + `pgvector` would add a 300 MB+ resident daemon and an ops surface for zero benefit at this scale. **What breaks**: SQLite is single-writer; ingestion is a single offline writer (fine). If the team later needs server-side concurrent writes or `pg_trgm` fuzzy search, migrate then — the data model is deliberately Postgres-portable (DDL below is ANSI-ish, no SQLite-only types except FTS5/triggers).

### 3.2 LanceDB **vs** FAISS **vs** pgvector
**Pick LanceDB** (`lancedb`, pin `0.17.x`). It's embedded (runs in the Python process), on-disk (IVF-PQ + HNSW options), supports **filtering during ANN** (critical: price/rating pre-filters), and stores metadata with vectors (join key = `product_id`). FAISS is in-RAM and needs you to manage the index/persistence yourself and do post-filtering manually. pgvector requires Postgres (rejected in 3.1). **What breaks**: LanceDB 0.x has API churn between minors — pin the version and wrap all calls in one `VectorStore` module so an upgrade touches one file.

### 3.3 Embedding model — benchmark (CPU, 2 vCPU)

| Model | dim | params | disk | MTEB avg (approx) | ~query latency | verdict |
|---|---|---|---|---|---|---|
| all-MiniLM-L6-v2 | 384 | 22.7M | 80 MB | ~56.3 | 3 ms | fastest, weakest |
| **BAAI/bge-small-en-v1.5** | 384 | 33.4M | 133 MB | **~62.6** | 6 ms | **English winner** |
| intfloat/e5-small-v2 | 384 | 33M | 133 MB | ~61.9 | 6 ms | needs `query:/passage:` prefixes |
| thenlper/gte-small | 384 | 33M | 130 MB | ~61.4 | 6 ms | close 2nd |
| intfloat/multilingual-e5-small | 384 | 118M | 471 MB | ~59.9 (multi) | 8 ms | **vernacular path** |

**Decision**: **bge-small-en-v1.5** for English product search; **multilingual-e5-small** for Hinglish/vernacular. Both are 384-dim so they share one LanceDB table with a `model_id` column (query is routed by a cheap language check: presence of Devanagari or common Hindi tokens → multilingual). Reject bge-m3/e5-large (1024-dim, too slow/heavy on CPU) and MiniLM-L6 (quality gap for the effort). **What breaks**: if the demo stays English-only, drop the multilingual model and save 471 MB RAM; if Hinglish is mandatory, don't skip it — bge-small-en will transliterate badly and hurt recall on code-switch.

### 3.4 Reranker
**Pick cross-encoder/ms-marco-MiniLM-L-6-v2** (80 MB, ~12 ms/pair) over top-50 (≈0.6 s). For tight latency, drop to `cross-encoder/ms-marco-TinyBERT-L-2-v2` (17 MB, ~5 ms/pair). Batch pairs in one `predict` call (not a loop). **What breaks**: feeding it >100 candidates turns 0.6 s into seconds; always cap inputs.

### 3.5 LLM access — provider-agnostic + offline fallback
This box already has: **`DEEPSEEK_API_KEY`** (`https://api.deepseek.com/anthropic`, Anthropic Messages-compatible), **`MINIMAX_API_KEY`** / `STAGEHAND_MINIMAX_API_KEY` (`api.minimax.io`), and Azure Managed Identity (Key Vault `claw-agent` → `DEEPSEEK-API-KEY`). **Do not** use LiteLLM — it's a heavy abstraction with version churn. Write a thin **`LLMClient`** (~120 lines, `httpx`) exposing one interface:

```python
@dataclass
class LLMResult:
    text: str
    tool_calls: list[ToolCall]   # parsed from the provider's tool-call format
    tokens_in: int; tokens_out: int; model: str; latency_ms: int

class LLMClient(Protocol):
    async def chat(self, messages, tools, *, max_tokens=600) -> LLMResult: ...
```

Three backends implement it: `DeepSeekClient`, `MiniMaxClient` (both just a different `base_url`+header on the same Anthropic-compatible shape), and **`MockLLM`** — a deterministic table-driven stub that maps scripted intents (P1–P5) to tool-call sequences, so the entire demo runs **with zero network and zero keys**. A fourth, optional `LocalLLM` (llama.cpp `Qwen2.5-3B-Instruct` Q4) is the "truly offline but slow (~5–12 tok/s)" last resort — acceptable for canned turns, not for live latency. **What breaks**: any code path that calls an SDK directly instead of through `LLMClient` silently kills the offline demo.

### 3.6 ASR
**Pick whisper.cpp `ggml-base` multilingual (142 MB)** invoked as a subprocess (`whisper-cli -m ggml-base.bin -l auto`), 16 kHz mono WAV in. On 2 vCPU, base ≈ 1–2× realtime → a 6 s clip in ~6–10 s (acceptable if the mic button shows "listening…"). **`ggml-small` multilingual (466 MB)** is higher-accuracy but 3–5× realtime — too slow for live; keep it as an optional batch/offline mode. `tiny` (75 MB) is the emergency fallback. English-only models are **rejected** — they garble Hindi product names ("Mixer Grinder" → "mixer griender" is fine; "Panasonic" in Hinglish is not). **What breaks**: long clips without VAD trim; enforce a browser-side ≤8 s limit.

### 3.7 OCR
**Pick `rapidocr-onnxruntime`** (~100 ms/image, CPU, no Paddle/Tesseract). Used on uploaded images to pull brand/model/price text. Fallback `pytesseract` + `tesseract-ocr` if RapidOCR's model download is blocked. **What breaks**: dense receipt scans — out of scope; we OCR product labels/screenshots only.

### 3.8 CLIP image embeddings
**Pick `sentence-transformers/clip-ViT-B-32`** (512-dim, ~350 MB). Encode product images once at ingest (~40 ms/img → 20k images ≈ 20 min). Store in a **second** LanceDB table (`image_embeddings`). Query: upload → CLIP → ANN → "similar" + price filter. **What breaks**: ViT-B/32 misses fine detail vs ViT-L/14 (768-dim, ~900 MB, ~3× slower) — accept the tradeoff; the OCR path covers exact-model matching.

### 3.9 Framework: FastAPI + Vite React **vs** Next.js
**Pick FastAPI (uvicorn, 1 worker) + Vite React 18 SPA, served as static files by FastAPI.** One Python process owns everything (CPU-heavy embedding/ASR stay in-thread-pool). SSE endpoint for token streaming; no WebSocket needed. **Reject Next.js**: it adds a second long-lived Node runtime (~200–400 MB RSS) and SSR complexity we don't need — the SPA is fully client-rendered. **What breaks**: SSR/SEO matters zero for a demo; the only cost is one `npm run build` step.

### 3.10 Caching
- **Embeddings**: computed once at ingest, persisted in LanceDB — *never* re-encoded at request time.
- **`functools.lru_cache`** on: cross-encoder (keyed by query hash), LLM tool-result lookups (keyed by (model, query, k)), price-history.
- **`cachetools.TTLCache`** (TTL 300 s) for `web_search_external` results.
- **Disk cache** (`data/cache/`) for resized images and OCR results.
- **Response cache** (optional, TTL 120 s) for byte-identical queries, disabled in eval mode.

---

## 4. Data model (SQLite DDL)

```sql
PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;

CREATE TABLE categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES categories(id),
  path TEXT NOT NULL,                 -- e.g. 'electronics/audio/headphones'
  level INTEGER NOT NULL
);

CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT,
  brand TEXT,
  category_id INTEGER REFERENCES categories(id),
  product_group_id INTEGER,           -- variant grouping (NULL = parent/standalone)
  price_inr REAL NOT NULL,            -- current sale price
  mrp_inr REAL,                       -- list price (for discount computation)
  currency TEXT DEFAULT 'INR',
  rating_avg REAL, rating_count INTEGER,
  stock_status INTEGER DEFAULT 0,     -- 0 out, 1 in, 2 low
  seller_id INTEGER, url TEXT, image_url TEXT,
  attrs_json TEXT,                    -- denormalized facets for display
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_products_cat  ON products(category_id);
CREATE INDEX idx_products_brand ON products(brand);
CREATE INDEX idx_products_price ON products(price_inr);
CREATE INDEX idx_products_rating ON products(rating_avg);
CREATE INDEX idx_products_group ON products(product_group_id);

CREATE TABLE variants (
  id INTEGER PRIMARY KEY,
  product_group_id INTEGER NOT NULL,
  product_id INTEGER REFERENCES products(id),  -- concrete SKU
  facet TEXT NOT NULL,                          -- 'size'|'color'|'storage'
  value TEXT NOT NULL,
  price_delta REAL DEFAULT 0,
  sku TEXT, stock_status INTEGER DEFAULT 0, image_url TEXT
);

CREATE TABLE attributes (              -- canonical facet registry
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, unit TEXT, is_faceted INTEGER DEFAULT 1
);
CREATE TABLE product_attributes (      -- normalized EAV (filterable)
  product_id INTEGER REFERENCES products(id),
  attribute_id INTEGER REFERENCES attributes(id),
  value_text TEXT, value_num REAL, value_unit TEXT,
  source TEXT,                          -- 'title'|'spec_sheet'|'seller'
  PRIMARY KEY (product_id, attribute_id)
);

CREATE TABLE reviews (
  id INTEGER PRIMARY KEY,
  product_id INTEGER REFERENCES products(id),
  user_id INTEGER, rating INTEGER, title TEXT, body TEXT,
  verified_purchase INTEGER DEFAULT 0, helpful_votes INTEGER DEFAULT 0,
  review_date TEXT, lang TEXT
);
CREATE INDEX idx_reviews_prod ON reviews(product_id);

CREATE TABLE users (
  id INTEGER PRIMARY KEY, external_id TEXT UNIQUE,
  name TEXT, pincode TEXT, preferred_lang TEXT DEFAULT 'en',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id),
  channel TEXT DEFAULT 'chat', created_at TEXT DEFAULT (datetime('now')),
  state_json TEXT                       -- constraints, cart ref, last products, turn history
);

CREATE TABLE events (                  -- behaviour log (implicit feedback)
  id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now')),
  session_id TEXT, user_id INTEGER, event_type TEXT,  -- view|add_to_cart|purchase|search|click
  product_id INTEGER, query TEXT, payload_json TEXT
);
CREATE INDEX idx_events_user ON events(user_id);

CREATE TABLE carts ( id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT DEFAULT 'open', created_at TEXT DEFAULT (datetime('now')) );
CREATE TABLE cart_items (
  id INTEGER PRIMARY KEY, cart_id INTEGER REFERENCES carts(id),
  product_id INTEGER REFERENCES products(id), variant_id INTEGER,
  qty INTEGER DEFAULT 1, unit_price REAL, added_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY, user_id INTEGER, cart_id INTEGER,
  status TEXT DEFAULT 'placed',          -- placed|confirmed|shipped|delivered|returned
  total_inr REAL, payment_method TEXT DEFAULT 'mock', payment_id TEXT,
  shipping_json TEXT, source TEXT DEFAULT 'catalogue',  -- 'catalogue'|'external'
  placed_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE order_items (
  id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id),
  product_id INTEGER, variant_id INTEGER, qty INTEGER, unit_price REAL, status TEXT DEFAULT 'confirmed'
);

CREATE TABLE product_edges (           -- catalogue graph
  src_product_id INTEGER, dst_product_id INTEGER,
  edge_type TEXT,                      -- also_bought|also_viewed|same_category|variant_of|parent_of
  weight REAL DEFAULT 1.0, source TEXT
);
CREATE INDEX idx_edges_src ON product_edges(src_product_id, edge_type);

CREATE TABLE traces (                  -- per-turn observability
  id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now')),
  session_id TEXT, turn_id INTEGER, step INTEGER,
  tool TEXT, tool_args_json TEXT, tool_result_summary TEXT,
  tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER,
  llm_model TEXT, citations_json TEXT, guardrail_flags_json TEXT, status TEXT
);
CREATE INDEX idx_traces_session ON traces(session_id);

-- FTS5 (lexical/BM25)
CREATE VIRTUAL TABLE products_fts USING fts5(
  title, description, brand, content='', tokenize='unicode61'
);
CREATE TRIGGER products_ai AFTER INSERT ON products BEGIN
  INSERT INTO products_fts(rowid,title,description,brand) VALUES (new.id,new.title,new.description,new.brand); END;
CREATE TRIGGER products_ad AFTER DELETE ON products BEGIN
  INSERT INTO products_fts(products_fts,rowid,title,description,brand) VALUES('delete',old.id,old.title,old.description,old.brand); END;
CREATE TRIGGER products_au AFTER UPDATE ON products BEGIN
  INSERT INTO products_fts(products_fts,rowid,title,description,brand) VALUES('delete',old.id,old.title,old.description,old.brand);
  INSERT INTO products_fts(rowid,title,description,brand) VALUES (new.id,new.title,new.description,new.brand); END;
```

**Vectors live in LanceDB** (two tables), not SQLite — SQLite stores only metadata; the join key is `product_id`:

```
lancedb/embeddings/text      : product_id (int64), model_id (str), vector (384f)
lancedb/embeddings/image     : product_id (int64), model_id (str), vector (512f)
lancedb/embeddings/user      : user_id (int64),  vector (384f)
```

The user profile vector is a weighted mean of the text vectors of items in `events` (purchase/view) — recomputed nightly/on-demand, no separate table beyond `user` embeddings.

---

## 5. Repo layout + build order + risks

### 5.1 Repo layout
```
agentic-shopping/
├─ backend/
│  ├─ pyproject.toml            # pinned deps (see below)
│  ├─ app/
│  │  ├─ main.py                # FastAPI app, static mount, SSE
│  │  ├─ api/                   # routes: chat, voice, image, cart, orders
│  │  ├─ core/                  # config.py, db.py, cache.py, logging.py
│  │  ├─ llm/                   # client.py (protocol), deepseek.py, minimax.py, mock.py, local.py
│  │  ├─ agents/                # loop.py, state.py, tools/*.py, guardrails/*.py
│  │  ├─ retrieval/             # fts.py, dense.py, image.py, fusion.py (RRF), rerank.py, graph.py
│  │  ├─ personalization/       # profile.py, rerank.py
│  │  ├─ ingestion/             # etl.py, normalize.py, dedupe.py, embed.py, graph.py
│  │  └─ eval/                  # golden/, metrics.py, harness.py
│  ├─ models/                   # (gitignored) bge, e5, CLIP, cross-encoder, ggml-base.bin
│  └─ tests/
├─ frontend/                    # Vite + React 18 SPA
├─ scripts/                     # download_data.sh, seed.sh, build_indexes.sh, run_demo.sh
├─ data/{raw,processed,cache}/
├─ research/                    # 01–06 (this file)
└─ Makefile                     # make ingest | index | serve | eval | demo
```

**Pinned Python deps** (verify current at build time, but pin these): `fastapi>=0.115,<0.116`, `uvicorn>=0.32,<0.33`, `sentence-transformers>=3.3,<3.4`, `lancedb>=0.17,<0.18`, `torch>=2.5,<2.6` (CPU), `transformers>=4.46,<4.47`, `rapidocr-onnxruntime>=1.3,<1.4`, `httpx>=0.27,<0.28`, `pydantic>=2.9,<2.10`, `structlog>=24`, `ranx>=0.3,<0.4`, `cachetools>=5`, `playwright>=1.48`. Frontend: `react@18`, `vite@5`.

### 5.2 Build order (10 working days) — with acceptance criteria

| Day | Work | Acceptance criteria (demo-able artifact) |
|---|---|---|
| **1** | Repo, Makefile, `download_data.sh`, SQLite DDL + FTS5, seed Olist + Amazon subset (products/reviews/images/taxonomy) | `make seed` loads ≥20k products, ≥100k reviews; FTS5 `MATCH 'headphones'` returns ranked rows; all tables open |
| **2** | Ingestion ETL + normalization + entity resolution + dedupe + variant grouping | `make ingest` idempotent; known dupes merge to one id; variants share `product_group_id`; attributes normalized to one currency/unit |
| **3** | Embeddings (bge-small + multilingual-e5 + CLIP) → LanceDB; hybrid RRF + cross-encoder rerank; graph edges | Offline eval on golden set: **Recall@10 ≥ 0.80, nDCG@10 ≥ 0.60, MRR ≥ 0.70**; query "headphones under ₹5000" returns sensible top-5 |
| **4** | Agent loop + tool catalogue + `LLMClient` (DeepSeek/MiniMax/**Mock**) + constraint extraction + cited answer generation | Text query → structured `SearchQuery` → ranked, cited answer in **< 6 s** (p95); `[P#id]` citations resolve |
| **5** | Guardrails + `verify_citations` + session state + SSE streaming | Injected hallucinated price is caught & corrected; tokens + tool names stream; session persists constraints across turns |
| **6** | Multimodal: whisper.cpp ASR + image upload (CLIP visual search) + RapidOCR + Hinglish routing | P1 Hinglish voice → transcript → cited answer; sneaker photo → "similar under ₹4000"; OCR extracts brand/price from a screenshot |
| **7** | Cart/checkout/order/track/return + `compare_products` + personalization re-rank + explanation | P4 full happy path (mock checkout → order → track); 3-way compare shows "not listed" for missing attrs; personalized top-1 has a truthful explanation |
| **8** | Buy for Me (sandboxed web_search_external + Playwright mock checkout) + harden price/claim guardrails | P5: external Gyuto found via Tavily, price verified, "SIMULATED" order created, budget enforced, `dry_run` blocks real writes |
| **9** | Eval harness end-to-end + traces/dashboard | Golden set runs as a script; reports Recall/nDCG/MRR, groundedness, task success rate, latency p50/p95, $/session |
| **10** | Polish + 5 persona demo scripts + README + fallback drills + freeze versions | `make demo` runs cold → P1 completes in < 90 s; **no-internet** and **no-key** fallbacks both verified working |

### 5.3 Risk register

| Risk | L | I | Mitigation |
|---|---|---|---|
| **No internet at runtime** | M | H | Pre-download everything (models, data, whisper binary, Playwright browsers); `MockLLM` + cached web-search results; `make offline` flag |
| **No LLM API key** | M | H | `MockLLM` drives scripted P1–P5 tool-loops; optional llama.cpp `Qwen2.5-3B` local fallback |
| **Dataset download too big** | M | M | Subset-first (Olist ≈ 120 MB; Amazon via HF datasets-server streaming `raw_review_*` first N rows); if blocked, synthetic generator (seeded Faker + template SKUs) |
| **CPU embedding/rerank too slow** | M | M | Precompute at ingest; cap rerank to 50; TinyBERT fallback; cache aggressively |
| **RAM pressure (torch + whisper + LanceDB)** | M | M | Load embedder+CLIP once, share; whisper as subprocess (freed after each call); cap uvicorn to 1 worker; `free -h` budgeted at ~5 GB peak |
| **Hinglish ASR/embedding quality** | M | M | Use multilingual models (base + e5-multilingual); show raw transcript; fall back to text input in UI |
| **LanceDB API churn** | L | M | Pin `0.17.x`; isolate behind `VectorStore` module |
| **DeepSeek/MiniMax latency variance** | M | M | Timeout 20 s + retry + `MockLLM` automatic fallback on timeout |
| **HF rate limits on model download** | M | M | Cache models to `models/` (gitignored) on first run; `HF_HUB_ENABLE_HF_TRANSFER=1`; mirror to local file |

---

## 6. Cost / latency budget

**Latency target**: full text turn **p95 < 6 s**; voice +4–8 s; image +1.5–3 s. Per-stage budget (one turn):

| Stage | Budget | Notes |
|---|---|---|
| Constraint extraction (LLM) | 0.5 s | 1 small call, `max_tokens≈300` |
| Pre-filter + candidate gen (FTS5+dense+graph) | 0.4 s | 3 parallel; LanceDB ANN on 384-dim is ms-scale |
| RRF + cross-encoder rerank (top-50) | 0.7 s | batched `predict` |
| Personalization re-rank | 0.1 s | vector dot + features |
| Answer generation (LLM, streamed) | 2.5 s | first token < 1 s; `max_tokens≈500` |
| `verify_citations` + guardrails | 0.5 s | DB re-reads + regex + NLI |
| **Total** | **~4.7 s** | + margin → p95 < 6 s |

**Token budget per turn**: system ~800 + retrieved context (5 products × ~120 tokens attrs + 3 reviews × ~80 tokens) ≈ 1,400 + history ~1,200 ≈ **~3,400 in**, **~300–500 out**. Keep rolling history to last 6 turns so a session stays **< ~15k tokens** total.

**Cost**: DeepSeek ≈ $0.27/M in, $1.10/M out (cache-hit $0.07/M). A full 10-turn session ≈ 34k in + 4k out ≈ **~$0.015**. 100 demo sessions/day ≈ **~$1.50**. Embedding/CLIP/ASR are local = **$0**. Web search via Tavily/Parallel is metered — cap `web_search_external` to ≤3 calls/session.

**How to stay under budget**: (1) never re-encode at request time; (2) cap candidates (200/100/50/10); (3) cap tool calls at 8/turn; (4) stream generation; (5) run CPU work in the thread pool, keep IO async; (6) `MockLLM` path means the whole demo is also runnable at **$0 and < 2 s/turn**.

---

## Summary (10 lines)

1. One FastAPI+SQLite+LanceDB Python process on this 2-vCPU/8 GB VM serves a React SPA, with DeepSeek/MiniMax as the LLM and a deterministic MockLLM so the demo survives no-internet/no-key.
2. The product is "Aria": chat/voice/image/Hinglish → constraint extraction → hybrid retrieval → cited answers → compare → mock checkout → order → track/return → sandboxed Buy-for-Me → personalization.
3. Retrieval = SQLite FTS5 (BM25) + LanceDB dense (bge-small-en-v1.5, multilingual-e5-small, CLIP ViT-B/32) + catalogue graph, fused with RRF then a cross-encoder reranker.
4. The agent is a tool-calling loop (8 tools, ≤8 calls/turn) with pre/post guardrails and a `verify_citations` pass so stock/price/review/discount claims can only come from the DB.
5. Ingestion does blocked entity resolution + variant grouping on Olist + Amazon Reviews 2023 + Myntra images, all offline and idempotent.
6. Data model: products, variants, attributes (EAV + JSON), reviews, users, sessions, events, carts, orders, product_edges, traces, FTS5, plus three LanceDB vector tables joined by `product_id`.
7. Evaluation: Recall@10/nDCG@10/MRR via `ranx`, groundedness/faithfulness via DB-verification + NLI, and deterministic end-state task success — not an LLM judge.
8. Build order is 10 days with per-day acceptance criteria, from seed → ingest → retrieval → agent → guardrails → multimodal → checkout/personalization → Buy-for-Me → eval → polish.
9. Budget: text turn p95 < 6 s (~4.7 s stage budget), ~3.4k tokens in / ~400 out, ~$0.015 per full session, ~$0 with the mock path.
10. The moat to demo is **trust-by-verification**: every answer ships with real, re-verifiable citations, which is exactly what Amazon's own gaps (hallucinated attributes, fake-review risk) leave open.
