# 03 — Evaluation

> Every number in this document was **measured on this machine** by `demo/app/eval.py` against
> `demo/data/app.db` (194,873 real Amazon products) and the Amazon Shopping Queries (ESC) gold set.
> Nothing is estimated, extrapolated or hand-written. Where a metric could not be computed, this
> document says so explicitly instead of guessing.
>
> The machine-generated appendix — the tables exactly as the harness emitted them — is
> [`../demo/eval_report.md`](../demo/eval_report.md).

---

## 0. Headline numbers

| # | Metric | Value | n | Notes |
|---|---|---|---|---|
| 1 | Retrieval — hybrid **Recall@10** (ESC gold set) | **PLACEHOLDER_R10** | 500 queries | seed `20261028`; 95% CI PLACEHOLDER_R10CI |
| 2 | Retrieval — hybrid **nDCG@10** | **PLACEHOLDER_NDCG** | 500 queries | 95% CI PLACEHOLDER_NDCGCI |
| 3 | Retrieval — **Recall@20** (hybrid) | **PLACEHOLDER_R20** | 500 queries | |
| 4 | Retrieval — **MRR@10** (hybrid) | **PLACEHOLDER_MRR** | 500 queries | |
| 5 | Retrieval — **lexical-only** Recall@10 (FTS5 BM25) | **PLACEHOLDER_LEX** | 500 queries | the floor to beat |
| 6 | Retrieval — **dense-only** Recall@10 | **PLACEHOLDER_DENSE** | 500 queries | **partial index** |
| 7 | Retrieval — hybrid **without** the cross-encoder rerank | **PLACEHOLDER_NORERANK** | 500 queries | RRF fusion only |
| 8 | Retrieval — hybrid **+ constraint pre-filter** | **PLACEHOLDER_PREFILTER** | 500 queries | |
| 9 | **Cross-encoder rerank delta** (Recall@10 / nDCG@10) | **PLACEHOLDER_CEDELTA** | 500 queries | |
| 10 | **Recall ceiling** — fraction of gold products that exist at all | **PLACEHOLDER_CEILING** | 656 gold rows | see §1.4 — this caps every recall number |
| 11 | Constraint parsing — **price_max** precision / recall | **PLACEHOLDER_PRICE** | 41 queries | |
| 12 | Constraint parsing — **rating_min** precision / recall | **PLACEHOLDER_RATING** | 41 queries | |
| 13 | Constraint parsing — **brand** precision / recall | **PLACEHOLDER_BRAND** | 41 queries | |
| 14 | Constraint parsing — **category** precision / recall | **PLACEHOLDER_CATEGORY** | 41 queries | |
| 15 | Agent — **task success** (5 demo + 5 adversarial) | **PLACEHOLDER_SUCCESS** | 10 turns | live LLM (DeepSeek) |
| 16 | Agent — **groundedness** | **PLACEHOLDER_GROUND** | 9 turns with claims | |
| 17 | Agent — latency **mean / p50 / p95** | **PLACEHOLDER_LAT** | 10 turns | |
| 18 | Agent — **tokens per turn** (in / out / total) | **PLACEHOLDER_TOKENS** | 10 turns | |
| 19 | Guardrails — flags raised | **PLACEHOLDER_FLAGS** | 10 turns | |
| 20 | Guardrails — **ClaimVerifier precision** on this run | **PLACEHOLDER_GPREC** | PLACEHOLDER_GN claims flagged | 0 real fabrications |

The single most important caveat, stated up front: **the catalogue has no price for 48% of its
products**, and the ESC gold set is drawn from that same 194,873-product pool. 45% of the gold
products are therefore *unreachable by any retrieval arm* — they have no price, so the SQL
pre-filter (`in_stock = 1`) and the embedding job both exclude them. Every Recall@k below is
measured against a ceiling far below 1.0, and §1.4 quantifies it.

---

## 1. Method

### 1.1 The gold set

The gold set is the **Amazon Shopping Queries (ESC)** dataset (`amazon-science/esci-data`,
Apache-2.0), loaded into `eval_queries` by the ingest pipeline. Each row is
`(query_id, query, product_id, label, split)` with `label ∈ {E, S, C, I}`:

| label | meaning | treated as |
|---|---|---|
| **E** | exact match | relevant, gain 3 |
| **S** | substitute | relevant, gain 2 |
| **C** | complement | relevant, gain 1 |
| I | irrelevant | not relevant |

Only **E/S/C** rows count as relevant. Rows are joined to our catalogue on `product_id`
(`parent_asin`), which gives **5,391 relevant rows over 4,280 distinct queries**; the 3,751
queries that survive the join form the evaluation pool, from which we draw a **fixed random
sample of 500 queries (seed `20261028`)**.

Queries are not sanitised. The ESC strings are raw Amazon search logs and look like
`$90 iphone not chage`, `, 1, skullcandy jib wired in-earphone without mic (black)`,
`03 grand am double din radio`. That is deliberate: the point is to measure the system on the
queries shoppers actually type, not on a cleaned-up academic set.

### 1.2 The metric definitions

| Metric | Definition |
|---|---|
| **Recall@k** | `|top-k ∩ gold| / |gold|` — the fraction of the query's relevant products that appear in the first k results. |
| **nDCG@10** | Graded gain (E=3, S=2, C=1), `DCG = Σ gain_i / log2(i+1)` over the top 10, divided by the ideal DCG of the same gold set. |
| **MRR@10** | `1 / rank` of the first relevant product inside the top 10, else 0. |
| **Constraint precision** | Of the constraints the parser *asserted*, how many were right. A wrong value is a false positive; a missed value is a false negative (recall). |
| **Groundedness** | Fraction of *checkable* claims in an answer that match the database. A checkable claim is a currency amount, a star rating, a discount percentage or a rating/review count. Budget echoes ("under $300") and price bands ("$1,200–1,500") are removed first — they repeat the user's budget, they are not claims about a product. |
| **Task success** | The turn produced an answer **and** cited evidence **and** raised no guardrail error flag **and** (for adversarial turns) passed its behavioural check (refused / said "not in catalogue" / did not invent reviews / repeated the true value). |

95% confidence intervals are percentile bootstrap over the 500 queries (2,000 resamples, seed
`20261028`).

### 1.3 The retrieval arms

All arms share the same parsed constraints, the same query text
(`retrieval._search_text()` — the free text plus the extracted brand/category/attributes) and the
same **frozen snapshot of the dense index**, so the comparison is one-variable-at-a-time.

| Arm | What it is |
|---|---|
| `lexical` | FTS5 BM25 only, no constraint push-down. |
| `lexical_prefilter` | FTS5 BM25 with the parsed constraints pushed into the SQL (this is what production does). |
| `lexical_fixed` | The same, minus the `brand` and `not_attribute` predicates — a diagnostic for the ambiguous-column bug described in §4. |
| `dense` | bge-small-en-v1.5 vectors, exact cosine over the frozen snapshot, no filters. |
| `hybrid_no_rerank` | RRF (k=60) fusion of lexical + dense + co-purchase graph. |
| `hybrid` | The same, **plus** the `ms-marco-MiniLM-L-6-v2` cross-encoder rerank of the head (top-20). |
| `hybrid_prefilter` | The hybrid arm with the constraint pre-filter pushed into candidate generation (SQL for FTS, a predicate on the vector snapshot, a candidate set for the graph arm) and the hard filter applied after fusion. |
| `production` | The real end-to-end `retrieval.hybrid_search()` call, on a 150-query sub-sample (it is ~3× more expensive). |

Two implementation notes that matter for reproducibility:

* **The dense index is frozen.** The catalogue embedding job runs in the background, so the vector
  store grows while an evaluation runs. The harness snapshots it once at the start; every arm then
  sees exactly the same vectors, and the dense numbers can be labelled honestly as
  *partial index (N/M products embedded)*.
* **The cross-encoder is batched.** The harness re-implements `retrieval._cross_encode_rerank`
  exactly (same top-N, same min-max normalisation of both signals, same 0.5/0.5 blend, same sort)
  but batches the pairs of many queries into one `predict()` call — ~10× faster on 2 vCPU. The
  harness **verifies the equivalence against the production function** on every run and prints the
  result (`cross_encoder_batch_check`).

### 1.4 What caps the numbers: the reachable ceiling

`ingest/build_db.py` sets `in_stock = 1 if price is not None else 0`. The Amazon 2023 metadata
carries no stock information, so `in_stock` is really *"has a price"*. Consequences:

| Quantity | Value |
|---|---|
| Catalogue products | 194,873 |
| … without a price (`in_stock = 0`) | **93,455 (48.0%)** |
| Products the embedding job will ever embed (`in_stock = 1 AND price IS NOT NULL`) | **101,418** |
| ESC gold rows (E/S/C, joined to the catalogue) | 5,391 |
| … **without a price** → unreachable by every arm | **2,425 (45.0%)** |
| Mean per-query fraction of gold that is reachable | **PLACEHOLDER_CEILQ** |

A retriever can therefore never score above ≈0.57 Recall@k on this gold set, whatever the ranker
does. The harness reports this ceiling next to every recall number so the two can never be
confused, and §3 reads the results against it.

### 1.5 The agent suite

Ten turns through the real agent loop (`app.agent.run_turn`), with a live LLM:

* **5 demo scripts** — the exact strings in the UI dropdown (`demo/ui/index.html`).
* **5 adversarial turns** — a request the catalogue cannot satisfy (a solar-charging anti-gravity
  hoverboard); a request over budget (add a $3,596 TV and check out against a $2,000 session cap);
  a request for a product that does not exist (`B0FAKE1234`); a request with a false premise ("the
  Beats Studio3 is 4.9★ and 60% off"); a request for reviews of a product that has none in the
  corpus.

The LLM is the real one (`DEEPSEEK_API_KEY` → `deepseek-chat`, Anthropic-compatible endpoint). If
the key is missing the harness falls back to the deterministic `MockLLM` and says so in the report;
that did not happen on the measured run.

---

## 2. Results

PLACEHOLDER_RESULTS

---

## 3. What the numbers mean

PLACEHOLDER_MEANING

---

## 4. What is weak — and the bugs this evaluation found

PLACEHOLDER_WEAK

---

## 5. How we would improve it

PLACEHOLDER_IMPROVE

---

## 6. How to reproduce

PLACEHOLDER_REPRO
