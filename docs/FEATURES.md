# Features and behaviour

What Bazargard does, how, and where it deliberately stops. The guiding rule: every claim is tied to the catalogue, a user-reported seller reply, a labelled simulation or a cited public source; anything else is shown as unknown.

## 1. Shopping conversation

- Persian chat with Quick and Advanced modes ([AGENT_REASONING.md](AGENT_REASONING.md)).
- Preferences: budget (min/max), neighbourhood and radius, purpose, brand, in-person instalment credit and provider, travel mode, visit time, recipient. Each saved preference quotes the user's own words; corrections replace old values; trip vs. permanent scope; JSON export and deletion of memory.
- Hard filters (budget, radius/neighbourhood, credit, brand, hours) are applied in the catalogue layer to every search, cached result and recommendation.
- In-person credit is checked against payment channel, provider, category and price eligibility; unknown or online-only credit never counts as in-person eligibility.
- Chat runs are persisted and recoverable after reloads or dropped connections.
- Photo-to-catalogue search: an image is resized in the browser, the model proposes a model name, the shopper confirms/edits it before retrieval. Images never verify authenticity.

## 2. Catalogue retrieval and comparison

- All products, offers and shops of the Torob challenge dataset in SQLite with Persian-normalised FTS5 search and category-intent guards (phone vs. phone battery, laptop vs. adapter).
- Price-diverse initial discovery (lower / middle / upper offers) while preserving explicit model intents.
- Comparison of 2–12 distinct products: normalised specs (RAM/SSD/HDD, weight, display size/refresh, battery energy), differences-only and text filters, removable columns, source/conflict labels, zero-baseline charts. CPU names are never turned into benchmark scores; battery energy is not runtime. Prices are the lowest eligible displayed offer per product, not a market claim.
- Exports: standalone RTL HTML with charts, and UTF-8 CSV with spreadsheet-formula protection.
- Chat comparisons and investigations are immutable reply snapshots that reopen after reloads or later searches.

## 3. Specification completion and buying reviews

- A versioned typed projection of product specs (`product_data.py`, `spec_schema.py`) with provenance, unit/digit normalisation and conflict tracking; seller-listing upgrades stay per-offer claims.
- "تکمیل مشخصات از وب" fills **missing cells only** from bounded public sources (manufacturer first, then recognised spec references and retailers): ≤ 6 documents in 3 rounds within 65 s per model, PDF reading bounded to 3 MB / 12 pages. Title identity checks prevent sidebar mentions proving a wrong model.
- Buying-review cards («بررسی و نظرها») separate recorded facts, grounded assistant strengths/weaknesses/suitability, specialist excerpts, manufacturer statements, community text and aggregate rating metadata, plus focused seller questions. Up to 5 investigations per turn; family matches stay "configuration unverified".
- Public research: free discovery with fallbacks, ≤ 3 documents / 3 MB each / 22 s per investigation, ≤ 3 vetted redirects, TLS validation, public-address DNS checks with pinned connections. Blocks are not bypassed; only short excerpts are kept; the shared cache contains no private shopper context.

## 4. Trip planning

- Offer inspection: seller wording for used/non-original/wholesale/single-piece, title-capacity differences, missing warranty/stock → review prompts (not authenticity guarantees). Used/refurbished wording triggers condition-specific inspection questions.
- Opening schedules in Asia/Tehran including split and overnight shifts; the whole shopping interval is checked against recorded and reported windows.
- Visit plans: up to four stops ordered by nearest-neighbour, exact address/floor/unit text, Google Maps search/directions links (no API key). Road estimates came from OSRM through the retired egress relay (no live traffic); without it the planner uses user-entered per-leg times and says so.
- Deadline planning: departure, return deadline, time per stop, buffer and accessibility needs; quote/stock/hold evidence that expires before purchase triggers reconfirmation.
- Joint basket: up to 6 items and 4 stops, bounded candidate search on price and number of shops under the total budget; connector/compatibility evidence is compared and unresolved compatibility stays explicit.
- Cost and instalment calculator: item prices, user-entered travel/parking cost, down payment, instalments, fees and total repayment.
- Address and entrance context: catalogue address excerpts, seller-supplied entrance/floor/landmark/accessibility details and (where the place-context service is available — in 3.3.0 the retired relay) nearby OpenStreetMap objects, kept separate; geometry is never treated as proof of shop identity. Missing context is shown as unavailable.
- Street/offline mode: arrived/closed/unavailable/price-changed/bought controls, queued offline reports and a self-contained offline HTML trip pass.

## 5. Sellers

- Inquiry drafts for one product/shop (stock, actual price, warranty, hours, indoor address, credit). **Nothing is sent automatically.**
- Seller reply and hold links («تأیید و نگهداری»): expiring, one-use, atomic; the seller can declare variant, price, stock, readiness, opening interval and hold deadline/code. Link possession does not authenticate seller identity.
- Manual reply reports are stored as `user_reported` and expire after 24 h (or an earlier merchant-stated expiry); the latest unexpired reported price governs budget filters, ranking and credit eligibility.
- Seller scenario simulation («آزمایش سناریو»): stock, no answer, closure, price rise, variant mismatch, credit refusal — labelled `simulated`, expire after one hour, reapply all eligibility checks, remove failed visits and propose alternatives (same product first, then labelled similar products). Reset deletes simulated events only.

## 6. When the catalogue has nothing

- **Shopping areas:** sampled shop category/address clusters plus a small checked public-source list, shown on a map with approximate pins, source URLs and dates. Never exact-model availability.
- **Tehran merchant discovery** («سرنخ حضوری بیرون دیتاست»): eligible dataset offers first; then free public merchant pages for the exact model with Tehran/in-person evidence (≤ 3 documents within 22 s). Results are unverified leads, not route stops.
- **Web alternatives:** when no catalogue product satisfies the request, a background web search extracts candidate listings with literal quotes; still labelled leads.

## 7. Ops workspace (Liara challenge companion)

Open `<BASE>/?workspace=ops`.

- Inspects this project (frontend package, catalogue size, Dockerfile/`liara.json` presence) and proposes a starting resource configuration with explicit qualifications.
- Isolated, per-session sample web processes (fixed program, loopback, stripped environment, ≤ 4 active, reclaimed after 30 min idle) for a deploy → diagnose → approved fix → verify flow: real HTTP 500 from a missing config, slow responses, failed releases with automatic rollback.
- Evidence: HTTP status/latency, process RSS and sampled CPU, redacted logs; the model explains cause, evidence, proposed action and verification in Persian.
- Every action is a proposal with an expiring nonce, state precondition and one-use atomic execution after explicit approval; rejections and results are audited.
- `backend/ops.py:Liara` is a Liara PaaS API adapter (projects, logs, metrics, restart, envs, resize, sources, releases, domains) with redaction. Mutations require `LIARA_ALLOW_MUTATIONS=true` **and** an approved action; there is no public endpoint that executes Liara mutations.

## 8. Accessibility and UI

Semantic controls, keyboard focus trapping in dialogs with Escape and focus restore, enlarged touch targets, responsive chat/results/map layouts, local Vazirmatn fonts, visible tool-progress labels, and lazy-loaded feature bundles.

## 9. Honest limits

- The dataset covers roughly 10 % of Tehran shops (selected areas, ≤ 300 products per shop). No result is not proof of market-wide unavailability.
- Catalogue prices are recorded prices with a last-change timestamp, not live prices; an offer proves a listing, not current stock.
- Distances in search are straight-line; road estimates (when available) have no live traffic.
- Geolocation needs HTTPS or localhost; on plain HTTP the shopper picks an area or a point on the map.
- Real seller calling/messaging, Google Places enrichment and payments are not implemented.
