"""Durable MySQL state used by electronic-library operational workers.

This module deliberately contains no SQLite compatibility.  One-time import
tools may still read legacy SQLite files, while long-running production jobs
must persist all queue, lease, cover, and reader-metric state here.
"""

from __future__ import annotations

import json
import hashlib
import re
import socket
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from oohstory_library.services.library_database import (
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
    RedisQueueClient,
)
from oohstory_library.services.library_cache import (
    LibraryCacheSettings,
    RedisHotCache,
)
from oohstory_library.services.library_catalog import normalize_catalog_title
from oohstory_library.services.cover_failure_policy import should_generate_ai_fallback
from oohstory_library.services.library_identity_claims import (
    bind_global_book_identity,
    book_identity_key,
    claim_global_book_identity,
)


def _cover_source_matches_detail(source_id: Any, detail_url: Any) -> bool:
    """Bind an authorized source id to its own exact HTTPS detail route."""

    source_id = str(source_id or "").strip()
    parsed = urlparse(str(detail_url or "").strip())
    if parsed.scheme != "https" or parsed.query or parsed.fragment:
        return False
    host = (parsed.hostname or "").lower()
    remote_id = source_id.split("-", 1)[-1]
    if source_id.startswith("fanqie-"):
        return (
            host in {"fanqienovel.com", "www.fanqienovel.com"}
            and parsed.path == f"/page/{remote_id}"
        )
    if source_id.startswith("xbiquge-"):
        match = re.fullmatch(r"/(\d+)/(\d+)/", parsed.path)
        return bool(
            host in {"xbiquge.info", "www.xbiquge.info"}
            and match
            and match.group(2) == remote_id
        )
    if source_id.startswith("ixdzs-"):
        return host == "ixdzs8.com" and parsed.path == f"/read/{remote_id}/"
    if source_id.startswith("shubaow-"):
        return (
            host in {"shubaow.org", "www.shubaow.org"}
            and parsed.path == f"/book{remote_id}.html"
        )
    if source_id.startswith("linovelib-"):
        return (
            host in {"linovelib.com", "www.linovelib.com"}
            and parsed.path == f"/novel/{remote_id}.html"
        )
    if source_id.isdigit():
        return bool(
            host in {"txt80.cc", "www.txt80.cc"}
            and re.fullmatch(r"/\d+/txt\d+\.html", parsed.path)
        )
    return False


class MySQLLibraryRuntime:
    def __init__(
        self,
        settings: LibraryInfrastructureSettings | None = None,
        pool: MySQLConnectionPool | None = None,
        redis_client: RedisQueueClient | RedisHotCache | None = None,
    ):
        self.settings = settings or LibraryInfrastructureSettings.from_env()
        if self.settings.catalog_backend != "mysql":
            raise RuntimeError(
                "operational MySQL runtime requires "
                "WEBNOVEL_CATALOG_BACKEND=mysql"
            )
        self.pool = pool or MySQLConnectionPool(self.settings)
        # Older callers supplied the queue client solely for catalog epochs.
        # Never touch it here: cache generations belong on the disposable
        # endpoint.  Accept the positional value during the transition while
        # constructing the correctly separated client.
        self.cache = (
            redis_client
            if isinstance(redis_client, RedisHotCache)
            else RedisHotCache(
                LibraryCacheSettings.from_infrastructure(self.settings)
            )
        )

    @staticmethod
    def worker_id(kind: str) -> str:
        return f"{kind}:{socket.gethostname()}:{uuid.uuid4()}"

    def invalidate_catalog_cache(self) -> None:
        self.cache.invalidate("catalog", "book", "cover")

    def authorized_download_candidates(
        self,
        limit: int,
        source_names: Iterable[str] = (
            "xbiquge", "ixdzs", "shubaow", "linovelib"
        ),
    ) -> list[dict[str, Any]]:
        sources = tuple(
            dict.fromkeys(
                str(value or "").strip().lower()
                for value in source_names
                if str(value or "").strip().lower()
                in {"xbiquge", "ixdzs", "shubaow", "linovelib"}
            )
        )
        if not sources:
            return []
        placeholders = ",".join("%s" for _ in sources)
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT b.id, b.source_id, b.detail_url, b.title, b.author,
                           b.category, b.book_status, j.id AS job_id,
                           j.status AS job_status
                    FROM download_jobs j
                    JOIN books b ON b.id=j.catalog_id
                    WHERE j.status IN ('pending', 'queued')
                      AND j.attempts<j.max_attempts
                      AND j.available_at<=UTC_TIMESTAMP(6)
                      AND j.source_name IN ({placeholders})
                    ORDER BY j.priority, j.attempts, j.id
                    LIMIT %s
                    """,
                    (*sources, min(max(int(limit), 1), 10000)),
                )
                return [dict(row) for row in cursor.fetchall()]

    def authorized_download_count(
        self,
        source_names: Iterable[str] = (
            "xbiquge", "ixdzs", "shubaow", "linovelib"
        ),
    ) -> int:
        sources = tuple(
            dict.fromkeys(
                str(value or "").strip().lower()
                for value in source_names
                if str(value or "").strip().lower()
                in {"xbiquge", "ixdzs", "shubaow", "linovelib"}
            )
        )
        if not sources:
            return 0
        placeholders = ",".join("%s" for _ in sources)
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM download_jobs
                    WHERE status IN ('pending', 'queued', 'downloading')
                      AND attempts<max_attempts
                      AND source_name IN ({placeholders})
                    """,
                    sources,
                )
                return int(cursor.fetchone()["count"])

    @staticmethod
    def _identity(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)

    def load_catalog_sections(self, source_name: str) -> list[dict[str, Any]]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT section_key,path,label,total_pages
                    FROM catalog_sections
                    WHERE source_name=%s AND total_pages>0
                    ORDER BY section_key<>'all',section_key
                    """,
                    (source_name,),
                )
                return [dict(row) for row in cursor.fetchall()]

    def upsert_catalog_section(
        self,
        source_name: str,
        item: dict[str, Any],
        *,
        error: str = "",
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO catalog_sections (
                      source_name,section_key,path,label,total_pages,status,
                      attempts,last_error,updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,1,%s,UTC_TIMESTAMP(6))
                    ON DUPLICATE KEY UPDATE
                      path=VALUES(path),label=VALUES(label),
                      total_pages=IF(
                        VALUES(total_pages)>0,
                        VALUES(total_pages),total_pages
                      ),
                      status=VALUES(status),attempts=attempts+1,
                      last_error=VALUES(last_error),
                      updated_at=UTC_TIMESTAMP(6)
                    """,
                    (
                        source_name,
                        str(item["section_key"]),
                        str(item["path"]),
                        str(item.get("label") or ""),
                        int(item.get("total_pages") or 0),
                        "failed" if error else "done",
                        error[:2000] or None,
                    ),
                )

    def crawl_page_states(
        self,
        source_name: str,
    ) -> dict[tuple[str, int], tuple[str, int]]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT section_key,page_number,status,attempts
                    FROM crawl_pages WHERE source_name=%s
                    """,
                    (source_name,),
                )
                return {
                    (str(row["section_key"]), int(row["page_number"])): (
                        str(row["status"]),
                        int(row["attempts"]),
                    )
                    for row in cursor.fetchall()
                }

    def register_txt80_page(
        self,
        *,
        section_key: str,
        page: int,
        url: str,
        books: Iterable[dict[str, Any]],
    ) -> int:
        items = [
            {**item, "title": normalize_catalog_title(item.get("title"))}
            for item in books
        ]
        stamp = datetime.utcnow()
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                for item in items:
                    identity_key = book_identity_key(
                        item.get("title"), item.get("author")
                    )
                    cursor.execute(
                        """
                        SELECT id,status
                        FROM books
                        WHERE source_id=%s
                        LIMIT 1
                        FOR UPDATE
                        """,
                        (str(item["source_id"]),),
                    )
                    existing = cursor.fetchone()
                    claimed, canonical_id = claim_global_book_identity(
                        cursor,
                        identity_key=identity_key,
                        catalog_id=(int(existing["id"]) if existing else None),
                    )
                    if not claimed and (
                        not existing
                        or int(canonical_id or 0) != int(existing["id"])
                    ):
                        continue
                    cursor.execute(
                        """
                        INSERT INTO books (
                          source_id,detail_url,title,author,category,
                          expected_size,status,attempts,discovered_at,
                          updated_at,book_status,identity_key,title_key,
                          library_id
                        ) VALUES (
                          %s,%s,%s,%s,%s,%s,'discovered',0,%s,%s,'已完结',
                          %s,%s,'local'
                        )
                        ON DUPLICATE KEY UPDATE
                          title=IF(title='',VALUES(title),title),
                          author=IF(author='',VALUES(author),author),
                          category=IF(category='',VALUES(category),category),
                          expected_size=IF(
                            VALUES(expected_size)='',
                            expected_size,VALUES(expected_size)
                          ),
                          updated_at=VALUES(updated_at),
                          identity_key=VALUES(identity_key),
                          title_key=VALUES(title_key),
                          row_version=row_version+1
                        """,
                        (
                            str(item["source_id"]),
                            str(item["detail_url"]),
                            str(item.get("title") or ""),
                            str(item.get("author") or ""),
                            str(item.get("category") or "未分类"),
                            str(item.get("expected_size") or ""),
                            stamp,
                            stamp,
                            identity_key,
                            self._identity(item.get("title")),
                        ),
                    )
                    cursor.execute(
                        """
                        SELECT id,status FROM books WHERE source_id=%s
                        """,
                        (str(item["source_id"]),),
                    )
                    book = cursor.fetchone()
                    if claimed and not existing:
                        bind_global_book_identity(
                            cursor,
                            identity_key=identity_key,
                            catalog_id=int(book["id"]),
                        )
                    if str(book["status"]) not in {"done", "duplicate"}:
                        cursor.execute(
                            """
                            INSERT INTO download_jobs (
                              catalog_id,source_id,source_name,status,priority,
                              attempts,max_attempts,available_at,payload
                            ) VALUES (
                              %s,%s,'txt80','pending',60,0,8,
                              UTC_TIMESTAMP(6),%s
                            )
                            ON DUPLICATE KEY UPDATE
                              source_id=VALUES(source_id),
                              source_name='txt80',
                              payload=VALUES(payload),
                              status=IF(
                                status IN ('done','downloading'),
                                status,'pending'
                              )
                            """,
                            (
                                int(book["id"]),
                                str(item["source_id"]),
                                json.dumps(item, ensure_ascii=False),
                            ),
                        )
                cursor.execute(
                    """
                    INSERT INTO crawl_pages (
                      source_name,section_key,page_number,url,status,attempts,
                      book_count,last_error,updated_at
                    ) VALUES (
                      'txt80',%s,%s,%s,'done',1,%s,NULL,UTC_TIMESTAMP(6)
                    )
                    ON DUPLICATE KEY UPDATE
                      status='done',attempts=attempts+1,
                      book_count=VALUES(book_count),last_error=NULL,
                      updated_at=UTC_TIMESTAMP(6)
                    """,
                    (section_key, int(page), url, len(items)),
                )
        self.invalidate_catalog_cache()
        return len(items)

    def fail_crawl_page(
        self,
        *,
        source_name: str,
        section_key: str,
        page: int,
        url: str,
        error: str,
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO crawl_pages (
                      source_name,section_key,page_number,url,status,attempts,
                      last_error,updated_at
                    ) VALUES (
                      %s,%s,%s,%s,'failed',1,%s,UTC_TIMESTAMP(6)
                    )
                    ON DUPLICATE KEY UPDATE
                      status='failed',attempts=attempts+1,
                      last_error=VALUES(last_error),
                      updated_at=UTC_TIMESTAMP(6)
                    """,
                    (
                        source_name,
                        section_key,
                        int(page),
                        url,
                        error[:2000],
                    ),
                )

    def catalog_runtime_stats(self, source_name: str) -> dict[str, Any]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status,COUNT(*) AS count FROM crawl_pages
                    WHERE source_name=%s GROUP BY status
                    """,
                    (source_name,),
                )
                pages = {
                    str(row["status"]): int(row["count"])
                    for row in cursor.fetchall()
                }
                cursor.execute(
                    """
                    SELECT status,COUNT(*) AS count,SUM(bytes) AS bytes
                    FROM books
                    WHERE (
                      source_id REGEXP '^[0-9]+$'
                      OR detail_url LIKE 'https://www.txt80.cc/%%'
                    )
                    GROUP BY status
                    """
                )
                rows = [dict(row) for row in cursor.fetchall()]
        return {
            "pages": pages,
            "books": {
                str(row["status"]): int(row["count"]) for row in rows
            },
            "downloaded_bytes": sum(
                int(row["bytes"] or 0)
                for row in rows
                if str(row["status"]) == "done"
            ),
        }

    def start_sync_run(self) -> int:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO library_sync_runs(started_at,status)
                    VALUES (UTC_TIMESTAMP(6),'running')
                    """
                )
                return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        summary: dict[str, Any],
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE library_sync_runs
                    SET finished_at=UTC_TIMESTAMP(6),status=%s,summary=%s
                    WHERE id=%s
                    """,
                    (
                        status,
                        json.dumps(summary, ensure_ascii=False),
                        int(run_id),
                    ),
                )

    def sync_snapshot(self) -> dict[str, Any]:
        libraries = {
            "local": {"total": 0, "downloaded": 0, "failed": 0},
            "fanqie": {"total": 0, "downloaded": 0, "failed": 0},
        }
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT library_id,status,book_count AS count
                    FROM catalog_status_counts
                    """
                )
                rows = [dict(row) for row in cursor.fetchall()]
        raw_total = duplicates = 0
        for row in rows:
            count = int(row["count"])
            raw_total += count
            if str(row["status"]) == "duplicate":
                duplicates += count
                continue
            item = libraries[str(row["library_id"])]
            item["total"] += count
            if str(row["status"]) == "done":
                item["downloaded"] += count
            elif str(row["status"]) == "failed":
                item["failed"] += count
        return {
            "raw_total": raw_total,
            "unique_total": raw_total - duplicates,
            "duplicates": duplicates,
            "downloaded": sum(
                item["downloaded"] for item in libraries.values()
            ),
            "libraries": libraries,
        }

    def reconcile_body_objects(self) -> dict[str, int]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id,body_object_key,legacy_output_path,bytes
                    FROM books WHERE status='done'
                    """
                )
                rows = [dict(row) for row in cursor.fetchall()]
        missing: list[int] = []
        repaired: list[tuple[int, int]] = []
        object_root = self.settings.object_root.resolve()
        for row in rows:
            object_key = str(row.get("body_object_key") or "").strip()
            legacy = str(row.get("legacy_output_path") or "").strip()
            path: Path | None = None
            if object_key:
                candidate = (object_root / object_key).resolve()
                try:
                    candidate.relative_to(object_root)
                    path = candidate
                except ValueError:
                    path = None
            if (path is None or not path.is_file()) and legacy:
                path = Path(legacy).expanduser().resolve()
            if path is None or not path.is_file():
                missing.append(int(row["id"]))
                continue
            size = path.stat().st_size
            if size != int(row.get("bytes") or 0):
                repaired.append((size, int(row["id"])))
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                if repaired:
                    cursor.executemany(
                        "UPDATE books SET bytes=%s WHERE id=%s",
                        repaired,
                    )
                if missing:
                    cursor.executemany(
                        """
                        UPDATE books
                        SET status='failed',attempts=0,body_object_key=NULL,
                            legacy_output_path=NULL,bytes=0,sha256=NULL,
                            last_error='正文对象缺失，等待队列恢复',
                            updated_at=UTC_TIMESTAMP(6),
                            row_version=row_version+1
                        WHERE id=%s
                        """,
                        [(value,) for value in missing],
                    )
                    cursor.executemany(
                        """
                        UPDATE download_jobs
                        SET status='pending',attempts=0,
                            available_at=UTC_TIMESTAMP(6),
                            lease_owner=NULL,lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s
                        """,
                        [(value,) for value in missing],
                    )
        if missing or repaired:
            self.invalidate_catalog_cache()
        return {
            "missing_files": len(missing),
            "repaired_sizes": len(repaired),
        }

    def retry_failed(self, *, all_failed: bool, after_hours: int) -> int:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE download_jobs j JOIN books b ON b.id=j.catalog_id
                    SET j.status='pending',j.attempts=0,
                        j.available_at=UTC_TIMESTAMP(6),
                        j.lease_owner=NULL,j.lease_token=NULL,
                        j.lease_expires_at=NULL,b.attempts=0
                    WHERE b.status='failed'
                      AND (
                        %s=1
                        OR (
                          j.attempts>=j.max_attempts
                          AND j.updated_at<=UTC_TIMESTAMP(6)
                            - INTERVAL %s HOUR
                        )
                      )
                    """,
                    (int(all_failed), max(int(after_hours), 1)),
                )
                return int(cursor.rowcount)

    def deduplicate_done_books(self) -> dict[str, Any]:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT identity_hash
                    FROM books
                    WHERE status='done' AND identity_key IS NOT NULL
                      AND identity_key<>''
                    GROUP BY identity_hash HAVING COUNT(*)>1
                    """
                )
                hashes = [bytes(row["identity_hash"]) for row in cursor.fetchall()]
                removed = 0
                for identity_hash in hashes:
                    cursor.execute(
                        """
                        SELECT id,source_id,title,author,bytes,sha256,
                               body_object_key,legacy_output_path
                        FROM books
                        WHERE status='done' AND identity_hash=%s
                        ORDER BY bytes DESC,id DESC
                        FOR UPDATE
                        """,
                        (identity_hash,),
                    )
                    rows = [dict(row) for row in cursor.fetchall()]
                    if len(rows) < 2:
                        continue
                    kept = rows[0]
                    for duplicate in rows[1:]:
                        cursor.execute(
                            """
                            INSERT INTO duplicate_books (
                              duplicate_book_id,duplicate_source_id,
                              kept_book_id,kept_source_id,title,author,
                              removed_object_key,removed_path,removed_bytes,
                              removed_sha256,reason,removed_at
                            ) VALUES (
                              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                              'MySQL 定时同步按标准书名/作者去重',
                              UTC_TIMESTAMP(6)
                            )
                            ON DUPLICATE KEY UPDATE
                              kept_book_id=VALUES(kept_book_id),
                              kept_source_id=VALUES(kept_source_id),
                              reason=VALUES(reason),
                              removed_at=VALUES(removed_at)
                            """,
                            (
                                int(duplicate["id"]),
                                duplicate.get("source_id"),
                                int(kept["id"]),
                                kept.get("source_id"),
                                duplicate.get("title"),
                                duplicate.get("author"),
                                duplicate.get("body_object_key"),
                                duplicate.get("legacy_output_path"),
                                int(duplicate.get("bytes") or 0),
                                duplicate.get("sha256"),
                            ),
                        )
                        cursor.execute(
                            """
                            UPDATE books SET status='duplicate',
                              body_object_key=NULL,legacy_output_path=NULL,
                              bytes=0,sha256=NULL,row_version=row_version+1
                            WHERE id=%s
                            """,
                            (int(duplicate["id"]),),
                        )
                        cursor.execute(
                            """
                            UPDATE download_jobs SET status='done',
                              completed_at=UTC_TIMESTAMP(6),
                              last_error=%s,lease_owner=NULL,lease_token=NULL,
                              lease_expires_at=NULL
                            WHERE catalog_id=%s
                            """,
                            (
                                f"与书目 {int(kept['id'])} 重复",
                                int(duplicate["id"]),
                            ),
                        )
                        removed += 1
        if removed:
            self.invalidate_catalog_cache()
        return {
            "groups": len(hashes),
            "removed_books": removed,
            "freed_bytes": 0,
            "backup": "mysql-transaction",
        }

    def enqueue_postprocess(self, catalog_ids: Iterable[int]) -> list[int]:
        ids = sorted({int(value) for value in catalog_ids if int(value) > 0})
        if not ids:
            return []
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO library_postprocess_jobs (
                        catalog_id, status, attempts, available_at
                    ) VALUES (%s, 'pending', 0, UTC_TIMESTAMP(6))
                    ON DUPLICATE KEY UPDATE
                        status=IF(status='done', status, 'pending'),
                        available_at=IF(
                            status='done', available_at, UTC_TIMESTAMP(6)
                        ),
                        lease_owner=NULL,
                        lease_token=NULL,
                        lease_expires_at=NULL
                    """,
                    [(value,) for value in ids],
                )
        return ids

    def claim_postprocess(
        self,
        *,
        limit: int,
        worker_id: str,
        lease_seconds: int = 3600,
    ) -> list[dict[str, Any]]:
        token = str(uuid.uuid4())
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE library_postprocess_jobs
                    SET status='pending', lease_owner=NULL, lease_token=NULL,
                        lease_expires_at=NULL,
                        available_at=UTC_TIMESTAMP(6),
                        last_error=COALESCE(
                            last_error,
                            '后处理租约过期，已自动恢复'
                        )
                    WHERE status='running'
                      AND lease_expires_at<UTC_TIMESTAMP(6)
                    """
                )
                cursor.execute(
                    """
                    SELECT catalog_id
                    FROM library_postprocess_jobs
                    WHERE status IN ('pending', 'failed')
                      AND attempts<max_attempts
                      AND available_at<=UTC_TIMESTAMP(6)
                    ORDER BY attempts, catalog_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (min(max(int(limit), 1), 1000),),
                )
                ids = [int(row["catalog_id"]) for row in cursor.fetchall()]
                if ids:
                    placeholders = ",".join(["%s"] * len(ids))
                    cursor.execute(
                        f"""
                        UPDATE library_postprocess_jobs
                        SET status='running', lease_owner=%s, lease_token=%s,
                            lease_expires_at=UTC_TIMESTAMP(6)
                                + INTERVAL %s SECOND
                        WHERE catalog_id IN ({placeholders})
                          AND status IN ('pending', 'failed')
                        """,
                        (worker_id, token, int(lease_seconds), *ids),
                    )
        return [{"catalog_id": value, "lease_token": token} for value in ids]

    def finish_postprocess(
        self,
        claims: Iterable[dict[str, Any]],
        *,
        succeeded: bool,
        error: str = "",
    ) -> None:
        values = [
            (int(item["catalog_id"]), str(item["lease_token"]))
            for item in claims
        ]
        if not values:
            return
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                catalog_ids = [catalog_id for catalog_id, _ in values]
                token = values[0][1]
                if any(value_token != token for _, value_token in values):
                    raise ValueError("后处理批次租约令牌不一致")
                placeholders = ",".join(["%s"] * len(catalog_ids))
                if succeeded:
                    cursor.execute(
                        f"""
                        UPDATE library_postprocess_jobs
                        SET status='done', attempts=attempts+1,
                            completed_at=UTC_TIMESTAMP(6), last_error=NULL,
                            lease_owner=NULL, lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id IN ({placeholders})
                          AND status='running' AND lease_token=%s
                        """,
                        (*catalog_ids, token),
                    )
                else:
                    cursor.execute(
                        f"""
                        UPDATE library_postprocess_jobs
                        SET status='failed', attempts=attempts+1,
                            available_at=UTC_TIMESTAMP(6)
                                + INTERVAL LEAST(
                                    3600,
                                    30 * POW(2, LEAST(attempts, 7))
                                  ) SECOND,
                            last_error=%s, lease_owner=NULL, lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id IN ({placeholders})
                          AND status='running' AND lease_token=%s
                        """,
                        (error[:2000], *catalog_ids, token),
                    )

    def select_done_books(
        self,
        book_ids: Iterable[int] = (),
        *,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        ids = sorted({int(value) for value in book_ids if int(value) > 0})
        params: list[Any] = []
        where = [
            "status='done'",
            "(body_object_key IS NOT NULL OR legacy_output_path IS NOT NULL)",
        ]
        if ids:
            where.append("id IN (" + ", ".join(["%s"] * len(ids)) + ")")
            params.extend(ids)
        sql = (
            "SELECT id, source_id, title, author, category, expected_size, "
            "legacy_output_path, body_object_key, bytes, sha256, updated_at "
            "FROM books WHERE "
            + " AND ".join(where)
            + " ORDER BY id"
        )
        if limit > 0:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = [dict(row) for row in cursor.fetchall()]
        object_root = self.settings.object_root.resolve()
        for row in rows:
            legacy = str(row.get("legacy_output_path") or "").strip()
            object_key = str(row.get("body_object_key") or "").strip()
            output_path = Path(legacy).expanduser() if legacy else None
            if not (output_path and output_path.is_file()) and object_key:
                candidate = (object_root / object_key).resolve()
                try:
                    candidate.relative_to(object_root)
                    output_path = candidate
                except ValueError:
                    output_path = None
            row["output_path"] = str(output_path) if output_path else ""
        return rows

    def existing_reader_metrics(
        self,
    ) -> dict[int, tuple[int, int, int, int]]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT catalog_id, word_count, chapter_count,
                           section_count, reader_schema_version
                    FROM book_metadata
                    """
                )
                return {
                    int(row["catalog_id"]): (
                        int(row["word_count"] or 0),
                        int(row["chapter_count"] or 0),
                        int(row["section_count"] or 0),
                        int(row["reader_schema_version"] or 0),
                    )
                    for row in cursor.fetchall()
                }

    def upsert_reader_metrics(
        self,
        catalog_id: int,
        reader_index: dict[str, Any],
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO book_metadata (
                        catalog_id, source_mtime_ns, reader_source_path,
                        reader_source_bytes, word_count, chapter_count,
                        section_count, reader_index_status,
                        reader_schema_version, reader_indexed_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        source_mtime_ns=VALUES(source_mtime_ns),
                        reader_source_path=VALUES(reader_source_path),
                        reader_source_bytes=VALUES(reader_source_bytes),
                        word_count=VALUES(word_count),
                        chapter_count=VALUES(chapter_count),
                        section_count=VALUES(section_count),
                        reader_index_status=VALUES(reader_index_status),
                        reader_schema_version=VALUES(reader_schema_version),
                        reader_indexed_at=VALUES(reader_indexed_at)
                    """,
                    (
                        int(catalog_id),
                        int(reader_index["source_mtime_ns"]),
                        str(reader_index["source_path"]),
                        int(reader_index["source_bytes"]),
                        int(reader_index["word_count"]),
                        int(reader_index["chapter_count"]),
                        int(reader_index["section_count"]),
                        str(reader_index["index_status"]),
                        int(reader_index["schema_version"]),
                        reader_index["indexed_at"],
                    ),
                )
                cursor.execute(
                    """
                    UPDATE books
                    SET approx_word_count=%s, approx_chapter_count=%s
                    WHERE id=%s
                    """,
                    (
                        int(reader_index["word_count"]),
                        int(reader_index["chapter_count"]),
                        int(catalog_id),
                    ),
                )
        self.invalidate_catalog_cache()

    def postprocess_status_counts(self) -> dict[str, int]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM library_postprocess_jobs GROUP BY status
                    """
                )
                return {
                    str(row["status"]): int(row["count"])
                    for row in cursor.fetchall()
                }

    def seed_cover_jobs(
        self,
        *,
        limit_per_source: int = 10000,
        library_id: str = "",
        source: str = "",
    ) -> int:
        """Seed only missing cover rows; never re-upsert the whole catalog."""

        limit = min(max(int(limit_per_source), 1), 50000)
        library_scope = str(library_id or "").strip().lower()
        if library_scope not in {"", "local", "fanqie"}:
            raise ValueError("未知封面同步书库范围")
        source_scope = str(source or "").strip().lower()
        if source_scope not in {
            "", "txt80", "xbiquge", "ixdzs", "shubaow", "linovelib"
        }:
            raise ValueError("未知封面同步来源范围")
        library_filter = " AND b.library_id=%s" if library_scope else ""
        source_condition_map = {
            "txt80": """
            b.detail_url LIKE 'https://www.txt80.cc/%%/txt%%.html'
            AND b.source_id REGEXP '^[0-9]+$'
            """,
            "xbiquge": """
            b.source_id LIKE 'xbiquge-%%'
            AND (
              b.detail_url LIKE 'https://www.xbiquge.info/%%'
              OR b.detail_url LIKE 'https://xbiquge.info/%%'
            )
            """,
            "ixdzs": """
            b.source_id LIKE 'ixdzs-%%'
            AND b.detail_url LIKE 'https://ixdzs8.com/read/%%/'
            """,
            "shubaow": """
            b.source_id LIKE 'shubaow-%%'
            AND (
              b.detail_url LIKE 'https://www.shubaow.org/book%%.html'
              OR b.detail_url LIKE 'https://shubaow.org/book%%.html'
            )
            """,
            "linovelib": """
            b.source_id LIKE 'linovelib-%%'
            AND (
              b.detail_url LIKE 'https://www.linovelib.com/novel/%%.html'
              OR b.detail_url LIKE 'https://linovelib.com/novel/%%.html'
            )
            """,
        }
        source_conditions = (
            (source_condition_map[source_scope],)
            if source_scope
            else tuple(source_condition_map.values())
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                seeded = 0
                for condition in source_conditions:
                    cursor.execute(
                        f"""
                    INSERT IGNORE INTO library_covers (
                        catalog_id, source_id, title, author, detail_url,
                        status, attempts
                    )
                    SELECT b.id, b.source_id, b.title, b.author, b.detail_url,
                           'pending', 0
                    FROM books AS b
                    LEFT JOIN library_covers AS c ON c.catalog_id=b.id
                    WHERE c.catalog_id IS NULL
                      AND b.status='done' AND b.body_available=1
                      {library_filter}
                      AND ({condition})
                    ORDER BY b.id
                    LIMIT %s
                    """,
                        (
                            *((library_scope,) if library_scope else ()),
                            limit,
                        ),
                    )
                    seeded += int(cursor.rowcount)
                # Select a bounded set through the status index before
                # updating by primary key.  A wide UPDATE with a source-id
                # predicate can wait behind active cover leases long enough
                # to hit the process read timeout and restart the watch
                # worker.  SKIP LOCKED leaves busy rows for the next seed.
                if source_scope in {"", "ixdzs"}:
                    cursor.execute(
                        """
                    SELECT catalog_id
                    FROM library_covers
                    FORCE INDEX (idx_library_covers_claim)
                    WHERE status='failed'
                      AND source_id LIKE 'ixdzs-%%'
                      AND last_error LIKE
                        '%%尚未配置可信封面适配器%%'
                    ORDER BY attempts, catalog_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                        (limit,),
                    )
                    reset_ids = [
                        int(row["catalog_id"]) for row in cursor.fetchall()
                    ]
                    if reset_ids:
                        cursor.executemany(
                            """
                        UPDATE library_covers
                        SET status='pending', attempts=0, last_error=NULL,
                            lease_owner=NULL, lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s AND status='failed'
                        """,
                            [(catalog_id,) for catalog_id in reset_ids],
                        )
                    reset = int(cursor.rowcount) if reset_ids else 0
                else:
                    reset = 0

                # Historical bare object keys are repaired once during
                # deployment.  Keeping that data migration out of this hot
                # seed transaction prevents a locked legacy book from
                # restarting the permanent cover worker.
                return seeded + reset

    def repair_bare_cover_object_keys(
        self,
        *,
        limit: int = 500,
    ) -> dict[str, int]:
        """Prefix verified legacy cover filenames with the storage directory.

        Older TXT80 and authorized-cover workers stored only the filename in
        ``books.cover_object_key`` even though the file lives below ``封面/``.
        Select candidates without holding locks while checking the filesystem,
        then use compare-and-set updates so an active worker always wins.
        """

        batch_limit = min(max(int(limit), 1), 500)
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT b.id AS catalog_id, c.filename
                    FROM books AS b
                    JOIN library_covers AS c ON c.catalog_id=b.id
                      AND c.source_id=b.source_id
                      AND c.title=b.title
                      AND c.author=b.author
                    WHERE b.is_active=1
                      AND b.cover_object_key=c.filename
                      AND b.cover_object_key NOT LIKE '%%/%%'
                      AND c.status='done'
                      AND c.filename IS NOT NULL AND c.filename<>''
                    ORDER BY b.id
                    LIMIT %s
                    """,
                    (batch_limit,),
                )
                candidates = [dict(row) for row in cursor.fetchall()]

        verified: list[tuple[str, int, str]] = []
        invalid = 0
        missing = 0
        cover_root = self.settings.object_root / "封面"
        for row in candidates:
            filename = str(row.get("filename") or "").strip()
            if not filename or Path(filename).name != filename:
                invalid += 1
                continue
            if not (cover_root / filename).is_file():
                missing += 1
                continue
            verified.append(
                (filename, int(row["catalog_id"]), filename)
            )

        repaired = 0
        if verified:
            with self.pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        UPDATE books
                        SET cover_object_key=CONCAT('封面/', %s),
                            row_version=row_version+1
                        WHERE id=%s AND cover_object_key=%s
                        """,
                        verified,
                    )
                    repaired = int(cursor.rowcount)
            if repaired:
                self.invalidate_catalog_cache()
        return {
            "candidates": len(candidates),
            "repaired": repaired,
            "missing_files": missing,
            "invalid_filenames": invalid,
        }

    def existing_cover(
        self,
        *,
        catalog_id: int,
        source_id: str,
        title: str,
        author: str,
    ) -> dict[str, Any] | None:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT filename,sha256,cover_url
                    FROM library_covers
                    WHERE catalog_id=%s AND source_id=%s AND title=%s
                      AND author=%s AND status='done'
                    """,
                    (int(catalog_id), source_id, title, author),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def prepare_cover(
        self,
        *,
        catalog_id: int,
        source_id: str,
        title: str,
        author: str,
        detail_url: str,
        cover_url: str = "",
    ) -> None:
        source_id = str(source_id or "").strip()
        detail_url = str(detail_url or "").strip()
        if not _cover_source_matches_detail(source_id, detail_url):
            raise ValueError("封面来源标识与详情站点不一致，已拒绝跨站绑定")
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT source_id,title,author
                    FROM books WHERE id=%s FOR UPDATE
                    """,
                    (int(catalog_id),),
                )
                book = cursor.fetchone()
                if not book:
                    raise ValueError("封面绑定的书目不存在")
                if str(book.get("source_id") or "") != source_id:
                    raise ValueError("封面来源与书目真实来源不一致，已拒绝跨站绑定")
                if book_identity_key(book.get("title"), book.get("author")) != (
                    book_identity_key(title, author)
                ):
                    raise ValueError("封面书名或作者与书目身份不一致")
                cursor.execute(
                    """
                    INSERT INTO library_covers (
                      catalog_id,source_id,title,author,detail_url,cover_url,
                      status
                    ) VALUES (%s,%s,%s,%s,%s,%s,'pending')
                    ON DUPLICATE KEY UPDATE
                      source_id=VALUES(source_id),title=VALUES(title),
                      author=VALUES(author),detail_url=VALUES(detail_url),
                      cover_url=VALUES(cover_url),status='pending',
                      last_error=NULL,lease_owner=NULL,lease_token=NULL,
                      lease_expires_at=NULL
                    """,
                    (
                        int(catalog_id),
                        source_id,
                        title,
                        author,
                        detail_url,
                        cover_url or None,
                    ),
                )

    def persist_cover_result(
        self,
        *,
        catalog_id: int,
        cover_url: str = "",
        filename: str = "",
        sha256: str = "",
        error: str = "",
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                if error:
                    cursor.execute(
                        """
                        UPDATE library_covers
                        SET status='failed',attempts=attempts+1,last_error=%s,
                            lease_owner=NULL,lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s
                        """,
                        (error[:2000], int(catalog_id)),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE library_covers
                        SET cover_url=%s,filename=%s,sha256=%s,status='done',
                            attempts=attempts+1,last_error=NULL,
                            lease_owner=NULL,lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s
                        """,
                        (
                            cover_url,
                            filename,
                            sha256,
                            int(catalog_id),
                        ),
                    )
                    suffix = Path(filename).suffix.casefold()
                    content_type = {
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".png": "image/png",
                        ".gif": "image/gif",
                        ".webp": "image/webp",
                    }.get(suffix, "application/octet-stream")
                    cursor.execute(
                        """
                        INSERT INTO object_assets (
                          catalog_id,asset_type,object_key,storage_backend,
                          content_type,bytes,sha256,state
                        ) VALUES (
                          %s,'cover',%s,'nas',%s,0,%s,'available'
                        )
                        ON DUPLICATE KEY UPDATE
                          object_key=VALUES(object_key),storage_backend='nas',
                          content_type=VALUES(content_type),
                          sha256=VALUES(sha256),state='available'
                        """,
                        (
                            int(catalog_id),
                            f"封面/{filename}",
                            content_type,
                            sha256 or None,
                        ),
                    )
                    cursor.execute(
                        """
                        UPDATE books
                        SET cover_object_key=CONCAT('封面/', %s),
                            row_version=row_version+1
                        WHERE id=%s
                        """,
                        (filename, int(catalog_id)),
                    )
        self.invalidate_catalog_cache()

    @staticmethod
    def _persist_missing_cover_default_cursor(
        cursor: Any,
        *,
        catalog_id: int,
        filename: str,
        sha256: str,
        bytes_count: int,
        content_type: str,
        reason: str,
        lease_token: str = "",
    ) -> dict[str, Any] | None:
        """Clear the catalog-owned cover and queue title generation atomically.

        Missing artwork uses one shared HTTP asset.  The catalog row retains
        only the fallback state while ``books`` and ``object_assets`` remain
        reserved for a real, independently replaceable cover.
        """

        lease_clause = " AND c.lease_token=%s" if lease_token else ""
        parameters: tuple[Any, ...] = (
            (int(catalog_id), str(lease_token))
            if lease_token
            else (int(catalog_id),)
        )
        cursor.execute(
            f"""
            SELECT c.filename,c.sha256,c.source_id,c.title,c.author
            FROM library_covers c
            WHERE c.catalog_id=%s{lease_clause}
            FOR UPDATE
            """,
            parameters,
        )
        cover = cursor.fetchone()
        if not cover:
            return None
        original_filename = str(cover.get("filename") or "")
        original_sha256 = str(cover.get("sha256") or "")
        cursor.execute(
            """
            UPDATE library_covers
            SET cover_url='oohstory-default://shared',filename=NULL,
                sha256=%s,status='ai_fallback',
                attempts=attempts+1,last_error=%s,
                lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL
            WHERE catalog_id=%s
            """,
            (
                sha256,
                str(reason)[:2000],
                int(catalog_id),
            ),
        )
        cursor.execute(
            """
            UPDATE books SET cover_object_key=NULL,
                row_version=row_version+1 WHERE id=%s
            """,
            (int(catalog_id),),
        )
        cursor.execute(
            """
            DELETE FROM object_assets
            WHERE catalog_id=%s AND asset_type='cover'
            """,
            (int(catalog_id),),
        )
        cursor.execute(
            """
            INSERT INTO library_clean_cover_jobs (
              catalog_id,source_id,title,author,status,
              original_filename,attempts,last_error
            ) VALUES (%s,%s,%s,%s,'generate_pending',NULL,0,%s)
            ON DUPLICATE KEY UPDATE
              source_id=VALUES(source_id),title=VALUES(title),
              author=VALUES(author),
              status=IF(library_clean_cover_jobs.status='processing',
                        'processing','generate_pending'),
              original_filename=IF(
                library_clean_cover_jobs.status='processing',
                library_clean_cover_jobs.original_filename,NULL
              ),
              replacement_url=IF(
                library_clean_cover_jobs.status='processing',
                library_clean_cover_jobs.replacement_url,NULL
              ),
              replacement_filename=IF(
                library_clean_cover_jobs.status='processing',
                library_clean_cover_jobs.replacement_filename,NULL
              ),
              verification_source=IF(
                library_clean_cover_jobs.status='processing',
                library_clean_cover_jobs.verification_source,NULL
              ),
              original_deleted_at=IF(
                library_clean_cover_jobs.status='processing',
                library_clean_cover_jobs.original_deleted_at,NULL
              ),
              attempts=IF(library_clean_cover_jobs.status='processing',
                          library_clean_cover_jobs.attempts,0),
              ai_session_id=IF(library_clean_cover_jobs.status='processing',
                               library_clean_cover_jobs.ai_session_id,NULL),
              last_error=IF(library_clean_cover_jobs.status='processing',
                            library_clean_cover_jobs.last_error,
                            VALUES(last_error)),
              lease_owner=IF(library_clean_cover_jobs.status='processing',
                             library_clean_cover_jobs.lease_owner,NULL),
              lease_token=IF(library_clean_cover_jobs.status='processing',
                             library_clean_cover_jobs.lease_token,NULL),
              lease_expires_at=IF(
                library_clean_cover_jobs.status='processing',
                library_clean_cover_jobs.lease_expires_at,NULL
              )
            """,
            (
                int(catalog_id),
                str(cover.get("source_id") or ""),
                str(cover.get("title") or ""),
                str(cover.get("author") or ""),
                str(reason)[:2000],
            ),
        )
        return {
            "original_filename": original_filename,
            "original_sha256": original_sha256,
            "filename": "",
            "sha256": sha256,
        }

    def persist_missing_cover_default(
        self,
        *,
        catalog_id: int,
        filename: str,
        sha256: str,
        bytes_count: int,
        content_type: str,
        reason: str,
    ) -> dict[str, Any]:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                result = self._persist_missing_cover_default_cursor(
                    cursor,
                    catalog_id=int(catalog_id),
                    filename=filename,
                    sha256=sha256,
                    bytes_count=bytes_count,
                    content_type=content_type,
                    reason=reason,
                )
                if result is None:
                    raise ValueError("缺失封面绑定的任务不存在")
        self.invalidate_catalog_cache()
        return result

    def persist_alternate_cover_result(
        self,
        *,
        catalog_id: int,
        catalog_source_id: str,
        origin_source_id: str,
        title: str,
        author: str,
        origin_detail_url: str,
        cover_url: str,
        filename: str,
        sha256: str,
        bytes_count: int,
        content_type: str,
    ) -> dict[str, Any]:
        """Atomically switch a local book to a verified alternate-source cover."""

        if not _cover_source_matches_detail(origin_source_id, origin_detail_url):
            raise ValueError("备用封面来源标识与详情站点不一致")
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT source_id,detail_url,title,author,library_id
                    FROM books WHERE id=%s FOR UPDATE
                    """,
                    (int(catalog_id),),
                )
                book = cursor.fetchone()
                if not book:
                    raise ValueError("备用封面绑定的书目不存在")
                if str(book.get("source_id") or "") != str(catalog_source_id):
                    raise ValueError("正文来源在封面验收期间发生变化，拒绝切换")
                if str(book.get("library_id") or "") != "local":
                    raise ValueError("备用封面只能替换本地逻辑书库作品")
                if book_identity_key(book.get("title"), book.get("author")) != (
                    book_identity_key(title, author)
                ):
                    raise ValueError("备用封面书名或作者与书目身份不一致")
                cursor.execute(
                    """
                    SELECT filename FROM library_covers
                    WHERE catalog_id=%s FOR UPDATE
                    """,
                    (int(catalog_id),),
                )
                existing = cursor.fetchone() or {}
                original_filename = str(existing.get("filename") or "")
                cursor.execute(
                    """
                    INSERT INTO library_covers (
                      catalog_id,source_id,title,author,detail_url,
                      cover_source_id,cover_detail_url,cover_url,filename,
                      sha256,status,attempts,last_error
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'done',1,NULL
                    )
                    ON DUPLICATE KEY UPDATE
                      source_id=VALUES(source_id),title=VALUES(title),
                      author=VALUES(author),detail_url=VALUES(detail_url),
                      cover_source_id=VALUES(cover_source_id),
                      cover_detail_url=VALUES(cover_detail_url),
                      cover_url=VALUES(cover_url),filename=VALUES(filename),
                      sha256=VALUES(sha256),status='done',
                      attempts=attempts+1,last_error=NULL,
                      lease_owner=NULL,lease_token=NULL,
                      lease_expires_at=NULL
                    """,
                    (
                        int(catalog_id),
                        str(book.get("source_id") or ""),
                        title,
                        author,
                        str(book.get("detail_url") or ""),
                        origin_source_id,
                        origin_detail_url,
                        cover_url,
                        filename,
                        sha256,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE books SET cover_object_key=CONCAT('封面/',%s),
                        row_version=row_version+1 WHERE id=%s
                    """,
                    (filename, int(catalog_id)),
                )
                cursor.execute(
                    """
                    INSERT INTO object_assets (
                      catalog_id,asset_type,object_key,storage_backend,
                      content_type,bytes,sha256,state
                    ) VALUES (
                      %s,'cover',%s,'nas',%s,%s,%s,'available'
                    )
                    ON DUPLICATE KEY UPDATE
                      object_key=VALUES(object_key),storage_backend='nas',
                      content_type=VALUES(content_type),bytes=VALUES(bytes),
                      sha256=VALUES(sha256),state='available'
                    """,
                    (
                        int(catalog_id),
                        f"封面/{filename}",
                        content_type,
                        max(int(bytes_count), 0),
                        sha256,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE library_clean_cover_jobs
                    SET status='source_replaced',
                        original_filename=COALESCE(original_filename,%s),
                        replacement_url=%s,replacement_filename=%s,
                        verification_source=%s,last_error=NULL,
                        lease_owner=NULL,lease_token=NULL,
                        lease_expires_at=NULL
                    WHERE catalog_id=%s
                    """,
                    (
                        original_filename or None,
                        cover_url,
                        filename,
                        f"authorized-source:{origin_source_id}",
                        int(catalog_id),
                    ),
                )
        self.invalidate_catalog_cache()
        return {
            "catalog_id": int(catalog_id),
            "original_filename": original_filename,
            "filename": filename,
        }

    def claim_cover_jobs(
        self,
        *,
        limit: int,
        worker_id: str,
        library_id: str = "",
        source: str = "",
    ) -> list[dict[str, Any]]:
        library_scope = str(library_id or "").strip().lower()
        if library_scope not in {"", "local", "fanqie"}:
            raise ValueError("未知封面同步书库范围")
        source_scope = str(source or "").strip().lower()
        if source_scope not in {
            "", "txt80", "xbiquge", "ixdzs", "shubaow", "linovelib"
        }:
            raise ValueError("未知封面同步来源范围")
        library_filter = " AND b.library_id=%s" if library_scope else ""
        source_condition_map = {
            "txt80": """
              c.detail_url LIKE 'https://www.txt80.cc/%%/txt%%.html'
              AND c.source_id REGEXP '^[0-9]+$'
            """,
            "xbiquge": """
              c.source_id LIKE 'xbiquge-%%'
              AND (
                c.detail_url LIKE 'https://www.xbiquge.info/%%'
                OR c.detail_url LIKE 'https://xbiquge.info/%%'
              )
            """,
            "ixdzs": """
              c.source_id LIKE 'ixdzs-%%'
              AND c.detail_url LIKE 'https://ixdzs8.com/read/%%/'
            """,
            "shubaow": """
              c.source_id LIKE 'shubaow-%%'
              AND (
                c.detail_url LIKE 'https://www.shubaow.org/book%%.html'
                OR c.detail_url LIKE 'https://shubaow.org/book%%.html'
              )
            """,
            "linovelib": """
              c.source_id LIKE 'linovelib-%%'
              AND (
                c.detail_url LIKE 'https://www.linovelib.com/novel/%%.html'
                OR c.detail_url LIKE 'https://linovelib.com/novel/%%.html'
              )
            """,
        }
        source_filter = (
            f" AND ({source_condition_map[source_scope]})"
            if source_scope
            else ""
        )
        token = str(uuid.uuid4())
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT catalog_id
                    FROM library_covers AS c
                    FORCE INDEX (idx_library_covers_claim)
                    WHERE c.status='processing'
                      AND c.lease_expires_at<UTC_TIMESTAMP(6)
                      {source_filter}
                    ORDER BY catalog_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (min(max(int(limit) * 2, 100), 5000),),
                )
                expired_ids = [
                    int(row["catalog_id"]) for row in cursor.fetchall()
                ]
                if expired_ids:
                    cursor.executemany(
                        """
                        UPDATE library_covers
                        SET status='failed', lease_owner=NULL,
                            lease_token=NULL, lease_expires_at=NULL,
                            last_error=COALESCE(
                                last_error, '封面租约过期，已自动恢复'
                            )
                        WHERE catalog_id=%s AND status='processing'
                        """,
                        [(catalog_id,) for catalog_id in expired_ids],
                    )
                cursor.execute(
                    f"""
                    SELECT c.*
                    FROM library_covers AS c
                    WHERE c.status IN ('pending','failed') AND c.attempts<5
                      {source_filter}
                      AND EXISTS (
                        SELECT 1 FROM books AS b
                        WHERE b.id=c.catalog_id AND b.status='done'
                          AND b.body_available=1
                          {library_filter}
                      )
                    ORDER BY c.source_id NOT LIKE 'xbiquge-%%',
                             c.status='failed', c.catalog_id DESC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (
                        *((library_scope,) if library_scope else ()),
                        min(max(int(limit), 1), 5000),
                    ),
                )
                rows = [dict(row) for row in cursor.fetchall()]
                if rows:
                    cursor.executemany(
                        """
                        UPDATE library_covers
                        SET status='processing', lease_owner=%s,
                            lease_token=%s,
                            lease_expires_at=UTC_TIMESTAMP(6)
                              + INTERVAL 15 MINUTE
                        WHERE catalog_id=%s
                        """,
                        [
                            (worker_id, token, int(row["catalog_id"]))
                            for row in rows
                        ],
                    )
        for row in rows:
            row["lease_token"] = token
        return rows

    def finish_cover_job(
        self,
        row: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
        error: str = "",
        ai_fallback: bool = False,
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                if error:
                    cursor.execute(
                        """
                        UPDATE library_covers
                        SET status=%s, attempts=attempts+1,
                            last_error=%s, lease_owner=NULL, lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s AND lease_token=%s
                        """,
                        (
                            "ai_fallback" if ai_fallback else "failed",
                            error[:2000],
                            int(row["catalog_id"]),
                            str(row["lease_token"]),
                        ),
                    )
                    if ai_fallback:
                        self._enqueue_generated_cover_fallback_cursor(
                            cursor,
                            catalog_id=int(row["catalog_id"]),
                            source_id=str(row.get("source_id") or ""),
                            title=str(row.get("title") or ""),
                            author=str(row.get("author") or ""),
                            reason=error,
                        )
                    return
                assert result is not None
                if result.get("missing_placeholder"):
                    self._persist_missing_cover_default_cursor(
                        cursor,
                        catalog_id=int(row["catalog_id"]),
                        filename=str(result["filename"]),
                        sha256=str(result["sha256"]),
                        bytes_count=int(result.get("bytes") or 0),
                        content_type=str(
                            result.get("content_type") or "image/jpeg"
                        ),
                        reason=str(
                            result.get("missing_reason")
                            or "原站返回缺失封面指纹，已改用默认封面"
                        ),
                        lease_token=str(row.get("lease_token") or ""),
                    )
                    return
                cursor.execute(
                    """
                    UPDATE library_covers
                    SET cover_url=%s, filename=%s, sha256=%s, status='done',
                        attempts=attempts+1, last_error=NULL,
                        lease_owner=NULL, lease_token=NULL,
                        lease_expires_at=NULL
                    WHERE catalog_id=%s AND lease_token=%s
                    """,
                    (
                        result["cover_url"],
                        result["filename"],
                        result["sha256"],
                        int(row["catalog_id"]),
                        str(row["lease_token"]),
                    ),
                )
                cursor.execute(
                    """
                    UPDATE books
                    SET cover_object_key=CONCAT('封面/', %s),
                        row_version=row_version+1
                    WHERE id=%s
                    """,
                    (result["filename"], int(row["catalog_id"])),
                )
                if result.get("needs_clean_replacement"):
                    cursor.execute(
                        """
                        INSERT INTO library_clean_cover_jobs (
                            catalog_id, source_id, title, author, status,
                            original_filename
                        ) VALUES (
                          %s,%s,%s,%s,
                          IF(%s REGEXP '^[0-9]+$',
                             'source_lookup_pending','source_cover_retained'),
                          %s
                        )
                        ON DUPLICATE KEY UPDATE
                            source_id=VALUES(source_id),
                            title=VALUES(title),
                            author=VALUES(author),
                            status=IF(
                              original_filename<>VALUES(original_filename),
                              IF(VALUES(source_id) REGEXP '^[0-9]+$',
                                 'source_lookup_pending',
                                 'source_cover_retained'),
                              status
                            ),
                            attempts=IF(
                              original_filename<>VALUES(original_filename),
                              0, attempts
                            ),
                            original_deleted_at=IF(
                              original_filename<>VALUES(original_filename),
                              NULL, original_deleted_at
                            ),
                            original_filename=VALUES(original_filename),
                            last_error=NULL
                        """,
                        (
                            int(row["catalog_id"]),
                            str(row["source_id"]),
                            str(row["title"]),
                            str(row["author"]),
                            str(row["source_id"]),
                            result["filename"],
                        ),
                    )
        self.invalidate_catalog_cache()

    def fanqie_catalog_rows(self) -> list[dict[str, Any]]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id AS catalog_id, COALESCE(source_id,'') AS source_id,
                           title, author, detail_url
                    FROM books
                    WHERE status<>'duplicate' AND library_id='fanqie'
                      AND (
                        source_id LIKE 'fanqie-%%'
                        OR detail_url LIKE
                          'https://fanqienovel.com/page/%%'
                        OR detail_url LIKE
                          'https://www.fanqienovel.com/page/%%'
                      )
                    ORDER BY id
                    """
                )
                return [dict(row) for row in cursor.fetchall()]

    def seed_fanqie_cover_jobs(
        self,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        values = list(rows)
        if not values:
            return 0
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO library_fanqie_cover_jobs (
                      catalog_id,catalog_source_id,title,author,book_id,status
                    ) VALUES (%s,%s,%s,%s,%s,'pending')
                    ON DUPLICATE KEY UPDATE
                      status=IF(
                        catalog_source_id<>VALUES(catalog_source_id)
                        OR title<>VALUES(title)
                        OR author<>VALUES(author),
                        'pending', status
                      ),
                      attempts=IF(
                        catalog_source_id<>VALUES(catalog_source_id)
                        OR title<>VALUES(title)
                        OR author<>VALUES(author),
                        0, attempts
                      ),
                      book_id=IF(
                        VALUES(book_id) IS NOT NULL
                        AND VALUES(book_id)<>'',
                        VALUES(book_id), book_id
                      ),
                      catalog_source_id=VALUES(catalog_source_id),
                      title=VALUES(title), author=VALUES(author)
                    """,
                    [
                        (
                            int(row["catalog_id"]),
                            str(row["source_id"]),
                            str(row["title"]),
                            str(row["author"]),
                            str(row.get("book_id") or "") or None,
                        )
                        for row in values
                    ],
                )
                return int(cursor.rowcount)

    def claim_fanqie_cover_jobs(
        self,
        *,
        limit: int,
        catalog_id: int = 0,
    ) -> list[dict[str, Any]]:
        scope_params: list[Any] = []
        scope = ""
        if catalog_id:
            scope = " AND catalog_id=%s"
            scope_params.append(int(catalog_id))
        safe_limit = min(max(int(limit), 1), 1000)
        token = str(uuid.uuid4())
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE library_fanqie_cover_jobs
                    SET status='failed', lease_owner=NULL, lease_token=NULL,
                        lease_expires_at=NULL
                    WHERE status='processing'
                      AND lease_expires_at<UTC_TIMESTAMP(6)
                    """
                )
                cursor.execute(
                    """
                    (SELECT * FROM library_fanqie_cover_jobs
                     WHERE status='pending' AND attempts<5
                       AND catalog_source_id LIKE 'fanqie-%%'
                    """
                    + scope
                    + """
                     ORDER BY attempts,catalog_id
                     LIMIT %s FOR UPDATE SKIP LOCKED)
                    UNION ALL
                    (SELECT * FROM library_fanqie_cover_jobs
                     WHERE status='failed' AND attempts<5
                       AND catalog_source_id LIKE 'fanqie-%%'
                    """
                    + scope
                    + """
                     ORDER BY attempts,catalog_id
                     LIMIT %s FOR UPDATE SKIP LOCKED)
                    LIMIT %s
                    """,
                    [*scope_params, safe_limit,
                     *scope_params, safe_limit,
                     safe_limit],
                )
                rows = [dict(row) for row in cursor.fetchall()]
                if rows:
                    cursor.executemany(
                        """
                        UPDATE library_fanqie_cover_jobs
                        SET status='processing',lease_owner='fanqie-cover',
                            lease_token=%s,
                            lease_expires_at=UTC_TIMESTAMP(6)
                              + INTERVAL 30 MINUTE
                        WHERE catalog_id=%s
                        """,
                        [(token, int(row["catalog_id"])) for row in rows],
                    )
        for row in rows:
            row["lease_token"] = token
        return rows

    def finish_fanqie_cover_job(
        self,
        row: dict[str, Any],
        *,
        book_id: str = "",
        resolved_by: str = "",
        error: str = "",
        ai_fallback: bool = False,
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE library_fanqie_cover_jobs
                    SET book_id=COALESCE(NULLIF(%s,''),book_id),
                        status=%s,attempts=attempts+1,last_error=%s,
                        resolved_by=COALESCE(NULLIF(%s,''),resolved_by),
                        lease_owner=NULL,lease_token=NULL,
                        lease_expires_at=NULL
                    WHERE catalog_id=%s AND lease_token=%s
                    """,
                    (
                        book_id,
                        (
                            "ai_fallback"
                            if error and ai_fallback
                            else "failed" if error else "done"
                        ),
                        error[:2000] or None,
                        resolved_by,
                        int(row["catalog_id"]),
                        str(row["lease_token"]),
                    ),
                )
                if error and ai_fallback:
                    self._enqueue_generated_cover_fallback_cursor(
                        cursor,
                        catalog_id=int(row["catalog_id"]),
                        source_id=str(row.get("catalog_source_id") or ""),
                        title=str(row.get("title") or ""),
                        author=str(row.get("author") or ""),
                        reason=error,
                    )

    @staticmethod
    def _enqueue_generated_cover_fallback_cursor(
        cursor: Any,
        *,
        catalog_id: int,
        source_id: str,
        title: str,
        author: str,
        reason: str,
    ) -> None:
        """Idempotently queue title-based generation when no real cover exists."""

        cursor.execute(
            """
            INSERT IGNORE INTO library_covers (
              catalog_id,source_id,title,author,detail_url,status,attempts,
              last_error
            )
            SELECT b.id,b.source_id,b.title,b.author,b.detail_url,
                   'ai_fallback',0,%s
            FROM books AS b
            WHERE b.id=%s
              AND (b.cover_object_key IS NULL OR b.cover_object_key='')
            """,
            (str(reason)[:2000], int(catalog_id)),
        )
        cursor.execute(
            """
            INSERT INTO library_clean_cover_jobs (
              catalog_id,source_id,title,author,status,
              original_filename,attempts,last_error
            )
            SELECT b.id,COALESCE(NULLIF(%s,''),b.source_id),
                   COALESCE(NULLIF(%s,''),b.title),
                   COALESCE(NULLIF(%s,''),b.author),
                   'generate_pending',NULL,0,%s
            FROM books AS b
            WHERE b.id=%s
              AND (b.cover_object_key IS NULL OR b.cover_object_key='')
            ON DUPLICATE KEY UPDATE
              source_id=VALUES(source_id),title=VALUES(title),
              author=VALUES(author),
              status=CASE
                WHEN library_clean_cover_jobs.status IN (
                  'done','processing','pending','manual_pending',
                  'generate_pending','failed'
                ) THEN library_clean_cover_jobs.status
                ELSE 'generate_pending'
              END,
              original_filename=CASE
                WHEN library_clean_cover_jobs.status IN (
                  'done','processing','pending','manual_pending',
                  'generate_pending','failed'
                ) THEN library_clean_cover_jobs.original_filename
                ELSE NULL
              END,
              attempts=CASE
                WHEN library_clean_cover_jobs.status IN (
                  'done','processing','pending','manual_pending',
                  'generate_pending','failed'
                ) THEN library_clean_cover_jobs.attempts
                ELSE 0
              END,
              last_error=CASE
                WHEN library_clean_cover_jobs.status IN (
                  'done','processing','pending','manual_pending',
                  'generate_pending','failed'
                ) THEN library_clean_cover_jobs.last_error
                ELSE VALUES(last_error)
              END
            """,
            (
                source_id,
                title,
                author,
                f"原站封面安全失败，转 AI 文生图：{str(reason)[:1800]}",
                int(catalog_id),
            ),
        )

    def enqueue_generated_cover_fallback(
        self,
        *,
        catalog_id: int,
        source_id: str,
        title: str,
        author: str,
        reason: str,
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                self._enqueue_generated_cover_fallback_cursor(
                    cursor,
                    catalog_id=catalog_id,
                    source_id=source_id,
                    title=title,
                    author=author,
                    reason=reason,
                )

    def seed_terminal_cover_fallback_jobs(
        self,
        *,
        library_id: str = "",
        source: str = "",
        limit: int = 10000,
    ) -> int:
        """Migrate historical explicitly rejected source failures into AI.

        Permanent failures (missing image, 404, identity mismatch, unsafe
        candidate, and validation rejection) may be eligible even on the first
        attempt.  Retry exhaustion alone never proves that a cover is absent,
        so transient, anti-bot, parsing, and infrastructure failures remain
        outside the AI queue.
        """

        library_scope = str(library_id or "").strip().lower()
        if library_scope not in {"", "local", "fanqie"}:
            raise ValueError("未知封面同步书库范围")
        source_scope = str(source or "").strip().lower()
        if source_scope not in {
            "", "txt80", "xbiquge", "ixdzs", "shubaow", "linovelib"
        }:
            raise ValueError("未知封面同步来源范围")
        safe_limit = min(max(int(limit), 1), 50000)
        library_filter = " AND b.library_id=%s" if library_scope else ""
        source_condition_map = {
            "txt80": """
              c.detail_url LIKE 'https://www.txt80.cc/%%/txt%%.html'
              AND c.source_id REGEXP '^[0-9]+$'
            """,
            "xbiquge": """
              c.source_id LIKE 'xbiquge-%%'
              AND (
                c.detail_url LIKE 'https://www.xbiquge.info/%%'
                OR c.detail_url LIKE 'https://xbiquge.info/%%'
              )
            """,
            "ixdzs": """
              c.source_id LIKE 'ixdzs-%%'
              AND c.detail_url LIKE 'https://ixdzs8.com/read/%%/'
            """,
            "shubaow": """
              c.source_id LIKE 'shubaow-%%'
              AND (
                c.detail_url LIKE 'https://www.shubaow.org/book%%.html'
                OR c.detail_url LIKE 'https://shubaow.org/book%%.html'
              )
            """,
            "linovelib": """
              c.source_id LIKE 'linovelib-%%'
              AND (
                c.detail_url LIKE 'https://www.linovelib.com/novel/%%.html'
                OR c.detail_url LIKE 'https://linovelib.com/novel/%%.html'
              )
            """,
        }
        source_filter = (
            f" AND ({source_condition_map[source_scope]})"
            if source_scope
            else ""
        )
        params: list[Any] = []
        if library_scope:
            params.append(library_scope)
        params.append(safe_limit)
        migrated = 0
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT c.catalog_id,c.source_id,c.title,c.author,
                           c.attempts,c.last_error,'library_covers' AS queue_name
                    FROM library_covers AS c
                    JOIN books AS b ON b.id=c.catalog_id
                    WHERE c.status='failed'
                      AND (b.cover_object_key IS NULL OR b.cover_object_key='')
                      {library_filter}
                      {source_filter}
                    ORDER BY c.catalog_id
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = [dict(row) for row in cursor.fetchall()]
                if (
                    not source_scope
                    and library_scope in {"", "fanqie"}
                    and len(rows) < safe_limit
                ):
                    cursor.execute(
                        """
                        SELECT j.catalog_id,j.catalog_source_id AS source_id,
                               j.title,j.author,j.attempts,j.last_error,
                               'fanqie_cover_jobs' AS queue_name
                        FROM library_fanqie_cover_jobs AS j
                        JOIN books AS b ON b.id=j.catalog_id
                        WHERE j.status='failed'
                          AND (b.cover_object_key IS NULL
                               OR b.cover_object_key='')
                        ORDER BY j.catalog_id
                        LIMIT %s
                        """,
                        (safe_limit - len(rows),),
                    )
                    rows.extend(dict(row) for row in cursor.fetchall())
                for item in rows:
                    error = str(item.get("last_error") or "")
                    if not should_generate_ai_fallback(
                        error,
                        attempts=int(item.get("attempts") or 0),
                    ):
                        continue
                    self._enqueue_generated_cover_fallback_cursor(
                        cursor,
                        catalog_id=int(item["catalog_id"]),
                        source_id=str(item.get("source_id") or ""),
                        title=str(item.get("title") or ""),
                        author=str(item.get("author") or ""),
                        reason=error,
                    )
                    table = str(item["queue_name"])
                    if table == "library_covers":
                        cursor.execute(
                            """
                            UPDATE library_covers SET status='ai_fallback'
                            WHERE catalog_id=%s AND status='failed'
                            """,
                            (int(item["catalog_id"]),),
                        )
                    else:
                        cursor.execute(
                            """
                            UPDATE library_fanqie_cover_jobs
                            SET status='ai_fallback'
                            WHERE catalog_id=%s AND status='failed'
                            """,
                            (int(item["catalog_id"]),),
                        )
                    migrated += 1
        return migrated

    def clean_cover_status(self) -> dict[str, int | bool]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      SUM(detail_url LIKE 'https://www.txt80.cc/%%')
                        AS txt80_total,
                      SUM(
                        detail_url LIKE 'https://www.txt80.cc/%%'
                        AND status<>'done'
                      ) AS txt80_not_ready
                    FROM library_covers
                    """
                )
                covers = dict(cursor.fetchone())
                cursor.execute(
                    """
                    SELECT COUNT(*) AS job_total,
                      SUM(status<>'done') AS pending,
                      SUM(
                        status='done'
                        AND (
                          replacement_filename IS NULL
                          OR replacement_filename=''
                        )
                      ) AS invalid_current
                    FROM library_clean_cover_jobs
                    """
                )
                jobs = dict(cursor.fetchone())
        report = {
            "txt80_total": int(covers["txt80_total"] or 0),
            "txt80_not_ready": int(covers["txt80_not_ready"] or 0),
            "job_total": int(jobs["job_total"] or 0),
            "pending_or_failed": int(jobs["pending"] or 0),
            "invalid_current": int(jobs["invalid_current"] or 0),
        }
        report["ready"] = bool(
            report["txt80_total"]
            and report["txt80_not_ready"] == 0
            and report["job_total"] == report["txt80_total"]
            and report["pending_or_failed"] == 0
            and report["invalid_current"] == 0
        )
        return report

    def clean_cover_operational_status(self) -> dict[str, int]:
        """Small indexed status projection for the admin redraw controller."""

        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      SUM(status='done' AND updated_at >= CURRENT_TIMESTAMP(6)
                          - INTERVAL 1 HOUR) AS completed_last_hour,
                      SUM(status='done' AND updated_at >= CURRENT_TIMESTAMP(6)
                          - INTERVAL 6 HOUR) AS completed_last_six_hours,
                      SUM(status='processing') AS processing,
                      SUM(status IN ('pending','manual_pending',
                                     'generate_pending','waiting_original'))
                        AS pending,
                      SUM(status='failed') AS failed
                    FROM library_clean_cover_jobs
                    """
                )
                row = dict(cursor.fetchone())
        return {key: int(value or 0) for key, value in row.items()}

    def cover_progress(self) -> dict[str, Any]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      COUNT(*) AS total,
                      SUM(status='done') AS done,
                      SUM(status='pending') AS pending,
                      SUM(status='failed') AS failed,
                      SUM(status='ai_fallback') AS ai_fallback
                    FROM library_covers
                    WHERE detail_url LIKE 'https://www.txt80.cc/%%'
                    """
                )
                local_covers = dict(cursor.fetchone())
                cursor.execute(
                    """
                    SELECT
                      COUNT(*) AS total,
                      SUM(status='done') AS done,
                      SUM(status='pending') AS pending,
                      SUM(status='failed') AS failed,
                      SUM(status='ai_fallback') AS ai_fallback
                    FROM library_fanqie_cover_jobs
                    """
                )
                fanqie_covers = dict(cursor.fetchone())
                cursor.execute(
                    """
                    SELECT
                      SUM(status IN (
                        'done','manual_pending','generate_pending',
                        'processing','failed'
                      )) AS total,
                      SUM(status='done') AS done,
                      SUM(status IN (
                        'pending','manual_pending','generate_pending',
                        'processing'
                      )) AS pending,
                      SUM(status='failed') AS failed,
                      SUM(status='generate_pending') AS generate_pending
                    FROM library_clean_cover_jobs
                    """
                )
                clean_covers = dict(cursor.fetchone())
                cursor.execute(
                    """
                    SELECT COUNT(*) AS total,
                      SUM(status='done') AS done,
                      SUM(status IN ('pending','processing')) AS pending,
                      SUM(status='failed') AS failed,
                      SUM(cover_replaced) AS covers_replaced,
                      SUM(body_replaced) AS bodies_replaced,
                      SUM(ai_fallback_queued) AS ai_fallbacks
                    FROM local_source_upgrade_jobs
                    """
                )
                source_upgrades = dict(cursor.fetchone())
        def _ints(row: dict) -> dict:
            return {k: int(v or 0) for k, v in row.items()}
        return {
            "local_sync": _ints(local_covers),
            "fanqie_sync": _ints(fanqie_covers),
            "ai_redraw": _ints(clean_covers),
            "local_source_upgrade": _ints(source_upgrades),
        }

    def clean_cover_deletion_rows(
        self,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 5000)
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT j.catalog_id,j.original_filename,
                           j.replacement_filename,c.sha256 AS replacement_sha256,
                           b.cover_object_key,c.filename AS current_filename,
                           o.object_key AS asset_object_key
                    FROM library_clean_cover_jobs j
                    JOIN books b ON b.id=j.catalog_id
                    JOIN library_covers c ON c.catalog_id=j.catalog_id
                    LEFT JOIN object_assets o
                      ON o.catalog_id=j.catalog_id AND o.asset_type='cover'
                    WHERE j.status='done'
                      AND j.original_deleted_at IS NULL
                      AND j.replacement_filename IS NOT NULL
                      AND j.replacement_filename<>''
                      AND b.cover_object_key=
                          CONCAT('封面/',j.replacement_filename)
                      AND c.filename=j.replacement_filename
                    ORDER BY j.catalog_id
                    LIMIT %s
                    """,
                    (safe_limit,),
                )
                return [dict(row) for row in cursor.fetchall()]

    def promote_clean_cover_asset(
        self,
        *,
        catalog_id: int,
        original_filename: str,
        replacement_filename: str,
        replacement_sha256: str,
        bytes_count: int,
        content_type: str,
    ) -> bool:
        """Make every durable cover pointer reference the verified AI file."""

        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT j.catalog_id
                    FROM library_clean_cover_jobs j
                    JOIN books b ON b.id=j.catalog_id
                    JOIN library_covers c ON c.catalog_id=j.catalog_id
                    WHERE j.catalog_id=%s AND j.status='done'
                      AND (j.original_filename<=>%s)
                      AND j.replacement_filename=%s
                      AND c.filename=j.replacement_filename
                      AND b.cover_object_key=
                          CONCAT('封面/',j.replacement_filename)
                    FOR UPDATE
                    """,
                    (
                        int(catalog_id),
                        original_filename or None,
                        replacement_filename,
                    ),
                )
                if not cursor.fetchone():
                    return False
                cursor.execute(
                    """
                    UPDATE library_covers
                    SET cover_url=%s
                    WHERE catalog_id=%s AND filename=%s
                    """,
                    (
                        (
                            f"local-ai-clean://{replacement_filename}"
                            if original_filename
                            else f"ai-generated://{replacement_filename}"
                        ),
                        int(catalog_id),
                        replacement_filename,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO object_assets (
                      catalog_id,asset_type,object_key,storage_backend,
                      content_type,bytes,sha256,state
                    ) VALUES (
                      %s,'cover',%s,'nas',%s,%s,%s,'available'
                    )
                    ON DUPLICATE KEY UPDATE
                      object_key=VALUES(object_key),storage_backend='nas',
                      content_type=VALUES(content_type),bytes=VALUES(bytes),
                      sha256=VALUES(sha256),state='available'
                    """,
                    (
                        int(catalog_id),
                        f"封面/{replacement_filename}",
                        content_type,
                        max(int(bytes_count), 0),
                        replacement_sha256 or None,
                    ),
                )
                return True

    def clean_cover_original_references(
        self,
        *,
        filename: str,
        catalog_id: int,
    ) -> dict[str, int]:
        """Count live references before a superseded cover is unlinked."""

        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM books
                       WHERE cover_object_key IN (%s,CONCAT('封面/',%s)))
                        AS books,
                      (SELECT COUNT(*) FROM library_covers
                       WHERE filename=%s) AS covers,
                      (SELECT COUNT(*) FROM object_assets
                       WHERE asset_type='cover'
                         AND object_key IN (%s,CONCAT('封面/',%s)))
                        AS assets,
                      (SELECT COUNT(*) FROM library_clean_cover_jobs
                       WHERE catalog_id<>%s AND original_filename=%s
                         AND status IN (
                           'pending','manual_pending','processing'
                         )) AS active_jobs
                    """,
                    (
                        filename,
                        filename,
                        filename,
                        filename,
                        filename,
                        int(catalog_id),
                        filename,
                    ),
                )
                row = dict(cursor.fetchone() or {})
                return {
                    key: int(row.get(key) or 0)
                    for key in ("books", "covers", "assets", "active_jobs")
                }

    def source_replaced_cover_deletion_rows(
        self,
        *,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Return exact source-replacement predecessors still needing retire."""

        safe_limit = min(max(int(limit), 1), 10000)
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT j.catalog_id,j.original_filename,
                           j.replacement_filename
                    FROM library_clean_cover_jobs j
                    JOIN books b ON b.id=j.catalog_id
                    JOIN library_covers c ON c.catalog_id=j.catalog_id
                    JOIN object_assets o
                      ON o.catalog_id=j.catalog_id AND o.asset_type='cover'
                    WHERE j.status='source_replaced'
                      AND j.original_deleted_at IS NULL
                      AND j.replacement_filename IS NOT NULL
                      AND j.replacement_filename<>''
                      AND b.cover_object_key=
                          CONCAT('封面/',j.replacement_filename)
                      AND c.filename=j.replacement_filename
                      AND o.object_key=
                          CONCAT('封面/',j.replacement_filename)
                      AND o.state='available'
                    ORDER BY j.catalog_id
                    LIMIT %s
                    """,
                    (safe_limit,),
                )
                return [dict(row) for row in cursor.fetchall()]

    def unreferenced_source_cover_rows(
        self,
        rows: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Filter a bounded candidate batch through indexed reference joins."""

        values = [
            (int(row["catalog_id"]), str(row.get("original_filename") or ""))
            for row in rows
            if str(row.get("original_filename") or "")
        ]
        if not values:
            return []
        protected: set[tuple[int, str]] = set()
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DROP TEMPORARY TABLE IF EXISTS "
                    "source_cover_cleanup_candidates"
                )
                cursor.execute(
                    """
                    CREATE TEMPORARY TABLE source_cover_cleanup_candidates (
                      catalog_id BIGINT UNSIGNED NOT NULL,
                      filename VARCHAR(512) NOT NULL,
                      PRIMARY KEY (catalog_id,filename),
                      KEY idx_source_cover_cleanup_filename (filename)
                    ) ENGINE=InnoDB
                    """
                )
                cursor.executemany(
                    """
                    INSERT IGNORE INTO source_cover_cleanup_candidates
                      (catalog_id,filename) VALUES (%s,%s)
                    """,
                    values,
                )
                queries = (
                    """
                    SELECT p.catalog_id,p.filename
                    FROM source_cover_cleanup_candidates p
                    JOIN books b ON b.cover_object_key IN (
                      p.filename,CONCAT('封面/',p.filename)
                    )
                    """,
                    """
                    SELECT p.catalog_id,p.filename
                    FROM source_cover_cleanup_candidates p
                    JOIN library_covers c ON c.filename=p.filename
                    """,
                    """
                    SELECT p.catalog_id,p.filename
                    FROM source_cover_cleanup_candidates p
                    JOIN object_assets o ON o.asset_type='cover'
                      AND o.object_key IN (
                        p.filename,CONCAT('封面/',p.filename)
                      )
                    """,
                    """
                    SELECT p.catalog_id,p.filename
                    FROM source_cover_cleanup_candidates p
                    JOIN library_clean_cover_jobs j
                      ON j.catalog_id<>p.catalog_id
                     AND j.original_filename=p.filename
                     AND j.status IN (
                       'pending','manual_pending','processing'
                     )
                    """,
                )
                for query in queries:
                    cursor.execute(query)
                    protected.update(
                        (int(row["catalog_id"]), str(row["filename"]))
                        for row in cursor.fetchall()
                    )
        return [
            row
            for row in rows
            if (
                int(row["catalog_id"]),
                str(row.get("original_filename") or ""),
            ) not in protected
        ]

    def mark_clean_cover_deleted(
        self,
        *,
        catalog_id: int,
        original_filename: str,
        replacement_filename: str,
    ) -> bool:
        return self.mark_clean_covers_deleted_batch(
            [
                {
                    "catalog_id": int(catalog_id),
                    "original_filename": original_filename or None,
                    "replacement_filename": replacement_filename,
                }
            ]
        ) == 1

    def mark_clean_covers_deleted_batch(
        self,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        """Persist verified source/AI predecessor cleanup in one transaction."""

        values = [
            (
                int(row["catalog_id"]),
                str(row.get("original_filename") or "") or None,
                str(row.get("replacement_filename") or ""),
            )
            for row in rows
        ]
        if not values:
            return 0
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    UPDATE library_clean_cover_jobs j
                    JOIN books b ON b.id=j.catalog_id
                    JOIN library_covers c ON c.catalog_id=j.catalog_id
                    JOIN object_assets o
                      ON o.catalog_id=j.catalog_id AND o.asset_type='cover'
                    SET j.original_deleted_at=UTC_TIMESTAMP(6)
                    WHERE j.catalog_id=%s
                      AND j.status IN ('done','source_replaced')
                      AND (j.original_filename<=>%s)
                      AND j.replacement_filename=%s
                      AND b.cover_object_key=
                          CONCAT('封面/',j.replacement_filename)
                      AND c.filename=j.replacement_filename
                      AND o.object_key=
                          CONCAT('封面/',j.replacement_filename)
                      AND o.state='available'
                    """,
                    values,
                )
                return int(cursor.rowcount or 0)

    def recover_clean_cover_jobs(self, *, batch_size: int = 100) -> int:
        """Recover expired AI jobs without waiting on a live row lease.

        A single broad UPDATE used to wait behind an unrelated processing row
        and could exceed the default MySQL read timeout at worker startup.
        Claiming primary keys in small SKIP LOCKED batches keeps recovery
        bounded; a currently locked row is safely picked up by the next pass.
        """

        safe_batch = min(max(int(batch_size), 1), 1000)
        recovered = 0
        while True:
            with self.pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT catalog_id,original_filename
                        FROM library_clean_cover_jobs
                        FORCE INDEX (idx_library_clean_cover_claim)
                        WHERE status='processing'
                          AND (
                            lease_expires_at IS NULL
                            OR lease_expires_at<UTC_TIMESTAMP(6)
                          )
                        ORDER BY attempts,catalog_id
                        LIMIT %s FOR UPDATE SKIP LOCKED
                        """,
                        (safe_batch,),
                    )
                    rows = [dict(row) for row in cursor.fetchall()]
                    if not rows:
                        return recovered
                    cursor.executemany(
                        """
                        UPDATE library_clean_cover_jobs
                        SET status=%s,
                            last_error='AI 重绘进程中断，任务已自动恢复',
                            lease_owner=NULL,lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s AND status='processing'
                        """,
                        [
                            (
                                (
                                    'generate_pending'
                                    if row.get('original_filename') is None
                                    else 'manual_pending'
                                ),
                                int(row['catalog_id']),
                            )
                            for row in rows
                        ],
                    )
                    recovered += int(cursor.rowcount)
            if len(rows) < safe_batch:
                return recovered

    def enqueue_clean_cover_redraw(
        self,
        *,
        catalog_id: int,
        source_id: str,
        title: str,
        author: str,
        original_filename: str,
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO library_clean_cover_jobs (
                      catalog_id,source_id,title,author,status,
                      original_filename,attempts,last_error
                    ) VALUES (%s,%s,%s,%s,'manual_pending',%s,0,NULL)
                    ON DUPLICATE KEY UPDATE
                      source_id=VALUES(source_id),title=VALUES(title),
                      author=VALUES(author),status='manual_pending',
                      original_filename=VALUES(original_filename),
                      replacement_url=NULL,replacement_filename=NULL,
                      verification_source=NULL,original_deleted_at=NULL,
                      attempts=0,ai_session_id=NULL,
                      last_error=NULL,lease_owner=NULL,lease_token=NULL,
                      lease_expires_at=NULL
                    """,
                    (
                        int(catalog_id),
                        source_id,
                        title,
                        author,
                        original_filename,
                    ),
                )

    def enqueue_local_source_lookup(
        self,
        *,
        catalog_id: int,
        source_id: str,
        title: str,
        author: str,
        original_filename: str,
    ) -> None:
        """Queue a watermarked local cover for real-source lookup first."""

        if not str(source_id or "").isdigit():
            raise ValueError("三站封面检索只接受 TXT80/TXT020 本地来源")
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO library_clean_cover_jobs (
                      catalog_id,source_id,title,author,status,
                      original_filename,attempts,last_error
                    ) VALUES (
                      %s,%s,%s,%s,'source_lookup_pending',%s,0,
                      '等待三站精确匹配，禁止直接调用 AI'
                    )
                    ON DUPLICATE KEY UPDATE
                      source_id=VALUES(source_id),title=VALUES(title),
                      author=VALUES(author),status='source_lookup_pending',
                      original_filename=VALUES(original_filename),
                      replacement_url=NULL,replacement_filename=NULL,
                      verification_source=NULL,original_deleted_at=NULL,
                      attempts=0,ai_session_id=NULL,
                      last_error=VALUES(last_error),lease_owner=NULL,
                      lease_token=NULL,lease_expires_at=NULL
                    """,
                    (
                        int(catalog_id),
                        str(source_id),
                        str(title),
                        str(author),
                        str(original_filename),
                    ),
                )

    def claim_clean_cover_job(
        self,
        *,
        worker_id: str,
        max_attempts: int,
    ) -> dict[str, Any] | None:
        token = str(uuid.uuid4())
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT j.*,c.filename AS current_filename,
                           c.sha256 AS current_sha256,b.library_id,b.category,
                           m.summary,m.genre_tags,m.tone_tags,
                           m.primary_tone_tags,m.secondary_tone_tags,
                           m.keyword_counts
                    FROM library_clean_cover_jobs j
                    LEFT JOIN library_covers c ON c.catalog_id=j.catalog_id
                    JOIN books b ON b.id=j.catalog_id
                    LEFT JOIN book_metadata m ON m.catalog_id=b.id
                    WHERE j.status IN (
                        'pending','manual_pending','generate_pending'
                      )
                      AND j.attempts<%s
                      AND (
                        j.status='generate_pending'
                        OR (c.status='done' AND c.filename IS NOT NULL)
                      )
                    ORDER BY j.status='generate_pending' DESC,
                             j.attempts,j.updated_at,j.catalog_id
                    LIMIT 1 FOR UPDATE SKIP LOCKED
                    """,
                    (int(max_attempts),),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                session_id = (
                    f"cover-ai-{int(row['catalog_id'])}-"
                    f"{int(datetime.now(UTC).timestamp())}"
                )
                cursor.execute(
                    """
                    UPDATE library_clean_cover_jobs
                    SET status='processing',attempts=attempts+1,
                        ai_session_id=%s,lease_owner=%s,lease_token=%s,
                        lease_expires_at=UTC_TIMESTAMP(6)
                          + INTERVAL 2 HOUR,last_error=NULL
                    WHERE catalog_id=%s
                    """,
                    (
                        session_id,
                        worker_id,
                        token,
                        int(row["catalog_id"]),
                    ),
                )
                result = dict(row)
                result["lease_token"] = token
                result["ai_session_id"] = session_id
                result["generation_mode"] = (
                    "title_generate"
                    if str(row.get("status") or "") == "generate_pending"
                    else "image_edit"
                )
                return result

    def finish_clean_cover_job(
        self,
        row: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
        status: str = "done",
        error: str = "",
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                if result is not None:
                    cursor.execute(
                        """
                        UPDATE library_covers
                        SET filename=%s,sha256=%s,status='done',
                            cover_url=%s,last_error=NULL
                        WHERE catalog_id=%s AND source_id=%s
                          AND title=%s AND author=%s
                        """,
                        (
                            result["filename"],
                            result["sha256"],
                            (
                                f"ai-generated://{result['filename']}"
                                if str(row.get("generation_mode") or "")
                                == "title_generate"
                                else f"local-ai-clean://{result['filename']}"
                            ),
                            int(row["catalog_id"]),
                            str(row["source_id"]),
                            str(row["title"]),
                            str(row["author"]),
                        ),
                    )
                    cursor.execute(
                        """
                        UPDATE books
                        SET cover_object_key=CONCAT('封面/', %s),
                            row_version=row_version+1 WHERE id=%s
                        """,
                        (result["filename"], int(row["catalog_id"])),
                    )
                    cursor.execute(
                        """
                        INSERT INTO object_assets (
                          catalog_id,asset_type,object_key,storage_backend,
                          content_type,bytes,sha256,state
                        ) VALUES (
                          %s,'cover',%s,'nas',%s,%s,%s,'available'
                        )
                        ON DUPLICATE KEY UPDATE
                          object_key=VALUES(object_key),
                          storage_backend='nas',
                          content_type=VALUES(content_type),
                          bytes=VALUES(bytes),sha256=VALUES(sha256),
                          state='available'
                        """,
                        (
                            int(row["catalog_id"]),
                            f"封面/{result['filename']}",
                            str(result.get("content_type") or "image/jpeg"),
                            max(int(result.get("bytes") or 0), 0),
                            result["sha256"],
                        ),
                    )
                    cursor.execute(
                        """
                        UPDATE library_clean_cover_jobs
                        SET status='done',original_filename=%s,
                          replacement_url=%s,replacement_filename=%s,
                          verification_source=%s,
                          source_width=%s,source_height=%s,
                          generated_width=%s,generated_height=%s,
                          original_deleted_at=NULL,
                          last_error=NULL,lease_owner=NULL,lease_token=NULL,
                          lease_expires_at=NULL
                        WHERE catalog_id=%s AND lease_token=%s
                        """,
                        (
                            str(row.get("current_filename") or "") or None,
                            (
                                f"ai-generated://{result['filename']}"
                                if str(row.get("generation_mode") or "")
                                == "title_generate"
                                else f"local-ai-clean://{result['filename']}"
                            ),
                            result["filename"],
                            (
                                "openclaw-image-generate-v1"
                                if str(row.get("generation_mode") or "")
                                == "title_generate"
                                else "openclaw-image-edit-v1"
                            ),
                            int(result["source_width"]),
                            int(result["source_height"]),
                            int(result["generated_width"]),
                            int(result["generated_height"]),
                            int(row["catalog_id"]),
                            str(row["lease_token"]),
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE library_clean_cover_jobs
                        SET status=%s,last_error=%s,lease_owner=NULL,
                            lease_token=NULL,lease_expires_at=NULL
                        WHERE catalog_id=%s AND lease_token=%s
                        """,
                        (
                            status,
                            error[:2000] or None,
                            int(row["catalog_id"]),
                            str(row["lease_token"]),
                        ),
                    )
        self.invalidate_catalog_cache()
