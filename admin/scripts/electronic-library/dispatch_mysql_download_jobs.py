#!/usr/bin/env python3
"""Refill the bounded Redis download stream from durable MySQL jobs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


from project_paths import APP_ROOT  # noqa: E402
sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
    RedisQueueClient,
)
from oohstory_library.services.library_download_queue import LibraryDownloadQueue  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    settings = LibraryInfrastructureSettings.from_env()
    queue = LibraryDownloadQueue(
        MySQLConnectionPool(settings),
        RedisQueueClient(settings),
    )
    result = queue.dispatch(limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
