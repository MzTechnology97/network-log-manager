from __future__ import annotations

import argparse
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


def parse_tuple_line(line: str):
    """
    Parser specializzato per il formato legacy reale:

    ('timestamp','source_ip','source_port',
     'dest_ip','dest_port','protocol'),

    I campi di questi log sono valori semplici:
    timestamp, IPv4, porte e protocollo numerico.
    """

    line = line.strip()

    if not line.startswith("('"):
        return None

    # Rimuove:
    # (
    # ),
    # );
    if line.endswith("),"):
        payload = line[1:-2]
    elif line.endswith(");"):
        payload = line[1:-2]
    else:
        return None

    parts = payload.split("','")

    if len(parts) != 6:
        return None

    # Primo e ultimo apice.
    parts[0] = parts[0][1:]

    if parts[-1].endswith("'"):
        parts[-1] = parts[-1][:-1]

    if len(parts) != 6:
        return None

    return parts


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
            row = parse_tuple_line(line)

            if row is None:
                continue

            parsed_rows += 1

            (
                ts_raw,
                src_ip,
                src_port_raw,
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

    if archive["schema_generation"] != "legacy":
        raise SystemExit(
            "Schema non supportato dal parser legacy: "
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
