#!/usr/bin/env python3
"""Continuously drain one electronic-library source at a bounded full rate.

Each enabled site owns an independent systemd instance.  Its execution lane
matches the real transport boundary: Xbiquge and Ixdzs get separate HTTP lanes,
while sources that reuse the same browser stay FIFO inside that browser lane.
Each site's bounded per-cycle count is read from OOHStory's atomic runtime
configuration before every cycle; cycle starts remain at least 60 seconds
apart. Slow upstream sites naturally run below the target instead of overlapping
another cycle.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from project_paths import APP_ROOT
from oohstory_library.services.runtime_controls import (
    OOHStoryRuntimeControls,
    SITE_IDS,
    SITE_BOOKS_PER_CYCLE_DEFAULT,
    validate_site_books_per_cycle,
)


TARGET_BOOKS_PER_MINUTE = SITE_BOOKS_PER_CYCLE_DEFAULT
MIN_BOOKS_PER_CYCLE = 1
MAX_BOOKS_PER_CYCLE = 500
SITE_LABELS = {
    "txt80": "TXT80 本地书库正文",
    "xbiquge": "新笔趣阁授权正文",
    "ixdzs": "爱下授权正文",
    "shubaow": "书宝授权正文",
    "linovelib": "哔哩轻小说授权正文",
}
AUTHORIZED_SITES = frozenset({"xbiquge", "ixdzs", "shubaow", "linovelib"})
SOURCE_SLOT_LANES = {
    "txt80": "local",
    "xbiquge": "http-xbiquge",
    "ixdzs": "http-ixdzs",
    "shubaow": "browser-shubaow",
    "linovelib": "browser-shubaow",
}
SITE_BOOK_LIMIT_CAPS = {
    "shubaow": 1,
}
DEFAULT_SITE_WORKERS = {
    "xbiquge": 3,
    "ixdzs": 8,
    "shubaow": 1,
    "linovelib": 5,
}
WORKER_FLAGS = {
    "xbiquge": "--xbiquge-workers",
    "ixdzs": "--ixdzs-workers",
    "shubaow": "--shubaow-workers",
    "linovelib": "--linovelib-workers",
}
TOOLS_ROOT = Path(__file__).resolve().parent
LIBRARY_ROOT = APP_ROOT / "electronic-library"
STATE_ROOT = LIBRARY_ROOT / "全局索引"
PYTHON = APP_ROOT / ".venv" / "bin" / "python"
STOP = threading.Event()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_state(site: str, payload: dict[str, object]) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    # Status belongs to the OOHStory service owner.  The execution lock below
    # is deliberately shared with Webnovel Writer to serialize NAS writes,
    # but each admin must render only its own service state.
    path = STATE_ROOT / f"oohstory-site-full-sync-{site}.json"
    temporary = path.with_suffix(".json.tmp")
    payload = {**payload, "site": site, "updated_at": now()}
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def slot_lane(site: str) -> str:
    return SOURCE_SLOT_LANES.get(site, "http")


def build_command(site: str, target_books_per_minute: int) -> list[str]:
    if site not in SITE_LABELS:
        raise ValueError("未知正文同步站点")
    target_books_per_minute = validate_site_books_per_cycle(
        target_books_per_minute
    )
    python = str(PYTHON if PYTHON.is_file() else Path(sys.executable))
    if site == "txt80":
        return [
            python,
            str(TOOLS_ROOT / "txt80_crawler.py"),
            "--skip-discovery",
            "--workers", "8",
            "--delay", "0.10",
            "--timeout", "60",
            "--max-attempts", "8",
            "--min-free-gb", "15",
            "--limit-books", str(target_books_per_minute),
        ]
    effective_limit = min(
        target_books_per_minute,
        SITE_BOOK_LIMIT_CAPS.get(site, target_books_per_minute),
    )
    return [
        python,
        str(TOOLS_ROOT / "authorized_site_catalog_sync.py"),
        "--sources", site,
        "--download-limit", str(effective_limit),
        "--page-limit", "5",
        WORKER_FLAGS[site], str(DEFAULT_SITE_WORKERS[site]),
    ]


def slot_path(site: str) -> Path:
    return LIBRARY_ROOT / f".site-full-sync-{slot_lane(site)}.lock"


def sleep_remaining(started: float, interval: float) -> None:
    STOP.wait(max(0.0, interval - (time.monotonic() - started)))


def configured_books_per_cycle(site: str, fallback: int) -> int:
    """Read the authorized site's latest atomic config before every cycle."""

    fallback = validate_site_books_per_cycle(fallback)
    if site not in SITE_IDS:
        return fallback
    controls = OOHStoryRuntimeControls(STATE_ROOT).read()
    return validate_site_books_per_cycle(
        controls["site_books_per_cycle"].get(site, fallback)
    )


def run_loop(site: str, target_books_per_minute: int, *, once: bool = False) -> int:
    target_books_per_minute = validate_site_books_per_cycle(
        target_books_per_minute
    )
    interval = 60.0
    state: dict[str, object] = {
        "status": "starting",
        "label": SITE_LABELS[site],
        "target_books_per_minute": target_books_per_minute,
        "cycle_limit": target_books_per_minute,
        "slot_lane": slot_lane(site),
        "message": "全力同步准备启动",
    }
    atomic_state(site, state)
    lock_path = slot_path(site)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    cycle = 0
    while not STOP.is_set():
        cycle += 1
        cycle_started = time.monotonic()
        cycle_limit = configured_books_per_cycle(
            site,
            target_books_per_minute,
        )
        command = build_command(site, cycle_limit)
        state.update(
            status="waiting_slot",
            cycle=cycle,
            target_books_per_minute=cycle_limit,
            cycle_limit=cycle_limit,
            slot_lane=slot_lane(site),
            message=f"等待 {slot_lane(site)} 执行槽位",
        )
        atomic_state(site, state)
        with lock_path.open("a+", encoding="utf-8") as slot:
            fcntl.flock(slot, fcntl.LOCK_EX)
            if STOP.is_set():
                break
            state.update(
                status="running",
                cycle_started_at=now(),
                slot_lane=slot_lane(site),
                message=f"正在同步；本轮最多 {cycle_limit} 本",
            )
            atomic_state(site, state)
            completed = subprocess.run(command, cwd=APP_ROOT, check=False)
            elapsed = round(time.monotonic() - cycle_started, 3)
            state.update(
                status="cooldown" if completed.returncode == 0 else "retry_wait",
                last_returncode=int(completed.returncode),
                last_cycle_seconds=elapsed,
                last_cycle_finished_at=now(),
                slot_lane=slot_lane(site),
                message=(
                    "本轮完成，等待下一分钟"
                    if completed.returncode == 0
                    else "本轮异常，限速等待后重试"
                ),
            )
            atomic_state(site, state)
            fcntl.flock(slot, fcntl.LOCK_UN)
        if once:
            return int(state.get("last_returncode") or 0)
        sleep_remaining(cycle_started, interval)
    state.update(status="stopped", message="全力同步已停止")
    atomic_state(site, state)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", choices=tuple(SITE_LABELS), required=True)
    parser.add_argument(
        "--target-books-per-minute",
        type=int,
        default=TARGET_BOOKS_PER_MINUTE,
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        validate_site_books_per_cycle(args.target_books_per_minute)
    except ValueError as exc:
        parser.error(str(exc))
    for watched in (signal.SIGTERM, signal.SIGINT):
        signal.signal(watched, lambda *_: STOP.set())
    return run_loop(
        args.site,
        args.target_books_per_minute,
        once=args.once,
    )


if __name__ == "__main__":
    raise SystemExit(main())
