#!/usr/bin/env python3
"""Scan four authorized recent-update feeds and refresh changed local books."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

from project_paths import APP_ROOT  # noqa: E402


BACKEND_ROOT = APP_ROOT / "src"
sys.path.insert(0, str(BACKEND_ROOT))

from oohstory_library.services.ai_service import get_ai_service  # noqa: E402
from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402
from oohstory_library.services.library_catalog import normalize_catalog_title  # noqa: E402
from oohstory_library.services.library_download_queue import LibraryDownloadQueue  # noqa: E402


STATE_PATH = (
    APP_ROOT
    / "electronic-library"
    / "txt80"
    / "全局索引"
    / "serialized-source-updates.json"
)
LOCK_PATH = STATE_PATH.with_suffix(".lock")
COMPLETED_STATUSES = {"已完结", "完结", "completed", "finished"}
DEFAULT_SOURCE_NAMES = ("xbiquge", "ixdzs", "shubaow", "linovelib")


def clean(value: Any) -> str:
    return re.sub(
        r"[\W_]+",
        "",
        unicodedata.normalize("NFKC", str(value or "")).casefold(),
        flags=re.UNICODE,
    )


def normalized_source_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "title": normalize_catalog_title(item.get("title")),
    }


def source_fields(
    service: ElectronicLibraryService,
    item: dict[str, Any],
) -> tuple[str, str, str]:
    provider = str(item.get("provider") or "")
    remote_id = str(item.get("remote_id") or "")
    source_ref = str(item.get("source_ref") or "")
    if provider == service.xbiquge_provider.PROVIDER_ID:
        return "xbiquge", f"xbiquge-{remote_id}", service.xbiquge_provider.base_url + source_ref
    if provider == service.ixdzs_provider.PROVIDER_ID:
        return "ixdzs", f"ixdzs-{remote_id}", service.ixdzs_provider.base_url + source_ref
    if provider == service.shubaow_provider.PROVIDER_ID:
        return "shubaow", f"shubaow-{remote_id}", service.shubaow_provider.base_url + source_ref
    if provider == service.linovelib_provider.PROVIDER_ID:
        return (
            "linovelib",
            f"linovelib-{remote_id}",
            service.linovelib_provider.base_url + source_ref,
        )
    if provider == service.txt80_provider.PROVIDER_ID:
        return "txt80", remote_id, service.txt80_provider.base_url + "/" + source_ref.lstrip("/")
    raise ValueError("未知授权更新来源")


def parse_source_names(value: str) -> tuple[str, ...]:
    requested = tuple(
        dict.fromkeys(
            part.strip().lower()
            for part in str(value or "").split(",")
            if part.strip()
        )
    )
    if not requested:
        raise ValueError("至少选择一个连载更新来源")
    allowed = {*DEFAULT_SOURCE_NAMES, "txt80"}
    invalid = sorted(set(requested) - allowed)
    if invalid:
        raise ValueError(f"未知连载更新来源：{','.join(invalid)}")
    return requested


def selected_source_specs(
    service: ElectronicLibraryService,
    source_names: tuple[str, ...],
) -> tuple[tuple[str, Any], ...]:
    providers = {
        "xbiquge": service.xbiquge_provider,
        "ixdzs": service.ixdzs_provider,
        "shubaow": service.shubaow_provider,
        "linovelib": service.linovelib_provider,
        "txt80": service.txt80_provider,
    }
    return tuple((name, providers[name]) for name in source_names)


def source_path(service: ElectronicLibraryService, row: dict[str, Any]) -> Path | None:
    legacy = str(row.get("legacy_output_path") or "").strip()
    if legacy:
        candidate = Path(legacy).expanduser()
        if candidate.is_file():
            return candidate.resolve()
    object_key = str(row.get("body_object_key") or "").strip()
    if not object_key:
        return None
    root = service.infrastructure_settings.object_root.resolve()
    candidate = (root / object_key).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def local_latest_chapter(
    service: ElectronicLibraryService,
    row: dict[str, Any],
) -> str:
    path = source_path(service, row)
    if not path:
        return ""
    index = service._reader_index(int(row["id"]), path)
    chapters = [
        value
        for value in index.get("chapters") or []
        if value.get("kind") == "chapter"
    ]
    if not chapters:
        return ""
    latest = chapters[-1]
    label = str(latest.get("label") or "").strip()
    title = str(latest.get("title") or "").strip()
    if title.startswith(label):
        return title
    return f"{label} {title}".strip()


def observe(
    service: ElectronicLibraryService,
    *,
    source_name: str,
    source_id: str,
    detail_url: str,
    item: dict[str, Any],
    persist: bool = True,
) -> dict[str, Any]:
    item = normalized_source_item(item)
    identity_key = service._catalog_identity_key(
        item.get("title"), item.get("author")
    )
    identity_hash = service.mysql_catalog._identity_hash(identity_key)
    with service.mysql_pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, source_id, title, author, category,
                       book_status, status,
                       body_available, legacy_output_path, body_object_key
                FROM books WHERE source_id=%s LIMIT 1
                """,
                (source_id,),
            )
            book = cursor.fetchone()
            matched_by = "source_id"
            if (
                not book
                or str(book.get("status") or "") != "done"
                or not int(book.get("body_available") or 0)
            ):
                cursor.execute(
                    """
                    SELECT id, source_id, title, author, category,
                           book_status, status,
                           body_available, legacy_output_path, body_object_key
                    FROM books
                    WHERE identity_hash=%s AND is_active=1
                    ORDER BY body_available DESC, bytes DESC, id
                    LIMIT 1
                    """,
                    (identity_hash,),
                )
                identity_book = cursor.fetchone()
                if identity_book:
                    book = identity_book
                    matched_by = "title_author"
            if persist:
                cursor.execute(
                    """
                INSERT INTO authorized_source_updates (
                    source_name, source_id, catalog_id, title, author,
                    detail_url, remote_revision, remote_latest_chapter,
                    remote_updated_at, state, last_seen_at
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,'observed',UTC_TIMESTAMP(6)
                )
                ON DUPLICATE KEY UPDATE
                    catalog_id=VALUES(catalog_id), title=VALUES(title),
                    author=VALUES(author), detail_url=VALUES(detail_url),
                    remote_revision=VALUES(remote_revision),
                    remote_latest_chapter=VALUES(remote_latest_chapter),
                    remote_updated_at=VALUES(remote_updated_at),
                    state=CASE
                        WHEN applied_revision=VALUES(remote_revision)
                        THEN 'applied' ELSE 'observed' END,
                    last_seen_at=UTC_TIMESTAMP(6)
                    """,
                    (
                        source_name,
                        source_id,
                        int(book["id"]) if book else None,
                        str(item.get("title") or ""),
                        str(item.get("author") or ""),
                        detail_url,
                        str(item.get("remote_revision") or ""),
                        str(item.get("remote_latest_chapter") or ""),
                        str(item.get("remote_updated_at") or ""),
                    ),
                )
            cursor.execute(
                """
                SELECT applied_revision, state
                FROM authorized_source_updates
                WHERE source_name=%s AND source_id=%s
                """,
                (source_name, source_id),
            )
            checkpoint_row = cursor.fetchone()
            checkpoint = dict(checkpoint_row) if checkpoint_row else {
                "applied_revision": "",
                "state": "unseen",
            }
    return {
        "book": dict(book) if book else None,
        "matched_by": matched_by,
        **checkpoint,
    }


def mark_current(
    service: ElectronicLibraryService,
    *,
    source_name: str,
    source_id: str,
    catalog_id: int,
    revision: str,
    local_latest: str,
) -> None:
    with service.mysql_pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE authorized_source_updates
                SET catalog_id=%s, applied_revision=%s,
                    local_latest_chapter=%s, state='applied',
                    last_error=NULL, applied_at=UTC_TIMESTAMP(6),
                    last_seen_at=UTC_TIMESTAMP(6)
                WHERE source_name=%s AND source_id=%s
                """,
                (
                    int(catalog_id), revision, local_latest,
                    source_name, source_id,
                ),
            )


def mark_failed(
    service: ElectronicLibraryService,
    *,
    source_name: str,
    source_id: str,
    error: str,
) -> None:
    with service.mysql_pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE authorized_source_updates
                SET state='failed', last_error=%s,
                    last_seen_at=UTC_TIMESTAMP(6)
                WHERE source_name=%s AND source_id=%s
                """,
                (error[:1000], source_name, source_id),
            )


def probe_update_item(provider: Any, item: dict[str, Any]) -> dict[str, Any]:
    probe = getattr(provider, "probe_update", None)
    return normalized_source_item(probe(item)) if callable(probe) else item


def write_state(payload: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, STATE_PATH)


def seconds_since_last_completion() -> float | None:
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        completed_at = float(payload.get("completed_at_epoch") or 0)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if completed_at <= 0:
        return None
    return max(0.0, time.time() - completed_at)


def fetch_and_register_page(
    service: ElectronicLibraryService,
    provider: Any,
    page: int,
    *,
    dry_run: bool,
    register_catalog: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Retry a whole page atomically; registration itself is idempotent."""

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            items = [
                normalized_source_item(item)
                for item in provider.latest_updates(page)
            ]
            registered = (
                {"added": 0, "known": 0, "duplicates": 0}
                if dry_run or not register_catalog or not items
                else service.register_authorized_catalog_items(items)
            )
            return items, registered
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt * 2)
    assert last_error is not None
    raise last_error


async def refresh_one(
    service: ElectronicLibraryService,
    *,
    source_name: str,
    candidate: dict[str, Any],
    ai_service: Any,
) -> dict[str, Any]:
    item = candidate["item"]
    book = candidate["book"]
    source_id = str(candidate["source_id"])
    remote_id = str(item.get("remote_id") or "")
    provider = {
        "xbiquge": service.xbiquge_provider.PROVIDER_ID,
        "ixdzs": service.ixdzs_provider.PROVIDER_ID,
        "shubaow": service.shubaow_provider.PROVIDER_ID,
        "linovelib": service.linovelib_provider.PROVIDER_ID,
        "txt80": service.txt80_provider.PROVIDER_ID,
    }[source_name]
    kwargs: dict[str, Any] = {
        "provider": provider,
        "remote_id": remote_id,
        "source_ref": str(item.get("source_ref") or ""),
        "book_status": str(item.get("book_status") or ""),
        "category_hint": str(book.get("category") or ""),
        "defer_postprocess": True,
        "refresh_existing_catalog_id": int(book["id"]),
        "ai_service": ai_service,
    }
    if source_name in {"xbiquge", "shubaow"}:
        kwargs["expected_latest_chapter"] = str(
            item.get("remote_latest_chapter") or ""
        )
    result = await service.import_public_book(**kwargs)
    catalog_id = int(result.get("catalog_id") or book["id"])
    if source_name == "ixdzs":
        result["branding"] = await asyncio.to_thread(
            service._apply_source_branding_normalizer,
            catalog_id,
            "ixdzs",
        )
    await asyncio.to_thread(
        mark_current,
        service,
        source_name=source_name,
        source_id=source_id,
        catalog_id=catalog_id,
        revision=str(item.get("remote_revision") or ""),
        local_latest=str(item.get("remote_latest_chapter") or ""),
    )
    return result


async def refresh_source_direct(
    service: ElectronicLibraryService,
    *,
    source_name: str,
    candidates: list[dict[str, Any]],
    workers: int,
    ai_service: Any,
) -> tuple[int, list[str]]:
    """Apply one website's refreshes before moving to the next website."""

    semaphore = asyncio.Semaphore(max(1, int(workers)))
    applied = 0
    errors: list[str] = []

    async def worker(candidate: dict[str, Any]) -> None:
        nonlocal applied
        async with semaphore:
            try:
                await refresh_one(
                    service,
                    source_name=source_name,
                    candidate=candidate,
                    ai_service=ai_service,
                )
                applied += 1
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:500]}"
                errors.append(
                    f"catalog_id={candidate['book']['id']}: {error}"
                )
                await asyncio.to_thread(
                    mark_failed,
                    service,
                    source_name=source_name,
                    source_id=str(candidate["source_id"]),
                    error=error,
                )

    await asyncio.gather(*(worker(candidate) for candidate in candidates))
    return applied, errors


def run(
    *,
    pages: int,
    dry_run: bool,
    source_names: tuple[str, ...] = DEFAULT_SOURCE_NAMES,
    apply_direct: bool = False,
    limit_per_source: int = 20,
    source_workers: dict[str, int] | None = None,
) -> dict[str, Any]:
    service = ElectronicLibraryService()
    if service.infrastructure_settings.catalog_backend != "mysql":
        raise RuntimeError("连载更新同步要求 WEBNOVEL_CATALOG_BACKEND=mysql")
    queue = (
        None
        if apply_direct or dry_run
        else LibraryDownloadQueue(service.mysql_pool, service.redis_queue)
    )
    specs = selected_source_specs(service, source_names)
    worker_counts = {
        "xbiquge": 8,
        "ixdzs": 8,
        "shubaow": 5,
        "linovelib": 5,
        "txt80": 4,
        **(source_workers or {}),
    }
    ai_service = get_ai_service() if apply_direct and not dry_run else None
    totals = {
        "seen": 0,
        "catalog_added": 0,
        "catalog_known": 0,
        "catalog_duplicates": 0,
        "local_matches": 0,
        "already_current": 0,
        "refresh_selected": 0,
        "refresh_applied": 0,
        "refresh_queued": 0,
        "already_queued": 0,
        "refresh_deferred": 0,
        "not_ready": 0,
        "failed": 0,
    }
    sources: dict[str, Any] = {}
    for source_name, provider in specs:
        source_stats = {key: 0 for key in totals}
        errors: list[str] = []
        direct_candidates: list[dict[str, Any]] = []
        selected_source_ids: set[str] = set()
        for page in range(1, pages + 1):
            try:
                items, registered = fetch_and_register_page(
                    service,
                    provider,
                    page,
                    dry_run=dry_run,
                    # The active catalog scanner immediately follows this
                    # direct refresh stage. Keep update observation isolated
                    # from global identity claiming so one unrelated new
                    # listing cannot suppress refreshes for an entire page.
                    register_catalog=not apply_direct,
                )
                if not items:
                    break
                source_stats["seen"] += len(items)
                source_stats["catalog_added"] += int(registered["added"])
                source_stats["catalog_known"] += int(registered["known"])
                source_stats["catalog_duplicates"] += int(
                    registered["duplicates"]
                )
                for item in items:
                    current_source, source_id, detail_url = source_fields(service, item)
                    observed = observe(
                        service,
                        source_name=current_source,
                        source_id=source_id,
                        detail_url=detail_url,
                        item=item,
                        persist=not dry_run,
                    )
                    book = observed["book"]
                    if not book:
                        continue
                    source_stats["local_matches"] += 1
                    if (
                        str(book.get("status")) != "done"
                        or not int(book.get("body_available") or 0)
                    ):
                        source_stats["not_ready"] += 1
                        continue
                    if callable(getattr(provider, "probe_update", None)):
                        try:
                            item = probe_update_item(provider, item)
                            current_source, source_id, detail_url = source_fields(
                                service, item
                            )
                            observed = observe(
                                service,
                                source_name=current_source,
                                source_id=source_id,
                                detail_url=detail_url,
                                item=item,
                                persist=not dry_run,
                            )
                        except Exception as exc:
                            source_stats["failed"] += 1
                            errors.append(
                                "catalog_id="
                                f"{book['id']} update-probe: "
                                f"{type(exc).__name__}: {str(exc)[:300]}"
                            )
                            continue
                    revision = str(item.get("remote_revision") or "")
                    if revision and revision == str(
                        observed.get("applied_revision") or ""
                    ):
                        source_stats["already_current"] += 1
                        continue
                    remote_status = str(item.get("book_status") or "").strip()
                    local_status = str(book.get("book_status") or "").strip()
                    ongoing = (
                        remote_status.casefold() not in COMPLETED_STATUSES
                        or local_status.casefold() not in COMPLETED_STATUSES
                    )
                    if not ongoing and not revision:
                        source_stats["not_ready"] += 1
                        continue
                    try:
                        local_latest = local_latest_chapter(service, book)
                    except Exception as exc:
                        local_latest = ""
                        errors.append(
                            f"catalog_id={book['id']} local-index: {type(exc).__name__}: {str(exc)[:220]}"
                        )
                    remote_latest = str(
                        item.get("remote_latest_chapter") or ""
                    ).strip()
                    if (
                        remote_latest
                        and local_latest
                        and clean(remote_latest) == clean(local_latest)
                    ):
                        if not dry_run:
                            mark_current(
                                service,
                                source_name=current_source,
                                source_id=source_id,
                                catalog_id=int(book["id"]),
                                revision=revision,
                                local_latest=local_latest,
                            )
                        source_stats["already_current"] += 1
                        continue
                    if dry_run:
                        source_stats["refresh_queued"] += 1
                        continue
                    if apply_direct:
                        if source_id in selected_source_ids:
                            continue
                        if len(direct_candidates) >= max(
                            int(limit_per_source), 0
                        ):
                            source_stats["refresh_deferred"] += 1
                            continue
                        direct_candidates.append(
                            {
                                "item": item,
                                "book": book,
                                "source_id": source_id,
                                "detail_url": detail_url,
                                "matched_by": observed["matched_by"],
                            }
                        )
                        selected_source_ids.add(source_id)
                        source_stats["refresh_selected"] += 1
                        continue
                    payload = {
                        **item,
                        "update_mode": "serialized_refresh",
                        "remote_revision": revision,
                        "remote_latest_chapter": remote_latest,
                        "local_latest_chapter": local_latest,
                        "matched_by": observed["matched_by"],
                    }
                    assert queue is not None
                    scheduled = queue.schedule_serialized_refresh(
                        source_name=current_source,
                        source_id=source_id,
                        title=str(item.get("title") or ""),
                        author=str(item.get("author") or ""),
                        detail_url=detail_url,
                        payload=payload,
                    )
                    status = str(scheduled.get("status") or "")
                    if status == "queued":
                        source_stats["refresh_queued"] += 1
                    elif status == "already_queued":
                        source_stats["already_queued"] += 1
                    else:
                        source_stats["not_ready"] += 1
            except Exception as exc:
                source_stats["failed"] += 1
                errors.append(f"page={page}: {type(exc).__name__}: {str(exc)[:500]}")
        if apply_direct and not dry_run and direct_candidates:
            assert ai_service is not None
            applied, refresh_errors = asyncio.run(
                refresh_source_direct(
                    service,
                    source_name=source_name,
                    candidates=direct_candidates,
                    workers=worker_counts[source_name],
                    ai_service=ai_service,
                )
            )
            source_stats["refresh_applied"] += applied
            source_stats["failed"] += len(refresh_errors)
            errors.extend(refresh_errors)
        for key in totals:
            totals[key] += int(source_stats[key])
        sources[source_name] = {**source_stats, "errors": errors[-20:]}
    index_refresh: dict[str, Any] = {"status": "not_required"}
    if apply_direct and totals["refresh_applied"]:
        index_refresh = service.start_queued_ingestion_index_refresh(wait=True)
    result = {
        "status": "dry_run" if dry_run else "completed",
        "mode": (
            "dry_run" if dry_run else ("direct" if apply_direct else "queue")
        ),
        "pages_per_source": pages,
        "sources_requested": list(source_names),
        "totals": totals,
        "sources": sources,
        "ingestion_index": index_refresh,
        "completed_at_epoch": time.time(),
        "state_path": str(STATE_PATH),
    }
    if not dry_run:
        write_state(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCE_NAMES),
        help=(
            "逗号分隔的更新来源；默认按新笔趣阁、爱下、书宝、"
            "Linovelib 严格串行"
        ),
    )
    parser.add_argument(
        "--apply-direct",
        action="store_true",
        help="直接原子刷新本地原书；否则写入兼容下载队列",
    )
    parser.add_argument("--limit-per-source", type=int, default=20)
    parser.add_argument("--min-interval", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--xbiquge-workers", type=int, default=8)
    parser.add_argument("--ixdzs-workers", type=int, default=8)
    parser.add_argument("--shubaow-workers", type=int, default=5)
    parser.add_argument("--linovelib-workers", type=int, default=5)
    args = parser.parse_args()
    if args.pages < 1 or args.pages > 100:
        parser.error("--pages 必须在 1..100")
    if args.limit_per_source < 0 or args.limit_per_source > 200:
        parser.error("--limit-per-source 必须在 0..200")
    if args.min_interval < 0 or args.min_interval > 604800:
        parser.error("--min-interval 必须在 0..604800")
    try:
        source_names = parse_source_names(args.sources)
    except ValueError as exc:
        parser.error(str(exc))
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running"}, ensure_ascii=False))
            return 0
        elapsed = seconds_since_last_completion()
        if (
            not args.force
            and int(args.min_interval) > 0
            and elapsed is not None
            and elapsed < int(args.min_interval)
        ):
            result = {
                "status": "skipped_recent",
                "seconds_since_last_completion": round(elapsed, 3),
                "min_interval": int(args.min_interval),
                "state_path": str(STATE_PATH),
            }
        else:
            result = run(
                pages=int(args.pages),
                dry_run=bool(args.dry_run),
                source_names=source_names,
                apply_direct=bool(args.apply_direct),
                limit_per_source=int(args.limit_per_source),
                source_workers={
                    "xbiquge": int(args.xbiquge_workers),
                    "ixdzs": int(args.ixdzs_workers),
                    "shubaow": int(args.shubaow_workers),
                    "linovelib": int(args.linovelib_workers),
                },
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
