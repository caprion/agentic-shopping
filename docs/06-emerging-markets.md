# 06 — Emerging markets

**Scope.** India-first: what changes when the shopper is **voice-first, vernacular, price-sensitive**, and
served by a marketplace that must **physically deliver** — to a specific pincode, with a specific payment
mode, at a price they will not stretch past.

**Method.** Built on [`research/05-india-emerging-markets.md`](../research/05-india-emerging-markets.md) (all
its URLs accessed **2026-10-28**), cross-read with
[`research/01-amazon-ai-shopping.md`](../research/01-amazon-ai-shopping.md) and
[`research/02-competition-landscape.md`](../research/02-competition-landscape.md), and checked against our
own code in `demo/app/`. Six URLs were **re-fetched for this document on 2026-10-28** and carry a ✓; other
external claims follow the dossier's own access date. Our judgement is marked **Speculation**; claims about
the prototype cite a file path, not a URL.

---

## 1. The physics of the market

Five constraints do most of the work. They are numbers with sources, not opinions about India.

| Constraint | The number | Source | What it forces on the agent |
|---|---|---|---|
| Voice is the input surface | Voice search queries growing **270%/yr**; Indians use voice **~2× the global average** | [LiveMint/MMA-Isobar](https://www.livemint.com/technology/tech-news/voice-search-queries-in-india-are-growing-at-270-per-year-report-11619166188074.html) ✓ *(2021 report)*; [India Today/Google](https://www.indiatoday.in/technology/news/story/india-is-using-voice-queries-nearly-two-times-more-than-rest-of-the-world-says-google-1878152-2021-11-18) | ASR is on the critical path; the transcript is *unverified input* |
| The language is code-mixed | Hinglish WER across models on identical audio: **26.97%–69.53%** | [Deepgram](https://deepgram.com/learn/hinglish-voice-ai-speech-recognition) ✓ | Constraints cannot come from ASR alone; parse deterministically, then re-confirm |
| The shopper is new and price-first | **290–300M** online shoppers; **~65%** of new shoppers from Tier-2+; penetration in small cities only **25–30%** | [IBEF](https://www.ibef.org/industry/ecommerce) ✓ | Budget is a *hard* constraint; the explanation is the product |
| Delivery is physical and local | Return rate **15–35%**; COD RTO **20–40%** vs prepaid **5–10%**; reverse logistics **1.5–2×** forward; one return costs **20–65%** of item value | [Shipmozo](https://www.shipmozo.com/blog/ecommerce-returns-and-reverse-logistics) ✓ | The agent's unit of work is *fulfilment risk*, not conversion |
| Money moves differently | UPI is **81%** of retail payment volume, **~54%** of e-commerce payments; NPCI's 30% cap deferred to **31 Dec 2026** | [IBEF](https://www.ibef.org/industry/ecommerce) ✓; [PaymentsCMI/EBANX](https://paymentscmi.com/insights/india-2025-ecommerce-payments-trends) | Checkout hands off to someone else's app; COD vs prepaid is a product decision |

**Voice is real; transactional voice is not.** The gap is precise: voice serves **search, discovery and
status**, while checkout stays tap. Meesho's ElevenLabs voice agent handles **60,000+ calls a day** in Hindi
and English ([ElevenLabs](https://elevenlabs.io/blog/meesho) ✓); the widely quoted "95% resolution" comes
from a secondary trade write-up, not from ElevenLabs or Meesho, so treat it as directional only. MakeMyTrip's
assistant handles **>3M conversations per quarter**, **>45%** of it from Tier-2 and smaller cities, mostly
Hinglish ([Deepgram citing ET](https://deepgram.com/learn/hinglish-voice-ai-speech-recognition) ✓). The
pattern: **voice works for service and dies at the purchase.** The wedge is not "can it talk" but "does the
transaction survive the conversation".

**Code-mixing is the grammar, not an accent.** Deepgram distinguishes three switching levels:
inter-sentential, intra-sentential ("Mujhe ek meeting schedule karni hai") and intra-word ("adjust-karo"). The
third breaks naive pipelines: you cannot tell where one language ends inside a token. There is also no
standardised written form — the same word appears in Devanagari, Roman script or a mix, several romanisations
each. Hence the rule: **never let the transcript be the constraint.** Parse deterministically, then echo the
parsed constraints back.

**Price sensitivity is structural.** Flipkart leads at **48%**, Amazon is #2, Meesho has the fastest-growing
user base ([ToI/AllianceBernstein](https://timesofindia.indiatimes.com/gadgets-news/indias-online-shopping-market-how-flipkart-amazon-and-meesho-are-placed/articleshow/107200100.cms));
Amazon has committed **~$75B** to India by 2030 and delivered **41 crore** same/next-day Prime items in 2024
([TechCrunch](https://techcrunch.com/2025/12/10/amazon-to-invest-additional-35b-in-india-by-2030-taking-total-planned-spending-to-75b),
[IBEF](https://www.ibef.org/industry/ecommerce) ✓). Value commerce — Amazon Bazaar, Meesho, quick commerce —
is the growth segment. A shopper hunting the lowest *total* price, delivery and return cost included, is not a
discount-hunting user; they are a risk-budget user.
**The growth is below the metros.** Tier-3 cities grew **21% YoY** and produced **38%** of order volume in the
summer-2025 sales; Bharat contributes roughly half of incremental e-retail orders; **>70%** of new Prime
sign-ups during Prime Day 2025 came from Tier-2/3 cities ([IBEF](https://www.ibef.org/industry/ecommerce) ✓).
Mobile-only, often first-time, frequently COD-preferring. **Speculation:** the agent that wins here is not
the one with the best model — it is the one that makes the fewest unrecoverable mistakes per thousand orders.

**Serviceability is a lookup, not a promise.** India is not one logistics network: a SKU can be in stock
nationally and still undeliverable — or deliverable in two days rather than seven — to a given pincode.
Meesho's *GeoIndia* LLM, which interprets India's unstructured addresses, reportedly improved geocoding accuracy
by **20 percentage points**
([TechCircle](https://www.techcircle.in/2026/08/03/how-meesho-is-rewiring-digital-commerce-with-ai-at-its-core) ✓).
**An agentic promise ("Friday, to Pune") is only true if it was resolved at pincode level before it was made.**

---

## 2. What actually breaks

The failures are not in the middle of the distribution. They are at the edges, and each one produces a
*physical* cost — a return, a wrong model, broken trust.

| Failure | Why | Consequence |
|---|---|---|
| ASR mangles SKUs and brand names | Model numbers are phonetically brittle ("A6400" ↔ "A6000", "Realme" ↔ "Redmi") | Wrong product shipped → return at 20–65% of value |
| Spoken prices are ambiguous | "₹10 hazar" / "teen hazaar" / "₹1 lakh" — digit normalisation is a guess | Wrong hard filter → wrong basket → wrong quote |
| Transliteration is not injective | Many spellings per word; homophones across languages | Category miss, or a false brand match |
| Negation has different morphology | Hindi negation is post-verbal and inflected ("plastic nahi chahiye"), not adjacent | The agent filters on the wrong thing — or drops the constraint |
| Transcripts are unverifiable | WER up to **69.53%** on code-switched audio | Every downstream constraint inherits the error |
| The catalogue is unstructured | Listings are image-based, mistranslated, seller-written | Retrieval is only as good as the listing it parses |

*(Sources: dossier §4–§5; [Deepgram](https://deepgram.com/learn/hinglish-voice-ai-speech-recognition) ✓.)*

**Our own negation handling shows the edge case is real, not theoretical.** `demo/app/retrieval.py` matches
negation markers (`not|no|without|avoid|except|other than|besides|excluding|don't|dont|nahi|nahin`) and then
consumes the **next one or two tokens** as the negated target. That is right for English ("no plastic") and
for pre-verbal Hindi. For post-verbal Hindi — *"plastic nahi chahiye"* — it binds the negation to `chahiye`
("want") and leaves `plastic` in the free text as a *positive* requirement. The filter is exactly inverted
from the intent, and it fails silently: the answer is simply the wrong product set.

**The same gap shows up in our own parser, measured.** `parse_constraints("mere bhai ke liye ek camera
chahiye budget teen hazaar")` — the scene-1 utterance below — returns **`price_max: null`** and leaves
`"teen hazaar"` in the free text. The parser handles *written* money ("3000", "₹3000 se kam", "1.5 lakh")
but not the *spoken* form; the ceiling silently disappears and the agent searches without it. (The English
"no plastic" in the same style of query does parse correctly, as `not_attribute: ["plastic"]`.) A missing
constraint is worse than a wrong one, because nothing complains.

**The dossier's failure list is not five bugs.** Beside its demo persona — *"a gift for my brother's birthday
under ₹3000, delivery to Pune by Friday"* — sit ASR brand confusion, spoken-number digit mismatch,
fake-discount propagation, the COD return spiral and pincode overpromising. **Speculation:** one bug in five
coats — *the system makes a commitment it cannot verify at the moment it makes it.* Better models and regexes
are the wrong lever; the fix is architectural: **make the unverifiable claim impossible to emit.**

---

## 3. The trust product

Where a ₹500 mispriced item costs the seller more to return than to sell, and a counterfeit is
indistinguishable from a genuine one until the box is open, the agent is not a shopkeeper. **It is a
guarantor**, and its failures become the marketplace's brand failures. That changes what the product *is*.
Three things become first-class features:

1. **Deal verification.** A discount is a claim about history, not about two numbers. The agent must be able
   to say "MRP ₹4,999, listed ₹2,799 — at the 90-day low" or "44% off an MRP that was itself raised last
   month". Indian law requires MRP display and "Below MRP" is the dominant UI pattern, which makes
   fake-MRP inflation a live fraud the agent must not propagate.
2. **Authenticity signals.** Brand-fulfilled vs marketplace-fulfilled, warranty route, counterparty type,
   review-text authenticity. The agent may say "I cannot verify this is genuine"; it may not imply it by
   omission. On a catalogue like Meesho's (~95% unbranded, dossier §2) the *absence* of a signal is itself
   the answer.
3. **Review-truth summarisation.** "4.6★ from 1,200 reviews" is not evidence — fake reviews are documented on
   Amazon India. The evidence is *which* reviews say *what*, quoted and citable, plus a read of the
   distribution rather than the mean.

**The hardest internal trade-off is ads vs organic.** Amazon Ads did **~$68B** globally in 2025
([Marketplace Pulse](https://www.marketplacepulse.com/articles/amazons-expansive-advertising-breaks-new-records));
Amazon's India ad revenue is reported around **$1B** and doubling in three years
([LinkedIn/Sidh Jhawar](https://www.linkedin.com/posts/sidjhawar_advertising-retailmedia-transactions-activity-7375801038785785856-XPmT));
Flipkart's and Myntra's *combined* ad revenue rose **27% to ₹7,232 crore (~$836M)** in FY25
([IBEF](https://www.ibef.org/industry/ecommerce) ✓). An assistant that hides sponsored results cannibalises
that business; one that surfaces them silently spends the trust it just earned. Three rules follow, and they
are product rules, not policy statements:

* **Ranking and verification are separated.** The verdict is computed on organic facts only. A sponsored
  placement can be *shown*; it can never change the verdict, and it can never be why something is called
  genuine or a deal verified.
* **Disclosure is fixed-format and unskippable.** "Sponsored" is a labelled field in the answer schema, not
  prose the model paraphrases. If it can be paraphrased away, it is not a disclosure.
* **Ads integrity is a measured metric** (§5). If sponsored share rises while trust metrics fall, the product
  is failing regardless of GMV.

---

## 4. What our prototype actually demonstrates — and what it does not

The prototype is **Amazon US, Electronics/Musical Instruments/Industrial, priced in USD**. It is not an India
prototype. What it demonstrates is that the market-independent hard half — parse, ground, guard — is buildable
and instrumented.

| Market need | What the prototype does | Where | Gap |
|---|---|---|---|
| Code-mixed parsing | Deterministic constraint parser + hand-written Hinglish lexicon (**82 seed entries → 28 concepts**): "₹3000 se kam", "saste", "mehenga", "1.5 lakh", "gift for brother" | `demo/app/lexicon.py` (`SEED_HINGLISH`), `demo/app/retrieval.py` (`_parse_constraints`) | A seed lexicon, not a vernacular model; the 41-case eval suite holds **6** code-mixed/INR cases |
| Constraint extraction before retrieval | Price/rating/brand/category/negation/sort extracted in code, pushed into the SQL pre-filter | `demo/app/retrieval.py` (`_where_clause`, `_hard_filter`) | No pincode, no delivery window, no payment-mode constraint |
| Citation-backed review synthesis | Every fact and quote carries `{"product_id", "field"\|"review_id", "value", "quote"}` | `demo/app/tools.py` (`_cite`), `demo/app/agent.py` (`_mark_citation`) | Review *authenticity* unchecked — real reviews, no authenticity score |
| Budget guardrail | `BudgetGuard` refuses a cart over the session cap; the cap is middleware, not prompt | `demo/app/guardrails.py` | Cap is **USD $2,000** (catalogue is USD); no ₹ cap, no COD exposure |
| Refusal to fabricate a discount | The 2023 Amazon metadata has **no MRP** — 93,455 of 194,873 rows have `price_mrp` NULL and **none** has an MRP above the sell price — so no struck-through price is shown; price history exists but returns `"label": "simulated"` with an explicit note | `demo/app/tools.py` (`_t_get_price_history`) | History is synthetic, so it cannot *verify* a below-MRP claim — it demonstrates the labelling discipline |
| Grounded answers | `ClaimVerifier` re-parses every price/rating/%-off in the final answer and re-checks it against DB rows; `CitationGate` rejects an uncited factual answer; `StockGuard` re-reads stock | `demo/app/guardrails.py`, post-pass in `demo/app/agent.py` | It checks arithmetic against the catalogue, not truth in the market |
| Voice input | `/api/voice` takes a client-side transcript, with optional server-side `faster-whisper` (base, CPU, int8) | `demo/app/main.py` (`_transcribe`) | No Indic model, no code-switching handling, no TTS, no ASR-confirmation loop |
| Checkout | Mock checkout; totals from DB prices only; budget refusal surfaced to the user | `demo/app/tools.py` (`_t_checkout_cart`) | No UPI, no COD, no RTO risk |

**What it explicitly does not do:** no pincode serviceability; no COD or prepaid; no vernacular TTS; no
authenticity checks; no GST-inclusive MRP display; no address geocoding; no India catalogue; no spoken-price
confirmation; no ad/verification separation, because it has no ads. The prototype's Indian-ness is
**parsing-level, not physics-level** — the parsing, grounding and guardrail machinery is portable; the physics
has to be built.

---

## 5. A design for the emerging-market agent

**What it should do**

| Behaviour | Rule | Why |
|---|---|---|
| **Speak back the constraint** | Before retrieval, echo the parsed constraints in the shopper's own words and numbers ("camera gear under ₹3,000, deliver to Pune 411001 by Friday — right?") | The transcript is 27–70% wrong; the echo is the cheapest correction available |
| **Confirm before acting** | No cart mutation, no checkout, no auto-buy until the shopper confirms a summary containing SKU, price and pincode | Kills the ASR-SKU and spoken-price bug classes at once |
| **Price in language** | Render amounts as they are said ("₹2,999 — do hazaar nau sau ninety-nine rupaye"), always all-inclusive (MRP incl. GST) | Removes digit ambiguity; meets the MRP-display floor |
| **One-tap compare** | Two or three options side by side on the axes asked for, every cell cited | A spoken shopper cannot scan a grid; the comparison *is* the answer |
| **Pincode check as a hard gate** | ETA only for SKUs already resolved at that pincode; unresolvable SKUs excluded, not annotated | Pincode overpromising is the costliest hallucination here |
| **Deal + authenticity verdict** | Every recommendation carries both verdicts: `deal` (verified against history) and `authenticity` (signal-backed, or explicitly unknown) | The agent is the guarantor; silence is a false claim |
| **COD→prepaid nudge** | Offer the ₹30–₹50 prepaid incentive Indian operators use to shift order mix, in one line (RTO 20–40% vs 5–10%) | Aligns the shopper's cost with the platform's economics |

**What it should refuse:** any delivery date for a SKU not resolved at the pincode; any "% off" without a
price-history row, and any history shown without its `simulated`/sourced label; any "genuine/original" without
a signal; any budget or model number taken from an unconfirmed transcript; auto-checkout on COD above a value
threshold or to a high-RTO pincode without fresh confirmation; any paid placement in the verdict.

**How the metrics should change**

| Metric | Definition | Why |
|---|---|---|
| **Completion rate** | Sessions ending in a *delivered, not-returned* order — not a chat, not a click | Chat volume rewards conversations; this market punishes unfinished ones |
| **Return-rate reduction** | Returns per 1,000 orders by category vs the 15–35% baseline | The dominant cost line (20–65% of value; 1.5–2× reverse logistics) |
| **COD→prepaid conversion** | Share of COD-eligible orders converted to prepaid | Attacks the 20–40% RTO penalty |
| **Verification accuracy** | % of deal/authenticity verdicts correct on audit | The agent's core claim must be measured, not asserted |
| **Constraint-parse accuracy (code-mixed)** | Per-field precision/recall on a Hinglish set, spoken and typed | A parse error upstream is a return downstream |
| **False-refusal rate** | % of legitimate requests declined | A guardrail that refuses everything is safe and useless |
| **Ads integrity** | Sponsored share, and the delta in trust metrics at fixed sponsored share | Makes the ads-vs-organic trade-off measurable |

Meesho's framing is the precedent: models ship against offline benchmarking, controlled online experiments
and production monitoring, and *"a model doesn't ship because it's impressive. It ships because it pays"*.
Its vernacular assistant Vaani reached **1.5M+ users in its first month** with users recording a **22%
higher conversion rate** — a business metric, not a chat metric
([TechCircle](https://www.techcircle.in/2026/08/03/how-meesho-is-rewiring-digital-commerce-with-ai-at-its-core) ✓).

---

## 6. A five-scene demo script set

One persona per market physics. Each scene names the utterance, the tools, the evidence and the guardrail
that must fire — enough to build against. Tools marked **new** do not exist in `demo/app/tools.py` today;
they are the minimum additions an India build needs.

### Scene 1 — Meera, 24, Nagpur · deadline + price + voice

> **Utterance (voice, Hinglish):** "Mere bhai ke liye ek camera chahiye, budget teen hazaar, Pune bhejna hai Friday tak."

* **Actions:** parse → **echo** ("camera gear under ₹3,000, deliver to Pune 411001 by Friday — right?") → confirm → retrieve → serviceability-filter → rank 2–3 with a *why* → optional add-to-cart.
* **Tools:** `search_products` · `get_reviews` · `get_product` · **new** `resolve_serviceability(product_ids, pincode, max_days)` · `add_to_cart`.
* **Evidence:** price, rating and review quotes per product (`product_id`/`review_id` citations); the serviceability row per SKU.
* **Guardrail:** **PincodeGate** — no SKU is presented as "Friday delivery" without a resolved serviceability row; the pincode is confirmed, not assumed.

### Scene 2 — Arun, 31, Lucknow · first-time buyer, COD, price ceiling

> **Utterance (typed, Hinglish):** "Mere liye ek naya phone chahiye, 9000 se kam, sasta aur jaldi."

* **Actions:** parse "naya" → condition (new), "se kam" → ₹9,000 ceiling, "sasta" → sort price_asc → retrieve → 3 options with total cost → prepaid incentive.
* **Tools:** `search_products` · `get_price_history` (is the "deal" real?) · **new** `get_total_cost(product_id, pincode, mode)` (price + delivery + COD fee) · **new** `get_rto_risk(pincode, category)`.
* **Evidence:** price and MRP per product; the 90-day-low comparison that justifies or refuses the "deal" claim.
* **Guardrail:** **MrpGuard** — no "% off" without a price-history row; **CodReturnGuard** — if the SKU is COD-eligible with high RTO, state the RTO band and the prepaid incentive *before* checkout.
### Scene 3 — Sneha, 27, Jaipur · return risk as the primary constraint

> **Utterance (voice, Hinglish):** "Mujhe ek kurte theek chahiye, shaadi ke liye, mujhe 32 size hai, wapas bhejne se galti na ho."

* **Actions:** parse category, occasion, size and the *return-averse* intent → filter by size availability and exchange friendliness → surface return-policy facts, not adjectives.
* **Tools:** `search_products` · `get_reviews` (size/fit evidence) · **new** `get_return_policy(product_id)` · **new** `get_size_availability(product_id, size)`.
* **Evidence:** size/fit review quotes with `review_id`; the return-policy row (window, exchange flag, COD vs prepaid return rate).
* **Guardrail:** **SizeEvidenceGuard** — no "will fit" claim without a size row or a cited review that mentions size; the agent may say "reviews report it runs large", never "this will fit".

### Scene 4 — Imran, 35, Hyderabad · authenticity anxiety

> **Utterance (typed):** "Is this headphone original hai ya duplicate? Amazon pe ₹1200 mein mil raha hai, original MRP 4999 hai."

* **Actions:** detect the authenticity question and the suspicious price gap → check the discount against price history → check seller/fulfilment signal → give a verdict *and its limits*.
* **Tools:** `get_product` · `get_price_history` · **new** `get_authenticity_signal(product_id)` (fulfilment type, seller, warranty route, review-authenticity score) · `get_reviews`.
* **Evidence:** the price-history row (is the "70% off" real?); the authenticity-signal row; review excerpts showing fake-review patterns (duplicated text, review burst).
* **Guardrail:** **AuthenticityGuard** — "original/genuine/100% authentic" only with a signal row, otherwise say what *cannot* be verified; **MrpGuard** fires if the %-off claim is unbacked.

### Scene 5 — Priya, 19, Kochi · conflicting constraints, voice-only

> **Utterance (voice, Hinglish):** "Mujhe ek phone chahiye jaldi chahiye, lekin 15000 se zyada nahi dena hai."

* **Actions:** parse the conflict (fast vs a hard ceiling) → determine what cannot be satisfied → **offer the trade-off instead of failing or silently exceeding the budget** → then confirm.
* **Tools:** `search_products` · **new** `resolve_serviceability(product_ids, pincode, max_days)` · `compare_products` (price vs ETA axis).
* **Evidence:** per option — price, resolved ETA at the pincode, and the review quote behind the recommendation.
* **Guardrail:** **BudgetGuard** (never exceed the stated ceiling without explicit consent) and **ConflictResolver** — the agent must present "₹15,799 by Tuesday" or "₹12,999 by Friday, which do you prefer?" rather than silently breaking one constraint.

---

**The two sentences to remember.** The market is won at the edges — the SKU, the spoken digit, the negation
scope, the pincode — and each edge produces a physical cost, not a bad answer. And the agent's product is its
guarantee: in a market with fake reviews, counterfeits, inflated MRP and 40% COD refusal, an assistant is worth
what it is *rarely wrong about the thing it promised to deliver*.