import csv
import os
import secrets
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pymysql

from .config import ENV
from .advanced_search import (
    AdvancedSearchError,
    archive_has_nat,
    get_archive_map,
    get_existing_tables,
    get_table_columns,
    optional_ipv4,
    optional_port,
    optional_protocol,
    tables_for_range,
)
from .archive_cache import cache_is_valid
from .archive_cache_search import (
    CACHE_ROOT,
    parse_cache_line,
    required_segments,
)


EXPORT_ROOT = Path(
    ENV.get(
        "EXPORT_ROOT",
        "/var/cache/netlog-manager/exports",
    )
)

EXPORT_PREFIX = "network-log-export"
EXPORT_MAX_AGE_HOURS = 24
EXPORT_MAX_ROWS = 2_000_000


class ExportError(Exception):
    pass


CSV_FIELDS = [
    "timestamp",
    "protocol",
    "source_ip",
    "source_port",
    "nat_source_ip",
    "nat_source_port",
    "dest_ip",
    "dest_port",
    "storage",
    "table",
]

CSV_HEADER = [
    "Timestamp",
    "Protocollo",
    "IP cliente",
    "Porta cliente",
    "IP pubblico NAT",
    "Porta pubblica NAT",
    "IP destinazione",
    "Porta destinazione",
    "Origine",
    "Tabella",
]


def ensure_export_root():
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)


def create_export_path():
    ensure_export_root()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    token = secrets.token_hex(4)
    filename = f"{EXPORT_PREFIX}_{timestamp}_{token}.csv"

    return EXPORT_ROOT / filename


def format_timestamp(value):
    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    return str(value)


def csv_row(row):
    storage = row.get(
        "_storage",
        row.get("storage", ""),
    )
    table = row.get(
        "_table",
        row.get("table", ""),
    )

    return [
        format_timestamp(row.get("timestamp")),
        row.get("protocol") or "",
        row.get("source_ip") or "",
        row.get("source_port") or "",
        row.get("nat_source_ip") or "",
        row.get("nat_source_port") or "",
        row.get("dest_ip") or "",
        row.get("dest_port") or "",
        storage,
        table,
    ]


def open_csv_writer(path):
    fh = open(
        path,
        "w",
        encoding="utf-8-sig",
        newline="",
    )

    writer = csv.writer(
        fh,
        delimiter=";",
        quoting=csv.QUOTE_MINIMAL,
    )
    writer.writerow(CSV_HEADER)

    return fh, writer


def remove_file_quietly(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _normalise_filters(
    source_ip=None,
    source_port=None,
    nat_source_ip=None,
    nat_source_port=None,
    dest_ip=None,
    dest_port=None,
    protocol=None,
):
    filters = {
        "source_ip": optional_ipv4(source_ip),
        "source_port": optional_port(source_port),
        "nat_source_ip": optional_ipv4(nat_source_ip),
        "nat_source_port": optional_port(nat_source_port),
        "dest_ip": optional_ipv4(dest_ip),
        "dest_port": optional_port(dest_port),
        "protocol": optional_protocol(protocol),
    }

    active = {
        key: value
        for key, value in filters.items()
        if value is not None
    }

    if not active:
        raise AdvancedSearchError(
            "Specificare almeno un filtro oltre all'intervallo temporale."
        )

    return filters, active


def _archive_rows(
    *,
    day,
    archive,
    start,
    end,
    source_ip=None,
    source_port=None,
    nat_source_ip=None,
    nat_source_port=None,
    dest_ip=None,
    dest_port=None,
    protocol=None,
):
    expected_rows = archive["row_count"]

    if expected_rows is not None:
        expected_rows = int(expected_rows)

    valid, reason = cache_is_valid(
        day.isoformat(),
        Path(archive["archive_path"]),
        expected_rows,
        archive["sha256"],
    )

    if not valid:
        raise ExportError(
            f"Cache storica non disponibile per "
            f"{day.isoformat()}: {reason}"
        )

    if (
        (nat_source_ip is not None or nat_source_port is not None)
        and not archive_has_nat(archive)
    ):
        raise ExportError(
            "I filtri NAT non sono disponibili nell'archivio "
            f"storico legacy ({day.isoformat()})."
        )

    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    query_start = max(start, day_start)
    query_end = min(end, day_end)

    start_key = query_start.isoformat(
        timespec="milliseconds"
    )[:23]
    end_key = query_end.isoformat(
        timespec="milliseconds"
    )[:23]

    protocol = protocol.upper() if protocol else None
    cache_dir = CACHE_ROOT / day.isoformat()

    for segment in required_segments(query_start, query_end):
        path = cache_dir / f"{segment}.tsv.zst"

        if not path.is_file():
            continue

        proc = subprocess.Popen(
            [
                "/usr/bin/zstd",
                "-dc",
                "--",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1024 * 1024,
        )

        try:
            assert proc.stdout is not None

            for line in proc.stdout:
                row = parse_cache_line(line)

                if row is None:
                    continue

                timestamp_local = row["timestamp"][:23]

                if timestamp_local < start_key:
                    continue

                # Do not break: the local 02:xx interval may appear twice at
                # the DST fallback inside concatenated zstd frames.
                if timestamp_local > end_key:
                    continue

                if (
                    source_ip is not None
                    and row["source_ip"] != source_ip
                ):
                    continue

                if (
                    source_port is not None
                    and row["source_port"] != source_port
                ):
                    continue

                if (
                    nat_source_ip is not None
                    and row["nat_source_ip"] != nat_source_ip
                ):
                    continue

                if (
                    nat_source_port is not None
                    and row["nat_source_port"] != nat_source_port
                ):
                    continue

                if (
                    dest_ip is not None
                    and row["dest_ip"] != dest_ip
                ):
                    continue

                if (
                    dest_port is not None
                    and row["dest_port"] != dest_port
                ):
                    continue

                if (
                    protocol is not None
                    and row["protocol"] != protocol
                ):
                    continue

                row["_storage"] = "archive"
                row["_table"] = archive["table_name"]
                yield row

        finally:
            if proc.stdout is not None:
                proc.stdout.close()

            if proc.poll() is None:
                proc.terminate()

                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def generate_advanced_export(
    *,
    start,
    end,
    source_ip=None,
    source_port=None,
    nat_source_ip=None,
    nat_source_port=None,
    dest_ip=None,
    dest_port=None,
    protocol=None,
    max_rows=EXPORT_MAX_ROWS,
    progress_callback=None,
    output_path=None,
):
    filters, active_filters = _normalise_filters(
        source_ip=source_ip,
        source_port=source_port,
        nat_source_ip=nat_source_ip,
        nat_source_port=nat_source_port,
        dest_ip=dest_ip,
        dest_port=dest_port,
        protocol=protocol,
    )

    source_ip = filters["source_ip"]
    source_port = filters["source_port"]
    nat_source_ip = filters["nat_source_ip"]
    nat_source_port = filters["nat_source_port"]
    dest_ip = filters["dest_ip"]
    dest_port = filters["dest_port"]
    protocol = filters["protocol"]

    requested_tables = tables_for_range(start, end)
    table_names = [table for _, table in requested_tables]
    archive_map = get_archive_map(
        [day.isoformat() for day, _ in requested_tables]
    )

    if output_path is None:
        path = create_export_path()
    else:
        ensure_export_root()
        path = Path(output_path)

        if path.parent.resolve() != EXPORT_ROOT.resolve():
            raise ExportError("Percorso export non consentito.")

    row_count = 0
    online_days = 0
    archive_days = 0
    conn = None
    fh = None

    try:
        # Server-side cursor keeps large online exports out of application RAM.
        conn = pymysql.connect(
            host=ENV["SYSLOG_DB_HOST"],
            user=ENV["SYSLOG_DB_USER"],
            password=ENV["SYSLOG_DB_PASSWORD"],
            database=ENV["SYSLOG_DB_NAME"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.SSDictCursor,
            autocommit=True,
        )

        fh, writer = open_csv_writer(path)

        with conn.cursor() as cur:
            existing = get_existing_tables(cur, table_names)

        for day, table in requested_tables:
            if progress_callback is not None:
                progress_callback(
                    row_count=row_count,
                    current_day=day,
                    online_days=online_days,
                    archive_days=archive_days,
                )

            if table in existing:
                with conn.cursor(
                    pymysql.cursors.DictCursor
                ) as meta_cur:
                    columns = get_table_columns(meta_cur, table)

                missing = set(active_filters) - columns

                if missing:
                    raise ExportError(
                        f"{table}: filtri non supportati: "
                        f"{', '.join(sorted(missing))}"
                    )

                day_start = datetime.combine(
                    day,
                    datetime.min.time(),
                )
                day_end = day_start + timedelta(days=1)
                query_start = max(start, day_start)
                query_end = min(end, day_end)

                select_nat_ip = (
                    "nat_source_ip"
                    if "nat_source_ip" in columns
                    else "NULL AS nat_source_ip"
                )
                select_nat_port = (
                    "nat_source_port"
                    if "nat_source_port" in columns
                    else "NULL AS nat_source_port"
                )

                sql = f"""
                    SELECT
                        id,
                        timestamp,
                        source_ip,
                        source_port,
                        {select_nat_ip},
                        {select_nat_port},
                        dest_ip,
                        dest_port,
                        protocol
                    FROM `{table}`
                    WHERE timestamp >= %s
                      AND timestamp <= %s
                """
                params = [query_start, query_end]

                for field, value in active_filters.items():
                    sql += f" AND `{field}`=%s"
                    params.append(value)

                sql += " ORDER BY timestamp ASC, id ASC"

                with conn.cursor() as data_cur:
                    data_cur.execute(sql, params)

                    for row in data_cur:
                        if row_count >= max_rows:
                            raise ExportError(
                                "Limite massimo export superato: "
                                f"{max_rows:,} record."
                            )

                        row["_storage"] = "database"
                        row["_table"] = table
                        writer.writerow(csv_row(row))
                        row_count += 1

                        if (
                            progress_callback is not None
                            and row_count % 10000 == 0
                        ):
                            progress_callback(
                                row_count=row_count,
                                current_day=day,
                                online_days=online_days,
                                archive_days=archive_days,
                            )

                online_days += 1
                continue

            archive = archive_map.get(day.isoformat())

            if archive is None:
                raise ExportError(
                    "Giornata non disponibile: "
                    + day.isoformat()
                )

            if archive["archive_status"] != "AVAILABLE":
                raise ExportError(
                    "Archivio non disponibile per "
                    + day.isoformat()
                )

            if (
                (nat_source_ip is not None or nat_source_port is not None)
                and not archive_has_nat(archive)
            ):
                raise ExportError(
                    "I filtri NAT non sono disponibili nell'archivio "
                    f"storico legacy ({day.isoformat()})."
                )

            for row in _archive_rows(
                day=day,
                archive=archive,
                start=start,
                end=end,
                source_ip=source_ip,
                source_port=source_port,
                nat_source_ip=nat_source_ip,
                nat_source_port=nat_source_port,
                dest_ip=dest_ip,
                dest_port=dest_port,
                protocol=protocol,
            ):
                if row_count >= max_rows:
                    raise ExportError(
                        "Limite massimo export superato: "
                        f"{max_rows:,} record."
                    )

                writer.writerow(csv_row(row))
                row_count += 1

                if (
                    progress_callback is not None
                    and row_count % 10000 == 0
                ):
                    progress_callback(
                        row_count=row_count,
                        current_day=day,
                        online_days=online_days,
                        archive_days=archive_days,
                    )

            archive_days += 1

        if progress_callback is not None:
            progress_callback(
                row_count=row_count,
                current_day=None,
                online_days=online_days,
                archive_days=archive_days,
            )

        fh.flush()
        os.fsync(fh.fileno())
        fh.close()
        fh = None

        return {
            "path": path,
            "filename": path.name,
            "rows": row_count,
            "online_days": online_days,
            "archive_days": archive_days,
            "filters": active_filters,
        }

    except Exception:
        if fh is not None:
            fh.close()

        remove_file_quietly(path)
        raise

    finally:
        if conn is not None:
            conn.close()
