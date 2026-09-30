from __future__ import annotations

import argparse
import fcntl
import time
from pathlib import Path

from .archive_cache import (
    CACHE_ROOT,
    build_cache,
    cache_is_valid,
)
from .database import app_db


def get_archives():
    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    log_date,
                    table_name,
                    archive_path,
                    row_count,
                    sha256,
                    schema_generation,
                    archive_status
                FROM archive_catalog
                WHERE archive_status = 'AVAILABLE'
                  AND schema_generation IN ('legacy', 'nat-v1')
                ORDER BY log_date ASC
                """
            )
            return cur.fetchall()
    finally:
        conn.close()


def classify_archives():
    archives = get_archives()

    valid = []
    pending = []

    for archive in archives:
        log_date = str(archive["log_date"])
        archive_path = Path(
            archive["archive_path"]
        )

        expected_rows = archive["row_count"]

        if expected_rows is not None:
            expected_rows = int(expected_rows)

        expected_sha = archive["sha256"]

        ok, reason = cache_is_valid(
            log_date,
            archive_path,
            expected_rows,
            expected_sha,
        )

        item = {
            "date": log_date,
            "table": archive["table_name"],
            "rows": expected_rows,
            "reason": reason,
        }

        if ok:
            valid.append(item)
        else:
            pending.append(item)

    return archives, valid, pending


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Numero massimo di cache da costruire",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Mostra solo lo stato senza costruire cache",
    )

    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit(
            "--limit deve essere almeno 1"
        )

    CACHE_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    #
    # Lock globale del builder.
    #
    # Impedisce che un'esecuzione manuale e quella
    # avviata da systemd costruiscano contemporaneamente
    # la stessa cache.
    #
    lock_path = CACHE_ROOT / ".builder.lock"
    lock_file = lock_path.open("a+")

    try:
        fcntl.flock(
            lock_file.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    except BlockingIOError:
        print(
            "Un altro Archive Search Cache Builder "
            "è già in esecuzione."
        )
        return

    archives, valid, pending = classify_archives()

    print()
    print("=== Archive Search Cache ===")
    print(f"Archivi catalogati: {len(archives)}")
    print(f"Cache valide:       {len(valid)}")
    print(f"Da generare:        {len(pending)}")
    print()

    if pending:
        print(
            "Prima da generare:  "
            f"{pending[0]['date']} "
            f"({pending[0]['reason']})"
        )
        print(
            "Ultima da generare: "
            f"{pending[-1]['date']} "
            f"({pending[-1]['reason']})"
        )
        print()

    if args.status:
        return

    if not pending:
        print("Tutte le cache sono valide.")
        return

    processed = 0
    failed = 0

    for item in pending[:args.limit]:
        log_date = item["date"]

        print(
            "=" * 60,
            flush=True,
        )
        print(
            f"Generazione {log_date}",
            flush=True,
        )
        print(
            f"Motivo: {item['reason']}",
            flush=True,
        )
        print(
            "=" * 60,
            flush=True,
        )

        started = time.monotonic()

        try:
            result = build_cache(
                log_date,
                force=True,
            )

            elapsed = (
                time.monotonic()
                - started
            )

            print()
            print(
                f"OK {log_date}: "
                f"{result['rows']:,} record, "
                f"{result['segments']} segmenti, "
                f"{result['bytes'] / 1024 / 1024:.2f} MiB, "
                f"{elapsed:.1f} s"
            )
            print()

            processed += 1

        except Exception as exc:
            failed += 1

            print()
            print(
                f"ERRORE {log_date}: {exc}",
                flush=True,
            )
            print()

            # Non proseguiamo automaticamente dopo un
            # errore: prima va verificata la causa.
            break

    print("=== Riepilogo ===")
    print(f"Generate: {processed}")
    print(f"Errori:   {failed}")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
