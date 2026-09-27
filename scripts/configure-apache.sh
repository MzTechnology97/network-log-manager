#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
source "$CONFIG_DIR/install.env"

install -d -o root -g root -m 0700 /etc/ssl/netlog-manager
CERT=/etc/ssl/netlog-manager/netlog-manager.crt
KEY=/etc/ssl/netlog-manager/netlog-manager.key

if [[ ! -s "$CERT" || ! -s "$KEY" ]]; then
  log "Generating local self-signed TLS certificate"
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825     -subj "/CN=$HOSTNAME_FQDN"     -addext "subjectAltName=DNS:$HOSTNAME_FQDN"     -keyout "$KEY" -out "$CERT"
  chmod 0600 "$KEY"
  chmod 0644 "$CERT"
fi

python3 - "$ROOT/apache/netlog-manager.conf.template" /etc/apache2/sites-available/netlog-manager.conf "$HOSTNAME_FQDN" <<'PY'
import pathlib, sys
src, dst, host = sys.argv[1:]
data = pathlib.Path(src).read_text()
data = data.replace("@@HOSTNAME_FQDN@@", host)
if "@@" in data:
    raise SystemExit("Unresolved Apache template placeholder")
pathlib.Path(dst).write_text(data)
PY

a2enmod ssl proxy proxy_http headers rewrite >/dev/null
a2ensite netlog-manager.conf >/dev/null
a2dissite 000-default.conf default-ssl.conf >/dev/null 2>&1 || true
apache2ctl configtest
systemctl reload apache2
log "Apache HTTPS reverse proxy configured for $HOSTNAME_FQDN"
log "HSTS is intentionally disabled until a trusted certificate is installed."
