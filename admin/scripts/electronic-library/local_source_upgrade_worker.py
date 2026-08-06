#!/usr/bin/env python3
"""Replace TXT80/TXT020 covers and refresh strictly newer exact matches."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import threading
import time

from project_paths import APP_ROOT  # noqa: E402

sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402
from oohstory_library.services.local_source_upgrade import (  # noqa: E402
    LocalSourceUpgradeRuntime,
    process_job,
    retire_recorded_source_covers,
)
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402


def install_shutdown_handlers(stop_event: threading.Event) -> None:
    """Drain the claimed batch before systemd restarts the worker.

    A hard stop leaves MySQL leases in ``processing`` for 45 minutes.  The
    signal handler therefore only requests shutdown; the active bounded batch
    finishes and persists its result before the main loop exits.
    """

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


async def process_batch(
    service: ElectronicLibraryService,
    rows: list[dict],
) -> list[dict | BaseException]:
    """Run the bounded claimed batch concurrently; persist results later."""

    return list(
        await asyncio.gather(
            *(process_job(service, row) for row in rows),
            return_exceptions=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--idle-seconds", type=float, default=30.0)
    parser.add_argument("--seed-seconds", type=float, default=900.0)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 20:
        parser.error("--batch-size 必须在 1..20")
    if args.limit < 0:
        parser.error("--limit 不能小于 0")

    stop_event = threading.Event()
    install_shutdown_handlers(stop_event)

    service = ElectronicLibraryService()
    runtime = LocalSourceUpgradeRuntime(service)
    mysql_runtime = MySQLLibraryRuntime(
        service.infrastructure_settings,
        service.mysql_pool,
        service.redis_queue,
    )
    cleanup = retire_recorded_source_covers(
        service,
        mysql_runtime,
        limit=5000,
    )
    if cleanup["selected"]:
        print(json.dumps({"cleanup": cleanup}, ensure_ascii=False), flush=True)
    seeded = runtime.seed()
    last_seeded_at = time.monotonic()
    worker_id = MySQLLibraryRuntime.worker_id("local-source-upgrade")
    completed = 0
    print(json.dumps({"seeded": seeded, "stats": runtime.stats()}, ensure_ascii=False), flush=True)
    while not stop_event.is_set() and (
        args.limit == 0 or completed < args.limit
    ):
        if time.monotonic() - last_seeded_at >= max(args.seed_seconds, 60.0):
            runtime.seed()
            cleanup = retire_recorded_source_covers(
                service,
                mysql_runtime,
                limit=200,
            )
            if cleanup["selected"]:
                print(json.dumps({"cleanup": cleanup}, ensure_ascii=False), flush=True)
            last_seeded_at = time.monotonic()
        remaining = args.batch_size if args.limit == 0 else min(
            args.batch_size, args.limit - completed
        )
        rows = runtime.claim(limit=remaining, worker_id=worker_id)
        if not rows:
            if args.limit:
                break
            stop_event.wait(max(float(args.idle_seconds), 1.0))
            continue
        batch_results = asyncio.run(process_batch(service, rows))
        for row, result in zip(rows, batch_results, strict=True):
            if not isinstance(result, BaseException):
                runtime.finish(row, result=result)
                outcome = {"catalog_id": int(row["catalog_id"]), **result}
            else:
                message = f"{type(result).__name__}: {str(result)[:3800]}"
                runtime.finish(row, error=message, retry=True)
                outcome = {
                    "catalog_id": int(row["catalog_id"]),
                    "status": "retry",
                    "error": message,
                }
            completed += 1
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
    print(json.dumps({"processed": completed, "stats": runtime.stats()}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
