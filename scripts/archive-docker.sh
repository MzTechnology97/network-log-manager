#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
load_install_state
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"
[[ "${MODE:-}" == "docker" ]] || die "Docker archive job requires MODE=docker."
cd "$REPO_ROOT"
if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(docker-compose); fi
ENV_FILE="$STATE_DIR/docker/.env"
DB=syslogdb
LIMIT=10
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
[[ "${1:-}" == "--all" ]] && LIMIT=0
if [[ "${1:-}" == "--limit" ]]; then [[ "${2:-}" =~ ^[1-9][0-9]*$ ]] || die "Invalid --limit"; LIMIT="$2"; fi
db(){ "${COMPOSE[@]}" --env-file "$ENV_FILE" exec -T db mariadb -uroot "-p$DB_ROOT_PASSWORD" "$@"; }
dump(){ "${COMPOSE[@]}" --env-file "$ENV_FILE" exec -T db mariadb-dump -uroot "-p$DB_ROOT_PASSWORD" "$@"; }
command -v zstd >/dev/null || die "zstd is required on the Docker host."
command -v flock >/dev/null || die "flock is required on the Docker host."
exec 9>"$STATE_DIR/docker/archive.lock"
flock -n 9 || die "Another Docker archive job is running."
year="$(date +%Y)"
cutoff="mikrotik_logs_${year}_01_01"
mapfile -t tables < <(db -N -B -e "SELECT table_name FROM information_schema.tables WHERE table_schema='$DB' AND table_name REGEXP '^mikrotik_logs_[0-9]{4}_[0-9]{2}_[0-9]{2}$' AND table_name < '$cutoff' ORDER BY table_name")
(( DRY_RUN )) && { printf '%s\n' "${tables[@]}"; exit 0; }
done_count=0
for table in "${tables[@]}"; do
  (( LIMIT == 0 || done_count < LIMIT )) || break
  [[ "$table" =~ ^mikrotik_logs_([0-9]{4})_([0-9]{2})_([0-9]{2})$ ]] || continue
  y="${BASH_REMATCH[1]}"; m="${BASH_REMATCH[2]}"; d="${BASH_REMATCH[3]}"
  dir="$ARCHIVE_ROOT/$y/$m"; final="$dir/$table.sql.zst"; tmp="$dir/.$table.sql.zst.tmp"
  meta="$dir/$table.meta"; sha="$dir/$table.sql.zst.sha256"
  install -d -m 0750 "$dir"
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
RETENTION_POLICY=current_year
EOF
  printf '%s  %s\n' "$hash" "$(basename "$final")" >"$sha"
  (cd "$dir" && sha256sum -c "$(basename "$sha")" >/dev/null)
  still_exists="$(db -N -B -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB' AND table_name='$table';")"
  [[ "$still_exists" == "1" ]] || die "Source table disappeared before DROP: $table"
  db "$DB" -e "DROP TABLE \`$table\`;"
  ((done_count+=1))
  log "Archived and removed $table"
done
log "Docker archive job completed: $done_count table(s)."
