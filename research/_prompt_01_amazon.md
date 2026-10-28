You are a research analyst. Task: build a deep, evidence-based dossier on **Amazon's current AI / agentic shopping experience** and its gaps.

Context: We are prototyping an end-to-end "agentic shopping" app (like Amazon's new AI shopping experiences) and writing a dossier for a job application / PM thinking. Your output feeds the architecture and demo design.

Do this:
1. Research (use web_search, web_fetch, claude_web_research — be thorough, 15-25 searches minimum) the CURRENT state of Amazon's AI shopping:
   - **Rufus** (Amazon's shopping assistant): what it does, architecture disclosures (Amazon Science blog + papers), model families used, how it connects to Amazon's catalogue graph, what it does NOT do.
   - **"Help Me Decide"** (2025 feature): what it is, how it reasons across products.
   - **Buy for Me** (agentic purchase of products outside Amazon, 2025): how it works, autoplay, agent's autonomy level.
   - **Interests / "Shop by Interest"**, **Rufus price-history/price-tracking**, **Amazon Lens (visual search)**, **Generative AI listing creation for sellers**, **Amazon Haul**, **Buy with Prime in Rufus**, **Rufus in India / vernacular / voice**, **AI-powered deals & "Smart Deal" selection**, **Amazon Q / agentic seller tools**, **Alexa+ shopping**.
   - 2026-era developments if any (we are in late 2026 — check for the newest announcements, Rufus 2.x, "Amazon Shopping Coach", agentic APIs).
2. For each feature, record: what it does, the surface, the AI technique, the funnel metric it targets, what Amazon publicly says about the underlying tech, and the user-visible limitations/ complaints (social media, reviews, Reddit/HN threads, reviewers).
3. Do a rigorous **pros / cons / gaps** analysis: where Amazon's experience is strong, where it is weak or frustrated, what is missing (multi-item comparison, budget/commerce constraints, cross-merchant checkout, transparent trade-offs, multimodal, multilingual/voice, accessibility, price transparency, returns, trust).
4. Note the **evaluation problem**: how would you measure quality of an agentic shopping answer (relevance, faithfulness, conversion, regret, CSAT, hallucinated attributes)? Cite how Amazon/others measure it.
5. Capture **pricing/tech-constraint reality**: Amazon's catalogue scale (~hundreds of millions of ASINs), the catalogue-graph / product-attribute normalisation problem, review corpora, "Amazon Product Advertising API" and the affiliate/search API constraints an external builder faces.

Output requirements:
- Write a single well-structured markdown file to `/home/sumit/agentic-shopping/research/01-amazon-ai-shopping.md`.
- Use tables. Cite every non-obvious claim with a URL and an access date.
- Include a section "10 product claims we can safely make in an interview" and "5 things a candidate would be wrong to claim".
- Include an ASCII architecture sketch of how Amazon's shopping assistant is believed to work (product graph + retrieval + LLM + tools + ranking).
- Aim for 3000-5000 words. Do not pad; be specific.
- Do not write anything outside that file except tiny notes if needed. End with a 10-line summary of your key findings.
