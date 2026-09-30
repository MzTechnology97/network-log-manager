import ipaddress
import re
from datetime import datetime, timedelta
from pathlib import Path

from .database import app_db, syslog_db
from .archive_cache import cache_is_valid
from .archive_cache_search import search_cache


TABLE_RE = re.compile(
    r"^mikrotik_logs_\d{4}_\d{2}_\d{2}$"
)

MAX_RESULTS = 1000
MAX_DAYS = 31
ALLOWED_PROTOCOLS = {"TCP", "UDP"}


class AdvancedSearchError(Exception):
    pass


def optional_ipv4(value):
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        raise AdvancedSearchError(
            f"Indirizzo IP non valido: {value}"
        )

    if ip.version != 4:
        raise AdvancedSearchError(
            "Sono supportati solamente indirizzi IPv4."
        )

    return str(ip)


def optional_port(value):
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    try:
        port = int(value)
    except (TypeError, ValueError):
        raise AdvancedSearchError(
            f"Porta non valida: {value}"
        )

    if not 1 <= port <= 65535:
        raise AdvancedSearchError(
            "Le porte devono essere comprese tra 1 e 65535."
        )

    return port


def optional_protocol(value):
    if value is None:
        return None

    value = str(value).strip().upper()

    if not value:
        return None

    if value not in ALLOWED_PROTOCOLS:
        raise AdvancedSearchError("Protocollo non valido.")

    return value


def tables_for_range(start, end):
    if not isinstance(start, datetime):
        raise AdvancedSearchError("Data iniziale non valida.")

    if not isinstance(end, datetime):
        raise AdvancedSearchError("Data finale non valida.")

    if end < start:
        raise AdvancedSearchError(
            "La data finale deve essere successiva alla data iniziale."
        )

    if end - start > timedelta(days=MAX_DAYS):
        raise AdvancedSearchError(
            f"L'intervallo massimo è {MAX_DAYS} giorni."
        )

    tables = []
    day = start.date()
    last_day = end.date()

    while day <= last_day:
        table = "mikrotik_logs_" + day.strftime("%Y_%m_%d")

        if not TABLE_RE.fullmatch(table):
            raise AdvancedSearchError("Nome tabella non valido.")

        tables.append((day, table))
        day += timedelta(days=1)

    return tables


def get_existing_tables(cur, table_names):
    if not table_names:
        return set()

    placeholders = ",".join(["%s"] * len(table_names))

    cur.execute(
        f"""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='syslogdb'
          AND table_name IN ({placeholders})
        """,
        table_names,
    )

    return {
        row["table_name"]
        for row in cur.fetchall()
    }


def get_table_columns(cur, table):
    if not TABLE_RE.fullmatch(table):
        raise AdvancedSearchError("Nome tabella non valido.")

    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='syslogdb'
          AND table_name=%s
        """,
        (table,),
    )

    return {
        row["column_name"]
        for row in cur.fetchall()
    }


def get_archive_map(days):
    if not days:
        return {}

    conn = app_db()

    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(days))

            cur.execute(
                f"""
                SELECT
                    log_date,
                    table_name,
                    archive_path,
                    row_count,
                    sha256,
                    schema_generation,
                    has_nat,
                    archive_status
                FROM archive_catalog
                WHERE log_date IN ({placeholders})
                """,
                days,
            )

            return {
                str(row["log_date"]): row
                for row in cur.fetchall()
            }
    finally:
        conn.close()


def archive_has_nat(archive):
    value = archive.get("has_nat")

    try:
        if value is not None and int(value) == 1:
            return True
    except (TypeError, ValueError):
        if str(value).strip().lower() in {
            "true",
            "yes",
            "on",
        }:
            return True

    return archive.get("schema_generation") == "nat-v1"


def local_iso(dt):
    # Cache segments are indexed by local wall-clock time.  The original
    # timezone offset remains in each stored timestamp and is returned to the
    # caller, but range selection intentionally uses the local component.
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:23]


def archive_search_for_day(
    *,
    day,
    archive,
    start,
    end,
    source_ip,
    source_port,
    nat_source_ip,
    nat_source_port,
    dest_ip,
    dest_port,
    protocol,
    remaining,
):
    log_date = day.isoformat()
    archive_path = Path(archive["archive_path"])
    expected_rows = archive["row_count"]

    if expected_rows is not None:
        expected_rows = int(expected_rows)

    valid, reason = cache_is_valid(
        log_date,
        archive_path,
        expected_rows,
        archive["sha256"],
    )

    if not valid:
        return {
            "status": "pending",
            "reason": reason,
            "rows": [],
        }

    if not archive_has_nat(archive):
        missing = []

        if nat_source_ip is not None:
            missing.append("nat_source_ip")

        if nat_source_port is not None:
            missing.append("nat_source_port")

        if missing:
            return {
                "status": "unsupported",
                "missing": missing,
                "rows": [],
            }

    day_start = datetime.combine(day, datetime.min.time())
    day_end = (
        day_start
        + timedelta(days=1)
        - timedelta(microseconds=1)
    )

    query_start = max(start, day_start)
    query_end = min(end, day_end)
    start_local = local_iso(query_start)
    end_local = local_iso(query_end)

    cache_result = search_cache(
        log_date=log_date,
        start_iso=start_local,
        end_iso=end_local,
        source_ip=source_ip,
        source_port=source_port,
        nat_source_ip=nat_source_ip,
        nat_source_port=nat_source_port,
        dest_ip=dest_ip,
        dest_port=dest_port,
        protocol=protocol,
        limit=remaining,
    )

    rows = []

    for index, row in enumerate(cache_result["results"]):
        timestamp_raw = row["timestamp"]
        timestamp_local = timestamp_raw[:23]

        if timestamp_local < start_local:
            continue

        if timestamp_local > end_local:
            continue

        rows.append(
            {
                "id": index,
                "timestamp": timestamp_raw,
                "source_ip": row["source_ip"],
                "source_port": row["source_port"],
                "nat_source_ip": row.get("nat_source_ip"),
                "nat_source_port": row.get("nat_source_port"),
                "dest_ip": row["dest_ip"],
                "dest_port": row["dest_port"],
                "protocol": row["protocol"],
                "_table": archive["table_name"],
                "_storage": "archive",
            }
        )

    return {
        "status": "searched",
        "rows": rows,
        "truncated": cache_result["truncated"],
    }


def search_logs(
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
):
    source_ip = optional_ipv4(source_ip)
    source_port = optional_port(source_port)
    nat_source_ip = optional_ipv4(nat_source_ip)
    nat_source_port = optional_port(nat_source_port)
    dest_ip = optional_ipv4(dest_ip)
    dest_port = optional_port(dest_port)
    protocol = optional_protocol(protocol)

    filters = {
        "source_ip": source_ip,
        "source_port": source_port,
        "nat_source_ip": nat_source_ip,
        "nat_source_port": nat_source_port,
        "dest_ip": dest_ip,
        "dest_port": dest_port,
        "protocol": protocol,
    }

    active_filters = {
        key: value
        for key, value in filters.items()
        if value is not None
    }

    if not active_filters:
        raise AdvancedSearchError(
            "Specificare almeno un filtro oltre all'intervallo temporale."
        )

    requested_tables = tables_for_range(start, end)
    table_names = [table for _, table in requested_tables]
    archive_map = get_archive_map(
        [day.isoformat() for day, _ in requested_tables]
    )

    conn = syslog_db()

    results = []
    searched_tables = []
    searched_archives = []
    skipped_tables = []
    unsupported_tables = []
    pending_archives = []
    missing_days = []
    globally_truncated = False

    try:
        with conn.cursor() as cur:
            existing = get_existing_tables(cur, table_names)

            for day, table in requested_tables:
                remaining = MAX_RESULTS + 1 - len(results)
                result_limit_reached = remaining <= 0

                if result_limit_reached:
                    globally_truncated = True

                # Online MariaDB data always has precedence over an archive.
                if table in existing:
                    columns = get_table_columns(cur, table)
                    missing_filter_columns = (
                        set(active_filters) - columns
                    )

                    if missing_filter_columns:
                        unsupported_tables.append(
                            {
                                "table": table,
                                "missing": sorted(
                                    missing_filter_columns
                                ),
                                "storage": "database",
                            }
                        )
                        continue

                    if result_limit_reached:
                        continue

                    searched_tables.append(table)

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

                    sql += """
                        ORDER BY timestamp ASC, id ASC
                        LIMIT %s
                    """
                    params.append(remaining)

                    cur.execute(sql, params)
                    rows = cur.fetchall()

                    for row in rows:
                        row["_table"] = table
                        row["_storage"] = "database"
                        results.append(row)

                    if len(results) > MAX_RESULTS:
                        globally_truncated = True

                    continue

                archive = archive_map.get(day.isoformat())

                if archive is None:
                    skipped_tables.append(table)
                    missing_days.append(day.isoformat())
                    continue

                if archive["archive_status"] != "AVAILABLE":
                    pending_archives.append(
                        {
                            "date": day.isoformat(),
                            "table": table,
                            "reason": (
                                "archive_status="
                                + str(archive["archive_status"])
                            ),
                        }
                    )
                    continue

                if result_limit_reached:
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
                        pending_archives.append(
                            {
                                "date": day.isoformat(),
                                "table": table,
                                "reason": reason,
                            }
                        )
                        continue

                    if not archive_has_nat(archive):
                        missing = []

                        if nat_source_ip is not None:
                            missing.append("nat_source_ip")

                        if nat_source_port is not None:
                            missing.append("nat_source_port")

                        if missing:
                            unsupported_tables.append(
                                {
                                    "table": table,
                                    "missing": missing,
                                    "storage": "archive",
                                }
                            )

                    continue

                archive_result = archive_search_for_day(
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
                    remaining=remaining,
                )

                if archive_result["status"] == "pending":
                    pending_archives.append(
                        {
                            "date": day.isoformat(),
                            "table": table,
                            "reason": archive_result["reason"],
                        }
                    )
                    continue

                if archive_result["status"] == "unsupported":
                    unsupported_tables.append(
                        {
                            "table": table,
                            "missing": archive_result["missing"],
                            "storage": "archive",
                        }
                    )
                    continue

                searched_archives.append(table)
                results.extend(archive_result["rows"])

                if archive_result.get("truncated"):
                    globally_truncated = True

                if len(results) > MAX_RESULTS:
                    globally_truncated = True

    finally:
        conn.close()

    def result_sort_key(row):
        timestamp = row["timestamp"]

        if isinstance(timestamp, datetime):
            ts_key = timestamp.strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )
        else:
            ts_key = str(timestamp)

        return (
            ts_key,
            int(row.get("id") or 0),
        )

    results.sort(key=result_sort_key)

    if len(results) > MAX_RESULTS:
        globally_truncated = True
        results = results[:MAX_RESULTS]

    searched_all = searched_tables + searched_archives

    return {
        "results": results,
        "count": len(results),
        "truncated": globally_truncated,
        "filters": active_filters,
        "start": start,
        "end": end,
        "searched_tables": searched_all,
        "skipped_tables": skipped_tables,
        "unsupported_tables": unsupported_tables,
        "searched_online_tables": searched_tables,
        "searched_archives": searched_archives,
        "pending_archives": pending_archives,
        "missing_days": missing_days,
    }
