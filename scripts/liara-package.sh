#!/usr/bin/env bash
# Build the gzip'd source archive that Liara builds with the root Dockerfile.
# Honours .liaraignore (same rules as .dockerignore). Output: dist/bazargard-source.tar.gz
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); OUT=${1:-$ROOT/dist/bazargard-source.tar.gz}
mkdir -p "$(dirname "$OUT")"
EXCLUDES=(--exclude=./dist)
while IFS= read -r line; do
  line=${line%%#*}; line=$(echo "$line" | xargs); [ -n "$line" ] || continue
  EXCLUDES+=("--exclude=${line#\*\*/}")
done < "$ROOT/.liaraignore"
tar -C "$ROOT" "${EXCLUDES[@]}" -czf "$OUT" .
echo "$OUT $(du -h "$OUT" | cut -f1)"; tar -tzf "$OUT" | sed 's#^\./##' | cut -d/ -f1 | sort -u | tr '\n' ' '; echo
