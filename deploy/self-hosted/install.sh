#!/usr/bin/env bash
# OPTIONAL self-hosted alternative to Liara: one-time setup on an Ubuntu VM.
# Run ON the VM, from the application directory (the copy of this repository):
#
#   sudo APP_DIR=$PWD APP_USER=ubuntu ENV_FILE=/home/<user>/.torob-agent.env \
#        NGINX_SITE=/etc/nginx/sites-enabled/default deploy/self-hosted/install.sh
#
# It renders the systemd unit, inserts the /hamrah/ location into an existing nginx
# server block (backing the file up first, idempotent), validates nginx and starts
# the service. It never touches other services. The env file must already exist.
set -euo pipefail

APP_DIR=${APP_DIR:-/home/<user>/torob-agent}
APP_USER=${APP_USER:-ubuntu}
ENV_FILE=${ENV_FILE:-/home/<user>/.torob-agent.env}
SERVICE=${SERVICE:-torob-shopping-agent}
PORT=${PORT:-8506}
NGINX_SITE=${NGINX_SITE:-/etc/nginx/sites-enabled/default}
BACKUP_DIR=${BACKUP_DIR:-/home/$APP_USER/backups}
HERE=$(cd "$(dirname "$0")" && pwd)

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
[ -s "$ENV_FILE" ] || { echo "missing $ENV_FILE (copy .env.example, fill it, chmod 600)" >&2; exit 1; }
[ -x "$APP_DIR/.venv/bin/uvicorn" ] || { echo "missing $APP_DIR/.venv (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)" >&2; exit 1; }
[ -f "$APP_DIR/frontend/dist/index.html" ] || echo "warning: $APP_DIR/frontend/dist not built; only the API will be served" >&2
chmod 600 "$ENV_FILE"; chown "$APP_USER": "$ENV_FILE"
install -d -o "$APP_USER" -m 700 "$APP_DIR/data" "$BACKUP_DIR"

sed -e "s#@APP_DIR@#$APP_DIR#g" -e "s#@APP_USER@#$APP_USER#g" -e "s#@ENV_FILE@#$ENV_FILE#g" -e "s#@PORT@#$PORT#g" \
    "$HERE/bazargard.service" > "/etc/systemd/system/$SERVICE.service"

if [ -f "$NGINX_SITE" ] && ! grep -q 'location /hamrah/' "$NGINX_SITE"; then
    cp -p "$NGINX_SITE" "$BACKUP_DIR/nginx-before-bazargard-$(date +%s).conf"
    SNIPPET=$(sed "s#127.0.0.1:8506#127.0.0.1:$PORT#" "$HERE/nginx-location.conf") \
    python3 - "$NGINX_SITE" <<'PY'
import os,sys
from pathlib import Path
p=Path(sys.argv[1]);s=p.read_text()
if '    location / {' not in s:sys.exit('no "    location / {" anchor in '+str(p)+'; include deploy/self-hosted/nginx-location.conf manually')
p.write_text(s.replace('    location / {',os.environ['SNIPPET']+'\n\n    location / {',1))
PY
elif [ ! -f "$NGINX_SITE" ]; then
    echo "note: $NGINX_SITE not found; configure nginx with deploy/self-hosted/nginx-site.conf.example" >&2
fi

nginx -t
systemctl daemon-reload
systemctl enable --now "$SERVICE"
systemctl restart "$SERVICE"
systemctl reload nginx
sleep 3
curl -fsS "http://127.0.0.1:$PORT/api/health" && echo
