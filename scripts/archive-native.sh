#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
source "$ROOT/scripts/lib/db-root.sh"
require_root
load_install_state
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"
export TZ="${TZ:-Europe/Rome}"
[[ "${MODE:-}" == "native" ]] || die "Native archive job requires MODE=native."
mysql_root_command
cd "$REPO_ROOT"
DB=syslogdb
LIMIT=10
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
[[ "${1:-}" == "--all" ]] && LIMIT=0
if [[ "${1:-}" == "--limit" ]]; then [[ "${2:-}" =~ ^[1-9][0-9]*$ ]] || die "Invalid --limit"; LIMIT="$2"; fi
db(){ "${MYSQL_ROOT[@]}" "$@"; }
dump(){ "${MYSQLDUMP_ROOT[@]}" "$@"; }
command -v zstd >/dev/null || die "zstd is required on the Docker host."
command -v flock >/dev/null || die "flock is required on the Docker host."
exec 9>"$STATE_DIR/archive.lock"
flock -n 9 || die "Another native archive job is running."
archive_after_days="$(db -N -B netlog_manager -e "SELECT COALESCE((SELECT setting_value FROM settings WHERE setting_key='archive_after_days' LIMIT 1),'365');" 2>/dev/null || echo 365)"
[[ "$archive_after_days" =~ ^[1-9][0-9]*$ ]] || archive_after_days=365
cutoff_date="$(date -d "$archive_after_days days ago" +%Y_%m_%d)"
cutoff="mikrotik_logs_${cutoff_date}"
mapfile -t tables < <(db -N -B -e "SELECT table_name FROM information_schema.tables WHERE table_schema='$DB' AND table_name REGEXP '^mikrotik_logs_[0-9]{4}_[0-9]{2}_[0-9]{2}$' AND table_name < '$cutoff' ORDER BY table_name")
(( DRY_RUN )) && { printf '%s\n' "${tables[@]}"; exit 0; }
done_count=0
for table in "${tables[@]}"; do
  (( LIMIT == 0 || done_count < LIMIT )) || break
  [[ "$table" =~ ^mikrotik_logs_([0-9]{4})_([0-9]{2})_([0-9]{2})$ ]] || continue
  y="${BASH_REMATCH[1]}"; m="${BASH_REMATCH[2]}"; d="${BASH_REMATCH[3]}"
  dir="$ARCHIVE_ROOT/$y/$m"; final="$dir/$table.sql.zst"; tmp="$dir/.$table.sql.zst.tmp"
  meta="$dir/$table.meta"; sha="$dir/$table.sql.zst.sha256"
  install -d -o netlog -g netlog -m 0750 "$dir"
  [[ ! -e "$final" ]] || { log "Archive exists, keeping DB table: $table"; continue; }
  rows="$(db -N -B "$DB" -e "SELECT COUNT(*) FROM \`$table\`;")"
  log "Archiving $table ($rows rows)"
  rm -f "$tmp"
  dump --single-transaction --quick --skip-lock-tables --default-character-set=utf8mb4 "$DB" "$table" | zstd -T0 -6 -q -o "$tmp"
  zstd -t -q "$tmp"
  zstdcat "$tmp" | grep -F "CREATE TABLE \`$table\`" >/dev/null
  if (( rows > 0 )); then zstdcat "$tmp" | grep -F "INSERT INTO \`$table\`" >/dev/null; fi
  mv "$tmp" "$final"
  hash="$(sha256sum "$final" | awk '{print $1}')"
  bytes="$(stat -c '%s' "$final")"
  uncompressed_bytes="$(zstdcat "$final" | wc -c)"
  cat >"$meta" <<EOF
TABLE=$table
DATE=$y-$m-$d
ROWS=$rows
COMPRESSED_BYTES=$bytes
UNCOMPRESSED_BYTES=$uncompressed_bytes
SHA256=$hash
ARCHIVED_AT=$(date --iso-8601=seconds)
RETENTION_POLICY=archive_after_${archive_after_days}_days
EOF
  printf '%s  %s\n' "$hash" "$(basename "$final")" >"$sha"
  (cd "$dir" && sha256sum -c "$(basename "$sha")" >/dev/null)
  # If a GUI-configured remote storage is active and verified, copy all
  # archive artifacts before the source DB table can be dropped.
  if ! {
    runuser -u netlog -- /opt/netlog-manager/venv/bin/python -m app.storage_sync --file "/archive/mikrotik/$y/$m/$(basename "$final")" --relative "$y/$m/$(basename "$final")" --sha256 "$hash" &&
    runuser -u netlog -- /opt/netlog-manager/venv/bin/python -m app.storage_sync --file "/archive/mikrotik/$y/$m/$(basename "$meta")" --relative "$y/$m/$(basename "$meta")" &&
    runuser -u netlog -- /opt/netlog-manager/venv/bin/python -m app.storage_sync --file "/archive/mikrotik/$y/$m/$(basename "$sha")" --relative "$y/$m/$(basename "$sha")";
  }; then
    rm -f "$final" "$meta" "$sha"
    die "Remote archive replication failed for $table; source table preserved and local partial archive reset."
  fi
  chown netlog:netlog "$final" "$meta" "$sha"
  chmod 0640 "$final" "$meta" "$sha"
  still_exists="$(db -N -B -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB' AND table_name='$table';")"
  [[ "$still_exists" == "1" ]] || die "Source table disappeared before DROP: $table"
  db "$DB" -e "DROP TABLE \`$table\`;"
  ((done_count+=1))
  log "Archived and removed $table"
done
log "Native archive job completed: $done_count table(s)."
