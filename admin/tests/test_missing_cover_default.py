from pathlib import Path

from oohstory_library.services import library_covers
from oohstory_library.services.default_cover import (
    OOHSTORY_DEFAULT_COVER_SHA256,
    delete_missing_placeholder_if_unreferenced,
    materialize_default_cover,
)


class ReferenceRuntime:
    def __init__(self, references: dict[str, int] | None = None):
        self.references = references or {
            "books": 0,
            "covers": 0,
            "assets": 0,
            "active_jobs": 0,
        }

    def clean_cover_original_references(self, **_kwargs):
        return dict(self.references)


def test_default_cover_materializes_one_shared_verified_file(tmp_path) -> None:
    result = materialize_default_cover(cover_root=tmp_path, catalog_id=42)
    second = materialize_default_cover(cover_root=tmp_path, catalog_id=43)
    other_shard = materialize_default_cover(cover_root=tmp_path, catalog_id=1043)

    target = Path(result["path"])
    second_target = Path(second["path"])
    other_shard_target = Path(other_shard["path"])
    assert target.is_file()
    assert target == second_target == other_shard_target
    assert result["filename"] == ""
    assert list(tmp_path.iterdir()) == []
    assert result["sha256"] == OOHSTORY_DEFAULT_COVER_SHA256
    assert result["cover_url"] == "oohstory-default://shared"
    assert result["bytes"] == target.stat().st_size


def test_exact_missing_default_is_deleted_only_at_zero_references(tmp_path) -> None:
    result = materialize_default_cover(cover_root=tmp_path, catalog_id=7)
    legacy = tmp_path / "7-old-placeholder.jpg"
    legacy.write_bytes(Path(result["path"]).read_bytes())
    retained = delete_missing_placeholder_if_unreferenced(
        runtime=ReferenceRuntime({"books": 1}),
        cover_root=tmp_path,
        catalog_id=7,
        filename=legacy.name,
    )
    assert retained["status"] == "retained_referenced"
    assert legacy.is_file()

    deleted = delete_missing_placeholder_if_unreferenced(
        runtime=ReferenceRuntime(),
        cover_root=tmp_path,
        catalog_id=7,
        filename=legacy.name,
    )
    assert deleted["status"] == "deleted"
    assert not legacy.exists()


def test_alternate_source_placeholder_returns_default_switch_marker(
    monkeypatch,
    tmp_path,
) -> None:
    template = (
        Path(__file__).resolve().parents[1]
        / "assets"
        / "oohstory-default-cover.jpg"
    )
    data = template.read_bytes()
    monkeypatch.setattr(library_covers, "_scan_cover_bytes", lambda *_args: {})

    prepared = library_covers.prepare_alternate_remote_cover(
        origin_detail_url="https://source.example/book/1",
        cover_url="https://images.example/placeholder.jpg",
        cover_index_path=tmp_path / "covers.sqlite3",
        allowed_hosts={"images.example"},
        request_bytes=lambda _url: (data, "image/jpeg"),
    )

    assert prepared["missing_placeholder"] is True
    assert prepared["source_sha256"] == OOHSTORY_DEFAULT_COVER_SHA256
    assert "data" not in prepared


def test_permanent_source_failure_installs_default_before_ai_queue() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "electronic-library"
        / "txt80_cover_sync.py"
    ).read_text(encoding="utf-8")

    assert "if ai_fallback:" in script
    assert "default = materialize_default_cover(" in script
    assert '"missing_placeholder": True' in script
    assert "原站没有可用封面，已改用 OOHStory" in script
