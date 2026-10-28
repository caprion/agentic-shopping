# Aria UI

Vanilla HTML/CSS/JS, no build step, no CDN (offline-safe).

## Run

```bash
python demo/run.py        # then open http://localhost:8000
```

## Features

- **Left** — chat transcript with streamed tokens, tool-call chips
  (`search_products → …`), inline citation pills (click to expand the source
  product/review), and product cards (image, title, price in **USD**
  `$1,234`, MRP strikethrough, discount %, rating, "Add to cart").
- **Right (collapsible)** — cart panel (items, total, budget bar against the
  **$2,000 USD session cap**, checkout → mock order id + ETA) and session panel
  (inferred constraints in `$` — `min $x` / `max $x`, flags, latency, tokens).
- **Top bar** — persona switcher (**Alex** · gift buyer, price-sensitive /
  **Rhea** · value + quality, wants warranty + rating evidence / **Guest**),
  "reset session", and a 5-script "Demo scripts" dropdown (US marketplace,
  USD; one Hinglish code-mixed script).
- **Mic** — browser Web Speech API → transcript → `/api/voice` → chat.
- **Image** — upload button + drag & drop → `/api/image` (visual search + OCR).
- **System toggle** — reveals per-turn tools / latency ms / tokens in-out /
  citations count / guardrail flags.
- **Multilingual** — code-mixed / Hinglish input is a first-class capability:
  "Try a code-mixed query: `saste earphones under $50 for mom`". The persona
  switcher never restricts the input language.
- **Sources & data footer** — catalogue is Amazon Reviews 2023 (**US**), prices
  in **USD**, checkout is a **mock** (no real orders or payments).
- Responsive; no Google Fonts or CDN JS.

## Demo scripts (one click, all satisfiable by the catalogue)

1. gift for brother's birthday, ≤ $300, photography
2. ANC headphones ≤ $200, ≥ 4.5★
3. compare Sony WH-1000XM5 vs Bose QuietComfort 45 (price / battery / comfort)
4. `saste earphones under $50 for mom, no plastic` — Hinglish + negation
5. best laptop for video editing ≤ $1,500
