#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"

mysql_root=(mariadb --protocol=socket -uroot)
if ! "${mysql_root[@]}" -N -e "SELECT 1" >/dev/null 2>&1; then
  [[ -n "${DB_ROOT_PASSWORD:-}" ]] || die "MariaDB root socket authentication failed and no root password is configured."
  mysql_root=(mariadb -uroot "-p$DB_ROOT_PASSWORD")
fi

sql_escape() { printf "%s" "$1" | sed "s/'/''/g"; }
APP_PW="$(sql_escape "$NETLOG_APP_PASSWORD")"
READER_PW="$(sql_escape "$NETLOG_READER_PASSWORD")"
INGEST_PW="$(sql_escape "$NETLOG_INGEST_PASSWORD")"
MAINT_PW="$(sql_escape "$NETLOG_MAINT_PASSWORD")"

"${mysql_root[@]}" <<SQL
CREATE DATABASE IF NOT EXISTS netlog_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS syslogdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'netlog_app'@'localhost' IDENTIFIED BY '$APP_PW';
CREATE USER IF NOT EXISTS 'netlog_reader'@'localhost' IDENTIFIED BY '$READER_PW';
CREATE USER IF NOT EXISTS 'netlog_ingest'@'localhost' IDENTIFIED BY '$INGEST_PW';
CREATE USER IF NOT EXISTS 'netlog_maintenance'@'localhost' IDENTIFIED BY '$MAINT_PW';
ALTER USER 'netlog_app'@'localhost' IDENTIFIED BY '$APP_PW';
ALTER USER 'netlog_reader'@'localhost' IDENTIFIED BY '$READER_PW';
ALTER USER 'netlog_ingest'@'localhost' IDENTIFIED BY '$INGEST_PW';
ALTER USER 'netlog_maintenance'@'localhost' IDENTIFIED BY '$MAINT_PW';
GRANT SELECT,INSERT,UPDATE,DELETE ON netlog_manager.* TO 'netlog_app'@'localhost';
GRANT SELECT ON syslogdb.* TO 'netlog_reader'@'localhost';
GRANT INSERT ON syslogdb.* TO 'netlog_ingest'@'localhost';
GRANT CREATE ON syslogdb.* TO 'netlog_maintenance'@'localhost';
FLUSH PRIVILEGES;
SQL
