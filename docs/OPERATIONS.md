# Operations

Day-2 guide for running Bazargard on Liara: what to watch, how to react, and routine maintenance. Deployment steps are in [DEPLOYMENT.md](DEPLOYMENT.md). All `make liara-*` targets need `LIARA_API_TOKEN` and `LIARA_TEAM_ID` exported in your shell.

## 1. Service inventory

| Component | Where | Notes | Criticality |
|---|---|---|---|
| Bazargard app | Liara app `bazargard-ai` (Docker, port 8080), `https://bazargard-ai.liara.run/` | single instance, plan `standard-base-g2` + `basic` | critical |
| Persistent disk | Liara disk `data` → `/data` | `catalog.db`, `memory.db`, caches | critical (`memory.db`) |
| LLM | BoxAPI (`BOXAPI_BASE_URL`), several keys | chat and analysis only; catalogue features work without it | high |
| Catalogue seed | token-protected `catalog.db.gz` on a domestic host | needed only when the disk is empty | low (first boot) |

## 2. Health signals

| Signal | Command | Healthy |
|---|---|---|
| Liveness + release | `curl -fsS $BASE/api/health` | `status: ok`, expected `release` |
| Readiness | `curl -s -o /dev/null -w '%{http_code}' $BASE/api/ready` | `200` (`503` = no or empty catalogue). Prefer this over `catalog_ready` in `/api/health`, which only checks that the file exists. |
| SPA | `curl -s -o /dev/null -w '%{http_code}' $BASE/` | `200` |
| Platform state | `make liara-status` | `status: ACTIVE`, `scale: 1` |
| Current release | `make liara-releases` | `*` on a `READY` release |
| CPU / memory / disk | `make liara-status` (metrics summary) | stable memory; disk < 80 % |
| LLM path | one chat message in the UI | an answer, not a configuration/provider error |

`BASE=https://bazargard-ai.liara.run`.

## 3. Monitoring and alerting

Liara offers no app-level webhooks; health checks gate deployments and restart crashed containers, and account notifications (low credit, frozen services, disk-backup failures) go to email/SMS/Telegram. Add:

1. **External uptime probe** every minute on `$BASE/api/ready` from any uptime service or a cron job elsewhere; alert after 3 consecutive failures:
   ```bash
   */1 * * * * curl -fsS -m 10 -o /dev/null https://bazargard-ai.liara.run/api/ready || logger -t bazargard "ready check failed"
   ```
2. **Release drift:** alert when `/api/health.release` differs from the expected release after a deployment window.
3. **Disk usage** > 80 % (`make liara-status`): `public-research.db` and `ops-labs/` grow with use.
4. **Credit:** enable Liara low-credit notifications; the balance is shared by all team apps.
5. **Error spikes:** `scripts/liara_release.py logs --minutes 10 --search Traceback`.

## 4. Logs

| Need | Command |
|---|---|
| Recent app logs | `make liara-logs` or `python3 scripts/liara_release.py logs --minutes 60` |
| Search | `python3 scripts/liara_release.py logs --minutes 120 --search catalogue` |
| Follow | `liara app:logs --app bazargard-ai -f` |
| Build logs | Liara console → Releases, or `GET /v2/releases/<id>/build-logs` |

The app does not log API keys, tokens or chat bodies; uvicorn access logging is off. Keep it that way when adding logs (redact `Authorization` headers and key-like values).

## 5. On-call checklist

1. `curl $BASE/api/health` and `/api/ready`, `make liara-status`: down, not ready, or degraded?
2. **Down / 502:** `make liara-releases` — a release `BUILDING`/`DEPLOYING` (wait), `UNHEALTHY`/`FAILED` (read `make liara-logs`, then `make liara-rollback`). App `scale: 0` or frozen → start it / check credit. Otherwise `python3 scripts/liara_release.py restart`.
3. **Not ready (503):** seeding still running (`catalogue: downloading` in logs) or an empty `catalog.db` left behind — see [DEPLOYMENT.md §4](DEPLOYMENT.md#4-troubleshooting).
4. **Chat failing, catalogue fine:** BoxAPI problem — check key validity/quota and provider status; rotate or add keys in `BOXAPI_KEYS` (env change restarts the app). Search, comparison and maps keep working meanwhile.
5. **Slow:** at most 4 concurrent chat runs (others queue); Advanced runs are capped at 6 minutes; public research is bounded per source. Check CPU/memory in `make liara-status`; resize the plan if consistently saturated.
6. **Bad release:** `make liara-rollback`, then investigate on a local Docker build.
7. Record the incident (time, symptom, cause, fix) in the team channel and add a test or check for the cause.

## 6. Routine maintenance

| Task | Frequency | How |
|---|---|---|
| Back up `memory.db` | daily and before each release | disk backup (if the bundle has them) or an in-container SQLite snapshot ([DEPLOYMENT.md §1.6](DEPLOYMENT.md#16-backups-and-restore)) |
| Clear caches if the disk is tight | as needed | delete `/data/public-research.db` via `liara app:shell`, restart (rebuilt on demand) |
| Refresh catalogue | when a new dataset is issued | build and verify (`make test-full`), re-publish the seed, delete `/data/catalog.db`, restart |
| Rotate secrets | on exposure or team change | update Liara env vars (full list), app restarts automatically |
| Dependency updates | monthly | bump pins in `requirements.txt` / `package-lock.json`, full tests, release |
| Credit check | weekly | Liara console; stop the app when not needed |

## 7. Capacity notes

- One uvicorn worker; up to 4 concurrent assistant runs, others wait. Each Advanced run may take minutes (bounded at 6 min).
- Catalogue queries are local SQLite/FTS5; memory is dominated by the page cache of the ~430 MB catalogue.
- Liara supports vertical scaling only (`resize`); this app is single-replica by design.

<!-- platform:begin (maintained by the platform kit) -->
## Platform operations: SLOs, backups, runbooks

### SLOs and monitoring

**SLOs** (30-day rolling): availability **≥ 99.5 %** and p95 latency **< 1.5 s** on `/api/health (status ok + catalog_ready)`.

**Monitoring (live).** `meow-monitor.timer` on Hetzner (Helsinki) probes every 60 s:

- status, content, latency with DNS excluded, and TLS expiry;
- a failed probe is retried once after 5 s;
- the result is cross-checked with Negahban's in-Liara probes, and the app counts as down only when both
  vantages fail;
- a run where every app fails from Helsinki is a probe-side `vantage_unreachable` note, not an outage.

**Alerts.** An app is marked DOWN after 2 consecutive down verdicts; alerts go to a JSON log and an optional
webhook.

**Status page:** https://negahban-sre.liara.run/status shows both vantages for each app.

**Error-budget policy:**

- While more than 50 % of the budget remains, deploy freely.
- Below 50 %, deploy only with a reviewer.
- When the budget is exhausted, freeze features and allow only fixes and rollbacks.

### Backups and restore

The `data` disk holds `memory.db` (user memory), `places.db`, `public-research.db` and `ops-labs/`. The 430 MB `catalog.db` is **excluded**: it is reproducible from the domestic mirror and checked against `CATALOG_SHA256` at boot. The bundle is basic, so there are no native Liara backups. The platform job `meow-backup.timer` (daily 02:30 UTC, Hetzner) instead exports the disk through the Liara file browser API into `bazargard-ai--data--<UTC>.tar.gz`. It verifies every archive (sha256 per file, `PRAGMA integrity_check` on each SQLite file) and keeps 7 daily + 4 weekly copies. **Status: ready, not enabled.** Enable with `systemctl enable --now meow-backup.timer`. Restore: `liara_backup.py verify <archive>`, then `liara_backup.py restore <archive> --app bazargard-ai --disk data` (dry run), then `--yes` (uploads into `restore-<ts>/`), then scale 0, move files over `/data`, scale 1, then smoke. The exact sequence is in the platform document §8.

### Runbooks

Full commands: platform document §10. Shortest paths for Bazargard:

| # | Symptom | First action |
|---|---|---|
| R1 | Errors right after a release / Deploy exit 3 | `python3 infra/liara_deploy.py rollback --manifest infra/liara.app.yaml` then `... smoke` |
| R2 | Probe DOWN, 502/503, crash loop | Read events (`oom`/`die`) and logs (`GET /v2/projects/bazargard-ai/logs`), then `POST /v1/projects/bazargard-ai/actions/restart`. For OOM, raise `plan` within `scaling.max`, then `liara_apply.py plan` / `apply` |
| R3 | Broke after an env change | `liara_apply.py plan --app bazargard-ai` (missing names), then roll back to the previous ENV_UPDATE release (`rollback --to <id>`) |
| R4 | LLM errors / timeouts | Keys rotate automatically; switch `LLM_PROVIDER`; Arvan gate kill switch `sudo touch /etc/nginx/meow-llm.disabled` |
| R5 | Disk full or data damaged | Read `metrics/summary` → `disksUsage`; grow `sizeGB` and apply; or scale 0, restore (above), scale 1 |
| R6 | `frozen_project` / low credit | `liara_apply.py plan` (runway), top up, `POST /v1/projects/bazargard-ai/unfreeze` |

### On-call checklist (platform)

1. Check the status page and the last CI/Deploy run.
2. Check `liara_deploy.py status`. A release in the last hour points to R1.
3. Mitigate first (rollback / restart / provider switch), then debug.
4. Verify with `liara_deploy.py smoke` and confirm the probe is UP.
5. File an *Incident / postmortem* issue: UTC timeline, cause, actions.
<!-- platform:end -->
