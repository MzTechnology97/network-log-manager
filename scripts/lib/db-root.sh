#!/usr/bin/env bash
set -Eeuo pipefail
mysql_root_command() {
  MYSQL_ROOT=(mariadb --protocol=socket -uroot)
  MYSQLDUMP_ROOT=(mariadb-dump --protocol=socket -uroot)
  if ! "${MYSQL_ROOT[@]}" -N -e "SELECT 1" >/dev/null 2>&1; then
    [[ -n "${DB_ROOT_PASSWORD:-}" ]] || die "MariaDB root socket authentication failed and no root password is configured."
    MYSQL_ROOT=(mariadb -uroot "-p$DB_ROOT_PASSWORD")
    MYSQLDUMP_ROOT=(mariadb-dump -uroot "-p$DB_ROOT_PASSWORD")
  fi
}
