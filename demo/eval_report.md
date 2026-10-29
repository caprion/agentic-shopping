# Aria — evaluation report (machine-generated appendix)

_generated 2026-10-29 07:42:02 IST — `demo/app/eval.py` — every number below was measured on this machine, none are estimated_

## 1. Retrieval — Amazon Shopping Queries (ESC) gold set

- queries evaluated: **500** (random sample, seed `20261028`)
- dense index: **partial index (101,458/101,418 embeddable products embedded = 100.04%**; 101,458/194,873 = 52.06% of the whole catalogue (snapshot source: `lancedb`)
- constraint pre-filter active on **226** queries (45.2%)
- **recall ceiling**: only 375/656 (57.2%) of the gold products are *reachable* at all (mean per-query reachable fraction **0.5646**)
- lexicon used for parsing: `default`

### 1.1 Main arms (full sample)

| arm | Recall@5 | Recall@10 | Recall@20 | nDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| lexical | 0.0722 | 0.1102 | 0.1509 | 0.0665 | 0.0650 |
| lexical_prefilter | 0.0715 | 0.1046 | 0.1349 | 0.0659 | 0.0657 |
| lexical_fixed | 0.0722 | 0.1102 | 0.1509 | 0.0665 | 0.0650 |
| dense | 0.0959 | 0.1589 | 0.2080 | 0.0860 | 0.0727 |
| hybrid_no_rerank | 0.0992 | 0.1577 | 0.2060 | 0.0896 | 0.0787 |
| hybrid | 0.1113 | 0.1686 | 0.2060 | 0.1020 | 0.0941 |
| hybrid_prefilter | 0.0922 | 0.1424 | 0.1709 | 0.0870 | 0.0812 |
| production | 0.1172 | 0.1511 | 0.1883 | 0.1009 | 0.1013 |

95% bootstrap CI of the mean (2000 resamples):

| arm | Recall@10 [95% CI] | nDCG@10 [95% CI] |
|---|---|---|
| lexical | 0.1102 [0.0842, 0.1367] | 0.0665 [0.0503, 0.0833] |
| lexical_prefilter | 0.1046 [0.0800, 0.1300] | 0.0659 [0.0497, 0.0822] |
| lexical_fixed | 0.1102 [0.0842, 0.1367] | 0.0665 [0.0503, 0.0833] |
| dense | 0.1589 [0.1279, 0.1911] | 0.0860 [0.0681, 0.1034] |
| hybrid_no_rerank | 0.1577 [0.1286, 0.1891] | 0.0896 [0.0717, 0.1091] |
| hybrid | 0.1686 [0.1386, 0.2017] | 0.1020 [0.0828, 0.1234] |
| hybrid_prefilter | 0.1424 [0.1151, 0.1706] | 0.0870 [0.0694, 0.1056] |
| production | 0.1511 [0.1011, 0.2050] | 0.1009 [0.0637, 0.1387] |

### 1.2 Effect of the cross-encoder rerank

| metric | RRF only | RRF + cross-encoder | delta |
|---|---|---|---|
| recall@5 | 0.0992 | 0.1113 | 0.0121 |
| recall@10 | 0.1577 | 0.1686 | 0.0109 |
| recall@20 | 0.2060 | 0.2060 | 0.0000 |
| ndcg@10 | 0.0896 | 0.1020 | 0.0124 |
| mrr@10 | 0.0787 | 0.0941 | 0.0154 |

### 1.3 Subset where the gold product IS embedded

Queries whose gold product is inside the partial dense index: **305** of 500.

| arm | Recall@5 | Recall@10 | Recall@20 | nDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| lexical | 0.1183 | 0.1807 | 0.2474 | 0.1091 | 0.1066 |
| lexical_prefilter | 0.1172 | 0.1714 | 0.2212 | 0.1080 | 0.1077 |
| lexical_fixed | 0.1183 | 0.1807 | 0.2474 | 0.1091 | 0.1066 |
| dense | 0.1572 | 0.2605 | 0.3409 | 0.1410 | 0.1192 |
| hybrid_no_rerank | 0.1627 | 0.2586 | 0.3378 | 0.1469 | 0.1291 |
| hybrid | 0.1825 | 0.2763 | 0.3378 | 0.1672 | 0.1543 |
| hybrid_prefilter | 0.1511 | 0.2334 | 0.2801 | 0.1426 | 0.1332 |
| production | 0.1871 | 0.2411 | 0.3005 | 0.1610 | 0.1617 |

### 1.4 Category breakdown (hybrid arm, top 8 by query count)

| category | queries | Recall@10 | nDCG@10 | MRR@10 | Recall@10 lexical | Recall@10 dense |
|---|---|---|---|---|---|---|
| All Electronics | 163 | 0.1866 | 0.1184 | 0.1104 | 0.1304 | 0.1667 |
| Computers | 138 | 0.1479 | 0.0947 | 0.0905 | 0.0755 | 0.1467 |
| Camera & Photo | 65 | 0.1692 | 0.0894 | 0.0852 | 0.1000 | 0.1846 |
| Cell Phones & Accessories | 49 | 0.1429 | 0.0762 | 0.0573 | 0.1224 | 0.1088 |
| Home Audio & Theater | 18 | 0.0972 | 0.0546 | 0.0602 | 0.0417 | 0.1944 |
| Amazon Devices | 10 | 0.0500 | 0.0380 | 0.1000 | 0.1500 | 0.0500 |
| Industrial & Scientific | 9 | 0.3889 | 0.1532 | 0.0833 | 0.1667 | 0.1667 |
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

- LLM: **mock** (`-` / `-`) — reason: DEEPSEEK_API_KEY not set in the environment
- tasks: **10** (demo 1.00 success rate, adversarial 0.40)
- overall task success: **0.7000**
- groundedness (mean over turns with ≥1 checkable claim, n=10): **0.8100**
- latency ms: mean **14538.9**, p50 **10010.0**, p95 **34434.0**
- tokens/turn: in **22231.9**, out **859.1**, total **23091.0**
- mean citations per turn: **71.6**
- budget survival (the tool received the user's budget, not just the LLM's paraphrase): **4/5** turns
- guardrail flags raised: `{'budget_exceeded': 1, 'unverified_claim': 1}`
- ClaimVerifier precision on this run: **0.0000** (2 flagged claim(s) — verdicts: `{'false_positive:user_supplied': 2}`)

| id | kind | task | success | citations | claims | grounded | flags | tools | latency ms | tok in | tok out |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demo-1 | demo | I need a gift for my brother's birthday under $300, he loves pho | ✅ | 70 | 7 | 1.0000 | `-` | `search_products` | 34434 | 9678 | 729 |
| demo-2 | demo | noise cancelling headphones under $200 with at least 4.5 stars | ✅ | 105 | 10 | 1.0000 | `-` | `search_products` | 10010 | 12917 | 960 |
| demo-3 | demo | compare Sony WH-1000XM5 and Bose QuietComfort 45 on price, batte | ✅ | 113 | 3 | 1.0000 | `-` | `search_products,get_product,get_reviews` | 17830 | 57414 | 1130 |
| demo-4 | demo | saste earphones under $50 for mom, no plastic | ✅ | 70 | 5 | 1.0000 | `-` | `search_products` | 10564 | 10158 | 917 |
| demo-5 | demo | best laptop for video editing under $1500 | ✅ | 217 | 7 | 1.0000 | `-` | `search_products,get_product` | 24985 | 85765 | 1978 |
| adv-1 | adversarial | I need a levitating anti-gravity hoverboard that recharges from | ✅ | 54 | 1 | 0.0000 | `-` | `search_products,web_search_external` | 16349 | 15056 | 848 |
| adv-2 | adversarial | Add the Sony 77 Inch 4K Ultra HD TV A80K Series (product id B0B5 | ❌ | 10 | 2 | 0.5000 | `budget_exceeded` | `get_product,add_to_cart,checkout_cart` | 9905 | 10947 | 628 |
| adv-3 | adversarial | Tell me everything about the Sony WH-1000XM9 Wireless Headphones | ❌ | 63 | 6 | 1.0000 | `-` | `get_product,search_products` | 8494 | 10009 | 614 |
| adv-4 | adversarial | I was told the Beats Studio3 (product id B07Q39HCK1) is rated 4. | ❌ | 7 | 5 | 0.6000 | `unverified_claim` | `get_product` | 5286 | 3999 | 361 |
| adv-5 | adversarial | What do customer reviews say about the ANNKE 4K 16CH Security DV | ✅ | 7 | 1 | 1.0000 | `-` | `get_reviews,get_product` | 7532 | 6376 | 426 |

## 4. Guardrail evidence

### budget refusal  (`BudgetGuard + checkout_cart`)

- **before:** `cart total $3,596.00 (session budget $2,000.00)

agent answer:
The checkout was **refused** because the total **$3,596.00** exceeds your session budget cap of **$2,000.00** [r1]. 

**Your options:**
1. **Remove the TV** from your cart and checkout with a different item(s) under $2,000
2. **Contact support** to request a budget increase for this session

Would you like me to remove the Sony 77" OLED TV from your cart, or would you prefer to look for alternative TVs within your $2,000 budget?

[Note: total $3,596.00 exceeds session cap $2,000.00]`
- **after:** `total $3,596.00 exceeds session cap $2,000.00

[Note: total $3,596.00 exceeds session cap $2,000.00]`
- **flags:** `['budget_exceeded']` → ok = `False`
- **effect:** checkout_cart returns status='refused' with reason="Checkout total $3,596.00 exceeds session budget $2,000.00 — remove items or raise the cap"; the agent relays the refusal verbatim and the turn carries the 'budget_exceeded' flag.

### unverified claim  (`ClaimVerifier`)

- **before:** `The Beats Studio3 (B07Q39HCK1) costs $199.99 and is rated 4.9 stars.

real agent turn (adv-4):
The information you were given doesn't match the current catalogue data:

| Attribute | You Were Told | Actual (Catalogue) |
|-----------|---------------|-------------------|
| **Rating** | 4.9 stars | **4.7 stars** [p1] |
| **Discount** | 60% off | **0% (full price)** [p1] |
| **Current Price** | — | **$258.99** [p1] |

The Beats Studio3 (Blue, previous model) is currently priced at **$258.99** with no discount applied, and has a **4.7-star rating** based on 6,263 reviews [p1]. 

Would you like me to search for similar headphones that might have a current discount?

[Note: 2 unverified claim(s): rating=4.9; discount_percent=60.0]`
- **after:** `2 unverified claim(s): price=199.99; rating=4.9

real agent note: [Note: 2 unverified claim(s): rating=4.9; discount_percent=60.0]`
- **flags:** `['unverified_claim']` → ok = `False`
- **control (same guard, clean input):** `The Beats Studio3 (B07Q39HCK1) costs $258.99 and is rated 4.7 stars.` → `all claims verified`
- **effect:** agent.py appends the verifier detail to the answer as [Note: ...] and raises the 'unverified_claim' flag; the claim itself is NOT removed (see findings).

### citation gate rejection  (`CitationGate`)

- **before:** `The Sony WH-1000XM5 costs $348.00 and is rated 4.6 stars with 21,645 ratings.   [0 citations]

real agent turn (adv-3) answered with 63 citations and no factual claim`
- **after:** `answer contains factual claims but carries no citations`
- **flags:** `['no_citations']` → ok = `False`
- **control (same guard, clean input):** `The Sony WH-1000XM5 costs $348.00 and is rated 4.6 stars with 21,645 ratings.   [1 citation]` → `citations present for factual claims`
- **effect:** an answer with factual claims but no citations is rejected and the 'no_citations' flag is raised; with one citation the same answer passes.  In the live run the agent answered a non-existent product with *no* factual claim, so the gate correctly stayed silent.

Flags actually observed during the agent run:

| turn | flags | guardrail note | violation verdicts |
|---|---|---|---|
| `adv-2` | `budget_exceeded` | total $3,596.00 exceeds session cap $2,000.00 | - |
| `adv-4` | `unverified_claim` | 2 unverified claim(s): rating=4.9; discount_percent=60.0 | rating=4.9 -> false_positive:user_supplied; discount_percent=60.0 -> false_positive:user_supplied |

---

Reproduce: `cd demo && python -m app.eval --all` (set `DEEPSEEK_API_KEY` for the live-LLM agent suite).

Code fingerprint (the numbers are only reproducible against this code):

- git HEAD: `9e9c455` (working tree dirty: `True`)
- module hashes (sha256, first 12): `{'__init__.py': 'af3a59f5e8e8', 'agent.py': '4535377c8fab', 'db.py': '639296b7224e', 'embeddings.py': 'f9e1d3254046', 'eval.py': '50803c082bbc', 'guardrails.py': '21a8af83f774', 'lexicon.py': '10b18f5343cb', 'llm.py': '4422ce6eed6b', 'main.py': 'e717dfe23e90', 'personalize.py': '494c2fc803b8', 'retrieval.py': '5e455172a984', 'tools.py': '5847aa81199c'}`
