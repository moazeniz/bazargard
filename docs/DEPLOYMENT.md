# Deployment guide

Production runs on **Liara** as the Docker app **`bazargard-ai`** with a persistent disk mounted at `/data`. The LLM is **BoxAPI**, called directly from the container. Commands below are copy-pasteable; replace `<PLACEHOLDERS>`.

| # | Section | When |
|---|---|---|
| 0 | [Prerequisites: catalogue and tools](#0-prerequisites) | once |
| 1 | [Liara (production)](#1-liara-production) | every release |
| 2 | [Docker (local or any host)](#2-docker-local-or-any-host) | local prod-like testing |
| 3 | [Health checks and smoke test](#3-health-checks-and-smoke-test) | after every change |
| 4 | [Troubleshooting](#4-troubleshooting) | when something breaks |
| 5 | [Optional: self-hosted VM (legacy)](#5-optional-self-hosted-vm-legacy) | only if Liara is not an option |

Common facts:

- One process (uvicorn, **one worker**) serves `/api/*` and the built SPA. Never run two instances against the same `/data`.
- State lives on the disk (`catalog.db`, `memory.db`, `public-research.db`, optional `canonical-products.db`); see [ARCHITECTURE.md §3](ARCHITECTURE.md#3-data).
- `GET /api/health` → liveness and `release`; `GET /api/ready` → 200 only when the catalogue is loaded (503 otherwise).
- Secrets (BoxAPI keys, catalogue token, Liara token) live only in Liara's environment variables or in your shell environment. Never commit them or bake them into images.

---

## 0. Prerequisites

**Tools:** Python 3.12+, Node 22 (for local builds), and either the Liara CLI (`npm i -g @liara/cli && liara login`) or a Liara API token.

**Liara API access for scripts** (`scripts/liara_release.py`, `make liara-*`) — export in the shell, never write to the repo:

```bash
export LIARA_API_TOKEN=<token>          # Liara console → API
export LIARA_TEAM_ID=<team-id>
export LIARA_API_BASE=https://api.iran.liara.ir   # default
```

**Catalogue:** the Torob dataset is not in the repository. Build `catalog.db` on a trusted machine ([DATA.md](DATA.md)):

```bash
make install
make catalog DATASET_DIR=/secure/torob-dataset    # data/catalog.db, ~430 MB
```

---

## 1. Liara (production)

| Setting | Value |
|---|---|
| App | `bazargard-ai`, platform `docker`, port `8080` (`liara.json`) |
| Plan | `standard-base-g2` + bundle `basic` (g2 plans require the team's private network) |
| Disk | `data` → `/data` (≥ 2 GB; catalogue ~430 MB + `memory.db` + caches) |
| Build | root `Dockerfile` (Node 22 builds the SPA, Python 3.12 slim runtime, non-root uid 10001), build timeout 5 min |
| URL | `https://bazargard-ai.liara.run/` |

### 1.1 First-time setup

1. **Create the app and the disk** in the Liara console (or via API: `POST /v1/projects {name, planID, platform:"docker", bundlePlanID, network}` then `POST /v1/projects/bazargard-ai/disks {"name":"data","size":2}`). The disk must exist before the first release, otherwise the release fails with `disk_not_found`.

2. **Publish the catalogue seed** on a host that Liara's Iranian runtime can download from quickly (a domestic server with nginx):
   ```bash
   # on the seed host, from a checkout of this repo
   sudo CATALOG_DB=/path/to/catalog.db PUBLIC_HOST=<SEED_HOST> scripts/publish-catalog-seed.sh
   # once: include deploy/catalog-seed/nginx-location.conf in a server block, then
   sudo nginx -t && sudo systemctl reload nginx
   sudo cat /root/catalog-seed.env     # CATALOG_URL / CATALOG_TOKEN / CATALOG_SHA256 for step 3
   ```
   Any HTTPS/HTTP endpoint that serves `catalog.db.gz` behind `Authorization: Bearer <token>` works.

3. **Set the environment variables** (console → app → Environment variables, or `liara env:set --app bazargard-ai K=V ...`):

   | Variable | Value |
   |---|---|
   | `PUBLIC_BASE_URL` | `https://bazargard-ai.liara.run/` |
   | `BOXAPI_BASE_URL` | `https://ai.boxapi.ir/ai/v1` |
   | `BOXAPI_KEYS` | `<key1>,<key2>,<key3>` |
   | `LLM_MODEL` | `openai/gpt-6.1-sol` |
   | `CATALOG_URL`, `CATALOG_TOKEN`, `CATALOG_SHA256` | from step 2 |
   | `WEB_GATEWAY_URL` | *(empty)* — release 3.3.0 only; makes public research fetch directly |

   Every env change restarts the app and creates an `ENV_UPDATE` release (which can be rolled back). `POST /v1/projects/update-envs` replaces the **whole** list — always send every variable. Do not put `envs` in `liara.json` or release bodies for the same reason.

4. **Deploy** — either way works:
   ```bash
   make liara-deploy      # REST API: package (.liaraignore) → POST /v2/projects/bazargard-ai/sources
                          # → POST /v2/projects/bazargard-ai/releases {sourceID, type, port, disks} → poll until READY
   make deploy-liara      # official CLI: liara deploy --app bazargard-ai --port 8080 --detach (reads liara.json)
   ```
   The app must be running (scale 1) for the CLI to deploy. `make liara-package` alone builds `dist/bazargard-source.tar.gz` if you want to inspect or archive the upload.

5. **First boot:** `backend/start.py` finds no `/data/catalog.db`, answers `503 {"status":"warming_up"}` on port 8080, downloads the gzip with the bearer token, verifies `CATALOG_SHA256`, unpacks it atomically, then starts the app. Later boots skip this.

6. **Verify** ([§3](#3-health-checks-and-smoke-test)): `make liara-status`, then `curl -fsS https://bazargard-ai.liara.run/api/ready`.

### 1.2 Routine release

```bash
make lint test                                  # plus: make test-full TEST_CATALOG=/abs/catalog.db
# bump 'release' in backend/server.py and add a CHANGELOG entry
make liara-deploy                               # waits for READY; exits non-zero on FAILED/UNHEALTHY/TIMEDOUT
make liara-status && curl -fsS https://bazargard-ai.liara.run/api/health
```

The `/data` disk survives releases. Builds that exceed the bundle's build timeout end as `TIMEDOUT`; rerun (the build cache usually makes the second attempt faster).

### 1.3 Rollback

Liara keeps release history; `POST /v1/releases/{releaseID}/rollback` switches back to an earlier release.

```bash
make liara-releases                       # * marks the current release
make liara-rollback                       # newest READY DEPLOY/ENV_UPDATE release that is not current
make liara-rollback RELEASE_ID=<id>       # a specific one
```

| Response | Meaning |
|---|---|
| 409 | That release is already current |
| 404 | Cannot roll back to this release |
| 428 `rollback_not_supported` | Not available for this app/bundle — use the fallback below |
| 403 `no_permission` | Token lacks team permission |

Fallback: check out the previous tag and redeploy its source (`git checkout <tag> && make liara-deploy`). Rollback does not touch the disk: if a release changed `memory.db` incompatibly, also restore a disk backup (§1.6).

### 1.4 Logs and metrics

```bash
make liara-logs                                       # last 30 min (scripts/liara_release.py logs --minutes N --search TEXT)
make liara-status                                     # state, plan, CPU, memory, disk usage
liara app:logs --app bazargard-ai -f                  # CLI follow mode
```

API: `GET /v2/projects/bazargard-ai/logs?start=<unix-s>` (no streaming; poll), `GET /v1/projects/bazargard-ai/metrics/summary`, `GET /v1/projects/bazargard-ai/metrics/{cpu|memory}?since=<unix-s>`. Log retention depends on the bundle.

### 1.5 Restart, stop, resize

```bash
python3 scripts/liara_release.py restart              # POST /v1/projects/bazargard-ai/actions/restart
python3 scripts/liara_release.py stop                 # scale 0 — stops hourly billing of the plan
python3 scripts/liara_release.py start                # scale 1
```

Resize with `POST /v1/projects/bazargard-ai/resize {"planID": "<plan>"}` (≈ 20 s of 502s; the new plan must fit the disk). There is no horizontal scaling — this app is single-replica by design anyway.

### 1.6 Backups and restore

- **Disk backups** (when the bundle includes them): `POST /v1/projects/bazargard-ai/disks/data/backups` creates one, `GET` lists them, `POST .../backups/<name>/download` returns a link. Restore: `POST /v2/projects/bazargard-ai/disks/data/restore {"backupName": "...", "targetDiskName": "data", "targetProjectID": "<project _id>"}`.
- **Consistent SQLite snapshot** from inside the container (works on any bundle), then download it via the disk file browser or FTP:
  ```bash
  liara app:shell --app bazargard-ai -c "python3 -c \"import sqlite3;s=sqlite3.connect('/data/memory.db');d=sqlite3.connect('/data/memory-backup.db');s.backup(d);d.close()\""
  ```
- Only `memory.db` is irreplaceable; the catalogue can always be re-seeded.

### 1.7 Cost hygiene

Plans are billed per hour from a shared team balance. Stop the app when not demoing (`python3 scripts/liara_release.py stop`), keep one disk, and avoid repeated failed builds.

---

## 2. Docker (local or any host)

```bash
make docker-build                                 # bazargard:<release> and :latest
mkdir -p data && cp /secure/catalog.db data/      # or set CATALOG_URL/TOKEN/SHA256 in .env
cp .env.example .env && $EDITOR .env              # gitignored
make docker-run                                   # http://127.0.0.1:8080, ./data → /data
```

The image runs as uid 10001 and `chown`s `/data` at start; if the host directory is not writable, `sudo chown -R 10001:10001 data`. The image has a Docker `HEALTHCHECK` on `/api/ready` and no `VOLUME` instruction (Liara mounts disks itself).

Without Docker: `make install install-frontend frontend-build run` serves on `http://127.0.0.1:8506`.

---

## 3. Health checks and smoke test

```bash
BASE=https://bazargard-ai.liara.run
curl -fsS $BASE/api/health                                   # {"status":"ok","release":"3.3.0",...}
curl -s -o /dev/null -w 'ready %{http_code}\n' $BASE/api/ready   # 200
curl -s -o /dev/null -w 'spa %{http_code}\n'   $BASE/            # 200
```

Use `/api/ready` for readiness: `/api/health.catalog_ready` only checks that the file exists. Then in a browser: search a product, open a comparison of two products, send one chat message (checks BoxAPI), open `?workspace=ops`.

---

## 4. Troubleshooting

| Symptom | Likely cause | Check / fix |
|---|---|---|
| Release `FAILED` with `disk_not_found data` | Disk not created | Create disk `data` on the app, redeploy. |
| Release `TIMEDOUT` | Build exceeded the bundle limit | Redeploy (cached layers); check that `.liaraignore` excludes `node_modules`, `data`, `*.db`. |
| Release `UNHEALTHY` | Container started but failed its health check | `make liara-logs`; usually a crash at import (bad env, missing dependency). Roll back. |
| Container stays `warming_up` (503) | Seed download slow/blocked | Logs show `catalogue: downloading`; serve the seed from a domestic host; test with `curl -I -H "Authorization: Bearer <token>" <CATALOG_URL>`. |
| Boot fails: `catalogue checksum mismatch` | `CATALOG_SHA256` stale | Recompute `sha256sum catalog.db.gz` after re-publishing; update the variable. |
| `/api/ready` 503 though `CATALOG_URL` is set | An earlier boot without `CATALOG_URL` left an **empty** `catalog.db` (opening the catalogue creates the file), so seeding is skipped | Delete the tiny `/data/catalog.db` (`liara app:shell`) and restart. |
| Chat says the model is not configured | LLM variables missing | With `LLM_PROVIDER=anthropic`, set `RELAY_URL` (LLM gate) and `RELAY_TOKEN`. For BoxAPI or the fallback, set `BOXAPI_BASE_URL` and `BOXAPI_KEYS`. `/api/health` → `providers` shows which side is missing. |
| Chat errors / 429 / slow | Provider limit or exhausted key | Logs show provider status (keys redacted); add or rotate keys in `BOXAPI_KEYS`. At most 4 concurrent runs by design. |
| Public research always "unavailable" | 3.3.0 tries the retired gateway first | Set `WEB_GATEWAY_URL` to empty. Some sites block or are slow; results stay explicit. |
| Seller/hold links point to the wrong host | `PUBLIC_BASE_URL` unset | Set it to `https://bazargard-ai.liara.run/`. |
| `database is locked` | Two instances or a manual write | Exactly one instance; back up with `sqlite3` backup API, not file copies, while running. |
| `PermissionError` on `/data` (local Docker) | Host directory not writable by uid 10001 | `sudo chown -R 10001:10001 data`. |
| Deploy from CLI refused: "app isn't running" | App scaled to 0 | `scripts/liara_release.py start`, then deploy. |

---

## 5. Optional: self-hosted VM (legacy)

> The Arvan VM deployment used during the hackathon is **frozen** and is no longer the production path. These templates remain for anyone who must self-host on Ubuntu.

Files in `deploy/self-hosted/`: `install.sh` (renders `bazargard.service`, inserts `nginx-location.conf` as `location /hamrah/` into an existing nginx server block with a backup, starts the service), `nginx-site.conf.example` (dedicated domain), `deploy-vm.sh` (build SPA locally → back up code and `memory.db` on the VM → rsync → pip install → restart → health check).

```bash
# on the VM (Ubuntu, python3-venv, nginx), in the app directory with backend/, frontend/dist/, requirements.txt
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
mkdir -p data && cp /secure/catalog.db data/
install -m 600 /dev/null ~/.bazargard.env && $EDITOR ~/.bazargard.env     # from .env.example; DATA_DIR=<app>/data
sudo APP_DIR=$PWD APP_USER=$USER ENV_FILE=$HOME/.bazargard.env NGINX_SITE=/etc/nginx/sites-enabled/default \
     SERVICE=bazargard deploy/self-hosted/install.sh
# later releases, from a workstation:
make deploy-vm HOST=<user>@<VM_HOST>        # env: APP_DIR, SERVICE, PORT, PIP_ARGS (e.g. offline wheelhouse)
```

The unit listens on `127.0.0.1:8506` with one worker; logs via `journalctl -u <service>`; backups with `scripts/backup-data.sh <DATA_DIR> <backup-dir>`; rollback by extracting the `code.tgz` that `deploy-vm.sh` leaves in `~/backups/bazargard-<timestamp>/` and restarting. Browser geolocation needs HTTPS (`certbot --nginx`).

<!-- platform:begin (maintained by the platform kit; edit infra/ and .github/ rather than this block) -->
## Release pipeline, infrastructure as code and health checks

The platform overview is in the team platform document (`negahban` repo, `docs/PLATFORM.md`). It
covers the architecture, SLOs, backups and why the setup is built this way. This section is the
Bazargard-specific part.

### Pipeline: GitHub Actions to Liara

| Workflow | Trigger | What it does |
|---|---|---|
| `CI` (`.github/workflows/ci.yml`) | every push and PR; reused by Deploy | backend lint + pytest, Vite frontend build, platform-kit tests + actionlint, gitleaks over the full history, pip-audit + npm audit (fails on critical), image build + container smoke + non-root check + SBOM (SPDX/CycloneDX) + Trivy, and on `main` a read-only Liara dry run + drift plan |
| `Deploy to Liara` (`.github/workflows/deploy.yml`) | tag `v*`, or manual (defaults to dry run) | CI gate, then `infra/liara_deploy.py`: package the tagged commit, upload, release with `gitInfo` + tag message, watch the build and health check, smoke tests, **automatic rollback** to the previous release on failure, job summary |

**Status: ready, not enabled.** No deploy has been run from GitHub yet. To release:

```bash
git tag vX.Y.Z && git push origin vX.Y.Z          # or Actions -> "Deploy to Liara" -> dry_run=false
```

**Same pipeline from a workstation** (needs `LIARA_API_TOKEN` and `LIARA_TEAM_ID` in the environment):

```bash
pip install pyyaml
python3 infra/liara_deploy.py deploy   --manifest infra/liara.app.yaml --dry-run            # nothing deployed
python3 infra/liara_deploy.py deploy   --manifest infra/liara.app.yaml --message vX.Y.Z
python3 infra/liara_deploy.py status   --manifest infra/liara.app.yaml                      # history, * = current
python3 infra/liara_deploy.py rollback --manifest infra/liara.app.yaml [--to <releaseId>]
python3 infra/liara_deploy.py smoke    --manifest infra/liara.app.yaml
```

**Exit codes** of `deploy`:

| Code | Meaning |
|---|---|
| 0 | Deployed |
| 1 | Failed before traffic moved |
| 2 | Smoke tests failed, rolled back automatically |
| 3 | Rollback failed: follow runbook R1 |
| 4 | Refused by preflight (stopped app, missing env name, daily quota, size) |

Release bodies never contain `envs`, because on Liara they would replace every variable. Values stay in Liara.

### Infrastructure as code

`infra/liara.app.yaml` declares the app's desired state:

- plan, bundle, network
- zero-downtime, read-only root filesystem
- disks and their backup policy
- required and optional env var **names**
- health check, smoke checks, SLO, and vertical scaling bounds

```bash
python3 infra/liara_apply.py plan  -f infra/liara.app.yaml      # read-only diff; exit 0 in sync, 2 drift, 1 invalid
python3 infra/liara_apply.py apply -f infra/liara.app.yaml      # prompts for "yes"; never deletes without --allow-delete
```

The plan is validated against the bundle's real capabilities, read from `/v1/me`. CI runs it read-only on
every push to `main` and puts it in the job summary.

### Health checks and zero downtime

| | Value |
|---|---|
| Liara health check (liveness) | `python -c urlopen('http://127.0.0.1:8080/api/health')`, interval 30 s, timeout 10 s, 3 retries, start period 60 s. Values are in seconds: Liara's API rejects `interval < 30` and `startPeriod > 3000`, so the CLI's "ms" label is wrong |
| Post-deploy smoke tests | `/api/health` must return `status: ok` with `catalog_ready: true`; `/api/ready` must return `ready: true`; `/` must serve the RTL app (contains «بازارگرد», under 3 s) |
| Zero-downtime | not available on the basic bundle. The upgrade to standard costs about +903 toman/h; set `bundle: standard` and `zeroDowntime: true`, then run `apply` |

Traffic moves to a new release only after its health check passes. Otherwise the release turns `UNHEALTHY`
and the pipeline rolls back. At runtime, a failing check makes Liara restart the container.

### Container hardening

Multi-stage build: Node only in the build stage, `python:3.12-slim-bookworm` at runtime (220 MB). The process runs as **uid 10001**: the image fixes ownership of the mounted disk, then drops privileges with `setpriv`, and CI verifies that PID 1 is not root. CI also checks that no `.env`, keys or `.git` ship in the image. Each build gets an SBOM and a Trivy scan.
<!-- platform:end -->
