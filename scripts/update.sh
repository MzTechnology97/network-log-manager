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

# update.sh is also installed outside the Git checkout under /opt. Existing
# deployments can therefore execute an older updater even after origin/main has
# moved forward. Re-exec the updater from the configured repository once per
# invocation so deployment/bootstrap logic always comes from the fetched tree.
if [[ "${NETLOG_UPDATER_REEXEC:-0}" != "1" ]]; then
  REPO="${REPO_ROOT:?REPO_ROOT is not configured}"
  if [[ -x "$REPO/scripts/update.sh" && "$(readlink -f "$0")" != "$(readlink -f "$REPO/scripts/update.sh")" ]]; then
    export NETLOG_UPDATER_REEXEC=1
    exec "$REPO/scripts/update.sh" "$@"
  fi
fi

if [[ "${AUTO_UPDATE:-0}" != "1" && "${1:-}" == "--automatic" ]]; then
  log "Automatic updates are disabled."
  exit 0
fi
REPO="${REPO_ROOT:?REPO_ROOT is not configured}"
cd "$REPO"

checkout_revision="$(git rev-parse HEAD)"
deployed_revision=""
if [[ -r "$STATE_DIR/current-revision" ]]; then
  deployed_revision="$(tr -d '[:space:]' <"$STATE_DIR/current-revision")"
fi
log "Checkout revision: $checkout_revision"
if [[ -n "$deployed_revision" ]]; then
  log "Deployed revision: $deployed_revision"
else
  log "Deployed revision: unknown (deployment will be applied)"
fi

git fetch --tags --prune origin

case "${UPDATE_CHANNEL:-stable}" in
 stable)
   target="$(git tag --list 'v[0-9]*' --sort=-v:refname | grep -Ev -- '-(rc|beta|alpha)' | head -n1 || true)"
   ;;
 candidate)
   target="$(git tag --list 'v*-rc*' --sort=-v:refname | head -n1 || true)"
   ;;
 development) target="origin/main";;
 *) die "Unknown update channel.";;
esac
[[ -n "$target" ]] || die "No release is available for channel ${UPDATE_CHANNEL:-stable}."
target_commit="$(git rev-parse "$target^{commit}")"
if [[ "$target_commit" == "$deployed_revision" ]]; then
  # Keep host-side deployment helpers self-healing even when application code
  # is already at the requested revision. This is important when an older
  # updater deployed the commit before a newly introduced systemd unit existed.
  if [[ "$MODE" == docker && ( ! -x /opt/netlog-manager/scripts/ops-agent.sh || ! -f /etc/systemd/system/netlog-ops-agent.service ) ]]; then
    log "Application is current but Operations watchdog is missing; repairing host integration."
    source "$CONFIG_DIR/install.env"
    install -d -o root -g 999 -m 0750 "$STATE_DIR/ops" "$STATE_DIR/ops/logs"
    install -m 0755 "$REPO/scripts/ops-agent.sh" /opt/netlog-manager/scripts/ops-agent.sh
    install -m 0644 "$REPO/systemd/netlog-ops-agent.service" /etc/systemd/system/netlog-ops-agent.service
    systemctl daemon-reload
    systemctl enable --now netlog-ops-agent.service
    log "Operations watchdog repaired."
  else
    log "Deployment already up to date: $target_commit"
  fi
  exit 0
fi

rollback_revision="$checkout_revision"
if [[ -n "$deployed_revision" ]] && git cat-file -e "$deployed_revision^{commit}" 2>/dev/null; then
  rollback_revision="$deployed_revision"
fi

if [[ "$MODE" == docker ]]; then
  # The application image runs as uid=10001 and its system group is gid=999.
  # Repair only the archive root so integrity probes can write temporary files;
  # do not recursively alter ownership of existing archived data.
  source "$CONFIG_DIR/install.env"
  install -d -o 10001 -g 999 -m 0750 "$ARCHIVE_ROOT"
  sanitize_compose_environment
  backup_path="$("$REPO/scripts/backup-docker.sh" | tail -n1)"
else
  source "$CONFIG_DIR/install.env"
  # Native app/monitor workers run as netlog and must be able to perform
  # storage integrity probes and archive writes at the configured root.
  install -d -o netlog -g netlog -m 0750 "$ARCHIVE_ROOT"
  backup_path="$("$REPO/scripts/backup.sh" | tail -n1)"
fi
log "Pre-update backup: $backup_path"

rollback(){
  log "Rolling application code back to $rollback_revision. Database migrations remain forward-only."
  git checkout --detach "$rollback_revision"
  if [[ "$MODE" == docker ]]; then
    sanitize_compose_environment
    source "$CONFIG_DIR/install.env"
    cd "$REPO"
    if docker compose version >/dev/null 2>&1; then C=(docker compose); else C=(docker-compose); fi
    "${C[@]}" --env-file "$STATE_DIR/docker/.env" up -d --build --remove-orphans
  else
    rsync -a --delete "$REPO/app/" /opt/netlog-manager/app/
    rsync -a --delete "$REPO/templates/" /opt/netlog-manager/templates/
    rsync -a --delete "$REPO/static/" /opt/netlog-manager/static/
    systemctl restart netlog-manager.service
  fi
}
trap 'rc=$?; if (( rc != 0 )); then rollback || true; fi; exit $rc' EXIT

git checkout --detach "$target_commit"

if [[ "$MODE" == docker ]]; then
  sanitize_compose_environment
  "$REPO/scripts/migrate-docker.sh"
  bash "$REPO/scripts/normalize-log-timezone-docker.sh"
  sanitize_compose_environment
  source "$CONFIG_DIR/install.env"
  if docker compose version >/dev/null 2>&1; then C=(docker compose); else C=(docker-compose); fi
  "${C[@]}" --env-file "$STATE_DIR/docker/.env" build --pull
  "${C[@]}" --env-file "$STATE_DIR/docker/.env" up -d --remove-orphans
  install -d -o root -g 999 -m 0750 "$STATE_DIR/ops" "$STATE_DIR/ops/logs"
  install -m 0755 "$REPO/scripts/ops-agent.sh" /opt/netlog-manager/scripts/ops-agent.sh
  install -m 0644 "$REPO/systemd/netlog-ops-agent.service" /etc/systemd/system/netlog-ops-agent.service
  systemctl daemon-reload
  systemctl enable --now netlog-ops-agent.service
  systemctl restart netlog-ops-agent.service
else
  "$REPO/scripts/migrate.sh"
  source "$CONFIG_DIR/install.env"
  rsync -a --delete "$REPO/app/" /opt/netlog-manager/app/
  rsync -a --delete "$REPO/templates/" /opt/netlog-manager/templates/
  rsync -a --delete "$REPO/static/" /opt/netlog-manager/static/
  install -m 0755 "$REPO/create-admin.py" /opt/netlog-manager/create-admin.py
  install -m 0644 "$REPO/requirements.txt" /opt/netlog-manager/requirements.txt
  /opt/netlog-manager/venv/bin/pip install --disable-pip-version-check -r /opt/netlog-manager/requirements.txt
  for unit in "$REPO"/systemd/*; do [[ -f "$unit" ]] && install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"; done
  install -d -m 0755 /opt/netlog-manager/scripts
  rsync -a --delete "$REPO/scripts/" /opt/netlog-manager/scripts/
  chmod 0755 /opt/netlog-manager/scripts/native-worker.sh /opt/netlog-manager/scripts/native-ops-agent.sh /opt/netlog-manager/scripts/archive-native.sh /opt/netlog-manager/scripts/retention-native.sh
  install -m 0644 "$REPO/logrotate/netlog-manager" /etc/logrotate.d/netlog-manager
  install -d -o netlog -g netlog -m 0750 "$STATE_DIR/workers"
  install -d -o root -g netlog -m 0750 "$STATE_DIR/ops" "$STATE_DIR/ops/logs"
  python3 - /opt/netlog-manager/config/app.env "$TZ" "$SYSLOG_PORT" <<'PY'
from pathlib import Path
import sys
path=Path(sys.argv[1]); tz=sys.argv[2]; port=sys.argv[3]
wanted={
    "NETLOG_TIMEZONE":tz, "TZ":tz,
    "SYSLOG_LISTENER_HOST":"127.0.0.1", "SYSLOG_LISTENER_PORT":port,
    "OPS_LOG_ROOT":"/var/lib/netlog-manager/ops/logs",
    "OPS_STATUS_FILE":"/var/lib/netlog-manager/ops/status.tsv",
}
lines=path.read_text().splitlines()
seen=set(); out=[]
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        key=line.split("=",1)[0].strip()
        if key in wanted:
            out.append(key+"="+wanted[key]); seen.add(key); continue
    out.append(line)
for key,value in wanted.items():
    if key not in seen: out.append(key+"="+value)
path.write_text("\n".join(out)+"\n")
PY
  chown root:netlog /opt/netlog-manager/config/app.env
  chmod 0640 /opt/netlog-manager/config/app.env
  systemctl disable --now netlog-archive-cache.timer netlog-export-worker.path netlog-export-cleanup.timer >/dev/null 2>&1 || true
  systemctl daemon-reload
  systemctl enable netlog-manager.service netlog-monitor.service netlog-native-ops-agent.service \
    netlog-archive-cache.service netlog-export-worker.service netlog-export-cleanup.service \
    netlog-native-archive.timer netlog-native-retention.timer
  systemctl restart netlog-manager.service netlog-monitor.service netlog-archive-cache.service \
    netlog-export-worker.service netlog-export-cleanup.service netlog-native-ops-agent.service
  systemctl restart netlog-native-archive.timer netlog-native-retention.timer
fi

"$REPO/scripts/healthcheck.sh"
printf '%s\n' "$target_commit" >"$STATE_DIR/current-revision"
chmod 0600 "$STATE_DIR/current-revision"
trap - EXIT
log "Update completed: $target_commit"
