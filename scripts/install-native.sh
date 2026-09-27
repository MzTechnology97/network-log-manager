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
apt-get install -y mariadb-server syslog-ng syslog-ng-mod-sql libdbd-mysql python3 python3-venv python3-pip apache2 libapache2-mod-proxy-html zstd rsync openssl curl

id netlog >/dev/null 2>&1 || useradd --system --home /var/lib/netlog-manager --shell /usr/sbin/nologin netlog
install -d -o netlog -g netlog -m 0750 /opt/netlog-manager /var/lib/netlog-manager /var/log/netlog-manager /var/cache/netlog-manager/exports
install -d -o root -g netlog -m 0750 /etc/netlog-manager
install -d -m 0750 "$ARCHIVE_ROOT"

log "Bootstrapping database"
"$ROOT/scripts/migrate.sh" --bootstrap

log "Native skeleton installed. Application source packaging is the next migration step from the reference snapshot."
log "No existing database or archive data was deleted."
