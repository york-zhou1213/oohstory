"""Manage the one branded cover shared by every missing-cover catalog row."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from oohstory_library.services.cover_failure_policy import (
    is_missing_cover_placeholder_sha256,
)


OOHSTORY_DEFAULT_COVER_SHA256 = (
    "d421cee15a266d258979455101443085bbc686504ee802c55686cbfc92d0b09e"
)
OOHSTORY_SHARED_DEFAULT_COVER_URL = (
    "/api/v1/assets/default-cover?v="
    f"{OOHSTORY_DEFAULT_COVER_SHA256[:16]}"
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_cover_template_path() -> Path:
    configured = os.getenv("OOHSTORY_DEFAULT_COVER_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        Path(__file__).resolve().parents[3]
        / "assets"
        / "oohstory-default-cover.jpg"
    )


def materialize_default_cover(
    *,
    cover_root: Path,
    catalog_id: int,
    template_path: Path | None = None,
) -> dict[str, Any]:
    """Ensure and describe the single shared default-cover asset.

    ``catalog_id`` remains in the call signature so deployed source workers can
    be upgraded without a flag-day API change.  It is deliberately *not* used
    in the filename: a missing cover is state, not a catalog-owned object.
    """

    source = (template_path or default_cover_template_path()).resolve()
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if (
        len(data) < 8 * 1024
        or not data.startswith(b"\xff\xd8\xff")
        or digest != OOHSTORY_DEFAULT_COVER_SHA256
    ):
        raise ValueError("OOHStory 默认封面资源校验失败")

    root = Path(cover_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    canonical_root = (root.parent / ".oohstory-default-assets").resolve()
    canonical_root.mkdir(parents=True, exist_ok=True)
    canonical = canonical_root / f"oohstory-default-cover-{digest}.jpg"
    if canonical.exists():
        if not canonical.is_file() or _file_sha256(canonical) != digest:
            raise ValueError("OOHStory 默认封面共享资源冲突")
    else:
        canonical_part = canonical.with_suffix(canonical.suffix + ".part")
        try:
            canonical_part.write_bytes(data)
            os.replace(canonical_part, canonical)
        finally:
            canonical_part.unlink(missing_ok=True)
    canonical.chmod(0o644)
    _ = catalog_id
    return {
        # A null filename is intentional.  Per-title AI output receives its
        # own filename later; the temporary default never enters object_assets.
        "filename": "",
        "path": str(canonical),
        "sha256": digest,
        "bytes": len(data),
        "content_type": "image/jpeg",
        "cover_url": "oohstory-default://shared",
        "public_url": OOHSTORY_SHARED_DEFAULT_COVER_URL,
    }


def delete_missing_placeholder_if_unreferenced(
    *,
    runtime: Any,
    cover_root: Path,
    catalog_id: int,
    filename: str,
) -> dict[str, Any]:
    """Delete only an exact known placeholder with no durable references."""

    name = str(filename or "")
    if not name or Path(name).name != name:
        return {"status": "not_applicable"}
    root = Path(cover_root).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        return {"status": "already_missing"}
    digest = _file_sha256(path)
    if not is_missing_cover_placeholder_sha256(digest):
        return {"status": "retained_not_placeholder", "sha256": digest}
    references = runtime.clean_cover_original_references(
        filename=name,
        catalog_id=int(catalog_id),
    )
    if any(references.values()):
        return {"status": "retained_referenced", "references": references}

    retiring = (
        root
        / f".retiring-missing-{int(catalog_id)}-{digest[:16]}{path.suffix}"
    ).resolve()
    if retiring.parent != root:
        raise ValueError("缺失封面清理暂存路径越界")
    os.replace(path, retiring)
    references = runtime.clean_cover_original_references(
        filename=name,
        catalog_id=int(catalog_id),
    )
    if any(references.values()):
        os.replace(retiring, path)
        return {"status": "restored_referenced", "references": references}
    size = retiring.stat().st_size
    retiring.unlink()
    return {"status": "deleted", "bytes": size, "sha256": digest}
