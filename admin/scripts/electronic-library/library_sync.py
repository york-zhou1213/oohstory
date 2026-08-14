#!/usr/bin/env python3
"""Reconcile, de-duplicate, and synchronize the shared electronic library."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


from project_paths import APP_ROOT  # noqa: E402


LIBRARY_ROOT = APP_ROOT / "electronic-library"
BOOKS_ROOT = LIBRARY_ROOT / "书籍"
CATALOG_PATH = LIBRARY_ROOT / "catalog.sqlite3"
STATUS_PATH = LIBRARY_ROOT / "全局索引" / "library-sync-status.json"
LOCK_PATH = LIBRARY_ROOT / ".library-sync.lock"
BACKUP_ROOT = LIBRARY_ROOT / "backups"
BACKEND_ROOT = APP_ROOT / "src"
sys.path.insert(0, str(BACKEND_ROOT))

from oohstory_library.services.library_catalog import (  # noqa: E402
    book_identity,
    inspect_book_text,
    preference_score,
    source_library_id,
)
from oohstory_library.services.library_database import LibraryInfrastructureSettings  # noqa: E402
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CATALOG_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS duplicate_books (
          duplicate_book_id INTEGER PRIMARY KEY,
          duplicate_source_id TEXT,
          kept_book_id INTEGER NOT NULL,
          kept_source_id TEXT,
          title TEXT,
          author TEXT,
          removed_path TEXT,
          removed_bytes INTEGER NOT NULL DEFAULT 0,
          removed_sha256 TEXT,
          duplicate_metrics TEXT,
          kept_metrics TEXT,
          reason TEXT NOT NULL,
          removed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS library_sync_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          status TEXT NOT NULL,
          summary TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_books_source_id
          ON books(source_id);
        CREATE INDEX IF NOT EXISTS idx_books_output_path
          ON books(output_path);
        CREATE INDEX IF NOT EXISTS idx_books_sha256
          ON books(sha256);
        """
    )
    conn.commit()


def backup_catalog(conn: sqlite3.Connection, label: str) -> Path:
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_ROOT / f"catalog-before-{label}-{stamp}.sqlite3"
    with sqlite3.connect(target) as backup:
        conn.backup(backup)
    return target


def is_controlled_book_path(path: Path) -> bool:
    try:
        path.resolve().relative_to(BOOKS_ROOT.resolve())
        return path.is_file()
    except (OSError, ValueError):
        return False


def reconcile_files(conn: sqlite3.Connection) -> dict[str, int]:
    missing_ids: list[int] = []
    repaired_sizes = 0
    for row in conn.execute(
        """
        SELECT id, output_path, bytes
        FROM books
        WHERE status='done' AND output_path IS NOT NULL
        """
    ):
        path = Path(str(row["output_path"])).expanduser()
        if not is_controlled_book_path(path):
            missing_ids.append(int(row["id"]))
            continue
        size = path.stat().st_size
        if size != int(row["bytes"] or 0):
            conn.execute(
                "UPDATE books SET bytes=?, updated_at=? WHERE id=?",
                (size, now(), int(row["id"])),
            )
            repaired_sizes += 1
    if missing_ids:
        placeholders = ",".join("?" for _ in missing_ids)
        conn.execute(
            f"""
            UPDATE books
            SET status='failed', attempts=0, output_path=NULL, bytes=NULL,
                sha256=NULL, last_error='正文文件缺失，等待定时同步恢复',
                updated_at=?
            WHERE id IN ({placeholders})
            """,
            (now(), *missing_ids),
        )
    conn.commit()
    return {"missing_files": len(missing_ids), "repaired_sizes": repaired_sizes}


def _duplicate_groups(conn: sqlite3.Connection) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT id, source_id, title, author, category, output_path, bytes,
               sha256, updated_at
        FROM books
        WHERE status='done' AND output_path IS NOT NULL
        ORDER BY id
        """
    ):
        identity = book_identity(row["title"], row["author"])
        if not identity[0] or identity[1] in {"", "未知作者", "作者未知"}:
            continue
        grouped[identity].append(dict(row))
    return [rows for rows in grouped.values() if len(rows) > 1]


def deduplicate(conn: sqlite3.Connection) -> dict[str, Any]:
    groups = _duplicate_groups(conn)
    if not groups:
        return {
            "groups": 0,
            "removed_books": 0,
            "freed_bytes": 0,
            "backup": "",
        }

    decisions: list[dict[str, Any]] = []
    for rows in groups:
        candidates: list[tuple[tuple[int, ...], dict[str, Any], dict[str, Any], Path]] = []
        for row in rows:
            path = Path(str(row["output_path"])).expanduser().resolve()
            if not is_controlled_book_path(path):
                continue
            metrics = inspect_book_text(path)
            candidates.append(
                (preference_score(row, metrics, path), row, metrics, path)
            )
        if len(candidates) < 2:
            continue
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, kept_row, kept_metrics, _ = candidates[0]
        for _, duplicate_row, duplicate_metrics, duplicate_path in candidates[1:]:
            decisions.append(
                {
                    "kept": kept_row,
                    "kept_metrics": kept_metrics,
                    "duplicate": duplicate_row,
                    "duplicate_metrics": duplicate_metrics,
                    "path": duplicate_path,
                }
            )

    if not decisions:
        return {
            "groups": 0,
            "removed_books": 0,
            "freed_bytes": 0,
            "backup": "",
        }

    backup = backup_catalog(conn, "dedup")
    removed_paths: list[tuple[Path, int]] = []
    stamp = now()
    with conn:
        for item in decisions:
            kept = item["kept"]
            duplicate = item["duplicate"]
            path = item["path"]
            size = path.stat().st_size
            same_hash = bool(
                duplicate.get("sha256")
                and duplicate.get("sha256") == kept.get("sha256")
            )
            reason = (
                "正文哈希完全相同"
                if same_hash
                else "同书多版本，仅保留章节数/正文完整度最高版本"
            )
            conn.execute(
                """
                INSERT INTO duplicate_books (
                    duplicate_book_id, duplicate_source_id, kept_book_id,
                    kept_source_id, title, author, removed_path, removed_bytes,
                    removed_sha256, duplicate_metrics, kept_metrics, reason,
                    removed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(duplicate_book_id) DO UPDATE SET
                    kept_book_id=excluded.kept_book_id,
                    kept_source_id=excluded.kept_source_id,
                    removed_path=excluded.removed_path,
                    removed_bytes=excluded.removed_bytes,
                    removed_sha256=excluded.removed_sha256,
                    duplicate_metrics=excluded.duplicate_metrics,
                    kept_metrics=excluded.kept_metrics,
                    reason=excluded.reason,
                    removed_at=excluded.removed_at
                """,
                (
                    int(duplicate["id"]),
                    str(duplicate.get("source_id") or ""),
                    int(kept["id"]),
                    str(kept.get("source_id") or ""),
                    str(duplicate.get("title") or ""),
                    str(duplicate.get("author") or ""),
                    str(path),
                    size,
                    str(duplicate.get("sha256") or ""),
                    json.dumps(item["duplicate_metrics"], ensure_ascii=False),
                    json.dumps(item["kept_metrics"], ensure_ascii=False),
                    reason,
                    stamp,
                ),
            )
            conn.execute(
                """
                UPDATE books
                SET status='duplicate', output_path=NULL, bytes=NULL,
                    sha256=NULL, last_error=?, updated_at=?
                WHERE id=?
                """,
                (
                    f"重复书目；保留 catalog_id={kept['id']} "
                    f"source_id={kept.get('source_id') or ''}",
                    stamp,
                    int(duplicate["id"]),
                ),
            )
            removed_paths.append((path, size))

    freed = 0
    for path, size in removed_paths:
        if is_controlled_book_path(path):
            path.unlink()
            freed += size
    return {
        "groups": len({int(item["kept"]["id"]) for item in decisions}),
        "removed_books": len(decisions),
        "freed_bytes": freed,
        "backup": str(backup),
    }


def retry_failed(
    conn: sqlite3.Connection,
    *,
    all_failed: bool,
    after_hours: int,
) -> int:
    if all_failed:
        cursor = conn.execute(
            """
            UPDATE books
            SET attempts=0, updated_at=?
            WHERE status='failed'
            """,
            (now(),),
        )
    else:
        cutoff = (
            datetime.now() - timedelta(hours=max(after_hours, 1))
        ).strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute(
            """
            UPDATE books
            SET attempts=0, updated_at=?
            WHERE status='failed' AND attempts >= 8 AND updated_at <= ?
            """,
            (now(), cutoff),
        )
    conn.commit()
    return int(cursor.rowcount)


async def scan_fanqie_exports(
    conn: sqlite3.Connection,
    *,
    refresh_tracked: bool = False,
) -> dict[str, int]:
    from oohstory_library.services.ai_service import get_ai_service
    from oohstory_library.services.electronic_library import ElectronicLibraryService

    service = ElectronicLibraryService()
    state_path = service.fanqie_downloader.state_path
    if not state_path.is_file():
        return {"found": 0, "imported": 0, "skipped": 0, "failed": 0}
    result = {
        "found": 0,
        "imported": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
    }

    async def current_book_status(book_id: str, title: str) -> str:
        """Read the official search status without blocking a valid refresh."""
        try:
            rows = await asyncio.to_thread(
                service.fanqie_downloader.search,
                str(title or book_id),
                limit=0,
            )
        except Exception:
            return ""
        for candidate in rows:
            if str(candidate.get("remote_id") or "") == book_id:
                return str(candidate.get("book_status") or "")
        return ""

    if refresh_tracked:
        columns = {
            str(item["name"])
            for item in conn.execute("PRAGMA table_info(books)")
        }
        status_filter = (
            "AND book_status='连载中'"
            if "book_status" in columns
            else ""
        )
        tracked = conn.execute(
            f"""
            SELECT id,source_id,title,author
            FROM books
            WHERE status='done' AND source_id LIKE 'fanqie-%'
              {status_filter}
            ORDER BY id
            """
        ).fetchall()
        for row in tracked:
            book_id = str(row["source_id"]).removeprefix("fanqie-")
            try:
                book_status = await current_book_status(
                    book_id,
                    str(row["title"] or ""),
                )
                await service.refresh_fanqie_incremental(
                    catalog_id=int(row["id"]),
                    book_id=book_id,
                    title=str(row["title"] or ""),
                    author=str(row["author"] or ""),
                    book_status=book_status,
                )
                result["updated"] += 1
            except Exception:
                result["failed"] += 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    history = state.get("history") or []
    result["found"] = len(history)
    for item in history:
        book_id = str(item.get("book_id") or "").strip()
        source_id = f"fanqie-{book_id}"
        existing = conn.execute(
            """
            SELECT id, status, output_path FROM books
            WHERE source_id=? LIMIT 1
            """,
            (source_id,),
        ).fetchone()
        if (
            existing
            and existing["status"] == "done"
            and existing["output_path"]
            and Path(str(existing["output_path"])).is_file()
        ):
            result["skipped"] += 1
            continue
        try:
            book_status = await current_book_status(
                book_id,
                str(item.get("book_name") or ""),
            )
            await service.import_fanqie_export(
                book_id=book_id,
                source_path=Path(str(item.get("save_path") or "")),
                title=str(item.get("book_name") or ""),
                author=str(item.get("author") or ""),
                book_status=book_status,
                ai_service=get_ai_service(),
            )
            result["imported"] += 1
        except Exception:
            result["failed"] += 1
    return result


async def scan_fanqie_exports_mysql(
    runtime: MySQLLibraryRuntime,
    *,
    refresh_tracked: bool = False,
) -> dict[str, int]:
    from oohstory_library.services.ai_service import get_ai_service
    from oohstory_library.services.electronic_library import ElectronicLibraryService

    service = ElectronicLibraryService()
    result = {
        "found": 0,
        "imported": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
    }
    if refresh_tracked:
        with runtime.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id,source_id,title,author
                    FROM books
                    WHERE status='done' AND source_id LIKE 'fanqie-%%'
                      AND book_status='连载中'
                    ORDER BY id
                    """
                )
                tracked = [dict(row) for row in cursor.fetchall()]
        for row in tracked:
            try:
                await service.refresh_fanqie_incremental(
                    catalog_id=int(row["id"]),
                    book_id=str(row["source_id"]).removeprefix("fanqie-"),
                    title=str(row["title"] or ""),
                    author=str(row["author"] or ""),
                    book_status="连载中",
                )
                result["updated"] += 1
            except Exception:
                result["failed"] += 1
    state_path = service.fanqie_downloader.state_path
    if not state_path.is_file():
        return result
    history = json.loads(
        state_path.read_text(encoding="utf-8")
    ).get("history") or []
    result["found"] = len(history)
    for item in history:
        book_id = str(item.get("book_id") or "").strip()
        source_id = f"fanqie-{book_id}"
        with runtime.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id,status,body_object_key,legacy_output_path
                    FROM books WHERE source_id=%s LIMIT 1
                    """,
                    (source_id,),
                )
                existing = cursor.fetchone()
        if existing and str(existing["status"]) == "done":
            result["skipped"] += 1
            continue
        try:
            await service.import_fanqie_export(
                book_id=book_id,
                source_path=Path(str(item.get("save_path") or "")),
                title=str(item.get("book_name") or ""),
                author=str(item.get("author") or ""),
                book_status=str(item.get("book_status") or ""),
                ai_service=get_ai_service(),
            )
            result["imported"] += 1
        except Exception:
            result["failed"] += 1
    return result


def snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    libraries = {
        "local": {"total": 0, "downloaded": 0, "failed": 0},
        "fanqie": {"total": 0, "downloaded": 0, "failed": 0},
    }
    raw_total = duplicates = 0
    book_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(books)")
    }
    library_expression = (
        "COALESCE(NULLIF(library_id, ''), 'local')"
        if "library_id" in book_columns
        else (
            "CASE WHEN LOWER(COALESCE(source_id, '')) LIKE 'fanqie-%' "
            "THEN 'fanqie' ELSE 'local' END"
        )
    )
    for row in conn.execute(
        f"""
        SELECT {library_expression} AS library_id,
               status, COUNT(*) count
        FROM books
        GROUP BY library_id, status
        """
    ):
        count = int(row["count"])
        raw_total += count
        if row["status"] == "duplicate":
            duplicates += count
            continue
        library = libraries[str(row["library_id"])]
        library["total"] += count
        if row["status"] == "done":
            library["downloaded"] += count
        elif row["status"] == "failed":
            library["failed"] += count
    return {
        "raw_total": raw_total,
        "unique_total": raw_total - duplicates,
        "duplicates": duplicates,
        "downloaded": sum(item["downloaded"] for item in libraries.values()),
        "libraries": libraries,
    }


def run_mysql_sync(args: argparse.Namespace) -> dict[str, Any]:
    runtime = MySQLLibraryRuntime()
    run_id = runtime.start_sync_run()
    payload: dict[str, Any] = {
        "status": "running",
        "running": True,
        "started_at": now(),
        "pid": os.getpid(),
    }
    atomic_json(STATUS_PATH, payload)
    try:
        if args.scan_fanqie:
            payload["fanqie_scan"] = asyncio.run(
                scan_fanqie_exports_mysql(
                    runtime,
                    refresh_tracked=args.refresh_tracked_fanqie,
                )
            )
        if args.reconcile:
            payload["reconcile"] = runtime.reconcile_body_objects()
        if args.dedupe:
            payload["dedupe"] = runtime.deduplicate_done_books()
        payload["failed_requeued"] = (
            0
            if args.no_retry
            else runtime.retry_failed(
                all_failed=args.retry_all_failed,
                after_hours=args.retry_after_hours,
            )
        )
        payload["catalog"] = runtime.sync_snapshot()
        payload.update(
            {
                "status": "completed",
                "running": False,
                "finished_at": now(),
                "message": (
                    f"书库同步预检完成：唯一书目 "
                    f"{payload['catalog']['unique_total']}，正文 "
                    f"{payload['catalog']['downloaded']}，重复 "
                    f"{payload['catalog']['duplicates']}"
                ),
            }
        )
        runtime.finish_sync_run(
            run_id,
            status="completed",
            summary=payload,
        )
    except Exception as exc:
        payload.update(
            {
                "status": "error",
                "running": False,
                "finished_at": now(),
                "message": str(exc)[:500],
            }
        )
        runtime.finish_sync_run(run_id, status="error", summary=payload)
        atomic_json(STATUS_PATH, payload)
        raise
    atomic_json(STATUS_PATH, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--dedupe", action="store_true")
    parser.add_argument("--scan-fanqie", action="store_true")
    parser.add_argument("--refresh-tracked-fanqie", action="store_true")
    parser.add_argument("--retry-all-failed", action="store_true")
    parser.add_argument("--retry-after-hours", type=int, default=24)
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="只做扫描/核对，不把本地来源失败记录重新加入下载队列",
    )
    args = parser.parse_args()

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if LibraryInfrastructureSettings.from_env().catalog_backend == "mysql":
            print(
                json.dumps(
                    run_mysql_sync(args),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        started = now()
        with connect() as conn:
            run_id = conn.execute(
                "INSERT INTO library_sync_runs(started_at,status) VALUES (?, 'running')",
                (started,),
            ).lastrowid
            conn.commit()
            payload: dict[str, Any] = {
                "status": "running",
                "running": True,
                "started_at": started,
                "pid": os.getpid(),
            }
            atomic_json(STATUS_PATH, payload)
            try:
                if args.scan_fanqie:
                    payload["fanqie_scan"] = asyncio.run(
                        scan_fanqie_exports(
                            conn,
                            refresh_tracked=args.refresh_tracked_fanqie,
                        )
                    )
                if args.reconcile:
                    payload["reconcile"] = reconcile_files(conn)
                if args.dedupe:
                    payload["dedupe"] = deduplicate(conn)
                payload["failed_requeued"] = (
                    0
                    if args.no_retry
                    else retry_failed(
                        conn,
                        all_failed=args.retry_all_failed,
                        after_hours=args.retry_after_hours,
                    )
                )
                payload["catalog"] = snapshot(conn)
                payload.update(
                    {
                        "status": "completed",
                        "running": False,
                        "finished_at": now(),
                        "message": (
                            f"书库同步预检完成：唯一书目 "
                            f"{payload['catalog']['unique_total']}，正文 "
                            f"{payload['catalog']['downloaded']}，重复 "
                            f"{payload['catalog']['duplicates']}"
                        ),
                    }
                )
                conn.execute(
                    """
                    UPDATE library_sync_runs
                    SET finished_at=?, status='completed', summary=?
                    WHERE id=?
                    """,
                    (
                        payload["finished_at"],
                        json.dumps(payload, ensure_ascii=False),
                        run_id,
                    ),
                )
                conn.commit()
            except Exception as exc:
                payload.update(
                    {
                        "status": "error",
                        "running": False,
                        "finished_at": now(),
                        "message": str(exc)[:500],
                    }
                )
                conn.execute(
                    """
                    UPDATE library_sync_runs
                    SET finished_at=?, status='error', summary=?
                    WHERE id=?
                    """,
                    (
                        payload["finished_at"],
                        json.dumps(payload, ensure_ascii=False),
                        run_id,
                    ),
                )
                conn.commit()
                atomic_json(STATUS_PATH, payload)
                raise
            atomic_json(STATUS_PATH, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
