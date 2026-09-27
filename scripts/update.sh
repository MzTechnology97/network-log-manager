#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
load_install_state
cd "$REPO_ROOT"

old="$(git rev-parse HEAD)"
log "Current revision: $old"
git fetch --tags --prune origin

case "${UPDATE_CHANNEL:-stable}" in
 stable)
   target="$(git tag --list 'v[0-9]*' --sort=-v:refname | grep -Ev -- '-(rc|beta|alpha)' | head -n1)"
   ;;
 candidate)
   target="$(git tag --list 'v*-rc*' --sort=-v:refname | head -n1)"
   ;;
 development) target="origin/main";;
 *) die "Unknown update channel.";;
esac
[[ -n "$target" ]] || die "No release is available for channel ${UPDATE_CHANNEL:-stable}."
target_commit="$(git rev-parse "$target^{commit}")"
if [[ "$target_commit" == "$old" ]]; then
  log "Already up to date."
  exit 0
fi

backup_path="$("$ROOT/scripts/backup.sh" | tail -n1)"
log "Pre-update backup: $backup_path"

git checkout --detach "$target_commit"

if ! "$ROOT/scripts/migrate.sh"; then
  log "Migration failed. Returning code to $old. Database migrations are forward-only."
  git checkout --detach "$old"
  exit 1
fi

if [[ "$MODE" == docker ]]; then
  docker compose pull
  docker compose up -d --remove-orphans
else
  rsync -a --delete "$ROOT/app/" /opt/netlog-manager/app/
  rsync -a --delete "$ROOT/templates/" /opt/netlog-manager/templates/
  install -m 0755 "$ROOT/create-admin.py" /opt/netlog-manager/create-admin.py
  install -m 0644 "$ROOT/requirements.txt" /opt/netlog-manager/requirements.txt
  /opt/netlog-manager/venv/bin/pip install --disable-pip-version-check -r /opt/netlog-manager/requirements.txt
  for unit in "$ROOT"/systemd/*; do
    [[ -f "$unit" ]] || continue
    install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
  done
  install -d -m 0755 /opt/netlog-manager/scripts
  rsync -a --delete "$ROOT/scripts/" /opt/netlog-manager/scripts/
  systemctl daemon-reload
  systemctl restart netlog-manager.service
fi

if ! "$ROOT/scripts/healthcheck.sh"; then
  log "Health check failed; returning application code to $old. Database migrations are not automatically reversed."
  git checkout --detach "$old"
  if [[ "$MODE" == docker ]]; then
    docker compose up -d --remove-orphans
  else
    rsync -a --delete "$ROOT/app/" /opt/netlog-manager/app/
    rsync -a --delete "$ROOT/templates/" /opt/netlog-manager/templates/
    systemctl restart netlog-manager.service
  fi
  exit 1
fi

printf '%s\n' "$target_commit" >"$STATE_DIR/current-revision"
chmod 0600 "$STATE_DIR/current-revision"
log "Update completed: $target_commit"
