#!/bin/sh
set -eu
STATE_DIR=/var/lib/netlog-manager/workers
mkdir -p "$STATE_DIR"
beat(){ date +%s >"$STATE_DIR/$1.heartbeat"; }
case "${1:-}" in
  archive-cache)
    while true; do
      beat archive-cache
      python -m app.archive_catalog --write || true
      python -m app.archive_cache_worker --limit 25 || true
      beat archive-cache
      sleep 3600
    done
    ;;
  export-worker)
    while true; do beat export-worker; python -m app.export_worker || true; beat export-worker; sleep 15; done
    ;;
  export-cleanup)
    while true; do beat export-cleanup; python -m app.export_cleanup --delete || true; beat export-cleanup; sleep 86400; done
    ;;
  monitor)
    while true; do beat monitor; python -m app.monitoring || true; beat monitor; sleep "${MONITOR_INTERVAL_SECONDS:-60}"; done
    ;;
  *) echo "Unknown worker mode" >&2; exit 2;;
esac
