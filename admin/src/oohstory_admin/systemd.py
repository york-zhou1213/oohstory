from __future__ import annotations

import subprocess
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .units import ALLOWED_ACTIONS, UNIT_ALLOWLIST


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class ActionResult:
    ok: bool
    action: str
    target: str
    message: str
    argv: tuple[str, ...]


def _clean_output(value: str, limit: int = 800) -> str:
    return " ".join(value.replace("\x00", "").split())[:limit]


_ABSOLUTE_SCRIPT_RE = re.compile(r"(?:^|[\s=])(?P<path>/[^\s;{}]+\.(?:py|js|sh))(?=\s|;|$)")
_RELATIVE_SCRIPT_RE = re.compile(r"(?P<path>(?:\./)?scripts/[^\s;{}]+\.(?:py|js|sh))(?=\s|;|$)")
_EXECUTABLE_RE = re.compile(r"(?:path=|argv\[\]=)(?P<path>/[^\s;{}]+)")


def _runtime_path(exec_start: str, working_directory: str) -> str:
    absolute = _ABSOLUTE_SCRIPT_RE.search(exec_start)
    if absolute:
        return absolute.group("path")
    relative = _RELATIVE_SCRIPT_RE.search(exec_start)
    if relative and working_directory.startswith("/"):
        return str(Path(working_directory) / relative.group("path").removeprefix("./"))
    executable = _EXECUTABLE_RE.search(exec_start)
    return executable.group("path") if executable else ""


class SystemdController:
    SHOW_PROPERTIES = (
        "Id",
        "LoadState",
        "ActiveState",
        "SubState",
        "UnitFileState",
        "MainPID",
        "MemoryCurrent",
        "NRestarts",
        "ExecMainStartTimestamp",
        "NextElapseUSecRealtime",
        "FragmentPath",
        "WorkingDirectory",
        "ExecStart",
    )

    def __init__(
        self,
        systemctl_path: str = "/usr/bin/systemctl",
        *,
        use_sudo_helper: bool = False,
        helper_path: str = "/usr/local/libexec/oohstory-admin-systemctl",
        runner: Runner = subprocess.run,
    ):
        self.systemctl_path = systemctl_path
        self.use_sudo_helper = use_sudo_helper
        self.helper_path = helper_path
        self.runner = runner

    @staticmethod
    def _run_options(timeout: float) -> dict[str, Any]:
        return {
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "check": False,
            "shell": False,
            "env": {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        }

    def status(self, unit: str) -> dict[str, Any]:
        if unit not in UNIT_ALLOWLIST:
            raise ValueError("unit is not allowlisted")
        properties = ",".join(self.SHOW_PROPERTIES)
        argv = [self.systemctl_path, "--no-pager", "show", unit, f"--property={properties}"]
        try:
            completed = self.runner(argv, **self._run_options(3.0))
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {
                "unit": unit,
                "label": UNIT_ALLOWLIST[unit],
                "available": False,
                "error": "systemd 状态读取超时或不可用",
            }
        if completed.returncode != 0:
            return {
                "unit": unit,
                "label": UNIT_ALLOWLIST[unit],
                "available": False,
                "error": _clean_output(completed.stderr) or "systemd 状态读取失败",
            }
        values: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        memory_value = values.get("MemoryCurrent", "")
        try:
            memory_bytes: int | None = int(memory_value)
        except ValueError:
            memory_bytes = None
        exec_start = values.get("ExecStart", "")
        working_directory = values.get("WorkingDirectory", "")
        return {
            "unit": unit,
            "label": UNIT_ALLOWLIST[unit],
            "available": True,
            "load": values.get("LoadState", "unknown"),
            "active": values.get("ActiveState", "unknown"),
            "sub": values.get("SubState", "unknown"),
            "enabled": values.get("UnitFileState", "unknown"),
            "pid": values.get("MainPID", "0"),
            "memory_bytes": memory_bytes,
            "restarts": values.get("NRestarts", "0"),
            "started_at": values.get("ExecMainStartTimestamp", ""),
            "next_run": values.get("NextElapseUSecRealtime", ""),
            "unit_path": values.get("FragmentPath", ""),
            "working_directory": working_directory,
            "exec_start": _clean_output(exec_start, 2_000),
            "runtime_path": _runtime_path(exec_start, working_directory),
        }

    def statuses(self) -> list[dict[str, Any]]:
        units = list(UNIT_ALLOWLIST)
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="systemd-status") as pool:
            return list(pool.map(self.status, units))

    def action(self, action: str, unit: str) -> ActionResult:
        if action not in ALLOWED_ACTIONS:
            raise ValueError("action is not allowlisted")
        if unit not in UNIT_ALLOWLIST:
            raise ValueError("unit is not allowlisted")
        if self.use_sudo_helper:
            argv = ["/usr/bin/sudo", "-n", self.helper_path, action, unit]
        else:
            argv = [self.systemctl_path, "--no-pager", action, unit]
        try:
            completed = self.runner(argv, **self._run_options(30.0))
        except subprocess.TimeoutExpired:
            return ActionResult(False, action, unit, "systemctl 操作超时", tuple(argv))
        except OSError:
            return ActionResult(False, action, unit, "systemctl 不可执行", tuple(argv))
        output = _clean_output(completed.stdout or completed.stderr)
        if completed.returncode == 0:
            return ActionResult(True, action, unit, output or "操作成功", tuple(argv))
        return ActionResult(False, action, unit, output or f"systemctl 返回 {completed.returncode}", tuple(argv))
