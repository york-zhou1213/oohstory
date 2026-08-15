"""Durable background inspection for user-uploaded deconstruction archives."""

from __future__ import annotations

import shutil
from typing import Any

from .accounts import AccountError, AccountStore
from .settings import Settings
from .submissions import (
    inspect_deconstruction_original,
    inspect_deconstruction_structure,
)
from .upload_security import UploadSecurityError, UploadSecurityScanner


def inspect_upload_once(
    settings: Settings,
    store: AccountStore,
    *,
    upload_id: str | None = None,
) -> dict[str, Any] | None:
    """Claim and inspect one persisted upload without involving the request path."""
    item = store.claim_upload_scan(upload_id=upload_id)
    if item is None:
        return None

    claimed_id = str(item["id"])
    user_id = str(item["user_id"])
    upload_root = settings.user_upload_root.resolve()
    source = (upload_root / str(item.get("stored_filename") or "")).resolve()
    directory = source.parent
    try:
        if upload_root not in source.parents or source.name != "source.zip":
            raise UploadSecurityError("隔离区文件路径无效")
        scanner = UploadSecurityScanner().scan(
            source,
            suffix=".zip",
            max_bytes=settings.max_upload_bytes,
        )
        extracted = directory / "extracted"
        shutil.rmtree(extracted, ignore_errors=True)
        names = UploadSecurityScanner.safe_extract_zip(source, extracted)
        structure = inspect_deconstruction_structure(names)
        if structure.get("valid"):
            structure.update(inspect_deconstruction_original(extracted, structure))
        store.finish_upload(
            claimed_id,
            user_id,
            stored_filename=str(source.relative_to(upload_root)),
            size=int(item.get("bytes") or source.stat().st_size),
            digest=str(scanner["sha256"]),
            scanner=scanner,
            structure=structure,
        )
        return {"id": claimed_id, "status": "ai_pending", "structure": structure}
    except (UploadSecurityError, AccountError, ValueError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        store.reject_upload(claimed_id, user_id, str(exc))
        store.create_notification(
            user_id,
            kind="submission_scan",
            title="拆书文安全检查未通过",
            message=str(exc),
            action_url="#/account/submit",
            resource_type="deconstruction",
            resource_id=claimed_id,
            dedupe_key=f"scan:deconstruction:{claimed_id}:rejected",
        )
        return {"id": claimed_id, "status": "rejected", "reason": str(exc)}
    except OSError:
        store.release_upload_scan(claimed_id)
        raise
