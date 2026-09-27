#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
load_install_state
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"
[[ "${MODE:-}" == "docker" ]] || die "Docker log rotation requires MODE=docker."
cd "$REPO_ROOT"
if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(docker-compose); fi
ENV_FILE="$STATE_DIR/docker/.env"
MAX=7
stamp="$(date +%Y%m%d_%H%M%S)"
tmp="$STATE_DIR/docker/network.log.$stamp"
cid="$("${COMPOSE[@]}" --env-file "$ENV_FILE" ps -q syslog)"
[[ -n "$cid" ]] || die "syslog container is not running."
size="$(docker exec "$cid" sh -c 'stat -c %s /var/log/network.log 2>/dev/null || echo 0')"
[[ "$size" =~ ^[0-9]+$ ]] || die "Invalid live log size."
(( size > 0 )) || exit 0
docker exec "$cid" sh -c 'cp /var/log/network.log /var/log/network.log.rotate && : > /var/log/network.log'
docker cp "$cid:/var/log/network.log.rotate" "$tmp"
docker exec "$cid" rm -f /var/log/network.log.rotate
gzip -9 "$tmp"
find "$STATE_DIR/docker" -maxdepth 1 -type f -name 'network.log.*.gz' -printf '%T@ %p\n' | sort -nr | awk -v keep="$MAX" 'NR>keep {sub(/^[^ ]+ /,""); print}' | xargs -r rm -f --
log "Docker live log rotated: $tmp.gz"
