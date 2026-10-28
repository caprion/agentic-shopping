# B4 — Consistency & demo polish

Coherence pass: catalogue is **Amazon US (USD)**, so the serving path now speaks
USD and US-marketplace personas.

- Currency: `$` everywhere in UI, `BudgetGuard` cap **$2,000**, `tools.DEFAULT_BUDGET = 2000.0`.
  Claim verifier still accepts `$` and `₹` (users may type either); display/defaults are USD.
- Personas: **Alex** (gift buyer, price-sensitive), **Rhea** (value + quality), **Guest**.
- Code-mixed / Hinglish input stays first-class (`saste earphones under $50 for mom`).
- Demo scripts (5) are USD and satisfiable by the real catalogue.
- Footer discloses Amazon Reviews 2023 (US), USD, mock checkout.
- Emerging-markets analysis lives in `research/`, not the UI.

Owns: `guardrails.py`, `tools.py`, `ui/**`, `main.py`, `tests/**` (currency/persona edits only).
