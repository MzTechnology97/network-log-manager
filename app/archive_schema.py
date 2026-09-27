from __future__ import annotations

import argparse
import re
import subprocess

from .database import app_db


COLUMN_RE = re.compile(
    r"^\s*`([A-Za-z0-9_]+)`\s+",
    re.MULTILINE,
)

EXPECTED_BASE = {
    "timestamp",
    "source_ip",
    "source_port",
    "dest_ip",
    "dest_port",
    "protocol",
}


def read_create_table(archive_path: str) -> str:
    proc = subprocess.Popen(
        [
            "/usr/bin/zstd",
            "-dc",
            "--",
            archive_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    lines = []
    found = False

    try:
        assert proc.stdout is not None

        for line in proc.stdout:
            if not found:
                if line.startswith("CREATE TABLE "):
                    found = True
                    lines.append(line)
                continue

            lines.append(line)

            if line.startswith(") ENGINE="):
                break

        if not found:
            raise RuntimeError(
                "CREATE TABLE non trovato"
            )

        return "".join(lines)

    finally:
        if proc.stdout is not None:
            proc.stdout.close()

        # Non dobbiamo decomprimere il resto del dump.
        if proc.poll() is None:
            proc.terminate()

            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def classify_schema(create_sql: str):
    columns = set(
        COLUMN_RE.findall(create_sql)
    )

    missing_base = EXPECTED_BASE - columns

    if missing_base:
        return (
            "unknown",
            0,
            columns,
            "missing:"
            + ",".join(sorted(missing_base)),
        )

    has_nat_ip = "nat_source_ip" in columns
    has_nat_port = "nat_source_port" in columns

    if has_nat_ip != has_nat_port:
        return (
            "unknown",
            0,
            columns,
            "partial_nat_schema",
        )

    has_id = "id" in columns

    # Schema storico originale:
    # 6 colonne senza id e senza NAT.
    if not has_id and not has_nat_ip:
        generation = "legacy"

    # Schema normalizzato senza NAT.
    elif has_id and not has_nat_ip:
        generation = "normalized"

    # Schema con NAT.
    elif has_id and has_nat_ip:
        generation = "nat"

    else:
        generation = "unknown"

    return (
        generation,
        1 if has_nat_ip else 0,
        columns,
        None,
    )


def load_archives():
    conn = app_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    log_date,
                    table_name,
                    archive_path
                FROM archive_catalog
                WHERE archive_status = 'AVAILABLE'
                ORDER BY log_date
                """
            )

            return cur.fetchall()

    finally:
        conn.close()


def save_classifications(results):
    conn = app_db()

    try:
        with conn.cursor() as cur:
            for item in results:
                cur.execute(
                    """
                    UPDATE archive_catalog
                    SET
                        schema_generation = %s,
                        has_nat = %s
                    WHERE id = %s
                    """,
                    (
                        item["generation"],
                        item["has_nat"],
                        item["id"],
                    ),
                )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
        help="Salva classificazione nel catalogo",
    )

    args = parser.parse_args()

    archives = load_archives()

    results = []

    counters = {
        "legacy": 0,
        "normalized": 0,
        "nat": 0,
        "unknown": 0,
        "errors": 0,
    }

    for index, archive in enumerate(
        archives,
        start=1,
    ):
        try:
            create_sql = read_create_table(
                archive["archive_path"]
            )

            (
                generation,
                has_nat,
                columns,
                warning,
            ) = classify_schema(create_sql)

            counters[generation] += 1

            results.append({
                "id": archive["id"],
                "generation": generation,
                "has_nat": has_nat,
            })

            if warning:
                print(
                    f"WARNING {archive['log_date']} "
                    f"{archive['table_name']}: "
                    f"{warning}"
                )

        except Exception as exc:
            counters["errors"] += 1

            print(
                f"ERROR {archive['log_date']} "
                f"{archive['table_name']}: {exc}"
            )

        if (
            index % 50 == 0
            or index == len(archives)
        ):
            print(
                f"Analizzati: "
                f"{index}/{len(archives)}"
            )

    print()
    print(f"Legacy:      {counters['legacy']}")
    print(
        f"Normalized:  "
        f"{counters['normalized']}"
    )
    print(f"NAT:         {counters['nat']}")
    print(f"Unknown:     {counters['unknown']}")
    print(f"Errori:      {counters['errors']}")

    if args.write:
        if counters["errors"]:
            raise SystemExit(
                "Classificazione NON salvata: "
                "sono presenti errori."
            )

        save_classifications(results)

        print()
        print(
            f"Catalogo aggiornato: "
            f"{len(results)} record"
        )
    else:
        print()
        print(
            "Dry-run: database non modificato."
        )


if __name__ == "__main__":
    main()
