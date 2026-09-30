from __future__ import annotations

import argparse
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from .archive_search import normalize_archive_timestamp


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


def parse_cache_line(line: str):
    """Parse legacy tsv-v1 and current tsv-v2 cache rows.

    tsv-v1:
      timestamp, source_ip, source_port, dest_ip, dest_port, protocol

    tsv-v2:
      timestamp, source_ip, source_port, nat_source_ip, nat_source_port,
      dest_ip, dest_port, protocol

    Older caches can contain the MariaDB DATETIME separator (space) rather
    than the ISO ``T`` separator.  Normalize it while reading so deployed v3
    caches stay searchable without forcing a full cache rebuild.
    """
    parts = line.rstrip("\n").split("\t")

    if len(parts) == 6:
        (
            timestamp,
            src_ip,
            src_port_raw,
            dst_ip,
            dst_port_raw,
            proto,
        ) = parts
        nat_src_ip = ""
        nat_src_port_raw = ""
    elif len(parts) == 8:
        (
            timestamp,
            src_ip,
            src_port_raw,
            nat_src_ip,
            nat_src_port_raw,
            dst_ip,
            dst_port_raw,
            proto,
        ) = parts
    else:
        return None

    timestamp = normalize_archive_timestamp(timestamp)

    try:
        src_port = int(src_port_raw)
        dst_port = int(dst_port_raw)
        nat_src_port = (
            int(nat_src_port_raw)
            if nat_src_port_raw
            else None
        )
    except (TypeError, ValueError):
        return None

    return {
        "timestamp": timestamp,
        "source_ip": src_ip,
        "source_port": src_port,
        "nat_source_ip": nat_src_ip or None,
        "nat_source_port": nat_src_port,
        "dest_ip": dst_ip,
        "dest_port": dst_port,
        "protocol": proto.upper(),
        "storage": "archive-cache",
    }


def search_cache(
    log_date: str,
    start_iso: str,
    end_iso: str,
    source_ip=None,
    source_port=None,
    nat_source_ip=None,
    nat_source_port=None,
    dest_ip=None,
    dest_port=None,
    protocol=None,
    limit=MAX_RESULTS,
):
    # Cache segments are keyed by local wall-clock time.  Keep comparisons on
    # YYYY-MM-DDTHH:MM:SS.mmm so the repeated DST hour remains searchable while
    # preserving the original offset in the returned timestamp.
    start_key = normalize_archive_timestamp(str(start_iso))[:23]
    end_key = normalize_archive_timestamp(str(end_iso))[:23]

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

                row = parse_cache_line(line)

                if row is None:
                    continue

                timestamp_local = row["timestamp"][:23]

                if timestamp_local < start_key:
                    continue

                # Do not break here: the local 02:xx interval can occur twice
                # during the DST fallback and concatenated zstd frames preserve
                # both occurrences in the same segment.
                if timestamp_local > end_key:
                    continue

                rows_in_window += 1

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

                if len(results) >= limit:
                    truncated = True
                    break

                results.append(row)

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
    parser.add_argument("--nat-source-ip")
    parser.add_argument("--nat-source-port", type=int)
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
        nat_source_ip=args.nat_source_ip,
        nat_source_port=args.nat_source_port,
        dest_ip=args.dest_ip,
        dest_port=args.dest_port,
        protocol=args.protocol,
        limit=min(max(args.limit, 1), MAX_RESULTS),
    )

    print()
    print("Segmenti:       " + ", ".join(result["segments"]))
    print(f"Record letti:   {result['scanned_rows']:,}")
    print(f"Record finestra: {result['rows_in_window']:,}")
    print(f"Risultati:      {result['count']}")
    print(f"Troncato:       {result['truncated']}")
    print(f"Tempo:          {result['elapsed']:.3f} s")
    print()

    for row in result["results"][:20]:
        nat = ""
        if row["nat_source_ip"] is not None:
            nat = (
                f" NAT {row['nat_source_ip']}:"
                f"{row['nat_source_port']}"
            )
        print(
            row["timestamp"],
            row["source_ip"],
            row["source_port"],
            "->",
            row["dest_ip"],
            row["dest_port"],
            row["protocol"],
            nat,
        )

    if result["count"] > 20:
        print(
            f"... altri "
            f"{result['count'] - 20} risultati"
        )


if __name__ == "__main__":
    main()
