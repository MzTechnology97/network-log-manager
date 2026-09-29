#!/usr/bin/env bash
set -Eeuo pipefail

mode="${1:-}"
STATE_DIR="${NETLOG_STATE_DIR:-/var/lib/netlog-manager}"
WORKER_DIR="$STATE_DIR/workers"
mkdir -p "$WORKER_DIR"

beat(){ date +%s >"$WORKER_DIR/$1.heartbeat"; }
run(){ /opt/netlog-manager/venv/bin/python -m "$@"; }

case "$mode" in
  archive-cache)
    while true; do
      beat archive-cache
      run app.archive_catalog --write || true
      run app.archive_cache_worker --limit 25 || true
      beat archive-cache
      sleep 3600
    done
    ;;
  export-worker)
    while true; do
      beat export-worker
      run app.export_worker || true
      beat export-worker
      sleep 15
    done
    ;;
  export-cleanup)
    while true; do
      beat export-cleanup
      run app.export_cleanup --delete || true
      beat export-cleanup
      sleep 86400
    done
    ;;
  monitor)
    while true; do
      beat monitor
      run app.monitoring || true
      beat monitor
      sleep "${MONITOR_INTERVAL_SECONDS:-60}"
    done
    ;;
  *) echo "Unknown native worker mode: $mode" >&2; exit 2 ;;
esac
