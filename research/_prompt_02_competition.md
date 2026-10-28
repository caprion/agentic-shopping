You are a competitive-intelligence analyst. Task: map the **agentic commerce / AI shopping** competitive landscape, and extract **defensible moats** we can implement in a prototype.

Research targets (use web_search/web_fetch/claude_web_research, 20-30 searches):
1. **Shopify**: Sidekick, agentic storefronts, Storefront MCP, Shopify Catalog/Product Graph, "agentic checkout", Shopify Plus/Search & Discovery, Magic, unified commerce, Shopify's public AI strategy (2025-2026).
2. **OpenAI**: ChatGPT shopping / Instant Checkout, Agentic Commerce Protocol (ACP) with Stripe, Shopping Research, ChatGPT App Store for merchants, "Buy with ChatGPT" / CUA checkout; plus any 2026 updates (advertising in ChatGPT, checkout agents, ACP adoption).
3. **Google**: AI Mode shopping, Google Merchant Center + "agentic" surfaces, Universal Commerce Protocol (UCP), Google Pay agentic checkout, Gemini shopping agents, "Try on" / virtual try-on.
4. **Perplexity**: Buy with Pro, Comet shopping agent, PayPal partnership, Perplexity Comet.
5. **Amazon** — only as a benchmark reference (do not duplicate another thread's depth).
6. **Others**: Alibaba/Temu (Accio), eBay (AI assistant "Ask eBay"), Etsy, Flipkart (Ask Flipkart), Meesho, Myntra/Ajio, Gumroad, Walmart (GenAI search), Target, Costco, Instacart (Ask Instacart), Klarna, Zalando, Rakuten, Croma/Nyka (India), Zepto/Blinkit (quick commerce India).
7. **Protocols & standards**: MCP (Model Context Protocol) in commerce, ACP, UCP, agentic SEO (GEO/AEO), llms.txt, Merchant API for agents, product feed standards (schema.org, GS1, Google Product Taxonomy), agent-readable checkout, "agentic identity"/delegated auth (e.g. Sign in with Google for agents, agent tokens).
8. **Business model reality**: who pays (affiliate/CPS vs first-party vs ads), commission take rates, CAC, whether agentic surfaces cannibalise or grow revenue, who owns the customer relationship, disintermediation risk, Amazon's "Buy for Me" disintermediation-of-Amazon concern (third-party sellers' traffic loss).

For each player produce a row/section: surface, autonomy level (search → recommend → transact), underlying technique, business model, what works, what breaks, and their moat.

Then:
- Produce a **moat matrix**: which moats are (a) data, (b) catalogue/inventory, (c) transaction history, (d) trust/fulfilment, (e) distribution, (f) interface habit, (g) evaluation data, (h) trust/safety, (i) multi-sided network effects. Which of these can a small startup/demo realistically own? Which are structurally impossible for a startup (and therefore where Amazon's moat is unassailable)?
- Produce a **"wedge" analysis**: what is the smallest agentic shopping experience a startup can build that incumbents cannot easily copy? Give 5 concrete wedges with feasibility scores.

Output: write `/home/sumit/agentic-shopping/research/02-competition-landscape.md` — 3000-5000 words, tables, URLs + access dates. End with a 10-line summary of the sharpest insights (especially anything about moats we can demo).
