#!/usr/bin/env python3
"""Repair verified legacy cover filenames in bounded MySQL batches."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="修复已核验封面的历史裸对象键"
    )
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    runtime = MySQLLibraryRuntime()
    totals = {
        "candidates": 0,
        "repaired": 0,
        "missing_files": 0,
        "invalid_filenames": 0,
    }
    while True:
        for attempt in range(5):
            try:
                result = runtime.repair_bare_cover_object_keys(
                    limit=args.batch_size
                )
                break
            except Exception as exc:
                code = exc.args[0] if exc.args else None
                if code not in {1205, 1213, 2006, 2013} or attempt == 4:
                    raise
                print(
                    {
                        "status": "retrying",
                        "attempt": attempt + 1,
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    },
                    flush=True,
                )
                time.sleep(2**attempt)
                runtime = MySQLLibraryRuntime()
        for key in totals:
            totals[key] += int(result[key])
        print(result, flush=True)
        if result["candidates"] < min(
            max(int(args.batch_size), 1), 500
        ):
            break
        if result["repaired"] == 0:
            break
    print({"total": totals}, flush=True)
    return 0 if not totals["missing_files"] and not totals["invalid_filenames"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
