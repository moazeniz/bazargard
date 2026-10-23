# بازارگرد — evidence matrix for the Torob challenge

Each row links a judging criterion or brief requirement to the feature that answers it and to a proof that a judge can check. The proofs are the live app, a test in `tests/`, a benchmark section, a screenshot in `docs/evidence/`, or the red-team report.

- **Live app:** <https://bazargard-ai.liara.run> (Liara, Iran). Health: [`/api/health`](https://bazargard-ai.liara.run/api/health), [`/healthz`](https://bazargard-ai.liara.run/healthz), [`/api/ready`](https://bazargard-ai.liara.run/api/ready). Dataset numbers: [`/api/dataset-facts`](https://bazargard-ai.liara.run/api/dataset-facts).
- **Test suite:** `make test`, 377 tests. **Benchmark:** `make bench` and `make bench-e2e` (results in [`BENCHMARK.md`](BENCHMARK.md)).
- **Dataset:** [`DATASET.md`](DATASET.md). **Security:** [`SECURITY.md`](SECURITY.md) and [`../redteam/REPORT.md`](../redteam/REPORT.md).

## Product (75)

| What the judges look for | Feature in بازارگرد | Proof |
|---|---|---|
| A complete in-person shopping journey | Chat → result cards → comparison → shop page → plan with stops → seller follow-up → trip mode and offline pass | `docs/evidence/desktop-chat-answer.png`, `desktop-results.png`, `desktop-plan.png`; README «Ten trip features» |
| Finds suitable real products | Persian full-text catalogue search with category intent and accessory demotion; spelling correction; price-stratified first results | BENCHMARK §2: precision@5 95.8% (ranked), success@5 96.3%, 0% accessories on main-product queries; `tests/test_search_quality.py` |
| Respects hard requirements | Budget, district, radius, open at the visit time, in-person credit (channel, provider, price range, category) | BENCHMARK §2–3: 100% constraint satisfaction in retrieval and on agent cards; `tests/test_contracts.py`, `tests/test_price_policy.py` |
| Honest when the dataset has nothing | Says «در دیتاست ترب پیدا نشد», shows near alternatives separately, then searches shops in the city on the web (labelled leads) and suggests districts | BENCHMARK §3: «not in Torob» stated in 100% of expected cases and 0% wrong «not found»; `tests/test_web_alternatives.py`, `tests/test_comparison_areas.py` |
| Comparison | 2–12 products, harmonised spec rows quoted from the catalogue; missing basic specs completed from the web with source links; charts; export | `docs/evidence/desktop-comparison.png`; `tests/test_spec_harmonizer.py`, `tests/test_basic_specs.py`; compare-12 prices filled 12/12 (`ui_polish.py`) |
| Buyer reviews | Digikala buyer reviews (exact model match) and foreign owner reviews from verbatim citations; a summary that cites review IDs | Live product page (602 Digikala reviews were shown on Liara); `tests/test_user_reviews.py`, `tests/test_polish.py::test_digikala_*` |
| Seller follow-up | Prepared question; real Instagram/website links for the 112 real-name shops; WhatsApp/Telegram/SMS/share deep links; seller reply link with hold; per-stop follow-up tracker with reminder; clearly labelled simulation | `docs/evidence/desktop-seller-followup.png`, `desktop-plan.png`; `tests/test_polish.py::test_follow_up_*`, `tests/test_seller_scenarios.py` |
| Planning the visit | Stop order, opening at arrival, deadline, OSRM road time without live traffic, costs and installments, indoor floor/unit from the address | `tests/test_visit_windows.py`, `tests/test_features.py`; `docs/evidence/desktop-map.png` |

## UX (75)

| What the judges look for | Feature | Proof |
|---|---|---|
| Persian, RTL, mobile-first | One design system (tokens in `style.css`, dialogs in `dialogs.css`), bottom navigation on mobile, sheets for dialogs | `docs/evidence/mobile-*.png`; `tools/ui/ui_smoke.py` and `tools/ui/ui_polish.py` (Playwright, desktop and mobile): no page errors, no horizontal scroll |
| Fast feedback | Live tool steps («جست‌وجوی محصول و فروشنده…»), cards stream before the answer, progress bars for background jobs | Typical answer 10–13 s (BENCHMARK §3); README «Observability» |
| Answers match the cards | Citations [n] always point to visible cards; clicking a citation opens that offer even after the list changes | BENCHMARK §3: citations valid 100%; `tests/test_catalogue_first.py`, `tests/test_polish.py::test_final_answer_*` |
| Failure states are clear | «در صف تلاش مجدد» when the model is temporarily down, Persian error messages with request ID, retry on network errors | `tests/test_observability.py`; `tools/ui/ui_polish.py` checks the reviews panel survives a failed request |
| Accessibility | Focus-trapped dialogs, Escape to close, labelled controls, enlarged touch targets | README «Audit fixes»; Playwright checks |

## Intelligence (50)

| What the judges look for | Feature | Proof |
|---|---|---|
| Understands Persian needs | Extracts budget, district, credit, brand, visit time and recipient with exact quotes; rewrites misspellings and Finglish into catalogue words | BENCHMARK §3 rows «هدفن بلوتوث», «لبتاپ ایسوس», «goshi samsung»: correct cards |
| Tool use and multi-step reasoning | 30 tools (search rounds, seller joins, verification, comparison, timing, costs, basket); plan → search → verify within bounded rounds | `backend/agent.py`; `tests/test_research.py`; mean 2.4 model rounds per answer (BENCHMARK §3) |
| No made-up facts | Prices only from cards; quotes verified against pages; model-written web text labelled «خلاصه جست‌وجوی وب» | BENCHMARK §3: 0% hallucinated prices (41 mentions); `tests/test_spec_harmonizer.py`, `tests/test_review_evidence.py` |
| Robust to attacks | Prompt-injection guard, data fencing, output filter, tool budgets, abuse termination | `redteam/REPORT.md`: 106/106 checks pass (baseline without the guard: 50/86); `tests/test_guard*.py` |

## Deployment (40)

| What the judges look for | Feature | Proof |
|---|---|---|
| Live and reachable in Iran | Liara app `bazargard-ai` with persistent disk; catalogue downloaded and checksum-verified on first start | <https://bazargard-ai.liara.run>, `/api/ready` (catalogue, database, disk, provider and queue) |
| Model provider resilience | Claude through the Arvan LLM gate, with automatic fallback to BoxAPI (key rotation, model fallback chain) on 429/5xx/credit/gate errors | `/api/health` shows both providers; `tests/test_llm.py` (provider switch, gate refusal, rate limits) |
| Operable | JSON logs for every request, LLM call, tool call and external call; durable job queue with retries and dead letter; admin endpoints; graceful shutdown | README «Observability and resilience»; `tests/test_observability.py`; Liara logs |

## Technical (40)

| What the judges look for | Feature | Proof |
|---|---|---|
| Architecture | FastAPI + SQLite FTS5 catalogue + React/Vite; provider-neutral LLM layer; durable queue; per-upstream circuit breakers | README «Architecture»; `backend/llm.py`, `backend/jobqueue.py`, `backend/resilience.py` |
| Quality gates | 377 automated tests on the real catalogue; Playwright UI checks; reproducible benchmark | `make test`, `make bench` |
| Performance | Catalogue search p50 ≈ 0.1 s; spec data computed only for returned offers; prompt caching across tool rounds | BENCHMARK §2 latency table; `tests/test_polish.py::test_catalogue_search_projects_*`, `::test_anthropic_tool_loops_*` |
| Security and privacy | Session isolation on every endpoint, CSRF/CSP, SSRF-guarded fetches, secrets never logged | `tests/test_guard_api.py` (endpoint sweep), `docs/SECURITY.md` |

## Dataset (20)

| What the judges look for | Feature | Proof |
|---|---|---|
| Uses the whole dataset | 225,903 products, 331,506 offers and 6,476 shops, including hours, coordinates, payment options, warranty and specs | `docs/DATASET.md`; in-app «درباره داده‌ها» (`docs/evidence/desktop-about-data.png`) |
| Honest about its limits | Synthetic names and phones are labelled on every card («نام ساختگی» / «نام واقعی»); prices show their age; stock is «نامشخص» without a report; implausible prices are flagged | BENCHMARK §4: data fidelity 100% on all shown cards; `docs/DATASET.md` «Known data issues» |
| Data quality findings | 1,645 offers under 1,000 toman, 211 over 10 billion; 2,029 shops without hours; model field often a slug | `docs/DATASET.md`; offer inspection flag `price_data` |

## Brief requirements

| Requirement | Where | Proof |
|---|---|---|
| Discover needs in Persian chat | Chat with quick/advanced modes, one useful follow-up question | `tests/test_locations_modes.py`, `docs/evidence/desktop-chat-answer.png` |
| Suitable products with explicit offers | Catalogue search with hard filters | BENCHMARK §2 |
| Compare price, distance, hours and credit | Cards, comparison, plan | `docs/evidence/desktop-results.png`, `desktop-comparison.png` |
| Contact preparation and reaction to failure | Follow-up kit, seller reply link, user-reported replies, alternatives after «closed/no answer» | `tests/test_seller_scenarios.py`, `tests/test_polish.py` |
| Visit planning | Plan page, timing, map, offline trip pass | `docs/evidence/desktop-plan.png`, `desktop-map.png` |
| Memory | Trip and permanent preferences, deletable | `tests/test_contracts.py` |
| Prices sellers do not update («تازه‌سازی قیمت») | One-tap signed seller link (no login) → «قیمت تأییدشده امروز» in search, plan and tracker; refresh queue (age × demand × uncertainty × visit); shopper reports with agreement; shop-website price check with verification; «برآورد امروز» range with confidence | `docs/PRICE_FRESHNESS.md`, `tests/test_freshness.py`, `tools/ui/ui_fresh.py` |
| No fabricated data | Quote-verified web facts, labelled simulations, synthetic-identity labels | BENCHMARK §3–4, `docs/DATASET.md` |
