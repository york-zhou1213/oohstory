#!/usr/bin/env python3
"""Run one serialized catalog/download synchronization cycle.

Derived indexes are deliberately excluded. They can only be started from the
admin page's explicit incremental/full rebuild buttons after ingestion ends.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
from pathlib import Path


from project_paths import APP_ROOT  # noqa: E402


TOOLS_ROOT = Path(__file__).resolve().parent
LIBRARY_ROOT = APP_ROOT / "electronic-library" / "txt80"
PYTHON = APP_ROOT / ".venv" / "bin" / "python"
LOCK_PATHS = {
    "local": LIBRARY_ROOT / ".scheduled-library-local-sync.lock",
    "fanqie": LIBRARY_ROOT / ".scheduled-library-fanqie-sync.lock",
}


def run(*args: str) -> None:
    command = [str(PYTHON), *args]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=APP_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--library",
        choices=("local", "fanqie"),
        required=True,
        help="只同步指定书库，避免本地与番茄定时开关互相耦合",
    )
    parser.add_argument("--refresh-catalog", action="store_true")
    args = parser.parse_args()

    lock_path = LOCK_PATHS[args.library]
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.library == "fanqie":
            run(
                str(TOOLS_ROOT / "library_sync.py"),
                "--scan-fanqie",
                "--refresh-tracked-fanqie",
                "--reconcile",
                "--dedupe",
                "--no-retry",
            )
            return

        run(
            str(TOOLS_ROOT / "library_sync.py"),
            "--reconcile",
            "--dedupe",
            "--retry-after-hours",
            "24",
        )
        if args.refresh_catalog:
            run(
                str(TOOLS_ROOT / "txt80_crawler.py"),
                "--discover-only",
                "--refresh-catalog",
                "--workers",
                "4",
                "--delay",
                "0.5",
                "--timeout",
                "60",
                "--max-attempts",
                "8",
            )
        run(
            str(TOOLS_ROOT / "txt80_crawler.py"),
            "--skip-discovery",
            "--workers",
            str(
                min(
                    max(
                        int(os.getenv("WEBNOVEL_TXT80_DOWNLOAD_WORKERS", "4")),
                        1,
                    ),
                    8,
                )
            ),
            "--delay",
            str(
                max(
                    float(os.getenv("WEBNOVEL_TXT80_DOWNLOAD_DELAY", "0.25")),
                    0.1,
                )
            ),
            "--timeout",
            "60",
            "--max-attempts",
            "8",
            "--min-free-gb",
            "15",
        )
        run(
            str(TOOLS_ROOT / "library_sync.py"),
            "--reconcile",
            "--dedupe",
            "--retry-after-hours",
            "24",
        )


if __name__ == "__main__":
    main()
