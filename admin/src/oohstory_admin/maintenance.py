from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]


class MaintenanceError(RuntimeError):
    """Raised when the fixed maintenance helper cannot change state."""


@dataclass(frozen=True, slots=True)
class MaintenanceStatus:
    available: bool
    enabled: bool
    changed_at: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "enabled": self.enabled,
            "changed_at": self.changed_at,
            "error": self.error,
        }


class MaintenanceController:
    """Call the root-owned helper with a closed action vocabulary."""

    def __init__(
        self,
        helper_path: str,
        *,
        use_sudo_helper: bool = False,
        runner: Runner = subprocess.run,
    ) -> None:
        self.helper_path = helper_path
        self.use_sudo_helper = use_sudo_helper
        self.runner = runner

    def _argv(self, action: str) -> list[str]:
        if action not in {"status", "enable", "disable"}:
            raise MaintenanceError("维护模式操作不在允许列表中")
        if self.use_sudo_helper:
            return ["/usr/bin/sudo", "-n", self.helper_path, action]
        return [self.helper_path, action]

    def _run(self, action: str) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(
                self._argv(action),
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MaintenanceError("维护模式助手不可用") from exc

    @staticmethod
    def _parse_status(output: str) -> MaintenanceStatus:
        try:
            payload = json.loads(output)
        except (json.JSONDecodeError, TypeError) as exc:
            raise MaintenanceError("维护模式助手返回无效状态") from exc
        if not isinstance(payload, dict) or type(payload.get("enabled")) is not bool:
            raise MaintenanceError("维护模式助手返回无效状态")
        return MaintenanceStatus(
            available=True,
            enabled=payload["enabled"],
            changed_at=str(payload.get("changed_at") or "")[:64],
        )

    def status(self) -> dict[str, object]:
        try:
            completed = self._run("status")
            if completed.returncode != 0:
                raise MaintenanceError(
                    (completed.stderr or completed.stdout).strip()
                    or "维护模式状态读取失败"
                )
            return self._parse_status(completed.stdout).as_dict()
        except MaintenanceError as exc:
            return MaintenanceStatus(
                available=False,
                enabled=False,
                error=str(exc)[:160],
            ).as_dict()

    def set_enabled(self, enabled: bool) -> dict[str, object]:
        action = "enable" if enabled else "disable"
        completed = self._run(action)
        if completed.returncode != 0:
            raise MaintenanceError(
                (completed.stderr or completed.stdout).strip()
                or "维护模式切换失败"
            )
        return self._parse_status(completed.stdout).as_dict()
