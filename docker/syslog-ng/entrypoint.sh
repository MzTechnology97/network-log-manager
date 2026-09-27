#!/bin/sh
set -eu
python3 - <<'PY'
import os
from pathlib import Path
src = Path("/etc/syslog-ng/syslog-ng.conf.template").read_text()
password = os.environ["NETLOG_INGEST_PASSWORD"]
escaped = password.replace("\\", "\\\\").replace('"', '\\"')
out = src.replace("@@NETLOG_INGEST_PASSWORD@@", escaped)
if "@@NETLOG_INGEST_PASSWORD@@" in out:
    raise SystemExit("unresolved syslog-ng password placeholder")
Path("/etc/syslog-ng/syslog-ng.conf").write_text(out)
PY
if [ "${1:-}" = "--check" ]; then
  exec syslog-ng -s -f /etc/syslog-ng/syslog-ng.conf
fi
exec syslog-ng -F --no-caps
