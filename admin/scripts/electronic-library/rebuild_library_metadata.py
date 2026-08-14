#!/usr/bin/env python3
"""Rebuild exact word counts and chapter indexes for the electronic library.

The source catalog stays read-only. Exact per-book/per-chapter metadata is
written to ``全局索引/阅读目录/<catalog_id>.json`` and, in production, to
MySQL. The SQLite metrics database is retained only for explicit legacy mode.

The command is incremental by default: unchanged files with a current reader
index are reused, so an interrupted full-library run can simply be started
again.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any


from project_paths import APP_ROOT  # noqa: E402


BACKEND_ROOT = APP_ROOT / "src"
sys.path.insert(0, str(BACKEND_ROOT))

from oohstory_library.services.electronic_library import (  # noqa: E402
    DEFAULT_LIBRARY_ROOT,
    DEFAULT_RUNTIME_DIR,
    ElectronicLibraryService,
    READER_INDEX_SCHEMA_VERSION,
    _is_within,
    _read_json,
)
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="重建全库准确字数、章节数量、章节名称和阅读索引"
    )
    parser.add_argument(
        "--library-root",
        type=Path,
        default=DEFAULT_LIBRARY_ROOT,
        help="电子书库根目录，默认 electronic-library",
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=DEFAULT_RUNTIME_DIR,
        help="派生索引目录，默认 electronic-library/全局索引",
    )
    parser.add_argument(
        "--book-id",
        dest="book_ids",
        type=int,
        action="append",
        default=[],
        help="只处理指定 catalog ID；可重复传入",
    )
    parser.add_argument(
        "--retry-report",
        type=Path,
        default=None,
        help=(
            "只重扫指定旧报告中的 fallback_index 与 failures；"
            "可与 --book-id 合并"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理多少本，0 表示全部",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(4, max(os.cpu_count() or 1, 1)),
        help="并行文件扫描数，默认最多 4",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略有效缓存，强制重新扫描正文",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查缓存覆盖率，不扫描正文、不写入文件或数据库",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="存在解析降级或失败时返回非零退出码",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="每处理多少本输出一次进度，默认 50",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON 报告路径；默认写入全局索引目录",
    )
    return parser.parse_args()


def selected_rows(
    service: ElectronicLibraryService,
    book_ids: list[int],
    limit: int,
) -> list[dict[str, Any]]:
    wanted = {int(value) for value in book_ids if int(value) > 0}
    if service.infrastructure_settings.catalog_backend == "mysql":
        runtime = MySQLLibraryRuntime(
            service.infrastructure_settings,
            service.mysql_pool,
            service.redis_queue,
        )
        rows = runtime.select_done_books(wanted, limit=limit)
    elif wanted:
        placeholders = ",".join("?" for _ in wanted)
        with service._catalog_connection() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT id, source_id, title, author, category,
                           expected_size, output_path, bytes, sha256,
                           updated_at
                    FROM books
                    WHERE id IN ({placeholders})
                      AND status='done'
                      AND output_path IS NOT NULL
                    ORDER BY id
                    """,
                    sorted(wanted),
                )
            ]
    else:
        rows = [dict(row) for row in service._iter_downloaded_books()]
    if wanted:
        found = {int(row["id"]) for row in rows}
        missing = sorted(wanted - found)
        if missing:
            raise ValueError(
                "以下 catalog ID 不存在、未下载完成或没有正文："
                + "、".join(str(value) for value in missing)
            )
    if limit > 0 and service.infrastructure_settings.catalog_backend != "mysql":
        rows = rows[:limit]
    return rows


def retry_report_ids(path: Path) -> list[int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("重扫报告必须是 JSON 对象")
    values: set[int] = set()
    for item in payload.get("anomalies") or []:
        if (
            isinstance(item, dict)
            and item.get("type") == "fallback_index"
            and int(item.get("catalog_id") or 0) > 0
        ):
            values.add(int(item["catalog_id"]))
    for item in payload.get("failures") or []:
        if (
            isinstance(item, dict)
            and int(item.get("catalog_id") or 0) > 0
        ):
            values.add(int(item["catalog_id"]))
    if not values:
        raise ValueError("指定报告中没有 fallback_index 或 failures")
    return sorted(values)


def source_path_for(
    service: ElectronicLibraryService,
    row: dict[str, Any],
) -> Path:
    source_path = Path(str(row.get("output_path") or "")).expanduser().resolve()
    allowed_roots = [service.books_root]
    if service.infrastructure_settings.catalog_backend == "mysql":
        allowed_roots.append(service.infrastructure_settings.object_root)
    if not any(_is_within(source_path, root) for root in allowed_roots):
        raise ValueError("正文路径不在电子书库书籍目录内")
    if not source_path.is_file():
        raise FileNotFoundError(f"正文文件不存在：{source_path}")
    return source_path


def cached_state(
    service: ElectronicLibraryService,
    row: dict[str, Any],
) -> str:
    try:
        source_path = source_path_for(service, row)
        cached = _read_json(service._reader_index_path(int(row["id"])), {})
        if not cached:
            return "missing"
        return (
            "current"
            if service._reader_index_cache_valid(cached, source_path)
            else "stale"
        )
    except Exception:
        return "invalid_source"


def scan_one(
    service: ElectronicLibraryService,
    row: dict[str, Any],
    force: bool,
) -> dict[str, Any]:
    catalog_id = int(row["id"])
    source_path = source_path_for(service, row)
    payload, rebuilt = service.rebuild_reader_index(
        catalog_id,
        source_path,
        force=force,
    )
    return {
        "catalog_id": catalog_id,
        "title": str(row.get("title") or source_path.stem),
        "source_path": str(source_path),
        "rebuilt": rebuilt,
        "payload": payload,
    }


def existing_metrics(
    connection: sqlite3.Connection,
) -> dict[int, tuple[int, int, int, int]]:
    return {
        int(row["catalog_id"]): (
            int(row["word_count"] or 0),
            int(row["chapter_count"] or 0),
            int(row["section_count"] or 0),
            int(row["schema_version"] or 0),
        )
        for row in connection.execute(
            """
            SELECT catalog_id, word_count, chapter_count, section_count,
                   schema_version
            FROM book_metrics
            """
        )
    }


def main() -> int:
    args = parse_args()
    if args.limit < 0:
        raise SystemExit("--limit 不能小于 0")
    workers = min(max(int(args.workers), 1), 16)
    progress_every = max(int(args.progress_every), 1)
    library_root = args.library_root.expanduser().resolve()
    runtime_dir = args.runtime_dir.expanduser().resolve()
    retry_report = (
        args.retry_report.expanduser().resolve()
        if args.retry_report
        else None
    )
    selected_book_ids = list(args.book_ids)
    if retry_report:
        selected_book_ids.extend(retry_report_ids(retry_report))
    report_path = (
        args.report.expanduser().resolve()
        if args.report
        else runtime_dir
        / (
            "library-metadata-retry-report.json"
            if retry_report
            else "library-metadata-rebuild-report.json"
        )
    )
    service = ElectronicLibraryService(library_root, runtime_dir)
    mysql_runtime = (
        MySQLLibraryRuntime(
            service.infrastructure_settings,
            service.mysql_pool,
            service.redis_queue,
        )
        if service.infrastructure_settings.catalog_backend == "mysql"
        else None
    )
    rows = selected_rows(service, selected_book_ids, args.limit)
    started_at = now()

    if args.dry_run:
        states = {"current": 0, "stale": 0, "missing": 0, "invalid_source": 0}
        for row in rows:
            states[cached_state(service, row)] += 1
        payload = {
            "status": "dry-run",
            "schema_version": READER_INDEX_SCHEMA_VERSION,
            "started_at": started_at,
            "finished_at": now(),
            "selected": len(rows),
            "cache": states,
            "message": (
                f"共检查 {len(rows)} 本：有效 {states['current']}，"
                f"待重建 {states['stale'] + states['missing']}，"
                f"正文异常 {states['invalid_source']}"
            ),
        }
        print(payload["message"], flush=True)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if args.strict and states["invalid_source"] else 0

    # Exact per-book rebuilds are safe to run beside the long full-library
    # sweep: they write distinct reader-index files and the selected metadata
    # backend serializes the short metrics upsert. Give imports a scoped lock
    # so a multi-hour global
    # rebuild cannot leave a freshly downloaded book without metadata.
    if selected_book_ids:
        if retry_report:
            scope = f"retry-{retry_report.stat().st_mtime_ns}"
        else:
            scoped_ids = sorted(set(selected_book_ids))
            scope_digest = hashlib.sha256(
                ",".join(str(value) for value in scoped_ids).encode("ascii")
            ).hexdigest()[:16]
            scope = (
                f"{len(scoped_ids)}-{scoped_ids[0]}-{scoped_ids[-1]}-"
                f"{scope_digest}"
            )
        lock_path = runtime_dir / f".library-metadata-rebuild-book-{scope}.lock"
    else:
        lock_path = runtime_dir / ".library-metadata-rebuild.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise SystemExit("已有书库元数据重建任务正在运行") from exc

    counters = {
        "processed": 0,
        "rebuilt": 0,
        "cache_hits": 0,
        "metrics_changed": 0,
        "metrics_unchanged": 0,
        "fallback_indexes": 0,
        "failed": 0,
        "word_count": 0,
        "chapter_count": 0,
        "section_count": 0,
    }
    anomalies: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    try:
        # Exact content metrics use an independent small database.  This
        # avoids contending with long-running tone/plot index transactions.
        metrics_context = (
            service._content_metrics_connection()
            if mysql_runtime is None
            else None
        )
        if mysql_runtime is not None:
            previous = mysql_runtime.existing_reader_metrics()
        else:
            previous = existing_metrics(metrics_context)
        with (
            metrics_context
            if metrics_context is not None
            else contextlib.nullcontext(None)
        ) as metrics_conn:
            if selected_book_ids:
                if metrics_conn is not None:
                    metrics_conn.execute("PRAGMA busy_timeout=300000")
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(scan_one, service, row, args.force): row
                    for row in rows
                }
                for future in as_completed(futures):
                    row = futures[future]
                    catalog_id = int(row["id"])
                    try:
                        result = future.result()
                        reader_index = result["payload"]
                        if mysql_runtime is not None:
                            mysql_runtime.upsert_reader_metrics(
                                catalog_id,
                                reader_index,
                            )
                        else:
                            service.sync_reader_metrics(
                                catalog_id,
                                reader_index,
                                metrics_connection=metrics_conn,
                            )
                        # Do not hold the legacy metadata write lock while the
                        # remaining books are scanned. Scheduled imports run exact
                        # per-book rebuilds in parallel with a full sweep.
                        if metrics_conn is not None:
                            metrics_conn.commit()
                        exact_metrics = (
                            int(reader_index["word_count"]),
                            int(reader_index["chapter_count"]),
                            int(reader_index["section_count"]),
                            int(reader_index["schema_version"]),
                        )
                        if previous.get(catalog_id) != exact_metrics:
                            counters["metrics_changed"] += 1
                        else:
                            counters["metrics_unchanged"] += 1
                        if result["rebuilt"]:
                            counters["rebuilt"] += 1
                        else:
                            counters["cache_hits"] += 1
                        if reader_index["index_status"] != "exact":
                            counters["fallback_indexes"] += 1
                            anomalies.append(
                                {
                                    "catalog_id": catalog_id,
                                    "title": result["title"],
                                    "type": "fallback_index",
                                    "message": "未识别到可靠章节标题，保留分片阅读且章节数记为 0",
                                    "section_count": int(
                                        reader_index["section_count"]
                                    ),
                                }
                            )
                        counters["word_count"] += int(reader_index["word_count"])
                        counters["chapter_count"] += int(
                            reader_index["chapter_count"]
                        )
                        counters["section_count"] += int(
                            reader_index["section_count"]
                        )
                    except Exception as exc:
                        counters["failed"] += 1
                        failures.append(
                            {
                                "catalog_id": catalog_id,
                                "title": str(row.get("title") or ""),
                                "error": str(exc)[:1000],
                            }
                        )
                    counters["processed"] += 1
                    if (
                        counters["processed"] % progress_every == 0
                        or counters["processed"] == len(rows)
                    ):
                        print(
                            f"[{counters['processed']}/{len(rows)}] "
                            f"重建 {counters['rebuilt']}，"
                            f"缓存 {counters['cache_hits']}，"
                            f"降级 {counters['fallback_indexes']}，"
                            f"失败 {counters['failed']}",
                            flush=True,
                        )
            if metrics_conn is not None:
                metrics_conn.commit()
    finally:
        fcntl.flock(lock_handle, fcntl.LOCK_UN)
        lock_handle.close()

    anomalies.sort(key=lambda item: int(item["catalog_id"]))
    failures.sort(key=lambda item: int(item["catalog_id"]))
    report = {
        "status": "completed" if not failures else "completed_with_errors",
        "schema_version": READER_INDEX_SCHEMA_VERSION,
        "started_at": started_at,
        "finished_at": now(),
        "library_root": str(library_root),
        "runtime_dir": str(runtime_dir),
        "selected": len(rows),
        "workers": workers,
        "force": bool(args.force),
        "retry_source_report": str(retry_report) if retry_report else "",
        **counters,
        "anomalies": anomalies,
        "failures": failures,
    }
    atomic_json(report_path, report)
    print(
        f"完成：{counters['processed']} 本，准确章节 "
        f"{counters['chapter_count']:,}，准确字数 "
        f"{counters['word_count']:,}；报告：{report_path}",
        flush=True,
    )
    if args.strict and (
        counters["failed"]
        or counters["fallback_indexes"]
    ):
        return 2
    return 1 if counters["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
