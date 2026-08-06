"""Atomic, OOHStory-owned runtime controls for long-running library workers."""

from __future__ import annotations

import fcntl
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


CONTROL_FILENAME = "oohstory-worker-controls.json"
SITE_IDS = ("txt80", "xbiquge", "ixdzs", "shubaow", "linovelib")
AUTHORIZED_SITE_IDS = SITE_IDS[1:]
SITE_BOOKS_PER_CYCLE_DEFAULT = 100
SITE_BOOKS_PER_CYCLE_MIN = 1
SITE_BOOKS_PER_CYCLE_MAX = 500
COVER_TARGET_PER_HOUR_DEFAULT = 60
COVER_TARGET_PER_HOUR_MIN = 50
COVER_TARGET_PER_HOUR_MAX = 160
COVER_CAPACITY_PER_WORKER = 20
COVER_MAX_WORKERS = 8


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _strict_int(value: Any, *, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是整数")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        normalized = int(value.strip())
    else:
        raise ValueError(f"{label}必须是整数")
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{label}必须在 {minimum}–{maximum} 之间")
    return normalized


def validate_site_books_per_cycle(value: Any) -> int:
    return _strict_int(
        value,
        minimum=SITE_BOOKS_PER_CYCLE_MIN,
        maximum=SITE_BOOKS_PER_CYCLE_MAX,
        label="每轮本数",
    )


def validate_cover_target_per_hour(value: Any) -> int:
    return _strict_int(
        value,
        minimum=COVER_TARGET_PER_HOUR_MIN,
        maximum=COVER_TARGET_PER_HOUR_MAX,
        label="每小时重绘目标",
    )


def cover_worker_count(target_per_hour: Any) -> int:
    target = validate_cover_target_per_hour(target_per_hour)
    return min(
        COVER_MAX_WORKERS,
        max(1, (target + COVER_CAPACITY_PER_WORKER - 1) // COVER_CAPACITY_PER_WORKER),
    )


def default_controls() -> dict[str, Any]:
    return {
        "version": 1,
        "site_books_per_cycle": {
            site_id: SITE_BOOKS_PER_CYCLE_DEFAULT for site_id in SITE_IDS
        },
        "cover_redraw": {
            "target_per_hour": COVER_TARGET_PER_HOUR_DEFAULT,
        },
        "updated_at": "",
    }


class OOHStoryRuntimeControls:
    """Read and update one bounded JSON document under an inter-process lock."""

    def __init__(self, runtime_dir: Path):
        self.runtime_dir = Path(runtime_dir).resolve()
        self.path = self.runtime_dir / CONTROL_FILENAME
        self.lock_path = self.runtime_dir / f".{CONTROL_FILENAME}.lock"

    def _read_unlocked(self) -> dict[str, Any]:
        controls = default_controls()
        if not self.path.exists():
            return controls
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("OOHStory 运行配置路径无效")
        if self.path.stat().st_size > 64 * 1024:
            raise ValueError("OOHStory 运行配置超过安全上限")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("OOHStory 运行配置不可读") from exc
        if not isinstance(payload, dict):
            raise ValueError("OOHStory 运行配置格式无效")
        raw_sites = payload.get("site_books_per_cycle")
        if isinstance(raw_sites, dict):
            for site_id in SITE_IDS:
                if site_id in raw_sites:
                    controls["site_books_per_cycle"][site_id] = (
                        validate_site_books_per_cycle(raw_sites[site_id])
                    )
        raw_cover = payload.get("cover_redraw")
        if isinstance(raw_cover, dict) and "target_per_hour" in raw_cover:
            controls["cover_redraw"]["target_per_hour"] = (
                validate_cover_target_per_hour(raw_cover["target_per_hour"])
            )
        controls["updated_at"] = str(payload.get("updated_at") or "")[:64]
        return controls

    def read(self) -> dict[str, Any]:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            try:
                return self._read_unlocked()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _write_locked(self, controls: dict[str, Any]) -> dict[str, Any]:
        controls = {
            **controls,
            "version": 1,
            "updated_at": _utc_now(),
        }
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(controls, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o640)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return controls

    def update_site(self, site_id: str, books_per_cycle: Any) -> dict[str, Any]:
        site_id = str(site_id or "").strip()
        if site_id not in SITE_IDS:
            raise ValueError("未知正文同步站点")
        normalized = validate_site_books_per_cycle(books_per_cycle)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                controls = self._read_unlocked()
                controls["site_books_per_cycle"][site_id] = normalized
                return self._write_locked(controls)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def update_cover_target(self, target_per_hour: Any) -> dict[str, Any]:
        normalized = validate_cover_target_per_hour(target_per_hour)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                controls = self._read_unlocked()
                controls["cover_redraw"]["target_per_hour"] = normalized
                return self._write_locked(controls)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
