#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
BOOTSTRAP=0
[[ "${1:-}" == "--bootstrap" ]] && BOOTSTRAP=1
source "$CONFIG_DIR/secrets.env"

mysql_root=(mariadb -uroot "-p$DB_ROOT_PASSWORD")
"${mysql_root[@]}" -e "CREATE DATABASE IF NOT EXISTS netlog_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE DATABASE IF NOT EXISTS syslogdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
"${mysql_root[@]}" netlog_manager -e "CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(64) PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, checksum CHAR(64) NOT NULL);"

shopt -s nullglob
for f in "$ROOT"/database/migrations/*.sql; do
  version="$(basename "$f" .sql)"
  checksum="$(sha256sum "$f" | awk '{print $1}')"
  existing="$("${mysql_root[@]}" -N netlog_manager -e "SELECT checksum FROM schema_migrations WHERE version='$version' LIMIT 1;")"
  if [[ -n "$existing" ]]; then
    [[ "$existing" == "$checksum" ]] || die "Migration $version was modified after being applied."
    continue
  fi
  log "Applying migration $version"
  "${mysql_root[@]}" <"$f"
  "${mysql_root[@]}" netlog_manager -e "INSERT INTO schema_migrations(version,checksum) VALUES('$version','$checksum');"
done
