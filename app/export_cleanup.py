from datetime import datetime, timedelta
from pathlib import Path
import argparse

from .database import app_db
from .export_search import (
    EXPORT_ROOT,
    EXPORT_MAX_AGE_HOURS,
)


def get_jobs():
    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    status,
                    filename,
                    completed_at
                FROM export_jobs
                """
            )
            return cur.fetchall()
    finally:
        conn.close()


def cleanup(dry_run=True):
    now = datetime.now()
    cutoff = now - timedelta(
        hours=EXPORT_MAX_AGE_HOURS
    )

    EXPORT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    jobs = get_jobs()

    jobs_by_filename = {
        job["filename"]: job
        for job in jobs
        if job["filename"]
    }

    removed = 0
    kept = 0
    orphan = 0
    stale_part = 0

    print(
        f"Export root: {EXPORT_ROOT}"
    )
    print(
        f"Retention: {EXPORT_MAX_AGE_HOURS} ore"
    )
    print(
        f"Cutoff: {cutoff}"
    )
    print(
        f"Modalita': "
        f"{'DRY-RUN' if dry_run else 'DELETE'}"
    )
    print()

    for path in sorted(EXPORT_ROOT.iterdir()):

        if not path.is_file():
            continue

        try:
            stat = path.stat()
        except OSError as exc:
            print(
                f"ERROR {path.name}: {exc}"
            )
            continue

        mtime = datetime.fromtimestamp(
            stat.st_mtime
        )

        # File temporanei abbandonati.
        if path.name.endswith(".part"):

            if mtime < cutoff:
                stale_part += 1

                print(
                    f"STALE-PART "
                    f"{path.name} "
                    f"mtime={mtime}"
                )

                if not dry_run:
                    path.unlink()
                    removed += 1
            else:
                kept += 1

            continue

        # Gestiamo soltanto i CSV del nostro export.
        if (
            path.suffix != ".csv"
            or not path.name.startswith(
                "network-log-export"
            )
        ):
            continue

        job = jobs_by_filename.get(
            path.name
        )

        # CSV senza job associato.
        if job is None:
            if mtime < cutoff:
                orphan += 1

                print(
                    f"ORPHAN "
                    f"{path.name} "
                    f"mtime={mtime}"
                )

                if not dry_run:
                    path.unlink()
                    removed += 1
            else:
                kept += 1

            continue

        # Non tocchiamo mai job attivi.
        if job["status"] in (
            "PENDING",
            "RUNNING",
        ):
            kept += 1
            continue

        # Per i COMPLETED usiamo completed_at,
        # non il timestamp del filesystem.
        if (
            job["status"] == "COMPLETED"
            and job["completed_at"] is not None
            and job["completed_at"] < cutoff
        ):
            print(
                f"EXPIRED "
                f"job={job['id']} "
                f"{path.name} "
                f"completed={job['completed_at']}"
            )

            if not dry_run:
                path.unlink()
                removed += 1

            continue

        kept += 1

    print()
    print(
        f"removed={removed} "
        f"orphan_expired={orphan} "
        f"stale_part={stale_part} "
        f"kept={kept}"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--delete",
        action="store_true",
        help="Elimina realmente i file scaduti",
    )

    args = parser.parse_args()

    cleanup(
        dry_run=not args.delete
    )


if __name__ == "__main__":
    main()
