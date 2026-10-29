# 08 — Thought leadership: what this work is worth publishing

The build produced a set of claims that are **defensible, specific and measured** — the kind of writing
that earns attention because the numbers are real and the caveats are stated. This document turns the
work into publishable pieces.

Rules we held ourselves to:

* **No claim without a number or a citation.** Everything below points at `demo/eval_report.md`,
  `docs/03-evaluation.md`, `research/*.md` or the code.
* **Publish the caveats.** The interesting parts of our results are the ceiling, the variance and the
  open defects. A post that hides them is a post nobody in the field will believe.
* **No vendor or employer framing.** The repo is public and stands alone.

---

## 1. The seven positions this work earns us the right to take

| # | Position | Why we can say it | Evidence anchor |
|---|---|---|---|
| P1 | **"The chat is the demo; the retrieval is the product."** | We measured the retrieval stack on a real gold set | hybrid R@10 0.1686 vs lexical 0.1102 and dense 0.1589; cross-encoder +1.2 nDCG — `docs/03-evaluation.md §2.1–2.2` |
| P2 | **"Ground every claim, or you are just a confident writer."** | We built citation-first answers and a verifier that caught its own false positives | 71.6 citations/turn; 2 residual false positives both explained — `docs/03-evaluation.md §2.6, §8.2` |
| P3 | **"Guardrails belong in middleware, not in the prompt."** | A prompt is advisory; a check is a control — and ours fired on real turns | budget refusal, stock truth, citation gate — `docs/02-architecture.md §5` |
| P4 | **"Agent metrics are noisy; publish the variance."** | Eight runs of the same 10-turn suite gave 0.40–0.80 compliance success; retrieval metrics were identical to four decimals in every run | `docs/03-evaluation.md §8.2` |
| P5 | **"Your metric definition decides what your agent learns."** | Our harness *rewarded* hallucination on adversarial tasks until we fixed the definition | compliance 0.80 vs behaviour-correct 10/10 — `docs/03-evaluation.md §8.1` |
| P6 | **"The trust layer is the product in commerce."** | Amazon's own assistant has documented failure modes; the category is moving fast | `research/01-amazon-ai-shopping.md`, `research/05-india-emerging-markets.md` |
| P7 | **"Agents own files, humans own interfaces."** | 3 build agents, 1 contract, zero merge conflicts, and a VM restart cost 40 minutes not a day | `demo/CONTRACT.md`, `docs/04-learnings.md §1` |

---

## 2. Publishable pieces (ranked by expected signal)

### Piece 1 — "We gave a shopping agent 195,000 real products and 2.5 million reviews. Then we measured whether it was any good."

*Format*: long-form blog / technical deep dive (1,800–2,500 words) · *Links*: the repo + `docs/03-evaluation.md`

**Hook**: "Chatbots can describe a product. Can they be *right* about one? We built the smallest version
of that question we could and then spent more time on the measurement than on the chat."

**Structure**
1. The question, and why a mock is a dead end (synthetic catalogues hide the hard cases).
2. What we built, in one diagram: hybrid retrieval → agent loop → guardrails → cited answer.
3. The numbers, with the ceiling stated up front (56.5% of gold rows reachable — 48% of the catalogue
   has no price in the source data).
4. The four arms: lexical 0.1102 · dense 0.1589 · hybrid 0.1686 · hybrid+rerank 0.1686 with nDCG 0.1020.
5. What the cross-encoder actually bought: **ordering, not coverage** (Recall@20 unchanged, MRR +1.5).
6. The guardrail table: what fired, what it looked like, and the two false positives we had to chase.
7. What we got wrong: the metric that rewarded hallucination; the first-turn latency of 105 seconds.
8. What we would do with a week more: contextual claim verification, a proper session-state reducer,
   pincode-level serviceability as a hard gate.

**Pull quotes**
- "RRF fusion beat either channel alone; the cross-encoder bought ordering, not coverage."
- "A guardrail that fires on correct behaviour trains people to ignore guardrails."

**Do not claim**: that we "solved agentic shopping"; that our numbers generalise beyond a 3-category
Amazon slice; anything about our latency on hardware other than a 4-vCPU VM.

---

### Piece 2 — "Prompt it or constrain it: we removed the LLM from the part of shopping that must be exact"

*Format*: engineering blog (1,200–1,600 words) + code snippet

**Argument**: constraint parsing (price, rating, brand, category, negation, code-mixed) is a *filter*,
not a *generation* task. We made it deterministic — regex plus a catalogue-derived lexicon plus a
Hinglish seed — and got precision/recall 1.00 on price, rating and brand across 41 hand-written
queries, at zero latency and zero cost, and it works offline. The LLM stayed on the prose.

**Sub-points**: why not let the model parse the filter (reproducibility, cost, offline-safety, and the
model's discretion is exactly where commerce mistakes become legal problems); the catalogue-derived
lexicon (4,031 terms, junk brands blocked); the code-mixed case ("saste earphones under $50 for mom,
no plastic"); the one place we were wrong — category canonicalisation (recall 0.92, three mismatches).

**Pair it with**: a 30-line snippet of `parse_constraints` and the evaluation table.

---

### Piece 3 — "Guardrails are middleware or they are suggestions"

*Format*: opinionated short post (700–900 words) + the guardrail module

**Argument**: every commerce invariant we cared about (budget cap, stock truth, price/rating accuracy,
citation presence) was implemented as a *check around the model*, not as an instruction to it. Show the
four guards, show the refusal, and show the two false positives we had to fix — because a guardrail
that cries wolf is worse than no guardrail.

**Evidence**: budget refusal (`status: refused`, no order created); stock truth (the guard now only
fires when the answer *claims* availability); the verifier's false-positive hunt (markdown budget
echoes → derived comparisons → approximate prices → context-blind quotations, which is still open).

**Best line**: "A prompt is a request. A middleware check is a control."

---

### Piece 4 — "Your agent benchmark is measuring compliance, and compliance is not correctness"

*Format*: data-heavy post (900–1,200 words) with the four-run table

**Argument**: our harness marked a turn successful when the agent did what the user asked. On three of
five adversarial tasks the *correct* behaviour was to refuse — so the metric rewarded hallucination.
We now publish two numbers: compliance (0.80) and behaviour-correct (10/10). Add the variance table:
0.40 / 0.70 / 0.40 / 0.70 / 0.80 / 0.80 / 0.70 / 0.80 across eight runs of the same 10-turn suite — with the
LLM mode of each run (mock or live) labelled, because the two are not comparable on token counts.

**The generalisation**: in any agent benchmark, ask what "success" means when the agent is right and the
task is impossible. If the answer is "the agent succeeded", the benchmark measures compliance.

---

### Piece 5 — "Three agents, one contract, zero merge conflicts"

*Format*: engineering-process post (1,000–1,400 words) with the contract excerpt

**Argument**: we built the demo with three AI agents writing code in parallel. The enabling artefact
was not the prompt — it was a 200-line contract: the DDL, the exact function signatures, the streaming
event schema, and a file-ownership table. The rule: **agents own files, humans own interfaces.**

**Proof points**: zero merge conflicts; a VM restart killed two agents and we rebuilt them *from the
contract* in 40 minutes; the model catalogue changed under us mid-build and the contract still described
the system; the failure mode (two agents editing one file) was caught by file ownership plus the
harness's conflict detection.

**Include**: an excerpt of `demo/CONTRACT.md` (the DDL + event schema are the interesting parts).

---

### Piece 6 — "What agentic commerce actually needs, from the catalogue up"

*Format*: strategy post (1,400–1,800 words) · *Source*: `research/02-competition-landscape.md` + `docs/05-moats.md`

**Argument**: the agent is becoming the interface and the marketplace is becoming the plumbing. That
changes where the margin sits, and it makes a specific set of assets defensible: catalogue integrity,
transaction history, fulfilment truth, evaluation data, and trust. Include the moat matrix with the
asymmetry stated plainly: incumbents hold the traffic, the catalogue and the logistics; a startup cannot
out-Amazon Amazon and should not try — it should be a better *verifier*, a better *evaluation layer*, or
the merchant that the agent's agent actually reads.

**Pair with**: `docs/06-emerging-markets.md` — in emerging markets the trust problem is sharper
(code-mixed voice, COD, pincode serviceability, below-MRP fraud), so "trust as product" is not a
nicety, it is the product.

---

### Piece 7 — "A demo you cannot measure is a demo you cannot defend"

*Format*: short post (600–800 words) · *Audience*: PMs and founders evaluating agent prototypes

**Argument**: the single highest-leverage thing we did was instrument before demoing. A `traces` table
(one row per tool call, with tokens, latency and flags) turned "it looked good" into six fixable
defects and a publishable evaluation. Give the checklist: instrument turns, build a gold set from real
queries (we used Amazon's Shopping Queries), measure retrieval *and* answer quality separately, and
run the suite four times before believing a number.

---

## 3. Formats and venues we can hit with this

| Format | Length | Best piece | Where it lands |
|---|---|---|---|
| Engineering blog (company or personal) | 1,500–2,500 words | Piece 1 | Deep-dive readers; technical leadership on Hacker News |
| Opinion/short post | 600–900 words | Piece 3 | LinkedIn, X, engineering blogs |
| Conference talk (30–40 min) | talk + live demo | Pieces 1 + 3 + 5 | Applied ML, recsys, product-engineering tracks |
| Guest newsletter | 900–1,200 words | Piece 4 | Data/ML newsletters |
| Internal talk (company) | 45 min + live demo | Piece 7 | Credibility with the team, then reuse the demo |

**Demo-in-a-talk format that works**: (1) the adversarial query — "is the Beats Studio3 4.9★ and 60%
off?" — and show the agent correcting it from the database; (2) the over-budget checkout that refuses;
(3) the code-mixed query; (4) the system panel showing tools, citations, latency and flags. That is a
four-minute demo that makes the architecture legible.

---

## 4. Claims we should NOT make (and why)

| Don't say | Say instead |
|---|---|
| "Our agent understands shopping" | "Our agent's answers were 100% grounded on 5 demo turns and 81% on the full suite" |
| "Hybrid retrieval beats BM25" (as a universal) | "On the Amazon Shopping Queries gold set, hybrid retrieval beat lexical-only by +4.8 pts Recall@10" |
| "We solved the latency problem" | "A warm turn is 11.7–19.7 s on 4 vCPUs; the cold first turn was 64–105 s until we added a warm-up" |
| "Guardrails prevent hallucinations" | "Guardrails caught fabricated prices and ratings in our suite; two false positives remain open" |
| "This is what Amazon's Rufus does under the hood" | "Amazon has published a RAG + planner + 'hydration' architecture for Rufus; our reading is a synthesis of public sources" |
| "Emerging markets need X" (as fact) | "In the Indian market the binding constraints we could source are COD economics, pincode serviceability and review trust" |

---

## 5. Publishing sequence (6 weeks)

| Week | Piece | Goal |
|---|---|---|
| 1 | Piece 1 (deep dive) | Anchor the technical credibility; link the repo |
| 2 | Piece 3 (guardrails) | The opinion piece that travels furthest |
| 3 | Piece 5 (contract/agents) | Process novelty; attracts builders |
| 4 | Piece 2 (constraint parsing) | Practical, copy-pasteable, high search value |
| 5 | Piece 4 (metrics) | Data point that gets quoted in other people's threads |
| 6 | Piece 6 (commerce/moats) | The strategy register; the one that reaches product leadership |

Each piece links the repo and the specific measurement doc, so the traffic compounds on the artefact
rather than on the post.

---

## 6. How to keep the repo and the writing in sync

* Every number quoted above lives in `demo/eval_report.md` (machine-generated) and
  `docs/03-evaluation.md` (narrative). If the code changes, re-run
  `python -m app.eval --all` and update both before publishing.
* The code fingerprint in `demo/eval_report.md` records the exact commit the numbers came from — quote
  it when someone asks "which version is this?".
* Keep the caveats in the post body, not in a footnote. The caveats *are* the differentiator.
