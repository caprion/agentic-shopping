# 07 — Decision log

Every substantive decision made while building this prototype, with the options that were on the table,
what we chose, what actually happened, and an honest verdict.

**Verdict key** — ✅ *good, kept* · ⚠️ *worked but cost us something* · ❌ *bad, reverted or still open*

This is the document we would hand to the next person. The interesting decisions are not the ones that
worked — they are the ones where we were wrong, and how the system told us.

---

## 1. Scope and framing (decisions before any code)

| # | Decision | Options considered | Chosen | Why | Outcome | Verdict |
|---|---|---|---|---|---|---|
| 1.1 | What is this project *for*? | (a) consumer app mockup, (b) evaluation/measurement platform, (c) research artefact | **(b)+(c)**: a working agent **plus** a measurement harness | A chat mockup proves nothing; the defensible skill is knowing what to measure and how | The eval harness found 6 real defects in our own code (§7) | ✅ |
| 1.2 | Build a "clone of Amazon" | (a) clone Amazon's surface, (b) build the *capability* Amazon is building | **(b)** | We cannot access Amazon's catalogue or surfaces; we can build the capability class | Demo is genuinely interactive on real data | ✅ |
| 1.3 | Real catalogue vs synthetic | (a) synthetic catalogue (fast, clean, fake), (b) public real catalogue (messy, incomplete) | **(b)** | Synthetic data hides exactly the problems worth solving (48% of rows have no price) | Forced us to confront the "no price" and "no MRP" realities early | ✅ |
| 1.4 | Data sourcing discipline | (a) trust the docs, (b) verify every URL by downloading | **(b)** — every dataset was fetched, parsed and row-counted | Docs lie; a 22 GB reviews file is not a usable demo dataset | 16 GB of raw downloads, all reproducible via `demo/scripts/fetch_data.sh` | ✅ |
| 1.5 | Currency & persona coherence | (a) ₹ + Indian personas over a USD catalogue, (b) USD everywhere, (c) fake an INR conversion | **(b)** after **(a)** shipped and looked incoherent | A catalogue with no price cannot support an INR story; a reviewer spots it in one line | Cost a mid-build rework (B4 pass touched 6 files) but the final artefact is coherent | ⚠️ → ✅ |
| 1.6 | Where does the emerging-markets story live? | (a) in the product, (b) in the research | **(b)** — the *demo* is US retail; the *docs* carry India | The JD-shaped framing (emerging markets) is a strategy question, not a catalogue question | `docs/06-emerging-markets.md` carries the argument without contaminating the demo | ✅ |
| 1.7 | Publish or keep private | (a) private, (b) public | **(b)** public repo | The user asked for a demo/thought-leadership artefact; a public repo forces the writing to be honest | Forced removal of all job-application framing — a discipline we would not have applied otherwise | ✅ |
| 1.8 | Repo hygiene rules | ad-hoc | `.gitignore` (data, models, db, `.env`), licence, `AGENTS.md` conventions, secret scan before every push | A 1 GB SQLite file and 16 GB of raw data must never be committed | Repo is 1.8 MB of history; `data/` and `demo/data/` are ignored and regenerable | ✅ |

---

## 2. Data decisions

| # | Decision | Options | Chosen | Why | Outcome | Verdict |
|---|---|---|---|---|---|---|
| 2.1 | Product entity key | (a) `asin` (per variant), (b) `parent_asin` (entity), (c) both | **(b) entity, `asin` as variant** | A shopper means "the Sony WH-1000XM5", not "the XM5 in black size L" | Enabled variant grouping, brand/attribute faceting and honest "same product" comparisons | ✅ |
| 2.2 | Category breadth | (a) one category (Electronics), (b) three categories | **(b)**: Electronics + Musical Instruments + Industrial & Scientific | Cross-category queries ("gift for a photography-loving brother") must be answerable | 194,873 products; demo scripts satisfiable in all five cases | ✅ |
| 2.3 | Review corpus | (a) full 22.6 GB raw reviews, (b) a 500 MB byte-range slice | **(b)** — HTTP range request on the raw JSONL | 500 MB → 2.5M real review texts in ~7 minutes of download instead of hours | 2.5M reviews with text for grounding and citation | ✅ |
| 2.4 | Where do real *transactions* come from | (a) invent them, (b) Amazon 5-core ratings (no order stream), (c) Olist (real marketplace orders) | **(c) Olist** for orders/customers/payments/delivery; **(b)** for user-item history | Amazon publishes no public order stream; Olist is real transactions (99,441 orders, 112k items, payments, delivery dates, reviews) | Two sources, clearly separated in the schema (`olist_*` tables vs `products`) so nothing is conflated | ✅ |
| 2.5 | Olist source | (a) Kaggle (needs an account), (b) a public GitHub mirror | **(b) mirror, verified column names** | Kaggle needs credentials; a public repo does not | But the mirror's **column names differ from canonical Olist** (`order_approved_at` vs `order_approved_timestamp`) → the loader broke twice before we made it tolerant | ⚠️ |
| 2.6 | The evaluation gold set | (a) hand-labelled queries, (b) Amazon Shopping Queries (ESC) | **(b)** — real shopper queries with E/S/C/I labels | Hand labels would be our own bias; ESC is real search logs | 5,391 relevant rows over 4,280 queries joined to our catalogue; 500-query seeded sample | ✅ |
| 2.7 | Dense index scope | (a) all 194,873 products, (b) only sellable rows | **(b) 101,418** (rows with a price) | A product with no price cannot be bought, so it is not searchable | 101,458 vectors; 52.06% catalogue coverage — **which is also the recall ceiling** | ✅ (with a cost, documented) |
| 2.8 | Model list price (MRP) | (a) synthesise a discount, (b) don't invent one | **(b)** — `price_mrp = price`, discount 0 | The dataset has no list price; a fabricated strike-through price is a lie a shopping agent must not tell | The UI shows no fake discounts; price-history is explicitly labelled *simulated* | ✅ |
| 2.9 | Missing-price rows in the catalogue | (a) keep them searchable, (b) keep them but exclude from results | **(b)** — excluded from `search_products`, kept in the catalogue | They were leaking into answers and producing "out of stock" flags | Out-of-stock false positives 3 → 0 | ✅ |
| 2.10 | Embedding text | (a) title+description+features (640 chars), (b) title+brand+category+desc[:160] (223 chars) | **(b)**, plus `max_seq_length=128` | Measured: 4.1 texts/s → 19–22 texts/s | Full index build went from "6+ hours" to ~80 min | ✅ |

---

## 3. Architecture & retrieval decisions

| # | Decision | Options | Chosen | Why | Outcome | Verdict |
|---|---|---|---|---|---|---|
| 3.1 | Relational store | (a) SQLite + FTS5, (b) Postgres + pgvector | **(a) SQLite** | One file, zero ops, native BM25; the DDL stays Postgres-portable | 195k products, sub-100 ms FTS queries, no daemon | ✅ |
| 3.2 | Vector store | (a) LanceDB, (b) FAISS, (c) pgvector, (d) numpy brute force | **(a) LanceDB** with a **numpy `.npy` fallback** | Embedded, on-disk, no service; the fallback means retrieval never depends on it | 101,458 vectors served from `demo/data/lance`; fallback exercised during the partial-index phase | ✅ |
| 3.3 | Embedding model | (a) `bge-small-en-v1.5` (384d), (b) `multilingual-e5-small`, (c) a large model | **(a)** for text; **CLIP ViT-B/32** for images (optional) | CPU-cheap, 384d, best quality/byte on a 4-vCPU box | 19–22 texts/s; dense arm R@10 0.1589 | ✅ |
| 3.4 | Fusion strategy | (a) weighted score blend, (b) Reciprocal Rank Fusion, (c) single channel | **(b) RRF, k=60** | BM25 scores, cosine similarities and graph weights are not comparable; RRF needs no calibration | Hybrid R@10 0.1686 vs 0.1102 lexical and 0.1589 dense — fusion beat both channels | ✅ |
| 3.5 | Reranking | (a) none, (b) cross-encoder `ms-marco-MiniLM-L-6-v2` over top-50 | **(b)** | Re-orders the head without changing coverage | +1.1 R@10, +1.2 nDCG@10, +1.5 MRR@10; Recall@20 unchanged, as expected | ✅ |
| 3.6 | Who parses the user's constraints | (a) the LLM, (b) deterministic regex + lexicon | **(b)** | Fast, free, reproducible, offline-safe — and the *filter* must not be the model's discretion | price/rating/brand precision **1.00** on 41 queries; category recall 0.92 | ✅ |
| 3.7 | Lexicon source | (a) hand-written only, (b) derived from the catalogue + a hand-written synonym/Hinglish seed | **(b)** | Brands and categories must come from the data, not from memory; the seed handles "khanai", "chappal", "saste" | 4,031 lexicon terms; junk brands ("for", "laptop") blocked | ✅ |
| 3.8 | Behavioural channel | (a) no personalisation, (b) collaborative filtering, (c) a co-purchase graph | **(c)** — edges from real baskets | Reuses the interaction data we already have; explainable ("you bought 2 Soundcore items") | 4.0M co-purchase edges (400k kept), 1.39M user profiles | ✅ |
| 3.9 | Personalisation blend | (a) learned model, (b) deterministic weighted blend | **(b)** | A learned ranker on 1.39M sparse users is out of scope; a deterministic blend is explainable and testable | Explanations are stable strings; the UI can show *why* | ✅ |
| 3.10 | Constraint pre-filter on retrieval | (a) always apply hard filters, (b) apply only when confident, (c) never | **(b)**, with a relaxation path | Measured: the pre-filter **hurts** recall on noisy ESC queries (0.1424 vs 0.1686) because ESC queries contain prices that are not constraints | The relaxation path ("FTS relaxed (match=1 where=0)") is logged on every query | ⚠️ (kept, but with a documented cost) |
| 3.11 | Tool count and shape | (a) 3 tools, (b) 12 tools, (c) 40 tools | **(b) 12** | Enough for a real journey (search → compare → cart → checkout → external), few enough that the model picks correctly | Mean 71.6 citations/turn; tool selection was never the failure mode | ✅ |
| 3.12 | Agent loop bounds | unbounded | **max 8 tool calls, 60 s wall clock** | A shopping agent must answer in one turn; the loop must terminate with something | Always produced an answer; the p95 (34.4 s) is a single overrun | ✅ |
| 3.13 | Provider strategy | (a) one provider, (b) provider-agnostic client with a deterministic offline fallback | **(b)** deepseek → minimax → openai → `MockLLM` | A demo must not die because a provider is down, rate-limited or has no key | The VM's provider catalogue changed mid-build (DeepSeek vanished) — the fallback is what kept the demo alive | ✅ |
| 3.14 | `MockLLM` behaviour | (a) canned strings, (b) grounded templating from the tool result | **(b)** — copies price/rating/title verbatim | A fake agent that invents prices would invalidate the whole point of the guardrails | Offline turns remain grounded | ✅ |
| 3.15 | Frontend | (a) Next.js + React build, (b) FastAPI + vanilla JS, (c) React+Vite | **(b)** | No build step, no CDN, no external fonts — it must work on a plane with no network | UI served from one process, 0 npm dependencies | ✅ |
| 3.16 | Streaming | (a) JSON at the end, (b) SSE with typed events | **(b)**: `token | tool | citation | done` | The demo must *show* the agent working; the system panel is the product | Typed events; the UI shows tool chips and citation pills live | ✅ |
| 3.17 | Cold-start latency | accept it, or fix it | **background warm-up thread at startup** + `/api/health` model readiness | A cold first turn was 64–105 s — the fastest way to lose a room | Warm turn 11.7–19.7 s; operator can poll readiness | ✅ |

---

## 4. Guardrail decisions (the part we would defend hardest)

| # | Decision | Options | Chosen | Why | Outcome | Verdict |
|---|---|---|---|---|---|---|
| 4.1 | Where do invariants live? | (a) in the system prompt, (b) in middleware around the model | **(b) middleware** | A prompt is a request; a middleware check is a control | Budget/stock/claim/citation checks ran on **every** turn, including offline ones | ✅ — *this is the most transferable idea in the repo* |
| 4.2 | Does a flagged answer still ship? | (a) block it, (b) annotate it | **(b) annotate + flag, plus a `[Note: …]`** | Blocking loses the user's answer; annotating keeps the trace honest | 2 violations in the final run, both visible to the user and to the trace table | ⚠️ (deliberate; see 4.7) |
| 4.3 | Budget cap | none, or a cap | **$2,000 session cap**, enforced in `checkout_cart` and re-checked after | An agent that spends more than the user intended is a trust failure | Checkout refused, order not created, alternatives offered | ✅ |
| 4.4 | Are cart totals citable values? | (a) no, (b) yes | **(b)** — the cart total is passed to the verifier as a known value | Otherwise every honest checkout refusal is flagged as a fabricated price | One false positive removed | ✅ |
| 4.5 | Is "out of stock" a violation? | (a) yes, (b) only if the answer claims availability | **(b)** | Saying "it is out of stock" is the *correct* behaviour; flagging it trains users to ignore flags | Out-of-stock false positives 3 → 0 | ✅ |
| 4.6 | Are approximate prices claims? | (a) yes, (b) no | **(b) — "~$39.99" is a paraphrase, not a verifiable claim** | An approximate price cannot be checked exactly; flagging it is noise | One false positive removed | ✅ |
| 4.7 | Is the verifier context-aware? | — | **No — and we know it** | It cannot tell "4.9 stars" (asserted) from "Claimed: 4.9 stars | Actual: 4.7" (quoted while correcting) | 2 residual false positives in the final run, both user-supplied quote-and-correct | ❌ **open** — specified fix in `docs/03-evaluation.md §8.3` |
| 4.8 | Citation gate | allow uncited answers, or reject | **reject** factual claims with zero citations | An uncited product claim is a hallucination waiting to happen | Zero-citation factual answers are rejected | ✅ |

---

## 5. Process decisions (how the thing got built)

| # | Decision | Options | Chosen | Why | Outcome | Verdict |
|---|---|---|---|---|---|---|
| 5.1 | Build strategy | sequential, or parallel agents | **6 research agents + 3 build agents against a written contract** | The research alone was 29k words; serial would have taken days | 15 thread instances, one repo, zero merge conflicts | ✅ |
| 5.2 | The contract | (a) trust agents to coordinate, (b) write the interface first | **(b) `demo/CONTRACT.md`** — DDL, exact signatures, event schema, file ownership | "Agents own files, humans own interfaces" | B2's agent code landed before B1's retrieval and still imported and degraded correctly | ✅ — *the single highest-leverage decision of the project* |
| 5.3 | Orchestrator ownership | (a) share everything, (b) own schema + data + integration | **(b)** | The interface is where agents collide | `db.py`, `ingest/`, `CONTRACT.md` and the data pipeline never moved | ✅ |
| 5.4 | Absolute vs relative paths for agents | (a) worktree-relative, (b) absolute paths in the brief | **(b)** | Agents ran in git worktrees but were told to write to `/home/sumit/agentic-shopping/demo/...` | All three builders' code landed in the working repo despite separate worktrees — but this is **accidental, not designed** | ⚠️ (would use a worktree + merge flow next time) |
| 5.5 | Parallel conflict handling | (a) isolate, (b) forbid overlap | **(b) file ownership**, plus the contract | Two agents still touched `retrieval.py`/`lexicon.py` concurrently | One agent detected the conflict and did **not** revert the other's file ("the merged file is coherent") | ✅ (process worked; agent behaviour was good) |
| 5.6 | Duplicate-agent hygiene | (a) leave both running, (b) stop the stale one | **(b) stop + archive the superseded thread** | Two agents editing one file is a race with a paper trail | The stale thread's edit was rejected by the harness (conflict detection) | ✅ |
| 5.7 | Provider/model change mid-build | (a) wait, (b) re-target and respawn | **(b)** — respawn with a model from the new catalogue | After the VM resize, `deepseek/deepseek-v4-pro` vanished from the pi catalogue; two retries failed with `Failed to resolve Pi model` | Both builds were re-run from the contract and completed | ✅ — *proof the contract was sufficient to rebuild from scratch* |
| 5.8 | VM restart mid-build | (a) recover transcripts, (b) rebuild from the contract | **(b)** | A restart killed two agents and both background jobs | Everything rebuilt from `demo/CONTRACT.md` + the ingestion scripts; the DB pipeline is idempotent | ✅ (cost: ~40 min of recompute) |
| 5.9 | Background jobs | run in the foreground, or detached | **detached with `setsid` + resumable checkpoints** | A restart killed an embedding job that had done 8,000 products | `embeddings_done` table made it resumable — it resumed and finished all 101,418 | ✅ |
| 5.10 | Evaluation approach | (a) trust the demo, (b) instrument everything | **(b)** — `traces` table per turn, seeded eval set, 4 full agent runs | "The demo looked fine" is not evidence | 6 real defects found and fixed (§7) | ✅ |
| 5.11 | Reporting one agent run | (a) publish the best run, (b) publish all runs | **(b) all four** | Variance is the finding | Success 0.40 / 0.70 / 0.40 / 0.70 published with the variance explained | ✅ |
| 5.12 | Metric definition | accept the harness's "success", (a) compliance-based | **(b) report both compliance *and* behaviour-correct** | The harness's success metric *rewards* hallucination on adversarial tasks (a correct refusal counts as failure) | 0.70 compliance vs 10/10 behaviour-correct — both published | ✅ |
| 5.13 | Writing style | (a) claims, (b) numbers + caveats | **(b)** | This document and `docs/03-evaluation.md` are full of caveats on purpose | A reviewer can check every number against `demo/eval_report.md` | ✅ |
| 5.14 | Where the job-application material lives | (a) in the repo, (b) outside it | **(b) `~/work-private/agentic-shopping-role/`** | The repo must be public-clean and reusable independently | Nothing job-related was ever pushed | ✅ |
| 5.15 | Delegation of writing | (a) one writer, (b) parallel specialists | **(b)** B5–B7 each owned one document | Speed, and the specialists had the code in front of them | B5's thread hit a provider token-plan 429 *after* delivering its document | ⚠️ |
| 5.16 | Provider rate limit (429) | (a) wait, (b) take over the work | **(b) — I re-ran the evaluation and rewrote the numbers myself** | The harness was finished; the doc still had 26 placeholders | All placeholders filled with measured values; four runs published | ✅ |
| 5.17 | Ports | hard-coded 8000 | **8000 default, `ARIA_PORT` override** | Port 8000 and 8080 were already occupied by other apps on this VM | The demo ran on 8077–8091 during testing; `run.py` stays at 8000 for the public | ⚠️ |
| 5.18 | Python packaging | (a) add to system Python, (b) venv | **system Python** | The box already has torch/sentence-transformers/lancedb installed | `pip install` was blocked by PEP 668 (externally-managed environment) anyway | ✅ (accidental) |
| 5.19 | Do the published numbers live in prose or in a generated file? | (a) prose only, (b) generated appendix + gated prose | **(b)** — `demo/eval_report.md` is machine-generated; `docs/03-evaluation.md` is the narrative, and its tables are **copied** from the appendix by `demo/ingest/sync_docs_tables.py` | A hand-written number drifts; we found real drift (stale tables, a stale duplicate subset table, prose quoting an earlier run) | The drift is now impossible to ship: `demo/tests/test_docs_numbers.py` fails the gate if the two disagree | ✅ |
| 5.20 | Should a sync tool rewrite prose too? | (a) yes, (b) tables only | **(b) tables only** | The first version of the sync script also rewrote prose — it dropped the `### 2.5` heading and duplicated a paragraph | Rewriting prose by script is how you lose a heading; the script now only owns table blocks | ✅ (learned the hard way) |

---

## 6. Things we deliberately did *not* do

| Decision | Rationale |
|---|---|
| No voice ASR server-side | whisper.cpp is 142 MB and CPU-slow; the browser speech API covers the demo story at 1/100th the cost |
| No CLIP-based visual search in the critical path | Image search is a nice demo beat but the risk/benefit is poor on a 4-vCPU box; the endpoint degrades gracefully instead |
| No real checkout or payment | Not needed and legally noisy; `dry_run` semantics throughout |
| No product embeddings for unpriced rows | They cannot be sold; embedding them would inflate the index and the metrics without helping |
| No learned re-ranker / collaborative model | 1.39M sparse users on 4 vCPUs; a deterministic blend is explainable, and explainability is the demo |
| No fabricated MRP or discount | A shopping agent that invents a discount is the failure mode this whole repo exists to prevent |
| No "shopping agent benchmark" dependency | τ-bench retail was downloaded and evaluated as a reference, but our own ESC-based harness is reproducible end-to-end |

---

## 7. Defects the evaluation found (and what they taught)

| # | Defect | Symptom | Root cause | Fix | Status |
|---|---|---|---|---|---|
| 7.1 | Claim verifier flagged budget echoes | `price=2000.0` flagged on a correct refusal | The regex required the amount to follow the keyword literally | Markdown-aware, connector-tolerant (`budget limit of $2,000`), reversed form (`$2,000 budget`), plus a cart-total citable value | ✅ fixed |
| 7.2 | Guardrail flagged truthful refusals | `out_of_stock` on answers that *said* the item was out of stock | StockGuard checked the row, not the sentence | Only a *claim of availability* is a violation | ✅ fixed |
| 7.3 | Unsellable products in answers | 3 turns flagged; agent apologising for products with no price | `in_stock` was a data artefact, and search returned those rows | Exclude unpriced rows from `search_products` | ✅ fixed |
| 7.4 | Approximate prices flagged | `~$39.99` from a review quote flagged | No notion of approximation | Strip approximate amounts | ✅ fixed |
| 7.5 | Verifier cannot read context | "Claimed 4.9 \| Actual 4.7" flagged | Context-insensitive numeric extraction | — | ❌ open (documented) |
| 7.6 | Dense arm measured on a partial index | Numbers were a moving target | The index build ran concurrently with the eval | Re-measured on the finished index (101,458 vectors) | ✅ fixed |
| 7.7 | Demo scripts could not be satisfied | "gift under ₹3,000" over a USD catalogue; "Sony WH-1000XM5" exists only as *Renewed* | Scripts written before the catalogue was inspected | Scripts re-derived from the data; brand-in-title rescue added | ✅ fixed |
| 7.8 | Harness "success" rewarded hallucination | Correct refusals counted as failures | Metric defined as compliance | Report compliance *and* behaviour-correct | ✅ fixed (documentation) |
| 7.9 | Published numbers drifted from the harness | §2.1/§2.3 tables and the §3 narrative quoted an earlier partial-index run; a stale duplicate subset table survived | Tables were hand-edited; nothing compared them to the appendix | `demo/ingest/sync_docs_tables.py` copies the tables; `demo/tests/test_docs_numbers.py` gates the difference | ✅ fixed |

---

## 8. Cost of the whole exercise (honest ledger)

| Item | Number |
|---|---|
| Wall-clock, first pass | ~6 h across 15 agent instances + orchestrator |
| Raw data downloaded | ~16 GB (99% of it Amazon raw JSONL/Parquet) |
| Catalogue built | 194,873 products · 2.5M reviews · 3.62M interactions · 1.39M users · 400k graph edges · 99,441 real Olist orders |
| Embedding index | 101,418 products, 101,458 vectors, ~80 min at ~20 texts/s (after the 5× optimisation) |
| Evaluation cost | retrieval sweep 1,290 s + 4 agent suites × ~4 min |
| Published words | ~25k in `docs/` + ~22k in `research/` |
| Public repo size | 1.8 MB of git history; no data committed |
| Lines of application code | ~4,500 across 13 modules |

---

## 9. The five decisions we would defend in a review

1. **Write the interface before the code.** `demo/CONTRACT.md` is why 3 agents could write a system in
   parallel without a single merge conflict, and why a VM restart cost us 40 minutes instead of a day.
2. **Deterministic parsing for the filter, LLM for the prose.** Constraint precision was 1.00 on price,
   rating and brand at zero latency and zero cost.
3. **Guardrails in middleware, not in prompts.** Every invariant we care about was checked outside the
   model's discretion — and every one of them fired on a real turn.
4. **Instrument before demoing.** The `traces` table turned "it looked fine" into 6 fixable defects and
   a publishable evaluation.
5. **Publish the variance.** Four runs, four different answers, and the *honest* metric definition
   (behaviour-correct 10/10 vs compliance 0.70) are the most interesting thing in the repo.
