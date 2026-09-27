from __future__ import annotations

import fcntl
import hashlib
import os
import sys
from pathlib import Path

from .database import app_db
from .export_search import (
    EXPORT_ROOT,
    ExportError,
    generate_advanced_export,
)


LOCK_PATH = Path(
    "/var/lib/netlog-manager/export-worker.lock"
)


def acquire_lock():
    LOCK_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fh = open(LOCK_PATH, "a+")

    try:
        fcntl.flock(
            fh.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    except BlockingIOError:
        fh.close()
        return None

    return fh


def recover_interrupted_jobs():
    conn = app_db()

    try:
        #
        # Prima identifichiamo esattamente i job
        # rimasti RUNNING.
        #
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM export_jobs
                WHERE status='RUNNING'
                ORDER BY id
                """
            )

            jobs = cur.fetchall()

        #
        # Un job RUNNING non possiede mai un
        # risultato ufficialmente completato.
        #
        # Rimuoviamo quindi entrambi i possibili
        # artefatti:
        #
        #   .csv.part -> crash durante scrittura
        #   .csv      -> crash dopo rename ma
        #               prima di COMPLETED
        #
        for job in jobs:
            job_id = job["id"]

            final_path = (
                EXPORT_ROOT
                / f"network-log-export-job-{job_id}.csv"
            )

            part_path = Path(
                str(final_path) + ".part"
            )

            for artifact in (
                part_path,
                final_path,
            ):
                try:
                    artifact.unlink()
                except FileNotFoundError:
                    pass

        if not jobs:
            return 0

        job_ids = [
            job["id"]
            for job in jobs
        ]

        placeholders = ",".join(
            ["%s"] * len(job_ids)
        )

        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE export_jobs
                SET
                    status='PENDING',
                    progress_rows=0,
                    progress_day=NULL,
                    online_days=0,
                    archive_days=0,
                    result_rows=NULL,
                    filename=NULL,
                    file_size=NULL,
                    file_sha256=NULL,
                    error_message=
                        'Recovered after interrupted worker',
                    started_at=NULL,
                    completed_at=NULL
                WHERE status='RUNNING'
                  AND id IN ({placeholders})
                """,
                job_ids,
            )

            recovered = cur.rowcount

        conn.commit()

        return recovered

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def claim_next_job():
    conn = app_db()

    try:
        conn.begin()

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM export_jobs
                WHERE status='PENDING'
                ORDER BY requested_at ASC, id ASC
                LIMIT 1
                FOR UPDATE
                """
            )

            job = cur.fetchone()

            if job is None:
                conn.rollback()
                return None

            cur.execute(
                """
                UPDATE export_jobs
                SET
                    status='RUNNING',
                    started_at=CURRENT_TIMESTAMP(3),
                    completed_at=NULL,
                    progress_rows=0,
                    progress_day=NULL,
                    result_rows=NULL,
                    online_days=0,
                    archive_days=0,
                    filename=NULL,
                    file_size=NULL,
                    file_sha256=NULL,
                    error_message=NULL
                WHERE id=%s
                  AND status='PENDING'
                """,
                (job["id"],),
            )

            if cur.rowcount != 1:
                conn.rollback()
                return None

        conn.commit()

        return job

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def update_progress(
    job_id,
    *,
    row_count,
    current_day,
    online_days,
    archive_days,
):
    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE export_jobs
                SET
                    progress_rows=%s,
                    progress_day=%s,
                    online_days=%s,
                    archive_days=%s
                WHERE id=%s
                  AND status='RUNNING'
                """,
                (
                    row_count,
                    current_day,
                    online_days,
                    archive_days,
                    job_id,
                ),
            )

        conn.commit()

    finally:
        conn.close()


def sha256_file(path: Path):
    digest = hashlib.sha256()

    with path.open("rb") as fh:
        while True:
            block = fh.read(
                8 * 1024 * 1024
            )

            if not block:
                break

            digest.update(block)

    return digest.hexdigest()


def mark_completed(job_id, result):
    path = Path(result["path"])

    file_size = path.stat().st_size
    file_sha256 = sha256_file(path)

    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE export_jobs
                SET
                    status='COMPLETED',
                    progress_rows=%s,
                    progress_day=NULL,
                    result_rows=%s,
                    online_days=%s,
                    archive_days=%s,
                    filename=%s,
                    file_size=%s,
                    file_sha256=%s,
                    error_message=NULL,
                    completed_at=CURRENT_TIMESTAMP(3)
                WHERE id=%s
                  AND status='RUNNING'
                """,
                (
                    result["rows"],
                    result["rows"],
                    result["online_days"],
                    result["archive_days"],
                    result["filename"],
                    file_size,
                    file_sha256,
                    job_id,
                ),
            )

            if cur.rowcount != 1:
                raise RuntimeError(
                    "Impossibile completare il job"
                )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def mark_failed(job_id, message):
    message = str(message)

    if len(message) > 4000:
        message = message[:4000]

    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE export_jobs
                SET
                    status='FAILED',
                    error_message=%s,
                    completed_at=CURRENT_TIMESTAMP(3)
                WHERE id=%s
                  AND status='RUNNING'
                """,
                (
                    message,
                    job_id,
                ),
            )

        conn.commit()

    finally:
        conn.close()


def process_job(job):
    job_id = job["id"]

    final_path = (
        EXPORT_ROOT
        / f"network-log-export-job-{job_id}.csv"
    )

    part_path = Path(
        str(final_path) + ".part"
    )

    #
    # Recovery sicuro:
    # un precedente worker interrotto potrebbe
    # aver lasciato solamente il file temporaneo.
    #
    try:
        part_path.unlink()
    except FileNotFoundError:
        pass

    #
    # Un job PENDING/RUNNING non deve mai
    # sovrascrivere silenziosamente un risultato
    # definitivo già esistente.
    #
    if final_path.exists():
        raise RuntimeError(
            f"File definitivo già esistente: "
            f"{final_path.name}"
        )

    def progress_callback(
        *,
        row_count,
        current_day,
        online_days,
        archive_days,
    ):
        update_progress(
            job_id,
            row_count=row_count,
            current_day=current_day,
            online_days=online_days,
            archive_days=archive_days,
        )

    try:
        result = generate_advanced_export(
            start=job["start_time"],
            end=job["end_time"],
            source_ip=job["source_ip"],
            source_port=job["source_port"],
            nat_source_ip=job["nat_source_ip"],
            nat_source_port=job["nat_source_port"],
            dest_ip=job["dest_ip"],
            dest_port=job["dest_port"],
            protocol=job["protocol"],
            progress_callback=progress_callback,
            output_path=part_path,
        )

        if not part_path.is_file():
            raise RuntimeError(
                "File temporaneo export "
                "non trovato."
            )

        #
        # generate_advanced_export ha già
        # eseguito flush + fsync + close.
        #
        # os.replace() rende visibile il nome
        # definitivo con un rename atomico
        # sullo stesso filesystem.
        #
        os.replace(
            part_path,
            final_path,
        )

        #
        # Rendiamo persistente anche la modifica
        # della directory che contiene il rename.
        #
        dir_fd = os.open(
            EXPORT_ROOT,
            os.O_RDONLY,
        )

        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

        result["path"] = final_path
        result["filename"] = final_path.name

        try:
            mark_completed(
                job_id,
                result,
            )
        except Exception:
            #
            # Se il DB non registra COMPLETED,
            # il file definitivo non deve restare
            # disponibile come risultato orfano.
            #
            try:
                final_path.unlink()
            except FileNotFoundError:
                pass

            raise

        return result

    except Exception:
        #
        # Nessun .part deve sopravvivere
        # ad un errore gestito.
        #
        try:
            part_path.unlink()
        except FileNotFoundError:
            pass

        raise


def main():
    lock_fh = acquire_lock()

    if lock_fh is None:
        print(
            "Un altro Export Worker "
            "è già in esecuzione."
        )
        return 0

    completed = 0
    failed = 0

    try:
        recovered = recover_interrupted_jobs()

        if recovered:
            print(
                f"Job RUNNING recuperati: "
                f"{recovered}"
            )

        while True:
            job = claim_next_job()

            if job is None:
                if completed == 0 and failed == 0:
                    print(
                        "Nessun export PENDING."
                    )
                else:
                    print(
                        "Coda export completata: "
                        f"{completed} COMPLETED, "
                        f"{failed} FAILED."
                    )

                break

            job_id = job["id"]

            print(
                f"Elaborazione export job "
                f"#{job_id}"
            )

            try:
                result = process_job(job)

            except Exception as exc:
                failed += 1

                mark_failed(
                    job_id,
                    f"{type(exc).__name__}: {exc}",
                )

                print(
                    f"Job #{job_id} FAILED: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )

                #
                # Un job fallito non deve bloccare
                # gli export successivi in coda.
                #
                continue

            completed += 1

            print(
                f"Job #{job_id} COMPLETED: "
                f"{result['rows']:,} righe, "
                f"{result['filename']}"
            )

        #
        # Il servizio è sano anche se un singolo
        # job è fallito: il fallimento è persistito
        # nello stato FAILED del job.
        #
        return 0

    finally:
        try:
            lock_fh.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
