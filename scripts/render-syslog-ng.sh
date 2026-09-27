#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/lib/common.sh"
require_root
source "$CONFIG_DIR/install.env"
source "$CONFIG_DIR/secrets.env"

template="$ROOT/syslog-ng/syslog-ng.conf.template"
target="/etc/syslog-ng/syslog-ng.conf"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

python3 - "$template" "$tmp" "$SYSLOG_PORT" "localhost" "$NETLOG_INGEST_PASSWORD" <<'PY'
from pathlib import Path
import sys
src, dst, port, host, password = sys.argv[1:]
text = Path(src).read_text()
for key, value in {
    "@@SYSLOG_PORT@@": port,
    "@@DB_HOST@@": host,
    "@@NETLOG_INGEST_PASSWORD@@": password,
}.items():
    text = text.replace(key, value)
if "@@" in text:
    raise SystemExit("unresolved installer placeholder")
Path(dst).write_text(text)
PY
syslog-ng --syntax-only -f "$tmp"
install -o root -g root -m 0600 "$tmp" "$target"
systemctl reload syslog-ng
