# 01 — The product: Aria

> Aria is a shopping assistant that behaves like a good in-store expert: it asks what you mean, shows you
> the three options that actually fit, tells you **why** (with the evidence), admits when it does not know,
> and refuses to sell you something outside your budget or its evidence.

The interesting claim is not "we built a chatbot for shopping". It is:

> **Every claim Aria makes is resolvable to a row in the catalogue, a review, or an order — and the system
> is instrumented well enough to prove it.**

---

## 1. The catalogue it reasons over

Nothing here is mocked. The catalogue is real Amazon product metadata:

| Field | Source | Notes |
|---|---|---|
| 194,873 products | Amazon Reviews 2023 (McAuley-Lab) | Electronics, Musical Instruments, Industrial & Scientific |
| title, description, features, brand, categories | same | three-level category path |
| price | same | **no MRR in this dataset — so we do not fake a discount** |
| average rating + rating count | same | |
| images | same | `m.media-amazon.com` URLs, fetched live by the UI |
| 2.5M reviews with text | same | used as the evidence layer |
| 3.62M interactions / 1.39M users | 5-core ratings | real purchase history → personalisation |
| 4.0M co-purchase edges | derived from the above | the behaviour graph |
| 99,441 orders, customers, payments, delivery times | **Olist** (Brazil) | real transactions → behavioural priors |
| 4,280 labelled queries | Amazon Shopping Queries (ESC) | the gold set for evaluation |

Three deliberate consequences:

1. **No fake discounts.** Amazon's 2023 metadata has only a sell price. So Aria shows a price, never a
   struck-through MRP. (Price history is available but explicitly labelled *simulated*.)
2. **No fake catalogue.** "Buy for Me" — finding an item that is *not* in the catalogue — is implemented
   against the live web via a search provider, and its results are labelled as external with a URL.
3. **No fake customers.** Personalisation is driven by 1.39M real Amazon users' purchase histories.

## 2. The surfaces

| Surface | Status | What it demonstrates |
|---|---|---|
| **Text chat** (streaming) | ✅ full | The spine: constraint extraction → retrieval → cited answer → comparison → cart → checkout |
| **Comparison** | ✅ full | Axis extraction from the request, per-axis table, every cell sourced |
| **Cart & mock checkout** | ✅ full | Total computed from DB prices only, budget cap enforced, order created |
| **Personalisation** | ✅ full | Re-ranking with a deterministic explanation ("you bought 2 Sony items…") |
| **Buy for Me** (external web) | ✅ live web search | Agent finds items outside the catalogue; clearly labelled external |
| **Code-mixed / vernacular input** | ✅ parsing + answer | Hinglish queries are parsed and answered ("saste earphones under $50 for mom") |
| **Voice input** | ◻ thin | Browser speech API → transcript → same pipeline. Server-side ASR is stubbed |
| **Visual search** | ◻ thin | CLIP embedding of an upload → nearest catalogue items; OCR-based keyword fallback |
| **Image input (screenshot of a listing)** | ◻ thin | Falls back to text extraction from the upload |

The thin surfaces are thin on purpose: the engineering budget went into retrieval, grounding and
evaluation, which is where the actual product risk lives.

## 3. The five demo scripts

The UI ships one-click scripts so a demo never depends on typing:

1. `"I need a gift for my brother's birthday under $300, he loves photography"`
2. `"noise cancelling headphones under $200 with at least 4.5 stars"`
3. `"compare Sony WH-1000XM5 and Bose QuietComfort 45 on price, battery and comfort"`
4. `"saste earphones under $50 for mom, no plastic"` *(code-mixed + negation)*
5. `"best laptop for video editing under $1500"`

Each was checked against the catalogue: the prices are USD because the catalogue is Amazon US, and the
scripts are satisfiable by real rows.

## 4. A turn, step by step

```
user: "noise cancelling headphones under $200 with at least 4.5 stars"
 │
 ├─ constraint extraction        → {price_max: 200, rating_min: 4.5, category: headphones,
 │                                   attributes: {noise_cancelling: true}}
 ├─ SQL pre-filter               → price ≤ 200 ∧ rating ≥ 4.5 ∧ in_stock
 ├─ FTS5 BM25 top-200  ─┐
 ├─ dense ANN top-200  ─┼─→ RRF(k=60) → cross-encoder rerank top-50 → top-10
 ├─ co-purchase graph  ─┘
 ├─ personalisation re-rank     → "because you bought 2 Soundcore items"
 ├─ answer synthesis            → prose + inline [p1]/[r12] markers
 ├─ guardrail pass              → every price/rating in the answer re-checked against the DB
 └─ done                        → {answer, citations[], product_ids[], flags[], latency_ms, tokens}
```

The **system panel** in the UI shows the whole trace live: which tool ran, what constraints were inferred,
how many citations were produced, the latency, token usage, and any guardrail flags. That panel is the
product demo — it is what separates "a chat demo" from "a system you can reason about".

## 5. What Aria refuses to do

| Refusal | Mechanism |
|---|---|
| Recommend something over the session budget | `BudgetGuard` in middleware (default cap, configurable) |
| Say "in stock" when the row says otherwise | `StockGuard` re-reads the DB |
| Quote a price or rating that is not in the row | `ClaimVerifier` parses prices/ratings out of the final answer and re-checks them |
| Answer with no evidence at all | `CitationGate` rejects answers with zero citations |
| Act on a request the catalogue cannot satisfy | the agent must fall back to external search and label it as such |
| Execute a real payment | checkout is a mock; `dry_run` semantics throughout |

Guardrails live in **middleware, not in the prompt** — a prompt is advisory, a middleware check is not.

## 6. Who it is for

Two named personas exist in the UI, so the demo can show cold-start vs personalised behaviour:

* **Alex** — gift buyer, price-sensitive, wants the *short* answer and the reason.
* **Rhea** — value + quality, wants warranty/rating evidence and a comparison before deciding.
* **Guest** — no history; popularity prior + explicit "tell me what you like" prompts.

Real user histories are available too: pick any of the 1.39M real Amazon users by id and Aria will
re-rank for them with an explanation.
