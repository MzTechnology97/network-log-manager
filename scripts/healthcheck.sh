#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
load_install_state

if [[ "$MODE" == docker ]]; then
  source "$CONFIG_DIR/install.env"
  source "$CONFIG_DIR/secrets.env"
  cd "$REPO_ROOT"
  if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(docker-compose); fi
  ENV_FILE="$STATE_DIR/docker/.env"
  for service in db app proxy syslog archive-cache export-worker export-cleanup; do
    id="$("${COMPOSE[@]}" --env-file "$ENV_FILE" ps -q "$service")"
    [[ -n "$id" ]] || die "Docker service $service is missing."
    state="$(docker inspect -f '{{.State.Status}}' "$id")"
    [[ "$state" == running ]] || die "Docker service $service is not running: $state"
  done
  "${COMPOSE[@]}" --env-file "$ENV_FILE" exec -T db mariadb-admin ping -uroot "-p$DB_ROOT_PASSWORD" --silent >/dev/null
  "${COMPOSE[@]}" --env-file "$ENV_FILE" exec -T db mariadb -uroot "-p$DB_ROOT_PASSWORD" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='netlog_manager' AND table_name='schema_migrations';" | grep -qx 1
  curl -kfsS --resolve "$HOSTNAME_FQDN:443:127.0.0.1" --max-time 10 "https://$HOSTNAME_FQDN/" >/dev/null
else
  systemctl is-active --quiet mariadb
  systemctl is-active --quiet syslog-ng
  systemctl is-active --quiet netlog-manager
  systemctl is-active --quiet apache2
  curl -fsS --max-time 10 http://127.0.0.1:8080/ >/dev/null
fi
printf 'OK\n'
