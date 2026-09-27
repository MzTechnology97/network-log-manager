#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
load_install_state
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"
cd "$REPO_ROOT"
if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(docker-compose); fi
ENV_FILE="$STATE_DIR/docker/.env"
stamp="$(date +%Y%m%d_%H%M%S)"
dest="$BACKUP_DIR/$stamp"
install -d -m 0700 "$dest"
tar -czf "$dest/config.tar.gz" "$CONFIG_DIR" "$STATE_DIR/docker/.env" "$STATE_DIR/docker/tls" 2>/dev/null
"${COMPOSE[@]}" --env-file "$ENV_FILE" exec -T db mariadb-dump -uroot "-p$DB_ROOT_PASSWORD" --single-transaction --routines --events netlog_manager >"$dest/netlog_manager.sql"
"${COMPOSE[@]}" --env-file "$ENV_FILE" exec -T db mariadb-dump -uroot "-p$DB_ROOT_PASSWORD" --no-data --routines --events syslogdb >"$dest/syslogdb-schema.sql"
sha256sum "$dest"/* >"$dest/SHA256SUMS"
printf '%s\n' "$dest"
