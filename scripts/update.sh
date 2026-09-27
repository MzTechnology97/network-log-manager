#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
load_install_state
cd "$REPO_ROOT"

old="$(git rev-parse HEAD)"
log "Current revision: $old"
"$ROOT/scripts/backup.sh" >/dev/null

git fetch --tags origin
case "${UPDATE_CHANNEL:-stable}" in
 stable)
   target="$(git tag --list 'v[0-9]*' --sort=-v:refname | head -n1)"
   [[ -n "$target" ]] || die "No stable release tag available."
   ;;
 candidate)
   target="$(git tag --list 'v*-rc*' --sort=-v:refname | head -n1)"
   [[ -n "$target" ]] || die "No candidate release tag available."
   ;;
 development) target="origin/main";;
 *) die "Unknown update channel.";;
esac

git checkout --detach "$target"
"$ROOT/scripts/migrate.sh"

if [[ "$MODE" == docker ]]; then
  docker compose pull
  docker compose up -d --remove-orphans
else
  systemctl daemon-reload
  systemctl restart netlog-manager.service
fi

if ! "$ROOT/scripts/healthcheck.sh"; then
  log "Health check failed; returning application code to $old. Database migrations are forward-only and are not automatically reversed."
  git checkout --detach "$old"
  [[ "$MODE" == docker ]] && docker compose up -d --remove-orphans || systemctl restart netlog-manager.service
  exit 1
fi
log "Update completed: $(git rev-parse HEAD)"
