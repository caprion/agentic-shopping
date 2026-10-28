# Aria — an agentic shopping assistant, built end-to-end on open data

> A working prototype of the next shopping surface: not a search box with a chatbot bolted on,
> but an **agent that reasons over a real catalogue, cites its evidence, and refuses to guess** —
> with an **evaluation harness** that proves the claims instead of asserting them.

Everything here is reproducible from public datasets. No proprietary catalogue, no API keys
required for the core loop, no invented product data.

```
   user (text / voice / image)
        │
        ▼
   ┌──────────────────────── agent loop (max 8 tool calls, 60 s cap) ─────────────────────────┐
   │  constraint extraction → candidate generation → fused ranking → cited answer → compare   │
   │  → cart → mock checkout → order                                                       │
   └─────────────────────────────────────────────────────────────────────────────────────────┘
        │                    │                        │
        ▼                    ▼                        ▼
  hybrid retrieval     evidence tools            guardrail middleware
  BM25 + dense +      product / reviews /       budget cap · stock truth
  co-purchase graph   comparison axes           claim verification
        │              external web (Buy-for-Me) citation gate
        ▼
   SQLite + FTS5 · LanceDB · product graph · traces
```

---

## 1. Why this project exists

Agentic commerce is being sold as a chat interface. The hard parts are elsewhere:

| The chat is trivial | The hard parts (what this repo implements) |
|---|---|
| "Ask a question" | turning a sentence into **structured constraints** (budget, rating, brand, negation) |
| "Show products" | **hybrid retrieval** across lexical, dense and behavioural signals, fused and reranked |
| "Summarise reviews" | **evidence-bound answers**: every claim carries a resolvable citation to a product field or a review |
| "Add to cart" | **guardrails**: budget caps, stock truth, claim verification, refusal to recommend without evidence |
| "It works" | an **evaluation harness** — Recall@k / nDCG / MRR on the Amazon Shopping Queries gold set, groundedness on agent answers, latency and cost per turn |

A demo that only shows the chat proves nothing. A demo with a **measured** retrieval and
groundedness layer proves you understand the product.

---

## 2. What's in the box

| Path | What it is |
|---|---|
| `demo/` | The application: FastAPI + SQLite/FTS5 + LanceDB + agent loop + guardrails + a vanilla-JS UI |
| `demo/ingest/` | The data pipeline — downloads/normalises public datasets into one SQLite file |
| `research/` | Six evidence dossiers (≈29k words, every claim sourced): Amazon's AI shopping surface, the competitive landscape, the open-dataset catalogue, the reference architecture, emerging markets, and the demo eval plan |
| `docs/` | The write-up: what we built, what we measured, what we learned, and where the moats are |
| `dev/prompts/` | The exact briefs used to run the research and the build in parallel with AI agents — the process is part of the artifact |

---

## 3. The demo

```bash
git clone <this-repo> && cd agentic-shopping
pip install -r demo/requirements.txt

# 1) fetch + build the catalogue (public data; ~15 min, ~1.5 GB)
bash demo/scripts/fetch_data.sh
python demo/ingest/build_db.py --stage all

# 2) embed the catalogue for dense retrieval (~80 min on 2 vCPU; incremental)
python demo/ingest/build_embeddings.py

# 3) run
python demo/run.py          # → http://localhost:8000
```

`demo/run.py` boots one uvicorn worker. The UI has a persona switcher, one-click **demo scripts**,
a streaming chat with tool-call chips, inline citation pills, a cart/checkout panel, and a
**system panel** that exposes per-turn tools, latency, tokens and guardrail flags.

Everything degrades gracefully: no network → deterministic offline agent; no embedding model →
lexical-only retrieval; no LLM key → templated grounded answers. The demo never dies on stage.

---

## 4. Data (all public, all real)

| Source | Used for | Link |
|---|---|---|
| Amazon Reviews 2023 (McAuley-Lab) | catalogue: titles, prices, ratings, features, images, categories | [HF dataset](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023) |
| Amazon Reviews 2023 (raw reviews) | review evidence, aspect quotes | same |
| Amazon 5-core ratings | real users with purchase history → personalisation | same |
| Amazon Shopping Queries (ESC) | **gold eval set**: query → relevant product labels | [amazon-science/esci-data](https://github.com/amazon-science/esci-data) |
| Olist Brazilian E-Commerce | **real transactions**: 99k orders, customers, payments, delivery times, reviews | [Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) |
| τ-bench (retail) | agent-task benchmark for tool-use evaluation | [GitHub](https://github.com/sierra-research/tau-bench) |
| Google Product Taxonomy | category normalisation | [Google](https://www.google.com/basepages/categories.htm) |

Raw downloads are **not committed** (≈16 GB). `demo/scripts/fetch_data.sh` reproduces them; the
pipeline in `demo/ingest/` is deterministic and idempotent.

---

## 5. Honest limitations

These are real, and worth reading before the demo:

* The catalogue is **Amazon US** (Electronics, Musical Instruments, Industrial & Scientific) — prices
  are USD. The **personas** are India-flavoured (Hinglish, ₹, delivery-by-Friday) because that is
  where agentic commerce is most interesting, but the catalogue is not Indian retail.
* Real **transactions** come from Olist (Brazil), not Amazon — Amazon exposes no public order stream.
  They populate customer/behaviour priors, not the product catalogue.
* Discounts/MRP are **not** modelled: the Amazon 2023 metadata has no list price, and we do not
  fabricate one. Price-history is explicitly labelled *simulated*.
* Checkout is a **mock**. No payment rail, no real merchant, no real fulfilment.
* Voice and image inputs are **thin stubs** (browser speech API + upload → text/CLIP fallback).
  The engineering effort went into retrieval, grounding and evaluation instead.

---

## 6. Research index

| Dossier | Question it answers |
|---|---|
| [01 — Amazon's AI shopping surface](research/01-amazon-ai-shopping.md) | What does Amazon's assistant actually do today, what is it good at, where does it break? |
| [02 — Competitive landscape](research/02-competition-landscape.md) | Who is building agentic commerce, on what business model, and which moats are real? |
| [03 — Open dataset catalogue](research/03-open-datasets.md) | Which datasets exist, what do they cost, what is verified, what should you actually use? |
| [04 — Reference architecture](research/04-architecture.md) | The full technical design: retrieval, agent, guardrails, evaluation, build order, risks. |
| [05 — Emerging markets](research/05-india-emerging-markets.md) | Voice, vernacular, trust, COD, pincode logistics — the physics of agentic commerce in India. |
| [docs/](docs/) | The write-up: measured results, lessons, and the moat map. |

---

## 7. How the work was run

The research and the build were parallelised across independent AI agents against a **written
contract** (`demo/CONTRACT.md`) — schema, module signatures, file ownership — so the branches could
never collide. The briefs are in `dev/prompts/`. The orchestrator owned the schema, the data
pipeline and the integration.

That contract is the interesting part: it is what makes agent-parallel development safe, and it is
reusable for any "build a thing from many agents" project.

---

## 8. Licence

Code: Apache-2.0 (see `LICENSE`).
Data stays with its original licences — the Amazon Reviews datasets are research-use, Olist is
CC BY-NC-SA 4.0. See `research/03-open-datasets.md` for the full licence table.
