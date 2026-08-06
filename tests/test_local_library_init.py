from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "init_local_library.py"
SPEC = importlib.util.spec_from_file_location("init_local_library", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_initializes_empty_library_idempotently(tmp_path: Path) -> None:
    root = tmp_path / "library"
    first = MODULE.initialize(root)
    second = MODULE.initialize(root)

    assert first == second
    assert (root / "书籍").is_dir()
    assert (root / "封面").is_dir()
    assert (root / "全局索引" / "阅读目录").is_dir()
    assert Path(str(first["cover_path"])).is_file()

    with sqlite3.connect(root / "catalog.sqlite3") as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(books)")
        }
        assert {
            "id",
            "source_id",
            "detail_url",
            "title",
            "author",
            "category",
            "output_path",
            "status",
            "book_status",
            "bytes",
            "sha256",
        } <= columns
        assert connection.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
