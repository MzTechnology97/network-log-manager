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
apt-get update
apt-get install -y zstd util-linux openssl gzip

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
  if [[ "$HOSTNAME_FQDN" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    TLS_SAN="IP:$HOSTNAME_FQDN"
  else
    TLS_SAN="DNS:$HOSTNAME_FQDN"
  fi
  openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 825 \
    -keyout "$STATE_DIR/docker/tls/netlog-manager.key" \
    -out "$STATE_DIR/docker/tls/netlog-manager.crt" \
    -subj "/CN=$HOSTNAME_FQDN" \
    -addext "subjectAltName=$TLS_SAN"
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
sql_escape(){ printf '%s' "$1" | sed "s/'/''/g"; }
APP_PW="$(sql_escape "$NETLOG_APP_PASSWORD")"
READER_PW="$(sql_escape "$NETLOG_READER_PASSWORD")"
INGEST_PW="$(sql_escape "$NETLOG_INGEST_PASSWORD")"
MAINT_PW="$(sql_escape "$NETLOG_MAINT_PASSWORD")"
"${COMPOSE[@]}" --env-file "$STATE_DIR/docker/.env" exec -T db mariadb -uroot "-p$DB_ROOT_PASSWORD" <<SQL
CREATE DATABASE IF NOT EXISTS netlog_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS syslogdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'netlog_app'@'%';
ALTER USER 'netlog_app'@'%' IDENTIFIED BY '$APP_PW';
CREATE USER IF NOT EXISTS 'netlog_reader'@'%';
ALTER USER 'netlog_reader'@'%' IDENTIFIED BY '$READER_PW';
CREATE USER IF NOT EXISTS 'netlog_ingest'@'%';
ALTER USER 'netlog_ingest'@'%' IDENTIFIED BY '$INGEST_PW';
CREATE USER IF NOT EXISTS 'netlog_maintenance'@'%';
ALTER USER 'netlog_maintenance'@'%' IDENTIFIED BY '$MAINT_PW';
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

admin_count="$("${COMPOSE[@]}" --env-file "$STATE_DIR/docker/.env" exec -T db mariadb -uroot "-p$DB_ROOT_PASSWORD" -N -B netlog_manager -e "SELECT COUNT(*) FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id WHERE r.name='Administrator';" 2>/dev/null || echo 0)"
if [[ "$admin_count" == "0" ]]; then
  echo
  log "Create the initial Administrator account."
  "${COMPOSE[@]}" --env-file "$STATE_DIR/docker/.env" exec app python create-admin.py
else
  log "Administrator account already exists; skipping initial account creation."
fi

install -d -m 0755 /opt/netlog-manager/scripts/lib
install -m 0644 "$ROOT/scripts/lib/common.sh" /opt/netlog-manager/scripts/lib/common.sh
install -m 0644 "$ROOT/scripts/lib/db-root.sh" /opt/netlog-manager/scripts/lib/db-root.sh
install -m 0755 "$ROOT/scripts/archive-docker.sh" /opt/netlog-manager/scripts/archive-docker.sh
install -m 0755 "$ROOT/scripts/retention-docker.sh" /opt/netlog-manager/scripts/retention-docker.sh
install -m 0755 "$ROOT/scripts/rotate-live-log-docker.sh" /opt/netlog-manager/scripts/rotate-live-log-docker.sh
install -m 0755 "$ROOT/scripts/backup-docker.sh" /opt/netlog-manager/scripts/backup-docker.sh
install -m 0755 "$ROOT/scripts/restore-docker.sh" /opt/netlog-manager/scripts/restore-docker.sh
install -m 0755 "$ROOT/scripts/migrate-docker.sh" /opt/netlog-manager/scripts/migrate-docker.sh
install -m 0755 "$ROOT/scripts/healthcheck.sh" /opt/netlog-manager/scripts/healthcheck.sh
install -m 0755 "$ROOT/scripts/update.sh" /opt/netlog-manager/scripts/update.sh
install -m 0644 "$ROOT/systemd/netlog-update.service" /etc/systemd/system/netlog-update.service
install -m 0644 "$ROOT/systemd/netlog-update.timer" /etc/systemd/system/netlog-update.timer
install -m 0644 "$ROOT/systemd/netlog-docker-archive.service" /etc/systemd/system/netlog-docker-archive.service
install -m 0644 "$ROOT/systemd/netlog-docker-archive.timer" /etc/systemd/system/netlog-docker-archive.timer
install -m 0644 "$ROOT/systemd/netlog-docker-logrotate.service" /etc/systemd/system/netlog-docker-logrotate.service
install -m 0644 "$ROOT/systemd/netlog-docker-logrotate.timer" /etc/systemd/system/netlog-docker-logrotate.timer
install -m 0644 "$ROOT/systemd/netlog-docker-retention.service" /etc/systemd/system/netlog-docker-retention.service
install -m 0644 "$ROOT/systemd/netlog-docker-retention.timer" /etc/systemd/system/netlog-docker-retention.timer
systemctl daemon-reload
systemctl enable --now netlog-docker-archive.timer netlog-docker-logrotate.timer netlog-docker-retention.timer
systemctl disable --now netlog-update.timer >/dev/null 2>&1 || true

log "Docker installation completed."
