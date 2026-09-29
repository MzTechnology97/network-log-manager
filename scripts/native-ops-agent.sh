#!/usr/bin/env bash
set -Eeuo pipefail

STATE_DIR="${NETLOG_STATE_DIR:-/var/lib/netlog-manager}"
OPS_DIR="$STATE_DIR/ops"
LOG_DIR="$OPS_DIR/logs"
STATUS_FILE="$OPS_DIR/status.tsv"
FAIL_DIR="$OPS_DIR/failures"
COOLDOWN_DIR="$OPS_DIR/cooldown"
INTERVAL="${NETLOG_OPS_INTERVAL:-15}"
FAIL_LIMIT="${NETLOG_OPS_FAIL_LIMIT:-3}"
COOLDOWN="${NETLOG_OPS_RESTART_COOLDOWN:-300}"

declare -A UNITS=(
  [db]=mariadb.service
  [app]=netlog-manager.service
  [proxy]=apache2.service
  [syslog]=syslog-ng.service
  [monitor]=netlog-monitor.service
  [archive-cache]=netlog-archive-cache.service
  [export-worker]=netlog-export-worker.service
  [export-cleanup]=netlog-export-cleanup.service
)
SERVICES=(db app proxy syslog monitor archive-cache export-worker export-cleanup)

mkdir -p "$LOG_DIR" "$FAIL_DIR" "$COOLDOWN_DIR"
chown root:netlog "$OPS_DIR" "$LOG_DIR"
chmod 0750 "$OPS_DIR" "$LOG_DIR"
chmod 0700 "$FAIL_DIR" "$COOLDOWN_DIR"

probe(){
  local name="$1" unit="${UNITS[$1]}" state
  state="$(systemctl is-active "$unit" 2>/dev/null || true)"
  if [[ "$state" != active ]]; then printf 'DOWN\t%s is %s' "$unit" "${state:-unknown}"; return; fi
  case "$name" in
    app)
      curl -fsS --max-time 3 http://127.0.0.1:8080/health >/dev/null 2>&1 ||
        { printf 'DOWN\tHTTP health probe failed'; return; }
      ;;
    db)
      mariadb-admin --protocol=socket -uroot ping --silent >/dev/null 2>&1 ||
        { printf 'DOWN\tMariaDB ping failed'; return; }
      ;;
  esac
  printf 'HEALTHY\trunning'
}

restart_if_needed(){
  local name="$1" status="$2" unit="${UNITS[$1]}" count now last
  [[ "$status" == DOWN ]] || { echo 0 >"$FAIL_DIR/$name"; return; }
  count="$(cat "$FAIL_DIR/$name" 2>/dev/null || echo 0)"; count=$((count+1)); echo "$count" >"$FAIL_DIR/$name"
  (( count >= FAIL_LIMIT )) || return 0
  now="$(date +%s)"; last="$(cat "$COOLDOWN_DIR/$name" 2>/dev/null || echo 0)"
  (( now-last >= COOLDOWN )) || return 0
  echo "$now" >"$COOLDOWN_DIR/$name"
  if systemctl restart "$unit"; then echo 0 >"$FAIL_DIR/$name"; fi
}

while true; do
  tmp="$STATUS_FILE.tmp"
  : >"$tmp"
  for name in "${SERVICES[@]}"; do
    IFS="$(printf '\t')" read -r status detail < <(probe "$name") || true
    [[ -n "${status:-}" ]] || { status=DOWN; detail="state probe returned no data"; }
    printf '%s\t%s\t%s\t%s\n' "$name" "$status" "$(date +%s)" "$detail" >>"$tmp"
    journalctl -u "${UNITS[$name]}" -n 400 --no-pager -o short-iso >"$LOG_DIR/$name.log" 2>&1 || true
    restart_if_needed "$name" "$status"
  done
  mv "$tmp" "$STATUS_FILE"
  chown root:netlog "$STATUS_FILE" "$LOG_DIR"/*.log 2>/dev/null || true
  chmod 0640 "$STATUS_FILE" "$LOG_DIR"/*.log 2>/dev/null || true
  sleep "$INTERVAL"
done
