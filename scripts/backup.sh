#!/usr/bin/env bash
# Nightly Postgres backup. Cron: 15 3 * * * cd /path/to/repo && ./scripts/backup.sh
# Set BACKUP_REMOTE (an rclone remote, e.g. r2:research-backups) to copy off the machine;
# a backup that lives only on the same disk is not a backup.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="${BACKUP_DIR:-./backups}"; KEEP="${BACKUP_KEEP:-14}"
mkdir -p "$DIR"
FILE="$DIR/research-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
docker compose exec -T db pg_dump -U research --clean --if-exists research | gzip > "$FILE"
gzip -t "$FILE"                                   # refuse to keep a corrupt archive
[ -s "$FILE" ] || { echo "empty backup" >&2; exit 1; }
if [ -n "${BACKUP_REMOTE:-}" ]; then rclone copy "$FILE" "$BACKUP_REMOTE"; fi
ls -1t "$DIR"/research-*.sql.gz | tail -n +$((KEEP + 1)) | xargs -r rm --
echo "backup ok: $FILE"
