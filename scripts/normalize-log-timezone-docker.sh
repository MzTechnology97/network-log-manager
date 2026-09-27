#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
load_install_state
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"
[[ "${MODE:-}" == docker ]] || exit 0
export TZ="${TZ:-Europe/Rome}"
cd "$REPO_ROOT"
if docker compose version >/dev/null 2>&1; then C=(docker compose); else C=(docker-compose); fi
ENV_FILE="$STATE_DIR/docker/.env"
db(){ "${C[@]}" --env-file "$ENV_FILE" exec -T db mariadb -uroot "-p$DB_ROOT_PASSWORD" "$@"; }
mode="$(db -N -B netlog_manager -e "SELECT COALESCE((SELECT setting_value FROM settings WHERE setting_key='log_timestamp_timezone' LIMIT 1),'UTC');")"
[[ "$mode" == "UTC" ]] || { log "Log timestamps already normalized: $mode"; exit 0; }
log "Loading timezone definitions into MariaDB."
"${C[@]}" --env-file "$ENV_FILE" exec -T db sh -c 'mariadb-tzinfo-to-sql /usr/share/zoneinfo 2>/dev/null' | db mysql >/dev/null
mapfile -t tables < <(db -N -B -e "SELECT table_name FROM information_schema.tables WHERE table_schema='syslogdb' AND table_name REGEXP '^mikrotik_logs_[0-9]{4}_[0-9]{2}_[0-9]{2}$' ORDER BY table_name")
for table in "${tables[@]}"; do
  log "Normalizing $table UTC -> Europe/Rome"
  db syslogdb -e "UPDATE \`$table\` SET timestamp=CONVERT_TZ(timestamp,'UTC','Europe/Rome');"
done
db netlog_manager -e "INSERT INTO settings(setting_key,setting_value) VALUES('log_timestamp_timezone','Europe/Rome') ON DUPLICATE KEY UPDATE setting_value='Europe/Rome';"
log "Existing log timestamps normalized to Europe/Rome."
