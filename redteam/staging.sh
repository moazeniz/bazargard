#!/bin/sh
# Red-team staging on 127.0.0.1:8612 with a PRIVATE poisoned DATA_DIR (never production data).
#   sh redteam/staging.sh start /opt/bazargard/project        # hardened tree
#   sh redteam/staging.sh baseline                               # same code, guard layer disabled
#   sh redteam/staging.sh stop
set -e
S=/opt/bazargard/staging-8612;UNIT=hamrah-redteam-8612;P=/opt/bazargard/project
start(){
  systemctl stop $UNIT 2>/dev/null || true;systemctl reset-failed $UNIT 2>/dev/null || true
  systemd-run --quiet --collect --unit=$UNIT --uid=hamrah-agent --gid=hamrah-agent -p EnvironmentFile=/root/.boxapi.env -p WorkingDirectory="$1" \
    --setenv=DATA_DIR=$S/data --setenv=HOME=/opt/bazargard/home --setenv=WEB_GATEWAY_URL= \
    $P/.venv/bin/uvicorn server:app --app-dir "$1/backend" --host 127.0.0.1 --port 8612 --proxy-headers --forwarded-allow-ips 127.0.0.1
  for i in $(seq 90);do curl -sf http://127.0.0.1:8612/api/ready >/dev/null && break;sleep 2;done
  curl -s http://127.0.0.1:8612/api/health;echo
}
case "$1" in
  start)start "$2";;
  baseline)
    B=$S/baseline;rm -rf $B;mkdir -p $B/frontend;cp -r $P/backend $B/;cp -r $P/frontend/dist $B/frontend/
    cat $P/redteam/null_guard.py >> $B/backend/guard.py
    sed -i 's/^t=chr(10).join(visible(p) for p in r.pages)\[:120000\]$/t=chr(10).join((p.extract_text() or "") for p in r.pages)[:120000]/' $B/backend/public_research.py
    chown -R hamrah-agent:hamrah-agent $B;rm -f $S/data/memory.db $S/data/memory.db-*;start $B;;
  stop)systemctl stop $UNIT;;
esac
