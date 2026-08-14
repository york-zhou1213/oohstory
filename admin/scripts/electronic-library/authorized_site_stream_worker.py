#!/usr/bin/env python3
"""Consume authorized downloads from Redis Streams with MySQL leases."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


from project_paths import APP_ROOT  # noqa: E402
BACKEND_ROOT = APP_ROOT / "src"
sys.path.insert(0, str(BACKEND_ROOT))

from oohstory_library.services.ai_service import get_ai_service  # noqa: E402
from oohstory_library.services.authorized_source_recovery import (  # noqa: E402
    find_exact_fallback_candidates,
    is_permanent_source_failure,
)
from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402
from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
    RedisQueueClient,
)
from oohstory_library.services.library_download_queue import LibraryDownloadQueue  # noqa: E402
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402


STATE_PATH = (
    APP_ROOT
    / "electronic-library"
    / "全局索引"
    / "authorized-site-stream-worker.json"
)


def reclaim_pending_messages(
    client: Any,
    *,
    stream: str,
    group: str,
    consumer: str,
    count: int,
    min_idle_ms: int,
) -> list[tuple[str, dict[str, str]]]:
    """Claim old entries left assigned to consumers that no longer exist."""

    result = client.xautoclaim(
        stream,
        group,
        consumer,
        min_idle_time=max(int(min_idle_ms), 60_000),
        start_id="0-0",
        count=max(int(count), 1),
    )
    entries = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
    return [(str(message_id), dict(fields)) for message_id, fields in entries]


def atomic_state(payload: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, STATE_PATH)


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024**3)


def is_transient_mysql_error(exc: BaseException) -> bool:
    code = exc.args[0] if getattr(exc, "args", ()) else None
    return code in {1205, 1213, 2006, 2013}


async def mysql_call_with_retry(
    function: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Retry short durable-state writes after lock or connection transients."""

    for attempt in range(3):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except Exception as exc:
            if not is_transient_mysql_error(exc) or attempt >= 2:
                raise
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable MySQL retry state")


async def import_job(
    service: ElectronicLibraryService,
    ai_service: Any,
    job: dict[str, Any],
) -> dict[str, Any]:
    source_id = str(job["source_id"])
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    source_recovery = payload.get("update_mode") == "source_recovery"
    if source_id.startswith("xbiquge-"):
        provider = service.xbiquge_provider.PROVIDER_ID
        remote_id = source_id.removeprefix("xbiquge-")
    elif source_id.startswith("ixdzs-"):
        provider = service.ixdzs_provider.PROVIDER_ID
        remote_id = source_id.removeprefix("ixdzs-")
    elif source_id.startswith("shubaow-"):
        provider = service.shubaow_provider.PROVIDER_ID
        remote_id = source_id.removeprefix("shubaow-")
    elif source_id.startswith("linovelib-"):
        provider = service.linovelib_provider.PROVIDER_ID
        remote_id = source_id.removeprefix("linovelib-")
    elif source_id.isdigit():
        provider = service.txt80_provider.PROVIDER_ID
        remote_id = source_id
    else:
        raise ValueError("待下载任务不属于授权来源")
    started = time.monotonic()
    refresh_catalog_id = (
        int(job["catalog_id"])
        if payload.get("update_mode") in {"serialized_refresh", "source_recovery"}
        else 0
    )
    import_kwargs = {
        "provider": provider,
        "remote_id": remote_id,
        "source_ref": (
            str(payload.get("source_ref") or "")
            if refresh_catalog_id
            else urlparse(str(job.get("detail_url") or "")).path
        ),
        "book_status": str(job.get("book_status") or ""),
        "category_hint": str(job.get("category") or ""),
        "defer_postprocess": True,
        "refresh_existing_catalog_id": refresh_catalog_id,
        "ai_service": ai_service,
    }
    if source_recovery:
        import_kwargs["rebind_existing_source"] = True
    if provider in {
        service.xbiquge_provider.PROVIDER_ID,
        service.shubaow_provider.PROVIDER_ID,
    } and payload.get(
        "remote_latest_chapter"
    ):
        import_kwargs["expected_latest_chapter"] = str(
            payload["remote_latest_chapter"]
        )
    result = await service.import_public_book(**import_kwargs)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def apply_source_branding(catalog_id: int, source_name: str) -> dict[str, Any]:
    """Run the canonical, MySQL-aware normalizer for one imported book."""

    if source_name not in {"ixdzs", "shubaow"}:
        raise ValueError("当前来源没有正文清洗规则")

    tool = APP_ROOT / "scripts" / "electronic-library" / "replace_ixdzs_branding.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(tool),
            "--apply",
            "--update-mysql",
            "--catalog-id",
            str(int(catalog_id)),
            "--profile",
            source_name,
        ],
        cwd=APP_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "品牌替换失败").strip()
        raise RuntimeError(f"replace_ixdzs_branding.py: {detail[-1200:]}")
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {"status": "completed", "output": completed.stdout[-500:]}


class StreamWorker:
    def __init__(
        self,
        *,
        concurrency: int,
        min_local_free_gb: float,
        min_nas_free_gb: float,
        idle_exit_seconds: int,
    ):
        self.settings = LibraryInfrastructureSettings.from_env()
        if self.settings.catalog_backend not in {"shadow", "mysql"}:
            raise RuntimeError(
                "Redis stream worker requires shadow or mysql catalog mode"
            )
        self.mysql = MySQLConnectionPool(self.settings)
        self.redis = RedisQueueClient(self.settings)
        self.queue = LibraryDownloadQueue(self.mysql, self.redis)
        self.runtime = MySQLLibraryRuntime(
            self.settings,
            self.mysql,
            self.redis,
        )
        self.service = ElectronicLibraryService()
        self.ai_service = get_ai_service()
        self.concurrency = min(max(int(concurrency), 1), 16)
        self.min_local_free_gb = max(float(min_local_free_gb), 1)
        self.min_nas_free_gb = max(float(min_nas_free_gb), 1)
        # ``0`` keeps an enabled Redis consumer resident.  Positive values
        # retain the bounded one-shot mode used by ad-hoc maintenance runs.
        self.idle_exit_seconds = max(int(idle_exit_seconds), 0)
        self.consumer = (
            f"{socket.gethostname()}-{os.getpid()}-{int(time.time())}"
        )
        self.stop_requested = False
        self.state: dict[str, Any] = {
            "status": "starting",
            "pid": os.getpid(),
            "consumer": self.consumer,
            "concurrency": self.concurrency,
            "completed": 0,
            "imported": 0,
            "duplicates": 0,
            "failed": 0,
            "source_switched": 0,
            "recent": [],
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def request_stop(self, *_: Any) -> None:
        self.stop_requested = True

    def storage_ready(self) -> tuple[bool, dict[str, float]]:
        values = {
            "local_free_gb": round(
                free_gb(self.service.library_root), 2
            ),
            "nas_free_gb": round(
                free_gb(self.settings.object_root), 2
            ),
        }
        return (
            values["local_free_gb"] >= self.min_local_free_gb
            and values["nas_free_gb"] >= self.min_nas_free_gb,
            values,
        )

    async def process_message(
        self,
        message_id: str,
        fields: dict[str, str],
    ) -> None:
        job_id = int(fields["job_id"])
        job = await asyncio.to_thread(
            self.queue.claim,
            job_id=job_id,
            worker_id=self.consumer,
        )
        if not job:
            self.redis.client.xack(
                self.redis.key(self.redis.DOWNLOAD_STREAM),
                self.redis.DOWNLOAD_GROUP,
                message_id,
            )
            return
        source_name = str(job["source_name"])
        payload = (
            job.get("payload")
            if isinstance(job.get("payload"), dict)
            else {}
        )
        serialized_refresh = (
            payload.get("update_mode") == "serialized_refresh"
        )
        source_limit = (
            max(
                1,
                int(
                    os.getenv(
                        "WEBNOVEL_XBIQUGE_BOOK_CONCURRENCY", "3"
                    )
                ),
            )
            if source_name == "xbiquge"
            else (
                max(
                    1,
                    int(os.getenv("WEBNOVEL_SHUBAOW_BOOK_CONCURRENCY", "1")),
                )
                if source_name == "shubaow"
                else (
                max(
                    1,
                    int(os.getenv("WEBNOVEL_LINOVELIB_BOOK_CONCURRENCY", "2")),
                )
                if source_name == "linovelib"
                else (
                max(
                1,
                int(os.getenv("WEBNOVEL_IXDZS_BOOK_CONCURRENCY", "4")),
                )
                if source_name == "ixdzs"
                else max(
                    1,
                    int(os.getenv("WEBNOVEL_TXT80_DOWNLOAD_WORKERS", "4")),
                )
                )
                )
            )
        )
        # Keep one reserved lane for changed local serials. This prevents a
        # deep first-import backlog from starving daily refreshes, while the
        # normal download concurrency remains unchanged.
        if serialized_refresh:
            source_limit += 1
        slot = await asyncio.to_thread(
            self.queue.acquire_source_slot,
            source_name=source_name,
            limit=source_limit,
        )
        if not slot:
            await asyncio.to_thread(
                self.queue.defer,
                job_id=job_id,
                lease_token=str(job["lease_token"]),
                reason="来源并发槽暂不可用，稍后重试",
                delay_seconds=5,
            )
            outcome = "deferred"
            detail: dict[str, Any] = {}
        else:
            try:
                detail = await import_job(
                    self.service,
                    self.ai_service,
                    job,
                )
                # This daemon can stay alive indefinitely, so there is no
                # reliable "end of batch" hook.  Finish the exact-id,
                # plot-free visibility index before accepting the next body.
                # The production stream worker is single-concurrency and this
                # keeps download, ClamAV and index I/O from overlapping.
                detail["ingestion_index"] = await asyncio.to_thread(
                    self.service.start_queued_ingestion_index_refresh,
                    wait=True,
                )
                outcome = (
                    "duplicate"
                    if detail.get("status") == "already_available"
                    and int(detail.get("catalog_id") or 0)
                    != int(job["catalog_id"])
                    else "imported"
                )
                if serialized_refresh and source_name == "ixdzs":
                    detail["branding"] = await asyncio.to_thread(
                        apply_source_branding,
                        int(detail.get("catalog_id") or job["catalog_id"]),
                        source_name,
                    )
                if outcome == "duplicate":
                    await asyncio.to_thread(
                        self.queue.complete_duplicate,
                        job_id=job_id,
                        lease_token=str(job["lease_token"]),
                        catalog_id=int(job["catalog_id"]),
                        kept_catalog_id=int(detail["catalog_id"]),
                    )
                if serialized_refresh and outcome != "duplicate":
                    await asyncio.to_thread(
                        self.queue.mark_serialized_refresh_applied,
                        source_name=source_name,
                        source_id=str(job["source_id"]),
                        catalog_id=int(
                            detail.get("catalog_id") or job["catalog_id"]
                        ),
                        remote_revision=str(
                            payload.get("remote_revision") or ""
                        ),
                        local_latest_chapter=str(
                            payload.get("remote_latest_chapter") or ""
                        ),
                    )
                # The service's MySQL mirror marks the durable job complete.
                self.state[
                    "duplicates" if outcome == "duplicate" else "imported"
                ] += 1
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:1800]}"
                switched: dict[str, Any] | None = None
                fallback_errors: list[str] = []
                if (
                    not serialized_refresh
                    and is_permanent_source_failure(
                        error,
                        attempts=int(job.get("attempts") or 0),
                        max_attempts=int(job.get("max_attempts") or 1),
                    )
                ):
                    recovery = payload.get("source_recovery")
                    if not isinstance(recovery, dict):
                        recovery = {}
                    history = recovery.get("history")
                    if not isinstance(history, list):
                        history = []
                    excluded_source_ids = {
                        str(job.get("source_id") or ""),
                        str(recovery.get("original_source_id") or ""),
                        *{
                            str(item.get("source_id") or "")
                            for item in history
                            if isinstance(item, dict)
                        },
                    } - {""}
                    candidates, search_errors = await asyncio.to_thread(
                        find_exact_fallback_candidates,
                        self.service,
                        title=str(job.get("title") or ""),
                        author=str(job.get("author") or ""),
                        current_source_name=source_name,
                        excluded_source_ids=excluded_source_ids,
                    )
                    fallback_errors.extend(search_errors)
                    for candidate in candidates:
                        try:
                            switched = await mysql_call_with_retry(
                                self.queue.schedule_source_recovery,
                                catalog_id=int(job["catalog_id"]),
                                candidate=candidate,
                                reason=error,
                                job_id=job_id,
                                lease_token=str(job["lease_token"]),
                            )
                            switched["candidate"] = candidate
                            break
                        except Exception as fallback_exc:
                            fallback_errors.append(
                                f"{candidate.get('source_name')}: "
                                f"{type(fallback_exc).__name__}: "
                                f"{str(fallback_exc)[:300]}"
                            )
                if switched:
                    outcome = "source_switched"
                    detail = {
                        "error": error[:500],
                        "switched_to": switched.get("source_id"),
                        "switched_source_name": switched.get("source_name"),
                        "fallback_errors": fallback_errors[-8:],
                    }
                    self.state["source_switched"] += 1
                else:
                    await mysql_call_with_retry(
                        self.queue.fail,
                        job_id=job_id,
                        lease_token=str(job["lease_token"]),
                        error=error,
                    )
                    outcome = "failed"
                    detail = {
                        "error": error[:500],
                        "fallback_errors": fallback_errors[-8:],
                    }
                    self.state["failed"] += 1
            finally:
                await mysql_call_with_retry(
                    self.queue.release_source_slot,
                    source_name,
                    slot,
                )
        self.redis.client.xack(
            self.redis.key(self.redis.DOWNLOAD_STREAM),
            self.redis.DOWNLOAD_GROUP,
            message_id,
        )
        self.redis.client.xdel(
            self.redis.key(self.redis.DOWNLOAD_STREAM),
            message_id,
        )
        self.state["completed"] += 1
        recent = {
            "job_id": job_id,
            "catalog_id": int(job["catalog_id"]),
            "source_id": job["source_id"],
            "title": job.get("title"),
            "outcome": outcome,
            "seconds": detail.get("elapsed_seconds"),
            "error": detail.get("error"),
            "switched_to": detail.get("switched_to"),
        }
        self.state["recent"] = [recent, *self.state["recent"][:19]]
        self.state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        atomic_state(self.state)

    async def run(self) -> dict[str, Any]:
        self.redis.ensure_groups()
        recovered = await asyncio.to_thread(self.queue.recover_stale)
        self.state.update({"status": "running", "recovered": recovered})
        atomic_state(self.state)
        stream = self.redis.key(self.redis.DOWNLOAD_STREAM)
        semaphore = asyncio.Semaphore(self.concurrency)
        running: set[asyncio.Task[None]] = set()
        idle_since = time.monotonic()
        last_reclaim_at = 0.0
        reclaim_idle_ms = max(
            int(
                os.getenv(
                    "WEBNOVEL_DOWNLOAD_PENDING_RECLAIM_MS",
                    str(2 * 60 * 60 * 1000),
                )
            ),
            60_000,
        )

        async def guarded(
            message_id: str,
            fields: dict[str, str],
        ) -> None:
            async with semaphore:
                try:
                    await self.process_message(message_id, fields)
                except Exception as exc:
                    self.state["last_worker_error"] = (
                        f"{type(exc).__name__}: {str(exc)[:500]}"
                    )
                    self.state["updated_at"] = time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    atomic_state(self.state)

        while not self.stop_requested:
            ready, storage = self.storage_ready()
            self.state.update(storage)
            if not ready:
                self.state.update(
                    {
                        "status": "paused_low_storage",
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                atomic_state(self.state)
                break
            reclaimed: list[tuple[str, dict[str, str]]] = []
            current = time.monotonic()
            if current - last_reclaim_at >= 300:
                reclaimed = await asyncio.to_thread(
                    reclaim_pending_messages,
                    self.redis.client,
                    stream=stream,
                    group=self.redis.DOWNLOAD_GROUP,
                    consumer=self.consumer,
                    count=max(self.concurrency * 2, 10),
                    min_idle_ms=reclaim_idle_ms,
                )
                # Keep draining immediately while old pending entries exist;
                # when the batch is empty, probe again in five minutes.
                if not reclaimed:
                    last_reclaim_at = current
            messages = (
                [(stream, reclaimed)]
                if reclaimed
                else await asyncio.to_thread(
                    self.redis.client.xreadgroup,
                    self.redis.DOWNLOAD_GROUP,
                    self.consumer,
                    {stream: ">"},
                    count=max(self.concurrency * 2, 10),
                    block=3000,
                )
            )
            if not messages:
                if (
                    not running
                    and self.idle_exit_seconds > 0
                    and time.monotonic() - idle_since
                    >= self.idle_exit_seconds
                ):
                    self.state["status"] = "idle"
                    break
                continue
            idle_since = time.monotonic()
            for _, entries in messages:
                for message_id, fields in entries:
                    task = asyncio.create_task(
                        guarded(str(message_id), dict(fields))
                    )
                    running.add(task)
                    task.add_done_callback(running.discard)
            if len(running) >= self.concurrency * 3:
                await asyncio.wait(
                    running,
                    return_when=asyncio.FIRST_COMPLETED,
                )
        if running:
            await asyncio.gather(*running)
        self.state.update(
            {
                "status": (
                    "stopped"
                    if self.stop_requested
                    else self.state.get("status", "idle")
                ),
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        atomic_state(self.state)
        return self.state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--min-local-free-gb", type=float, default=40)
    parser.add_argument("--min-nas-free-gb", type=float, default=100)
    parser.add_argument(
        "--idle-exit-seconds",
        type=int,
        default=60,
        help="空闲退出秒数；设为 0 时作为常驻 Redis 消费者运行",
    )
    args = parser.parse_args()
    worker = StreamWorker(
        concurrency=args.concurrency,
        min_local_free_gb=args.min_local_free_gb,
        min_nas_free_gb=args.min_nas_free_gb,
        idle_exit_seconds=args.idle_exit_seconds,
    )
    signal.signal(signal.SIGTERM, worker.request_stop)
    signal.signal(signal.SIGINT, worker.request_stop)
    result = asyncio.run(worker.run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"idle", "stopped"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
