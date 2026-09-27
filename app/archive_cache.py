from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .archive_search import get_archive, parse_tuple_line, normalize_protocol
from .storage_registry import materialize_from_fallback


CACHE_ROOT = Path("/var/cache/netlog-manager/history")

CACHE_VERSION = 2
CACHE_FORMAT = "tsv-v1"
SEGMENT_MINUTES = 15
PROGRESS_EVERY = 5_000_000


class ArchiveCacheError(Exception):
    pass


def source_sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)

            if not chunk:
                break

            h.update(chunk)

    return h.hexdigest()


def segment_name(timestamp: str) -> str:
    hour = int(timestamp[11:13])
    minute = int(timestamp[14:16])

    segment_minute = (
        minute // SEGMENT_MINUTES
    ) * SEGMENT_MINUTES

    return f"{hour:02d}{segment_minute:02d}"


def read_manifest(cache_dir: Path) -> dict[str, str]:
    manifest_path = cache_dir / "manifest.txt"

    if not manifest_path.is_file():
        return {}

    result = {}

    with manifest_path.open(
        "r",
        encoding="utf-8",
    ) as f:
        for line in f:
            line = line.rstrip("\n")

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            result[key] = value

    return result


def cache_is_valid(
    log_date: str,
    archive_path: Path,
    expected_rows: int | None,
    expected_sha256: str | None = None,
) -> tuple[bool, str]:
    cache_dir = CACHE_ROOT / log_date

    if not cache_dir.is_dir():
        return False, "cache assente"

    manifest = read_manifest(cache_dir)

    required = (
        "CACHE_VERSION",
        "FORMAT",
        "SEGMENT_MINUTES",
        "SOURCE",
        "SOURCE_BYTES",
        "SOURCE_SHA256",
        "ROWS",
        "SEGMENTS",
        "COMPLETE",
    )

    for key in required:
        if key not in manifest:
            return False, f"manifest privo di {key}"

    if manifest["CACHE_VERSION"] != str(CACHE_VERSION):
        return False, "versione cache differente"

    if manifest["FORMAT"] != CACHE_FORMAT:
        return False, "formato cache differente"

    if manifest["SEGMENT_MINUTES"] != str(SEGMENT_MINUTES):
        return False, "segmentazione differente"

    if manifest["COMPLETE"] != "1":
        return False, "cache incompleta"

    if manifest["SOURCE"] != str(archive_path):
        return False, "sorgente differente"

    try:
        source_bytes = archive_path.stat().st_size
    except FileNotFoundError:
        return False, "archivio sorgente assente"

    if manifest["SOURCE_BYTES"] != str(source_bytes):
        return False, "dimensione sorgente differente"

    if expected_rows is not None:
        if manifest["ROWS"] != str(expected_rows):
            return False, "numero record differente"

    try:
        segment_count = int(manifest["SEGMENTS"])
    except ValueError:
        return False, "numero segmenti non valido"

    actual_segments = len(
        list(cache_dir.glob("*.tsv.zst"))
    )

    if actual_segments != segment_count:
        return False, "numero file segmento differente"

    if expected_sha256 is not None:
        if (
            manifest["SOURCE_SHA256"].lower()
            != expected_sha256.lower()
        ):
            return False, "SHA256 sorgente differente"

    return True, "OK"


def build_cache(
    log_date: str,
    force: bool = False,
):
    archive = get_archive(log_date)

    if not archive:
        raise ArchiveCacheError(
            f"Nessun archivio per {log_date}"
        )

    if archive["archive_status"] != "AVAILABLE":
        raise ArchiveCacheError(
            "Archivio non disponibile: "
            f"{archive['archive_status']}"
        )

    if archive["schema_generation"] != "legacy":
        raise ArchiveCacheError(
            "Questa versione supporta solamente "
            "gli archivi legacy"
        )

    archive_path = Path(
        archive["archive_path"]
    )

    expected_sha = archive.get("sha256")
    if not archive_path.is_file():
        try:
            root=Path("/archive/mikrotik")
            relative=str(archive_path.relative_to(root))
            materialize_from_fallback(relative,archive_path,expected_sha)
        except Exception as exc:
            raise ArchiveCacheError(f"File non trovato su primary/fallback: {archive_path}: {exc}") from exc

    expected_rows = archive.get("row_count")

    if expected_rows is not None:
        expected_rows = int(expected_rows)

    CACHE_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    final_dir = CACHE_ROOT / log_date

    if final_dir.exists() and not force:
        valid, reason = cache_is_valid(
            log_date,
            archive_path,
            expected_rows,
            expected_sha,
        )

        if valid:
            manifest = read_manifest(final_dir)

            return {
                "status": "already-valid",
                "rows": int(manifest["ROWS"]),
                "segments": int(manifest["SEGMENTS"]),
                "bytes": sum(
                    p.stat().st_size
                    for p in final_dir.glob(
                        "*.tsv.zst"
                    )
                ),
                "elapsed": 0.0,
                "path": str(final_dir),
            }

        raise ArchiveCacheError(
            f"Cache esistente non valida: {reason}. "
            f"Usare --force per rigenerarla."
        )

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{log_date}-",
            dir=CACHE_ROOT,
        )
    )

    started = time.monotonic()

    print("Calcolo SHA256 sorgente...", flush=True)

    actual_sha = source_sha256(
        archive_path
    )

    if expected_sha:
        if actual_sha.lower() != expected_sha.lower():
            shutil.rmtree(
                temp_dir,
                ignore_errors=True,
            )

            raise ArchiveCacheError(
                "SHA256 dell'archivio non coincide "
                "con archive_catalog"
            )

    source_bytes = archive_path.stat().st_size

    zstd_proc = None
    segment_proc = None
    segment_input = None
    segment_output = None
    current_segment = None

    rows = 0
    segment_rows: dict[str, int] = {}

    try:
        print(
            "Conversione archivio...",
            flush=True,
        )

        zstd_proc = subprocess.Popen(
            [
                "/usr/bin/zstd",
                "-dc",
                "--",
                str(archive_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1024 * 1024,
        )

        assert zstd_proc.stdout is not None

        for line in zstd_proc.stdout:
            row = parse_tuple_line(line)

            if row is None:
                continue

            (
                timestamp,
                source_ip,
                source_port,
                dest_ip,
                dest_port,
                protocol,
            ) = row

            seg = segment_name(timestamp)

            if seg != current_segment:
                if segment_input is not None:
                    segment_input.close()
                    segment_proc.wait()

                    if segment_output is not None:
                        segment_output.close()
                        segment_output = None

                    if segment_proc.returncode != 0:
                        raise ArchiveCacheError(
                            "Errore zstd segmento "
                            f"{current_segment}"
                        )

                output_path = (
                    temp_dir /
                    f"{seg}.tsv.zst"
                )

                #
                # Durante il ritorno dall'ora legale
                # all'ora solare la stessa fascia 02:xx
                # compare due volte:
                #
                #   02:xx +02:00
                #   02:xx +01:00
                #
                # Se il segmento esiste già aggiungiamo
                # un nuovo frame Zstandard concatenato.
                #
                segment_output = output_path.open(
                    "ab"
                    if output_path.exists()
                    else "wb"
                )

                segment_proc = subprocess.Popen(
                    [
                        "/usr/bin/zstd",
                        "-q",
                        "-T1",
                        "-3",
                        "-c",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=segment_output,
                    text=True,
                    encoding="utf-8",
                )

                segment_input = segment_proc.stdin
                current_segment = seg

                segment_rows.setdefault(
                    seg,
                    0,
                )

            proto = normalize_protocol(
                protocol
            )

            segment_input.write(
                f"{timestamp}\t"
                f"{source_ip}\t"
                f"{source_port}\t"
                f"{dest_ip}\t"
                f"{dest_port}\t"
                f"{proto}\n"
            )

            rows += 1
            segment_rows[seg] += 1

            if rows % PROGRESS_EVERY == 0:
                elapsed = (
                    time.monotonic()
                    - started
                )

                print(
                    f"  {rows:,} record "
                    f"({elapsed:.1f} s)",
                    flush=True,
                )

        if segment_input is not None:
            segment_input.close()
            segment_proc.wait()

            if segment_output is not None:
                segment_output.close()
                segment_output = None

            if segment_proc.returncode != 0:
                raise ArchiveCacheError(
                    "Errore zstd segmento "
                    f"{current_segment}"
                )

        zstd_proc.stdout.close()

        rc = zstd_proc.wait()

        if rc != 0:
            stderr = ""

            if zstd_proc.stderr is not None:
                stderr = zstd_proc.stderr.read()

            raise ArchiveCacheError(
                "zstd sorgente terminato con "
                f"codice {rc}: {stderr}"
            )

        if expected_rows is not None:
            if rows != expected_rows:
                raise ArchiveCacheError(
                    "Conteggio record non coerente: "
                    f"catalogo={expected_rows:,}, "
                    f"cache={rows:,}"
                )

        created_at = (
            datetime.now()
            .astimezone()
            .isoformat(timespec="seconds")
        )

        manifest = (
            temp_dir /
            "manifest.txt"
        )

        with manifest.open(
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                f"CACHE_VERSION="
                f"{CACHE_VERSION}\n"
            )
            f.write(
                f"FORMAT={CACHE_FORMAT}\n"
            )
            f.write(
                f"SEGMENT_MINUTES="
                f"{SEGMENT_MINUTES}\n"
            )
            f.write(
                f"DATE={log_date}\n"
            )
            f.write(
                f"TABLE="
                f"{archive['table_name']}\n"
            )
            f.write(
                f"SOURCE="
                f"{archive_path}\n"
            )
            f.write(
                f"SOURCE_BYTES="
                f"{source_bytes}\n"
            )
            f.write(
                f"SOURCE_SHA256="
                f"{actual_sha}\n"
            )
            f.write(
                f"ROWS={rows}\n"
            )
            f.write(
                f"SEGMENTS="
                f"{len(segment_rows)}\n"
            )
            f.write(
                f"CREATED_AT="
                f"{created_at}\n"
            )

            for seg in sorted(segment_rows):
                f.write(
                    f"SEGMENT_{seg}_ROWS="
                    f"{segment_rows[seg]}\n"
                )

            # Scritto per ultimo: una cache senza
            # COMPLETE=1 non deve essere utilizzata.
            f.write("COMPLETE=1\n")

        if final_dir.exists():
            old_dir = (
                CACHE_ROOT /
                f".{log_date}.old"
            )

            if old_dir.exists():
                shutil.rmtree(old_dir)

            os.rename(
                final_dir,
                old_dir,
            )

            try:
                os.rename(
                    temp_dir,
                    final_dir,
                )
            except Exception:
                os.rename(
                    old_dir,
                    final_dir,
                )
                raise

            shutil.rmtree(
                old_dir,
                ignore_errors=True,
            )
        else:
            os.rename(
                temp_dir,
                final_dir,
            )

        elapsed = (
            time.monotonic()
            - started
        )

        compressed_bytes = sum(
            p.stat().st_size
            for p in final_dir.glob(
                "*.tsv.zst"
            )
        )

        return {
            "status": "built",
            "rows": rows,
            "segments": len(segment_rows),
            "bytes": compressed_bytes,
            "elapsed": elapsed,
            "path": str(final_dir),
        }

    except Exception:
        if segment_input is not None:
            try:
                segment_input.close()
            except Exception:
                pass

        if segment_proc is not None:
            try:
                segment_proc.terminate()
            except Exception:
                pass

        if segment_output is not None:
            try:
                segment_output.close()
            except Exception:
                pass

        if zstd_proc is not None:
            try:
                zstd_proc.terminate()
            except Exception:
                pass

        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )

        raise


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--date",
        required=True,
    )

    parser.add_argument(
        "--force",
        action="store_true",
    )

    args = parser.parse_args()

    print(
        f"Preparazione cache archivio "
        f"{args.date}...",
        flush=True,
    )

    result = build_cache(
        args.date,
        force=args.force,
    )

    print()
    print(
        f"Stato:        "
        f"{result['status']}"
    )
    print(
        f"Record:       "
        f"{result['rows']:,}"
    )
    print(
        f"Segmenti:     "
        f"{result['segments']}"
    )
    print(
        f"Dimensione:   "
        f"{result['bytes'] / 1024 / 1024:.2f} MiB"
    )
    print(
        f"Tempo:        "
        f"{result['elapsed']:.2f} s"
    )
    print(
        f"Directory:    "
        f"{result['path']}"
    )


if __name__ == "__main__":
    main()
