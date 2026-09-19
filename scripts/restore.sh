#!/usr/bin/env bash
# Restore a backup into the running db service. Usage: ./scripts/restore.sh backups/research-XYZ.sql.gz
# Stop the worker first so no run is mid-flight:  docker compose stop worker api
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f "${1:-}" ] || { echo "usage: $0 <backup.sql.gz>" >&2; exit 1; }
gunzip -c "$1" | docker compose exec -T db psql -U research -d research -v ON_ERROR_STOP=1 -q
echo "restored from $1 — start services with: docker compose up -d"
