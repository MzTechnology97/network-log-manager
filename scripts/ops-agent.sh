#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_DIR="${NETLOG_CONFIG_DIR:-/etc/netlog-manager}"
STATE_DIR="${NETLOG_STATE_DIR:-/var/lib/netlog-manager}"
OPS_DIR="$STATE_DIR/ops"
LOG_DIR="$OPS_DIR/logs"
STATUS_FILE="$OPS_DIR/status.tsv"
FAIL_DIR="$OPS_DIR/failures"
COOLDOWN_DIR="$OPS_DIR/cooldown"
INTERVAL="${NETLOG_OPS_INTERVAL:-15}"
FAIL_LIMIT="${NETLOG_OPS_FAIL_LIMIT:-3}"
COOLDOWN="${NETLOG_OPS_RESTART_COOLDOWN:-300}"
SERVICES=(db app proxy syslog monitor archive-cache export-worker export-cleanup)

mkdir -p "$LOG_DIR" "$FAIL_DIR" "$COOLDOWN_DIR"
# Containers run as uid 10001, gid 999.  The web/monitor containers receive
# this tree read-only, so grant their group traversal/read access without
# making operational logs world-readable.
chown root:999 "$OPS_DIR" "$LOG_DIR"
chmod 0750 "$OPS_DIR" "$LOG_DIR"
chmod 0700 "$FAIL_DIR" "$COOLDOWN_DIR"

source "$CONFIG_DIR/install.env"
if docker compose version >/dev/null 2>&1; then C=(docker compose); else C=(docker-compose); fi
ENV_FILE="$STATE_DIR/docker/.env"
REPO="${REPO_ROOT:-/root/network-log-manager}"

cd "$REPO"

log_event(){
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" >>"$LOG_DIR/ops-agent.log"
  tail -n 1000 "$LOG_DIR/ops-agent.log" >"$LOG_DIR/.ops-agent.tmp" && mv "$LOG_DIR/.ops-agent.tmp" "$LOG_DIR/ops-agent.log"
}

container_state(){
  local service="$1" id state health
  id="$("${C[@]}" --env-file "$ENV_FILE" ps -q "$service" 2>/dev/null || true)"
  if [[ -z "$id" ]]; then printf 'DOWN\tcontainer missing'; return; fi
  state="$(docker inspect -f '{{.State.Status}}' "$id" 2>/dev/null || echo unknown)"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id" 2>/dev/null || echo unknown)"
  if [[ "$state" != running ]]; then printf 'DOWN\tcontainer %s' "$state"; return; fi
  case "$health" in
    unhealthy) printf 'DOWN\thealthcheck unhealthy';;
    starting) printf 'DEGRADED\thealthcheck starting';;
    *) printf 'HEALTHY\trunning';;
  esac
}

restart_if_needed(){
  local service="$1" status="$2" count now last
  [[ "$status" == DOWN ]] || { echo 0 >"$FAIL_DIR/$service"; return; }
  count="$(cat "$FAIL_DIR/$service" 2>/dev/null || echo 0)"; count=$((count+1)); echo "$count" >"$FAIL_DIR/$service"
  (( count >= FAIL_LIMIT )) || return 0
  now="$(date +%s)"; last="$(cat "$COOLDOWN_DIR/$service" 2>/dev/null || echo 0)"
  (( now-last >= COOLDOWN )) || return 0
  echo "$now" >"$COOLDOWN_DIR/$service"
  log_event "WARNING restart service=$service failures=$count"
  if "${C[@]}" --env-file "$ENV_FILE" restart "$service" >>"$LOG_DIR/ops-agent.log" 2>&1; then
    log_event "INFO restart-success service=$service"
    echo 0 >"$FAIL_DIR/$service"
  else
    log_event "ERROR restart-failed service=$service"
  fi
}

while true; do
  tmp="$STATUS_FILE.tmp"
  : >"$tmp"
  for service in "${SERVICES[@]}"; do
    IFS=$'\t' read -r status detail < <(container_state "$service")
    printf '%s\t%s\t%s\t%s\n' "$service" "$status" "$(date +%s)" "$detail" >>"$tmp"
    "${C[@]}" --env-file "$ENV_FILE" logs --no-color --timestamps --tail=400 "$service" >"$LOG_DIR/$service.log" 2>&1 || true
    restart_if_needed "$service" "$status"
  done
  mv "$tmp" "$STATUS_FILE"
  sleep "$INTERVAL"
done
