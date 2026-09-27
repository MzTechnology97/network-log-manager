#!/bin/sh
set -eu
case "${1:-}" in
  archive-cache)
    while true; do
      python -m app.archive_catalog --write || true
      python -m app.archive_cache_worker --limit 25 || true
      sleep 3600
    done
    ;;
  export-worker)
    while true; do
      python -m app.export_worker || true
      sleep 15
    done
    ;;
  export-cleanup)
    while true; do
      python -m app.export_cleanup --delete || true
      sleep 86400
    done
    ;;
  *) echo "Unknown worker mode" >&2; exit 2;;
esac
