#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"

cd "$ROOT"
if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(docker-compose); fi
ENV_FILE="$STATE_DIR/docker/.env"
db(){ "${COMPOSE[@]}" --env-file "$ENV_FILE" exec -T db mariadb -uroot "-p$DB_ROOT_PASSWORD" "$@"; }

db -e "CREATE DATABASE IF NOT EXISTS netlog_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE DATABASE IF NOT EXISTS syslogdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
db netlog_manager -e "CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(64) PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, checksum CHAR(64) NOT NULL);"

shopt -s nullglob
for f in "$ROOT"/database/migrations/*.sql; do
  version="$(basename "$f" .sql)"
  checksum="$(sha256sum "$f" | awk '{print $1}')"
  existing="$(db -N netlog_manager -e "SELECT checksum FROM schema_migrations WHERE version='$version' LIMIT 1;")"
  if [[ -n "$existing" ]]; then
    [[ "$existing" == "$checksum" ]] || die "Migration $version was modified after being applied."
    continue
  fi
  log "Applying Docker migration $version"
  db <"$f"
  db netlog_manager -e "INSERT INTO schema_migrations(version,checksum) VALUES('$version','$checksum');"
done

db -e "GRANT EXECUTE ON PROCEDURE syslogdb.CreateFutureTables TO 'netlog_maintenance'@'%'; FLUSH PRIVILEGES;"
