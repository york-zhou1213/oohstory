from __future__ import annotations

import json
from pathlib import Path

import pytest

from oohstory_library.services.runtime_controls import (
    OOHStoryRuntimeControls,
    cover_worker_count,
)
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime


def test_runtime_controls_persist_all_five_sites_atomically(tmp_path: Path) -> None:
    store = OOHStoryRuntimeControls(tmp_path)
    values = {
        "txt80": 11,
        "xbiquge": 22,
        "ixdzs": 33,
        "shubaow": 44,
        "linovelib": 55,
    }
    for site_id, count in values.items():
        store.update_site(site_id, count)

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["site_books_per_cycle"] == values
    assert not list(tmp_path.glob("*.tmp"))


def test_runtime_controls_reject_unsafe_ranges_and_symlink(tmp_path: Path) -> None:
    store = OOHStoryRuntimeControls(tmp_path)
    with pytest.raises(ValueError, match="1–500"):
        store.update_site("txt80", 0)
    with pytest.raises(ValueError, match="未知正文同步站点"):
        store.update_site("../../shell", 10)
    with pytest.raises(ValueError, match="50–160"):
        store.update_cover_target(49)

    foreign = tmp_path / "foreign.json"
    foreign.write_text("{}", encoding="utf-8")
    store.path.symlink_to(foreign)
    with pytest.raises(ValueError, match="路径无效"):
        store.read()


def test_cover_target_maps_fifty_per_hour_to_three_workers(tmp_path: Path) -> None:
    store = OOHStoryRuntimeControls(tmp_path)
    saved = store.update_cover_target(50)
    assert saved["cover_redraw"]["target_per_hour"] == 50
    assert cover_worker_count(50) == 3
    assert cover_worker_count(160) == 8


def test_cover_actual_window_uses_same_mysql_session_timezone() -> None:
    class FakeCursor:
        def __init__(self):
            self.query = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query):
            self.query = query

        def fetchone(self):
            return {
                "completed_last_hour": 51,
                "completed_last_six_hours": 301,
                "processing": 3,
                "pending": 100,
                "failed": 0,
            }

    class FakeConnection:
        def __init__(self, cursor):
            self._cursor = cursor

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return self._cursor

    class FakePool:
        def __init__(self, cursor):
            self._connection = FakeConnection(cursor)

        def connection(self, *, readonly):
            assert readonly is True
            return self._connection

    cursor = FakeCursor()
    runtime = object.__new__(MySQLLibraryRuntime)
    runtime.pool = FakePool(cursor)

    status = runtime.clean_cover_operational_status()

    assert status["completed_last_hour"] == 51
    assert "CURRENT_TIMESTAMP(6)" in cursor.query
    assert "UTC_TIMESTAMP" not in cursor.query
