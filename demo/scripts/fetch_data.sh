#!/usr/bin/env bash
# Reproduce every raw dataset this project uses.
# Sources are public; each URL is pinned to a specific file/revision.
# Usage:  bash demo/scripts/fetch_data.sh [out_dir]   (default: repo/data/raw)
set -euo pipefail
RAW="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/data/raw}"
mkdir -p "$RAW"

HF="https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/resolve/main"

echo "==> Amazon Reviews 2023 — product metadata (Electronics shard)"
curl -L --fail -o "$RAW/meta_electronics_0.parquet" \
  "$HF/raw_meta_Electronics/full-00000-of-00010.parquet"

echo "==> Amazon Reviews 2023 — product metadata (Musical Instruments, Industrial)"
for cat in Musical_Instruments Industrial_and_Scientific; do
  curl -L --fail -o "$RAW/meta_${cat}_subset.jsonl" \
    "$HF/raw/meta_categories/meta_${cat}.jsonl"
done

echo "==> Amazon Reviews 2023 — raw reviews (Electronics, first 500 MB)"
curl -L --fail -r 0-500000000 -o "$RAW/reviews_electronics_part.jsonl" \
  "$HF/raw/review_categories/Electronics.jsonl"

echo "==> Amazon Reviews 2023 — 5-core ratings (Electronics, first 250 MB)"
curl -L --fail -r 0-250000000 -o "$RAW/ratings_electronics.csv" \
  "$HF/benchmark/5core/rating_only/Electronics.csv"

echo "==> Amazon Shopping Queries (ESC) — gold eval set"
curl -L --fail -o "$RAW/esci_examples.parquet" \
  "https://media.githubusercontent.com/media/amazon-science/esci-data/main/shopping_queries_dataset/shopping_queries_dataset_examples.parquet"

echo "==> Google Product Taxonomy"
curl -L --fail -o "$RAW/google_product_taxonomy.en-US.txt" \
  "https://www.google.com/basepages/categories/taxonomy.en-US.txt" || echo "  (optional, skipped)"

echo "==> Olist Brazilian E-Commerce (real transactions)"
OLIST_BASE="https://raw.githubusercontent.com/Ganesh7699/Brazilian-E-Commerce-OList/main"
mkdir -p "$RAW/olist"
for f in olist_customers_dataset.csv olist_orders_dataset.csv olist_order_items_dataset.csv \
         olist_order_payments_dataset.csv olist_order_reviews_dataset.csv \
         olist_products_dataset.csv olist_sellers_dataset.csv; do
  curl -L --fail -o "$RAW/olist/$f" "$OLIST_BASE/$f"
done

echo "==> Done. Raw files in $RAW"
ls -la "$RAW"
