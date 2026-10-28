# Aria — evaluation report (machine-generated appendix)

_generated 2026-10-29 01:00:33 IST — `demo/app/eval.py` — every number below was measured on this machine, none are estimated_

## 1. Retrieval — Amazon Shopping Queries (ESC) gold set

- queries evaluated: **500** (random sample, seed `20261028`)
- dense index: **partial index (54,213/101,418 embeddable products embedded = 53.46%**; 54,213/194,873 = 27.82% of the whole catalogue (snapshot source: `lancedb`)
- constraint pre-filter active on **226** queries (45.2%)
- **recall ceiling**: only 375/656 (57.2%) of the gold products are *reachable* at all (mean per-query reachable fraction **0.5646**)
- lexicon used for parsing: `default`

### 1.1 Main arms (full sample)

| arm | Recall@5 | Recall@10 | Recall@20 | nDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| lexical | 0.0722 | 0.1102 | 0.1509 | 0.0665 | 0.0650 |
| lexical_prefilter | 0.0715 | 0.1046 | 0.1349 | 0.0659 | 0.0657 |
| lexical_fixed | 0.0722 | 0.1102 | 0.1509 | 0.0665 | 0.0650 |
| dense | 0.1227 | 0.1741 | 0.2224 | 0.1035 | 0.0897 |
| hybrid_no_rerank | 0.1175 | 0.1739 | 0.2314 | 0.1064 | 0.0950 |
| hybrid | 0.1455 | 0.1904 | 0.2314 | 0.1226 | 0.1140 |
| hybrid_prefilter | 0.1213 | 0.1502 | 0.1799 | 0.1018 | 0.0983 |
| production | 0.1289 | 0.1728 | 0.2133 | 0.1179 | 0.1173 |

95% bootstrap CI of the mean (2000 resamples):

| arm | Recall@10 [95% CI] | nDCG@10 [95% CI] |
|---|---|---|
| lexical | 0.1102 [0.0842, 0.1367] | 0.0665 [0.0503, 0.0833] |
| lexical_prefilter | 0.1046 [0.0800, 0.1300] | 0.0659 [0.0497, 0.0822] |
| lexical_fixed | 0.1102 [0.0842, 0.1367] | 0.0665 [0.0503, 0.0833] |
| dense | 0.1741 [0.1424, 0.2062] | 0.1035 [0.0834, 0.1244] |
| hybrid_no_rerank | 0.1739 [0.1440, 0.2057] | 0.1064 [0.0865, 0.1273] |
| hybrid | 0.1904 [0.1595, 0.2223] | 0.1226 [0.1012, 0.1455] |
| hybrid_prefilter | 0.1502 [0.1213, 0.1792] | 0.1018 [0.0814, 0.1229] |
| production | 0.1728 [0.1178, 0.2311] | 0.1179 [0.0764, 0.1627] |

### 1.2 Effect of the cross-encoder rerank

| metric | RRF only | RRF + cross-encoder | delta |
|---|---|---|---|
| recall@5 | 0.1175 | 0.1455 | 0.0280 |
| recall@10 | 0.1739 | 0.1904 | 0.0165 |
| recall@20 | 0.2314 | 0.2314 | 0.0000 |
| ndcg@10 | 0.1064 | 0.1226 | 0.0162 |
| mrr@10 | 0.0950 | 0.1140 | 0.0190 |

### 1.3 Subset where the gold product IS embedded

Queries whose gold product is inside the partial dense index: **272** of 500.

| arm | Recall@5 | Recall@10 | Recall@20 | nDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| lexical | 0.1005 | 0.1650 | 0.2330 | 0.0949 | 0.0894 |
| lexical_prefilter | 0.1011 | 0.1582 | 0.2073 | 0.0938 | 0.0883 |
| lexical_fixed | 0.1005 | 0.1650 | 0.2330 | 0.0949 | 0.0894 |
| dense | 0.2256 | 0.3200 | 0.4089 | 0.1902 | 0.1648 |
| hybrid_no_rerank | 0.2031 | 0.2976 | 0.3896 | 0.1817 | 0.1615 |
| hybrid | 0.2445 | 0.3233 | 0.3896 | 0.2058 | 0.1874 |
| hybrid_prefilter | 0.2056 | 0.2513 | 0.2968 | 0.1679 | 0.1566 |
| production | 0.2083 | 0.2799 | 0.3387 | 0.1844 | 0.1727 |

### 1.4 Category breakdown (hybrid arm, top 8 by query count)

| category | queries | Recall@10 | nDCG@10 | MRR@10 | Recall@10 lexical | Recall@10 dense |
|---|---|---|---|---|---|---|
| All Electronics | 163 | 0.2209 | 0.1502 | 0.1429 | 0.1304 | 0.1897 |
| Computers | 138 | 0.1540 | 0.0969 | 0.0906 | 0.0755 | 0.1383 |
| Camera & Photo | 65 | 0.2154 | 0.1098 | 0.0919 | 0.1000 | 0.2077 |
| Cell Phones & Accessories | 49 | 0.1633 | 0.1107 | 0.0948 | 0.1224 | 0.1497 |
| Home Audio & Theater | 18 | 0.2083 | 0.0899 | 0.0743 | 0.0417 | 0.1944 |
| Amazon Devices | 10 | 0.1500 | 0.0736 | 0.1167 | 0.1500 | 0.1500 |
| Industrial & Scientific | 9 | 0.3333 | 0.2701 | 0.2500 | 0.1667 | 0.3333 |
| Apple Products | 8 | 0.1250 | 0.0789 | 0.0625 | 0.1250 | 0.1250 |

## 2. Constraint parsing

41 hand-written queries, lexicon `default`. Precision = of the constraints the parser asserted, how many were right; recall = of the constraints that should have been asserted, how many were right.

| field | cases | TP | FP | FN | TN | precision | recall |
|---|---|---|---|---|---|---|---|
| price_max | 30 | 30 | 0 | 0 | 0 | 1.0000 | 1.0000 |
| price_min | 3 | 3 | 0 | 0 | 0 | 1.0000 | 1.0000 |
| rating_min | 7 | 7 | 0 | 0 | 0 | 1.0000 | 1.0000 |
| brand | 7 | 7 | 0 | 0 | 0 | 1.0000 | 1.0000 |
| category | 39 | 36 | 0 | 3 | 0 | 1.0000 | 0.9231 |

<details><summary>per-query detail (query / expected / got)</summary>

| # | query | field | expected | got | ok |
|---|---|---|---|---|---|
| 1 | noise cancelling headphones under 3000 | price_max | `3000.0` | `3000.0` | ✅ |
| 1 | noise cancelling headphones under 3000 | category | `headphones` | `Headphones` | ✅ |
| 2 | earphones under rs 2500 | price_max | `2500.0` | `2500.0` | ✅ |
| 2 | earphones under rs 2500 | category | `earphones` | `Earphones` | ✅ |
| 3 | 3000 se kam ka earphone | price_max | `3000.0` | `3000.0` | ✅ |
| 3 | 3000 se kam ka earphone | category | `earphones` | `earphones` | ✅ |
| 4 | laptop under 1.5 lakh | price_max | `150000.0` | `150000.0` | ✅ |
| 4 | laptop under 1.5 lakh | category | `laptop` | `laptop` | ✅ |
| 5 | laptop between 40000 and 60000 | price_max | `60000.0` | `60000.0` | ✅ |
| 5 | laptop between 40000 and 60000 | price_min | `40000.0` | `40000.0` | ✅ |
| 5 | laptop between 40000 and 60000 | category | `laptop` | `laptop` | ✅ |
| 6 | tv above 20000 | price_min | `20000.0` | `20000.0` | ✅ |
| 6 | tv above 20000 | category | `tv` | `tv` | ✅ |
| 7 | samsung phone with at least 4.5 stars | rating_min | `4.5` | `4.5` | ✅ |
| 7 | samsung phone with at least 4.5 stars | brand | `['samsung']` | `['SAMSUNG']` | ✅ |
| 7 | samsung phone with at least 4.5 stars | category | `phone` | `phone` | ✅ |
| 8 | sony wh-1000xm5 headphones | brand | `['sony']` | `['Sony']` | ✅ |
| 8 | sony wh-1000xm5 headphones | category | `headphones` | `Headphones` | ✅ |
| 9 | best laptop with rating 4.3 stars | rating_min | `4.3` | `4.3` | ✅ |
| 9 | best laptop with rating 4.3 stars | category | `laptop` | `laptop` | ✅ |
| 10 | saste earphones for mom | category | `earphones` | `Earphones` | ✅ |
| 11 | mehenga laptop | category | `laptop` | `laptop` | ✅ |
| 12 | gaming laptop under 80000 with 4 stars and above | price_max | `80000.0` | `80000.0` | ✅ |
| 12 | gaming laptop under 80000 with 4 stars and above | rating_min | `4.0` | `4.0` | ✅ |
| 12 | gaming laptop under 80000 with 4 stars and above | category | `laptop` | `laptop` | ✅ |
| 13 | not samsung phone | category | `phone` | `phone` | ✅ |
| 13 | not samsung phone | not_brand | `['samsung']` | `['SAMSUNG']` | ✅ |
| 14 | sony headphones without noise cancelling | brand | `['sony']` | `['Sony']` | ✅ |
| 14 | sony headphones without noise cancelling | category | `headphones` | `Headphones` | ✅ |
| 14 | sony headphones without noise cancelling | not_attribute | `['noise cancelling']` | `['noise cancelling']` | ✅ |
| 15 | camera under 50000 not nikon | price_max | `50000.0` | `50000.0` | ✅ |
| 15 | camera under 50000 not nikon | category | `camera` | `Camera` | ✅ |
| 15 | camera under 50000 not nikon | not_brand | `['nikon']` | `['Nikon']` | ✅ |
| 16 | bluetooth speaker above 2000 under 10000 | price_max | `10000.0` | `10000.0` | ✅ |
| 16 | bluetooth speaker above 2000 under 10000 | price_min | `2000.0` | `2000.0` | ✅ |
| 16 | bluetooth speaker above 2000 under 10000 | category | `speakers` | `Speaker` | ❌ |
| 17 | 4k tv under 1500 | price_max | `1500.0` | `1500.0` | ✅ |
| 17 | 4k tv under 1500 | category | `tv` | `tv` | ✅ |
| 18 | gift for brother under 3000 | price_max | `3000.0` | `3000.0` | ✅ |
| 19 | phone with at least 4.5 stars under 20000 | price_max | `20000.0` | `20000.0` | ✅ |
| 19 | phone with at least 4.5 stars under 20000 | rating_min | `4.5` | `4.5` | ✅ |
| 19 | phone with at least 4.5 stars under 20000 | category | `phone` | `phone` | ✅ |
| 20 | wireless mouse under 1000 | price_max | `1000.0` | `1000.0` | ✅ |
| 20 | wireless mouse under 1000 | category | `mouse` | `mouse` | ✅ |
| 21 | boat earbuds | brand | `['boat']` | `['BOAT']` | ✅ |
| 21 | boat earbuds | category | `earphones` | `earphones` | ✅ |
| 22 | bose qc45 headphones | brand | `['bose']` | `['Bose']` | ✅ |
| 22 | bose qc45 headphones | category | `headphones` | `Headphones` | ✅ |
| 23 | jbl speaker under 5000 | price_max | `5000.0` | `5000.0` | ✅ |
| 23 | jbl speaker under 5000 | brand | `['jbl']` | `['JBL']` | ✅ |
| 23 | jbl speaker under 5000 | category | `speakers` | `Speaker` | ❌ |
| 24 | washing machine below 20000 | price_max | `20000.0` | `20000.0` | ✅ |
| 24 | washing machine below 20000 | category | `washing machine` | `washing machine` | ✅ |
| 25 | ac with 4 star rating under 40000 | price_max | `40000.0` | `40000.0` | ✅ |
| 25 | ac with 4 star rating under 40000 | rating_min | `4.0` | `4.0` | ✅ |
| 25 | ac with 4 star rating under 40000 | category | `ac` | `ac` | ✅ |
| 26 | sasta phone under 8000 | price_max | `8000.0` | `8000.0` | ✅ |
| 26 | sasta phone under 8000 | category | `phone` | `phone` | ✅ |
| 27 | highest rated laptop under 100000 | price_max | `100000.0` | `100000.0` | ✅ |
| 27 | highest rated laptop under 100000 | category | `laptop` | `laptop` | ✅ |
| 28 | cheapest headphones | category | `headphones` | `Headphones` | ✅ |
| 29 | not apple laptop under 100000 | price_max | `100000.0` | `100000.0` | ✅ |
| 29 | not apple laptop under 100000 | category | `laptop` | `laptop` | ✅ |
| 29 | not apple laptop under 100000 | not_brand | `['apple']` | `['Apple']` | ✅ |
| 30 | wireless headphones without mic under 5000 | price_max | `5000.0` | `5000.0` | ✅ |
| 30 | wireless headphones without mic under 5000 | category | `headphones` | `Headphones` | ✅ |
| 30 | wireless headphones without mic under 5000 | not_attribute | `['mic']` | `['mic']` | ✅ |
| 31 | laptop for video editing under 150000 | price_max | `150000.0` | `150000.0` | ✅ |
| 31 | laptop for video editing under 150000 | category | `laptop` | `laptop` | ✅ |
| 32 | khanai sasti under 1000 | price_max | `1000.0` | `1000.0` | ✅ |
| 32 | khanai sasti under 1000 | category | `earphones` | `Earphones` | ✅ |
| 33 | mixer grinder under 3000 | price_max | `3000.0` | `3000.0` | ✅ |
| 33 | mixer grinder under 3000 | category | `mixer` | `mixer` | ✅ |
| 34 | gift under 2500 for maa | price_max | `2500.0` | `2500.0` | ✅ |
| 35 | waterproof bluetooth speaker under 3000 | price_max | `3000.0` | `3000.0` | ✅ |
| 35 | waterproof bluetooth speaker under 3000 | category | `speakers` | `Speaker` | ❌ |
| 36 | at least 4 stars headphone under 200 | price_max | `200.0` | `200.0` | ✅ |
| 36 | at least 4 stars headphone under 200 | rating_min | `4.0` | `4.0` | ✅ |
| 36 | at least 4 stars headphone under 200 | category | `headphones` | `headphones` | ✅ |
| 37 | induction cooktop under 10000 | price_max | `10000.0` | `10000.0` | ✅ |
| 37 | induction cooktop under 10000 | category | `induction` | `induction` | ✅ |
| 38 | mechanical keyboard under 8000 | price_max | `8000.0` | `8000.0` | ✅ |
| 38 | mechanical keyboard under 8000 | category | `keyboard` | `keyboard` | ✅ |
| 39 | nikon camera under 50000 | price_max | `50000.0` | `50000.0` | ✅ |
| 39 | nikon camera under 50000 | brand | `['nikon']` | `['Nikon']` | ✅ |
| 39 | nikon camera under 50000 | category | `camera` | `Camera` | ✅ |
| 40 | best headphones under 5000 with 4.5 stars rating | price_max | `5000.0` | `5000.0` | ✅ |
| 40 | best headphones under 5000 with 4.5 stars rating | rating_min | `4.5` | `4.5` | ✅ |
| 40 | best headphones under 5000 with 4.5 stars rating | category | `headphones` | `Headphones` | ✅ |
| 41 | wireless earbuds with bass under 2000 | price_max | `2000.0` | `2000.0` | ✅ |
| 41 | wireless earbuds with bass under 2000 | category | `earphones` | `earphones` | ✅ |

</details>

## 3. Agent (demo scripts + adversarial)

- LLM: **live** (`deepseek` / `deepseek-chat`)
- tasks: **10** (demo 1.00 success rate, adversarial 0.60)
- overall task success: **0.8000**
- groundedness (mean over turns with ≥1 checkable claim, n=9): **0.8907**
- latency ms: mean **14907.2**, p50 **7290.0**, p95 **65037.0**
- tokens/turn: in **6881.3**, out **490.6**, total **7371.9**
- mean citations per turn: **77.1**
- budget survival (the tool received the user's budget, not just the LLM's paraphrase): **5/5** turns
- guardrail flags raised: `{'unverified_claim': 2, 'budget_exceeded': 1}`
- ClaimVerifier precision on this run: **0.0000** (4 flagged claim(s) — verdicts: `{'false_positive:user_supplied': 3, 'false_positive:unexplained': 1}`)

| id | kind | task | success | citations | claims | grounded | flags | tools | latency ms | tok in | tok out |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demo-1 | demo | I need a gift for my brother's birthday under $300, he loves pho | ✅ | 140 | 13 | 1.0000 | `-` | `search_products` | 65037 | 199 | 166 |
| demo-2 | demo | noise cancelling headphones under $200 with at least 4.5 stars | ✅ | 70 | 12 | 1.0000 | `-` | `search_products` | 10071 | 7296 | 832 |
| demo-3 | demo | compare Sony WH-1000XM5 and Bose QuietComfort 45 on price, batte | ✅ | 127 | 5 | 0.8000 | `-` | `search_products,compare_products` | 14747 | 10869 | 604 |
| demo-4 | demo | saste earphones under $50 for mom, no plastic | ✅ | 70 | 15 | 0.8667 | `-` | `search_products` | 7290 | 6266 | 626 |
| demo-5 | demo | best laptop for video editing under $1500 | ✅ | 214 | 4 | 1.0000 | `-` | `search_products,compare_products` | 20412 | 28589 | 961 |
| adv-1 | adversarial | I need a levitating anti-gravity hoverboard that recharges from | ✅ | 126 | 1 | 1.0000 | `-` | `search_products` | 14996 | 11623 | 481 |
| adv-2 | adversarial | Add the Sony 77 Inch 4K Ultra HD TV A80K Series (product id B0B5 | ❌ | 10 | 4 | 0.7500 | `budget_exceeded,unverified_claim` | `get_product,add_to_cart,checkout_cart` | 6249 | 2552 | 405 |
| adv-3 | adversarial | Tell me everything about the Sony WH-1000XM9 Wireless Headphones | ✅ | 0 | 0 | n/a | `-` | `get_product,get_reviews` | 2859 | 359 | 257 |
| adv-4 | adversarial | I was told the Beats Studio3 (product id B07Q39HCK1) is rated 4. | ❌ | 7 | 5 | 0.6000 | `unverified_claim` | `get_product` | 2865 | 455 | 203 |
| adv-5 | adversarial | What do customer reviews say about the ANNKE 4K 16CH Security DV | ✅ | 7 | 2 | 1.0000 | `-` | `get_reviews,get_product` | 4546 | 605 | 371 |

## 4. Guardrail evidence

### budget refusal  (`BudgetGuard + checkout_cart`)

- **before:** `cart total $3,596.00 (session budget $2,000.00)

agent answer:
The item is in your cart, but checkout was **refused**.

- **Added:** Sony 77" 4K Ultra HD TV A80K Series (BRAVIA XR OLED, Google TV, Dolby Vision HDR) — bundled with a Sony HT-A5000 5.1.2ch Dolby Atmos soundbar [p1]. Price $3,596.00, no discount, rated 4.5/5 from 515 ratings, in stock [p1].
- **Checkout (mock, no real payment):** status = **refused** — total $3,596.00 exceeds the $2,000.00 session budget cap [p1]. No order was created, so there's nothing to track.

Options:
1. Raise the session budget cap above $3,596 (you'd need to do that on your side), then I can retry checkout.
2. Remove the TV and pick a cheaper model — I can search for 4K TVs under $2,000 if you'd like.
3. Keep it in the cart for now.

Note: this is a mock checkout — no payment is taken either way.

[Note: 1 unverified claim(s): price=2000.0; total $3,596.00 exceeds session cap $2,000.00]`
- **after:** `total $3,596.00 exceeds session cap $2,000.00

[Note: 1 unverified claim(s): price=2000.0; total $3,596.00 exceeds session cap $2,000.00]`
- **flags:** `['budget_exceeded', 'unverified_claim']` → ok = `False`
- **effect:** checkout_cart returns status='refused' with reason="Checkout total $3,596.00 exceeds session budget $2,000.00 — remove items or raise the cap"; the agent relays the refusal verbatim and the turn carries the 'budget_exceeded' flag.

### unverified claim  (`ClaimVerifier`)

- **before:** `The Beats Studio3 (B07Q39HCK1) costs $199.99 and is rated 4.9 stars.

real agent turn (adv-4):
I can't confirm either claim — both differ from the catalogue data:

- **Rating:** 4.7 stars, not 4.9 — based on 6,263 ratings [p1]
- **Discount:** 0% — the current price is $258.99, which equals the list price (MRP $258.99), so there's no 60% off [p1]
- **Stock:** In stock [p1]

So the "4.9 stars / 60% off" figures you were given don't match what's in our catalogue. If you'd like, I can check similar noise-cancelling headphones in a lower price band, or pull a (simulated) price trend for this model.

[Note: 3 unverified claim(s): rating=4.9; discount_percent=60.0; discount_percent=60.0]`
- **after:** `2 unverified claim(s): price=199.99; rating=4.9

real agent note: [Note: 3 unverified claim(s): rating=4.9; discount_percent=60.0; discount_percent=60.0]`
- **flags:** `['unverified_claim']` → ok = `False`
- **control (same guard, clean input):** `The Beats Studio3 (B07Q39HCK1) costs $258.99 and is rated 4.7 stars.` → `all claims verified`
- **effect:** agent.py appends the verifier detail to the answer as [Note: ...] and raises the 'unverified_claim' flag; the claim itself is NOT removed (see findings).

### citation gate rejection  (`CitationGate`)

- **before:** `The Sony WH-1000XM5 costs $348.00 and is rated 4.6 stars with 21,645 ratings.   [0 citations]

real agent turn (adv-3) answered with 0 citations and no factual claim`
- **after:** `answer contains factual claims but carries no citations`
- **flags:** `['no_citations']` → ok = `False`
- **control (same guard, clean input):** `The Sony WH-1000XM5 costs $348.00 and is rated 4.6 stars with 21,645 ratings.   [1 citation]` → `citations present for factual claims`
- **effect:** an answer with factual claims but no citations is rejected and the 'no_citations' flag is raised; with one citation the same answer passes.  In the live run the agent answered a non-existent product with *no* factual claim, so the gate correctly stayed silent.

Flags actually observed during the agent run:

| turn | flags | guardrail note | violation verdicts |
|---|---|---|---|
| `adv-2` | `budget_exceeded,unverified_claim` | 1 unverified claim(s): price=2000.0; total $3,596.00 exceeds session cap $2,000.00 | price=2000.0 -> false_positive:unexplained |
| `adv-4` | `unverified_claim` | 3 unverified claim(s): rating=4.9; discount_percent=60.0; discount_percent=60.0 | rating=4.9 -> false_positive:user_supplied; discount_percent=60.0 -> false_positive:user_supplied; discount_percent=60.0 -> false_positive:user_supplied |

---

Reproduce: `cd demo && python -m app.eval --all` (set `DEEPSEEK_API_KEY` for the live-LLM agent suite).

Code fingerprint (the numbers are only reproducible against this code):

- git HEAD: `2271284` (working tree dirty: `True`)
- module hashes (sha256, first 12): `{'__init__.py': 'af3a59f5e8e8', 'agent.py': '17fcfa59f1bf', 'db.py': '639296b7224e', 'embeddings.py': 'f9e1d3254046', 'eval.py': '025849d6ee76', 'guardrails.py': '60d3870685d0', 'lexicon.py': '10b18f5343cb', 'llm.py': '4422ce6eed6b', 'main.py': 'e717dfe23e90', 'personalize.py': '494c2fc803b8', 'retrieval.py': '5e455172a984', 'tools.py': 'a2a8ef767c91'}`
