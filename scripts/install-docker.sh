#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
source "$CONFIG_DIR/install.env"

if ! command_exists docker; then
  log "Docker is not installed. Installing Debian Docker packages."
  apt-get update
  apt-get install -y docker.io docker-compose-plugin || apt-get install -y docker.io docker-compose
fi
systemctl enable --now docker

install -d -m 0750 "$STATE_DIR/docker" "$ARCHIVE_ROOT"
log "Docker runtime ready."
log "Compose application services will be enabled after application source extraction and container tests."
