from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from oohstory_admin.library_catalog import (
    INDEX_TASK_UNIT_ALLOWLIST,
    GlobalDeconstructionReader,
    LibraryCatalogDatabase,
    LibraryCatalogFacade,
    LibraryCatalog,
    VIEWS,
    incremental_index_task,
    incremental_index_tasks,
)
from oohstory_admin.units import UNIT_ALLOWLIST


class FakeCursor:
    def __init__(self, responses):
        self.responses = responses
        self.rows = []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        self.executed.append((normalized, tuple(params)))
        self.rows = self.responses(normalized, tuple(params))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, responses):
        self.cursor_value = FakeCursor(responses)
        self.rolled_back = False
        self.closed = False
        self.committed = False

    def cursor(self):
        return self.cursor_value

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_overview_reproduces_catalog_cards_in_read_only_transaction(settings):
    def responses(query, _params):
        if "FROM catalog_status_counts" in query:
            return [
                {"library_id": "local", "status": "done", "count": 8},
                {"library_id": "local", "status": "failed", "count": 2},
                {"library_id": "fanqie", "status": "done", "count": 3},
                {"library_id": "fanqie", "status": "discovered", "count": 1},
                {"library_id": "fanqie", "status": "duplicate", "count": 4},
            ]
        if "FROM catalog_facets" in query:
            if "GROUP BY library_id" in query:
                return [
                    {"library_id": "local", "count": 7},
                    {"library_id": "fanqie", "count": 3},
                ]
            return [{"name": "科幻", "count": 7}]
        if "FROM book_metadata" in query:
            return [{"tone_books": 9, "indexable_books": 11}]
        if "FROM crawl_state" in query:
            return [{"count": 6}]
        return []

    connection = FakeConnection(responses)
    database = LibraryCatalogDatabase(settings, connector=lambda **_options: connection)

    data = database.overview_snapshot()

    assert data["books"]["total"] == 14
    assert data["books"]["readable"] == 10
    assert data["books"]["raw_total"] == 18
    assert data["books"]["intercepted_duplicates"] == 10
    assert data["books"]["libraries"]["local"]["downloaded"] == 8
    assert data["books"]["libraries"]["local"]["recovery"] == 3
    assert data["books"]["libraries"]["fanqie"]["label"] == "番茄/授权书库"
    assert data["tone_index"]["count"] == 9
    assert data["tone_index"]["pending"] == 2
    assert data["categories"] == [{"name": "科幻", "count": 7}]
    assert connection.cursor_value.executed[0][0] == "SET SESSION TRANSACTION READ ONLY"
    assert connection.rolled_back is True
    assert connection.committed is False
    assert connection.closed is True


def test_catalog_browse_uses_fixed_filters_and_bound_parameters(settings):
    def responses(query, params):
        if query.startswith("SELECT COUNT(*)"):
            assert "library_id=%s" in query
            assert "body_available=1" in query
            assert params == ("local", "%星海%", "%星海%", "科幻")
            return [{"total": 1}]
        if query.startswith("SELECT b.id AS catalog_id"):
            assert params[-2:] == (12, 12)
            return [
                {
                    "catalog_id": 42,
                    "title": "星海测试",
                    "author": "作者",
                    "category": "科幻",
                    "library_id": "local",
                    "download_status": "done",
                    "body_available": 1,
                    "source_bytes": 1024,
                    "updated_at": "2026-08-03 10:00:00",
                }
            ]
        return []

    connection = FakeConnection(responses)
    database = LibraryCatalogDatabase(settings, connector=lambda **_options: connection)
    result = database.browse(
        library="local",
        availability="readable",
        query=" 星海\x00 ",
        category="科幻",
        page=2,
        page_size=12,
    )

    assert result["total"] == 1
    assert result["items"][0]["body_available"] is True
    assert result["filters"]["query"] == "星海"
    assert result["source_read_only"] is True
    assert connection.rolled_back is True
    assert connection.committed is False

    with pytest.raises(ValueError):
        database.browse(library="../../etc")
    with pytest.raises(ValueError):
        database.browse(availability="writable")
    with pytest.raises(ValueError):
        database.browse(page_size=1000)


def test_catalog_card_uses_verified_cover_and_current_chapter_metrics(settings, tmp_path):
    object_root = tmp_path / "objects"
    cover = object_root / "cover" / "ab" / "cover.png"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(b"\x89PNG\r\n\x1a\nverified-cover")
    local = replace(settings, library_object_root=object_root)

    def responses(query, _params):
        if query.startswith("SELECT COUNT(*)"):
            return [{"total": 1}]
        if query.startswith("SELECT b.id AS catalog_id"):
            return [{
                "catalog_id": 42,
                "title": "星海测试",
                "author": "作者",
                "category": "科幻",
                "library_id": "local",
                "download_status": "done",
                "body_available": 1,
                "source_bytes": 4096,
                "cover_object_key": "cover/ab/cover.png",
                "body_object_key": "body/private.txt",
                "word_count": 12000,
                "chapter_count": 12,
                "reader_source_bytes": 4096,
            }]
        if query.startswith("SELECT cover_object_key FROM books"):
            return [{"cover_object_key": "cover/ab/cover.png"}]
        return []

    connection = FakeConnection(responses)
    database = LibraryCatalogDatabase(local, connector=lambda **_options: connection)
    result = database.browse(page=1, page_size=28)
    item = result["items"][0]

    assert item["cover_available"] is True
    assert item["cover_url"].startswith("/api/admin/books/catalog/42/cover?v=")
    assert "cover_object_key" not in item
    assert "body_object_key" not in item
    assert item["chapter_count"] == 12
    assert item["chapter_count_known"] is True
    assert item["chapter_count_label"] == "12 章"
    assert item["chapter_count_source"] == "reader_index"
    assert result["range_start"] == 1
    assert result["range_end"] == 1
    descriptor = database.cover_file(42)
    assert descriptor["path"] == cover.resolve()
    assert descriptor["media_type"] == "image/png"


def test_catalog_cover_rejects_path_escape_and_unknown_chapters(settings, tmp_path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8\xffunsafe")
    object_root = tmp_path / "objects"
    object_root.mkdir()
    local = replace(settings, library_object_root=object_root)

    def responses(query, _params):
        if query.startswith("SELECT cover_object_key FROM books"):
            return [{"cover_object_key": "../outside.jpg"}]
        return []

    database = LibraryCatalogDatabase(
        local, connector=lambda **_options: FakeConnection(responses)
    )
    with pytest.raises(KeyError):
        database.cover_file(7)
    (object_root / "escape.jpg").symlink_to(outside)
    assert database._cover_file_from_key("escape.jpg") is None

    item = {
        "catalog_id": 7,
        "source_bytes": 100,
        "body_available": 0,
        "chapter_count": 0,
        "approx_chapter_count": 0,
    }
    database._shape_catalog_item(item)
    assert item["chapter_count_known"] is False
    assert item["chapter_count_label"] == "未识别"
    assert item["cover_available"] is True
    assert item["cover_is_default"] is True
    assert item["cover_url"].startswith("/api/admin/library/default-cover?v=")


def test_plot_evidence_is_read_only_bounded_and_decodes_motif_tags(settings):
    def responses(query, params):
        if "FROM plot_index_meta" in query:
            return [{
                "catalog_id": 42, "source_id": "local-42", "segment_count": 2,
                "indexed_at": "2026-08-03", "title": "星海测试", "author": "作者",
                "category": "科幻",
            }]
        if "FROM plot_segments" in query:
            assert params == (42, 1, 1)
            return [{"id": 2, "location": "第2章", "motif_tags": '["启航"]', "content": "证据正文"}]
        return []

    connection = FakeConnection(responses)
    database = LibraryCatalogDatabase(settings, connector=lambda **_options: connection)
    result = database.plot_evidence(42, page=2, page_size=1)

    assert result["total"] == 2
    assert result["items"][0]["motif_tags"] == ["启航"]
    assert result["source_read_only"] is True
    assert connection.rolled_back is True
    assert connection.committed is False


def test_global_deconstruction_and_status_files_are_bounded_and_read_only(settings, tmp_path):
    root = tmp_path / "electronic-library" / "txt80"
    runtime = root / "全局索引"
    deconstruction = root / "全局拆书库"
    task_root = deconstruction / ".tasks"
    runtime.mkdir(parents=True)
    task_root.mkdir(parents=True)
    completed = deconstruction / "星海猎人__42"
    completed.mkdir()
    (completed / "快速预览.md").write_text("preview", encoding="utf-8")
    (completed / "拆文报告.md").write_text("full", encoding="utf-8")
    (task_root / "run.json").write_text(
        json.dumps({"status": "running", "output_dir": str(deconstruction / "另一部书")}),
        encoding="utf-8",
    )
    (runtime / "electronic_library_index_status.json").write_text(
        json.dumps({"status": "completed", "running": False, "processed": 9}),
        encoding="utf-8",
    )
    local = replace(settings, library_root=root, library_runtime_dir=runtime)

    class FakeDatabase:
        def overview_snapshot(self):
            return {
                "books": {"total": 2, "readable": 1, "libraries": {}},
                "tone_index": {"count": 1, "indexable": 2},
                "categories": [],
            }

    facade = LibraryCatalogFacade(local, database=FakeDatabase())
    result = facade.overview()

    assert result["global_deconstruction"]["total"] == 2
    assert result["global_deconstruction"]["completed"] == 1
    assert result["global_deconstruction"]["running"] == 1
    assert result["global_deconstruction"]["items"][0]["has_full_report"] is True
    assert result["tone_index"]["processed"] == 9
    assert result["incremental_index"]["project_tone_matching"] is False
    assert "对标项目基调匹配" in result["incremental_index"]["note"]

    # Symlinked task JSON must not be followed.
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (task_root / "link.json").symlink_to(outside)
    snapshot = GlobalDeconstructionReader(deconstruction).snapshot()
    assert snapshot["task_counts"] == {"running": 1}
    records = GlobalDeconstructionReader(deconstruction).records()
    assert {item["title"] for item in records} == {"星海猎人", "另一部书"}
    assert {item["state"] for item in records} == {"full", "running"}
    page = GlobalDeconstructionReader(deconstruction).browse(
        query="星海", state="full", page=1, page_size=12
    )
    assert page["total"] == 1
    assert page["items"][0]["title"] == "星海猎人"
    with pytest.raises(ValueError):
        GlobalDeconstructionReader(deconstruction).browse(state="running")


def test_incremental_index_mapping_is_exact_and_excludes_project_tone_match():
    tasks = incremental_index_tasks()

    assert {item["id"] for item in tasks} == {"incremental_index", "ingestion_index"}
    assert all(item["unit"] in UNIT_ALLOWLIST for item in tasks)
    assert all(item["action"] == "start" for item in tasks)
    assert all(item["writes_catalog"] is False for item in tasks)
    assert all(item["project_tone_matching"] is False for item in tasks)
    assert INDEX_TASK_UNIT_ALLOWLIST["incremental_index"] == (
        "oohstory-library-index-refresh.service"
    )
    assert incremental_index_task("incremental_index")["scope"] == "global_tone_index"
    with pytest.raises(KeyError):
        incremental_index_task("arbitrary-command")


def test_fastapi_catalog_compatibility_views_and_book(settings):
    class FakeCatalogDatabase:
        def __init__(self):
            self.filters = None

        def browse(self, **filters):
            self.filters = filters
            return {
                "items": [],
                "total": 0,
                "page": filters["page"],
                "page_size": filters["page_size"],
                "page_count": 1,
                "categories": [],
            }

        def book(self, catalog_id):
            return {"catalog_id": catalog_id, "title": "测试书"}

    database = FakeCatalogDatabase()
    catalog = LibraryCatalog(settings, database=database)
    result = catalog.browse(view="tone", query="星海", page=1, page_size=24)

    assert set(VIEWS) == {
        "all", "local", "fanqie", "readable", "tone", "plot", "deconstruction"
    }
    assert result["view_label"] == "基调索引"
    assert database.filters["library"] == "all"
    assert database.filters["availability"] == "all"
    assert database.filters["index_kind"] == "tone"
    assert catalog.book(7) == {"catalog_id": 7, "title": "测试书"}


def test_catalog_facade_has_no_legacy_http_or_book_write_dependency():
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "oohstory_admin"
        / "library_catalog.py"
    ).read_text(encoding="utf-8")
    assert "127.0.0.1:" + "8080" not in source
    assert "requests." not in source
    assert "aiohttp" not in source
    assert "INSERT INTO books" not in source
    assert "UPDATE books" not in source
    assert "DELETE FROM books" not in source
