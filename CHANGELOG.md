# Changelog

All notable changes to Bazargard. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow the release string reported by `/api/health`.

## [Unreleased]

## [4.4.1] - 2026-10-23

### Changed
- The developer console is an on/off toggle in the browser (no login), with rail and top-bar entries. It is still gated server-wide by `DEV_CONSOLE=1`.

## [4.4.0] - 2026-10-23

### Added
- Developer console (internal testing; `DEV_CONSOLE=1`). Per chat run it shows model rounds (provider, model, latency, tokens, cache use, cost, fallbacks), every tool call, guard decisions, queue and retry events, and a time breakdown, with JSON export. Traces are redacted, stored in `memory.db` for `DEV_TRACE_HOURS` and scoped to the browser's own chats (`backend/devtrace.py`, `frontend/src/devconsole.jsx`, `tests/test_devconsole.py`).

## [4.3.0] - 2026-10-23

### Added
- Benchmark on the Torob dataset: 61 labelled Persian requests in 10 groups, a retrieval benchmark without a model and an end-to-end agent benchmark (`bench/`, `make bench`, `docs/BENCHMARK.md`).
- Dataset facts computed from the catalogue (`backend/dataset_facts.py`, `/api/dataset-facts`, the «درباره داده‌ها» page, `docs/DATASET.md`).
- Evidence matrix for the judging criteria with desktop and mobile screenshots (`docs/EVIDENCE.md`, `docs/evidence/`).
- UI check scripts (`tools/ui/`).

### Changed
- Search quality improvements, covered by `tests/test_search_quality.py`.
- Chat messages can be up to 3000 characters.

## [4.2.0] - 2026-10-22

Live on Liara (`bazargard-ai`).

### Added
- Seller follow-up and tracker (`frontend/src/followup.jsx`), plus shared dialog styles (`dialogs.css`).
- Anthropic prompt caching for agent turns (`LLM_PROMPT_CACHE`, on by default).

### Changed
- No rate limits on new users and new chats, and much higher per-IP limits on search and research, so shared
  networks are not blocked. Tool budgets per chat and one active run per chat still bound spend.
- Faster chat turns: typed spec projections are computed only for returned offers or when spec filters are used.

### Fixed
- Digikala buyer reviews: matching by model code.
- Comparison of up to 12 products.
- Observability: token counts (`tokens_*`, `*_tokens`) are no longer redacted as secrets.
- Flaky tests are pinned.

## [4.1.0] - 2026-10-22

Live on Liara (`bazargard-ai`).

### Added
- **Switchable LLM provider** (`backend/llm.py`): `LLM_PROVIDER=anthropic|boxapi`, with automatic fallback through `LLM_FALLBACK_PROVIDER`. The Anthropic path goes through the Arvan LLM gate and the relay (`RELAY_URL`, `RELAY_TOKEN`; the Anthropic key stays on the relay). It uses `claude-sonnet-5-5` / `claude-haiku-5-5`, mapped from `gpt-6.1-sol` / `gpt-6-luna`.
- **Observability** (`backend/obs.py`): structured JSON logs with request, trace and span IDs, `X-Request-ID` / `traceparent` propagation, redaction of secrets and PII, and a global JSON error handler with a Persian message. `/readyz` is an alias of `/api/ready`, which now also reports providers, queue and circuits.
- **Resilience** (`backend/resilience.py`): one retry policy (exponential backoff with full jitter, `Retry-After`) and circuit breakers per upstream and per web host.
- **Durable job queue** (`backend/jobqueue.py`): spec, review, web-alternative and `chat_retry` jobs with idempotency keys, retry, dead letter and crash recovery. Admin endpoints `/api/admin/queue` and `/api/admin/jobs[...]` are enabled by `ADMIN_TOKEN`.
- Platform kit: CI (tests, image smoke, gitleaks, audits, SBOM, Trivy), a tag-driven Liara deploy with automatic rollback, and IaC in `infra/liara.app.yaml`.

### Changed
- The release string moves to `backend/obs.py` (`RELEASE`).

## [4.0.0] - 2026-10-22

### Added
- **Prompt-injection and abuse hardening** (`backend/guard.py`, [docs/SECURITY.md](docs/SECURITY.md)) covering 52 attack classes in 12 groups:
  - Unicode normalisation that keeps Persian text intact, and multi-view detection (base64/hex/rot13/leetspeak/reversed/spaced).
  - An abuse score per conversation that warns and then closes the conversation (HTTP 423).
  - Fencing of untrusted data, and visible-text-only HTML/PDF extraction.
  - An output filter against prompt, tool or key leaks, plus tool budgets and CSP/CSRF headers.
- **Red team** (`redteam/`): 106 checks against staging with a real model. The hardened build passes 106/106; the baseline without the guard passes 50 of the 86 model-dependent checks. False positives on 77 real shopper messages: 0.

### Changed
- All model calls moved to BoxAPI (OpenAI-compatible Responses API, `openai/gpt-6.1-sol`) with multi-key rotation (`BOXAPI_BASE_URL`, `BOXAPI_KEYS`, `LLM_MODEL`).
- OSRM and OpenStreetMap context are requested directly. The web gateway is no longer needed.
- Repository restructured for publication: `scripts/`, `docs/`, CI and pinned requirements. Liara became the production platform (`scripts/liara_release.py`, `make liara-*`). The Dockerfile tolerates a missing `liara.json`.

### Removed
- Web gateway (see `docs/legacy/RELAY_AND_GATEWAY.md`). The Arvan VM path remains only as an optional self-hosted alternative in `deploy/self-hosted/`.

## [3.3.0] - 2026-10-22

Summary of the 3.x line (3.0.0–3.3.0).

### Added
- Product rebrand to **بازارگرد (Bazargard)** (formerly "Hamrah").
- Web alternatives: background web search for listings when the catalogue has no product satisfying the request.
- Product pages with on-demand specification completion and user-review collection jobs.
- Liara deployment (`bazargard-ai`): Docker image with a persistent `/data` disk and first-boot catalogue seeding from `CATALOG_URL` (bearer token + SHA-256 check).

## [2.5.0] - 2026-10-22

### Added
- Shared typed product-fact projection (`product_data.py`) and category schemas (`spec_schema.py`) used by retrieval, filters, comparisons and reviews.
- Missing-only, source-attributed specification completion for comparison tables, charts and exports (`spec_fetcher.py`, bounded PDF reading).
- Comparison rebuild from immutable historical snapshots; offline derived index builder (`build_product_index.py`).

## [2.4.1] - 2026-10-21

### Added
- Catalogue-first retention of eligible offers, bounded free public research and Persian buying-review cards.
- Researched comparisons as immutable snapshots; labelled seller failure scenarios (stock, no answer, closure, price rise, variant mismatch, credit refusal).
- Whole visit-window checks with evidence expiry, condition-aware inspection questions, Tehran external merchant discovery.

### Fixed
- Leaflet resize/teardown race in modal maps.

## [2.3.0] - 2026-10-21

### Added
- Comparison of 2–12 products with normalised specs, charts, HTML/CSV export and chat snapshots.
- Sourced shopping-area suggestions on a map when the catalogue has no result.

## [2.2.0]

### Added
- Quick / Advanced chat modes with progressive clarification.
- Free address and entrance context from OpenStreetMap with cached, rate-limited lookups.

## [2.0.0]

### Added
- Ten trip features: seller reply/hold links, deadline planning, offer inspection, joint basket planning, cost and instalment calculator, indoor arrival details, street/offline mode with trip pass, single specification question, photo-to-catalogue search, trip vs. permanent memory.
- Multi-hop Advanced investigation and recoverable persisted chat runs.
- Ops workspace (Liara challenge companion) with isolated lab deployments and approval-gated actions.

## [1.0.0]

### Added
- Persian RTL shopping assistant: source-backed memory, full catalogue in SQLite/FTS5, hard budget/location/credit/hour filters, comparisons, opening schedules, maps and routes, seller inquiry drafts and simulations.
