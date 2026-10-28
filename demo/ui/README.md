# Aria UI

Vanilla HTML/CSS/JS, no build step, no CDN (offline-safe).

## Run

```bash
python demo/run.py        # then open http://localhost:8000
```

## Features

- **Left** — chat transcript with streamed tokens, tool-call chips
  (`search_products → …`), inline citation pills (click to expand the source
  product/review), and product cards (image, title, price in USD, MRP
  strikethrough, discount %, rating, "Add to cart").
- **Right (collapsible)** — cart panel (items, total, budget bar, checkout →
  mock order id + ETA) and session panel (inferred constraints, flags,
  latency, tokens).
- **Top bar** — persona switcher (Alex · gift buyer / Rhea · value + quality / Guest), "reset session",
  and a 5-script "Demo scripts" dropdown (USD, US marketplace; one Hinglish code-mixed script).
- **Mic** — browser Web Speech API → transcript → `/api/voice` → chat.
- **Image** — upload button + drag & drop → `/api/image` (visual search +
  OCR).
- **System toggle** — reveals per-turn tools / latency / tokens / citations /
  guardrail flags.
- **Multilingual** — code-mixed / Hinglish input is a first-class capability
  (try `saste earphones under $50 for mom`).
- **Sources & data footer** — catalogue is Amazon Reviews 2023 (US), prices in
  USD, checkout is a mock.
- Responsive; no Google Fonts or CDN JS.
