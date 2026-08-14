#!/usr/bin/env python3
"""Durable worker for administrator-requested tone or plot indexes."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from project_paths import APP_ROOT


sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402
from oohstory_library.services.unit_names import library_unit_name  # noqa: E402


SERVICE_NAME = library_unit_name("oohstory-library-derived-index.service")
DEFAULT_RUNTIME_DIR = APP_ROOT / "electronic-library" / "全局索引"
RUNTIME_DIR = Path(
    os.getenv("WEBNOVEL_LIBRARY_RUNTIME_DIR", str(DEFAULT_RUNTIME_DIR))
).expanduser().resolve()
REQUEST_PATH = RUNTIME_DIR / "electronic_library_derived_index_request.json"
STATUS_PATH = RUNTIME_DIR / "electronic_library_derived_index_refresh_status.json"
QUEUE_LOCK_PATH = RUNTIME_DIR / ".electronic_library_derived_index_queue.lock"
WORKER_LOCK_PATH = RUNTIME_DIR / ".electronic_library_derived_index_worker.lock"
MAX_ATTEMPTS = 5
MANUAL_PLOT_REASON_PREFIX = "manual_plot_"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def update_status(patch: dict[str, Any]) -> dict[str, Any]:
    current = read_json(STATUS_PATH, {})
    current.update(patch)
    current["updated_at"] = now()
    atomic_json(STATUS_PATH, current)
    return current


def pid_is_alive(pid: Any) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
        os.kill(value, 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def service_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE_NAME],
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def start_service() -> None:
    subprocess.run(
        ["systemctl", "reset-failed", SERVICE_NAME],
        capture_output=True,
        timeout=10,
        check=False,
    )
    subprocess.run(
        ["systemctl", "start", "--no-block", SERVICE_NAME],
        capture_output=True,
        timeout=10,
        check=False,
    )


def merge_request(
    *,
    run_tone: bool,
    run_plot: bool,
    force_tone: bool = False,
    force_plot: bool = False,
    manual_plot_authorized: bool = False,
    plot_reason: str = "",
    reason: str,
) -> dict[str, Any]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with QUEUE_LOCK_PATH.open("a+", encoding="utf-8") as queue_lock:
        fcntl.flock(queue_lock, fcntl.LOCK_EX)
        current = read_json(REQUEST_PATH, {})
        pending = bool(current.get("pending"))
        current_plot_authorized = bool(
            pending
            and current.get("run_plot")
            and current.get("manual_plot_authorized") is True
            and str(current.get("plot_reason") or "").startswith(
                MANUAL_PLOT_REASON_PREFIX
            )
        )
        requested_plot_authorized = bool(
            run_plot
            and manual_plot_authorized
            and str(plot_reason or "").startswith(MANUAL_PLOT_REASON_PREFIX)
        )
        merged_plot = bool(current_plot_authorized or requested_plot_authorized)
        request = {
            "schema_version": 1,
            "revision": int(current.get("revision") or 0) + 1,
            "pending": True,
            "run_tone": bool(run_tone or (pending and current.get("run_tone"))),
            "run_plot": merged_plot,
            "force_tone": bool(
                force_tone or (pending and current.get("force_tone"))
            ),
            "force_plot": bool(
                (force_plot and requested_plot_authorized)
                or (current_plot_authorized and current.get("force_plot"))
            ),
            "manual_plot_authorized": merged_plot,
            "plot_reason": (
                str(plot_reason or "")[:80]
                if requested_plot_authorized
                else (
                    str(current.get("plot_reason") or "")[:80]
                    if current_plot_authorized
                    else ""
                )
            ),
            "reason": str(reason)[:80],
            "requested_at": now(),
            "request_id": str(current.get("request_id") or "probe-recovery"),
        }
        atomic_json(REQUEST_PATH, request)
        fcntl.flock(queue_lock, fcntl.LOCK_UN)
    return request


def claim_request() -> dict[str, Any] | None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with QUEUE_LOCK_PATH.open("a+", encoding="utf-8") as queue_lock:
        fcntl.flock(queue_lock, fcntl.LOCK_EX)
        request = read_json(REQUEST_PATH, {})
        if not request.get("pending"):
            fcntl.flock(queue_lock, fcntl.LOCK_UN)
            return None
        request["pending"] = False
        request["claimed_at"] = now()
        request["claimed_by_pid"] = os.getpid()
        atomic_json(REQUEST_PATH, request)
        fcntl.flock(queue_lock, fcntl.LOCK_UN)
    return request


def requeue_request(request: dict[str, Any], reason: str) -> None:
    merge_request(
        run_tone=bool(request.get("run_tone")),
        run_plot=bool(request.get("run_plot")),
        force_tone=bool(request.get("force_tone")),
        force_plot=bool(request.get("force_plot")),
        manual_plot_authorized=request.get("manual_plot_authorized") is True,
        plot_reason=str(request.get("plot_reason") or ""),
        reason=reason,
    )


def retryable_mysql_error(exc: BaseException) -> bool:
    message = f"{type(exc).__name__}: {exc}".casefold()
    return any(
        marker in message
        for marker in (
            "2013",
            "2006",
            "lost connection",
            "server has gone away",
            "read operation timed out",
            "timed out",
            "derived index incomplete",
        )
    )


def run_pipeline() -> int:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with WORKER_LOCK_PATH.open("a+", encoding="utf-8") as worker_lock:
        try:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        while True:
            request = claim_request()
            if request is None:
                update_status(
                    {
                        "status": "completed",
                        "running": False,
                        "stage": "completed",
                        "pid": os.getpid(),
                        "run_tone": False,
                        "run_plot": False,
                        "finished_at": now(),
                        "message": "派生索引后台队列已处理完成",
                    }
                )
                return 0

            plot_requested = bool(request.get("run_plot"))
            plot_authorized = bool(
                plot_requested
                and request.get("manual_plot_authorized") is True
                and str(request.get("plot_reason") or "").startswith(
                    MANUAL_PLOT_REASON_PREFIX
                )
            )
            if plot_requested and not plot_authorized:
                request["run_plot"] = False
                request["force_plot"] = False
                update_status(
                    {
                        "status": "blocked",
                        "running": False,
                        "stage": "plot_blocked",
                        "pid": os.getpid(),
                        "run_plot": False,
                        "message": "已拒绝缺少后台手动授权标记的剧情索引请求",
                    }
                )
                if not request.get("run_tone"):
                    return 0

            previous = read_json(STATUS_PATH, {})
            attempts = int(previous.get("attempts") or 0) + 1
            update_status(
                {
                    "status": "running",
                    "running": True,
                    "stage": "tone" if request.get("run_tone") else "plot",
                    "pid": os.getpid(),
                    "attempts": attempts,
                    "request_id": request.get("request_id"),
                    "request_revision": request.get("revision"),
                    "run_tone": bool(request.get("run_tone")),
                    "run_plot": bool(request.get("run_plot")),
                    "started_at": now(),
                    "finished_at": None,
                    "rerun_requested": False,
                    "message": (
                        "后台正在增量更新书籍基调和索引"
                        if request.get("run_tone")
                        else "后台正在增量更新剧情索引"
                    ),
                }
            )
            service = ElectronicLibraryService()
            try:
                tone_result: dict[str, Any] | None = None
                plot_result: dict[str, Any] | None = None
                if request.get("run_tone"):
                    tone_result = service.build_index(
                        force=bool(request.get("force_tone"))
                    )
                    if int(tone_result.get("failed") or 0):
                        raise RuntimeError(
                            "derived index incomplete: tone failures="
                            f"{int(tone_result.get('failed') or 0)}"
                        )
                if request.get("run_plot"):
                    update_status(
                        {
                            "status": "running",
                            "running": True,
                            "stage": "plot",
                            "pid": os.getpid(),
                            "message": "基调索引已完成，后台自动增量更新剧情索引",
                        }
                    )
                    plot_result = service.build_plot_index(
                        force=bool(request.get("force_plot"))
                    )
                    if int(plot_result.get("failed") or 0):
                        raise RuntimeError(
                            "derived index incomplete: plot failures="
                            f"{int(plot_result.get('failed') or 0)}"
                        )
                if request.get("run_tone") and request.get("run_plot"):
                    completed_message = "书籍基调与剧情索引后台更新完成"
                elif request.get("run_tone"):
                    completed_message = "书籍基调和索引后台增量更新完成"
                else:
                    completed_message = "剧情索引手动后台更新完成"
                update_status(
                    {
                        "status": "completed",
                        "running": False,
                        "stage": "completed",
                        "pid": os.getpid(),
                        "finished_at": now(),
                        "attempts": 0,
                        "tone": tone_result,
                        "plot": plot_result,
                        "message": completed_message,
                    }
                )
            except Exception as exc:
                retryable = retryable_mysql_error(exc)
                message = f"{type(exc).__name__}: {str(exc)[:420]}"
                update_status(
                    {
                        "status": "retrying" if retryable else "error",
                        "running": False,
                        "stage": "retrying" if retryable else "error",
                        "pid": os.getpid(),
                        "finished_at": now(),
                        "retryable": retryable,
                        "message": message,
                    }
                )
                if retryable and attempts < MAX_ATTEMPTS:
                    requeue_request(request, "mysql_timeout_retry")
                    return 1
                return 0


def newer(left: Any, right: Any) -> bool:
    return str(left or "") > str(right or "")


def probe() -> int:
    """Read-only compatibility probe.

    The worker is started directly when an administrator clicks an index
    action. A probe must never create, recover, or start an index job.
    """

    status = read_json(STATUS_PATH, {})
    request = read_json(REQUEST_PATH, {})
    if request.get("pending") and not service_active():
        update_status(
            {
                **status,
                "status": "queued",
                "running": False,
                "stage": "manual_start_required",
                "message": "索引请求等待后台手动操作，不执行自动唤醒",
            }
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    return probe() if args.probe else run_pipeline()


if __name__ == "__main__":
    raise SystemExit(main())
