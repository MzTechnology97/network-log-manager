import os
import re
import shutil
import socket
import time
from datetime import datetime
from pathlib import Path

import psutil

from .config import ENV
from .database import app_db, syslog_db
from .monitoring import settings as operational_settings


LIVE_LOG = Path(
    ENV.get("LIVE_LOG", "/var/log/network.log")
)

ARCHIVE_ROOT = Path(
    ENV.get("ARCHIVE_ROOT", "/archive/mikrotik")
)

TABLE_RE = re.compile(
    r"^mikrotik_logs_\d{4}_\d{2}_\d{2}$"
)


_rate_state = {
    "time": None,
    "id": None,
    "table": None,
}


def human_bytes(value):
    if value is None:
        return None

    value = float(value)

    units = ["B", "KB", "MB", "GB", "TB"]

    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"

        value /= 1024


def current_table():
    return "mikrotik_logs_" + datetime.now().strftime(
        "%Y_%m_%d"
    )


def get_last_log_line():
    try:
        with LIVE_LOG.open("rb") as f:
            f.seek(0, os.SEEK_END)

            size = f.tell()

            if size == 0:
                return None

            pos = size - 1

            while pos > 0:
                f.seek(pos)
                if f.read(1) == b"\n" and pos != size - 1:
                    break
                pos -= 1

            if pos > 0:
                f.seek(pos + 1)
            else:
                f.seek(0)

            return f.readline().decode(
                "utf-8",
                errors="replace",
            ).strip()

    except (OSError, PermissionError):
        return None


def service_port(host, port, timeout=0.5):
    try:
        with socket.create_connection(
            (host, port),
            timeout=timeout,
        ):
            return True
    except OSError:
        return False


def archive_size():
    total = 0

    try:
        for root, dirs, files in os.walk(ARCHIVE_ROOT):
            for filename in files:
                try:
                    total += (
                        Path(root) / filename
                    ).stat().st_size
                except OSError:
                    pass

        return total

    except OSError:
        return None


def database_metrics():
    table = current_table()

    if not TABLE_RE.fullmatch(table):
        raise RuntimeError("Invalid table name")

    result = {
        "connected": False,
        "table": table,
        "table_exists": False,
        "last_row": None,
        "estimated_today": None,
        "database_bytes": None,
        "tables_count": None,
        "index_ok": None,
    }

    conn = None

    try:
        conn = syslog_db()

        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()

            result["connected"] = True

            cur.execute(
                """
                SELECT
                    COUNT(*) AS tables_count,
                    COALESCE(
                        SUM(data_length + index_length),
                        0
                    ) AS database_bytes
                FROM information_schema.tables
                WHERE table_schema='syslogdb'
                """
            )

            storage = cur.fetchone()

            if storage:
                result["tables_count"] = int(
                    storage["tables_count"] or 0
                )
                result["database_bytes"] = int(
                    storage["database_bytes"] or 0
                )

            cur.execute(
                """
                SELECT COUNT(*) AS c
                FROM information_schema.tables
                WHERE table_schema='syslogdb'
                  AND table_name=%s
                """,
                (table,),
            )

            exists = int(cur.fetchone()["c"]) == 1
            result["table_exists"] = exists

            if not exists:
                return result

            cur.execute(
                """
                SELECT COUNT(*) AS c
                FROM information_schema.statistics
                WHERE table_schema='syslogdb'
                  AND table_name=%s
                  AND index_name='idx_nat_time'
                """,
                (table,),
            )

            result["index_ok"] = (
                int(cur.fetchone()["c"]) > 0
            )

            cur.execute(
                f"""
                SELECT
                    id,
                    timestamp,
                    source_ip,
                    source_port,
                    nat_source_ip,
                    nat_source_port,
                    dest_ip,
                    dest_port,
                    protocol
                FROM `{table}`
                ORDER BY id DESC
                LIMIT 1
                """
            )

            row = cur.fetchone()

            if row:
                result["last_row"] = row
                result["estimated_today"] = int(
                    row["id"]
                )

        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result

    finally:
        if conn:
            conn.close()


def calculate_rate(db):
    now = time.monotonic()

    row = db.get("last_row")

    if not row:
        return None

    current_id = int(row["id"])
    table = db["table"]

    previous_time = _rate_state["time"]
    previous_id = _rate_state["id"]
    previous_table = _rate_state["table"]

    _rate_state["time"] = now
    _rate_state["id"] = current_id
    _rate_state["table"] = table

    if (
        previous_time is None
        or previous_id is None
        or previous_table != table
    ):
        return None

    elapsed = now - previous_time

    if elapsed <= 0:
        return None

    delta = current_id - previous_id

    if delta < 0:
        return None

    return round(delta / elapsed, 1)


def collect_dashboard():
    now = datetime.now()

    try:
        stat = LIVE_LOG.stat()
        log_age = max(
            0,
            (now - datetime.fromtimestamp(
                stat.st_mtime
            )).total_seconds(),
        )
        log_size = stat.st_size
    except OSError:
        log_age = None
        log_size = None

    db = database_metrics()

    rate = calculate_rate(db)

    disk = shutil.disk_usage("/")

    memory = psutil.virtual_memory()

    load1, load5, load15 = os.getloadavg()

    cfg = operational_settings()
    archive_path = Path(cfg.get('external_storage_path') or str(ARCHIVE_ROOT))
    try:
        archive_disk = shutil.disk_usage(archive_path)
    except OSError:
        archive_disk = None
    archive_bytes = archive_size()
    open_alerts = 0
    active_alerts = []
    try:
        alert_conn = app_db()
        with alert_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM system_alerts WHERE resolved_at IS NULL")
            open_alerts = int(cur.fetchone()['c'])
            cur.execute("SELECT severity,title,message,last_seen_at FROM system_alerts WHERE resolved_at IS NULL ORDER BY last_seen_at DESC LIMIT 5")
            active_alerts = cur.fetchall()
        alert_conn.close()
    except Exception:
        pass

    listener_host = ENV.get("SYSLOG_LISTENER_HOST", "127.0.0.1")
    listener_port = int(ENV.get("SYSLOG_LISTENER_PORT", "5514"))
    listener_ok = service_port(listener_host, listener_port)

    has_received_logs = bool(db.get("last_row"))
    ingestion_fresh = bool(
        has_received_logs
        and log_age is not None
        and log_age < 60
    )

    services_ok = bool(
        db.get("connected")
        and listener_ok
        and db.get("table_exists")
    )

    pipeline_ok = bool(services_ok and ingestion_fresh)
    if not services_ok:
        pipeline_state = "unavailable"
    elif not has_received_logs:
        pipeline_state = "waiting"
    elif ingestion_fresh:
        pipeline_state = "operational"
    else:
        pipeline_state = "stale"

    return {
        "generated_at": now.isoformat(),
        "pipeline": {
            "ok": pipeline_ok,
            "state": pipeline_state,
            "services_ok": services_ok,
            "has_received_logs": has_received_logs,
            "log_age_seconds": (
                round(log_age, 1)
                if log_age is not None
                else None
            ),
        },
        "services": {
            "mariadb": bool(db.get("connected")),
            "syslog_listener": listener_ok,
            "syslog_listener_host": listener_host,
            "syslog_listener_port": listener_port,
        },
        "ingestion": {
            "logs_per_second": rate,
            "estimated_today": db.get(
                "estimated_today"
            ),
            "last_log": get_last_log_line(),
            "live_log_bytes": log_size,
        },
        "database": {
            "table": db.get("table"),
            "table_exists": db.get(
                "table_exists"
            ),
            "index_ok": db.get("index_ok"),
            "tables_count": db.get(
                "tables_count"
            ),
            "bytes": db.get(
                "database_bytes"
            ),
            "human_size": human_bytes(
                db.get("database_bytes")
            ),
            "last_row": db.get("last_row"),
            "error": db.get("error"),
        },
        "system": {
            "cpu_percent": psutil.cpu_percent(
                interval=None
            ),
            "memory_percent": memory.percent,
            "memory_used": (
                memory.total - memory.available
            ),
            "memory_total": memory.total,
            "load1": round(load1, 2),
            "load5": round(load5, 2),
            "load15": round(load15, 2),
            "uptime_seconds": int(
                time.time() - psutil.boot_time()
            ),
        },
        "storage": {
            "disk_total": disk.total,
            "disk_used": disk.used,
            "disk_free": disk.free,
            "disk_percent": round(
                disk.used / disk.total * 100,
                1,
            ),
            "archive_bytes": archive_bytes,
            "archive_human": human_bytes(
                archive_bytes
            ),
            "archive_path": str(archive_path),
            "archive_disk_percent": round(archive_disk.used / archive_disk.total * 100, 1) if archive_disk and archive_disk.total else None,
            "open_alerts": open_alerts,
            "active_alerts": active_alerts,
            "remote_type": cfg.get("external_storage_type","LOCAL"),
            "remote_enabled": cfg.get("external_storage_enabled","0") == "1",
            "remote_test_status": cfg.get("external_storage_test_status","NOT_TESTED"),
            "remote_tested_at": cfg.get("external_storage_tested_at") or None,
        },
    }
