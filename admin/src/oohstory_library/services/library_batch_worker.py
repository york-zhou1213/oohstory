"""全局电子书库批量拆书调度器。

一个批次只维护选择范围和游标，真正的书籍分析仍由
``library_task_worker.py`` 一书一进程执行。调度器按全局并行上限持续补位，
因此即使选择数万本，也不会在同一时刻拉起数万个 AI 会话。
"""

from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from oohstory_library.services.electronic_library import ElectronicLibraryService
from oohstory_library.services.library_task_runners import max_parallel_tasks


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    payload["updated_at"] = now()
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def child_counts(service: ElectronicLibraryService, batch: Dict[str, Any]) -> Dict[str, int]:
    task_root = service.global_task_root
    counts = {
        "active": 0,
        "terminal": 0,
        "completed": 0,
        "paused": 0,
        "error": 0,
    }
    for task_id in batch.get("child_task_ids") or []:
        task = read_json(task_root / f"{task_id}.json")
        status = str(task.get("status") or "")
        if status in {"queued", "running"}:
            counts["active"] += 1
        elif status in {"completed", "paused", "error"}:
            counts["terminal"] += 1
            counts[status] += 1
    return counts


def active_global_tasks(service: ElectronicLibraryService, project_root: Path) -> int:
    state = service.list_global_deconstructions(project_root)
    return sum(
        item.get("status") in {"queued", "running"}
        for item in state.get("items") or []
    )


def run(batch_file: Path) -> int:
    service = ElectronicLibraryService()
    while True:
        batch = read_json(batch_file)
        if not batch:
            return 2
        if batch.get("status") in {"cancelled", "completed", "error"}:
            return 0

        project_root = Path(str(batch["project_root"])).expanduser().resolve()
        book_ids = [int(value) for value in batch.get("book_ids") or []]
        batch.setdefault("session_strategy", "one_book_one_session")
        batch.setdefault("session_total", len(book_ids))
        batch.setdefault("session_ids", [])
        cursor = min(int(batch.get("cursor") or 0), len(book_ids))
        counts = child_counts(service, batch)
        batch["finished"] = int(batch.get("reused") or 0) + counts["terminal"]
        batch["parallel_limit"] = max_parallel_tasks()

        if cursor >= len(book_ids):
            if counts["active"]:
                batch["status"] = "waiting"
                batch["current_stage"] = "全部作品已分发，等待剩余任务完成"
                batch["message"] = (
                    f"已分发 {cursor}/{len(book_ids)} 本，"
                    f"当前仍有 {counts['active']} 个本批次任务运行"
                )
                write_json(batch_file, batch)
                time.sleep(5)
                continue
            batch["status"] = "completed"
            batch["finished"] = len(book_ids)
            batch["current_stage"] = "批量拆书调度完成"
            batch["message"] = (
                f"共处理 {len(book_ids)} 本："
                f"启动 {batch.get('started', 0)}，"
                f"复用 {batch.get('reused', 0)}，"
                f"失败 {batch.get('failed', 0)}"
            )
            write_json(batch_file, batch)
            return 0

        active_count = active_global_tasks(service, project_root)
        if active_count >= int(batch["parallel_limit"]):
            batch["status"] = "waiting"
            batch["current_stage"] = "等待全局并行空位"
            batch["message"] = (
                f"已分发 {cursor}/{len(book_ids)} 本，"
                f"当前全局 {active_count}/{batch['parallel_limit']} 个任务运行"
            )
            write_json(batch_file, batch)
            time.sleep(5)
            continue

        book_id = book_ids[cursor]
        try:
            resume_task_id = str(
                (batch.get("resume_task_ids") or {}).get(str(book_id)) or ""
            )
            if resume_task_id:
                task = service.continue_task(project_root, resume_task_id)
            else:
                task = service.create_task(
                    project_root,
                    book_id,
                    str(batch["mode"]),
                    runner_id=str(batch["runner_requested"]),
                    profile_id=str(batch["model_requested"]),
                    reasoning_effort=batch.get("reasoning_requested"),
                )
            managed_id = str(task.get("managed_task_id") or task.get("id") or "")
            if (
                managed_id
                and not managed_id.startswith("global-")
                and managed_id not in batch["child_task_ids"]
            ):
                batch["child_task_ids"].append(managed_id)
                ai_session_id = str(task.get("ai_session_id") or "")
                if (
                    ai_session_id
                    and ai_session_id not in batch["session_ids"]
                ):
                    batch["session_ids"].append(ai_session_id)
                batch["started"] = int(batch.get("started") or 0) + 1
            else:
                batch["reused"] = int(batch.get("reused") or 0) + 1
        except ValueError as exc:
            if "并行上限" in str(exc):
                batch["status"] = "waiting"
                batch["current_stage"] = "等待全局并行空位"
                batch["message"] = str(exc)
                write_json(batch_file, batch)
                time.sleep(5)
                continue
            batch["failed"] = int(batch.get("failed") or 0) + 1
            batch["message"] = f"作品 {book_id} 入队失败：{exc}"
        except RECOVERABLE_OPERATION_ERRORS as exc:  # pragma: no cover - worker safety boundary
            batch["failed"] = int(batch.get("failed") or 0) + 1
            batch["message"] = f"作品 {book_id} 入队异常：{exc}"

        batch["cursor"] = cursor + 1
        batch["status"] = "dispatching"
        batch["current_stage"] = "按并行空位持续分发"
        if not str(batch.get("message") or "").startswith("作品 "):
            batch["message"] = (
                f"已分发 {batch['cursor']}/{len(book_ids)} 本，"
                f"当前并行上限 {batch['parallel_limit']}"
            )
        write_json(batch_file, batch)
        time.sleep(0.35)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-file", required=True)
    args = parser.parse_args()
    return run(Path(args.batch_file).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
