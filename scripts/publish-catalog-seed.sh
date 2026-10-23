#!/usr/bin/env bash
# Publish data/catalog.db as a token-protected download for Liara first boot.
# Run ON a host with nginx that Liara's (Iranian) runtime can download from quickly.
# Snapshots the catalogue, gzips it to /srv/liara-data, writes a fresh bearer token into
# an nginx include and writes CATALOG_URL / CATALOG_TOKEN / CATALOG_SHA256 to a
# mode-600 file (never stdout). Set those three values on the Liara app afterwards.
#
#   sudo CATALOG_DB=/path/catalog.db PUBLIC_HOST=<SEED_HOST> scripts/publish-catalog-seed.sh
set -euo pipefail
CATALOG_DB=${CATALOG_DB:-data/catalog.db}; PUBLIC_HOST=${PUBLIC_HOST:?set PUBLIC_HOST}
SCHEME=${SCHEME:-https}; OUT=${OUT:-/root/catalog-seed.env}; SEED_DIR=/srv/liara-data
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
[ -s "$CATALOG_DB" ] || { echo "missing $CATALOG_DB" >&2; exit 1; }
install -d -m 750 -g www-data "$SEED_DIR"
python3 -c "import sqlite3,sys;s=sqlite3.connect(sys.argv[1]);d=sqlite3.connect(sys.argv[2]);s.backup(d);d.close()" "$CATALOG_DB" /tmp/catalog-snap.db
gzip -c -6 /tmp/catalog-snap.db > "$SEED_DIR/catalog.db.gz"; rm -f /tmp/catalog-snap.db
chgrp www-data "$SEED_DIR/catalog.db.gz"; chmod 640 "$SEED_DIR/catalog.db.gz"
TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
umask 077; printf 'if ($http_authorization != "Bearer %s") { return 401; }\n' "$TOKEN" > /etc/nginx/catalog-seed-token.conf
printf 'CATALOG_URL=%s://%s/liara-data/catalog.db.gz\nCATALOG_TOKEN=%s\nCATALOG_SHA256=%s\n' \
  "$SCHEME" "$PUBLIC_HOST" "$TOKEN" "$(sha256sum "$SEED_DIR/catalog.db.gz" | cut -d' ' -f1)" > "$OUT"
grep -qr 'liara-data/catalog.db.gz' /etc/nginx/sites-enabled/ || echo "add deploy/catalog-seed/nginx-location.conf to a server block" >&2
nginx -t && systemctl reload nginx
echo "seed published; Liara env values written to $OUT (mode 600)"
