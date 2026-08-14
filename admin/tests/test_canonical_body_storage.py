from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oohstory_library.services.electronic_library import ElectronicLibraryService
from oohstory_library.services.library_catalog_mysql import MySQLCatalogStore
from oohstory_library.services.library_object_store import NasObjectStore


class FakeCatalog:
    def __init__(self) -> None:
        self.payload = None

    def mirror_imported_book(self, **payload):
        self.payload = payload
        return 73


class ForbiddenBodyStore:
    def put_file(self, *_args, **_kwargs):
        raise AssertionError("正文不得复制进 content-addressed body/ 目录")


def service_for(tmp_path: Path) -> tuple[ElectronicLibraryService, FakeCatalog]:
    library_root = tmp_path / "electronic-library"
    (library_root / "书籍" / "科幻小说").mkdir(parents=True)
    service = ElectronicLibraryService.__new__(ElectronicLibraryService)
    service.library_root = library_root.resolve()
    service.books_root = service.library_root / "书籍"
    service.infrastructure_settings = SimpleNamespace(catalog_backend="mysql")
    catalog = FakeCatalog()
    service.mysql_catalog = catalog
    service.object_store = ForbiddenBodyStore()
    return service, catalog


def test_import_registers_the_categorized_file_without_body_copy(tmp_path: Path):
    service, catalog = service_for(tmp_path)
    body = service.books_root / "科幻小说" / "星海维修师.txt"
    body.write_text("第一章\n正文", encoding="utf-8")

    catalog_id = service._insert_imported_catalog_row(
        source_id="txt80-73",
        title="星海维修师",
        author="测试作者",
        category="科幻小说",
        detail_url="https://example.invalid/73",
        file_url="",
        output_path=body,
        sha256="a" * 64,
    )

    assert catalog_id == 73
    assert catalog.payload is not None
    assert catalog.payload["body_object_key"] == "书籍/科幻小说/星海维修师.txt"
    assert catalog.payload["legacy_output_path"] == str(body.resolve())


def test_import_rejects_body_outside_categorized_books_root(tmp_path: Path):
    service, _catalog = service_for(tmp_path)
    body = service.library_root / "body" / "legacy.txt"
    body.parent.mkdir()
    body.write_text("旧副本", encoding="utf-8")

    with pytest.raises(ValueError, match="电子书库根目录的书籍"):
        service._insert_imported_catalog_row(
            source_id="txt80-74",
            title="错误路径",
            author="测试作者",
            category="科幻小说",
            detail_url="https://example.invalid/74",
            file_url="",
            output_path=body,
            sha256="b" * 64,
        )


@pytest.mark.parametrize(
    "object_key",
    ("body/aa/bb/file.txt", "/书籍/科幻小说/a.txt", "书籍/../body/a.txt"),
)
def test_mysql_catalog_rejects_noncanonical_body_keys(object_key: str):
    with pytest.raises(ValueError, match="电子书库根目录的书籍"):
        MySQLCatalogStore._canonical_body_object_key(object_key)


def test_nas_object_store_rejects_body_copies(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("正文", encoding="utf-8")

    with pytest.raises(ValueError, match="电子书库根目录的书籍"):
        NasObjectStore(tmp_path / "objects").put_file(
            source,
            asset_type="body",
        )
