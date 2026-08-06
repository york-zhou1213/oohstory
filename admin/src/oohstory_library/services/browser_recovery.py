"""Shared recovery guard for the dedicated Chrome CDP service."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from typing import Callable, TypeVar
from urllib.parse import urlparse

import requests


_SERVICE_NAME = re.compile(r"^[A-Za-z0-9@_.:-]+$")
_T = TypeVar("_T")


def _positive_number(name: str, default: float, *, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


class BrowserRecoveryCoordinator:
    """Count shared CDP failures and serialize service recovery attempts."""

    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        session_factory: Callable[[], requests.Session] = requests.Session,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._run = run
        self._session_factory = session_factory
        self._monotonic = monotonic
        self._sleep = sleep
        self._condition = threading.Condition()
        self._consecutive_failures = 0
        self._restarting = False
        self._restart_generation = 0
        self._last_restart_started = float("-inf")

    @property
    def consecutive_failures(self) -> int:
        with self._condition:
            return self._consecutive_failures

    def record_success(self) -> None:
        with self._condition:
            self._consecutive_failures = 0

    def call(self, cdp_url: str, operation: Callable[[], _T]) -> _T:
        """Run one CDP operation, retrying once after a successful recovery."""
        recoverable = (
            OSError,
            RuntimeError,
            ValueError,
            subprocess.SubprocessError,
        )
        try:
            result = operation()
        except recoverable:
            if not self.recover_after_failure(cdp_url):
                raise
            try:
                result = operation()
            except recoverable:
                self.recover_after_failure(cdp_url)
                raise
        self.record_success()
        return result

    def recover_after_failure(self, cdp_url: str) -> bool:
        """Return true only when Chrome recovered and the caller should retry."""
        threshold = int(
            _positive_number(
                "OOHSTORY_LIBRARY_BROWSER_FAILURE_RESTART_THRESHOLD",
                10,
                minimum=1,
            )
        )
        cooldown = _positive_number(
            "OOHSTORY_LIBRARY_BROWSER_RESTART_COOLDOWN_SECONDS",
            60,
            minimum=0,
        )
        ready_timeout = _positive_number(
            "OOHSTORY_LIBRARY_BROWSER_READY_TIMEOUT_SECONDS",
            30,
            minimum=1,
        )

        with self._condition:
            self._consecutive_failures += 1
            if self._consecutive_failures < threshold:
                return False
            if self._restarting:
                generation = self._restart_generation
                self._condition.wait_for(
                    lambda: not self._restarting,
                    timeout=ready_timeout + 35,
                )
                return self._restart_generation > generation
            now = self._monotonic()
            if now - self._last_restart_started < cooldown:
                return False
            self._restarting = True
            self._last_restart_started = now

        recovered = False
        try:
            self._restart_service(cdp_url, ready_timeout=ready_timeout)
            recovered = True
            return True
        finally:
            with self._condition:
                if recovered:
                    self._consecutive_failures = 0
                    self._restart_generation += 1
                self._restarting = False
                self._condition.notify_all()

    def _restart_service(self, cdp_url: str, *, ready_timeout: float) -> None:
        service = os.getenv(
            "OOHSTORY_LIBRARY_BROWSER_SERVICE",
            "oohstory-shubaow-browser.service",
        ).strip()
        if not service or not _SERVICE_NAME.fullmatch(service):
            raise RuntimeError("浏览器恢复服务名无效")
        parsed = urlparse(str(cdp_url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("浏览器恢复所需 CDP 地址无效")

        completed = self._run(
            ["systemctl", "restart", service],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "systemctl 失败").strip()
            raise RuntimeError(f"浏览器服务重启失败：{detail[-400:]}")

        session = self._session_factory()
        session.trust_env = False
        ready_url = f"{str(cdp_url).rstrip('/')}/json/version"
        deadline = self._monotonic() + ready_timeout
        last_error = ""
        while self._monotonic() < deadline:
            try:
                response = session.get(ready_url, timeout=(1, 2))
                if response.ok:
                    payload = response.json()
                    if payload.get("webSocketDebuggerUrl"):
                        return
                last_error = f"HTTP {response.status_code}"
            except (requests.RequestException, TypeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            self._sleep(0.5)
        raise RuntimeError(f"浏览器服务重启后 CDP 未就绪：{last_error[-300:]}")


shared_browser_recovery = BrowserRecoveryCoordinator()
