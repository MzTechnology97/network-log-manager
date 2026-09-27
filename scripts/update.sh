#!/usr/bin/env bash
set -Eeuo pipefail
LAUNCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$LAUNCH_ROOT/scripts/lib/common.sh"
require_root

# Docker Compose gives exported shell variables precedence over --env-file.
# Remove credential/configuration names from the inherited environment so the
# installation-owned env file remains the single source of truth.
sanitize_compose_environment() {
  unset DB_ROOT_PASSWORD NETLOG_APP_PASSWORD NETLOG_READER_PASSWORD
  unset NETLOG_INGEST_PASSWORD NETLOG_MAINT_PASSWORD SECRET_KEY
  unset SYSLOG_PORT ARCHIVE_ROOT TZ HOSTNAME_FQDN TLS_DIR
}
load_install_state
if [[ "${AUTO_UPDATE:-0}" != "1" && "${1:-}" == "--automatic" ]]; then
  log "Automatic updates are disabled."
  exit 0
fi
REPO="${REPO_ROOT:?REPO_ROOT is not configured}"
cd "$REPO"

old="$(git rev-parse HEAD)"
log "Current revision: $old"
git fetch --tags --prune origin

case "${UPDATE_CHANNEL:-stable}" in
 stable) target="$(git tag --list 'v[0-9]*' --sort=-v:refname | grep -Ev -- '-(rc|beta|alpha)' | head -n1)";;
 candidate) target="$(git tag --list 'v*-rc*' --sort=-v:refname | head -n1)";;
 development) target="origin/main";;
 *) die "Unknown update channel.";;
esac
[[ -n "$target" ]] || die "No release is available for channel ${UPDATE_CHANNEL:-stable}."
target_commit="$(git rev-parse "$target^{commit}")"
[[ "$target_commit" != "$old" ]] || { log "Already up to date."; exit 0; }

if [[ "$MODE" == docker ]]; then
  sanitize_compose_environment
  backup_path="$("$REPO/scripts/backup-docker.sh" | tail -n1)"
else
  backup_path="$("$REPO/scripts/backup.sh" | tail -n1)"
fi
log "Pre-update backup: $backup_path"

rollback(){
  log "Rolling application code back to $old. Database migrations remain forward-only."
  git checkout --detach "$old"
  if [[ "$MODE" == docker ]]; then
    sanitize_compose_environment
    source "$CONFIG_DIR/install.env"
    cd "$REPO"
    if docker compose version >/dev/null 2>&1; then C=(docker compose); else C=(docker-compose); fi
    "${C[@]}" --env-file "$STATE_DIR/docker/.env" up -d --build --remove-orphans
  else
    rsync -a --delete "$REPO/app/" /opt/netlog-manager/app/
    rsync -a --delete "$REPO/templates/" /opt/netlog-manager/templates/
    systemctl restart netlog-manager.service
  fi
}
trap 'rc=$?; if (( rc != 0 )); then rollback || true; fi; exit $rc' EXIT

git checkout --detach "$target_commit"

if [[ "$MODE" == docker ]]; then
  sanitize_compose_environment
  "$REPO/scripts/migrate-docker.sh"
  sanitize_compose_environment
  source "$CONFIG_DIR/install.env"
  if docker compose version >/dev/null 2>&1; then C=(docker compose); else C=(docker-compose); fi
  "${C[@]}" --env-file "$STATE_DIR/docker/.env" build --pull
  "${C[@]}" --env-file "$STATE_DIR/docker/.env" up -d --remove-orphans
else
  "$REPO/scripts/migrate.sh"
  rsync -a --delete "$REPO/app/" /opt/netlog-manager/app/
  rsync -a --delete "$REPO/templates/" /opt/netlog-manager/templates/
  install -m 0755 "$REPO/create-admin.py" /opt/netlog-manager/create-admin.py
  install -m 0644 "$REPO/requirements.txt" /opt/netlog-manager/requirements.txt
  /opt/netlog-manager/venv/bin/pip install --disable-pip-version-check -r /opt/netlog-manager/requirements.txt
  for unit in "$REPO"/systemd/*; do [[ -f "$unit" ]] && install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"; done
  install -d -m 0755 /opt/netlog-manager/scripts
  rsync -a --delete "$REPO/scripts/" /opt/netlog-manager/scripts/
  systemctl daemon-reload
  systemctl restart netlog-manager.service
fi

"$REPO/scripts/healthcheck.sh"
printf '%s\n' "$target_commit" >"$STATE_DIR/current-revision"
chmod 0600 "$STATE_DIR/current-revision"
trap - EXIT
log "Update completed: $target_commit"
