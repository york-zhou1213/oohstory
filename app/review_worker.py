"""OOHStory-owned AI review worker for reader submissions.

It never publishes into the business library. Approved items become immutable,
versioned handoff manifests for a separately owned ingestion consumer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .accounts import AccountStore
from .settings import Settings, load_settings
from .submission_moderation import inspect_submission_content
from .submissions import parse_strict_review
from .upload_worker import inspect_upload_once


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _submission_name(item: dict[str, Any]) -> str:
    kind = str(item.get("submission_type") or "")
    raw = item.get("title") if kind == "novel" else item.get("original_filename")
    name = str(raw or item.get("resource_title") or "未命名投稿").strip()
    if kind == "deconstruction" and name.casefold().endswith(".zip"):
        name = name[:-4].rstrip()
    return (name or "未命名投稿")[:80]


def _text_excerpt(path: Path, limit: int = 60_000) -> str:
    data = path.read_bytes()[:limit]
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _review_payload(item: dict[str, Any], settings: Settings) -> dict[str, Any]:
    kind = str(item["submission_type"])
    payload: dict[str, Any] = {
        "contract": "oohstory-submission-review-v1",
        "submission_type": kind,
        "submission_id": str(item["id"]),
    }
    if kind == "deconstruction":
        try:
            structure = json.loads(item.get("structure_report") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            structure = {}
        payload["structure"] = structure
        stored = settings.user_upload_root / str(item.get("stored_filename") or "")
        excerpts: dict[str, str] = {}
        excerpt_budget = 18_000
        extracted = stored.parent / "extracted"
        content_root = extracted / str(structure.get("normalized_root") or "")
        for name in (
            "_meta.json",
            "_progress.md",
            "拆文报告.md",
            "快速预览.md",
            "概要.md",
            "情节节点.md",
            "写作手法.md",
            "原文/原文.txt",
        ):
            candidate = (content_root / name).resolve()
            if (
                excerpt_budget > 0
                and candidate.is_file()
                and extracted.resolve() in candidate.parents
            ):
                excerpt = _text_excerpt(candidate, min(3_000, excerpt_budget))
                excerpts[name] = excerpt
                excerpt_budget -= len(excerpt.encode("utf-8", errors="replace"))
        payload["excerpts"] = excerpts
        payload["content_evidence"] = inspect_submission_content(
            kind=kind,
            path=content_root,
        )
    else:
        for key in (
            "title",
            "author",
            "category",
            "serialization_status",
            "summary",
            "source",
            "authorization",
        ):
            payload[key] = item.get(key)
        manuscript = settings.user_upload_root / str(item.get("manuscript_path") or "")
        payload["content_evidence"] = inspect_submission_content(
            kind=kind,
            path=manuscript,
        )
    return payload


def _call_ai(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.submission_review_command:
        raise RuntimeError("未配置 OOHSTORY_SUBMISSION_REVIEW_COMMAND")
    result = subprocess.run(
        list(settings.submission_review_command),
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=settings.submission_review_timeout,
        check=False,
        env={**os.environ, "LC_ALL": "C.UTF-8"},
    )
    if result.returncode != 0:
        diagnostic = (result.stderr or "").strip().splitlines()
        if diagnostic:
            print(
                "AI review bridge: "
                + " | ".join(line[:300] for line in diagnostic[-10:]),
                file=sys.stderr,
            )
        raise RuntimeError(f"审核进程退出码 {result.returncode}")
    return parse_strict_review(result.stdout.strip())


def _copy_payload(
    item: dict[str, Any], settings: Settings, temp: Path
) -> list[dict[str, Any]]:
    kind = str(item["submission_type"])
    if kind == "deconstruction":
        sources = [
            (settings.user_upload_root / str(item["stored_filename"]), "source.zip")
        ]
    else:
        manuscript = settings.user_upload_root / str(item["manuscript_path"])
        sources = [
            (manuscript, f"manuscript{manuscript.suffix.casefold()}"),
            (settings.user_upload_root / str(item["cover_path"]), "cover.png"),
        ]
    files: list[dict[str, Any]] = []
    for source, name in sources:
        source = source.resolve(strict=True)
        if (
            settings.user_upload_root.resolve() not in source.parents
            or source.is_symlink()
        ):
            raise RuntimeError("投稿沙箱路径无效")
        target = temp / name
        shutil.copyfile(source, target)
        target.chmod(0o640)
        files.append(
            {
                "path": name,
                "bytes": target.stat().st_size,
                "sha256": _sha256(target),
            }
        )
    return files


def _write_handoff(
    item: dict[str, Any], result: dict[str, Any], settings: Settings
) -> str:
    root = settings.user_submission_handoff_root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    final = (root / str(item["id"])).resolve()
    if final.parent != root:
        raise RuntimeError("投稿交接路径无效")
    if (final / "ready.json").is_file():
        return str((final / "ready.json").relative_to(root))
    temp = Path(tempfile.mkdtemp(prefix=f".{item['id']}.", dir=root))
    try:
        temp.chmod(0o750)
        files = _copy_payload(item, settings, temp)
        metadata = {
            key: item.get(key)
            for key in (
                "title",
                "author",
                "category",
                "serialization_status",
                "summary",
                "source",
                "authorization",
                "structure_profile",
            )
            if item.get(key) is not None
        }
        if str(item["submission_type"]) == "deconstruction":
            try:
                structure = json.loads(item.get("structure_report") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                structure = {}
            metadata.update(
                {
                    "original_filename": item.get("original_filename") or "",
                    "upload_sha256": item.get("sha256") or "",
                    "structure_report": structure,
                    "contributor_username": str(
                        item.get("contributor_username") or "读者"
                    )[:40],
                }
            )
        manifest = {
            "schema_version": 1,
            "type": str(item["submission_type"]),
            "submission_id": str(item["id"]),
            "metadata": metadata,
            "files": files,
            "review": result,
        }
        ready_tmp = temp / "ready.json.tmp"
        ready_tmp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ready_tmp.chmod(0o640)
        ready_tmp.replace(temp / "ready.json")
        try:
            temp.replace(final)
        except FileExistsError:
            if not (final / "ready.json").is_file():
                raise
        return str((final / "ready.json").relative_to(root))
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)


def review_once(settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or load_settings()
    store = AccountStore(
        settings.user_database_path, session_ttl_seconds=settings.session_ttl_seconds
    )
    inspect_upload_once(settings, store)
    item = store.claim_review()
    if not item:
        return None
    kind = str(item["submission_type"])
    try:
        payload = _review_payload(item, settings)
        content_evidence = payload.get("content_evidence") or {}
        try:
            queued_review = json.loads(str(item.get("review_result") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            queued_review = {}
        if content_evidence.get("decision") == "reject":
            result = {
                "decision": "reject",
                "reason": str(content_evidence.get("reason") or "内容安全审核未通过"),
                "missing_files": [],
                "issues": [
                    str(value) for value in content_evidence.get("issues") or []
                ],
            }
        elif kind == "novel" and queued_review.get("admin_approved") is True:
            result = {
                "decision": "approve",
                "reason": "管理员已确认发布授权，隔离内容复核通过",
                "missing_files": [],
                "issues": [],
            }
        else:
            result = _call_ai(settings, payload)
        if kind == "deconstruction":
            contributor = store.comment_authors([str(item["user_id"])]).get(
                str(item["user_id"]), {}
            )
            item["contributor_username"] = str(
                contributor.get("display_name") or "读者"
            )
        structure = payload.get("structure") or {}
        if kind == "deconstruction" and not bool(structure.get("valid")):
            missing = [str(value) for value in structure.get("missing_files") or []]
            result = {
                "decision": "reject",
                "reason": "拆文结构不完整：" + "、".join(missing),
                "missing_files": missing,
                "issues": result.get("issues") or [],
            }
        handoff = (
            _write_handoff(item, result, settings)
            if result["decision"] == "approve"
            else ""
        )
        completed = store.complete_review(
            kind, str(item["id"]), result, handoff_manifest=handoff
        )
        if completed:
            approved = completed["status"] == "approved"
            submission_name = _submission_name(item)
            store.create_notification(
                completed["user_id"],
                kind="submission_review",
                title=(
                    f"《{submission_name}》投稿审核通过"
                    if approved
                    else f"《{submission_name}》投稿审核未通过"
                ),
                message=(
                    f"《{submission_name}》已安全交接，等待入库。"
                    if approved
                    else f"《{submission_name}》：{result['reason']}"
                ),
                action_url="#/account/submissions",
                resource_type=kind,
                resource_id=str(item["id"]),
                dedupe_key=f"review:{kind}:{item['id']}:{completed['status']}",
            )
        return {
            "id": str(item["id"]),
            "type": kind,
            "status": completed["status"] if completed else "stale",
        }
    except BaseException as exc:
        store.release_review(kind, str(item["id"]), str(exc))
        raise


def reconcile_results(
    store: AccountStore, settings: Settings, *, user_id: str | None = None
) -> int:
    """Consume trusted consumer result files once and create idempotent notices."""
    root = settings.user_submission_handoff_root.resolve()
    updated = 0
    for item in store.handoff_records(user_id):
        manifest = (root / str(item["handoff_manifest"])).resolve()
        if root not in manifest.parents or manifest.name != "ready.json":
            continue
        result_path = manifest.parent / "result.json"
        if not result_path.is_file() or result_path.is_symlink():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(result, dict) or str(
            result.get("status") or ""
        ).casefold() not in {"completed", "rejected", "failed"}:
            continue
        allowed = {
            "status",
            "catalog_id",
            "public_id",
            "output",
            "output_slug",
            "message",
            "reason",
            "completed_at",
            "source_upgrade",
        }
        if set(result) - allowed or not str(result.get("completed_at") or "").strip():
            continue
        if not str(result.get("message") or result.get("reason") or "").strip():
            continue
        completed = store.complete_handoff(
            str(item["submission_type"]), str(item["id"]), result
        )
        if not completed:
            continue
        succeeded = completed["status"] == "completed"
        submission_name = _submission_name(item)
        store.create_notification(
            completed["user_id"],
            kind="submission_ingestion",
            title=(
                f"《{submission_name}》投稿已完成入库"
                if succeeded
                else f"《{submission_name}》投稿入库被驳回"
            ),
            message=f"《{submission_name}》：{completed['message']}",
            action_url="#/account/submissions",
            resource_type=str(item["submission_type"]),
            resource_id=str(item["id"]),
            dedupe_key=f"handoff:{item['submission_type']}:{item['id']}:{completed['status']}",
        )
        updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once", action="store_true", help="process at most one queued submission"
    )
    parser.parse_args()
    settings = load_settings()
    store = AccountStore(
        settings.user_database_path, session_ttl_seconds=settings.session_ttl_seconds
    )
    reconcile_results(store, settings)
    result = review_once(settings)
    if result:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
