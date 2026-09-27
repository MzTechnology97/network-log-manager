#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
source "$CONFIG_DIR/secrets.env"
stamp="$(date +%Y%m%d_%H%M%S)"
dest="$BACKUP_DIR/$stamp"
install -d -m 0700 "$dest"
log "Backing up configuration and schemas before update"
tar -czf "$dest/config.tar.gz" "$CONFIG_DIR" 2>/dev/null
mariadb-dump -uroot "-p$DB_ROOT_PASSWORD" --single-transaction --routines --events netlog_manager >"$dest/netlog_manager.sql"
mariadb-dump -uroot "-p$DB_ROOT_PASSWORD" --no-data --routines --events syslogdb >"$dest/syslogdb-schema.sql"
printf '%s\n' "$dest"
