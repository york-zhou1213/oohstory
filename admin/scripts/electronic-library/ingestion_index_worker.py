#!/usr/bin/env python3
"""Targeted, plot-free index worker for newly ingested books."""

from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from project_paths import APP_ROOT


sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402


DEFAULT_RUNTIME_DIR = APP_ROOT / "electronic-library" / "txt80" / "全局索引"
RUNTIME_DIR = Path(
    os.getenv("WEBNOVEL_LIBRARY_RUNTIME_DIR", str(DEFAULT_RUNTIME_DIR))
).expanduser().resolve()
REQUEST_PATH = RUNTIME_DIR / "electronic_library_ingestion_index_request.json"
STATUS_PATH = RUNTIME_DIR / "electronic_library_ingestion_index_status.json"
QUEUE_LOCK_PATH = RUNTIME_DIR / ".electronic_library_ingestion_index_queue.lock"
WORKER_LOCK_PATH = RUNTIME_DIR / ".electronic_library_ingestion_index_worker.lock"
MAX_ATTEMPTS = 3


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def normalize_ids(values: Any) -> list[int]:
    result: set[int] = set()
    for value in values or []:
        try:
            catalog_id = int(value)
        except (TypeError, ValueError):
            continue
        if catalog_id > 0:
            result.add(catalog_id)
    return sorted(result)


def update_status(patch: dict[str, Any]) -> dict[str, Any]:
    current = read_json(STATUS_PATH, {})
    current.update(patch)
    current["updated_at"] = now()
    atomic_json(STATUS_PATH, current)
    return current


def claim_request() -> dict[str, Any] | None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with QUEUE_LOCK_PATH.open("a+", encoding="utf-8") as queue_lock:
        fcntl.flock(queue_lock, fcntl.LOCK_EX)
        request = read_json(REQUEST_PATH, {})
        ids = normalize_ids(request.get("catalog_ids"))
        if not request.get("pending") or not ids:
            fcntl.flock(queue_lock, fcntl.LOCK_UN)
            return None
        request["pending"] = False
        request["catalog_ids"] = ids
        request["claimed_at"] = now()
        request["claimed_by_pid"] = os.getpid()
        atomic_json(REQUEST_PATH, request)
        fcntl.flock(queue_lock, fcntl.LOCK_UN)
    return request


def requeue_ids(catalog_ids: Any, reason: str) -> None:
    ids = normalize_ids(catalog_ids)
    if not ids:
        return
    with QUEUE_LOCK_PATH.open("a+", encoding="utf-8") as queue_lock:
        fcntl.flock(queue_lock, fcntl.LOCK_EX)
        current = read_json(REQUEST_PATH, {})
        pending_ids = (
            normalize_ids(current.get("catalog_ids"))
            if current.get("pending")
            else []
        )
        request = {
            "schema_version": 1,
            "revision": int(current.get("revision") or 0) + 1,
            "pending": True,
            "catalog_ids": normalize_ids([*pending_ids, *ids]),
            "reason": str(reason)[:80],
            "requested_at": now(),
            "request_id": str(current.get("request_id") or "retry"),
        }
        atomic_json(REQUEST_PATH, request)
        fcntl.flock(queue_lock, fcntl.LOCK_UN)


def run_pipeline() -> int:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with WORKER_LOCK_PATH.open("a+", encoding="utf-8") as worker_lock:
        try:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        while True:
            request = claim_request()
            if request is None:
                update_status(
                    {
                        "status": "completed",
                        "running": False,
                        "stage": "completed",
                        "pid": os.getpid(),
                        "catalog_ids": [],
                        "finished_at": now(),
                        "message": "新书轻量索引队列已处理完成",
                    }
                )
                return 0

            catalog_ids = normalize_ids(request.get("catalog_ids"))
            previous = read_json(STATUS_PATH, {})
            attempts = int(previous.get("attempts") or 0) + 1
            update_status(
                {
                    "status": "running",
                    "running": True,
                    "stage": "tone_metadata",
                    "pid": os.getpid(),
                    "attempts": attempts,
                    "request_id": request.get("request_id"),
                    "request_revision": request.get("revision"),
                    "catalog_ids": catalog_ids,
                    "started_at": now(),
                    "finished_at": None,
                    "message": (
                        f"正在顺序处理 {len(catalog_ids)} 本新书轻量索引；"
                        "剧情索引未启用"
                    ),
                }
            )
            try:
                result = ElectronicLibraryService().build_ingestion_index(
                    catalog_ids
                )
                failed_ids = [
                    item.get("catalog_id")
                    for item in (result.get("failures") or [])
                ]
                if failed_ids and attempts < MAX_ATTEMPTS:
                    requeue_ids(failed_ids, "ingestion_index_retry")
                update_status(
                    {
                        "status": str(result.get("status") or "completed"),
                        "running": False,
                        "stage": "completed",
                        "pid": os.getpid(),
                        "attempts": 0 if not failed_ids else attempts,
                        "catalog_ids": catalog_ids,
                        "indexed": int(result.get("indexed") or 0),
                        "failed": int(result.get("failed") or 0),
                        "result": result,
                        "finished_at": now(),
                        "message": str(
                            result.get("message")
                            or "新书轻量索引处理完成"
                        ),
                    }
                )
            except Exception as exc:
                if attempts < MAX_ATTEMPTS:
                    requeue_ids(catalog_ids, "ingestion_index_worker_retry")
                update_status(
                    {
                        "status": (
                            "retrying" if attempts < MAX_ATTEMPTS else "error"
                        ),
                        "running": False,
                        "stage": "retrying" if attempts < MAX_ATTEMPTS else "error",
                        "pid": os.getpid(),
                        "catalog_ids": catalog_ids,
                        "finished_at": now(),
                        "message": f"{type(exc).__name__}: {str(exc)[:420]}",
                    }
                )
                return 1 if attempts < MAX_ATTEMPTS else 0


if __name__ == "__main__":
    raise SystemExit(run_pipeline())
