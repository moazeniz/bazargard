#!/usr/bin/env bash
# Consistent online backup of the SQLite state (safe while the service runs).
#   scripts/backup-data.sh [DATA_DIR] [BACKUP_ROOT]
# memory.db (users, trips, chats, seller replies) is the only irreplaceable file.
# public-research.db is a cache; catalog.db / canonical-products.db can be rebuilt.
set -euo pipefail
DATA=${1:-${DATA_DIR:-data}}; DEST=${2:-backups}/bazargard-$(date +%Y%m%d-%H%M%S)
mkdir -p "$DEST"; chmod 700 "$DEST"
for db in memory.db public-research.db; do
  [ -f "$DATA/$db" ] || continue
  python3 - "$DATA/$db" "$DEST/$db" <<'PY'
import sqlite3,sys
src=sqlite3.connect(sys.argv[1]);dst=sqlite3.connect(sys.argv[2])
with dst:src.backup(dst)
ok=dst.execute('pragma integrity_check').fetchone()[0];dst.close();src.close()
if ok!='ok':sys.exit('integrity check failed: '+sys.argv[2])
PY
  gzip -9 "$DEST/$db"
done
[ -f "$DATA/catalog.db" ] && sha256sum "$DATA/catalog.db" > "$DEST/catalog.db.sha256" || true
ls -la "$DEST"
