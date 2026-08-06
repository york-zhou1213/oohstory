import hashlib
from pathlib import Path
from unittest.mock import patch

from oohstory_library.services.library_covers import sync_alternate_remote_cover


class FakeRuntime:
    def __init__(self, original_filename: str) -> None:
        self.original_filename = original_filename
        self.marked_deleted: dict[str, object] | None = None

    def persist_alternate_cover_result(self, **kwargs: object) -> dict[str, object]:
        return {
            "catalog_id": kwargs["catalog_id"],
            "original_filename": self.original_filename,
            "filename": kwargs["filename"],
        }

    def clean_cover_original_references(self, **_kwargs: object) -> dict[str, int]:
        return {"books": 0, "covers": 0, "assets": 0, "active_jobs": 0}

    def mark_clean_cover_deleted(self, **kwargs: object) -> bool:
        self.marked_deleted = kwargs
        return True


def test_alternate_cover_deletes_and_marks_unreferenced_predecessor(tmp_path: Path) -> None:
    jpeg = b"\xff\xd8\xff" + b"\x00" * 2048
    digest = hashlib.sha256(jpeg).hexdigest()
    cover_root = tmp_path / "封面"
    cover_root.mkdir()
    old = cover_root / "7-old.jpg"
    old.write_bytes(jpeg + b"old")
    runtime = FakeRuntime(old.name)

    with (
        patch(
            "oohstory_library.services.library_covers._mysql_runtime",
            return_value=runtime,
        ),
        patch("oohstory_library.services.library_covers._scan_cover_bytes"),
    ):
        result = sync_alternate_remote_cover(
            catalog_id=7,
            catalog_source_id="123",
            origin_source_id="ixdzs-456",
            title="测试书",
            author="测试作者",
            origin_detail_url="https://ixdzs8.com/read/456/",
            cover_url="https://img.ixdzs.com/test.jpg",
            cover_root=cover_root,
            cover_index_path=cover_root / "index.sqlite3",
            allowed_host_suffixes=(".ixdzs.com",),
            request_bytes=lambda _url: (jpeg, "image/jpeg"),
        )

    expected = f"7-cover-{digest[:16]}.jpg"
    assert result["filename"] == expected
    assert (cover_root / expected).is_file()
    assert not old.exists()
    assert result["deleted_original"] is True
    assert runtime.marked_deleted is not None
    assert runtime.marked_deleted["original_filename"] == old.name
