#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
load_install_state

if [[ "$MODE" == docker ]]; then
  cd "$REPO_ROOT"
  docker compose ps --status running >/dev/null
  curl -fsS --max-time 10 https://127.0.0.1/ -k >/dev/null
else
  systemctl is-active --quiet mariadb
  systemctl is-active --quiet syslog-ng
  systemctl is-active --quiet netlog-manager
  systemctl is-active --quiet apache2
  curl -fsS --max-time 10 http://127.0.0.1:8080/ >/dev/null
fi
printf 'OK\n'
