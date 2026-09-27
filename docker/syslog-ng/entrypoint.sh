#!/bin/sh
set -eu
python3 - <<'PY'
import os
from pathlib import Path
src = Path("/etc/syslog-ng/syslog-ng.conf.template").read_text()
password = os.environ["NETLOG_INGEST_PASSWORD"]
escaped = password.replace("\\", "\\\\").replace('"', '\\"')
timezone = os.environ.get("TZ", "Europe/Rome")
if not timezone or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+-/" for ch in timezone):
    raise SystemExit("invalid TZ")
out = src.replace("@@NETLOG_INGEST_PASSWORD@@", escaped).replace("@@NETLOG_TIMEZONE@@", timezone)
if "@@NETLOG_INGEST_PASSWORD@@" in out or "@@NETLOG_TIMEZONE@@" in out:
    raise SystemExit("unresolved syslog-ng password placeholder")
Path("/etc/syslog-ng/syslog-ng.conf").write_text(out)
PY
if [ "${1:-}" = "--check" ]; then
  exec syslog-ng -s -f /etc/syslog-ng/syslog-ng.conf
fi
exec syslog-ng -F --no-caps
