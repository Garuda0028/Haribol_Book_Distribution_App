import io
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from book_sales.analytics import distribution_report
from book_sales import report_image
from book_sales.report_image import render_distribution_report_jpeg


class DistributionReportTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(
            """
            CREATE TABLE sales (
                sale_date TEXT,
                book_name TEXT,
                book_category TEXT,
                book_sub_category TEXT,
                quantity INTEGER,
                points REAL,
                reported_points_total REAL,
                shastra_daan REAL
            );
            CREATE TABLE website_submissions (
                sale_date TEXT,
                lakshmi_transferred REAL,
                shastra_daan REAL
            );
            """
        )
        self.connection.executemany(
            "INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("2026-05-03", "Small title", "Small Book", "", 2, 0.5, None, 2),
                ("2026-05-01", "Older small title", "Small Book", "", 5, 0.5, None, 0),
                (
                    "2026-05-03",
                    "Bhagavatam title",
                    "Maha Big Book",
                    "Srimad Bhagavatam",
                    1,
                    2.5,
                    3,
                    0,
                ),
                (
                    "2026-05-03",
                    "Caitanya title",
                    "Maha Big Book",
                    "Caitanya Caritamrita",
                    1,
                    4,
                    4,
                    0,
                ),
                ("2026-05-03", "Other title", "Unlisted category", "", 1, 1, None, 0),
                ("2026-05-04", "Future title", "Medium Book", "", 50, 1, 50, 50),
            ],
        )
        self.connection.executemany(
            "INSERT INTO website_submissions VALUES (?, ?, ?)",
            [
                ("2026-05-02", 4, 5),
                ("2026-05-04", 200, 100),
            ],
        )

        def query_df(sql, params=()):
            return pd.read_sql_query(sql, self.connection, params=params)

        self.query_patch = patch(
            "book_sales.analytics.query_df",
            side_effect=query_df,
        )
        self.query_patch.start()
        self.settings_patch = patch(
            "book_sales.analytics.get_settings",
            return_value={
                "overall_target": "10",
                "overall_target_start_date": "2026-05-02",
                "overall_target_end_date": "2026-05-03",
            },
        )
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()
        self.query_patch.stop()
        self.connection.close()

    def test_report_groups_categories_and_uses_configured_summary_fields(self):
        report = distribution_report("2026-05-03")

        self.assertEqual(
            [row["category"] for row in report["daily"]],
            [
                "Small Books",
                "Medium Books",
                "Big Books",
                "Maha Big Books",
                "Bhagavatam",
                "Caitanya Caritamrita",
                "Other",
            ],
        )
        daily = {row["category"]: row for row in report["daily"]}
        self.assertEqual(daily["Small Books"]["copies"], 2)
        self.assertEqual(daily["Bhagavatam"]["points"], 3)
        self.assertEqual(daily["Caitanya Caritamrita"]["points"], 4)
        self.assertEqual(report["daily_total_copies"], 5)
        self.assertEqual(report["daily_total_points"], 9)

        overall = {row["category"]: row for row in report["overall"]}
        self.assertEqual(overall["Small Books"]["copies"], 2)
        self.assertEqual(report["overall_total_copies"], 5)
        self.assertEqual(report["overall_total_points"], 9)
        self.assertEqual(report["total_book_points"], 9)
        self.assertEqual(report["target_progress_pct"], 90)
        self.assertEqual(report["shastra_daan_collected"], 7)
        self.assertEqual(report["total_lakshmi_collected"], 4)

    def test_selected_date_outside_configured_period_has_no_daily_totals(self):
        report = distribution_report("2026-05-04")

        self.assertEqual(report["daily_total_copies"], 0)
        self.assertEqual(report["daily_total_points"], 0)
        self.assertEqual(report["overall_total_copies"], 5)
        self.assertEqual(report["total_book_points"], 9)
        self.assertEqual(report["shastra_daan_collected"], 7)
        self.assertEqual(report["total_lakshmi_collected"], 4)

    def test_report_renders_a_valid_jpeg_image(self):
        report = distribution_report("2026-05-03")
        jpeg = render_distribution_report_jpeg(report)

        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        with Image.open(io.BytesIO(jpeg)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.width, 1080)
            self.assertGreater(image.height, 900)

    def test_currency_rows_render_rupee_symbol_without_system_glyph(self):
        report = distribution_report("2026-05-03")
        with (
            patch(
                "book_sales.report_image._font",
                return_value=ImageFont.load_default(size=25),
            ),
            patch(
                "book_sales.report_image._draw_currency_cell",
                wraps=report_image._draw_currency_cell,
            ) as draw_currency_cell,
        ):
            jpeg = render_distribution_report_jpeg(report)

        self.assertEqual(
            [call.args[3] for call in draw_currency_cell.call_args_list],
            [7, 4],
        )
        with Image.open(io.BytesIO(jpeg)) as image:
            draw = ImageDraw.Draw(image)
            for call in draw_currency_cell.call_args_list:
                bounds, amount, font, symbol = (
                    call.args[2], call.args[3], call.args[5], call.args[6]
                )
                text_bounds = draw.textbbox((0, 0), f"{amount:,.2f}", font=font)
                text_width = text_bounds[2] - text_bounds[0]
                text_height = text_bounds[3] - text_bounds[1]
                symbol_width = max(1, round(symbol.width * text_height / symbol.height))
                expected = symbol.resize(
                    (symbol_width, text_height), Image.Resampling.LANCZOS
                )
                x = round(bounds[0] + (bounds[2] - bounds[0] - symbol_width - 4 - text_width) / 2)
                y = round(bounds[1] + (bounds[3] - bounds[1] - text_height) / 2)
                opaque_pixels = [
                    (sx, sy)
                    for sy in range(text_height)
                    for sx in range(symbol_width)
                    if expected.getpixel((sx, sy))[3] > 230
                ]
                self.assertGreater(len(opaque_pixels), 0)
                dark_pixels = sum(
                    max(image.getpixel((x + sx, y + sy))) < 100
                    for sx, sy in opaque_pixels
                )
                self.assertGreater(dark_pixels / len(opaque_pixels), 0.65)


if __name__ == "__main__":
    unittest.main()
