#!/usr/bin/env bash
# OPTIONAL self-hosted path: ship a release to a VM set up with install.sh
# (see docs/DEPLOYMENT.md, "Optional: self-hosted VM").
#
#   HOST=ubuntu@<ARVAN_HOST> deploy/self-hosted/deploy-vm.sh            # deploy
#   HOST=ubuntu@<ARVAN_HOST> DRY_RUN=1 deploy/self-hosted/deploy-vm.sh  # show what would be copied
#
# Steps: build frontend locally -> back up current code + memory.db on the VM ->
# rsync backend/, frontend/dist, requirements.txt -> pip install -> restart -> health check.
# data/, the env file and the venv are never overwritten. Requires ssh + rsync.
set -euo pipefail
: "${HOST:?set HOST=user@host}"
APP_DIR=${APP_DIR:-/home/<user>/torob-agent}
SERVICE=${SERVICE:-torob-shopping-agent}
PORT=${PORT:-8506}
SSH_OPTS=${SSH_OPTS:-}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
RSYNC=(rsync -az --delete --exclude __pycache__ --exclude '*.pyc' -e "ssh $SSH_OPTS")

if [ -z "${SKIP_BUILD:-}" ]; then npm --prefix "$ROOT/frontend" ci --no-audit --no-fund && npm --prefix "$ROOT/frontend" run build; fi
[ -f "$ROOT/frontend/dist/index.html" ] || { echo "frontend/dist missing" >&2; exit 1; }

if [ -n "${DRY_RUN:-}" ]; then
    "${RSYNC[@]}" -n -i "$ROOT/backend/" "$HOST:$APP_DIR/backend/"
    "${RSYNC[@]}" -n -i "$ROOT/frontend/dist/" "$HOST:$APP_DIR/frontend/dist/"
    exit 0
fi

# shellcheck disable=SC2029
ssh $SSH_OPTS "$HOST" "set -e; B=\$HOME/backups/bazargard-$STAMP; mkdir -p \$B
  tar -C '$APP_DIR' -czf \$B/code.tgz backend frontend/dist requirements.txt 2>/dev/null || true
  [ -f '$APP_DIR/data/memory.db' ] && python3 -c \"import sqlite3,sys;s=sqlite3.connect(sys.argv[1]);d=sqlite3.connect(sys.argv[2]);s.backup(d);d.close()\" '$APP_DIR/data/memory.db' \$B/memory.db || true
  echo backup: \$B"
"${RSYNC[@]}" "$ROOT/backend/" "$HOST:$APP_DIR/backend/"
"${RSYNC[@]}" "$ROOT/frontend/dist/" "$HOST:$APP_DIR/frontend/dist/"
rsync -az -e "ssh $SSH_OPTS" "$ROOT/requirements.txt" "$HOST:$APP_DIR/"
rsync -az -e "ssh $SSH_OPTS" "$ROOT/frontend/package.json" "$HOST:$APP_DIR/frontend/"
ssh $SSH_OPTS "$HOST" "set -e
  '$APP_DIR/.venv/bin/pip' install -q -r '$APP_DIR/requirements.txt' ${PIP_ARGS:-}
  sudo systemctl restart '$SERVICE'; sleep 4
  curl -fsS http://127.0.0.1:$PORT/api/health; echo; curl -fsS -o /dev/null -w 'ready %{http_code}\n' http://127.0.0.1:$PORT/api/ready"
