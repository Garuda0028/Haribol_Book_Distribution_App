from __future__ import annotations

import io
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from .config import BOOK_CATALOGUE_PATH, DB_PATH, RAW_DIR


def create_backup_zip() -> bytes:
    archive_buffer = io.BytesIO()
    with TemporaryDirectory() as temporary_directory:
        database_snapshot = Path(temporary_directory) / "book_sales.sqlite3"
        with closing(sqlite3.connect(DB_PATH)) as source:
            with closing(sqlite3.connect(database_snapshot)) as destination:
                source.backup(destination)

        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(database_snapshot, "book_sales.sqlite3")
            if BOOK_CATALOGUE_PATH.exists():
                archive.write(BOOK_CATALOGUE_PATH, "book_catalogue.csv")
            if RAW_DIR.exists():
                for raw_file in RAW_DIR.glob("*.csv"):
                    archive.write(raw_file, f"raw/{raw_file.name}")
    return archive_buffer.getvalue()
