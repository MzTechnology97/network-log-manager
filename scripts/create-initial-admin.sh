#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root

if ! systemctl is-active --quiet netlog-manager.service; then
  die "netlog-manager.service is not active."
fi

echo
echo "=== Initial Network Log Manager Administrator ==="
echo "The password is entered interactively and is never written by this installer."
exec /opt/netlog-manager/venv/bin/python /opt/netlog-manager/create-admin.py
