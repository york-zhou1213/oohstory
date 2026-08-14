#!/usr/bin/env python3
"""Incrementally ingest new books from owner-authorized whole sites."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import fcntl
import gc
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


from project_paths import APP_ROOT  # noqa: E402


BACKEND_ROOT = APP_ROOT / "src"
LIBRARY_ROOT = APP_ROOT / "electronic-library"
STATE_PATH = LIBRARY_ROOT / "全局索引" / "authorized-site-catalog-sync.json"
LOCK_PATH = STATE_PATH.with_suffix(".lock")
CATEGORY_BACKFILL_STATE_PATH = (
    LIBRARY_ROOT
    / "全局索引"
    / "authorized-site-category-backfill.json"
)
sys.path.insert(0, str(BACKEND_ROOT))

from oohstory_library.services.ai_service import get_ai_service  # noqa: E402
from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402


AUTHORIZED_SOURCE_NAMES = ("xbiquge", "ixdzs", "shubaow", "linovelib")
DEFAULT_SOURCE_NAMES = AUTHORIZED_SOURCE_NAMES


def parse_source_names(value: str) -> tuple[str, ...]:
    requested = tuple(
        dict.fromkeys(
            part.strip().lower()
            for part in str(value or "").split(",")
            if part.strip()
        )
    )
    if not requested:
        raise ValueError("至少选择一个授权同步来源")
    invalid = sorted(set(requested) - set(AUTHORIZED_SOURCE_NAMES))
    if invalid:
        raise ValueError(f"未知授权同步来源：{','.join(invalid)}")
    return requested


def selected_source_specs(
    service: ElectronicLibraryService,
    source_names: tuple[str, ...],
) -> tuple[tuple[str, Any, int], ...]:
    available = {
        "xbiquge": (service.xbiquge_provider, 8),
        "ixdzs": (service.ixdzs_provider, 12),
        "shubaow": (service.shubaow_provider, 12),
        "linovelib": (service.linovelib_provider, 14),
    }
    return tuple(
        (source_name, *available[source_name])
        for source_name in source_names
    )


def atomic_state(payload: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, STATE_PATH)


def load_state() -> dict[str, Any]:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    state.setdefault(
        "xbiquge",
        {"category": 1, "page": 1, "queue": [], "complete": False},
    )
    state.setdefault(
        "ixdzs",
        {"category": 1, "page": 1, "queue": [], "complete": False},
    )
    state.setdefault(
        "shubaow",
        {"category": 1, "page": 1, "queue": [], "complete": False},
    )
    state.setdefault(
        "linovelib",
        {"category": 1, "page": 1, "queue": [], "complete": False},
    )
    for source in AUTHORIZED_SOURCE_NAMES:
        state[source].setdefault("complete", False)
    stats = state.setdefault("stats", {})
    for key in (
        "discovered",
        "catalog_added",
        "catalog_updated",
        "catalog_known",
        "deduplicated",
        "invalid",
        "skipped",
        "imported",
        "failed",
    ):
        stats.setdefault(key, 0)
    return state


def load_category_backfill_state(
    discovery_state: dict[str, Any],
) -> dict[str, Any]:
    """Load an independent cursor bounded by the catalog already discovered.

    The regular discovery cursor must never be rewound: doing so would mix a
    metadata repair with the live new-book queue.  A category backfill instead
    snapshots the next unscanned page for each source and only revisits pages
    that the normal scan had already consumed at that moment.
    """
    try:
        state = json.loads(
            CATEGORY_BACKFILL_STATE_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        state = {}
    sources = state.setdefault("sources", {})
    for source in AUTHORIZED_SOURCE_NAMES:
        if source in sources:
            continue
        discovery_cursor = discovery_state[source]
        sources[source] = {
            "category": 1,
            "page": 1,
            "target_category": int(
                discovery_cursor.get("category") or 1
            ),
            "target_page": int(discovery_cursor.get("page") or 1),
            "target_complete": bool(discovery_cursor.get("complete")),
            "complete": False,
        }
    stats = state.setdefault(
        "stats",
        {
            "seen": 0,
            "updated": 0,
            "known": 0,
            "invalid": 0,
        },
    )
    return state


def atomic_category_backfill_state(payload: dict[str, Any]) -> None:
    CATEGORY_BACKFILL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CATEGORY_BACKFILL_STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, CATEGORY_BACKFILL_STATE_PATH)


def category_backfill_reached_snapshot(cursor: dict[str, Any]) -> bool:
    if bool(cursor.get("target_complete")):
        return bool(cursor.get("complete"))
    category = int(cursor.get("category") or 1)
    page = int(cursor.get("page") or 1)
    target_category = int(cursor.get("target_category") or 1)
    target_page = int(cursor.get("target_page") or 1)
    return category > target_category or (
        category == target_category and page >= target_page
    )


def advance(cursor: dict[str, Any], *, categories: int, found: bool) -> None:
    if found:
        cursor["page"] = int(cursor.get("page") or 1) + 1
        return
    next_category = int(cursor.get("category") or 1) + 1
    if next_category > categories:
        cursor["complete"] = True
        return
    cursor["category"] = next_category
    cursor["page"] = 1


async def run_category_backfill(
    page_limit: int,
    source_names: tuple[str, ...] = DEFAULT_SOURCE_NAMES,
) -> dict[str, Any]:
    """Repair categories on already-seen pages without moving live cursors."""
    service = ElectronicLibraryService()
    state = load_category_backfill_state(load_state())
    source_specs = selected_source_specs(service, source_names)
    run_results: dict[str, Any] = {}
    for source_name, provider, categories in source_specs:
        cursor = state["sources"][source_name]
        pages_scanned = 0
        aggregate = {
            "seen": 0,
            "added": 0,
            "updated": 0,
            "known": 0,
            "duplicates": 0,
            "invalid": 0,
        }
        last_error = ""
        while pages_scanned < page_limit:
            if category_backfill_reached_snapshot(cursor):
                cursor["complete"] = True
                break
            try:
                items = await asyncio.to_thread(
                    provider.catalog_page,
                    int(cursor["category"]),
                    int(cursor["page"]),
                )
                registered = service.register_authorized_catalog_items(items)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                cursor["last_error"] = last_error
                atomic_category_backfill_state(state)
                break
            for key in aggregate:
                aggregate[key] += int(registered[key])
            state["stats"]["seen"] += int(registered["seen"])
            state["stats"]["updated"] += int(registered["updated"])
            state["stats"]["known"] += int(registered["known"])
            state["stats"]["invalid"] += int(registered["invalid"])
            advance(cursor, categories=categories, found=bool(items))
            pages_scanned += 1
            cursor.pop("last_error", None)
            if category_backfill_reached_snapshot(cursor):
                cursor["complete"] = True
            atomic_category_backfill_state(state)
        run_results[source_name] = {
            "pages": pages_scanned,
            "registered": aggregate,
            "complete": bool(cursor.get("complete")),
            "error": last_error,
        }
    return {
        "status": "completed",
        "mode": "category_backfill",
        "sources": run_results,
        "stats": state["stats"],
        "state_path": str(CATEGORY_BACKFILL_STATE_PATH),
    }


def accumulate_registration(
    state: dict[str, Any],
    registered: dict[str, Any],
) -> None:
    state["stats"]["catalog_added"] += int(registered["added"])
    state["stats"]["catalog_updated"] += int(registered["updated"])
    state["stats"]["catalog_known"] += int(registered["known"])
    state["stats"]["deduplicated"] += int(registered["duplicates"])
    state["stats"]["invalid"] += int(registered["invalid"])


def initialize_mysql_dedup_state(
    service: ElectronicLibraryService,
    state: dict[str, Any],
) -> None:
    if (
        service.infrastructure_settings.catalog_backend != "mysql"
        or service.mysql_catalog is None
    ):
        return
    state["stats"]["deduplicated"] = max(
        int(state["stats"].get("deduplicated") or 0),
        service.mysql_catalog.authorized_deduplicated_count(),
    )


def persist_mysql_dedup_state(
    service: ElectronicLibraryService,
    state: dict[str, Any],
) -> None:
    if (
        service.infrastructure_settings.catalog_backend == "mysql"
        and service.mysql_catalog is not None
    ):
        service.mysql_catalog.set_authorized_deduplicated_count(
            int(state["stats"].get("deduplicated") or 0)
        )


def source_scan_rounds(
    sources: tuple[tuple[str, Any, int], ...],
    page_limit: int,
):
    """Finish one bounded site phase before moving to the next site."""

    for source in sources:
        for _ in range(max(int(page_limit), 0)):
            yield source


def catalog_download_candidates(
    service: ElectronicLibraryService,
    limit: int,
    source_names: tuple[str, ...] = DEFAULT_SOURCE_NAMES,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    if service.infrastructure_settings.catalog_backend == "mysql":
        return MySQLLibraryRuntime(
            service.infrastructure_settings,
            service.mysql_pool,
            service.redis_queue,
        ).authorized_download_candidates(
            limit,
            source_names=source_names,
        )
    with sqlite3.connect(service.catalog_path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_xinfo(books)")
        }
        book_status_select = (
            "book_status" if "book_status" in columns else "NULL AS book_status"
        )
        if "source_kind" in columns:
            placeholders = ",".join("?" for _ in source_names)
            source_condition = f"source_kind IN ({placeholders})"
            source_params: tuple[Any, ...] = tuple(source_names)
        else:
            source_condition = "(" + " OR ".join(
                "source_id LIKE ?" for _ in source_names
            ) + ")"
            source_params = tuple(f"{name}-%" for name in source_names)
        retry_condition = (
            "AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))"
            if "next_retry_at" in columns
            else ""
        )
        rows = conn.execute(
            f"""
            SELECT id, source_id, detail_url, title, author,
                   {book_status_select}
            FROM books
            WHERE status IN ('discovered', 'failed')
              AND attempts < 4
              AND {source_condition}
              {retry_condition}
            ORDER BY CASE status WHEN 'discovered' THEN 0 ELSE 1 END,
                     attempts, id
            LIMIT ?
            """,
            (*source_params, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def update_download_result(
    service: ElectronicLibraryService,
    catalog_id: int,
    *,
    completed: bool = False,
    duplicate: bool = False,
    error: str = "",
) -> None:
    if service.infrastructure_settings.catalog_backend == "mysql":
        runtime = MySQLLibraryRuntime(
            service.infrastructure_settings,
            service.mysql_pool,
            service.redis_queue,
        )
        with runtime.pool.connection() as connection:
            with connection.cursor() as cursor:
                if completed:
                    cursor.execute(
                        """
                        UPDATE books SET status='done', last_error=NULL,
                            updated_at=UTC_TIMESTAMP(6),
                            row_version=row_version+1
                        WHERE id=%s
                        """,
                        (int(catalog_id),),
                    )
                    cursor.execute(
                        """
                        UPDATE download_jobs SET status='done',
                            last_error=NULL,
                            completed_at=UTC_TIMESTAMP(6),
                            lease_owner=NULL, lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s
                        """,
                        (int(catalog_id),),
                    )
                elif duplicate:
                    cursor.execute(
                        """
                        UPDATE books SET status='duplicate', last_error=NULL,
                            updated_at=UTC_TIMESTAMP(6),
                            row_version=row_version+1
                        WHERE id=%s
                        """,
                        (int(catalog_id),),
                    )
                    cursor.execute(
                        """
                        UPDATE download_jobs SET status='done',
                            completed_at=UTC_TIMESTAMP(6),
                            lease_owner=NULL, lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s
                        """,
                        (int(catalog_id),),
                    )
                elif error:
                    cursor.execute(
                        """
                        UPDATE books SET status='failed',
                            attempts=attempts+1, last_error=%s,
                            updated_at=UTC_TIMESTAMP(6),
                            row_version=row_version+1
                        WHERE id=%s
                        """,
                        (error[:500], int(catalog_id)),
                    )
                    cursor.execute(
                        """
                        UPDATE download_jobs SET status='pending',
                            attempts=attempts+1, last_error=%s,
                            available_at=UTC_TIMESTAMP(6)
                                + INTERVAL 5 MINUTE,
                            lease_owner=NULL, lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s
                        """,
                        (error[:500], int(catalog_id)),
                    )
        runtime.invalidate_catalog_cache()
        return
    with sqlite3.connect(service.catalog_path, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        if completed:
            conn.execute(
                """
                UPDATE books
                SET status='done', last_error=NULL, updated_at=datetime('now')
                WHERE id=?
                """,
                (int(catalog_id),),
            )
        elif duplicate:
            conn.execute(
                """
                UPDATE books
                SET status='duplicate', last_error=NULL, updated_at=datetime('now')
                WHERE id=?
                """,
                (int(catalog_id),),
            )
        elif error:
            conn.execute(
                """
                UPDATE books
                SET status='failed', attempts=attempts+1,
                    last_error=?, updated_at=datetime('now')
                WHERE id=?
                """,
                (error[:500], int(catalog_id)),
            )
        conn.commit()


def source_worker_count(
    source_name: str,
    configured: dict[str, int] | None = None,
) -> int:
    defaults = {
        "xbiquge": 2,
        "ixdzs": 2,
        "shubaow": 2,
        "linovelib": 2,
    }
    requested = int((configured or {}).get(source_name, defaults[source_name]))
    return min(max(requested, 1), 8)


def release_completed_download_memory() -> None:
    """Return completed whole-book buffers before the next site/index phase."""

    gc.collect()
    try:
        malloc_trim = ctypes.CDLL(None).malloc_trim
        malloc_trim.argtypes = [ctypes.c_size_t]
        malloc_trim.restype = ctypes.c_int
        malloc_trim(0)
    except (AttributeError, OSError):
        pass


async def download_one_candidate(
    *,
    service: ElectronicLibraryService,
    ai_service: Any,
    state: dict[str, Any],
    source_name: str,
    item: dict[str, Any],
) -> str:
    source_id = str(item.get("source_id") or "")
    expected_prefix = f"{source_name}-"
    if not source_id.startswith(expected_prefix):
        error = (
            f"来源隔离校验失败：任务 {source_id or '<empty>'} "
            f"不属于 {source_name}"
        )
        update_download_result(
            service,
            int(item["id"]),
            error=error,
        )
        state["stats"]["failed"] += 1
        return "failed"

    provider = {
        "xbiquge": service.xbiquge_provider.PROVIDER_ID,
        "ixdzs": service.ixdzs_provider.PROVIDER_ID,
        "shubaow": service.shubaow_provider.PROVIDER_ID,
        "linovelib": service.linovelib_provider.PROVIDER_ID,
    }[source_name]
    remote_id = source_id.removeprefix(expected_prefix)
    source_ref = urlparse(str(item.get("detail_url") or "")).path
    try:
        result = await service.import_public_book(
            provider=provider,
            remote_id=remote_id,
            source_ref=source_ref,
            book_status=str(item.get("book_status") or ""),
            defer_postprocess=True,
            ai_service=ai_service,
        )
        if result.get("status") == "already_available":
            state["stats"]["skipped"] += 1
            if int(result.get("catalog_id") or 0) != int(item["id"]):
                update_download_result(
                    service,
                    int(item["id"]),
                    duplicate=True,
                )
                return "duplicate"
            update_download_result(
                service,
                int(item["id"]),
                completed=True,
            )
            return "already_available"
        state["stats"]["imported"] += 1
        return "imported"
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        update_download_result(
            service,
            int(item["id"]),
            error=error,
        )
        state["stats"]["failed"] += 1
        return "failed"


async def download_source_phase(
    *,
    service: ElectronicLibraryService,
    ai_service: Any,
    state: dict[str, Any],
    source_name: str,
    limit: int,
    workers: int,
) -> dict[str, Any]:
    """Drain one bounded source batch with no overlap from another site."""

    candidates = catalog_download_candidates(
        service,
        limit,
        (source_name,),
    )
    worker_total = min(
        source_worker_count(source_name, {source_name: workers}),
        max(len(candidates), 1),
    )
    phase: dict[str, Any] = {
        "source": source_name,
        "status": "running" if candidates else "queue_empty",
        "workers": worker_total if candidates else 0,
        "selected": len(candidates),
        "attempted": 0,
        "imported": 0,
        "already_available": 0,
        "duplicates": 0,
        "failed": 0,
    }
    state["download_phase"] = dict(phase)
    atomic_state(state)
    if not candidates:
        return phase

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    for item in candidates:
        queue.put_nowait(item)

    async def worker() -> None:
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            outcome = await download_one_candidate(
                service=service,
                ai_service=ai_service,
                state=state,
                source_name=source_name,
                item=item,
            )
            phase["attempted"] += 1
            if outcome == "imported":
                phase["imported"] += 1
            elif outcome == "already_available":
                phase["already_available"] += 1
            elif outcome == "duplicate":
                phase["duplicates"] += 1
            else:
                phase["failed"] += 1
            state["download_phase"] = dict(phase)
            atomic_state(state)
            queue.task_done()

    await asyncio.gather(*(worker() for _ in range(worker_total)))
    release_completed_download_memory()
    phase["status"] = "completed"
    state["download_phase"] = dict(phase)
    atomic_state(state)
    return phase


async def download_sources_serially(
    *,
    service: ElectronicLibraryService,
    ai_service: Any,
    state: dict[str, Any],
    source_names: tuple[str, ...],
    limit: int,
    source_workers: dict[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for source_name in source_names:
        # Await the complete source batch before starting the next source.
        # Workers add concurrency only inside this one active website.
        runs[source_name] = await download_source_phase(
            service=service,
            ai_service=ai_service,
            state=state,
            source_name=source_name,
            limit=limit,
            workers=source_worker_count(source_name, source_workers),
        )
    return runs


async def run(
    download_limit: int,
    page_limit: int,
    source_names: tuple[str, ...] = DEFAULT_SOURCE_NAMES,
    source_workers: dict[str, int] | None = None,
) -> dict[str, Any]:
    service = ElectronicLibraryService()
    state = load_state()
    initialize_mysql_dedup_state(service, state)
    sources = selected_source_specs(service, source_names)
    scan_runs: dict[str, dict[str, Any]] = {}
    for source_name, provider, categories in sources:
        cursor = state[source_name]
        aggregate = {
            "seen": 0,
            "added": 0,
            "updated": 0,
            "known": 0,
            "duplicates": 0,
            "invalid": 0,
        }
        queued_items = list(cursor.pop("queue", []))
        if queued_items:
            registered = service.register_authorized_catalog_items(queued_items)
            accumulate_registration(state, registered)
            persist_mysql_dedup_state(service, state)
            for key in aggregate:
                aggregate[key] += int(registered[key])
            atomic_state(state)
        scan_runs[source_name] = {
            "provider": provider,
            "categories": categories,
            "aggregate": aggregate,
            "pages": 0,
            "error": "",
        }

    # Sites are strict phases: finish the bounded Xbiquge scan before Ixdzs,
    # and never run two source scanners at once.  This mirrors the download
    # phases below and keeps per-site traffic isolated.
    for source_name, provider, categories in source_scan_rounds(
        sources,
        page_limit,
    ):
        cursor = state[source_name]
        source_run = scan_runs[source_name]
        if bool(cursor.get("complete")) or source_run["error"]:
            continue
        try:
            items = await asyncio.to_thread(
                provider.catalog_page,
                int(cursor["category"]),
                int(cursor["page"]),
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            source_run["error"] = last_error
            cursor["last_error"] = last_error
            atomic_state(state)
            continue

        state["stats"]["discovered"] += len(items)
        registered = service.register_authorized_catalog_items(items)
        accumulate_registration(state, registered)
        persist_mysql_dedup_state(service, state)
        for key in source_run["aggregate"]:
            source_run["aggregate"][key] += int(registered[key])
        advance(cursor, categories=categories, found=bool(items))
        source_run["pages"] += 1
        cursor.pop("last_error", None)
        atomic_state(state)

    for source_name, _provider, _categories in sources:
        cursor = state[source_name]
        source_run = scan_runs[source_name]
        cursor["last_run"] = {
            "pages": int(source_run["pages"]),
            "items": int(source_run["aggregate"]["seen"]),
            "registered": source_run["aggregate"],
            "complete": bool(cursor.get("complete")),
            "error": str(source_run["error"]),
        }
        atomic_state(state)

    ai_service = get_ai_service()
    download_runs = await download_sources_serially(
        service=service,
        ai_service=ai_service,
        state=state,
        source_names=source_names,
        limit=download_limit,
        source_workers=source_workers,
    )
    # Every import above only appends its exact catalog id to the dedicated
    # plot-free queue.  Start that queue once, after all website workers have
    # released their whole-book buffers, and wait before this timer round can
    # schedule the next crawl.
    release_completed_download_memory()
    index_refresh = await asyncio.to_thread(
        service.start_queued_ingestion_index_refresh,
        wait=True,
    )

    attempted_now = sum(
        int(result["attempted"]) for result in download_runs.values()
    )
    imported_now = sum(
        int(result["imported"]) for result in download_runs.values()
    )

    if service.infrastructure_settings.catalog_backend == "mysql":
        queued = MySQLLibraryRuntime(
            service.infrastructure_settings,
            service.mysql_pool,
            service.redis_queue,
        ).authorized_download_count(source_names=source_names)
    else:
        with sqlite3.connect(service.catalog_path, timeout=30) as conn:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_xinfo(books)")
            }
            if "source_kind" in columns:
                placeholders = ",".join("?" for _ in source_names)
                source_condition = f"source_kind IN ({placeholders})"
                source_params: tuple[Any, ...] = tuple(source_names)
            else:
                source_condition = "(" + " OR ".join(
                    "source_id LIKE ?" for _ in source_names
                ) + ")"
                source_params = tuple(f"{name}-%" for name in source_names)
            queued = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM books
                    WHERE status IN ('discovered', 'failed')
                      AND attempts < 4
                      AND {source_condition}
                    """,
                    source_params,
                ).fetchone()[0]
            )
    return {
        "status": "completed",
        "attempted_now": attempted_now,
        "imported_now": imported_now,
        "download_sources": download_runs,
        "ingestion_index": index_refresh,
        "queued": queued,
        "stats": state["stats"],
        "cursors": {
            name: {
                "category": int(state[name]["category"]),
                "page": int(state[name]["page"]),
                "complete": bool(state[name].get("complete")),
                "last_run": state[name].get("last_run") or {},
            }
            for name, _, _ in sources
        },
        "state_path": str(STATE_PATH),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--download-limit",
        type=int,
        default=100,
        help="每个来源每轮最多处理的正文数；来源之间严格串行",
    )
    parser.add_argument("--xbiquge-workers", type=int, default=8)
    parser.add_argument("--ixdzs-workers", type=int, default=8)
    parser.add_argument("--shubaow-workers", type=int, default=5)
    parser.add_argument("--linovelib-workers", type=int, default=5)
    parser.add_argument(
        "--backfill-categories",
        action="store_true",
        help=(
            "仅重扫已发现的授权目录页并修复待下载书目的分类；"
            "使用独立断点，不回退正常发现游标，也不下载正文"
        ),
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=25,
        help="每个来源本轮最多扫描的目录页数",
    )
    parser.add_argument(
        "--sources",
        default=os.getenv(
            "WEBNOVEL_AUTHORIZED_CATALOG_SOURCES",
            ",".join(DEFAULT_SOURCE_NAMES),
        ),
        help=(
            "逗号分隔的来源白名单；生产默认按 "
            "xbiquge → ixdzs → shubaow → linovelib 严格串行"
        ),
    )
    args = parser.parse_args()
    try:
        source_names = parse_source_names(args.sources)
    except ValueError as exc:
        parser.error(str(exc))
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                json.dumps(
                    {
                        "status": "already_running",
                        "message": "授权站点目录扫描已在运行，本轮不重复排队",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        page_limit = max(0, min(int(args.page_limit), 1000))
        if args.backfill_categories:
            result = asyncio.run(
                run_category_backfill(page_limit, source_names)
            )
        else:
            result = asyncio.run(
                run(
                    max(0, min(int(args.download_limit), 200)),
                    page_limit,
                    source_names,
                    {
                        "xbiquge": args.xbiquge_workers,
                        "ixdzs": args.ixdzs_workers,
                        "shubaow": args.shubaow_workers,
                        "linovelib": args.linovelib_workers,
                    },
                )
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
