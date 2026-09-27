from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

from .config import ENV
from .database import app_db


TABLE_RE = re.compile(
    r"^mikrotik_logs_(\d{4})_(\d{2})_(\d{2})$"
)

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def parse_meta(path: Path) -> dict:
    data = {}

    with path.open(
        "r",
        encoding="utf-8",
        errors="strict",
    ) as fh:
        for raw_line in fh:
            line = raw_line.strip()

            if not line or "=" not in line:
                continue

            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()

    return data


def parse_sha256_file(path: Path) -> str | None:
    try:
        line = path.read_text(
            encoding="utf-8",
            errors="strict",
        ).strip()
    except OSError:
        return None

    if not line:
        return None

    digest = line.split()[0].lower()

    if not SHA256_RE.fullmatch(digest):
        return None

    return digest


def parse_archived_at(value: str | None):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None

    # MariaDB DATETIME non conserva timezone.
    # Manteniamo l'ora locale registrata nel meta.
    return dt.replace(tzinfo=None)


def safe_int(value):
    if value is None:
        return None

    try:
        number = int(value)
    except (TypeError, ValueError):
        return None

    if number < 0:
        return None

    return number


def scan_archive(root: Path):
    records = []

    # Solo directory YYYY:
    # /archive/mikrotik/test viene esclusa.
    year_dirs = sorted(
        p for p in root.iterdir()
        if p.is_dir()
        and re.fullmatch(r"\d{4}", p.name)
    )

    for year_dir in year_dirs:
        for meta_path in sorted(
            year_dir.rglob("mikrotik_logs_*.meta")
        ):
            meta = parse_meta(meta_path)

            table_name = meta.get("TABLE", "").strip()
            match = TABLE_RE.fullmatch(table_name)

            problems = []

            if not match:
                problems.append("invalid_table_name")
                continue

            year, month, day = map(
                int,
                match.groups(),
            )

            try:
                filename_date = datetime(
                    year,
                    month,
                    day,
                ).date()
            except ValueError:
                problems.append("invalid_filename_date")
                continue

            meta_date_raw = meta.get("DATE")

            try:
                meta_date = datetime.strptime(
                    meta_date_raw,
                    "%Y-%m-%d",
                ).date()
            except (TypeError, ValueError):
                meta_date = None
                problems.append("invalid_meta_date")

            if (
                meta_date is not None
                and meta_date != filename_date
            ):
                problems.append("date_mismatch")

            expected_meta_name = (
                f"{table_name}.meta"
            )

            if meta_path.name != expected_meta_name:
                problems.append("meta_name_mismatch")

            archive_path = (
                meta_path.parent
                / f"{table_name}.sql.zst"
            )

            sha256_path = Path(
                str(archive_path) + ".sha256"
            )

            if not archive_path.is_file():
                problems.append("archive_missing")

            if not sha256_path.is_file():
                problems.append("sha256_missing")

            meta_sha = (
                meta.get("SHA256", "")
                .strip()
                .lower()
            )

            if not SHA256_RE.fullmatch(meta_sha):
                problems.append("invalid_meta_sha256")
                meta_sha = None

            file_sha = None

            if sha256_path.is_file():
                file_sha = parse_sha256_file(
                    sha256_path
                )

                if file_sha is None:
                    problems.append(
                        "invalid_sha256_file"
                    )

            if (
                meta_sha
                and file_sha
                and meta_sha != file_sha
            ):
                problems.append("sha256_mismatch")

            compressed_bytes = safe_int(
                meta.get("COMPRESSED_BYTES")
            )

            actual_bytes = None

            if archive_path.is_file():
                actual_bytes = (
                    archive_path.stat().st_size
                )

                if (
                    compressed_bytes is not None
                    and actual_bytes
                    != compressed_bytes
                ):
                    problems.append(
                        "compressed_size_mismatch"
                    )

            rows = safe_int(meta.get("ROWS"))

            if rows is None:
                problems.append("invalid_rows")

            retention_days = safe_int(
                meta.get("RETENTION_DAYS")
            )

            archived_at = parse_archived_at(
                meta.get("ARCHIVED_AT")
            )

            if meta.get("ARCHIVED_AT") and archived_at is None:
                problems.append(
                    "invalid_archived_at"
                )

            if not problems:
                status = "AVAILABLE"
            elif (
                "archive_missing" in problems
                or "sha256_missing" in problems
            ):
                status = "INCOMPLETE"
            else:
                status = "INVALID"

            records.append({
                "log_date": filename_date,
                "table_name": table_name,
                "archive_path": str(
                    archive_path
                ),
                "meta_path": str(
                    meta_path
                ),
                "sha256_path": str(
                    sha256_path
                ),
                "row_count": rows,
                "compressed_bytes": (
                    actual_bytes
                    if actual_bytes is not None
                    else compressed_bytes
                ),
                "sha256": (
                    meta_sha
                    if meta_sha
                    else file_sha
                ),
                "archived_at": archived_at,
                "retention_days": retention_days,
                "archive_status": status,
                "problems": problems,
            })

    return records


def write_catalog(records):
    sql = """
        INSERT INTO archive_catalog (
            log_date,
            table_name,
            archive_path,
            meta_path,
            sha256_path,
            row_count,
            compressed_bytes,
            sha256,
            archived_at,
            retention_days,
            schema_generation,
            has_nat,
            archive_status,
            integrity_verified_at,
            last_scanned_at
        )
        VALUES (
            %(log_date)s,
            %(table_name)s,
            %(archive_path)s,
            %(meta_path)s,
            %(sha256_path)s,
            %(row_count)s,
            %(compressed_bytes)s,
            %(sha256)s,
            %(archived_at)s,
            %(retention_days)s,
            NULL,
            0,
            %(archive_status)s,
            NULL,
            NOW()
        )
        ON DUPLICATE KEY UPDATE
            archive_path =
                VALUES(archive_path),
            meta_path =
                VALUES(meta_path),
            sha256_path =
                VALUES(sha256_path),
            row_count =
                VALUES(row_count),
            compressed_bytes =
                VALUES(compressed_bytes),
            sha256 =
                VALUES(sha256),
            archived_at =
                VALUES(archived_at),
            retention_days =
                VALUES(retention_days),
            archive_status =
                VALUES(archive_status),
            last_scanned_at =
                NOW()
    """

    conn = app_db()

    try:
        with conn.cursor() as cur:
            for record in records:
                cur.execute(sql, record)

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def print_summary(records):
    available = sum(
        r["archive_status"] == "AVAILABLE"
        for r in records
    )

    incomplete = sum(
        r["archive_status"] == "INCOMPLETE"
        for r in records
    )

    invalid = sum(
        r["archive_status"] == "INVALID"
        for r in records
    )

    total_bytes = sum(
        r["compressed_bytes"] or 0
        for r in records
    )

    total_rows = sum(
        r["row_count"] or 0
        for r in records
    )

    print(f"Archivi trovati:     {len(records)}")
    print(f"Disponibili:         {available}")
    print(f"Incompleti:          {incomplete}")
    print(f"Invalidi:            {invalid}")
    print(
        "Dimensione:         "
        f"{total_bytes / 1024**3:.2f} GiB"
    )
    print(
        "Righe dichiarate:    "
        f"{total_rows:,}"
    )

    if records:
        dates = sorted(
            r["log_date"]
            for r in records
        )

        print(f"Prima data:          {dates[0]}")
        print(f"Ultima data:         {dates[-1]}")

    bad = [
        r for r in records
        if r["problems"]
    ]

    if bad:
        print()
        print("ANOMALIE:")

        for record in bad:
            print(
                record["table_name"],
                "->",
                ",".join(record["problems"]),
            )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
        help="Aggiorna archive_catalog",
    )

    args = parser.parse_args()

    root = Path(
        ENV.get(
            "ARCHIVE_ROOT",
            "/archive/mikrotik",
        )
    )

    if not root.is_dir():
        raise SystemExit(
            f"Archive root non valido: {root}"
        )

    records = scan_archive(root)

    print_summary(records)

    if args.write:
        write_catalog(records)
        print()
        print(
            f"Catalogo aggiornato: {len(records)} record"
        )
    else:
        print()
        print(
            "Dry-run: database non modificato."
        )


if __name__ == "__main__":
    main()
