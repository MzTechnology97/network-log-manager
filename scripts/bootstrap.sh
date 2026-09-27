#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root

if [[ -s "$STATE_FILE" && -s "$CONFIG_DIR/install.env" && -s "$CONFIG_DIR/secrets.env" ]]; then
  load_install_state
  echo "=== $PRODUCT maintenance ==="
  echo "1) Resume / repair installation"
  echo "2) Reset Administrator credentials"
  echo "3) Check and install available update"
  echo "4) Exit"
  read -r -p "Action [1/2/3/4]: " maintenance_choice
  case "$maintenance_choice" in
    1) ;;
    2)
      source "$CONFIG_DIR/install.env"
      if [[ "$MODE" == docker ]]; then
        source "$CONFIG_DIR/secrets.env"
        cd "$REPO_ROOT"
        if docker compose version >/dev/null 2>&1; then C=(docker compose); else C=(docker-compose); fi
        exec "${C[@]}" --env-file "$STATE_DIR/docker/.env" exec app python create-admin.py --reset
      else
        exec /opt/netlog-manager/venv/bin/python /opt/netlog-manager/create-admin.py --reset
      fi
      ;;
    3) exec "$REPO_ROOT/scripts/update.sh";;
    4) exit 0;;
    *) die "Invalid action.";;
  esac
fi

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
if [[ -s "$CONFIG_DIR/secrets.env" ]]; then
  echo "Existing service credentials found; reusing them for installation recovery."
  # shellcheck disable=SC1090
  source "$CONFIG_DIR/secrets.env"
else
  echo "Service credentials are generated automatically and stored only in root-readable local configuration."
  DB_ROOT_PASSWORD=""
  if [[ "$MODE" == docker ]]; then
    DB_ROOT_PASSWORD="$(random_secret 32)"
  fi
  NETLOG_APP_PASSWORD="$(random_secret 32)"
  NETLOG_READER_PASSWORD="$(random_secret 32)"
  NETLOG_INGEST_PASSWORD="$(random_secret 32)"
  NETLOG_MAINT_PASSWORD="$(random_secret 32)"
  SECRET_KEY="$(random_secret 48)"
fi

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
