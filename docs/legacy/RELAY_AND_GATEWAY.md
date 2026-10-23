# Legacy: egress relay and web gateway (retired)

**Status: retired.** Production runs on Liara and calls BoxAPI directly. The relay and gateway code is no longer in this repository; this note records what they did so that the remaining legacy variables in release 3.3.0 make sense.

## What they were

Both ran as small loopback-only services on a host outside Iran, because some foreign services refused or could not be reached from Iranian addresses at the time.

| Service | Purpose |
|---|---|
| **Egress relay** (FastAPI, bearer-token auth) | Proxied the Anthropic Messages API (`/v1/messages`, `/v1/models`) for chat and analysis; OSRM road estimates (`/routing`); cached OpenStreetMap place context (`/place-context`); an authenticated door to the web gateway (`/gw/fetch`); a token-protected catalogue seed download (`/liara-data/catalog.db.gz`). |
| **Web gateway** | Fetched public HTTPS pages for spec completion, reviews and merchant discovery from a non-Iranian address, reusing the app's SSRF-guarded `public_research.fetch()`. |

On the old VM deployment the app reached them through a reverse SSH tunnel on loopback ports 9894 (relay) and 9893 (gateway).

## Why they were retired

- The LLM moved to **BoxAPI**, which is reachable directly from Iran (OpenAI-compatible `chat/completions` / `responses`, multi-key rotation). There is nothing left to proxy.
- An extra host, its secrets and a tunnel were operational burden and an additional attack surface.
- The catalogue seed is served from a domestic host (`scripts/publish-catalog-seed.sh`), which is faster for Liara's Iranian runtime.

## Remaining legacy variables (release 3.3.0)

| Variable | Effect today | After the BoxAPI migration |
|---|---|---|
| `RELAY_URL`, `RELAY_TOKEN` | Chat via the relay; OSRM routing and OSM place context | Removed. Without them, road timing falls back to user-entered per-leg times and place context shows "unavailable". |
| `ANTHROPIC_MODEL` | Model name sent through the relay | Replaced by `LLM_MODEL`. |
| `WEB_GATEWAY_URL`, `WEB_GATEWAY_TOKEN` | Public page fetches via the gateway | Removed. Set `WEB_GATEWAY_URL=` (empty) to fetch directly; unset means a loopback default that no longer exists. |
| `CATALOG_TOKEN` fallback to `RELAY_TOKEN` | Seed download auth | Set `CATALOG_TOKEN` explicitly. |

In 3.3.0 these variables are read by `agent.py`, `features.py`, `locations.py`, `review_analysis.py`, `spec_harmonizer.py`, `user_reviews.py`, `web_alternatives.py`, `ops.py` and `server.py` (health check); the BoxAPI migration removes those reads. The relay and gateway source (`deploy/egress-relay/`, `deploy/web-gateway/`) remain in this repository's history at commit `875c728` if a non-Iranian egress is ever needed again.
