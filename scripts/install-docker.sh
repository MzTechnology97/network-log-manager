#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"

if ! command_exists docker; then
  log "Docker is not installed. Installing Debian Docker packages."
  apt-get update
  apt-get install -y docker.io docker-compose-plugin || apt-get install -y docker.io docker-compose
fi
systemctl enable --now docker

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command_exists docker-compose; then
  COMPOSE=(docker-compose)
else
  die "Docker Compose is unavailable."
fi

install -d -o root -g root -m 0750 "$STATE_DIR/docker"
install -d -o root -g root -m 0750 "$ARCHIVE_ROOT"
install -d -o root -g root -m 0750 "$STATE_DIR/docker/config" "$STATE_DIR/docker/tls"

cat >"$STATE_DIR/docker/.env" <<EOF
DB_ROOT_PASSWORD=$DB_ROOT_PASSWORD
NETLOG_APP_PASSWORD=$NETLOG_APP_PASSWORD
NETLOG_READER_PASSWORD=$NETLOG_READER_PASSWORD
NETLOG_INGEST_PASSWORD=$NETLOG_INGEST_PASSWORD
NETLOG_MAINT_PASSWORD=$NETLOG_MAINT_PASSWORD
SECRET_KEY=$SECRET_KEY
SYSLOG_PORT=$SYSLOG_PORT
ARCHIVE_ROOT=$ARCHIVE_ROOT
TZ=$TZ
HOSTNAME_FQDN=$HOSTNAME_FQDN
TLS_DIR=$STATE_DIR/docker/tls
EOF
chmod 0600 "$STATE_DIR/docker/.env"

if [[ ! -s "$STATE_DIR/docker/tls/netlog-manager.key" || ! -s "$STATE_DIR/docker/tls/netlog-manager.crt" ]]; then
  log "Generating initial self-signed Docker TLS certificate"
  openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 825 \
    -keyout "$STATE_DIR/docker/tls/netlog-manager.key" \
    -out "$STATE_DIR/docker/tls/netlog-manager.crt" \
    -subj "/CN=$HOSTNAME_FQDN" \
    -addext "subjectAltName=DNS:$HOSTNAME_FQDN"
  chmod 0600 "$STATE_DIR/docker/tls/netlog-manager.key"
  chmod 0644 "$STATE_DIR/docker/tls/netlog-manager.crt"
fi

log "Building and starting database"
cd "$ROOT"
"${COMPOSE[@]}" --env-file "$STATE_DIR/docker/.env" up -d --build db

log "Waiting for MariaDB"
for _ in {1..60}; do
  if "${COMPOSE[@]}" --env-file "$STATE_DIR/docker/.env" exec -T db mariadb-admin ping -uroot "-p$DB_ROOT_PASSWORD" --silent >/dev/null 2>&1; then break; fi
  sleep 2
done
"${COMPOSE[@]}" --env-file "$STATE_DIR/docker/.env" exec -T db mariadb-admin ping -uroot "-p$DB_ROOT_PASSWORD" --silent >/dev/null || die "MariaDB did not become ready."

log "Provisioning Docker databases and least-privilege users"
APP_PW="$NETLOG_APP_PASSWORD" READER_PW="$NETLOG_READER_PASSWORD" INGEST_PW="$NETLOG_INGEST_PASSWORD" MAINT_PW="$NETLOG_MAINT_PASSWORD" "${COMPOSE[@]}" --env-file "$STATE_DIR/docker/.env" exec -T db mariadb -uroot "-p$DB_ROOT_PASSWORD" <<SQL
CREATE DATABASE IF NOT EXISTS netlog_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS syslogdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'netlog_app'@'%';
ALTER USER 'netlog_app'@'%' IDENTIFIED BY '$NETLOG_APP_PASSWORD';
CREATE USER IF NOT EXISTS 'netlog_reader'@'%';
ALTER USER 'netlog_reader'@'%' IDENTIFIED BY '$NETLOG_READER_PASSWORD';
CREATE USER IF NOT EXISTS 'netlog_ingest'@'%';
ALTER USER 'netlog_ingest'@'%' IDENTIFIED BY '$NETLOG_INGEST_PASSWORD';
CREATE USER IF NOT EXISTS 'netlog_maintenance'@'%';
ALTER USER 'netlog_maintenance'@'%' IDENTIFIED BY '$NETLOG_MAINT_PASSWORD';
GRANT SELECT,INSERT,UPDATE,DELETE ON netlog_manager.* TO 'netlog_app'@'%';
GRANT SELECT ON syslogdb.* TO 'netlog_reader'@'%';
GRANT INSERT ON syslogdb.* TO 'netlog_ingest'@'%';
GRANT CREATE ON syslogdb.* TO 'netlog_maintenance'@'%';
SET GLOBAL event_scheduler=ON;
FLUSH PRIVILEGES;
SQL

log "Applying tracked Docker migrations"
"$ROOT/scripts/migrate-docker.sh"

log "Starting application and syslog ingestion"
"${COMPOSE[@]}" --env-file "$STATE_DIR/docker/.env" up -d --build

"$ROOT/scripts/healthcheck.sh"

echo
log "Create the initial Administrator account."
"${COMPOSE[@]}" --env-file "$STATE_DIR/docker/.env" exec app python create-admin.py

log "Docker installation completed."
