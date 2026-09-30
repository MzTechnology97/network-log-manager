#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
source "$ROOT/scripts/lib/db-root.sh"
require_root
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"

if ! systemctl is-active --quiet netlog-manager.service; then
  die "netlog-manager.service is not active."
fi

mysql_root_command
admin_count="$(
  "${MYSQL_ROOT[@]}" -N -B netlog_manager -e \
    "SELECT COUNT(*) FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id WHERE r.name='Administrator';"
)"

if ! [[ "$admin_count" =~ ^[0-9]+$ ]]; then
  die "Unable to determine whether an Administrator account already exists."
fi

if (( admin_count > 0 )); then
  log "Administrator account already exists; skipping initial account creation."
  exit 0
fi

echo
echo "=== Initial Network Log Manager Administrator ==="
echo "The password is entered interactively and is never written by this installer."
exec /opt/netlog-manager/venv/bin/python /opt/netlog-manager/create-admin.py
