# 03 — Open Dataset Catalogue for an Agentic Shopping Prototype

**Author:** data engineer · **Access date:** 2026-10-28 · **Scope:** catalogue + customers + transactions + reviews + images + taxonomy + search-intent + agent benchmarks.

Every URL below was either **fetched into `/home/sumit/agentic-shopping/data/raw/`** or probed with `curl -sI` / the HuggingFace datasets-server API / GitHub API on the access date. Status is marked **VERIFIED** (bytes on disk or a live 200 with known `Content-Length`) or **UNVERIFIED** (docs only / moved / gated).

---

## 0. What is already on disk (downloads done this session)

| File (in `data/raw/`) | Rows | Size | Content |
|---|---|---|---|
| `olist/*.csv` (9 files) | 99,442 orders · 112,651 items · 99,442 customers · 104,720 reviews · 32,952 products · 3,096 sellers · 1,000,163 geolocation · 103,887 payments | ~121 MB | **Real Brazilian e-commerce transactions** (orders→items→customers→reviews→payments, fully joined) |
| `Musical_Instruments_5core_rating_only.csv` | 511,836 | 29 MB | `user_id,parent_asin,rating,timestamp` |
| `Industrial_and_Scientific_5core_rating_only.csv` | 412,947 | 24 MB | same schema |
| `ratings_electronics.csv` | 3,029,868 | 168 MB | same schema (Electronics 5-core) |
| `reviews_electronics_part.jsonl` | 365,362 | 207 MB | full-text reviews: `asin, helpful_vote, images, parent_asin, rating, text, timestamp, title, user_id, verified_purchase` |
| `meta_Musical_Instruments_subset.jsonl` | 24,587 products | 90 MB | full product metadata (below) |
| `meta_Industrial_and_Scientific_subset.jsonl` | 25,847 products | 94 MB | full product metadata |
| `meta_electronics_0.parquet` | 161,002 products | 210 MB | full product metadata |
| `esci_examples.parquet` | 2,621,288 | 49 MB | **Search-intent labels** `query, query_id, product_id, product_locale, esci_label, split` |
| `google_product_taxonomy.en-US.txt` | 5,596 categories | 436 KB | Google Product Taxonomy 2021-09-21 |
| `tau_bench_retail/*.json` | 50 products + users + orders + tasks | 2.4 MB | τ-bench retail agent benchmark |

**Total on disk ≈ 990 MB.** Product metadata schema (verified from JSONL): `main_category, title, average_rating, rating_number, features[], description[], price, images[] {thumb, large, hi_res, variant}, videos[], store, categories[] (3-level path), details, parent_asin, bought_together, subtitle, author`. Amazon image URLs (`m.media-amazon.com`) return `HTTP 200 image/jpeg` **without auth** — verified.

---

## 1. Amazon family

### 1.1 Amazon Reviews 2023 (McAuley-Lab) — **PRIMARY SOURCE**
- **URL:** https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023
- **Status:** **VERIFIED** (tree + files downloaded). The dataset-viewer is disabled ("runs arbitrary Python code"), so files are pulled directly via `…/resolve/main/…`.
- **Scale:** ~571M reviews, ~245M users, ~48M items across 33 categories (Electronics, Clothing_Shoes_and_Jewelry, Home_and_Kitchen, Beauty_and_Personal_Care, Grocery_and_Gourmet_Food, Toys_and_Games, Office_Products, Appliances, Musical_Instruments, Video_Games, Industrial_and_Scientific, …).
- **Layout (verified via HF tree API):**
  - `raw/meta_categories/meta_{Cat}.jsonl` — product metadata (full schema above). e.g. `meta_Musical_Instruments.jsonl` = 631 MB; `meta_Electronics.jsonl` = 5.2 GB.
  - `raw/review_categories/{Cat}.jsonl` — raw reviews (text + images + helpful_vote). `Electronics.jsonl` = 22.6 GB.
  - `benchmark/5core/rating_only/{Cat}.csv` — deduped 5-core ratings (`user_id,parent_asin,rating,timestamp`), no review text. Sizes verified: Musical_Instruments 29.7 MB, Industrial_and_Scientific 24 MB, Office_Products 104 MB, Electronics 897 MB, Video_Games 47 MB, Grocery_and_Gourmet_Food 229 MB.
  - `benchmark/5core/timestamp/{Cat}.{train,valid,test}.csv` — temporal splits for sequential-recommendation eval.
  - `benchmark/5core/last_out`, `last_out_w_his`, `timestamp_w_his` — leave-last-out / with-history variants.
- **License:** research-only (no formal CC; see HF card + paper). Fine for a prototype/demo, not for redistribution.
- **Cheap subset:** 5-core `rating_only` CSVs of small categories (~25–50 MB each), plus streamed `meta_*` lines filtered to the ASINs you keep (exact method used below in §7).

### 1.2 Amazon Review Data (2014) — legacy
- **URL:** was `https://cseweb.ucsd.edu/~jmcauley/datasets/amazon_v2/categoryFiles/{Cat}_5.json.gz`
- **Status:** **UNVERIFIED / MOVED** — returns `404` today (UCSD host retired; same for `datarepo.eng.ucsd.edu` paths probed). Superseded by Amazon Reviews 2023. Academic mirrors exist (UCSD Library Dataverse, Kaggle mirrors) but are unofficial. **Recommendation: use the 2023 dataset instead.**

### 1.3 Amazon QA (2019, McAuley)
- **URL:** was `https://cseweb.ucsd.edu/~jmcauley/datasets/amazon/qa/qa_{Cat}.json.gz` → **404 now (moved).**
- **Status:** **UNVERIFIED.** Mirrors exist on HF (`embedding-data/Amazon-QA`, `amazon/AmazonQAC`). ~1.4M Q&A pairs. Optional for the prototype (reviews suffice).

### 1.4 Amazon Product Graph / ARPA & Selling Partner API samples
- **Product Graph / ARPA** (Amazon product knowledge graph): referenced in Amazon Science papers; **no public bulk dump** — **UNVERIFIED / not downloadable**. Useful only as an architecture reference (attribute normalisation, ASIN→canonical entity).
- **Selling Partner API sample product ads:** the SP-API catalogue/data feeds are **live APIs requiring seller credentials**, not datasets. **UNVERIFIED for bulk use.** The only "sample ads" material is documentation snippets.

---

## 2. Shopping queries / search intent

### 2.1 Amazon Shopping Queries Dataset (ESC/ESCID) — amazon-science/esci-data
- **URL:** https://github.com/amazon-science/esci-data (files: `shopping_queries_dataset_examples.parquet`, `shopping_queries_dataset_products.parquet`, `shopping_queries_dataset_sources.csv`)
- **Status:** **VERIFIED** — `examples.parquet` fetched (2,621,288 rows, 51 MB, on disk). Schema: `example_id, query, query_id, product_id, product_locale, esci_label (E=exact, S=substitute, C=complement, I=irrelevant), small_version, large_version, split`. `products.parquet` (1.11 GB, `product_id, product_locale, product_title, product_description, product_bullet_point, product_brand, product_color`) probed live at the LFS media URL.
- **License:** Apache-2.0 (LICENSE in repo). **Best free query→product relevance dataset.**

### 2.2 Others
| Dataset | URL | Status | Notes |
|---|---|---|---|
| Multi-choice Shopping Queries (MCSQ) | https://github.com/… (Criteo/amazon-science) | UNVERIFIED | query→product multiple-choice; older, smaller |
| Amazon Shopper Intention | — | UNVERIFIED | no stable public dump found |
| Kaggle "eCommerce search logs" / "Walmart search logs" | kaggle.com | UNVERIFIED | scattered, license-inconsistent, Kaggle auth |
| Instacart Personalized Recipe Recommendations | kaggle.com/shuyangli94/food-com-recipes-and-user-interactions | UNVERIFIED (Kaggle auth) | recipes + interactions |
| Ali-Express Behavioural Log / AliCCP | https://tianchi.aliyun.com / github.com/alicogintel | UNVERIFIED | ~50M users, CTR prediction (ad ranking, not retail search) |
| Search Results Relevance (Judged, CrowdFlower) | kaggle.com/crowdflower/search-relevance | UNVERIFIED | small human-judged query↔product pairs |

---

## 3. Real transactions + customers

### 3.1 Olist Brazilian E-Commerce — **BEST single real-transaction dataset**
- **Canonical:** https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce (Kaggle **auth required**).
- **Verified mirror (no auth):** https://huggingface.co/datasets/MafiaAzulBr/olistcsv — files at `…/resolve/main/archive/{name}.csv`. **VERIFIED — all 9 CSVs downloaded (121 MB).**
- **Schema (joined star):**
  - `olist_orders` — `order_id, customer_id, order_status, order_purchase/approved/delivered/estimated_delivery timestamps`
  - `olist_order_items` — `order_id, order_item_id, product_id, seller_id, price, freight_value`
  - `olist_customers` — `customer_id, customer_unique_id, customer_zip_code_prefix, customer_city, customer_state`
  - `olist_products` — `product_id, product_category_name, product_name_length, product_description_length, product_photos_qty, product_weight/height/width/length`
  - `olist_order_reviews` — `review_id, order_id, review_score (1-5), review_comment_title, review_comment_message, timestamps`
  - `olist_order_payments` — `order_id, payment_sequential, payment_type, payment_installments, payment_value`
  - `olist_sellers`, `olist_geolocation`, `product_category_name_translation` (pt→en).
- **Facts:** 99,441 orders (2016-10 → 2018-08), 112,650 order items, 99,441 customers, 100k+ reviews, 32,951 products, 73 top-level categories, multi-seller marketplace. **Real customers with sparse repeat purchase history, real reviews with text.**
- **License:** CC BY-NC-SA 4.0 (non-commercial) — fine for a demo.

### 3.2 UCI Online Retail (UK)
- **URL:** https://archive.ics.uci.edu/ml/machine-learning-databases/00352/Online%20Retail.xlsx
- **Status:** **VERIFIED (200 OK).** ~541k rows, 2010-12→2011-12, gift-store transactions. Schema: `InvoiceNo, StockCode, Description, Quantity, InvoiceDate, UnitPrice, CustomerID, Country`. **License:** CC BY 4.0.

### 3.3 Others
| Dataset | URL | Status | Notes |
|---|---|---|---|
| H&M Personalized Fashion Recommendations | kaggle.com/competitions/h-and-m-personalized-fashion-recommendations | UNVERIFIED (Kaggle auth + rules acceptance) | ~31M transactions, 105k customers, 105k articles |
| Instacart Market Basket (2017) | kaggle.com/datasets/psparks/instacart-market-basket-analysis | UNVERIFIED (Kaggle auth) | 3.4M orders, 206k users, 49k products, aisles/departments |
| Rossmann Store Sales | kaggle.com/competitions/rossmann-store-sales | UNVERIFIED (Kaggle auth) | 1M rows, store/sales/promo |
| Taobao / Tmall UserBehavior (Aliyun Tianchi) | tianchi.aliyun.com/dataset/649 | UNVERIFIED | ~1M users, ~100M (user,item,behavior,timestamp) |
| KuaiRec | https://chongminggao.github.io/KuaiRec/ | UNVERIFIED | short-video rec (not shopping) |
| MobyMart | — | **Not found** (no public dataset by this name) | drop |
| TracIn | — | **Not a dataset** (Google model-attribution method) | drop |
| ReChorus2 | https://github.com/THUwangcy/ReChorus | UNVERIFIED | recommendation *library* bundling small Amazon/Grocery subsets |

---

## 4. Product catalogue + taxonomy

| Dataset | URL | Status | License | Notes |
|---|---|---|---|---|
| **Google Product Taxonomy** | https://www.google.com/basepages/producttype/taxonomy.en-US.txt | **VERIFIED (5,596 categories, 442 KB on disk)** | Google public resource | `Animals & Pet Supplies > Pet Supplies > Bird Supplies` — direct plug-in taxonomy |
| **Amazon Product Metadata** (`raw/meta_categories/meta_*.jsonl`) | in Amazon Reviews 2023 (§1.1) | VERIFIED | research-only | 48M products, full attributes + image URLs — this *is* your catalogue |
| Open Food Facts | https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz | **VERIFIED (1.5 GB gz)** | **ODbL** | 3M+ food products, nutrition facts, images; JSONL export 13.3 GB |
| Open Beauty Facts | https://static.openfoodfacts.org/data/en.openbeautyfacts.org.products.csv.gz | UNVERIFIED (same infra) | ODbL | cosmetics |
| GS1 GTIN / GPC | gs1.org | UNVERIFIED | membership | GTIN registry is not an open bulk dump |
| Schema.org Product ontology | https://schema.org/Product | VERIFIED (docs) | CC-BY 4.0 (text) | vocabulary, not data |
| Walmart Product Taxonomy | — | UNVERIFIED | — | no public dump; internal |
| Google Shopping Products (GSOD) | — | UNVERIFIED | — | only messy scrapes on data.world/Kaggle |

---

## 5. Images / multimodal

| Dataset | URL | Status | License | Notes |
|---|---|---|---|---|
| **Amazon Reviews 2023 meta images** | `images[]` field in §1.1 | **VERIFIED (URLs return 200 image/jpeg)** | research-only | thumb/large/hi_res per product — zero extra download, hotlink from Amazon CDN |
| Fashion Product Images (Small) | kaggle.com/datasets/paramaggarwal/fashion-product-images-small | UNVERIFIED (Kaggle auth) | CC0 | 44k Myntra products, `styles.csv` + 44k images (60×80) |
| Myntra Fashion Dataset | kaggle.com/datasets/ronakbokaria/myntra-fashion-dataset | UNVERIFIED | — | apparel |
| DeepFashion | https://github.com/yumingj/DeepFashion | UNVERIFIED | research-only | 800k fashion images, attributes |
| Products-10K | https://products-10k.github.io/ (challenge.html) + Kaggle `c/products-10k` | UNVERIFIED (Google Drive / Kaggle) | research-only | 10k JD.com SKUs, ~150k images, hierarchical graph |
| Unsplash-Lite (25k) | unsplash.com/data | UNVERIFIED (form) | Unsplash license | generic photos, not product-focused |
| Google Open Images V7 | storage.googleapis.com/openimages | UNVERIFIED (huge) | CC-BY 2.0 | 9M images, weak product coverage |
| LAION subsets | laion.ai | UNVERIFIED | none (web-scraped) | noisy, avoid for a demo |

**Recommendation:** Amazon meta `images[]` URLs (already verified) are the cheapest multimodal path — no image download needed, just store/join URLs.

---

## 6. Reviews sentiment / long-form

| Dataset | URL | Status | Notes |
|---|---|---|---|
| **Amazon Reviews 2023 raw reviews** (`raw/review_categories/{Cat}.jsonl`) | §1.1 | **VERIFIED** (Electronics text subset on disk: 365k reviews) | `rating, title, text, helpful_vote, verified_purchase, images` |
| Magazine Subscriptions (review category) | §1.1 `Magazine_Subscriptions.jsonl` (33 MB) | VERIFIED (size) | tiny long-form niche |
| Yelp Open Dataset | https://www.yelp.com/dataset → business.yelp.com/data/resources/open-dataset | UNVERIFIED (form/agreement) | 7M reviews, 150k businesses — venue reviews, not products |
| Amazon Customer Reviews (Datafiniti) | kaggle.com/datasets/datafiniti/consumer-reviews-of-amazon-products | UNVERIFIED (Kaggle auth) | 3.5k products, structured |
| Gowalla | — | UNVERIFIED | check-in LBSN — **not relevant** |
| IMDB | — | — | not relevant (movies) |

---

## 7. Agent / tool-use & shopping-agent benchmarks (as of 2026)

**Key discovery:** the curated list **`yifeizhangcs/awesome-agentic-commerce`** (github.com/yifeizhangcs/awesome-agentic-commerce) tracks the current agentic-commerce benchmark landscape — use it as the index.

| Benchmark | URL | Status | What it measures | Shopping-relevant? |
|---|---|---|---|---|
| **τ-bench** (retail domain) | https://github.com/sierra-research/tau-bench · taubench.com | **VERIFIED** (retail `products/users/orders.json` + tasks downloaded) | tool-agent↔simulated-user multi-turn task completion, scored on final DB state; `pass^k` reliability | **Yes** — canonical retail customer-service agent eval |
| τ²-bench | arxiv.org/abs/2506.07982 (Jun 2025) | UNVERIFIED | dual-control (user also acts on world) | Yes |
| τ³-bench | arxiv.org/abs/2603.13686 (Mar 2026) | UNVERIFIED | voice + knowledge retrieval retail/airline/telecom/banking | Yes (voice) |
| **WebShop** | https://github.com/princeton-nlp/WebShop | **VERIFIED (repo + setup.sh; data via `gdown` Google Drive, `-d small` = 1000 items)** | web-agent goal→product success + reward (1.18M products, 12,087 instructions) | **Yes** — the classic shopping web-agent benchmark |
| WebArena | https://github.com/web-arena-x/webarena | VERIFIED (repo) | web-agent tasks on real sites incl. Amazon/eBay/OneStopShop | Yes |
| Mind2Web (Shopping domain) | https://github.com/OSU-NLP-Group/Mind2Web | UNVERIFIED | 2,350 web tasks, 137 sites incl. shopping | Partial |
| ShoppingBench | arxiv.org/abs/2508.04266 (AAAI 2026) | UNVERIFIED | real-world intent-grounded shopping, multi-hop | **Yes** (Alibaba) |
| Shopping MMLU | arxiv (Jin et al. 2024) | UNVERIFIED | multimodal e-commerce reasoning | Yes |
| DeepShop | arxiv.org/abs/2506.02839 | UNVERIFIED | deep-research shopping agents | Yes |
| EComAgentBench | arxiv.org/abs/2606.17698 | UNVERIFIED | long-horizon shopping, hidden intent | Yes |
| ShopX | arxiv.org/abs/2606.31693 | UNVERIFIED | intent-to-item foundation model | Yes |
| WebMall | doi 10.1145/3805712.3808592 (SIGIR 2026) | UNVERIFIED | multi-shop web agents | Yes |
| ECom-Bench | aclanthology.org/2025.emnlp-industry.19 | UNVERIFIED | customer-support issue resolution | Yes (service) |
| EcomScriptBench / EcomBench / τ-Rec / AgentRecBench / SalesSim | via awesome list | UNVERIFIED | script planning / holistic / verifiable rec / personalization / user simulators | Yes |
| GAIA | huggingface.co/datasets/gaia-benchmark/GAIA | **UNVERIFIED (gated — needs HF auth + agreement)** | general assistant tool-use (466 Qs) | Partial (general) |
| BFCL / AgentBench / AssistantBench / WorkArena / SeeAct / WebVoyager / Mind2Web-Live | github / arxiv | UNVERIFIED | function-calling / general web / enterprise automation | Partial (not commerce-native) |

**For a 2-week prototype, the pragmatic agent-eval choice is τ-bench retail (already downloaded) + WebShop (`gdown` small).** Everything else is citations for an interview.

---

## 8. Recommended minimal stack (download today, no paid creds)

| Layer | Dataset + exact subset | On-disk | Schema → our tables |
|---|---|---|---|
| **Catalogue + images** | Amazon Reviews 2023 `raw/meta_categories/meta_Electronics.jsonl` (stream-filter) + `meta_Musical_Instruments` + `meta_Industrial_and_Scientific` | ~500 MB (or reuse on-disk `meta_electronics_0.parquet` 161k products + two subsets) | `products(parent_asin, title, description, features[], price, avg_rating, rating_count, categories[], images[]{thumb,large,hi_res}, store, brand)` |
| **Reviews** | `benchmark/5core/rating_only/Electronics.csv` (3.0M) + `reviews_electronics_part.jsonl` (365k with text) + MI/Industrial rating CSVs | ~430 MB | `ratings(user_id, parent_asin, rating, ts)` · `reviews(user_id, parent_asin, rating, title, text, helpful_vote, verified_purchase, ts)` |
| **Transactions + customers** | Olist (full 9-CSV star) | 121 MB | `customers, orders, order_items, payments, reviews, sellers, geolocation` |
| **Taxonomy** | Google Product Taxonomy `taxonomy.en-US.txt` | 436 KB | `taxonomy(id, name, path, parent_id)` + map Olist `product_category_name` |
| **Search intent (eval)** | ESCI `shopping_queries_dataset_examples.parquet` | 49 MB | `search_intent(query, product_id, esci_label, locale, split)` — label E/S/C/I → retrieval/relevance eval |
| **Agent eval** | τ-bench retail `products/users/orders.json` + `tasks_test.py` | 2.4 MB | `agent_tasks` + tool DB for tool-use eval |

**Totals ≈ 1.1 GB** (most already on disk). This hits every target: **~212k products** with images+descriptions, **~3.9M ratings + 365k text reviews**, **99k real transactions** with real customers (sparse repeat history), a **5.6k-category taxonomy**, and a **2.6M-row query→product label set** for evaluation.

### 8.1 Loader sketches

```python
# 1. Catalogue: meta JSONL -> products table
import json
def load_products(path):
    for line in open(path):
        o = json.loads(line)
        yield dict(
            product_id=o["parent_asin"], title=o.get("title"),
            description=" ".join(o.get("description") or []),
            features=o.get("features"), price=o.get("price"),
            avg_rating=o.get("average_rating"), rating_count=o.get("rating_number"),
            categories=o.get("categories"),
            image_thumb=((o.get("images") or [{}])[0] or {}).get("thumb"),
            image_large=((o.get("images") or [{}])[0] or {}).get("large"),
            brand=o.get("store"))

# 2. Ratings CSV -> ratings table (pandas/dask for scale)
#    pd.read_csv("ratings_electronics.csv") -> [user_id, product_id, rating, ts]

# 3. Olist -> transactions star (SQLite/Postgres)
#    load 9 CSVs verbatim; FK: orders.customer_id, items.order_id, items.product_id,
#    items.seller_id, reviews.order_id, payments.order_id

# 4. ESCI -> search_intent (parquet)
#    pd.read_parquet("esci_examples.parquet")
#      [example_id, query, query_id, product_id, product_locale, esci_label, split]
#    join product_id -> parent_asin (ESCI uses ASIN for us locale) for retrieval eval
```

### 8.2 Exact download commands (for anything not yet on disk)

```bash
cd data/raw
B="https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/resolve/main"
# Full 5-core ratings (Electronics) and other small categories
curl -L -o ratings_electronics.csv      "$B/benchmark/5core/rating_only/Electronics.csv"          # 897 MB
curl -L -o ratings_office.csv           "$B/benchmark/5core/rating_only/Office_Products.csv"       # 104 MB
curl -L -o ratings_videogames.csv       "$B/benchmark/5core/rating_only/Video_Games.csv"           #  47 MB
# Raw review text (Electronics, 22.6 GB — stream a prefix for a demo):
curl -L "$B/raw/review_categories/Electronics.jsonl" | head -n 400000 > reviews_electronics_part.jsonl
# ESCI products (join table for search-intent):
curl -L -o esci_products.parquet "https://media.githubusercontent.com/media/amazon-science/esci-data/main/shopping_queries_dataset/shopping_queries_dataset_products.parquet"
# Olist (no-auth mirror):
curl -L -o olist.zip "https://huggingface.co/datasets/MafiaAzulBr/olistcsv/resolve/main/archive.zip"  # or per-file as done
```

---

## 9. Synthetic-data recipes (for what does NOT exist openly)

### 9.1 Shopping-session logs (view → add-to-cart → purchase clickstream)
No open dataset ships complete **session logs** (impressions/clicks/abandonment). Generate them from Olist + Amazon ratings:

```python
import random, itertools
# Inputs: orders (real purchases), ratings (real affinity), products (price, category)
def gen_sessions(orders, ratings, products, n_sessions=200_000, seed=7):
    rng = random.Random(seed)
    user_items = ratings.groupby("user_id")["product_id"].agg(set)   # sparse history
    for uid, grp in orders.groupby("customer_id"):
        seq = []
        for pid in rng.sample(list(products.index), rng.randint(2, 12)):   # browse window
            seq.append({"ts": ..., "event": "view", "product": pid})
            if pid in user_items.get(uid, ()):                               # affinity
                if rng.random() < 0.15: seq.append({"event": "cart", "product": pid})
                if rng.random() < 0.05: seq.append({"event": "purchase", "product": pid})
        yield uid, seq
# Output: sessions(uid, [{ts, event∈{view,cart,purchase}, product}])  -> sessions + events tables
# Calibrate p(view->cart)≈0.10, p(cart->purchase)≈0.35 from Olist funnel.
```

### 9.2 Search-intent generation (query → product labels) when ESCI is too coarse
```python
def gen_queries(products, n=100_000):
    # templates over metadata -> query, with label E (exact) / S (substitute)
    for p in products.sample(n):
        yield {"query": f"{p['brand']} {p['title'][:40]}".strip(), "product_id": p.id, "label": "E"}
        yield {"query": p["categories"][-1], "product_id": p.id, "label": "S"}   # category-level substitute
```

### 9.3 Agent trajectories (tool-use, for τ-bench-style eval)
```python
# JSONL of OpenAI-compatible tool-call turns; tools = search/cart/compare/checkout against your DB
def gen_traj(products, user, seed):
    return {
      "task": "find a wireless over-ear headphone under $150 and add to cart",
      "turns": [
        {"role":"user","content":"...headphones under $150"},
        {"role":"assistant","tool_calls":[{"name":"search","args":{"query":"wireless over-ear","max_price":150}}]},
        {"role":"tool","name":"search","content": json.dumps(top_k)},
        {"role":"assistant","tool_calls":[{"name":"cart_add","args":{"product_id":"B0..."}}]},
        {"role":"tool","name":"cart_add","content": '{"ok":true}'},
      ],
      "expected_world_state": {"cart": ["B0..."]},   # ground truth for scoring
    }
```
This mirrors τ-bench's DB-state scoring so you can reuse `tau_bench`'s evaluator directly.

---

## 10-line summary

1. **Amazon Reviews 2023** (McAuley-Lab, HF) is the single richest free source — 571M reviews / 48M products; its 5-core `rating_only` CSVs and `raw/meta_*` product JSONL (with live image URLs) are verified and partially downloaded.
2. The legacy **2014 McAuley data and Amazon QA** have **moved** (UCSD 404) — use the 2023 dataset instead.
3. **Olist** (99k real orders → items → customers → reviews → payments) is downloadable **without Kaggle creds** via an HF mirror; 121 MB now on disk.
4. **ESCI / Amazon Shopping Queries Dataset** (Apache-2.0) gives 2.6M query→product `E/S/C/I` relevance labels — verified and on disk.
5. **Google Product Taxonomy** (5,596 categories, 442 KB) is downloaded and ready as the category ontology.
6. **τ-bench retail** (50 products, users, orders, tasks) is downloaded and is the pragmatic agent-eval harness; **WebShop** needs `gdown` (Google Drive), `-d small`.
7. Kaggle datasets (H&M, Instacart, Rossmann, Fashion Product Images) all **require a free account/API key** — no anonymous download.
8. The 2026 agentic-commerce benchmark landscape (ShoppingBench, DeepShop, EComAgentBench, τ³, WebMall, ShopX…) is indexed by `yifeizhangcs/awesome-agentic-commerce`.
9. Recommended 2-week stack ≈ **1.1 GB** on disk: 212k products + images, 3.9M ratings + 365k text reviews, 99k transactions, taxonomy, and a search-intent eval set.
10. Gaps (session logs, agent trajectories) are filled with the synthetic generators in §9 rather than purchased data.
