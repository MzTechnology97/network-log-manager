import os
import socket
import time
from datetime import datetime
from pathlib import Path

from .database import app_db, syslog_db

STATE_DIR = Path("/var/lib/netlog-manager/workers")
LIVE_LOG = Path(os.environ.get("LIVE_LOG", "/var/log/network.log"))

WORKERS = {
    "monitor": 180,
    "export-worker": 60,
    "archive-cache": 7500,
    "export-cleanup": 172800,
}


def _state(name, status, detail, *, checked_at=None):
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "checked_at": checked_at or datetime.now().isoformat(timespec="seconds"),
    }


def service_health():
    out = []
    try:
        conn = app_db()
        try:
            with conn.cursor() as cur:
                started = time.monotonic()
                cur.execute("SELECT 1 AS ok")
                cur.fetchone()
                elapsed = (time.monotonic() - started) * 1000
            status = "DEGRADED" if elapsed > 1000 else "HEALTHY"
            out.append(_state("MariaDB", status, f"query {elapsed:.0f} ms"))
        finally:
            conn.close()
    except Exception as exc:
        out.append(_state("MariaDB", "DOWN", str(exc)[:240]))

    try:
        host = os.environ.get("SYSLOG_LISTENER_HOST", "syslog")
        port = int(os.environ.get("SYSLOG_LISTENER_PORT", "5514"))
        started = time.monotonic()
        with socket.create_connection((host, port), timeout=2):
            pass
        elapsed = (time.monotonic() - started) * 1000
        out.append(_state("syslog-ng", "HEALTHY", f"TCP {host}:{port}, {elapsed:.0f} ms"))
    except Exception as exc:
        out.append(_state("syslog-ng", "DOWN", str(exc)[:240]))

    now = time.time()
    for worker, maximum_age in WORKERS.items():
        path = STATE_DIR / f"{worker}.heartbeat"
        try:
            stamp = int(path.read_text().strip())
            age = max(0, int(now - stamp))
            if age <= maximum_age:
                status = "HEALTHY"
            elif age <= maximum_age * 2:
                status = "DEGRADED"
            else:
                status = "DOWN"
            out.append(_state(worker, status, f"heartbeat {age}s ago"))
        except Exception as exc:
            out.append(_state(worker, "DOWN", f"heartbeat unavailable: {exc}"[:240]))

    return out


def read_live_log(limit=200, query=""):
    limit = max(1, min(int(limit), 1000))
    if not LIVE_LOG.is_file():
        return []
    # Bounded read: network.log is rotated by the existing maintenance job.
    with LIVE_LOG.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()[-max(limit * 4, limit):]
    if query:
        q = query.casefold()
        lines = [line for line in lines if q in line.casefold()]
    return [line.rstrip("\n") for line in lines[-limit:]]
