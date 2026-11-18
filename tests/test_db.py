import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from book_sales import db


class DatabaseMigrationTests(unittest.TestCase):
    def test_inventory_and_subcategory_fields_are_added_to_existing_database(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            database_path = data_dir / "book_sales.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE book_variants (
                        id INTEGER PRIMARY KEY,
                        book_id INTEGER,
                        language TEXT,
                        book_category TEXT,
                        price REAL,
                        points REAL,
                        active INTEGER,
                        updated_at TEXT
                    );
                    CREATE TABLE sales (
                        id INTEGER PRIMARY KEY,
                        seller_name TEXT,
                        sale_date TEXT,
                        book_name TEXT
                    );
                    """
                )
            finally:
                connection.close()

            @contextmanager
            def managed_connect():
                connection = sqlite3.connect(database_path)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                try:
                    yield connection
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
                finally:
                    connection.close()

            with (
                patch.object(db, "DATA_DIR", data_dir),
                patch.object(db, "DB_PATH", database_path),
                patch.object(db, "RAW_DIR", data_dir / "raw"),
                patch.object(db, "connect", managed_connect),
                patch("book_sales.catalogue.sync_book_catalogue"),
            ):
                db.initialize_database()

                connection = sqlite3.connect(database_path)
                connection.row_factory = sqlite3.Row
                try:
                    variant_columns = {
                        row["name"]
                        for row in connection.execute(
                            "PRAGMA table_info(book_variants)"
                        )
                    }
                    sale_columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(sales)")
                    }
                finally:
                    connection.close()
                self.assertIn("book_sub_category", variant_columns)
                self.assertIn("stock", variant_columns)
                self.assertIn("ibc_s_stock_amt", variant_columns)
                self.assertIn("book_sub_category", sale_columns)


if __name__ == "__main__":
    unittest.main()
