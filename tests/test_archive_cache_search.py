import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import archive_cache_search as cache_search


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


class ArchiveCacheSearchTests(unittest.TestCase):
    def test_parse_legacy_row(self):
        row = cache_search.parse_cache_line(
            "2026-09-20T12:00:00.000+02:00\t"
            "10.0.0.10\t12345\t1.1.1.1\t443\tTCP\n"
        )

        self.assertEqual(row["source_ip"], "10.0.0.10")
        self.assertEqual(row["source_port"], 12345)
        self.assertIsNone(row["nat_source_ip"])
        self.assertIsNone(row["nat_source_port"])
        self.assertEqual(row["dest_ip"], "1.1.1.1")
        self.assertEqual(row["dest_port"], 443)

    def test_parse_tsv_v2_row_preserves_nat(self):
        row = cache_search.parse_cache_line(
            "2026-09-20T12:00:00.000+02:00\t"
            "10.0.0.10\t12345\t203.0.113.10\t54321\t"
            "1.1.1.1\t443\tUDP\n"
        )

        self.assertEqual(row["nat_source_ip"], "203.0.113.10")
        self.assertEqual(row["nat_source_port"], 54321)
        self.assertEqual(row["protocol"], "UDP")

    def test_search_cache_filters_nat_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root / "2026-09-20"
            day.mkdir()
            (day / "manifest.txt").write_text(
                "COMPLETE=1\n",
                encoding="utf-8",
            )
            (day / "1200.tsv.zst").touch()

            rows = (
                "2026-09-20T12:01:00.000+02:00\t"
                "10.0.0.10\t12345\t203.0.113.10\t54321\t"
                "1.1.1.1\t443\tTCP\n"
                "2026-09-20T12:02:00.000+02:00\t"
                "10.0.0.11\t12346\t203.0.113.11\t54322\t"
                "8.8.8.8\t53\tUDP\n"
            )

            with patch.object(cache_search, "CACHE_ROOT", root), patch.object(
                cache_search.subprocess,
                "Popen",
                return_value=FakeProcess(rows),
            ):
                result = cache_search.search_cache(
                    log_date="2026-09-20",
                    start_iso="2026-09-20T12:00:00.000",
                    end_iso="2026-09-20T12:14:59.999",
                    nat_source_ip="203.0.113.10",
                    nat_source_port=54321,
                    limit=10,
                )

        self.assertEqual(result["count"], 1)
        self.assertEqual(
            result["results"][0]["source_ip"],
            "10.0.0.10",
        )
        self.assertEqual(
            result["results"][0]["nat_source_port"],
            54321,
        )


if __name__ == "__main__":
    unittest.main()
