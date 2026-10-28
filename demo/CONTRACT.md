# BUILD CONTRACT — "Aria" agentic shopping prototype

**Repo:** `/home/sumit/agentic-shopping`  ·  **Demo root:** `demo/`  ·  **DB:** `demo/data/app.db`

Every builder thread codes against this contract. Do **not** change the DDL in `demo/app/db.py`
(owned by the orchestrator). If you need a new column/table, add it in your own module using
side tables (prefix your table name with your module, e.g. `aspect_cache`), or ask the orchestrator.

## Environment facts (already verified on this box)

- Python 3.12.3, 2 vCPU, 8 GB RAM, no GPU. Disk free ~23 GB.
- Installed: `fastapi 0.115`, `uvicorn`, `pandas 3.0.2`, `pyarrow 24`, `lancedb 0.30.2`,
  `sentence-transformers 5.4.1`, `sqlite3` (FTS5 available).
- Env keys present: `DEEPSEEK_API_KEY`, `MINIMAX_API_KEY`, `PARALLEL_API_KEY`, `TAVILY_API_KEY`.
  **Never print a key. Read from env only.**
- Internet is available at build time. Assume it may be **absent at demo time** → every network
  path must degrade gracefully (see offline fallback below).

## Data already on disk (`demo/data/raw/`)

| File | Content |
|---|---|
| `meta_electronics_0.parquet` | 161k Amazon Electronics products: title, price, average_rating, rating_number, features, description, images{hi_res,large,thumb}, store, categories, details, parent_asin |
| `reviews_electronics_part.jsonl` | raw Amazon reviews: rating, title, text, asin, parent_asin, user_id, timestamp |
| `ratings_electronics.csv` | `user_id,parent_asin,rating,timestamp` (5-core interactions → real user history) |
| `esci_examples.parquet` | Amazon Shopping Queries (ESC) gold set: query → product_id with label E/S/C/I |
| `meta_Musical_Instruments_subset.jsonl`, `*_5core_rating_only.csv` | second category (added by the datasets thread) |

`product_id` = **parent_asin** (entity). `variant_id` = asin.

## Module contracts (implement exactly these signatures)

### `app/embeddings.py`
```python
def get_text_model() -> SentenceTransformer     # BAAI/bge-small-en-v1.5 (384d), lazy-loaded, cached
def get_clip_model()                            # sentence-transformers/clip-ViT-B-32, lazy, optional
def encode_texts(texts: list[str], batch_size=64) -> np.ndarray   # (n,384) float32, L2-normalised
def encode_query(text: str) -> np.ndarray         # (384,)
def embed_products(conn, ids: list[str]) -> None  # upserts into LanceDB table "products"
def search_vectors(vec: np.ndarray, k: int, filt: dict | None = None) -> list[tuple[str, float]]
```
Vector store: **LanceDB** table `products`, columns: `product_id` (str), `vector` (list[float]), `title`, `price`, `rating`, `brand`, `category_main`. Path: `demo/data/lance`. If LanceDB is unavailable, fall back to a numpy brute-force matrix persisted as `.npy` — **the retrieval layer must not care which**.

### `app/retrieval.py`
```python
@dataclass
class Hit:
    product_id: str
    score: float
    lexical_rank: int | None
    dense_rank: int | None
    bm25: float | None

def parse_constraints(text: str) -> dict
    # -> {"free_text": str, "brand": [str], "price_min": float|None, "price_max": float|None,
    #     "rating_min": float|None, "category": str|None, "attributes": {k: v}, "sort": str|None}
    # Must handle "under 3000", "₹3000 se kam", "at least 4.5 stars", "sony", "gaming laptop".
    # Use app/lexicon.py brand/category tables + regex. No LLM required (deterministic, offline-safe).

def hybrid_search(conn, query: str, constraints: dict | None = None, k: int = 20,
                  user_id: str | None = None) -> list[Hit]
    # 1) SQL pre-filter on price/rating/brand/category/stock
    # 2) FTS5 bm25 top-200  + dense ANN top-200  + graph (product_edges co_purchase) top-50
    # 3) fuse with Reciprocal Rank Fusion (k=60), then rerank with cross-encoder
    #    ms-marco-MiniLM-L-6-v2 if downloadable, else skip rerank (log it)
    # 4) optional personalisation re-rank (see app/personalize.py)
    # Must never raise if embeddings/model missing → degrade to lexical only.

def get_product(conn, product_id: str) -> dict | None
def get_reviews(conn, product_id: str, limit: int = 5, min_rating: float | None = None) -> list[dict]
def similar_products(conn, product_id: str, k: int = 8) -> list[dict]
def graph_expand(conn, product_ids: list[str], k: int = 30) -> list[Hit]
```

### `app/personalize.py`
```python
def build_profile(conn, user_id: str) -> dict
    # -> {"user_id","n_events","avg_rating","avg_spend","brand_affinity":{brand:score},
    #     "category_affinity":{cat:score},"recent":[product_id,...]}
def rerank(conn, hits: list[Hit], user_id: str | None) -> list[tuple[Hit, str]]
    # -> [(hit, explanation)]  explanation like "you bought 2 Sony items; ₹2,499 < your usual ₹3,000"
def explain_hit(conn, hit, profile) -> str
```

### `app/llm.py`
```python
class LLMClient:
    def __init__(self, provider: str | None = None, model: str | None = None)
    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 temperature: float = 0.2, max_tokens: int = 1200) -> dict
        # -> {"text": str, "tool_calls": [{"name","arguments"}], "tokens_in": int, "tokens_out": int,
        #     "model": str, "finish_reason": str}
class MockLLM(LLMClient):
    # Deterministic offline fallback: parses the last user message, emits a tool call
    # (search_products) then a templated grounded answer using the tool result.
    # It MUST never invent prices/ratings — copy them from the tool result.
```
Providers: `deepseek` (Anthropic-compatible `https://api.deepseek.com/anthropic`),
`minimax` (`https://api.minimax.io/anthropic`), `openai` (`https://api.openai.com/v1`).
Read `DEEPSEEK_API_KEY` / `MINIMAX_API_KEY` from env. Retry twice with backoff; on failure
after retries → fall back to `MockLLM` and set `flags += ["llm_fallback"]`.

### `app/tools.py`
```python
TOOLS: list[dict]   # JSON-schema function declarations, names:
# search_products, get_product, get_reviews, compare_products, similar_products,
# add_to_cart, remove_from_cart, view_cart, checkout_cart, track_order,
# web_search_external (Tavily, graceful offline failure), get_price_history
def run_tool(conn, name: str, args: dict, session: dict) -> dict
    # -> {"ok": bool, "result": any, "citations": [ {"product_id","field"|"review_id","quote","value"} ], "error": str|None}
```
Every factual claim returned by a tool must carry a citation. `compare_products` must extract
comparison axes from the user's request and return a per-axis table **with evidence** (price from
DB, rating from DB, review quotes with review_id).

### `app/agent.py`
```python
def run_turn(conn, session_id: str, user_id: str | None, user_message: str,
             stream: bool = False) -> dict
    # ReAct loop, max 8 tool calls, 60s wall clock, streaming via generator of events:
    #   {"type":"token","text":...} {"type":"tool","name":...,"args":...}
    #   {"type":"citation","citation":{...}} {"type":"done","answer":...,"citations":[...],
    #   "product_ids":[...], "flags":[...], "latency_ms":int, "tokens_in":int,"tokens_out":int}
    # Must persist state (constraints, cart, last hits) into sessions.state, and write traces.
    # Post-answer verification pass: every price/rating in the final answer must match the DB
    # row of a cited product; strip or flag mismatches (flags += ["unverified_claim"]).
```

### `app/eval.py`
```python
def build_eval_set(conn, limit: int = 500) -> int     # from eval_queries ∩ catalogue, E/S/C labels
def run_retrieval_eval(conn, k: int = 10) -> dict     # Recall@k, nDCG@k, MRR
def run_agent_eval(conn, n: int = 30) -> dict         # task success, groundedness, latency, tokens
def report() -> str                                    # markdown summary written to demo/eval_report.md
```

### `app/main.py` (FastAPI)
```
POST /api/chat      {session_id?, user_id?, message}  → SSE stream of events
POST /api/voice     {session_id?, audio_base64}      → transcript + chat stream
POST /api/image     {session_id?, image_base64}      → visual search results + chat stream
GET  /api/session/{id}                                → state, cart, recent citations
POST /api/cart      {session_id, product_id, qty}
POST /api/checkout  {session_id, ship_to}             → mock order
GET  /api/products/{product_id}                       → detail + reviews + variants
GET  /api/health
```
Static UI served from `demo/ui/`. One uvicorn worker.

## Rules for all builders

1. **Never fabricate data.** Prices, ratings, stock, review text come from SQLite only.
2. **Offline-safe**: no import-time network calls; model loads lazy; every optional dependency
   wrapped in try/except with a logged fallback.
3. **No secrets in code or logs.**
4. Use `logging` (stdlib) with `logging.getLogger(__name__)`.
5. Modules must import cleanly with **no DB populated** (guard against missing tables).
6. Add a `__main__` self-test to each module: `python -m app.<module> --selftest` prints a short
   report and exits 0.
7. Write tests under `demo/tests/` as plain `pytest` files (pytest may not be installed — also make
   them runnable via `python demo/tests/test_<x>.py`).
8. Do not modify: `demo/app/db.py`, `demo/CONTRACT.md`, `demo/requirements.txt`, files owned by
   another builder (listed below).

## File ownership (do not touch other builders' files)

| Builder | Owns |
|---|---|
| B1 retrieval | `demo/app/embeddings.py`, `demo/app/retrieval.py`, `demo/app/personalize.py`, `demo/app/lexicon.py` |
| B2 agent | `demo/app/llm.py`, `demo/app/tools.py`, `demo/app/agent.py`, `demo/app/guardrails.py` |
| B3 serving | `demo/app/main.py`, `demo/app/eval.py`, `demo/ui/**`, `demo/tests/**` |
| Orchestrator | `demo/app/db.py`, `demo/ingest/**`, `demo/CONTRACT.md`, `demo/data/**` |
