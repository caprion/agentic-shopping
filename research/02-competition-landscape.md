# Agentic Commerce / AI Shopping — Competitive Landscape & Defensible Moats

**Prepared:** 2026-10-28 (all URLs accessed this date)
**Scope:** Competitive-intelligence map of agentic commerce (2025–2026), followed by a moat matrix and a startup wedge analysis.
**Autonomy scale used throughout:** `Search → Recommend → Transact` (S/R/T), plus `Post-purchase` (returns, reorder) where relevant.

---

## 0. Executive summary

Agentic commerce stopped being a demo in 2025 and became a land-grab in 2026. The defining event is **protocol fragmentation with convergent mechanics**: OpenAI+Stripe shipped ACP (Sept 2025), Google+Shopify shipped UCP (Jan 2026), Google+PayPal shipped AP2 (Sept 2025), Mastercard shipped Verifiable Intent, Visa shipped Trusted Agent Protocol, and Coinbase shipped x402. Everyone is building a **checkout/identity/trust layer**, but the product experience has already bifurcated into two worlds that matter enormously for a startup:

1. **The "curated app" world** — OpenAI retreated from native checkout (only ~12 of millions of Shopify merchants went live) and moved to a small pool of big retailer apps (Target, Instacart, DoorDash). Protocols serve *few, large, integrated* merchants.
2. **The "long-tail" world** — millions of merchants will never integrate a protocol. This is where Rye (universal checkout via browser automation, 15,000+ merchants) and, more importantly, *any startup that can own the pre-purchase decision* can live.

The single sharpest insight for a demo: **the moat is shifting from "who holds the catalog" to "who holds the evaluation trace."** Cookie attribution is dying; agent-mediated transactions are deterministic and log the entire decision chain. Whoever owns the *evaluation data* (why the agent chose A over B) owns attribution — and attribution is the revenue plumbing. That is a moat a startup can realistically seed, and it is *not* something Amazon, Google, or OpenAI currently expose to third parties.

Amazon remains structurally unassailable on four axes (fulfilment, transaction history, trust/safety at checkout, and first-party advertising on its own traffic), but it is *not* unassailable on evaluation data, cross-retailer discovery, or long-tail coverage — which is precisely the gap a startup wedge should target.

---

## 1. Market sizing & timeline (orientation)

| Source | Estimate |
|---|---|
| Bain & Company (Dec 2025) | US agentic commerce **$300–500B by 2030**, 15–25% of US e-commerce |
| Morgan Stanley AlphaWise (Nov 2025) | US **$190B by 2030**, ~10% of US e-commerce |
| McKinsey (Oct 2025) | **$3–5T/yr globally by 2030** (bull case) |
| Gartner | **20%** of digital commerce transactions via AI platforms |
| Industry (multiple) | $190B–$385B US spend through AI agents by 2030; 20–30% of online transactions agent-mediated |

Key dated milestones (2025→2026): Operator/CUA (Jan 2025) → Amazon Buy for Me beta (Apr 2025) → Perplexity+PayPal (May 2025) → Walmart Sparky (Jun 2025) → Shopify Catalog API (Jul 2025) → OpenAI Instant Checkout + ACP (Sep 29, 2025) → Perplexity Comet free worldwide (Oct 2025) → Perplexity Buy with Pro (Nov 2025) → **UCP (Shopify+Google, Jan 2026)** → **ChatGPT Ads pilot (Feb 2026)** → **OpenAI retreats from native checkout (Mar 2026)** → ChatGPT Ads self-serve (May 2026) → Rufus→Alexa for Shopping (May 2026) → Klarna Agentic Product Protocol (Dec 2025/2026).

---

## 2. Player-by-player

### 2.1 Shopify — the merchant-side aggregator

- **Surface:** Sidekick (merchant admin copilot); Storefront MCP (every store has a zero-setup AI endpoint); Shopify Catalog / Global Catalog + Storefront Catalog; Agentic Storefronts (central admin control); Agentic Plan; UCP; Shop app.
- **Autonomy:** R (for shoppers) but **T** via embedded checkouts it controls (Google AI Mode, Gemini, Copilot Checkout, ChatGPT). Sidekick is R→Act on the merchant side.
- **Technique:** Product Graph — embeds billions of products, HNSW/FAISS nearest-neighbor clustering, LLM-based categorization/enrichment/standardization. Catalog API exposes real-time price/stock/variants. Storefront MCP = "connect any assistant to real-time commerce data."
- **Business model:** SaaS plans (Basic $39 → Plus $2,300/mo) + ~2.9%+30¢ payment take rate; **Agentic Plan is $0/mo** for non-Shopify merchants (they pay standard payment rates only when Shopify Checkout settles a sale) — a land-grab to be the catalog/checkout substrate even for SAP/ERP merchants.
- **What works:** Symmetry — one data entry, syndicated to every AI channel. Traction: 7× AI-driven traffic since Jan 2025, ~11–15× orders attributed to AI search. UCP endorsed by 20+ retailers. Finkelstein's stated moat: **transaction integrity** (checkout, subscriptions, inventory, shipping, taxes, merchandising) + flywheel of "millions of stores, billions of transactions, trillions of data points."
- **What breaks:** It owns the *plumbing*, not the *customer*. It is dependent on the AI surfaces (Google, OpenAI, Microsoft) for demand. OpenAI's retreat showed Shopify's checkout ambition is gated by how well the AI firms onboard merchants.
- **Moat:** Catalogue/inventory (largest normalized product graph outside Amazon), transaction integrity, multi-sided network (merchant↔surface). Defensible, but *surrenders the customer relationship to the LLM front-ends*.

### 2.2 OpenAI — demand side, retreating to "apps + ads"

- **Surface:** ChatGPT search/shopping; Instant Checkout (Sept 2025, now deprecated for listings); ChatGPT **Apps** (Target, Instacart, DoorDash, Expedia, The Knot, Booking.com); Operator/CUA browser agent; ChatGPT Ads (Feb 2026).
- **Autonomy:** R→T, but T is being **outsourced to retailer apps**. CUA can still drive any web checkout (browser-level autonomy).
- **Technique:** ACP (co-developed with Stripe, Apache 2.0). GPT-class models for discovery; CUA/Operator for browser automation.
- **Business model:** (1) subscription; (2) **merchant take rate ~2–4%** on purchases; (3) **advertising** — ChatGPT Ads pilot at $60 CPM / $200K min (Feb 2026), self-serve Ads Manager May 5, 2026, CPMs $20–80, CPCs $1.50–6.00; ~900M WAU (Feb 2026), most free tier.
- **What works:** 900M+ WAU distribution; ~50M shopping queries/day; ACP as an open standard gave it ecosystem credibility; app model delegates messy ops (tax, inventory) to retailers.
- **What breaks:** Native checkout failed to scale — only ~12 of millions of Shopify merchants went live; no sales-tax remittance as of Feb 2026; users researched but didn't buy in-chat. Merchant adoption is the bottleneck (per Finkelstein, on the AI firms' side, not merchants').
- **Moat:** Distribution/interface habit (the default conversational front-end), plus a growing ad auction. *Weakest axis:* fulfilment and catalogue — hence the app pivot and the $15B→$50B Amazon investment.

### 2.3 Google — search gravity + open standards

- **Surface:** AI Mode (Search) + Gemini app; Google Merchant Center; agentic checkout; Direct Offers pilot; Google Pay as credential provider; virtual try-on.
- **Autonomy:** R→T (agentic checkout on eligible listings; "buys on the customer's behalf on the merchant's site at the customer's direction").
- **Technique:** **UCP** (co-developed with Shopify, Jan 2026) — open, transport-agnostic (REST, MCP, AP2, A2A); Mastercard Verifiable Intent co-developed with Google; Gemini models.
- **Business model:** Ads (the incumbent search monopoly) + Google Pay rails + Merchant Center. Commerce is a retention/ads play, not primarily a take-rate play.
- **What works:** Unmatched top-of-funnel gravity and the credibility to make UCP the de-facto standard (Flipkart, Ulta, 20+ retailers backing). Google Pay/Wallet hold the *credentials* that make one-click agentic checkout frictionless.
- **What breaks:** Same merchant-onboarding tax as everyone; anti-trust scrutiny of bundling commerce into search; limited own-inventory (no first-party fulfilment).
- **Moat:** Distribution (search), identity/credentials (Google Wallet/Pay), and **standard-setting** (UCP). Structurally dominant but slow.

### 2.4 Perplexity — the aggressor on evaluation + publisher share

- **Surface:** Perplexity Shopping; **Buy with Pro** (Nov 2025, in-chat one-click via PayPal passkey); **Comet** browser (free worldwide Oct 2025; launched $200/mo Max tier Jul 2025).
- **Autonomy:** R→T (PayPal instant buy), and Comet extends to browser-level T (auto-fill, multi-site research, aggregate reviews, compare pricing).
- **Technique:** Answer-engine + structured product data + PayPal passkey auth; Publishers' Program.
- **Business model:** Subscription (Pro/Max) + a **rev-share precedent: 80% of subscription revenue to cited publishers** — the clearest live example of "data-source attribution."
- **What works:** Fastest to close the loop on evaluation→transaction; PayPal partnership removed the checkout-integration tax; Comet makes the *browser itself* the agent, sidestepping merchant protocols entirely.
- **What breaks:** Tiny DAU vs Google/ChatGPT; depends on PayPal for trust; browser market share vs Chrome/Safari is a brutal distribution fight.
- **Moat:** Interface habit among power users + a nascent **publisher/evaluation-data moat** (the 80% rev-share makes it the most attractive home for the content that agents cite). Fragile but strategically well-placed.

### 2.5 Amazon — benchmark (do not re-derive in depth)

- **Surface:** Rufus → **Alexa for Shopping** (May 13, 2026); **Buy for Me** (Apr 2025 beta); **Auto-buy** (target-price, auto-purchase with 30/90-day price history).
- **Autonomy:** R→T **and** Post-purchase (reorder, price-watch, returns via A-to-Z).
- **What works (why it's the benchmark):** Rufus drove ~$12B incremental annualized sales in 2025; 60% higher conversion for engaged sessions; 250M MAU (Nov 2025). Buy for Me extends Amazon's checkout, tracking, and **A-to-Z guarantee** onto *external* merchants' sites — Amazon inserts itself as the trust/fulfilment layer even when it doesn't hold inventory.
- **What breaks / risk:** Buy for Me triggered a **brand revolt** — DTC brands found their full catalogs scraped and listed *without permission*; Shopify added a default "Robots & Agent policy" to block scraping; Amazon also **blocks outside AI apps from showing its products** (walled garden). It is betting ~$15–50B on OpenAI while refusing to interoperate.
- **Moat (unassailable):** fulfilment/Prime logistics, transaction history, checkout trust/safety, first-party ad monetization on its own traffic. **The one place it is *not* unassailable:** cross-retailer evaluation and any surface that isn't Amazon-owned.

### 2.6 Others (condensed table)

| Player | Surface / autonomy | Technique | Model | Note |
|---|---|---|---|---|
| **Walmart** | Sparky (Jun 2025) + Wally; OpenAI partnership (Oct 2025) | GenAI search w/ Microsoft models + shopper data | First-party retail + ads | Occasion-based ("plan a cookout") planning; S→R→T |
| **Instacart** | Ask Instacart; Cart Assistant; ChatGPT app (Dec 2025) | ChatGPT + own models over 1B+ SKUs, 80K stores | Delivery + ads + enterprise AI | Grocery's deepest catalog + fulfilment |
| **Klarna** | AI assistant; Agentic Product Protocol (Dec 2025) | Open product standard, 100M+ SKUs; Stripe SPT integration | BNPL + payments | Buying as credit/payments wedge |
| **Alibaba Accio** | Accio sourcing engine + Accio Work agent | NLP search, 7,600 categories, 200M+ products | B2B sourcing, cross-border | 500K SME users; supplier-matching |
| **eBay** | "Ask eBay" assistant | LLM over listings | Marketplace | Legacy trust + used-goods inventory |
| **Etsy** | Early Instant Checkout partner | Catalog via ACP | Marketplace + handmade supply | Long-tail handmade inventory |
| **Zalando / Rakuten** | GenAI stylist / search | LLM + inventory | First-party retail / cashback | Regional (EU/JP) moats |
| **Flipkart (India)** | Flippi (2023) → **SLAP** standalone app (Jan 2026) | Conversational commerce; FCC; backs UCP | Marketplace + ads | Google-Flipkart Gemini/AI Mode buying pilot |
| **Myntra / Nyka / Croma (India)** | GenAI style/beauty/electronics assist | LLM over curated catalog | First-party + marketplace | Category trust |
| **Zepto / Blinkit / Instamart (India)** | "Zap" multi-agent CS (Zepto); AI basket | Agentic support; **no public MCP yet** | Quick commerce (10–20 min) | $6.78B GQV 2025 → $12.97B 2029; agent interface gap open |

India is notable for two reasons: (1) the fastest-growing quick-commerce layer has **not yet exposed machine-readable agent interfaces** — a wide-open wedge; (2) Google is running its first cross-border agentic-buying pilot on Flipkart, making India a live lab for UCP.

---

## 3. Protocols & standards layer

| Standard | Authors / date | What it does | Status |
|---|---|---|---|
| **MCP** | Anthropic (Nov 2024, open) | Connect models to tools/data | De-facto tool-call standard; Shopify, Amazon Ads (Feb 2026) adopt |
| **ACP** | OpenAI + Stripe (Sep 2025, Apache 2.0) | Agent↔merchant↔payment transaction standard | Live; narrowed to "apps" after Mar 2026 |
| **UCP** | Shopify + Google (Jan 2026, open) | Full commerce journeys incl. checkout; transport via REST/MCP/AP2/A2A | Rolling out (AI Mode, Gemini) |
| **AP2** | Google + PayPal + 50 orgs (Sep 2025) | Payment-authorization layer on A2A + MCP; Verifiable Credentials | Live |
| **A2A** | Google (2025) | Agent-to-agent communication | Live |
| **Verifiable Intent / Agent Pay** | Mastercard (+Google) | Cryptographic record of user authorization; agent identity | Live (Fiserv etc.) |
| **Trusted Agent Protocol** | Visa | Identify registered agents; link agent↔consumer; signal browse-vs-pay | Live |
| **x402** | Coinbase | HTTP-402 stablecoin micropayments ($0.001/query) | Live; AWS integration 2026 |
| **llms.txt / GEO / AEO** | Community | "robots.txt for LLMs"; agent-readable site intent | Emerging, not standardized |
| **Schema.org / GS1 (GTIN, GPC) / Google Product Taxonomy** | Standards bodies | Machine-readable product identity/attributes | Mature (the substrate) |
| **Agentic identity / delegated auth** | Google Cloud, Descope, SecureAuth, Okta | SPIFFE-based agent identity; OAuth/PKCE delegation; scoped tokens | Rapidly maturing |

**Pattern:** commerce has produced *too many* adjacent standards in 12 months. The rationalization is predictable: UCP (checkout) + AP2 (payments) + Verifiable Intent (trust) are converging into one stack, with MCP as the tool-call substrate. For a startup, the standards layer is **table stakes to speak, not a place to win** — building a proprietary checkout protocol is a dead end; the winning moves are (a) consume the standards, (b) own something the standards *don't* define (evaluation, decision traces, cross-retailer arbitrage).

---

## 4. Business-model reality

- **Who pays:** Three emerging revenue pools — (1) **merchant take rates** (OpenAI 2–4%; Shopify ~2.9%+30¢); (2) **advertising** (ChatGPT Ads CPM $20–80, CPC $1.50–6.00; Google/Amazon incumbent auctions); (3) **affiliate/CPS** (legacy 5–30%, Amazon 1–4% but 46% of the network).
- **The affiliate collapse:** $13B US / $20B global affiliate industry is built on cookies + last-click. Agents don't click, don't carry cookies, don't cross the 14-touchpoint funnel — the funnel compresses to 1–2 interactions *inside the agent*. Chrome killed third-party cookies in late 2025. Attribution infrastructure "never fires" for agent purchases.
- **The paradox that creates the startup moat:** agent transactions are **deterministic and fully logged** — *better* attribution than cookies ever gave, *if* someone owns the log. New models emerging: **data-source attribution** (credit who *informed* the decision), **micropayments** (x402), **discovery premiums** (pay to be agent-discoverable), **agent-as-affiliate** (agent builders become a commission channel). Perplexity's 80% publisher rev-share is the proof-of-concept.
- **Cannibalization vs growth:** Amazon's $12B incremental Rufus sales and 60% higher conversion show agent surfaces *grow* first-party GMV when you own the customer. OpenAI's retreat shows the opposite risk when you *don't* own fulfilment: users researched in ChatGPT but converted elsewhere — checkout leakage, not cannibalization, was the killer.
- **Who owns the customer:** Amazon owns it (A-to-Z, Prime, tracking). OpenAI is *surrendering* it to retailer apps. Shopify owns the *merchant* relationship but not the shopper. **The customer relationship is still up for grabs at the discovery/evaluation layer** — and that is the only layer a startup can realistically contest.

---

## 5. Moat matrix

Legend: **S = structurally impossible for a startup**, **N = network-effect (hard but path-dependent)**, **Y = realistically ownable by a small team/demo**.

| # | Moat | Who owns it today | Startup can own? | Why |
|---|---|---|---|---|
| (a) **Data** (user intent/preference graph) | Google, Amazon, Meta | **N** | Only via a narrow vertical or a sticky loop; general-purpose user data is a scale game |
| (b) **Catalogue / inventory** | Amazon, Shopify, Walmart, Instacart | **N** | Aggregation is winnable only in a niche; general catalog is a capital + integration slog |
| (c) **Transaction history** | Amazon, card networks, banks | **S** | Years of receipts + returns + fraud labels; no shortcut |
| (d) **Trust / fulfilment** (logistics, guarantees, returns) | Amazon, Walmart, Instacart | **S** | Physical infrastructure + A-to-Z-grade guarantee requires capital & time |
| (e) **Distribution** (where the eyeballs are) | Google, OpenAI, Amazon, Meta | **S** | You cannot out-distribute a 900M-WAU assistant or a search monopoly |
| (f) **Interface habit** | OpenAI (ChatGPT), Google, Amazon app | **N** | Writable only with a radically better surface (e.g., Comet), and even that is brutal |
| (g) **Evaluation data** (why A beat B; decision traces) | *No one yet* | **Y — the white space** | Agents log decisions deterministically; no incumbent exposes this; attribution runs on it |
| (h) **Trust / safety** (agent identity, fraud, verifiable intent) | Visa, Mastercard, Google, Stripe | **N→Y (niche)** | Standards are being set by incumbents, but *verification tooling* for the long tail is open |
| (i) **Multi-sided network effects** (merchant↔surface↔buyer) | Amazon, Shopify, Google | **N** | Needs both sides at once; only a niche two-sided loop is viable |

**Reading of the matrix:**
- Amazon's unassailable moat = **(c) + (d) + (h) + first-party ads on (e)**. Any startup that tries to out-fulfil, out-trust, or out-distribute Amazon loses. This is the hard constraint on every wedge.
- **The structurally open moat = (g) evaluation data.** The entire affiliate/attribution industry is about to migrate from "last cookie" to "who informed the agent's decision," and *no incumbent owns the decision trace*. Shopify has the catalog, Google has the query, Amazon has the purchase — but none of them currently owns or exposes the *comparison/evaluation record*. That record is also the key input to (a) future data moats: evaluation data is the only user-intent signal you can accumulate without already being at scale.
- **Realistic startup portfolio:** (g) as the wedge → feed (a) in a vertical → then (b) niche catalog → eventually (i) a niche two-sided loop. (c), (d), (e) are off the table; (f) and (h) are opportunistic.

---

## 6. Wedge analysis — smallest agentic shopping experiences incumbents can't easily copy

Feasibility = (build effort low → 10) × (defensibility) × (incumbent inertia). Scored /10.

**W1 — "Evaluation trace as a product": the receipts-for-decisions layer.**
Build a shopping agent that, for every recommendation, *emits a signed, deterministic evaluation trace* (which sources it read, which attributes it compared, why it picked A). Sell that trace to merchants/publishers as a new attribution primitive, and to buyers as "show your work" trust. Incumbents won't copy fast because (a) it cannibalizes their own closed attribution, (b) it requires being neutral across retailers, which Amazon/Google structurally are not. **Feasibility 8/10.** Highest-moat/lowest-scale option; the hard part is getting a purchase event to flow through your trace, but you can start with *referral + CPS pass-through* without owning checkout.

**W2 — Vertical "decision agent" for a high-involvement niche (e.g., baby gear, PC parts, EVs/home energy, camera lenses).**
Own the *evaluation* for a category where wrong choices are costly and where Amazon's "most reviews" signal is weak. Deep domain knowledge + a curated comparison graph + affiliate/CPS monetization. Incumbents generalize; you specialize. Shopify/Amazon supply the catalog and checkout via API/affiliate so you own only the decision. **Feasibility 7/10.** Defensible via (g)+(a) compounding, but niche CAC can be punishing.

**W3 — Quick-commerce / India agent-interface gap.**
Zepto/Blinkit/Instamart have inventory + 10–20-min fulfilment but *no public MCP/agent interface*. A thin MCP server that exposes their catalogs + a basket agent ("restock my kitchen under ₹2,000 before 7pm") would be the first mover on agentic quick-commerce. Risk: they ship their own interface next quarter. **Feasibility 6/10** (fast, but the platform can close it at will — treat as a demonstration, not a company).

**W4 — "Agent-readable storefront" / llms.txt-for-commerce as a service.**
A drop-in tool that turns any Shopify/WooCommerce store into a perfectly agent-discoverable store (llms.txt + schema.org/GS1 + a Storefront-MCP-compatible endpoint + a product feed that stays fresh). Monetize as a SaaS or a "discovery premium" marketplace. This rides Shopify's own Storefront MCP but targets the *long tail that Shopify's Agentic plan hasn't reached* (custom platforms, marketplaces, small merchants). **Feasibility 6/10** — easily copyable by Shopify, so defensibility is only in the long tail + SEO-for-agents expertise.

**W5 — "Buy-for-me, neutral" trust layer: delegated identity + verifiable intent for the long tail.**
A lightweight delegated-auth + approval primitive (scoped OAuth/PKCE, spend limits, per-purchase human confirmation) that any small agent can use to transact safely — a consumer-facing "agent wallet" independent of Amazon/PayPal/Google. Visa/Mastercard are building the *network* side but not a *neutral consumer UX* for the long tail. **Feasibility 5/10** — the moat is real (trust/safety, (h)) but it's a security/capital-intensive and regulatory-adjacent play, hard for a pure demo.

**Recommended demo path:** W1 as the core (evaluation-trace engine) wrapped in W2 (one vertical) with W4 (agent-readiness) as the distribution tactic — and W3 as the optional India flash-demo.

---

## 7. Sharpest insights (10 lines)

1. Agentic commerce split into two worlds in 2026: **curated apps for ~a dozen big retailers** (OpenAI's retreat after only 12 of millions of Shopify merchants went live) and a **long tail no protocol will ever reach** — the long tail is the startup's arena.
2. **The moat is migrating from "who holds the catalog" to "who holds the evaluation trace"** — agents log decisions deterministically, and no incumbent owns or exposes that log.
3. Affiliate/attribution is dying (cookies gone, last-click dead) and being replaced by **data-source attribution** — Perplexity's 80% publisher rev-share is the live proof; this is the revenue plumbing a startup can seed.
4. **Amazon is unassailable on fulfilment, transaction history, checkout trust, and first-party ads** — do not compete there; every viable wedge assumes Amazon keeps those.
5. Amazon's one weakness is also its strategy: it **scrapes brands without permission (Buy for Me revolt) and blocks outside AI from showing its products** — a walled garden that creates demand for a *neutral* evaluation layer.
6. Shopify owns **transaction integrity + the largest normalized product graph** but surrenders the customer to LLM front-ends — it is the catalog you should *consume*, not the company you should fight.
7. OpenAI learned the hard lesson: **you can't monetize checkout if you don't own fulfilment** — checkout leakage killed Instant Checkout, not cannibalization.
8. The **payments/trust stack is converging** (UCP + AP2 + Verifiable Intent + x402) — building a proprietary checkout protocol is a dead end; the winning move is to *consume* standards and own what they don't define.
9. India's **quick-commerce layer (Zepto/Blinkit/Instamart) has no public agent interface yet** — a narrow, time-boxed wedge and a great demo surface.
10. **A startup's realistic moat portfolio = evaluation data (W1) → vertical intent data → niche catalog → niche two-sided loop**; distribution, fulfilment, transaction history, and general-purpose user data are structurally off the table.

---

## 8. Sources (accessed 2026-10-28)

**Shopify**
- https://www.shopify.com/news/ai-commerce-at-scale (UCP, Agentic plan, AI Mode/Gemini/Copilot)
- https://www.shopify.com/blog/how-agentic-commerce-works
- https://shopify.engineering/catalog-clustering (Product Graph, HNSW/FAISS)
- https://shopify.dev/docs/agents (Global/Storefront Catalog)
- https://www.shopify.com/sidekick
- https://www.bydas.com/en/publications/shopify-edition-spring-2026

**OpenAI / Stripe**
- https://openai.com/index/buy-it-in-chatgpt (Instant Checkout + ACP)
- https://stripe.com/newsroom/news/stripe-openai-instant-checkout
- https://www.digitalcommerce360.com/2026/03/06/openai-shifts-checkout-plans-agentic-commerce-strategy
- https://rye.com/blog/openai-chatgpt-checkout-agentic-commerce (12 merchants, tax, universal checkout)
- https://www.digitalcommerce360.com/2025/11/19/target-to-launch-app-in-openai-chatgpt
- https://intuitionlabs.ai/articles/chatgpt-ads-economic-analysis
- https://segwise.ai/blog/chatgpt-ads-2026-guide

**Google / UCP / AP2**
- https://support.google.com/merchants/answer/16837055 (UCP + agentic checkout)
- https://blog.google/products/ads-commerce/agentic-commerce-ai-tools-protocol-retailers-platforms
- https://cloud.google.com/blog/products/ai-machine-learning/announcing-agents-to-payments-ap2-protocol
- https://cloud.google.com/blog/topics/financial-services/introducing-an-agentic-commerce-solution-for-merchants-from-paypal-and-google-cloud
- https://developer.paypal.com/community/blog/PayPal-Agent-Payments-Protocol

**Perplexity**
- https://stellagent.ai/insights/perplexity-shopping-buy-with-pro
- https://www.cnbc.com/2025/10/02/perplexity-ai-comet-browser-free-.html
- https://finance.yahoo.com/news/perplexity-selects-paypal-power-agentic-120000920.html

**Amazon**
- https://www.aboutamazon.com/news/retail/amazon-shopping-app-buy-for-me-brands
- https://www.modernretail.co/technology/brands-are-upset-that-buy-for-me-is-featuring-their-products-on-amazon-without-permission
- https://stellagent.ai/insights/amazon-ai-agent-rufus-buy-for-me
- https://www.paperclipped.de/en/blog/amazon-rufus-ai-shopping-agent ($12B, 60% conv)
- https://novadata.io/resources/news/amazon-rufus-agentic-auto-buy-250-million-users

**Others**
- https://corporate.walmart.com/news/2025/06/06/walmart-the-future-of-shopping-is-agentic-meet-sparky
- https://company.instacart.com/updates/bringing-inspirational-ai-powered-search-to-the-instacart-app-with-ask-instacart
- https://www.stocktitan.net/news/KLAR/klarna-launches-agentic-product-protocol...
- https://www.medianama.com/2026/01/223-flipkart-conversational-ai-commerce-slap
- https://m.economictimes.com/.../shopping-without-searching-the-agentic-ai-future-amazon-swiggy-and-zepto-are-building-in-india
- https://finance.yahoo.com/news/alibabas-accio-ai-search-engine-050000597.html

**Protocols / trust / identity**
- https://ap2-protocol.org
- https://www.mastercard.com/global/en/news-and-trends/stories/2026/verifiable-intent.html
- https://blog.cloudflare.com/secure-agentic-commerce (Visa TAP / Mastercard Agent Pay)
- https://solana.com/x402/what-is-x402
- https://eco.com/support/en/articles/12328618-x402-protocol-explained-how-ai-agents-pay-onchain
- https://docs.cloud.google.com/iam/docs/agent-identity-overview
- https://www.descope.com/blog/post/google-vertex-ai-identity
- https://www.andrewcoyle.com/blog/generative-engine-optimization-and-the-llms-txt-file
- https://schema.org/Product

**Business model / market**
- https://rye.com/blog/agentic-commerce-affiliate-commissions (2–4% take, x402, data-source attribution)
- https://www.bain.com/insights/2030-forecast-how-agentic-ai-will-reshape-us-retail-snap-chart
- https://www.paz.ai/agentic-commerce
- https://commercetools.com/blog/agentic-commerce-stats-enterprise-guide
- https://eseospace.com/blog/shopify-pricing-plans (Agentic plan $0)
