from __future__ import annotations

import argparse
import csv
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .database import app_db


MAX_RESULTS = 1000

PROTO_MAP = {
    "6": "TCP",
    "17": "UDP",
}


class ArchiveSearchError(Exception):
    pass


def get_archive(log_date: str):
    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    log_date,
                    table_name,
                    archive_path,
                    schema_generation,
                    has_nat,
                    archive_status
                FROM archive_catalog
                WHERE log_date = %s
                LIMIT 1
                """,
                (log_date,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def parse_tuple_rows(line: str):
    """Return normalized rows from legacy and nat-v1 mysqldump INSERT lines.

    Supported layouts:
      legacy: timestamp, source_ip, source_port, dest_ip, dest_port, protocol
      nat-v1: id, timestamp, source_ip, source_port, dest_ip, dest_port,
              protocol, nat_source_ip, nat_source_port

    mysqldump may emit one or many tuples on the same INSERT line.
    """
    line = line.strip()
    if not line or "INSERT INTO" not in line and not line.startswith("("):
        return []

    if " VALUES " in line:
        line = line.split(" VALUES ", 1)[1]

    tuples = re.findall(r"\(([^()]*)\)", line)
    rows = []
    for payload in tuples:
        try:
            parts = next(csv.reader(
                [payload],
                delimiter=",",
                quotechar="'",
                escapechar="\\",
                strict=True,
            ))
        except (csv.Error, StopIteration):
            continue

        parts = [None if value == "NULL" else value for value in parts]

        if len(parts) == 6:
            ts, src, sport, dst, dport, proto = parts
            rows.append((ts, src, sport, None, None, dst, dport, proto))
        elif len(parts) == 9:
            _id, ts, src, sport, dst, dport, proto, nat_ip, nat_port = parts
            rows.append((ts, src, sport, nat_ip, nat_port, dst, dport, proto))

    return rows


def parse_tuple_line(line: str):
    """Backward-compatible single-row wrapper."""
    rows = parse_tuple_rows(line)
    return rows[0] if rows else None

def normalize_protocol(value: str):
    return PROTO_MAP.get(value, value.upper())


def search_archive(
    archive_path: str,
    start_iso: str,
    end_iso: str,
    source_ip=None,
    source_port=None,
    dest_ip=None,
    dest_port=None,
    protocol=None,
    limit=MAX_RESULTS,
):
    archive = Path(archive_path)

    if not archive.is_file():
        raise ArchiveSearchError(
            f"Archivio non trovato: {archive}"
        )

    start_dt = datetime.fromisoformat(start_iso)
    end_dt = datetime.fromisoformat(end_iso)

    if end_dt < start_dt:
        raise ArchiveSearchError(
            "Intervallo temporale non valido"
        )

    # I dump di ogni singola giornata sono cronologicamente
    # ordinati e utilizzano timestamp ISO 8601.
    # Nel percorso critico confrontiamo quindi direttamente
    # le stringhe, evitando datetime.fromisoformat() per
    # milioni di record.
    start_key = start_iso
    end_key = end_iso

    if protocol:
        protocol = protocol.upper()

    results = []
    matched = 0
    parsed_rows = 0
    rows_in_window = 0
    stopped_by_time = False
    truncated = False

    proc = subprocess.Popen(
        [
            "/usr/bin/zstd",
            "-dc",
            "--",
            str(archive),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1024 * 1024,
    )

    started = time.monotonic()

    try:
        assert proc.stdout is not None

        for line in proc.stdout:
            for row in parse_tuple_rows(line):
                parsed_rows += 1

                (
                    ts_raw,
                    src_ip,
                    src_port_raw,
                    nat_src_ip,
                    nat_src_port_raw,
                    dst_ip,
                    dst_port_raw,
                    proto_raw,
                ) = row

                # Confronto diretto dei timestamp ISO 8601.
                # Evita una conversione datetime per ogni record.
                if ts_raw < start_key:
                    continue

                if ts_raw > end_key:
                    stopped_by_time = True
                    break

                rows_in_window += 1

                if source_ip is not None:
                    if src_ip != source_ip:
                        continue

                if source_port is not None:
                    try:
                        if int(src_port_raw) != source_port:
                            continue
                    except ValueError:
                        continue

                if dest_ip is not None:
                    if dst_ip != dest_ip:
                        continue

                if dest_port is not None:
                    try:
                        if int(dst_port_raw) != dest_port:
                            continue
                    except ValueError:
                        continue

                proto = normalize_protocol(proto_raw)

                if protocol is not None:
                    if proto != protocol:
                        continue

                matched += 1

                if len(results) < limit:
                    results.append({
                        "timestamp": ts_raw,
                        "source_ip": src_ip,
                        "source_port": int(src_port_raw),
                        "nat_source_ip": None,
                        "nat_source_port": None,
                        "dest_ip": dst_ip,
                        "dest_port": int(dst_port_raw),
                        "protocol": proto,
                        "storage": "archive",
                    })
                else:
                    # Abbiamo trovato il risultato limit+1:
                    # sappiamo con certezza che l'output
                    # è troncato.
                    truncated = True
                    break

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

    elapsed = time.monotonic() - started

    return {
        "results": results,
        "count": len(results),
        "truncated": truncated,
        "parsed_rows": parsed_rows,
        "rows_in_window": rows_in_window,
        "stopped_by_time": stopped_by_time,
        "elapsed": elapsed,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--date", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)

    parser.add_argument("--source-ip")
    parser.add_argument("--source-port", type=int)

    parser.add_argument("--dest-ip")
    parser.add_argument("--dest-port", type=int)

    parser.add_argument(
        "--protocol",
        choices=["TCP", "UDP"],
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
    )

    args = parser.parse_args()

    archive = get_archive(args.date)

    if not archive:
        raise SystemExit(
            f"Nessun archivio per {args.date}"
        )

    if archive["archive_status"] != "AVAILABLE":
        raise SystemExit(
            "Archivio non disponibile: "
            f"{archive['archive_status']}"
        )

    if archive["schema_generation"] not in ("legacy", "nat-v1"):
        raise SystemExit(
            Schema archivio non supportato: "
            f"{archive['schema_generation']}"
        )

    result = search_archive(
        archive_path=archive["archive_path"],
        start_iso=args.start,
        end_iso=args.end,
        source_ip=args.source_ip,
        source_port=args.source_port,
        dest_ip=args.dest_ip,
        dest_port=args.dest_port,
        protocol=args.protocol,
        limit=min(max(args.limit, 1), MAX_RESULTS),
    )

    print()
    print(f"Archivio:          {archive['table_name']}")
    print(f"Record letti:      {result['parsed_rows']:,}")
    print(f"Record finestra:   {result['rows_in_window']:,}")
    print(f"Risultati:         {result['count']}")
    print(f"Troncato:          {result['truncated']}")
    print(f"Stop temporale:    {result['stopped_by_time']}")
    print(f"Tempo:             {result['elapsed']:.3f} s")

    print()

    for row in result["results"][:20]:
        print(
            row["timestamp"],
            row["source_ip"],
            row["source_port"],
            "->",
            row["dest_ip"],
            row["dest_port"],
            row["protocol"],
        )

    if result["count"] > 20:
        print(
            f"... altri "
            f"{result['count'] - 20} risultati"
        )


if __name__ == "__main__":
    main()
