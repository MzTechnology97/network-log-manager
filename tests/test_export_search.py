import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import export_search


class FakeProcess:
    def __init__(self, text):
        self.stdout = io.StringIO(text)
        self.returncode = 0

    def poll(self):
        return 0

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class HistoricalExportTests(unittest.TestCase):
    def setUp(self):
        self.day = datetime(2026, 9, 20).date()
        self.archive = {
            "archive_path": "/archive/2026-09-20.sql.zst",
            "row_count": 2,
            "sha256": "synthetic-sha256",
            "table_name": "mikrotik_logs_2026_09_20",
            "schema_generation": "nat-v1",
            "has_nat": 1,
        }

    def test_archive_rows_preserve_nat_v1_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / self.day.isoformat()
            cache_dir.mkdir()
            (cache_dir / "1200.tsv.zst").touch()

            rows = (
                "2026-09-20T12:01:00.000+02:00\t"
                "10.0.0.10\t12345\t203.0.113.10\t54321\t"
                "192.0.2.10\t443\tTCP\n"
                "2026-09-20T12:02:00.000+02:00\t"
                "10.0.0.11\t12346\t203.0.113.11\t54322\t"
                "192.0.2.11\t53\tUDP\n"
            )

            with patch.object(export_search, "CACHE_ROOT", root), patch.object(
                export_search,
                "cache_is_valid",
                return_value=(True, "ok"),
            ), patch.object(
                export_search,
                "archive_has_nat",
                return_value=True,
            ), patch.object(
                export_search.subprocess,
                "Popen",
                return_value=FakeProcess(rows),
            ):
                result = list(
                    export_search._archive_rows(
                        day=self.day,
                        archive=self.archive,
                        start=datetime(2026, 9, 20, 12, 0, 0),
                        end=datetime(2026, 9, 20, 12, 14, 59, 999000),
                        nat_source_ip="203.0.113.10",
                        nat_source_port=54321,
                    )
                )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_ip"], "10.0.0.10")
        self.assertEqual(result[0]["source_port"], 12345)
        self.assertEqual(result[0]["nat_source_ip"], "203.0.113.10")
        self.assertEqual(result[0]["nat_source_port"], 54321)
        self.assertEqual(result[0]["dest_ip"], "192.0.2.10")
        self.assertEqual(result[0]["dest_port"], 443)
        self.assertEqual(result[0]["protocol"], "TCP")
        self.assertEqual(result[0]["_storage"], "archive")
        self.assertEqual(
            result[0]["_table"],
            "mikrotik_logs_2026_09_20",
        )

    def test_archive_rows_accept_legacy_cache_without_nat_filter(self):
        legacy_archive = dict(self.archive)
        legacy_archive["schema_generation"] = "legacy"
        legacy_archive["has_nat"] = 0

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / self.day.isoformat()
            cache_dir.mkdir()
            (cache_dir / "1200.tsv.zst").touch()

            rows = (
                "2026-09-20T12:01:00.000+02:00\t"
                "10.0.0.20\t23456\t192.0.2.20\t443\tTCP\n"
            )

            with patch.object(export_search, "CACHE_ROOT", root), patch.object(
                export_search,
                "cache_is_valid",
                return_value=(True, "ok"),
            ), patch.object(
                export_search,
                "archive_has_nat",
                return_value=False,
            ), patch.object(
                export_search.subprocess,
                "Popen",
                return_value=FakeProcess(rows),
            ):
                result = list(
                    export_search._archive_rows(
                        day=self.day,
                        archive=legacy_archive,
                        start=datetime(2026, 9, 20, 12, 0, 0),
                        end=datetime(2026, 9, 20, 12, 14, 59, 999000),
                    )
                )

        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["nat_source_ip"])
        self.assertIsNone(result[0]["nat_source_port"])

    def test_archive_rows_reject_nat_filter_for_legacy_archive(self):
        legacy_archive = dict(self.archive)
        legacy_archive["schema_generation"] = "legacy"
        legacy_archive["has_nat"] = 0

        with patch.object(
            export_search,
            "cache_is_valid",
            return_value=(True, "ok"),
        ), patch.object(
            export_search,
            "archive_has_nat",
            return_value=False,
        ):
            with self.assertRaises(export_search.ExportError):
                list(
                    export_search._archive_rows(
                        day=self.day,
                        archive=legacy_archive,
                        start=datetime(2026, 9, 20, 12, 0, 0),
                        end=datetime(2026, 9, 20, 12, 14, 59, 999000),
                        nat_source_ip="203.0.113.10",
                    )
                )

    def test_csv_row_keeps_nat_columns_in_expected_order(self):
        row = {
            "timestamp": "2026-09-20T12:01:00.000+02:00",
            "protocol": "TCP",
            "source_ip": "10.0.0.10",
            "source_port": 12345,
            "nat_source_ip": "203.0.113.10",
            "nat_source_port": 54321,
            "dest_ip": "192.0.2.10",
            "dest_port": 443,
            "_storage": "archive",
            "_table": "mikrotik_logs_2026_09_20",
        }

        exported = export_search.csv_row(row)

        self.assertEqual(exported[2], "10.0.0.10")
        self.assertEqual(exported[3], 12345)
        self.assertEqual(exported[4], "203.0.113.10")
        self.assertEqual(exported[5], 54321)
        self.assertEqual(exported[6], "192.0.2.10")
        self.assertEqual(exported[7], 443)
        self.assertEqual(exported[8], "archive")
        self.assertEqual(exported[9], "mikrotik_logs_2026_09_20")


if __name__ == "__main__":
    unittest.main()
