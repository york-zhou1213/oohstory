#!/usr/bin/env python3
"""Consume authorized-site body downloads independently from catalog discovery.

The catalog scanner is intentionally metadata-only.  This worker owns a
separate lease/lock and processes a bounded, source-balanced batch so a slow
book cannot block discovery or the other source indefinitely.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import inspect
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


from project_paths import APP_ROOT  # noqa: E402
BACKEND_ROOT = APP_ROOT / "src"
LIBRARY_ROOT = APP_ROOT / "electronic-library" / "txt80"
STATE_PATH = (
    LIBRARY_ROOT / "全局索引" / "authorized-site-download-worker.json"
)
LOCK_PATH = STATE_PATH.with_suffix(".lock")
POSTPROCESS_DB_PATH = (
    LIBRARY_ROOT / "全局索引" / "authorized-postprocess.sqlite3"
)
sys.path.insert(0, str(BACKEND_ROOT))

from oohstory_library.services.ai_service import get_ai_service  # noqa: E402
from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402
from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
    RedisQueueClient,
)
from oohstory_library.services.library_download_queue import LibraryDownloadQueue  # noqa: E402


def with_sqlite_write_retry(operation, *, attempts: int = 8):
    """Retry short SQLite writer collisions without dropping a timer run."""
    for retry in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or retry + 1 >= attempts:
                raise
            time.sleep(min(0.25 * (2**retry), 5.0))
    raise RuntimeError("SQLite write retry exhausted")


def atomic_state(payload: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, STATE_PATH)


def disk_free_gb() -> float:
    return shutil.disk_usage(LIBRARY_ROOT).free / (1024**3)


def reset_abandoned_claims(service: ElectronicLibraryService) -> int:
    """A held process lock proves every previous ``downloading`` lease is stale."""
    def operation() -> int:
        with sqlite3.connect(service.catalog_path, timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            before = conn.total_changes
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_xinfo(books)")
            }
            source_condition = (
                "source_kind IN ('xbiquge', 'ixdzs', 'shubaow')"
                if "source_kind" in columns
                else (
                    "(source_id LIKE 'xbiquge-%' "
                    "OR source_id LIKE 'ixdzs-%' "
                    "OR source_id LIKE 'shubaow-%')"
                )
            )
            retry_assignment = (
                ", next_retry_at=datetime('now'), download_claimed_at=NULL"
                if {"next_retry_at", "download_claimed_at"}.issubset(columns)
                else ""
            )
            conn.execute(
                f"""
                UPDATE books
                SET status='failed',
                    last_error=COALESCE(
                      last_error, '下载进程中断，已自动重新排队'
                    ),
                    updated_at=datetime('now')
                    {retry_assignment}
                WHERE status='downloading'
                  AND {source_condition}
                """
            )
            conn.commit()
            return conn.total_changes - before

    return int(with_sqlite_write_retry(operation))


def _source_rows(
    conn: sqlite3.Connection,
    source: str,
    limit: int,
) -> list[sqlite3.Row]:
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_xinfo(books)")
    }
    if "source_kind" in columns:
        source_condition = "source_kind=?"
        source_parameter = source
    else:
        source_condition = "source_id LIKE ?"
        source_parameter = f"{source}-%"
    retry_condition = (
        "AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))"
        if "next_retry_at" in columns
        else ""
    )
    return conn.execute(
        f"""
        SELECT id, source_id, detail_url, title, author, category, book_status
        FROM books
        WHERE status IN ('discovered', 'failed')
          AND attempts < 4
          AND {source_condition}
          {retry_condition}
        ORDER BY
          CASE status WHEN 'discovered' THEN 0 ELSE 1 END,
          attempts,
          id
        LIMIT ?
        """,
        (source_parameter, int(limit)),
    ).fetchall()


def claim_batch(
    service: ElectronicLibraryService,
    limit: int,
    source_weights: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Claim a fair batch so one large source cannot starve the other."""
    if limit <= 0:
        return []
    weights = {
        "xbiquge": max(int((source_weights or {}).get("xbiquge", 1)), 1),
        "ixdzs": max(int((source_weights or {}).get("ixdzs", 1)), 1),
    }
    weight_total = sum(weights.values())
    quotas = {
        source: max(1, int(limit * weight / weight_total))
        for source, weight in weights.items()
    }
    while sum(quotas.values()) > limit:
        source = max(quotas, key=lambda key: (quotas[key], weights[key]))
        if quotas[source] <= 1:
            break
        quotas[source] -= 1
    while sum(quotas.values()) < limit:
        source = max(
            quotas,
            key=lambda key: weights[key] / max(quotas[key], 1),
        )
        quotas[source] += 1

    def operation() -> list[dict[str, Any]]:
        with sqlite3.connect(service.catalog_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("BEGIN IMMEDIATE")
            grouped = {
                source: _source_rows(conn, source, quotas[source])
                for source in ("xbiquge", "ixdzs")
            }
            selected = [
                row
                for source in ("xbiquge", "ixdzs")
                for row in grouped[source]
            ][:limit]
            if len(selected) < limit:
                selected_ids = {int(row["id"]) for row in selected}
                for source in ("ixdzs", "xbiquge"):
                    for row in _source_rows(conn, source, limit):
                        if int(row["id"]) in selected_ids:
                            continue
                        selected.append(row)
                        selected_ids.add(int(row["id"]))
                        if len(selected) >= limit:
                            break
                    if len(selected) >= limit:
                        break
            if not selected:
                conn.rollback()
                return []
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_xinfo(books)")
            }
            claim_assignment = (
                ", download_claimed_at=datetime('now')"
                if "download_claimed_at" in columns
                else ""
            )
            conn.executemany(
                f"""
                UPDATE books
                SET status='downloading', last_error=NULL,
                    updated_at=datetime('now')
                    {claim_assignment}
                WHERE id=? AND status IN ('discovered', 'failed')
                """,
                [(int(row["id"]),) for row in selected],
            )
            conn.commit()
        return [dict(row) for row in selected]

    return list(with_sqlite_write_retry(operation))


def update_failure(
    service: ElectronicLibraryService,
    catalog_id: int,
    error: str,
) -> None:
    def operation() -> None:
        with sqlite3.connect(service.catalog_path, timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_xinfo(books)")
            }
            row = conn.execute(
                "SELECT attempts FROM books WHERE id=?",
                (int(catalog_id),),
            ).fetchone()
            next_attempt = int(row[0] if row else 0) + 1
            delay_minutes = min(5 * (2 ** max(next_attempt - 1, 0)), 360)
            retry_at = (
                datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
            ).strftime("%Y-%m-%d %H:%M:%S")
            retry_assignment = (
                ", next_retry_at=?, download_claimed_at=NULL"
                if {"next_retry_at", "download_claimed_at"}.issubset(columns)
                else ""
            )
            params: list[Any] = [error[:1000]]
            if retry_assignment:
                params.append(retry_at)
            params.append(int(catalog_id))
            conn.execute(
                f"""
                UPDATE books
                SET status='failed', attempts=attempts+1,
                    last_error=?, updated_at=datetime('now')
                    {retry_assignment}
                WHERE id=?
                """,
                params,
            )
            conn.commit()

    with_sqlite_write_retry(operation)


def update_duplicate(
    service: ElectronicLibraryService,
    catalog_id: int,
) -> None:
    with sqlite3.connect(service.catalog_path, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_xinfo(books)")
        }
        clear_claim = (
            ", next_retry_at=NULL, download_claimed_at=NULL"
            if {"next_retry_at", "download_claimed_at"}.issubset(columns)
            else ""
        )
        conn.execute(
            f"""
            UPDATE books
            SET status='duplicate', last_error=NULL, updated_at=datetime('now')
                {clear_claim}
            WHERE id=?
            """,
            (int(catalog_id),),
        )
        conn.commit()


async def import_one(
    service: ElectronicLibraryService,
    ai_service: Any,
    item: dict[str, Any],
) -> dict[str, Any]:
    source_id = str(item.get("source_id") or "")
    if source_id.startswith("xbiquge-"):
        provider = service.xbiquge_provider.PROVIDER_ID
        remote_id = source_id.removeprefix("xbiquge-")
    elif source_id.startswith("ixdzs-"):
        provider = service.ixdzs_provider.PROVIDER_ID
        remote_id = source_id.removeprefix("ixdzs-")
    else:
        raise ValueError("待下载记录不属于授权来源")
    kwargs: dict[str, Any] = {
        "provider": provider,
        "remote_id": remote_id,
        "source_ref": urlparse(str(item.get("detail_url") or "")).path,
        "book_status": str(item.get("book_status") or ""),
        "ai_service": ai_service,
    }
    parameters = inspect.signature(service.import_public_book).parameters
    if "category_hint" in parameters:
        kwargs["category_hint"] = str(item.get("category") or "")
    if "defer_postprocess" in parameters:
        kwargs["defer_postprocess"] = True
    started = time.monotonic()
    result = await service.import_public_book(**kwargs)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def enqueue_deferred_postprocess(catalog_ids: list[int]) -> dict[str, Any]:
    if not catalog_ids:
        return {"status": "not_required", "catalog_ids": []}
    POSTPROCESS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(POSTPROCESS_DB_PATH, timeout=30) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              catalog_id INTEGER PRIMARY KEY,
              status TEXT NOT NULL DEFAULT 'pending',
              attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_authorized_postprocess_queue
            ON jobs(status, attempts, catalog_id)
            """
        )
        conn.executemany(
            """
            INSERT INTO jobs(catalog_id, status, attempts, updated_at)
            VALUES (?, 'pending', 0, datetime('now'))
            ON CONFLICT(catalog_id) DO UPDATE SET
              status=CASE
                WHEN jobs.status='done' THEN jobs.status
                ELSE 'pending'
              END,
              updated_at=datetime('now')
            """,
            [(catalog_id,) for catalog_id in sorted(set(catalog_ids))],
        )
        conn.commit()
    return {
        "status": "queued",
        "catalog_ids": sorted(set(catalog_ids)),
    }


async def run_batch(
    *,
    limit: int,
    xbiquge_concurrency: int,
    ixdzs_concurrency: int,
    min_free_gb: float,
) -> dict[str, Any]:
    settings = LibraryInfrastructureSettings.from_env()
    if settings.catalog_backend == "mysql":
        # Preserve this historical oneshot entry point while routing mysql
        # production through the durable MySQL job table and Redis Stream.
        # No legacy catalog/postprocess SQLite file is opened in this branch.
        from authorized_site_stream_worker import StreamWorker

        mysql = MySQLConnectionPool(settings)
        redis_queue = RedisQueueClient(settings)
        dispatched = await asyncio.to_thread(
            LibraryDownloadQueue(mysql, redis_queue).dispatch,
            limit=limit,
        )
        worker = StreamWorker(
            concurrency=xbiquge_concurrency + ixdzs_concurrency,
            min_local_free_gb=min_free_gb,
            min_nas_free_gb=max(
                float(os.getenv("WEBNOVEL_MIN_NAS_FREE_GB", "100")),
                1,
            ),
            idle_exit_seconds=15,
        )
        result = await worker.run()
        result["dispatch"] = dispatched
        return result
    service = ElectronicLibraryService()
    recovered = reset_abandoned_claims(service)
    free_before = disk_free_gb()
    if free_before < min_free_gb:
        return {
            "status": "paused_low_disk",
            "free_gb": round(free_before, 2),
            "min_free_gb": min_free_gb,
            "recovered_claims": recovered,
        }
    source_concurrency = {
        "xbiquge": max(1, xbiquge_concurrency),
        "ixdzs": max(1, ixdzs_concurrency),
    }
    items = claim_batch(service, limit, source_concurrency)
    if not items:
        return {
            "status": "queue_empty",
            "claimed": 0,
            "recovered_claims": recovered,
            "free_gb": round(free_before, 2),
        }

    ai_service = get_ai_service()
    semaphores = {
        source: asyncio.Semaphore(value)
        for source, value in source_concurrency.items()
    }
    state: dict[str, Any] = {
        "status": "running",
        "pid": os.getpid(),
        "claimed": len(items),
        "completed": 0,
        "imported": 0,
        "duplicates": 0,
        "failed": 0,
        "recovered_claims": recovered,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "free_gb_before": round(free_before, 2),
        "source_concurrency": source_concurrency,
        "sources": {
            "xbiquge": {"completed": 0, "imported": 0, "failed": 0},
            "ixdzs": {"completed": 0, "imported": 0, "failed": 0},
        },
        "recent": [],
    }
    atomic_state(state)

    async def guarded(
        item: dict[str, Any],
    ) -> tuple[dict[str, Any], Any, Exception | None]:
        source = (
            "xbiquge"
            if str(item.get("source_id") or "").startswith("xbiquge-")
            else "ixdzs"
        )
        async with semaphores[source]:
            try:
                return item, await import_one(service, ai_service, item), None
            except Exception as exc:
                return item, None, exc

    tasks = [asyncio.create_task(guarded(item)) for item in items]
    imported_catalog_ids: list[int] = []
    deferred_catalog_ids: list[int] = []
    for task in asyncio.as_completed(tasks):
        item, result, error = await task
        source = (
            "xbiquge"
            if str(item.get("source_id") or "").startswith("xbiquge-")
            else "ixdzs"
        )
        if error is None:
            source_catalog_id = int(item["id"])
            result_catalog_id = int(result.get("catalog_id") or 0)
            if (
                result.get("status") == "already_available"
                and result_catalog_id
                and result_catalog_id != source_catalog_id
            ):
                update_duplicate(service, source_catalog_id)
                state["duplicates"] += 1
                outcome = "duplicate"
            else:
                state["imported"] += 1
                state["sources"][source]["imported"] += 1
                imported_catalog_ids.append(result_catalog_id or source_catalog_id)
                if result.get("postprocess_deferred"):
                    deferred_catalog_ids.append(
                        result_catalog_id or source_catalog_id
                    )
                outcome = str(result.get("status") or "downloaded")
            recent = {
                "catalog_id": source_catalog_id,
                "source_id": item.get("source_id"),
                "title": item.get("title"),
                "outcome": outcome,
                "seconds": result.get("elapsed_seconds"),
            }
        else:
            catalog_id = int(item.get("id") or 0)
            if catalog_id:
                update_failure(
                    service,
                    catalog_id,
                    f"{type(error).__name__}: {error}",
                )
            state["failed"] += 1
            state["sources"][source]["failed"] += 1
            recent = {
                "catalog_id": catalog_id,
                "source_id": item.get("source_id"),
                "title": item.get("title"),
                "outcome": "failed",
                "error": f"{type(error).__name__}: {str(error)[:500]}",
            }
        state["completed"] += 1
        state["sources"][source]["completed"] += 1
        state["recent"] = [recent, *state["recent"][:19]]
        atomic_state(state)

    state["postprocess"] = await asyncio.to_thread(
        service.start_queued_ingestion_index_refresh,
        wait=True,
    )
    elapsed = max(
        time.time()
        - time.mktime(time.strptime(state["started_at"], "%Y-%m-%d %H:%M:%S")),
        0.001,
    )
    state.update(
        {
            "status": "completed",
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": round(elapsed, 3),
            "books_per_minute": round(state["completed"] * 60 / elapsed, 3),
            "free_gb_after": round(disk_free_gb(), 2),
            "imported_catalog_ids": sorted(set(imported_catalog_ids)),
        }
    )
    atomic_state(state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=0,
        help="兼容旧配置；非零时覆盖两个来源的并发数",
    )
    parser.add_argument("--xbiquge-concurrency", type=int, default=2)
    parser.add_argument("--ixdzs-concurrency", type=int, default=4)
    parser.add_argument("--min-free-gb", type=float, default=50)
    args = parser.parse_args()
    limit = min(max(int(args.limit), 1), 100)
    legacy_concurrency = max(int(args.concurrency), 0)
    xbiquge_concurrency = min(
        max(
            legacy_concurrency
            or int(args.xbiquge_concurrency),
            1,
        ),
        4,
    )
    ixdzs_concurrency = min(
        max(
            legacy_concurrency
            or int(args.ixdzs_concurrency),
            1,
        ),
        8,
    )
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                json.dumps(
                    {"status": "already_running"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        result = asyncio.run(
            run_batch(
                limit=limit,
                xbiquge_concurrency=xbiquge_concurrency,
                ixdzs_concurrency=ixdzs_concurrency,
                min_free_gb=max(float(args.min_free_gb), 20),
            )
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
