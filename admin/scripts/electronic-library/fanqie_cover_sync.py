#!/usr/bin/env python3
"""Synchronize official Fanqie covers for true ``fanqie-*`` catalog rows.

The logical Fanqie library also contains books imported from authorized
websites.  Those rows are deliberately excluded here and are handled by the
source-routed online-cover worker.  Only direct Fanqie rows may use the pinned
official desktop client.  Only a terminal safe failure is handed to the shared
title-based AI generation fallback; no unverified remote cover is accepted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any


from project_paths import APP_ROOT  # noqa: E402


BACKEND_ROOT = APP_ROOT / "src"
LIBRARY_ROOT = APP_ROOT / "electronic-library"
CATALOG_PATH = LIBRARY_ROOT / "catalog.sqlite3"
MEMBERSHIP_PATH = (
    LIBRARY_ROOT / "全局索引" / "library_memberships.sqlite3"
)
COVER_ROOT = LIBRARY_ROOT / "封面"
COVER_INDEX_PATH = Path(
    os.getenv(
        "WEBNOVEL_COVER_INDEX_PATH",
        str(LIBRARY_ROOT / "全局索引" / "cover_index.sqlite3"),
    )
).expanduser().resolve()
sys.path.insert(0, str(BACKEND_ROOT))

from oohstory_library.services.fanqie_downloader_bridge import FanqieDownloaderBridge  # noqa: E402
from oohstory_library.services.cover_failure_policy import (  # noqa: E402
    MAX_SOURCE_COVER_ATTEMPTS,
    should_generate_ai_fallback,
)
from oohstory_library.services.library_covers import sync_fanqie_cover  # noqa: E402
from oohstory_library.services.library_database import LibraryInfrastructureSettings  # noqa: E402
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402
import txt80_cover_sync as source_cover_sync  # noqa: E402


JOB_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;
CREATE TABLE IF NOT EXISTS fanqie_cover_jobs (
  catalog_id INTEGER PRIMARY KEY,
  catalog_source_id TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT NOT NULL,
  book_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  resolved_by TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fanqie_cover_jobs_status
  ON fanqie_cover_jobs(status, attempts, catalog_id);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def identity(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def direct_book_id(source_id: Any, detail_url: Any) -> str:
    source = str(source_id or "").strip()
    match = re.fullmatch(r"fanqie-(\d{10,24})", source)
    if match:
        return match.group(1)
    page = re.fullmatch(
        r"https://(?:www\.)?fanqienovel\.com/page/(\d{10,24})",
        str(detail_url or "").strip(),
    )
    return page.group(1) if page else ""


def connect_jobs() -> sqlite3.Connection:
    COVER_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(COVER_INDEX_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(JOB_SCHEMA)
    return conn


def fanqie_catalog_rows() -> list[dict[str, Any]]:
    if LibraryInfrastructureSettings.from_env().catalog_backend == "mysql":
        return MySQLLibraryRuntime().fanqie_catalog_rows()
    catalog_uri = f"{CATALOG_PATH.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(catalog_uri, uri=True, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        if MEMBERSHIP_PATH.exists():
            conn.execute(
                "ATTACH DATABASE ? AS membership",
                (str(MEMBERSHIP_PATH.resolve()),),
            )
            membership = (
                "COALESCE(("
                "SELECT target_library FROM "
                "membership.catalog_library_overrides m "
                "WHERE m.catalog_id=books.id"
                "), CASE WHEN lower(COALESCE(source_id,'')) "
                "LIKE 'fanqie-%' THEN 'fanqie' ELSE 'local' END)"
            )
        else:
            membership = (
                "CASE WHEN lower(COALESCE(source_id,'')) LIKE "
                "'fanqie-%' THEN 'fanqie' ELSE 'local' END"
            )
        rows = conn.execute(
            f"""
            SELECT id AS catalog_id, COALESCE(source_id,'') AS source_id,
                   COALESCE(title,'') AS title,
                   COALESCE(author,'') AS author,
                   COALESCE(detail_url,'') AS detail_url
            FROM books
            WHERE status != 'duplicate' AND {membership}='fanqie'
              AND (
                lower(COALESCE(source_id,'')) LIKE 'fanqie-%'
                OR detail_url LIKE 'https://fanqienovel.com/page/%'
                OR detail_url LIKE 'https://www.fanqienovel.com/page/%'
              )
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def seed_jobs() -> int:
    rows = fanqie_catalog_rows()
    if LibraryInfrastructureSettings.from_env().catalog_backend == "mysql":
        for row in rows:
            row["book_id"] = direct_book_id(
                row["source_id"],
                row["detail_url"],
            )
        return MySQLLibraryRuntime().seed_fanqie_cover_jobs(rows)
    with connect_jobs() as conn:
        before = conn.total_changes
        for row in rows:
            book_id = direct_book_id(row["source_id"], row["detail_url"])
            current = conn.execute(
                """
                SELECT catalog_source_id,title,author,book_id,status
                FROM fanqie_cover_jobs WHERE catalog_id=?
                """,
                (row["catalog_id"],),
            ).fetchone()
            identity_changed = bool(
                current
                and (
                    str(current["catalog_source_id"]) != row["source_id"]
                    or str(current["title"]) != row["title"]
                    or str(current["author"]) != row["author"]
                )
            )
            conn.execute(
                """
                INSERT INTO fanqie_cover_jobs
                  (catalog_id,catalog_source_id,title,author,book_id,status,
                   updated_at)
                VALUES (?,?,?,?,?,'pending',?)
                ON CONFLICT(catalog_id) DO UPDATE SET
                  catalog_source_id=excluded.catalog_source_id,
                  title=excluded.title,
                  author=excluded.author,
                  book_id=CASE
                    WHEN excluded.book_id != '' THEN excluded.book_id
                    WHEN ? THEN NULL
                    ELSE fanqie_cover_jobs.book_id
                  END,
                  status=CASE
                    WHEN ? THEN 'pending'
                    ELSE fanqie_cover_jobs.status
                  END,
                  attempts=CASE WHEN ? THEN 0 ELSE attempts END,
                  last_error=CASE WHEN ? THEN NULL ELSE last_error END,
                  updated_at=excluded.updated_at
                """,
                (
                    row["catalog_id"],
                    row["source_id"],
                    row["title"],
                    row["author"],
                    book_id,
                    now(),
                    int(identity_changed),
                    int(identity_changed),
                    int(identity_changed),
                    int(identity_changed),
                ),
            )
        conn.commit()
        return conn.total_changes - before


def candidate_book_ids(
    bridge: FanqieDownloaderBridge,
    row: sqlite3.Row,
) -> list[tuple[str, str]]:
    stored = str(row["book_id"] or "").strip()
    if stored:
        return [(stored, str(row["resolved_by"] or "stored_binding"))]
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    queries = [str(row["title"] or "").strip()]
    author = str(row["author"] or "").strip()
    if author:
        queries.append(author)
    expected_title = identity(row["title"])
    expected_author = identity(author)
    search_errors: list[str] = []
    for query in queries:
        if len(query) < 2:
            continue
        try:
            results = bridge.search(query, limit=0, timeout=120)
        except Exception as exc:
            search_errors.append(
                f"{query}: {type(exc).__name__}: {str(exc)[:200]}"
            )
            continue
        for item in results:
            book_id = str(item.get("remote_id") or "").strip()
            if not re.fullmatch(r"\d{10,24}", book_id) or book_id in seen:
                continue
            item_title = identity(item.get("title"))
            item_author = identity(item.get("author"))
            if (
                item_title == expected_title
                or (
                    expected_author
                    and item_author == expected_author
                )
            ):
                seen.add(book_id)
                candidates.append((book_id, f"official_search:{query}"))
    if not candidates and search_errors:
        raise RuntimeError(" | ".join(search_errors)[:800])
    return candidates


def process_job(
    bridge: FanqieDownloaderBridge,
    row: sqlite3.Row,
    *,
    force: bool,
    runtime: MySQLLibraryRuntime | None = None,
) -> dict[str, Any]:
    candidates = candidate_book_ids(bridge, row)
    if not candidates:
        raise ValueError("番茄官方搜索没有找到可机械验真的同名/同作者作品")
    errors: list[str] = []
    for book_id, resolved_by in candidates:
        try:
            result = sync_fanqie_cover(
                catalog_id=int(row["catalog_id"]),
                book_id=book_id,
                title=str(row["title"]),
                author=str(row["author"]),
                catalog_source_id=str(row["catalog_source_id"]),
                cover_root=COVER_ROOT,
                cover_index_path=COVER_INDEX_PATH,
                force=force,
            )
            if runtime is not None:
                runtime.finish_fanqie_cover_job(
                    row,
                    book_id=book_id,
                    resolved_by=resolved_by,
                )
            else:
                with connect_jobs() as conn:
                    conn.execute(
                        """
                        UPDATE fanqie_cover_jobs
                        SET book_id=?,status='done',attempts=attempts+1,
                            last_error=NULL,resolved_by=?,updated_at=?
                        WHERE catalog_id=?
                        """,
                        (
                            book_id,
                            resolved_by,
                            now(),
                            int(row["catalog_id"]),
                        ),
                    )
                    conn.commit()
            return {
                **result,
                "catalog_id": int(row["catalog_id"]),
                "book_id": book_id,
            }
        except Exception as exc:
            errors.append(f"{book_id}: {type(exc).__name__}: {exc}")
    raise ValueError(" | ".join(errors)[:1000])


def run_batch(
    *,
    limit: int,
    force: bool = False,
    catalog_id: int = 0,
    book_id: str = "",
    seed: bool = True,
) -> dict[str, Any]:
    if seed:
        seed_jobs()
    settings = LibraryInfrastructureSettings.from_env()
    runtime = MySQLLibraryRuntime() if settings.catalog_backend == "mysql" else None
    if runtime is not None:
        if book_id:
            if not catalog_id or not re.fullmatch(r"\d{10,24}", book_id):
                raise ValueError("--book-id 必须与有效的 --catalog-id 同时使用")
            with runtime.pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE library_fanqie_cover_jobs
                        SET book_id=%s,status='pending',attempts=0,
                            last_error=NULL,
                            resolved_by='verified_manual_binding'
                        WHERE catalog_id=%s
                        """,
                        (book_id, catalog_id),
                    )
        if force:
            with runtime.pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE library_fanqie_cover_jobs
                        SET status='pending',attempts=0,last_error=NULL
                        WHERE (%s=0 OR catalog_id=%s)
                        """,
                        (catalog_id, catalog_id),
                    )
        rows = runtime.claim_fanqie_cover_jobs(
            limit=limit,
            catalog_id=catalog_id,
        )
    else:
        with connect_jobs() as conn:
            if book_id:
                if not catalog_id or not re.fullmatch(r"\d{10,24}", book_id):
                    raise ValueError("--book-id 必须与有效的 --catalog-id 同时使用")
                conn.execute(
                    """
                    UPDATE fanqie_cover_jobs
                    SET book_id=?,status='pending',attempts=0,last_error=NULL,
                        resolved_by='verified_manual_binding',updated_at=?
                    WHERE catalog_id=?
                    """,
                    (book_id, now(), catalog_id),
                )
                conn.commit()
            if force:
                scope = "WHERE catalog_id=?" if catalog_id else ""
                params = (catalog_id,) if catalog_id else ()
                conn.execute(
                    f"""
                    UPDATE fanqie_cover_jobs
                    SET status='pending',attempts=0,last_error=NULL,updated_at=?
                    {scope}
                    """,
                    (now(), *params),
                )
                conn.commit()
            filters = [
                "status IN ('pending','failed')",
                "attempts < 5",
                "catalog_source_id LIKE 'fanqie-%'",
            ]
            params: list[Any] = []
            if catalog_id:
                filters.append("catalog_id=?")
                params.append(catalog_id)
            params.append(max(1, int(limit)))
            rows = conn.execute(
                f"""
                SELECT * FROM fanqie_cover_jobs
                WHERE {' AND '.join(filters)}
                ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                         attempts,catalog_id
                LIMIT ?
                """,
                params,
            ).fetchall()
    bridge = FanqieDownloaderBridge(LIBRARY_ROOT)
    result: dict[str, Any] = {
        "queued": len(rows),
        "done": 0,
        "failed": 0,
        "items": [],
    }
    for row in rows:
        try:
            item = process_job(
                bridge,
                row,
                force=force,
                runtime=runtime,
            )
            result["done"] += 1
            result["items"].append(item)
        except Exception as exc:
            message = f"{type(exc).__name__}: {str(exc)[:900]}"
            next_attempt = int(dict(row).get("attempts") or 0) + 1
            ai_fallback = should_generate_ai_fallback(
                message,
                attempts=next_attempt,
                max_attempts=MAX_SOURCE_COVER_ATTEMPTS,
            )
            if runtime is not None:
                runtime.finish_fanqie_cover_job(
                    row,
                    error=message,
                    ai_fallback=ai_fallback,
                )
            else:
                with connect_jobs() as conn:
                    conn.execute(
                        """
                        UPDATE fanqie_cover_jobs
                        SET status=?,attempts=attempts+1,last_error=?,
                            updated_at=? WHERE catalog_id=?
                        """,
                        (
                            "ai_fallback" if ai_fallback else "failed",
                            message,
                            now(),
                            int(row["catalog_id"]),
                        ),
                    )
                    conn.commit()
                if ai_fallback:
                    with source_cover_sync.connect_index() as cover_conn:
                        source_cover_sync.enqueue_sqlite_generated_cover(
                            cover_conn,
                            row,
                            reason=message,
                        )
                        cover_conn.commit()
            result["failed"] += 1
            result["items"].append(
                {
                    "catalog_id": int(row["catalog_id"]),
                    "status": "failed",
                    "error": message,
                }
            )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="仅同步真实 fanqie 来源的官方无水印封面"
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--catalog-id", type=int, default=0)
    parser.add_argument("--book-id", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--seed-interval", type=int, default=3600)
    args = parser.parse_args()
    last_seed_at = 0.0
    while True:
        current = time.monotonic()
        seed_due = (
            not args.watch
            or current - last_seed_at >= max(int(args.seed_interval), 300)
        )
        result = run_batch(
            limit=args.limit,
            force=args.force,
            catalog_id=args.catalog_id,
            book_id=args.book_id,
            seed=seed_due,
        )
        if seed_due:
            last_seed_at = time.monotonic()
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not args.watch:
            return 0 if result["failed"] == 0 else 1
        time.sleep(
            15 if result["queued"] else max(int(args.interval), 60)
        )


if __name__ == "__main__":
    raise SystemExit(main())
