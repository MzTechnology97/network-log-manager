#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"

log "Installing native dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y mariadb-server syslog-ng syslog-ng-mod-sql libdbd-mysql python3 python3-venv python3-pip apache2 zstd rsync openssl curl logrotate

systemctl enable --now mariadb syslog-ng apache2

log "Enabling MariaDB event scheduler persistently"
install -d -m 0755 /etc/mysql/mariadb.conf.d
cat > /etc/mysql/mariadb.conf.d/60-netlog-manager.cnf <<'EOF'
[mysqld]
event_scheduler=ON
EOF
systemctl restart mariadb
mariadb --protocol=socket -uroot -N -e "SELECT @@event_scheduler" | grep -qx 'ON' || die "MariaDB event_scheduler did not start."
id netlog >/dev/null 2>&1 || useradd --system --home /var/lib/netlog-manager --shell /usr/sbin/nologin netlog

install -d -o netlog -g netlog -m 0750 /opt/netlog-manager /var/lib/netlog-manager /var/log/netlog-manager
install -d -o netlog -g netlog -m 0750 /var/cache/netlog-manager/history /var/cache/netlog-manager/exports
install -d -o root -g netlog -m 0750 /etc/netlog-manager
install -d -o root -g netlog -m 0750 "$ARCHIVE_ROOT"

log "Provisioning least-privilege database accounts"
"$ROOT/scripts/provision-db.sh"

log "Applying database migrations"
"$ROOT/scripts/migrate.sh" --bootstrap

log "Rendering syslog-ng configuration"
"$ROOT/scripts/render-syslog-ng.sh"

log "Deploying application source"
rsync -a --delete "$ROOT/app/" /opt/netlog-manager/app/
rsync -a --delete "$ROOT/templates/" /opt/netlog-manager/templates/
install -m 0755 "$ROOT/create-admin.py" /opt/netlog-manager/create-admin.py
install -m 0644 "$ROOT/requirements.txt" /opt/netlog-manager/requirements.txt

python3 -m venv /opt/netlog-manager/venv
/opt/netlog-manager/venv/bin/pip install --disable-pip-version-check --upgrade pip
/opt/netlog-manager/venv/bin/pip install --disable-pip-version-check -r /opt/netlog-manager/requirements.txt

install -d -o root -g netlog -m 0750 /opt/netlog-manager/config
cat > /opt/netlog-manager/config/app.env <<EOF
NETLOG_DB_HOST=127.0.0.1
NETLOG_DB_USER=netlog_app
NETLOG_DB_PASSWORD=$NETLOG_APP_PASSWORD
NETLOG_DB_NAME=netlog_manager
SYSLOG_DB_HOST=127.0.0.1
SYSLOG_DB_USER=netlog_reader
SYSLOG_DB_PASSWORD=$NETLOG_READER_PASSWORD
SYSLOG_DB_NAME=syslogdb
SECRET_KEY=$SECRET_KEY
ARCHIVE_ROOT=$ARCHIVE_ROOT
EXPORT_ROOT=/var/cache/netlog-manager/exports
LIVE_LOG=/var/log/network.log
EOF
chown root:netlog /opt/netlog-manager/config/app.env
chmod 0640 /opt/netlog-manager/config/app.env
chown -R root:root /opt/netlog-manager/app /opt/netlog-manager/templates /opt/netlog-manager/create-admin.py /opt/netlog-manager/requirements.txt
chmod 0755 /opt/netlog-manager
chmod 0644 /opt/netlog-manager/app/*.py

log "Installing application systemd units"
for unit in   netlog-manager.service   netlog-archive-cache.service netlog-archive-cache.timer   netlog-export-worker.service netlog-export-worker.path   netlog-export-cleanup.service netlog-export-cleanup.timer \
  netlog-update.service netlog-update.timer
do
  install -m 0644 "$ROOT/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable netlog-manager.service netlog-archive-cache.timer netlog-export-worker.path netlog-export-cleanup.timer
install -d -o root -g root -m 0755 /opt/netlog-manager/scripts
rsync -a --delete "$ROOT/scripts/" /opt/netlog-manager/scripts/
# Automatic updates remain disabled until a release channel has been explicitly enabled.

log "Validating application import"
/opt/netlog-manager/venv/bin/python -m compileall -q /opt/netlog-manager/app /opt/netlog-manager/create-admin.py

systemctl restart netlog-manager.service
systemctl start netlog-archive-cache.timer netlog-export-worker.path netlog-export-cleanup.timer

log "Persistent archive: $ARCHIVE_ROOT"
log "Disposable historical cache: /var/cache/netlog-manager/history"
log "Application runtime and background workers installed."
log "Configuring Apache HTTPS reverse proxy"
"$ROOT/scripts/configure-apache.sh"

log "Creating initial Administrator"
"$ROOT/scripts/create-initial-admin.sh"

log "Running installation health check"
"$ROOT/scripts/healthcheck.sh"

log "Native installation completed successfully."
