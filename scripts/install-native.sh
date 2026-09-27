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
id netlog >/dev/null 2>&1 || useradd --system --home /var/lib/netlog-manager --shell /usr/sbin/nologin netlog

install -d -o netlog -g netlog -m 0750 /opt/netlog-manager /var/lib/netlog-manager /var/log/netlog-manager
install -d -o netlog -g netlog -m 0750 /var/cache/netlog-manager/history /var/cache/netlog-manager/exports
install -d -o root -g netlog -m 0750 /etc/netlog-manager
install -d -o root -g root -m 0750 "$ARCHIVE_ROOT"

log "Provisioning least-privilege database accounts"
"$ROOT/scripts/provision-db.sh"

log "Applying database migrations"
"$ROOT/scripts/migrate.sh" --bootstrap

log "Rendering syslog-ng configuration"
"$ROOT/scripts/render-syslog-ng.sh"

log "Persistent archive: $ARCHIVE_ROOT"
log "Disposable historical cache: /var/cache/netlog-manager/history"
log "Native platform foundation installed without deleting existing database or archive data."
log "Application source/systemd/Apache deployment is enabled after importing the sanitized reference source."
