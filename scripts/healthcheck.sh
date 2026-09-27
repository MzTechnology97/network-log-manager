#!/usr/bin/env bash
set -Eeuo pipefail
curl -fsS --max-time 10 http://127.0.0.1:8080/ >/dev/null
systemctl is-active --quiet mariadb
printf 'OK\n'
