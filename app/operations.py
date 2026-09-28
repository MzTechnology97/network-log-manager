import os
import socket
import time
from datetime import datetime
from pathlib import Path

from .database import app_db

STATE_DIR = Path("/var/lib/netlog-manager/workers")
LIVE_LOG = Path(os.environ.get("LIVE_LOG", "/var/log/network.log"))
OPS_LOG_ROOT = Path(os.environ.get("OPS_LOG_ROOT", "/var/lib/netlog-manager/ops-host/logs"))
OPS_STATUS_FILE = Path(os.environ.get("OPS_STATUS_FILE", "/var/lib/netlog-manager/ops-host/status.tsv"))

WORKERS = {
    "monitor": 180,
    "export-worker": 60,
    "archive-cache": 7500,
    "export-cleanup": 172800,
}
LOG_SOURCES = {
    "network": LIVE_LOG,
    "ops-agent": OPS_LOG_ROOT / "ops-agent.log",
    "mariadb": OPS_LOG_ROOT / "db.log",
    "app": OPS_LOG_ROOT / "app.log",
    "proxy": OPS_LOG_ROOT / "proxy.log",
    "syslog-ng": OPS_LOG_ROOT / "syslog.log",
    "monitor": OPS_LOG_ROOT / "monitor.log",
    "archive-cache": OPS_LOG_ROOT / "archive-cache.log",
    "export-worker": OPS_LOG_ROOT / "export-worker.log",
    "export-cleanup": OPS_LOG_ROOT / "export-cleanup.log",
}
SEVERITIES = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _state(name, status, detail, *, checked_at=None):
    return {"name": name, "status": status, "detail": detail,
            "checked_at": checked_at or datetime.now().isoformat(timespec="seconds")}


def _agent_states():
    rows = {}
    try:
        for line in OPS_STATUS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            name, status, stamp, detail = parts
            age = max(0, int(time.time() - int(stamp)))
            if age > 90:
                status, detail = "DOWN", f"watchdog telemetry stale ({age}s)"
            rows[name] = _state(name, status if status in {"HEALTHY","DEGRADED","DOWN"} else "DOWN", detail)
    except (OSError, ValueError):
        pass
    return rows


def service_health():
    out = []
    agent = _agent_states()
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
            status = "HEALTHY" if age <= maximum_age else ("DEGRADED" if age <= maximum_age * 2 else "DOWN")
            out.append(_state(worker, status, f"heartbeat {age}s ago"))
        except Exception as exc:
            out.append(_state(worker, "DOWN", f"heartbeat unavailable: {exc}"[:240]))

    # Components not directly testable from the unprivileged web container use
    # read-only telemetry produced by the host watchdog.
    for name in ("app", "proxy"):
        if name in agent:
            out.append(agent[name])
    if not agent:
        out.append(_state("ops-agent", "DEGRADED", "host watchdog telemetry unavailable"))
    else:
        out.append(_state("ops-agent", "HEALTHY", "host watchdog active"))
    return out


def log_sources():
    return [{"id": key, "label": key, "available": path.is_file()} for key, path in LOG_SOURCES.items()]


def _matches_severity(line, severity):
    if not severity:
        return True
    severity = severity.upper()
    if severity not in SEVERITIES:
        return True
    upper = line.upper()
    aliases = {
        "WARNING": ("WARNING", " WARN ", "[WARN]", "LEVEL=WARNING"),
        "ERROR": ("ERROR", " ERR ", "[ERROR]", "LEVEL=ERROR", "FAILED"),
        "CRITICAL": ("CRITICAL", " FATAL ", "[CRITICAL]", "PANIC"),
        "DEBUG": ("DEBUG", "[DEBUG]"),
        "INFO": ("INFO", "[INFO]"),
    }
    return any(token in upper for token in aliases[severity])


def read_live_log(limit=200, query="", source="network", severity=""):
    limit = max(1, min(int(limit), 1000))
    path = LOG_SOURCES.get(source)
    if path is None:
        raise ValueError("Unknown log source")
    if not path.is_file():
        return []
    # Files are bounded by the host watchdog/normal log rotation; still read
    # only a tail window to avoid unbounded web requests.
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()[-max(limit * 6, limit):]
    if query:
        q = query.casefold()
        lines = [line for line in lines if q in line.casefold()]
    lines = [line for line in lines if _matches_severity(line, severity)]
    return [line.rstrip("\n") for line in lines[-limit:]]
