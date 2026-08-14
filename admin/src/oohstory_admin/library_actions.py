"""Privilege-separated mutations for the shared electronic-library workbench."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any


MAX_HELPER_RESPONSE = 4 * 1024 * 1024
JOB_ID_RE = re.compile(r"^[a-f0-9]{20}$")
TASK_ID_RE = re.compile(r"^[a-f0-9]{12}$")
SEARCH_SOURCES = frozenset(
    {
        "local_txt80_catalog",
        "authorized_xbiquge",
        "authorized_ixdzs",
        "authorized_shubaow",
        "linovelib",
        "authorized_txt80",
        "fanqie_desktop_bridge",
        "authorized_zlibrary",
        "project_gutenberg",
    }
)


class LibraryActionError(RuntimeError):
    """The root helper rejected or failed an exact workbench action."""


@dataclass(frozen=True, slots=True)
class LibraryActionResult:
    ok: bool
    action: str
    message: str
    data: dict[str, Any]


class LibraryActionClient:
    """Invoke the root-owned helper using JSON over stdin, never paths or shell."""

    def __init__(
        self,
        helper_path: str,
        *,
        use_sudo_helper: bool,
        timeout_seconds: int = 45,
    ):
        self.helper_path = helper_path
        self.use_sudo_helper = use_sudo_helper
        self.timeout_seconds = timeout_seconds

    def _command(self) -> list[str]:
        if self.use_sudo_helper:
            return ["/usr/bin/sudo", "-n", self.helper_path]
        return [self.helper_path]

    def run(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: int | None = None,
    ) -> LibraryActionResult:
        body = {"action": str(action), **(payload or {})}
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(encoded) > 64 * 1024:
            raise LibraryActionError("书库操作请求超过安全上限")
        try:
            completed = subprocess.run(
                self._command(),
                input=encoded,
                capture_output=True,
                timeout=timeout_seconds or self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LibraryActionError("书库操作助手不可用或响应超时") from exc
        if len(completed.stdout) > MAX_HELPER_RESPONSE:
            raise LibraryActionError("书库操作助手响应超过安全上限")
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise LibraryActionError(detail[-500:] or "书库操作助手返回无效响应") from exc
        if not isinstance(response, dict):
            raise LibraryActionError("书库操作助手返回无效响应")
        ok = bool(response.get("ok")) and completed.returncode == 0
        message = str(response.get("message") or ("操作完成" if ok else "操作失败"))[:500]
        data = response.get("data")
        if not isinstance(data, dict):
            data = {}
        if not ok:
            raise LibraryActionError(message)
        return LibraryActionResult(True, str(action), message, data)

    def capabilities(self) -> dict[str, Any]:
        return self.run("capabilities").data

    def search(self, query: str, source: str, limit: int = 24) -> dict[str, Any]:
        if source not in SEARCH_SOURCES:
            raise LibraryActionError("未知书源")
        return self.run(
            "search", {"query": query, "source": source, "limit": limit}, timeout_seconds=120
        ).data

    def job(self, job_id: str) -> dict[str, Any]:
        if not JOB_ID_RE.fullmatch(job_id):
            raise LibraryActionError("任务标识无效")
        return self.run("job_status", {"job_id": job_id}).data

    def task_runners(self) -> dict[str, Any]:
        return self.run("task_runners", timeout_seconds=30).data

    def task(self, task_id: str) -> dict[str, Any]:
        if not TASK_ID_RE.fullmatch(task_id):
            raise LibraryActionError("拆书任务标识无效")
        return self.run("task_detail", {"task_id": task_id}).data

    def serialized_update_source(self, source_id: str, enabled: bool) -> dict[str, Any]:
        return self.run(
            "serialized_update_source",
            {"source_id": source_id, "enabled": bool(enabled)},
            timeout_seconds=120,
        ).data
