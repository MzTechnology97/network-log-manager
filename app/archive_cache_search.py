from __future__ import annotations

import argparse
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path


CACHE_ROOT = Path("/var/cache/netlog-manager/history")
MAX_RESULTS = 1000


class ArchiveCacheSearchError(Exception):
    pass


def segment_key(dt: datetime) -> str:
    quarter = (dt.minute // 15) * 15
    return f"{dt.hour:02d}{quarter:02d}"


def required_segments(start_dt: datetime, end_dt: datetime):
    current = start_dt.replace(
        minute=(start_dt.minute // 15) * 15,
        second=0,
        microsecond=0,
    )

    segments = []

    while current <= end_dt:
        segments.append(segment_key(current))
        current += timedelta(minutes=15)

    return segments


def search_cache(
    log_date: str,
    start_iso: str,
    end_iso: str,
    source_ip=None,
    source_port=None,
    dest_ip=None,
    dest_port=None,
    protocol=None,
    limit=MAX_RESULTS,
):
    #
    # Le cache storiche sono segmentate secondo
    # l'ora locale presente nel log originale.
    #
    # Per la selezione dei segmenti e per il confronto
    # temporale utilizziamo quindi esclusivamente la
    # componente locale YYYY-MM-DDTHH:MM:SS.mmm.
    #
    # L'offset originale (+01:00/+02:00) resta conservato
    # nel record restituito, ma non influenza la ricerca
    # richiesta dalla GUI, che lavora in Europe/Rome.
    #
    start_key = str(start_iso)[:23]
    end_key = str(end_iso)[:23]

    try:
        start_dt = datetime.fromisoformat(start_key)
        end_dt = datetime.fromisoformat(end_key)
    except ValueError as exc:
        raise ArchiveCacheSearchError(
            "Intervallo temporale non valido"
        ) from exc

    if end_dt < start_dt:
        raise ArchiveCacheSearchError(
            "Intervallo temporale non valido"
        )

    cache_dir = CACHE_ROOT / log_date

    if not cache_dir.is_dir():
        raise ArchiveCacheSearchError(
            f"Cache non disponibile: {cache_dir}"
        )

    manifest = cache_dir / "manifest.txt"

    if not manifest.is_file():
        raise ArchiveCacheSearchError(
            "Manifest cache non trovato"
        )

    protocol = protocol.upper() if protocol else None

    results = []
    scanned_rows = 0
    rows_in_window = 0
    truncated = False

    segments = required_segments(start_dt, end_dt)
    scanned_segments = []

    started = time.monotonic()

    for seg in segments:
        path = cache_dir / f"{seg}.tsv.zst"

        if not path.is_file():
            continue

        scanned_segments.append(seg)

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
                scanned_rows += 1

                line = line.rstrip("\n")

                parts = line.split("\t")

                if len(parts) != 6:
                    continue

                (
                    timestamp,
                    src_ip,
                    src_port_raw,
                    dst_ip,
                    dst_port_raw,
                    proto,
                ) = parts

                timestamp_local = timestamp[:23]

                if timestamp_local < start_key:
                    continue

                if timestamp_local > end_key:
                    #
                    # Non usare break: durante il ritorno
                    # CET una fascia 02:xx può comparire
                    # nuovamente dopo una fascia successiva
                    # appartenente al primo ciclo DST.
                    #
                    continue

                rows_in_window += 1

                if source_ip is not None:
                    if src_ip != source_ip:
                        continue

                if source_port is not None:
                    if src_port_raw != str(source_port):
                        continue

                if dest_ip is not None:
                    if dst_ip != dest_ip:
                        continue

                if dest_port is not None:
                    if dst_port_raw != str(dest_port):
                        continue

                if protocol is not None:
                    if proto != protocol:
                        continue

                if len(results) >= limit:
                    truncated = True
                    break

                results.append({
                    "timestamp": timestamp,
                    "source_ip": src_ip,
                    "source_port": int(src_port_raw),
                    "nat_source_ip": None,
                    "nat_source_port": None,
                    "dest_ip": dst_ip,
                    "dest_port": int(dst_port_raw),
                    "protocol": proto,
                    "storage": "archive-cache",
                })

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

        if truncated:
            break

    elapsed = time.monotonic() - started

    return {
        "results": results,
        "count": len(results),
        "truncated": truncated,
        "scanned_rows": scanned_rows,
        "rows_in_window": rows_in_window,
        "segments": scanned_segments,
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

    result = search_cache(
        log_date=args.date,
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
    print(
        "Segmenti:       "
        + ", ".join(result["segments"])
    )
    print(
        f"Record letti:   "
        f"{result['scanned_rows']:,}"
    )
    print(
        f"Record finestra:"
        f" {result['rows_in_window']:,}"
    )
    print(
        f"Risultati:      "
        f"{result['count']}"
    )
    print(
        f"Troncato:       "
        f"{result['truncated']}"
    )
    print(
        f"Tempo:          "
        f"{result['elapsed']:.3f} s"
    )
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
