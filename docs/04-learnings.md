# 04 — Learnings

What we learned building **Aria**: an agentic shopping assistant on public data, on a 4-vCPU VM, with
nine agents working in parallel against a written contract.

This is the document that tells you what actually happened, including the parts that did not work. Every
number below is measured from this repo (`demo/data/app.db`, the `traces` table, `demo/app/*.py`); where
we are guessing we say so. The numbers in this file are a **diagnostic**, not a benchmark — the sample
sizes are small and we explain why in §6.

---

## 1. The build process: the contract was the product

Five research briefs ran in parallel (`dev/prompts/01_amazon.md` … `dev/prompts/05_india.md`); they landed as evidence dossiers in
`research/`. Then three build agents ran in parallel against `demo/CONTRACT.md` (`dev/prompts/b1.md` … `b3.md`), with an orchestrator
owning the schema, the data pipeline and the integration.

The contract had four parts, and all four mattered:

1. **The DDL.** "Do not change the DDL in `demo/app/db.py`." New columns go in side tables prefixed with
   your module name (`embeddings_done`, `aspect_cache`).
2. **Exact module signatures.** `parse_constraints`, `hybrid_search`, `run_tool`, `run_turn`,
   `build_eval_set` — each with its return shape written down before a line of code existed.
3. **The event schema.** `{"type": "token"|"tool"|"citation"|"done", ...}` with the exact keys, so the
   serving layer could be written against a document instead of against a running agent.
4. **File ownership.** B1 retrieval (`embeddings.py`, `retrieval.py`, `personalize.py`, `lexicon.py`),
   B2 agent (`llm.py`, `tools.py`, `agent.py`, `guardrails.py`), B3 serving (`main.py`, `eval.py`,
   `demo/ui/**`, `demo/tests/**`), orchestrator (`db.py`, `ingest/**`, `data/**`).

**What worked.** Zero merge conflicts — the integration is a single commit with no merge parents,
because no two agents could legally write the same file. More importantly, the contract caught API drift
*before* integration. `run_tool` is specified to return `{"ok", "result", "citations", "error"}` with a
citation on every factual claim; `agent.py`'s streaming code depends on that shape and `_mark_citation`
assumes the `{"product_id", "field"|"review_id", "value", "quote"}` layout. When B2's guardrails landed
before B1's retrieval, the system still imported, ran and degraded correctly, because retrieval falls
back to lexical-only and `tools.py` imports retrieval *lazily inside functions*. That was designed in
the contract ("modules must import cleanly with no DB populated"), and it is the reason the integration
day was boring.

**What broke.** Two things, and they are worth separating because they teach opposite lessons.

*A VM restart killed two agents mid-flight.* Their in-context reasoning died with the process. We
restarted them **from the contract**, not from a transcript. That worked: the contract was a complete
enough specification to re-implement a module against. This is the strongest argument for writing the
contract down at all.

*The LLM model catalogue changed under us.* `llm.py` pinned `DEFAULT_MODELS = {deepseek: "deepseek-chat",
minimax: "MiniMax-M1", openai: "gpt-4o-mini"}`. When a provider retired or renamed a model, the retry
loop (`MAX_RETRIES = 2`, exponential backoff) could not recover and every turn silently fell back to
`MockLLM` with an `llm_fallback` flag. The fix was boring and should have been there from the start: the
model name is now an env var (`ARIA_DEEPSEEK_MODEL`, `ARIA_MINIMAX_MODEL`, `ARIA_OPENAI_MODEL`) and the
fallback is part of the contract rather than an accident. **The contract survived; the agents didn't.**
That asymmetry is the whole lesson.

**The rule we would adopt: agents own files, humans own interfaces.** Everything two agents must agree
on — the schema, the tool-result shape, the citation shape, the event names, the default budget — was
written by a human and never edited by an agent. Everything *behind* an interface was agent-owned. The
interface is the expensive thing to change, because it breaks every consumer at once; the file is cheap
to change. We would add one thing next time: a `CONTRACT_VERSION` line and a check that every module
satisfies it, so a stale agent is caught by CI rather than by a demo.

---

## 2. Retrieval lessons

The query path is: deterministic constraint parsing → SQL pre-filter → three candidate channels
(FTS5 BM25 top-200, dense ANN top-200, co-purchase graph top-50) → Reciprocal Rank Fusion (`k=60`) →
cross-encoder rerank of the top 20 → optional personalisation re-rank.

**Why RRF and not score blending.** The three channels return three incomparable things: SQLite's
`bm25()` is negative and unbounded, LanceDB returns a cosine, and the graph channel returns an
arbitrary weighted sum (`co_purchase` × 2.0, `also_bought` × 1.5, `co_view` × 1.0). Blending them
requires calibrating three scales per query, and min-max normalisation is unstable the moment a channel
returns three results. RRF reads only ranks — `1/(60 + rank + 1)` — so you cannot be wrong about scale,
and the one constant is legible and arguable. When we changed a channel we never had to revisit the
fusion.

**What the cross-encoder actually contributed.** `cross-encoder/ms-marco-MiniLM-L-6-v2` reranks the top
20 (we narrowed from the designed 50: ~12 ms/pair on CPU means 50 pairs is 0.6 s of the turn budget) and
blends `0.5 * rrf + 0.5 * sigmoid(logit)`. It re-orders the head of the list. It cannot rescue the list,
because it only ever sees the top 20 — a gold product at rank 41 is unrecoverable. The reranker is a
precision tool; the recall work has to happen upstream.

**Cold start, three ways.** (a) *Catalogue cold start:* the embedding job has embedded **10,000 of the
101,418 in-stock products** (`embeddings_done`), so the dense channel currently covers ~10% of what can
be sold. Today "hybrid" is mostly lexical with a graph garnish. (b) *User cold start:*
`personalize.build_profile` returns `n_events = 0` for anyone but the demo user and the re-rank is a
no-op; we fall back to a popularity prior. (c) *Query cold start:* a query with no lexical and no dense
neighbour still gets the SQL fallback (rating desc, popularity desc, price asc) — a poor answer, but an
answer.

**Why constraint parsing is code and not a prompt.** `parse_constraints` is regex plus a lexicon built
from the catalogue (50,204 rows in `lexicon`) plus a hand-written Hinglish map. It handles "under 3000",
"3000 se kam" (Hinglish), "at least 4.5 stars", "1.5 lakh", negations ("not Samsung", "no plastic") and sort
intents ("cheapest", "saste"). We chose determinism for four reasons: it sits on the critical path of
every turn; it costs nothing per query; the same query must produce the same filter or the evaluation is
meaningless; and it must work with no network and no API key. **The rule we landed on: the LLM phrases
the answer, the code computes the filter.** The LLM still owns the explanation of the filter ("I
filtered to under $300 and brand in {Sony}") — but only because the code told it what it did.

**And the honest measurement.** Our first retrieval run on the Amazon Shopping Queries (ESC) gold set
looked bad, and most of the badness was our own instrumentation:

| Measurement (first 40 ESC queries) | Value |
|---|---|
| Recall@10 as the harness computes it | **0.063** |
| Recall@10 over gold present in our catalogue | **0.104** |
| E-only Recall@10, hybrid | **0.100** (n=25) |
| E-only Recall@10, lexical only | **0.160** (n=25) |
| nDCG@10 | **0.038** |
| Queries with *zero* gold product in our catalogue | **16 / 40** |
| Gold pairs excluded by our own `in_stock = 1` filter | **18 / 45** |

Hybrid currently *loses* to lexical on this sample. The diagnosis is not "dense retrieval is useless" —
it is four separate measurement and coverage faults:

1. Our ingest sets `in_stock = 1` **iff the row has a price**. The Amazon metadata has no price for many
   rows, so 40% of the gold answers were deleted before the retriever could see them. This was our
   schema, not the data.
2. Only **55%** of ESC gold pairs (2,966 / 5,391 over 4,008 queries) exist in our catalogue at all. ESC
   is drawn from Amazon's US catalogue; ours is four categories.
3. The dense channel covers 10% of the sellable catalogue.
4. ESC queries are keyword strings — `"$90 iphone not chage"`, `", 1, skullcandy jib wired in-earphone
   without mic (black)"` — and our parser reads `"$90"` as `price_max`, which fights the gold label.

The fixes, in order of expected value: separate `price_known` from `in_stock`; finish the embedding job;
publish coverage next to the metric; build a constraint-bearing gold set of our own.

---

## 3. Grounding lessons

"A chatty answer" is fluent. "An answer whose every claim has a citation" is *checkable*. The difference
is not stylistic: a citation is a pointer into the database, so a reviewer can refute it in one click.

We enforce it in three places. The tool layer returns citations with facts and nothing else
(`{"product_id", "field"|"review_id", "value", "quote"}`). The agent converts them into inline markers
(`[p1]` for products, `[r12]` for reviews) and streams them as events. Then, after the answer is composed
but before it is shown, `run_post_guards` runs two checks: **CitationGate** rejects an answer that
contains a currency amount, a star rating or a %-off claim while carrying zero citations; **ClaimVerifier**
re-reads the DB rows for the cited products and checks every number in the text against them (2%
relative / $2 absolute tolerance on price, 0.15 on rating, 2 points on discount), flagging
`unverified_claim` on a miss.

**The failure mode we care about: a templated answer can still be wrong.** `MockLLM` is deterministic and
copies price, rating and title verbatim out of the tool result — and it can still be wrong, because the
wrongness is upstream of the phrasing. The tool may have returned the wrong product (a retrieval error),
the wrong field (a product whose title matches the query but whose category does not), or a simulated
value. `get_price_history` is the clearest case: it generates a 12-month series from a seeded RNG seeded
by `sha256(product_id)`, and the payload says `"simulated": true` — the phrasing is exact, the data is
invented. Templating guarantees the *shape* of a claim, not its truth.

**The honest limit of our verifier.** It checks numbers, not propositions. An answer that says "the Sony
WH-1000XM5 is the best noise-cancelling headphone under $500" passes if $499 is the price of *some*
cited product. It does not check that the cited product is the right product for the question, that a
review quote actually appears in that review, or that the recommendation is any good. Those are unsolved,
and we would rather say so than pretend the citation gate covers them.

One more trap worth naming: our `groundedness` metric returns **1.0 when the answer makes no currency
claim**. A system that replies "I couldn't find anything matching that" scores perfect groundedness. We
now read that number as "no fabrication detected", never as "grounded".

---

## 4. Guardrail lessons

The system prompt in `agent.py` carries seven rules ("Answer ONLY from tool results", "Cite every factual
claim", "checkout_cart is a MOCK…"). The middleware enforces five invariants. The prompt is a request;
the middleware is a control.

| Guard | Enforces | How |
|---|---|---|
| `BudgetGuard` | $2,000 default session cap | recomputes the cart total from SQLite rows and refuses checkout with `status="refused"` + a reason |
| `StockGuard` | never claim in-stock for a row that says otherwise | re-reads `in_stock` for every cited product after the answer |
| `ClaimVerifier` | every price/rating/%-off matches the cited DB row | numeric re-check with tolerances |
| `CitationGate` | factual answers carry citations | rejects factual text with zero citations |
| `PromptGuard` | mock-only policy | refuses real-money framing ("place a real order with my credit card") |

The reason these belong in middleware is structural: every one of these invariants is about the *world*
— prices, stock, money — not about the sentence. The model cannot check a price against a row it has
not read; the database can. It is also testable without a model: `python -m app.guardrails --selftest`
asserts out-of-stock detection, the real-money refusal, claim-violation detection and the no-citation
rejection in under a second, with no network and no key. That is a property we want in every guardrail
from now on: **a guardrail you cannot test without a model is not a guardrail.**

Two design choices with costs we accept. First, the guards *detect*; they do not repair — an unverified
number still reaches the screen with a `[Note: …]` appended. We show the failure rather than silently
rewriting the answer, but the right fix (strip the sentence, re-verify, re-render) is on the roadmap.
Second, our "streaming" is not token streaming: the final answer is composed, verified, and then chunked
into 8-word pieces (`_chunk_text`) and emitted. We cannot show the user a token before the guardrail
pass has run, and that is the correct trade — a pretty stream that leaks an unverified price is worth
less than a pause.

---

## 5. Data lessons

Everything here is public, and every dataset was good at exactly one thing.

| Source | Rows we hold | What it was actually good for | What it could not give us |
|---|---|---|---|
| Amazon Reviews 2023 metadata | 194,873 products, 4 categories | catalogue: titles, brands, price, rating, features, images | no list price / MRP; no order stream; US prices only |
| Amazon 5-core ratings | 1,390,956 users, 3,621,289 interactions | real behaviour: brand/category affinity, average spend, and 400,000 co-purchase edges | ratings, not baskets — the edge type says `co_purchase` but the source is co-rating; we should rename it |
| Amazon raw reviews | 2,500,000 reviews | citation-able evidence (`review_id` + quote) | category-skewed — one category file dominates |
| Olist (Brazil) | 99,441 orders, 112,650 items, 99,441 customers | real transactions, delivery timestamps, payments, review scores | a Brazilian marketplace: we keep it in `olist_*` tables so a Brazilian order is never confused with an Amazon product |
| ESC (amazon-science/esci-data) | 4,008 labelled queries, 5,391 gold pairs | a gold relevance set with graded labels (E=3, S=2, C=1) | keyword queries, noisy labels, and only 55% coverage of our catalogue |

**The limits that shaped the product.** There is no MRP anywhere in the Amazon metadata, so our ingest
sets `price_mrp = price` for all 101,417 priced rows: discount is structurally 0, and "% off" can never
be a truthful claim from our data. We did not fabricate one — the price-history tool returns a series
explicitly labelled `simulated`. Second, there is no public Amazon order stream, so "people bought these
together" is inferred from co-ratings, and the personalisation profile is a prior, not a fact about a
real shopper in our product. Third, the catalogue and the personas are now both USD — the default session
budget is $2,000 while the flagship demo script says "under 3000". Our claim verifier is deliberately
currency-agnostic (it accepts ₹ and $), but the *budget* should not be; that inconsistency was on the
fix list.

**What ingestion actually cost.** `bge-small-en-v1.5` encodes at ≈40 texts/s on this box, so 195k
products is ≈80 minutes. The job is resumable (the `embeddings_done` side table records completed ids),
which is why it can be stopped and restarted, and which is also why the dense channel is only 10%
populated right now. The SQLite file is 2.3 GB, plus a 772 MB WAL we never checkpointed.

**What we would do differently.** (a) **Quantise**: int8 ONNX is roughly 2–3× the throughput and half the
RAM — worth it at 195k rows, less so at 10k. (b) **Embed a slice, not everything**: order by
`rating_count` descending and stop at ~50k products; the long tail of one-rating products contributes
almost nothing to recall and most of the 80 minutes. (c) **Move it to a batch job** on bigger hardware
and ship the artefact, so the app never pays for ingestion. (d) **Checkpoint the WAL** and keep the
LanceDB table separate from the SQLite file so the database is always rebuildable from raw.

---

## 6. Evaluation lessons

Measuring an agentic shopping surface without users means measuring the *instrument* as carefully as the
system. What we measure today: retrieval metrics on ESC (Recall@k, nDCG@k, MRR with graded labels),
constraint-parsing precision per field on a hand-written set, agent task success
(*answered AND cited AND within the parsed budget*), groundedness, plus latency and tokens from the
`traces` table.

| Metric | What it really measures | Proxy for |
|---|---|---|
| Recall@k / nDCG@k on ESC | can the retriever surface Amazon's labelled products | "the right thing surfaces first" — but ESC queries are keyword, and our parser rewrites them into filters |
| MRR | how high the first relevant item sits | shopper relevance, roughly |
| Task success | answered + cited + within budget | "a person would accept this" — it never checks the product is *good* |
| Groundedness | no fabricated currency values | trust — and it is 1.0 when there is nothing to check |
| Latency / tokens | cost per turn | real, but measured on our VM with a warm model and 10k embeddings |

**What we have no metric for**, and cannot fix without users: conversion, return rate, regret, repeat
use. Amazon discloses a "downstream impact" metric with a 7-day rolling attribution window and does not
disclose return-rate-on-recommended-item or hallucinated-attribute rate at all.

**The traps.**

1. **The catalogue-shrinking trap.** Our own run shows the mechanism: dropping rows without prices
   removed 40% of the gold answers and made every metric look catastrophic. In the other direction,
   pruning to popular items *improves* nDCG while making the assistant worse for the long tail. Always
   publish catalogue size and gold coverage next to the metric, or the number lies to you.
2. **Label semantics.** ESC's `C` = "complement" — a complementary product, graded as partially
   relevant. An agent that surfaces complements as recommendations earns credit for something the
   shopper did not ask for. We would score `E` separately from `S`+`C`.
3. **The silent metric.** Groundedness of 1.0 with no claims is a reward for silence.
4. **Self-authored tasks.** The agent eval runs 5 demo scripts × 5 mutations. We wrote them and we tuned
   to them. That is a regression suite, not an evaluation.
5. **Sample size.** The numbers in §2 are n=40 — a diagnostic, not a benchmark.

**What we would build instead:** a held-out, constraint-bearing gold set (an ESC query *plus* a filter
spec *plus* the product that satisfies it); a pairwise answer-quality rubric scored by two humans rather
than by a model; and a regret proxy — carts that were abandoned after a recommendation, which is the
cheapest available shadow of "the shopper did not want that".

---

## 7. What we would do next

**Next 30 days.** Separate `price_known` from `in_stock` and re-run the eval — a one-hour schema change
that moves the headline number more than any modelling work. Finish the embedding job and publish a
channel ablation (lexical / dense / graph / hybrid) with coverage attached. Write the constraint-bearing
gold set (200 queries with filters and gold). Report recall over *attainable* gold, always.

**Next 60 days.** Make the decision trace a first-class artifact: every turn writes the parsed
constraints, the per-channel candidate lists, the fused ranks, the rerank scores, the citations and the
guardrail flags. The trace is the product; the chat is the demo of the trace. Repair instead of note:
on a claim violation, remove the offending sentence and re-verify. Drop the simulated price series from
the answer path and keep it in a clearly-labelled panel. Sign up one real merchant with a real
price/stock feed — there is no way to test the trust claim against our own mock catalogue.

**Next 90 days.** Model pincode-level serviceability, even as a stub, because "deliver to
Friday" cannot be answered truthfully without it and it is the India-specific, hard-to-copy data. Add a
second merchant and a genuine cross-merchant comparison. Expose the verification layer as an API other
agents can call — the first revenue-shaped version of the wedge in [05-moats](05-moats.md). Publish the
harness so somebody else can reproduce the numbers.

**If we had less time, we would delete three things.**

1. **The image / CLIP path.** Visual search is a UX surface, not a moat; it needs a second model and an
   image index, and no part of the moat map rewards it.
2. **The voice stub.** Browser speech API plus a text fallback adds a modality without touching the
   three things that actually differentiate — constraint parsing, grounding, evaluation. India's real
   voice problem is ASR on model numbers ("Sony A6400" heard as "A6000"), which we do not solve at all.
3. **The personalised re-rank.** With `n_events = 0` for everyone but the demo user it is a no-op that
   adds a code path and a failure mode. The underlying behaviour data is real (1.39M users), but we
   cannot show a benefit until there are sessions.

At all costs we keep the claim verifier, the evaluation harness and the traces. Everything else in this
repo is replaceable; those three are the part that would survive contact with a customer.
