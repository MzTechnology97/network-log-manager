import ipaddress
import re
from datetime import datetime, timedelta

from .database import syslog_db


TABLE_RE = re.compile(r"^mikrotik_logs_\d{4}_\d{2}_\d{2}$")

MAX_RESULTS = 1000
MAX_DAYS = 31


class ExplorerError(Exception):
    pass


def validate_ipv4(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        raise ExplorerError("Indirizzo IP non valido.")

    if ip.version != 4:
        raise ExplorerError("È richiesto un indirizzo IPv4.")

    return str(ip)


def validate_port(value) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ExplorerError("Porta non valida.")

    if not 1 <= port <= 65535:
        raise ExplorerError(
            "La porta deve essere compresa tra 1 e 65535."
        )

    return port


def validate_protocol(value: str | None):
    if not value:
        return None

    value = value.strip().upper()

    if value not in ("TCP", "UDP"):
        raise ExplorerError("Protocollo non valido.")

    return value


def tables_for_range(start: datetime, end: datetime):
    if end < start:
        raise ExplorerError(
            "La data finale deve essere successiva a quella iniziale."
        )

    if end - start > timedelta(days=MAX_DAYS):
        raise ExplorerError(
            f"L'intervallo massimo per una ricerca online è {MAX_DAYS} giorni."
        )

    tables = []

    day = start.date()
    last_day = end.date()

    while day <= last_day:
        table = "mikrotik_logs_" + day.strftime("%Y_%m_%d")

        if not TABLE_RE.fullmatch(table):
            raise ExplorerError("Nome tabella non valido.")

        tables.append((day, table))
        day += timedelta(days=1)

    return tables


def search_nat(
    public_ip: str,
    public_port,
    start: datetime,
    end: datetime,
    protocol: str | None = None,
):
    public_ip = validate_ipv4(public_ip)
    public_port = validate_port(public_port)
    protocol = validate_protocol(protocol)

    requested_tables = tables_for_range(start, end)

    conn = syslog_db()

    results = []
    searched_tables = []
    skipped_tables = []

    try:
        with conn.cursor() as cur:

            table_names = [table for _, table in requested_tables]

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

            existing = {
                row["table_name"]
                for row in cur.fetchall()
            }

            for day, table in requested_tables:

                if table not in existing:
                    skipped_tables.append(table)
                    continue

                searched_tables.append(table)

                day_start = datetime.combine(
                    day,
                    datetime.min.time(),
                )

                day_end = day_start + timedelta(days=1)

                query_start = max(start, day_start)
                query_end = min(end, day_end)

                sql = f"""
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
                    WHERE nat_source_ip=%s
                      AND nat_source_port=%s
                      AND timestamp >= %s
                      AND timestamp <= %s
                """

                params = [
                    public_ip,
                    public_port,
                    query_start,
                    query_end,
                ]

                if protocol:
                    sql += " AND protocol=%s"
                    params.append(protocol)

                sql += """
                    ORDER BY timestamp ASC
                    LIMIT %s
                """

                params.append(MAX_RESULTS + 1)

                cur.execute(sql, params)

                for row in cur.fetchall():
                    row["_table"] = table
                    results.append(row)

                    if len(results) > MAX_RESULTS:
                        break

                if len(results) > MAX_RESULTS:
                    break

    finally:
        conn.close()

    results.sort(
        key=lambda row: (
            row["timestamp"],
            row["id"],
        )
    )

    truncated = len(results) > MAX_RESULTS

    if truncated:
        results = results[:MAX_RESULTS]

    return {
        "results": results,
        "count": len(results),
        "truncated": truncated,
        "searched_tables": searched_tables,
        "skipped_tables": skipped_tables,
        "public_ip": public_ip,
        "public_port": public_port,
        "protocol": protocol,
        "start": start,
        "end": end,
    }
