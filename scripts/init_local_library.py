#!/usr/bin/env python3
"""Create the empty, local SQLite library layout used by OOH Story.

The initializer is deliberately data-free and idempotent.  It never replaces
an existing catalog or cover; it only creates missing directories and schema.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIBRARY_ROOT = PROJECT_ROOT / "data" / "library"
DEFAULT_COVER_SOURCE = PROJECT_ROOT / "admin" / "assets" / "oohstory-default-cover.jpg"
DEFAULT_COVER_SHA256 = "d421cee15a266d258979455101443085bbc686504ee802c55686cbfc92d0b09e"

CATALOG_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;

CREATE TABLE IF NOT EXISTS pages (
  page INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  book_count INTEGER,
  last_error TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS catalog_sections (
  section_key TEXT PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  label TEXT,
  total_pages INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS listing_pages (
  section_key TEXT NOT NULL,
  page INTEGER NOT NULL,
  url TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  book_count INTEGER,
  last_error TEXT,
  updated_at TEXT,
  PRIMARY KEY (section_key, page)
);

CREATE TABLE IF NOT EXISTS crawl_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS books (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT,
  detail_url TEXT NOT NULL UNIQUE,
  title TEXT,
  author TEXT,
  category TEXT,
  expected_size TEXT,
  download_page_url TEXT,
  file_url TEXT,
  output_path TEXT,
  status TEXT NOT NULL DEFAULT 'discovered',
  book_status TEXT NOT NULL DEFAULT '已完结',
  attempts INTEGER NOT NULL DEFAULT 0,
  bytes INTEGER,
  sha256 TEXT,
  last_error TEXT,
  discovered_at TEXT,
  updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_books_status ON books(status, attempts);
CREATE INDEX IF NOT EXISTS idx_books_category ON books(category);
CREATE INDEX IF NOT EXISTS idx_books_source_id ON books(source_id);
CREATE INDEX IF NOT EXISTS idx_books_txt80_download_queue
  ON books(status, attempts, id)
  WHERE detail_url LIKE 'https://www.txt80.cc/%';
CREATE INDEX IF NOT EXISTS idx_listing_pages_status
  ON listing_pages(status, attempts);
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def initialize(library_root: Path) -> dict[str, object]:
    root = library_root.expanduser().resolve()
    if root.is_symlink():
        raise RuntimeError("library root must not be a symlink")

    for relative in (
        "书籍",
        "封面",
        "全局索引/阅读目录",
        "全局拆书库",
        ".oohstory-default-assets",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)

    catalog_path = root / "catalog.sqlite3"
    with sqlite3.connect(catalog_path, timeout=30) as connection:
        connection.executescript(CATALOG_SCHEMA)
        connection.commit()

    if not DEFAULT_COVER_SOURCE.is_file():
        raise RuntimeError(f"default cover asset is missing: {DEFAULT_COVER_SOURCE}")
    if _sha256(DEFAULT_COVER_SOURCE) != DEFAULT_COVER_SHA256:
        raise RuntimeError("default cover asset checksum mismatch")
    cover_path = (
        root
        / ".oohstory-default-assets"
        / f"oohstory-default-cover-{DEFAULT_COVER_SHA256}.jpg"
    )
    if not cover_path.exists():
        shutil.copyfile(DEFAULT_COVER_SOURCE, cover_path)
    elif _sha256(cover_path) != DEFAULT_COVER_SHA256:
        raise RuntimeError("existing default cover has an unexpected checksum")

    with sqlite3.connect(catalog_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    return {
        "library_root": str(root),
        "catalog_path": str(catalog_path),
        "cover_path": str(cover_path),
        "table_count": len(tables),
        "index_count": len(indexes),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--library-root",
        type=Path,
        default=DEFAULT_LIBRARY_ROOT,
        help="empty or existing OOH Story library root",
    )
    args = parser.parse_args()
    result = initialize(args.library_root)
    print(
        "Initialized {library_root} ({table_count} tables, {index_count} indexes)".format(
            **result
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
