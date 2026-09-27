#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
load_install_state
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"
[[ "${INSTALL_MODE:-}" == "docker" ]] || die "Docker restore requires INSTALL_MODE=docker."
backup="${1:-}"
[[ -n "$backup" && -d "$backup" ]] || die "Usage: $0 /var/backups/netlog-manager/YYYYmmdd_HHMMSS"
[[ -f "$backup/SHA256SUMS" && -f "$backup/netlog_manager.sql" && -f "$backup/syslogdb-schema.sql" ]] || die "Incomplete backup."
[[ "$backup" == "$BACKUP_DIR/"* ]] || die "Refusing backup path outside $BACKUP_DIR."
(cd "$backup" && sha256sum -c SHA256SUMS)
cd "$REPO_ROOT"
if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(docker-compose); fi
ENV_FILE="$STATE_DIR/docker/.env"
log "Creating pre-restore safety backup"
"$ROOT/scripts/backup-docker.sh" >/dev/null
log "Stopping application services while restoring metadata"
"${COMPOSE[@]}" --env-file "$ENV_FILE" stop app archive-cache export-worker export-cleanup syslog
db(){ "${COMPOSE[@]}" --env-file "$ENV_FILE" exec -T db mariadb -uroot "-p$DB_ROOT_PASSWORD" "$@"; }
trap '"${COMPOSE[@]}" --env-file "$ENV_FILE" up -d app archive-cache export-worker export-cleanup syslog' EXIT
db -e "DROP DATABASE IF EXISTS netlog_manager; CREATE DATABASE netlog_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
db <"$backup/netlog_manager.sql"
log "Application database restored. Existing syslog log data was intentionally preserved."
"$ROOT/scripts/migrate-docker.sh"
"${COMPOSE[@]}" --env-file "$ENV_FILE" up -d app archive-cache export-worker export-cleanup syslog
"$ROOT/scripts/healthcheck.sh"
trap - EXIT
log "Docker restore completed."
