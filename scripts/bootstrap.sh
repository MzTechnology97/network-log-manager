#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
require_root

echo "=== $PRODUCT guided installation ==="
echo "1) Docker Compose"
echo "2) Native Debian installation"
read -r -p "Installation mode [1/2]: " choice
case "$choice" in 1) MODE=docker;; 2) MODE=native;; *) die "Invalid installation mode.";; esac

prompt_default HOSTNAME_FQDN "Web hostname" "netlog-manager.local"
prompt_default SYSLOG_PORT "Syslog UDP/TCP port" "5514"
prompt_default TZ "Timezone" "Europe/Rome"
prompt_default ARCHIVE_ROOT "Archive directory" "/archive/mikrotik"

echo
echo "Database credentials. Passwords are stored only in root-readable local configuration."
prompt_secret DB_ROOT_PASSWORD "MariaDB root password"
prompt_secret NETLOG_APP_PASSWORD "netlog_app database password"
prompt_secret NETLOG_READER_PASSWORD "netlog_reader database password"
prompt_secret NETLOG_INGEST_PASSWORD "netlog_ingest database password"
prompt_secret NETLOG_MAINT_PASSWORD "netlog_maintenance database password"
SECRET_KEY="$(random_secret 48)"

install -d -m 0750 "$CONFIG_DIR" "$STATE_DIR" "$BACKUP_DIR" "$ARCHIVE_ROOT"
cat >"$CONFIG_DIR/install.env" <<EOF
MODE=$MODE
HOSTNAME_FQDN=$HOSTNAME_FQDN
SYSLOG_PORT=$SYSLOG_PORT
TZ=$TZ
ARCHIVE_ROOT=$ARCHIVE_ROOT
EOF
chmod 0600 "$CONFIG_DIR/install.env"

cat >"$CONFIG_DIR/secrets.env" <<EOF
DB_ROOT_PASSWORD=$DB_ROOT_PASSWORD
NETLOG_APP_PASSWORD=$NETLOG_APP_PASSWORD
NETLOG_READER_PASSWORD=$NETLOG_READER_PASSWORD
NETLOG_INGEST_PASSWORD=$NETLOG_INGEST_PASSWORD
NETLOG_MAINT_PASSWORD=$NETLOG_MAINT_PASSWORD
SECRET_KEY=$SECRET_KEY
EOF
chmod 0600 "$CONFIG_DIR/secrets.env"

save_install_state <<EOF
MODE=$MODE
REPO_ROOT=$ROOT
UPDATE_CHANNEL=stable
AUTO_UPDATE=0
EOF

case "$MODE" in
 docker) exec "$ROOT/scripts/install-docker.sh";;
 native) exec "$ROOT/scripts/install-native.sh";;
esac
