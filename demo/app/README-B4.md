# B4 — Consistency & demo polish

The catalogue is **Amazon US (USD)**, so the whole serving path now speaks USD.

- Currency: `$` in the UI (`en-US` grouping, `min $x` / `max $x` chips),
  `BudgetGuard` cap **$2,000**, `tools.DEFAULT_BUDGET = 2000.0`,
  `main.DEFAULT_BUDGET = 2000.0`. The claim verifier still accepts `$` **and**
  `₹` (users may type either); display and defaults are USD.
- Personas: **Alex** (gift buyer, price-sensitive), **Rhea** (value + quality,
  wants warranty + rating evidence), **Guest**. Code-mixed / Hinglish input
  stays first-class (`saste earphones under $50 for mom`).
- Demo scripts (5) are USD and satisfiable by the real catalogue; the brand
  filter now also matches Renewed listings by title, so the XM5/QC45
  comparison resolves.
- Footer discloses Amazon Reviews 2023 (US), USD, mock checkout.

Owns: `guardrails.py`, `tools.py`, `ui/**`, `main.py`, `tests/**`.
