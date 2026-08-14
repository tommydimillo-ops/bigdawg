import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import agent.personal_context as personal_context


class TestPersonalContextCatalog(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "Documents"
        self.root.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_catalog_is_aggregate_only(self):
        (self.root / "ECO 302 homework.pdf").write_text("private document text")
        (self.root / "notes.txt").write_text("another private value")

        catalog = personal_context.build_catalog(
            roots=[str(self.root)], photo_db=str(self.root / "missing.sqlite")
        )
        encoded = json.dumps(catalog)

        self.assertEqual(catalog["total_files"], 2)
        self.assertNotIn("homework.pdf", encoded)
        self.assertNotIn("private document text", encoded)
        self.assertEqual(catalog["recurring_course_codes"], [])
        self.assertFalse(catalog["privacy"]["cloud_analysis_used"])

    def test_sensitive_and_hidden_names_are_excluded(self):
        (self.root / "api_key.txt").write_text("secret")
        (self.root / ".hidden.txt").write_text("hidden")
        (self.root / "safe.txt").write_text("safe")

        catalog = personal_context.build_catalog(
            roots=[str(self.root)], photo_db=str(self.root / "missing.sqlite")
        )

        self.assertEqual(catalog["total_files"], 1)

    def test_photo_database_is_opened_read_only_and_summarized(self):
        database = self.root / "Photos.sqlite"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE ZASSET (ZTRASHEDSTATE INT, ZDATECREATED REAL, ZFAVORITE INT)"
            )
            connection.execute("INSERT INTO ZASSET VALUES (0, 0, 1)")

        catalog = personal_context.build_catalog(
            roots=[str(self.root)], photo_db=str(database)
        )

        self.assertTrue(catalog["photos"]["available"])
        self.assertEqual(catalog["photos"]["asset_count"], 1)
        self.assertEqual(catalog["photos"]["earliest_year"], 2001)

    def test_save_and_load_round_trip(self):
        path = str(self.root / "catalog.json")
        catalog = {"version": 1, "total_files": 3}

        personal_context.save_catalog(catalog, path=path)

        self.assertEqual(personal_context.load_catalog(path=path), catalog)

    def test_summary_never_exposes_raw_file_details(self):
        path = str(self.root / "catalog.json")
        personal_context.save_catalog({
            "version": 1,
            "total_files": 2,
            "roots": {"Documents": 2},
            "recurring_course_codes": [{"code": "ECO 302", "mentions": 3}],
            "photos": {
                "available": True, "asset_count": 10,
                "earliest_year": 2020, "latest_year": 2026,
            },
        }, path=path)

        text = personal_context.summary_text(path=path)

        self.assertIn("ECO 302", text)
        self.assertIn("10 photo/video assets", text)
        self.assertIn("no filenames", text)


if __name__ == "__main__":
    unittest.main()
