#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
source "$ROOT/scripts/lib/db-root.sh"
require_root
load_install_state
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"
[[ "${MODE:-}" == "native" ]] || die "Native retention cleanup requires MODE=native."
mysql_root_command
cd "$REPO_ROOT"
db(){ "${MYSQL_ROOT[@]}" "$@"; }
retention_days="$(db -N -B netlog_manager -e "SELECT COALESCE((SELECT setting_value FROM settings WHERE setting_key='retention_days' LIMIT 1),'1825');")"
[[ "$retention_days" =~ ^[1-9][0-9]*$ ]] || die "Invalid retention_days."
cutoff="$(date -d "$retention_days days ago" +%F)"
deleted=0
while IFS= read -r -d '' archive; do
  base="$(basename "$archive" .sql.zst)"
  [[ "$base" =~ ^mikrotik_logs_([0-9]{4})_([0-9]{2})_([0-9]{2})$ ]] || continue
  day="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"
  [[ "$day" < "$cutoff" ]] || continue
  dir="$(dirname "$archive")"; sha="$archive.sha256"; meta="$dir/$base.meta"
  [[ -f "$sha" && -f "$meta" ]] || { log "Skipping incomplete archive: $archive"; continue; }
  (cd "$dir" && sha256sum -c "$(basename "$sha")" >/dev/null) || { log "Skipping archive with failed checksum: $archive"; continue; }
  rm -f -- "$archive" "$sha" "$meta"
  ((deleted+=1))
  log "Retention removed $base ($day; policy $retention_days days)"
done < <(find "$ARCHIVE_ROOT" -mindepth 3 -maxdepth 3 -type f -name 'mikrotik_logs_*.sql.zst' -print0)
log "Retention cleanup completed: $deleted archive(s) removed; cutoff $cutoff."
