import csv
import io
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from book_sales.analytics import accumulated_points, export_csv, export_sales_csv
from book_sales.settings import (
    _persistent_settings_from_secrets,
    get_settings,
    save_settings,
)


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            "CREATE TABLE sales (sale_date TEXT, points REAL, quantity INTEGER, "
            "reported_points_total REAL)"
        )
        self.connection.executemany(
            "INSERT INTO sales VALUES (?, ?, ?, NULL)",
            [
                ("2026-04-30", 1, 5),
                ("2026-05-01", 2, 3),
                ("2026-05-02", 4, 2),
                ("2026-05-03", 1, 10),
            ],
        )

        def query_df(sql, params):
            return pd.read_sql_query(sql, self.connection, params=params)

        self.query_patch = patch("book_sales.analytics.query_df", side_effect=query_df)
        self.query_patch.start()

    def tearDown(self):
        self.query_patch.stop()
        self.connection.close()

    def test_accumulated_points_obeys_inclusive_target_dates(self):
        self.assertEqual(
            accumulated_points("2026-05-04", "2026-05-01", "2026-05-02"),
            14,
        )
        self.assertEqual(
            accumulated_points("2026-05-01", "2026-05-02", "2026-05-03"),
            0,
        )
        self.assertEqual(accumulated_points("2026-05-02"), 19)

    def test_accumulated_points_uses_sheet_reported_total_when_present(self):
        self.connection.execute(
            "UPDATE sales SET reported_points_total=7 WHERE sale_date='2026-05-01'"
        )

        self.assertEqual(
            accumulated_points("2026-05-01", "2026-05-01", "2026-05-01"),
            7,
        )

    def test_export_escapes_formula_text_but_preserves_numbers(self):
        frame = pd.DataFrame(
            {
                "seller": [
                    "=1+1",
                    " \t@SUM(A1:A5)",
                    "\u200b-1+2",
                    "Haribol",
                ],
                "quantity": [1, 2, 3, 4],
                "amount": [10.5, 0, -2, 7],
            }
        )

        rows = list(
            csv.DictReader(
                io.StringIO(export_sales_csv(frame).decode("utf-8-sig"))
            )
        )

        self.assertEqual(
            [row["seller"] for row in rows],
            ["'=1+1", "' \t@SUM(A1:A5)", "'\u200b-1+2", "Haribol"],
        )
        self.assertEqual([row["quantity"] for row in rows], ["1", "2", "3", "4"])
        self.assertEqual([row["amount"] for row in rows], ["10.5", "0.0", "-2.0", "7.0"])

    def test_generic_csv_export_escapes_import_history_text(self):
        frame = pd.DataFrame({"notes": ["=HYPERLINK(\"bad\")"]})

        exported = export_csv(frame).decode("utf-8-sig")

        self.assertIn("'=HYPERLINK", exported)

    def test_settings_reject_a_reversed_target_period(self):
        with patch("book_sales.settings.save_env_settings") as save_env_settings:
            with self.assertRaisesRegex(ValueError, "start date"):
                save_settings(
                    {
                        "overall_target_start_date": "2026-05-03",
                        "overall_target_end_date": "2026-05-01",
                    }
                )
            save_env_settings.assert_not_called()

    def test_settings_save_configured_target_dates(self):
        with patch("book_sales.settings.save_env_settings") as save_env_settings:
            save_settings(
                {
                    "overall_target_start_date": "2026-05-01",
                    "overall_target_end_date": "2026-05-31",
                }
            )

        save_env_settings.assert_called_once_with(
            {
                "OVERALL_TARGET_START_DATE": "2026-05-01",
                "OVERALL_TARGET_END_DATE": "2026-05-31",
            }
        )

    def test_google_sheet_settings_save_to_database(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE TABLE settings (setting_key TEXT PRIMARY KEY, setting_value TEXT)"
        )
        try:
            with patch("book_sales.settings.connect", return_value=connection):
                save_settings(
                    {
                        "spreadsheet_id": (
                            "https://docs.google.com/spreadsheets/d/"
                            "configured-sheet_id/edit"
                        ),
                        "lists_tab": "Books",
                        "responses_tab": "Web responses",
                        "submissions_tab": "Summaries",
                        "google_sheets_auto_sync": True,
                        "google_sheets_sync_interval_minutes": 5,
                        "google_sheets_manual_sync": False,
                    }
                )

            settings = dict(
                connection.execute(
                    "SELECT setting_key, setting_value FROM settings"
                ).fetchall()
            )
            self.assertEqual(settings["spreadsheet_id"], "configured-sheet_id")
            self.assertEqual(settings["lists_tab"], "Books")
            self.assertEqual(settings["google_sheets_auto_sync"], "1")
            self.assertEqual(settings["google_sheets_sync_interval_minutes"], "5")
            self.assertEqual(settings["google_sheets_manual_sync"], "0")
        finally:
            connection.close()

    def test_google_sheet_sync_settings_default_to_five_minutes_and_manual_allowed(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE TABLE settings (setting_key TEXT PRIMARY KEY, setting_value TEXT)"
        )
        try:
            with (
                patch("book_sales.settings.connect", return_value=connection),
                patch("book_sales.settings.get_env_setting", return_value=None),
            ):
                settings = get_settings()

            self.assertEqual(settings["google_sheets_auto_sync"], "1")
            self.assertEqual(settings["google_sheets_sync_interval_minutes"], "5")
            self.assertEqual(settings["google_sheets_manual_sync"], "1")
        finally:
            connection.close()

    def test_persistent_settings_are_normalized_and_validated(self):
        settings = _persistent_settings_from_secrets(
            {
                "app_settings": {
                    "spreadsheet_id": (
                        "https://docs.google.com/spreadsheets/d/"
                        "persistent-sheet/edit"
                    ),
                    "overall_target": 250,
                    "google_sheets_auto_sync": False,
                    "google_sheets_sync_interval_minutes": 15,
                }
            }
        )

        self.assertEqual(settings["spreadsheet_id"], "persistent-sheet")
        self.assertEqual(settings["overall_target"], "250.0")
        self.assertEqual(settings["google_sheets_auto_sync"], "0")
        self.assertEqual(settings["google_sheets_sync_interval_minutes"], "15")

    def test_persistent_settings_reject_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "sync interval"):
            _persistent_settings_from_secrets(
                {"app_settings": {"google_sheets_sync_interval_minutes": 0}}
            )
        with self.assertRaisesRegex(ValueError, "supported application settings"):
            _persistent_settings_from_secrets(
                {"app_settings": {"unknown_setting": "value"}}
            )

    def test_persistent_settings_override_database_values(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE settings (setting_key TEXT PRIMARY KEY, setting_value TEXT)"
        )
        connection.executemany(
            "INSERT INTO settings VALUES (?, ?)",
            [("overall_target", "20"), ("spreadsheet_id", "database-sheet")],
        )
        try:
            with (
                patch("book_sales.settings.connect", return_value=connection),
                patch("book_sales.settings.get_env_setting", return_value=None),
                patch(
                    "book_sales.settings.get_persistent_settings",
                    return_value={
                        "overall_target": "250.0",
                        "spreadsheet_id": "persistent-sheet",
                    },
                ),
            ):
                settings = get_settings()

            self.assertEqual(settings["overall_target"], "250.0")
            self.assertEqual(settings["spreadsheet_id"], "persistent-sheet")
        finally:
            connection.close()

    def test_google_sheet_sync_interval_must_be_whole_minutes_between_one_and_1440(self):
        for invalid_interval in (0, 1441, 5.5, "five"):
            with self.subTest(interval=invalid_interval):
                with self.assertRaisesRegex(ValueError, "sync interval"):
                    save_settings(
                        {
                            "google_sheets_sync_interval_minutes": invalid_interval,
                        }
                    )

    def test_google_sheet_sync_boolean_settings_reject_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            save_settings({"google_sheets_manual_sync": "sometimes"})


if __name__ == "__main__":
    unittest.main()
