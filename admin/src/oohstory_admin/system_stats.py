from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _read_text(path: str, limit: int = 64 * 1024) -> str:
    with open(path, "rb") as handle:
        return handle.read(limit).decode("ascii", "replace")


def memory_summary() -> dict[str, int | None]:
    fields: dict[str, int] = {}
    try:
        for line in _read_text("/proc/meminfo").splitlines():
            key, _, raw = line.partition(":")
            if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                fields[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return {
        "total_bytes": fields.get("MemTotal"),
        "available_bytes": fields.get("MemAvailable"),
        "used_bytes": (
            fields["MemTotal"] - fields["MemAvailable"]
            if "MemTotal" in fields and "MemAvailable" in fields
            else None
        ),
        "swap_total_bytes": fields.get("SwapTotal"),
        "swap_used_bytes": (
            fields["SwapTotal"] - fields["SwapFree"]
            if "SwapTotal" in fields and "SwapFree" in fields
            else None
        ),
    }


def disk_summary(paths: tuple[str, ...] = ("/", "/srv/oohstory/library")) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for text_path in paths:
        path = Path(text_path)
        try:
            stat = os.statvfs(path)
            result.append(
                {
                    "path": text_path,
                    "available": True,
                    "total_bytes": stat.f_blocks * stat.f_frsize,
                    "free_bytes": stat.f_bavail * stat.f_frsize,
                    "used_bytes": (stat.f_blocks - stat.f_bfree) * stat.f_frsize,
                }
            )
        except OSError:
            result.append({"path": text_path, "available": False})
    return result


def process_summary() -> dict[str, Any]:
    result: dict[str, Any] = {"load": None, "uptime_seconds": None, "process_count": None}
    try:
        values = _read_text("/proc/loadavg", 4096).split()
        result["load"] = [float(value) for value in values[:3]]
    except (OSError, ValueError):
        pass
    try:
        result["uptime_seconds"] = int(float(_read_text("/proc/uptime", 4096).split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    try:
        count = 0
        with os.scandir("/proc") as entries:
            for index, entry in enumerate(entries):
                if index >= 65_536:
                    break
                if entry.name.isdigit():
                    count += 1
        result["process_count"] = count
    except OSError:
        pass
    return result


def system_summary() -> dict[str, Any]:
    return {
        "memory": memory_summary(),
        "disks": disk_summary(),
        "processes": process_summary(),
    }
