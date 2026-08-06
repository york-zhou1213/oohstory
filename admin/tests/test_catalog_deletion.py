from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from oohstory_library.services.electronic_library import ElectronicLibraryService
from oohstory_library.services.library_catalog_deletion import (
    CatalogDeletionArchive,
    normalize_catalog_delete_request,
)
from oohstory_library.services.library_object_store import NasObjectStore


def test_delete_request_is_bounded_and_bound_to_exact_phrase() -> None:
    ids, phrase = normalize_catalog_delete_request([9, "2", 9], "确认删除2本书")
    assert ids == [2, 9]
    assert phrase == "确认删除2本书"
    with pytest.raises(ValueError, match="确认短语"):
        normalize_catalog_delete_request([1, 2], "确认删除全部")
    with pytest.raises(ValueError, match="单次最多删除 100 本"):
        normalize_catalog_delete_request(range(1, 102), "确认删除101本书")


def test_catalog_archive_is_recoverable_and_rejects_outside_path(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    source = root / "书籍" / "book.txt"
    source.parent.mkdir()
    source.write_text("正文", encoding="utf-8")
    archive = CatalogDeletionArchive(root / ".deleted-catalog", (root,), "test-batch")
    entries = archive.stage([source])
    assert len(entries) == 1
    assert not source.exists()
    assert json.loads(archive.write_manifest({"status": "deleted"}).read_text())["status"] == "deleted"
    archive.restore()
    assert source.read_text(encoding="utf-8") == "正文"
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="白名单"):
        archive.stage([outside])


def _deletion_service(root: Path, *, fail_delete: bool = False):
    library_root = root / "library"
    object_root = root / "objects"
    runtime_root = library_root / "全局索引"
    for path in (library_root, object_root, runtime_root / "阅读目录"):
        path.mkdir(parents=True, exist_ok=True)
    body = object_root / "body" / "book.txt"
    cover = object_root / "cover" / "book.jpg"
    body.parent.mkdir(parents=True)
    cover.parent.mkdir(parents=True)
    body.write_text("正文", encoding="utf-8")
    cover.write_bytes(b"cover")
    legacy = library_root / "书籍" / "legacy.txt"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("旧正文", encoding="utf-8")
    reader_index = runtime_root / "阅读目录" / "7.json"
    reader_index.write_text("{}", encoding="utf-8")

    class FakeCatalog:
        deleted: list[int] = []

        def prepare_book_deletion(self, ids):
            return {
                "books": [{"catalog_id": ids[0], "source_id": "fixture-1"}],
                "object_assets": [],
                "exclusive_object_keys": ["body/book.txt", "cover/book.jpg"],
                "exclusive_legacy_paths": [str(legacy)],
            }

        def delete_books(self, ids):
            if fail_delete:
                raise RuntimeError("database failed")
            self.deleted = list(ids)
            return {"deleted": len(ids), "related_counts": {"plot_segments": 4}}

    service = ElectronicLibraryService.__new__(ElectronicLibraryService)
    service.library_root = library_root
    service.books_root = library_root / "书籍"
    service.runtime_dir = runtime_root
    service.reader_index_root = runtime_root / "阅读目录"
    service.infrastructure_settings = SimpleNamespace(
        catalog_backend="mysql", object_root=object_root
    )
    service.object_store = NasObjectStore(object_root)
    service.mysql_catalog = FakeCatalog()
    service.xbiquge_provider = SimpleNamespace(
        chapter_cache_root=library_root / ".download-cache" / "xbiquge"
    )
    service.shubaow_provider = SimpleNamespace(
        chapter_cache_root=library_root / ".download-cache" / "shubaow"
    )
    service._deconstruction_cache_lock = threading.Lock()
    service._deconstruction_status_cache = {"old": {}}
    service._catalog_deconstruction_delete_plan = lambda ids: {
        "paths": [], "registry_changed": False, "registry": {},
        "removed_links": [], "batch_updates": [], "task_count": 0,
        "artifact_count": 0,
    }
    warmed: list[str] = []
    service.browse_catalog = lambda **kwargs: warmed.append(kwargs["library"])
    return service, (body, cover, legacy, reader_index), warmed


def test_oohstory_service_archives_then_deletes_and_warms_cache(tmp_path: Path) -> None:
    service, paths, warmed = _deletion_service(tmp_path)
    result = service.delete_catalog_books(
        catalog_ids=[7], confirmation="确认删除1本书"
    )
    assert result["deleted"] == 1
    assert all(not path.exists() for path in paths)
    assert Path(result["archive_manifest"]).is_file()
    assert service.mysql_catalog.deleted == [7]
    assert warmed == ["all", "local", "fanqie"]


def test_oohstory_service_restores_files_when_database_delete_fails(tmp_path: Path) -> None:
    service, paths, _ = _deletion_service(tmp_path, fail_delete=True)
    with pytest.raises(RuntimeError, match="database failed"):
        service.delete_catalog_books(catalog_ids=[7], confirmation="确认删除1本书")
    assert all(path.exists() for path in paths)
