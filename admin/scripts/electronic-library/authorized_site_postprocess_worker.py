#!/usr/bin/env python3
"""Batch metadata/index post-processing outside the body-download hot path."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


from project_paths import APP_ROOT  # noqa: E402
LIBRARY_ROOT = APP_ROOT / "electronic-library"
RUNTIME_DIR = LIBRARY_ROOT / "全局索引"
QUEUE_PATH = RUNTIME_DIR / "authorized-postprocess.sqlite3"
LOCK_PATH = RUNTIME_DIR / "authorized-postprocess.lock"
STATE_PATH = RUNTIME_DIR / "authorized-postprocess.json"
sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402
from oohstory_library.services.library_database import LibraryInfrastructureSettings  # noqa: E402
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402


def atomic_state(payload: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(STATE_PATH)


def connect_queue() -> sqlite3.Connection:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(QUEUE_PATH, timeout=30)
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
    return conn


def queue_tone_index_refresh(catalog_ids: list[int]) -> dict[str, Any]:
    """Run the targeted visibility/tone queue; plot remains manual-only."""

    return ElectronicLibraryService().queue_ingestion_index_refresh(
        catalog_ids,
        start_worker=True,
        wait=True,
        reason="automatic_postprocess_targeted",
    )


async def sync_covers(
    catalog_ids: list[int],
    *,
    concurrency: int,
) -> dict[str, Any]:
    """Fill covers independently from the body-download critical path."""
    service = ElectronicLibraryService()
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(catalog_id: int) -> dict[str, Any]:
        async with semaphore:
            try:
                result = await service.sync_catalog_cover(
                    catalog_id,
                    action="sync",
                )
                return {
                    "catalog_id": catalog_id,
                    "ok": True,
                    "status": str(result.get("status") or "completed"),
                }
            except Exception as exc:
                return {
                    "catalog_id": catalog_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }

    items = await asyncio.gather(*(one(value) for value in catalog_ids))
    succeeded = sum(1 for item in items if item["ok"])
    return {
        "requested": len(items),
        "succeeded": succeeded,
        "failed": len(items) - succeeded,
        "items": items,
    }


def run(limit: int, cover_concurrency: int) -> dict[str, Any]:
    settings = LibraryInfrastructureSettings.from_env()
    if settings.catalog_backend == "mysql":
        return run_mysql(limit, cover_concurrency)
    with connect_queue() as conn:
        conn.execute(
            """
            UPDATE jobs SET status='pending', updated_at=datetime('now')
            WHERE status='running'
            """
        )
        rows = conn.execute(
            """
            SELECT catalog_id FROM jobs
            WHERE status IN ('pending', 'failed') AND attempts < 4
            ORDER BY attempts, catalog_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        catalog_ids = [int(row[0]) for row in rows]
        if not catalog_ids:
            return {"status": "queue_empty", "processed": 0}
        conn.executemany(
            """
            UPDATE jobs SET status='running', updated_at=datetime('now')
            WHERE catalog_id=?
            """,
            [(catalog_id,) for catalog_id in catalog_ids],
        )
        conn.commit()

    # Plot indexing stays out of this path and is only started manually.  The
    # old four-process metadata rebuild read every target book in full before
    # starting another tone pass.  The dedicated ingestion worker now creates
    # visibility metadata from bounded samples in one sequential task.
    # Covers are owned by the import transaction and the permanent cover
    # worker.  Re-downloading them here races those writers and can leave both
    # pipelines waiting on the same library_covers/books rows.
    covers = {
        "status": "handled_by_import_or_cover_worker",
        "requested": 0,
        "succeeded": 0,
        "failed": 0,
        "items": [],
    }
    succeeded = True
    error = ""
    try:
        refresh = queue_tone_index_refresh(catalog_ids)
    except Exception as exc:
        succeeded = False
        error = f"{type(exc).__name__}: {str(exc)[:1000]}"
        refresh = {"status": "error", "message": error}
    with connect_queue() as conn:
        conn.executemany(
            """
            UPDATE jobs
            SET status=?, attempts=attempts+1, last_error=?,
                updated_at=datetime('now')
            WHERE catalog_id=?
            """,
            [
                (
                    "done" if succeeded else "failed",
                    None if succeeded else error or None,
                    catalog_id,
                )
                for catalog_id in catalog_ids
            ],
        )
        conn.commit()
    return {
        "status": "completed" if succeeded else "failed",
        "processed": len(catalog_ids),
        "catalog_ids": catalog_ids,
        "metadata_returncode": 0 if succeeded else 1,
        "global_index_refresh": refresh,
        "covers": covers,
        "error": error,
    }


def run_mysql(limit: int, cover_concurrency: int) -> dict[str, Any]:
    """Run the same post-processing pipeline from durable MySQL leases."""
    runtime = MySQLLibraryRuntime()
    worker_id = runtime.worker_id("postprocess")
    claims = runtime.claim_postprocess(
        limit=limit,
        worker_id=worker_id,
        lease_seconds=max(3600, limit * 120),
    )
    catalog_ids = [int(item["catalog_id"]) for item in claims]
    if not catalog_ids:
        return {
            "status": "queue_empty",
            "processed": 0,
            "queue": runtime.postprocess_status_counts(),
        }
    covers = {
        "status": "handled_by_import_or_cover_worker",
        "requested": 0,
        "succeeded": 0,
        "failed": 0,
        "items": [],
    }
    succeeded = True
    error = ""
    try:
        refresh = queue_tone_index_refresh(catalog_ids)
    except Exception as exc:
        succeeded = False
        error = f"{type(exc).__name__}: {str(exc)[:1800]}"
        refresh = {"status": "error", "message": error}
    runtime.finish_postprocess(
        claims,
        succeeded=succeeded,
        error=error,
    )
    return {
        "status": "completed" if succeeded else "failed",
        "processed": len(catalog_ids),
        "catalog_ids": catalog_ids,
        "metadata_returncode": 0 if succeeded else 1,
        "global_index_refresh": refresh,
        "covers": covers,
        "error": error,
        "queue": runtime.postprocess_status_counts(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--cover-concurrency", type=int, default=4)
    args = parser.parse_args()
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running"}))
            return 0
        result = run(
            min(max(int(args.limit), 1), 500),
            min(max(int(args.cover_concurrency), 1), 8),
        )
        atomic_state(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
