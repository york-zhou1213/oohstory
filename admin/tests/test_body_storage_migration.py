from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "electronic-library"
    / "migrate_body_objects_to_books.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("body_storage_migration", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture_row(root: Path, content: bytes = b"chapter body"):
    digest = hashlib.sha256(content).hexdigest()
    categorized = root / "书籍" / "科幻小说" / "sample.txt"
    categorized.parent.mkdir(parents=True)
    categorized.write_bytes(content)
    body = root / "body" / digest[:2] / digest[2:4] / f"{digest}.txt"
    body.parent.mkdir(parents=True)
    body.write_bytes(content)
    row = {
        "catalog_id": 7,
        "body_object_key": body.relative_to(root).as_posix(),
        "legacy_output_path": str(categorized),
        "book_bytes": len(content),
        "book_sha256": digest,
        "asset_id": 17,
        "asset_object_key": body.relative_to(root).as_posix(),
        "asset_bytes": len(content),
        "asset_sha256": digest,
    }
    return row, categorized, body


def test_audit_maps_duplicate_body_to_categorized_file(tmp_path: Path):
    row, categorized, _body = fixture_row(tmp_path)

    record = MODULE._audit_one(
        row,
        root=tmp_path,
        canonical_assets={},
    )

    assert record["new_object_key"] == "书籍/科幻小说/sample.txt"
    assert record["canonical_path"] == str(categorized.resolve())
    assert record["old_object_key"].startswith("body/")


def test_audit_rejects_categorized_content_mismatch(tmp_path: Path):
    row, categorized, _body = fixture_row(tmp_path)
    categorized.write_bytes(b"different")

    with pytest.raises(MODULE.MigrationError, match="大小不一致"):
        MODULE._audit_one(row, root=tmp_path, canonical_assets={})


def test_audit_rejects_existing_owner_of_canonical_key(tmp_path: Path):
    row, _categorized, _body = fixture_row(tmp_path)

    with pytest.raises(MODULE.MigrationError, match="其他书目"):
        MODULE._audit_one(
            row,
            root=tmp_path,
            canonical_assets={"书籍/科幻小说/sample.txt": 99},
        )


def test_verify_unchanged_accepts_audited_file_fingerprint(tmp_path: Path):
    row, _categorized, _body = fixture_row(tmp_path)
    record = MODULE._audit_one(row, root=tmp_path, canonical_assets={})

    MODULE._verify_unchanged(record, tmp_path)
