"""Durable MySQL jobs accelerated by Redis Streams."""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
import unicodedata
import uuid
from datetime import datetime
from typing import Any

from oohstory_library.services.library_database import MySQLConnectionPool, RedisQueueClient
from oohstory_library.services.library_cache import LibraryCacheSettings, RedisHotCache
from oohstory_library.services.library_identity_claims import (
    book_identity_hash,
    book_identity_key,
)


class LibraryDownloadQueue:
    def __init__(
        self,
        mysql: MySQLConnectionPool,
        redis_queue: RedisQueueClient,
        cache: RedisHotCache | None = None,
    ):
        self.mysql = mysql
        self.redis = redis_queue
        self.cache = cache or RedisHotCache(
            LibraryCacheSettings.from_infrastructure(redis_queue.settings)
        )

    @staticmethod
    def _refresh_book_aggregates(
        cursor: Any,
        *,
        status_keys: set[tuple[str, str]],
        facet_keys: set[tuple[str, int, str]],
    ) -> None:
        # Migration 007 maintains both aggregate tables in constant time.
        del cursor, status_keys, facet_keys

    def _invalidate_catalog_cache(self) -> None:
        self.cache.invalidate("catalog", "book", "cover")

    @staticmethod
    def _identity_key(title: Any, author: Any) -> str:
        def normalize(value: Any) -> str:
            text = unicodedata.normalize("NFKC", str(value or "")).casefold()
            return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)

        return f"{normalize(title)}\x1f{normalize(author)}"

    def schedule_serialized_refresh(
        self,
        *,
        source_name: str,
        source_id: str,
        title: str,
        author: str,
        detail_url: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Queue a verified remote revision without letting dedupe discard it."""

        identity_hash = hashlib.sha256(
            self._identity_key(title, author).encode("utf-8")
        ).digest()
        with self.mysql.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, source_id, title, author, book_status,
                           status, body_available
                    FROM books
                    WHERE source_id=%s
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (source_id,),
                )
                book = cursor.fetchone()
                matched_by = "source_id"
                if not book:
                    cursor.execute(
                        """
                        SELECT id, source_id, title, author, book_status,
                               status, body_available
                        FROM books
                        WHERE identity_hash=%s AND is_active=1
                          AND status='done' AND body_available=1
                        ORDER BY bytes DESC, id
                        LIMIT 1
                        FOR UPDATE
                        """,
                        (identity_hash,),
                    )
                    book = cursor.fetchone()
                    matched_by = "title_author"
                if not book:
                    return {"status": "not_local"}
                if str(book["status"]) != "done" or not int(
                    book["body_available"] or 0
                ):
                    return {
                        "status": "not_ready",
                        "catalog_id": int(book["id"]),
                    }
                catalog_id = int(book["id"])
                cursor.execute(
                    """
                    SELECT id, status FROM download_jobs
                    WHERE catalog_id=%s
                    FOR UPDATE
                    """,
                    (catalog_id,),
                )
                job = cursor.fetchone()
                if job and str(job["status"]) in {
                    "pending", "queued", "downloading"
                }:
                    return {
                        "status": "already_queued",
                        "catalog_id": catalog_id,
                        "job_id": int(job["id"]),
                        "matched_by": matched_by,
                    }
                serialized = json.dumps(payload, ensure_ascii=False)
                cursor.execute(
                    """
                    INSERT INTO download_jobs (
                        catalog_id, source_id, source_name, status,
                        priority, attempts, max_attempts, available_at,
                        payload, completed_at, last_error
                    ) VALUES (
                        %s, %s, %s, 'pending', 5, 0, 8,
                        UTC_TIMESTAMP(6), %s, NULL, NULL
                    )
                    ON DUPLICATE KEY UPDATE
                        source_id=VALUES(source_id),
                        source_name=VALUES(source_name),
                        status='pending', priority=5, attempts=0,
                        available_at=UTC_TIMESTAMP(6),
                        lease_owner=NULL, lease_token=NULL,
                        lease_expires_at=NULL, payload=VALUES(payload),
                        completed_at=NULL, last_error=NULL,
                        updated_at=UTC_TIMESTAMP(6)
                    """,
                    (catalog_id, source_id, source_name, serialized),
                )
                cursor.execute(
                    "SELECT id FROM download_jobs WHERE catalog_id=%s",
                    (catalog_id,),
                )
                job_id = int(cursor.fetchone()["id"])
                cursor.execute(
                    """
                    UPDATE authorized_source_updates
                    SET catalog_id=%s, state='queued', last_error=NULL,
                        last_seen_at=UTC_TIMESTAMP(6)
                    WHERE source_name=%s AND source_id=%s
                    """,
                    (catalog_id, source_name, source_id),
                )
        return {
            "status": "queued",
            "catalog_id": catalog_id,
            "job_id": job_id,
            "matched_by": matched_by,
        }

    def mark_serialized_refresh_applied(
        self,
        *,
        source_name: str,
        source_id: str,
        catalog_id: int,
        remote_revision: str,
        local_latest_chapter: str = "",
    ) -> None:
        with self.mysql.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE authorized_source_updates
                    SET catalog_id=%s, applied_revision=%s,
                        local_latest_chapter=%s,
                        state='applied', last_error=NULL,
                        applied_at=UTC_TIMESTAMP(6),
                        last_seen_at=UTC_TIMESTAMP(6)
                    WHERE source_name=%s AND source_id=%s
                    """,
                    (
                        int(catalog_id), remote_revision,
                        local_latest_chapter,
                        source_name, source_id,
                    ),
                )

    def schedule_source_recovery(
        self,
        *,
        catalog_id: int,
        candidate: dict[str, Any],
        reason: str,
        job_id: int | None = None,
        lease_token: str = "",
    ) -> dict[str, Any]:
        """Queue an exact alternate source without rebinding the book early."""

        new_source_id = str(candidate.get("source_id") or "").strip()
        new_source_name = str(candidate.get("source_name") or "").strip()
        detail_url = str(candidate.get("detail_url") or "").strip()
        source_ref = str(candidate.get("source_ref") or "").strip()
        candidate_title = str(candidate.get("title") or "").strip()
        candidate_author = str(candidate.get("author") or "").strip()
        if (
            not new_source_id
            or new_source_name not in {"txt80", "xbiquge", "ixdzs", "shubaow"}
            or not detail_url
            or not source_ref
        ):
            raise ValueError("备用正文来源字段不完整")
        candidate_identity = book_identity_key(candidate_title, candidate_author)
        with self.mysql.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id,source_id,detail_url,title,author,library_id,
                           status,body_available
                    FROM books WHERE id=%s AND is_active=1
                    FOR UPDATE
                    """,
                    (int(catalog_id),),
                )
                book = cursor.fetchone()
                if not book:
                    raise ValueError("待恢复书目不存在或已停用")
                identity_key = book_identity_key(book["title"], book["author"])
                if not identity_key or identity_key != candidate_identity:
                    raise ValueError("备用来源书名或作者与待恢复书目不一致")
                cursor.execute(
                    """
                    SELECT catalog_id FROM global_book_identity_claims
                    WHERE identity_hash=%s FOR UPDATE
                    """,
                    (book_identity_hash(identity_key),),
                )
                claim = cursor.fetchone()
                if not claim or int(claim.get("catalog_id") or 0) != int(catalog_id):
                    raise ValueError("全书库身份占位未绑定当前书目，已拒绝切源")
                cursor.execute(
                    """
                    SELECT id,title,author,status,body_available
                    FROM books
                    WHERE id<>%s AND (
                        source_id=%s OR detail_url_hash=UNHEX(SHA2(%s,256))
                    )
                    LIMIT 1 FOR UPDATE
                    """,
                    (int(catalog_id), new_source_id, detail_url),
                )
                conflict = cursor.fetchone()
                if conflict:
                    raise ValueError(
                        f"备用来源已绑定书目 {int(conflict['id'])}，已拒绝覆盖"
                    )
                cursor.execute(
                    """
                    SELECT id,status,source_id,source_name,payload,lease_token
                    FROM download_jobs WHERE catalog_id=%s FOR UPDATE
                    """,
                    (int(catalog_id),),
                )
                job = cursor.fetchone()
                if job_id and (not job or int(job["id"]) != int(job_id)):
                    raise ValueError("待恢复下载任务已变化")
                if job and str(job.get("status") or "") == "downloading":
                    if not lease_token or str(job.get("lease_token") or "") != lease_token:
                        raise ValueError("待恢复下载任务正在由其他 worker 处理")
                payload = job.get("payload") if job else {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                recovery = payload.get("source_recovery")
                if not isinstance(recovery, dict):
                    recovery = {}
                history = recovery.get("history")
                if not isinstance(history, list):
                    history = []
                previous_source_id = str(
                    (job or {}).get("source_id") or book.get("source_id") or ""
                )
                previous_source_name = str(
                    (job or {}).get("source_name")
                    or ("txt80" if previous_source_id.isdigit() else previous_source_id.split("-", 1)[0])
                )
                history.append(
                    {
                        "source_id": previous_source_id,
                        "source_name": previous_source_name,
                        "error": str(reason or "")[:500],
                    }
                )
                payload.update(
                    {
                        "update_mode": "source_recovery",
                        "source_ref": source_ref,
                        "source_recovery": {
                            **recovery,
                            "original_source_id": str(
                                recovery.get("original_source_id")
                                or book.get("source_id")
                                or ""
                            ),
                            "original_detail_url": str(
                                recovery.get("original_detail_url")
                                or book.get("detail_url")
                                or ""
                            ),
                            "history": history[-12:],
                            "candidate_source_id": new_source_id,
                            "candidate_source_name": new_source_name,
                        },
                    }
                )
                serialized = json.dumps(payload, ensure_ascii=False)
                if job:
                    cursor.execute(
                        """
                        UPDATE download_jobs
                        SET source_id=%s,source_name=%s,status='pending',
                            priority=1,attempts=0,max_attempts=8,
                            available_at=UTC_TIMESTAMP(6),payload=%s,
                            completed_at=NULL,lease_owner=NULL,lease_token=NULL,
                            lease_expires_at=NULL,last_error=%s,
                            updated_at=UTC_TIMESTAMP(6)
                        WHERE id=%s
                        """,
                        (
                            new_source_id,
                            new_source_name,
                            serialized,
                            str(reason or "")[:2000],
                            int(job["id"]),
                        ),
                    )
                    scheduled_job_id = int(job["id"])
                else:
                    cursor.execute(
                        """
                        INSERT INTO download_jobs (
                            catalog_id,source_id,source_name,status,priority,
                            attempts,max_attempts,available_at,payload,last_error
                        ) VALUES (
                            %s,%s,%s,'pending',1,0,8,UTC_TIMESTAMP(6),%s,%s
                        )
                        """,
                        (
                            int(catalog_id),
                            new_source_id,
                            new_source_name,
                            serialized,
                            str(reason or "")[:2000],
                        ),
                    )
                    scheduled_job_id = int(cursor.lastrowid)
                cursor.execute(
                    """
                    UPDATE books
                    SET status='failed',last_error=%s,
                        updated_at=UTC_TIMESTAMP(6),row_version=row_version+1
                    WHERE id=%s AND body_available=0
                    """,
                    (str(reason or "")[:2000], int(catalog_id)),
                )
        self._invalidate_catalog_cache()
        return {
            "status": "scheduled",
            "job_id": scheduled_job_id,
            "catalog_id": int(catalog_id),
            "source_id": new_source_id,
            "source_name": new_source_name,
        }

    def recover_stale(self, *, batch_size: int = 200) -> dict[str, int]:
        """Recover stale jobs in lock-skipping primary-key batches."""

        safe_batch = min(max(int(batch_size), 1), 2000)

        def recover_status(*, status: str, index: str, stale_clause: str) -> int:
            recovered = 0
            while True:
                with self.mysql.connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            f"""
                            SELECT id FROM download_jobs FORCE INDEX ({index})
                            WHERE status=%s AND {stale_clause}
                            ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED
                            """,
                            (status, safe_batch),
                        )
                        rows = [int(row["id"]) for row in cursor.fetchall()]
                        if not rows:
                            return recovered
                        if status == "downloading":
                            cursor.executemany(
                                """
                                UPDATE download_jobs
                                SET status='pending',lease_owner=NULL,
                                    lease_token=NULL,lease_expires_at=NULL,
                                    available_at=UTC_TIMESTAMP(6),
                                    last_error=COALESCE(
                                      last_error,
                                      '任务租约过期，已自动重新排队'
                                    )
                                WHERE id=%s AND status='downloading'
                                """,
                                [(job_id,) for job_id in rows],
                            )
                        else:
                            cursor.executemany(
                                """
                                UPDATE download_jobs
                                SET status='pending',
                                    available_at=UTC_TIMESTAMP(6)
                                WHERE id=%s AND status='queued'
                                """,
                                [(job_id,) for job_id in rows],
                            )
                        recovered += int(cursor.rowcount)
                if len(rows) < safe_batch:
                    return recovered

        expired_leases = recover_status(
            status="downloading",
            index="idx_download_jobs_expired_lease",
            stale_clause="lease_expires_at<UTC_TIMESTAMP(6)",
        )
        stale_queued = recover_status(
            status="queued",
            index="idx_download_jobs_stale_queue",
            stale_clause=(
                "updated_at<UTC_TIMESTAMP(6) - INTERVAL 10 MINUTE"
            ),
        )
        return {
            "expired_leases": expired_leases,
            "stale_queued": stale_queued,
        }

    def dispatch(self, *, limit: int = 1000) -> dict[str, Any]:
        requested_limit = min(max(int(limit), 1), 10000)
        max_stream_depth = min(
            max(
                int(os.getenv("WEBNOVEL_DOWNLOAD_STREAM_MAX_DEPTH", "2000")),
                100,
            ),
            100000,
        )
        stream_depth = int(
            self.redis.client.xlen(
                self.redis.key(self.redis.DOWNLOAD_STREAM)
            )
        )
        limit = min(
            requested_limit,
            max(max_stream_depth - stream_depth, 0),
        )
        if limit == 0:
            return {
                "selected": 0,
                "dispatched": 0,
                "failed": 0,
                "job_ids": [],
                "recovered": {
                    "expired_leases": 0,
                    "stale_queued": 0,
                },
                "stream_depth": stream_depth,
                "stream_max_depth": max_stream_depth,
            }
        recovered = self.recover_stale()
        sources = ("xbiquge", "ixdzs", "shubaow", "txt80")
        per_source = max((limit + len(sources) - 1) // len(sources), 1)
        selected: list[dict[str, Any]] = []
        with self.mysql.connection() as connection:
            with connection.cursor() as cursor:
                for source_name in sources:
                    cursor.execute(
                        """
                        SELECT
                            id, catalog_id, source_id, source_name, priority
                        FROM download_jobs
                        WHERE status='pending'
                          AND source_name=%s
                          AND attempts<max_attempts
                          AND available_at<=UTC_TIMESTAMP(6)
                        ORDER BY priority, attempts, id
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                        """,
                        (source_name, per_source),
                    )
                    selected.extend(dict(row) for row in cursor.fetchall())
                selected.sort(
                    key=lambda row: (
                        int(row["priority"]),
                        int(row["id"]),
                    )
                )
                selected = selected[:limit]
                if selected:
                    cursor.executemany(
                        """
                        UPDATE download_jobs
                        SET status='queued', updated_at=UTC_TIMESTAMP(6)
                        WHERE id=%s AND status='pending'
                        """,
                        [(int(row["id"]),) for row in selected],
                    )

        dispatched: list[int] = []
        failures: list[tuple[int, str]] = []
        for row in selected:
            job_id = int(row["id"])
            try:
                self.redis.enqueue_download(
                    job_id=job_id,
                    catalog_id=int(row["catalog_id"]),
                    source_name=str(row["source_name"]),
                    priority=int(row["priority"]),
                )
                dispatched.append(job_id)
            except Exception as exc:
                failures.append(
                    (job_id, f"{type(exc).__name__}: {str(exc)[:500]}")
                )
        if failures:
            with self.mysql.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        UPDATE download_jobs
                        SET status='pending',
                            last_error=%s,
                            available_at=UTC_TIMESTAMP(6)
                        WHERE id=%s AND status='queued'
                        """,
                        [(error, job_id) for job_id, error in failures],
                    )
        return {
            "selected": len(selected),
            "dispatched": len(dispatched),
            "failed": len(failures),
            "job_ids": dispatched,
            "recovered": recovered,
            "stream_depth": stream_depth + len(dispatched),
            "stream_max_depth": max_stream_depth,
        }

    def claim(
        self,
        *,
        job_id: int,
        worker_id: str,
        lease_seconds: int = 1800,
    ) -> dict[str, Any] | None:
        lease_seconds = min(max(int(lease_seconds), 60), 21600)
        token = str(uuid.uuid4())
        with self.mysql.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        jobs.*,
                        books.title,
                        books.author,
                        books.category,
                        books.book_status,
                        books.detail_url
                    FROM download_jobs AS jobs
                    JOIN books ON books.id=jobs.catalog_id
                    WHERE jobs.id=%s
                    FOR UPDATE
                    """,
                    (int(job_id),),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                status = str(row["status"])
                lease_expired = (
                    status == "downloading"
                    and row.get("lease_expires_at")
                    and row["lease_expires_at"] < datetime.utcnow()
                )
                if status not in {"queued", "pending"} and not lease_expired:
                    return None
                cursor.execute(
                    """
                    UPDATE download_jobs
                    SET status='downloading',
                        lease_owner=%s,
                        lease_token=%s,
                        lease_expires_at=UTC_TIMESTAMP(6)
                            + INTERVAL %s SECOND,
                        updated_at=UTC_TIMESTAMP(6)
                    WHERE id=%s
                    """,
                    (worker_id, token, lease_seconds, int(job_id)),
                )
                result = dict(row)
                result["lease_token"] = token
                payload = result.get("payload")
                if isinstance(payload, str):
                    try:
                        result["payload"] = json.loads(payload)
                    except json.JSONDecodeError:
                        result["payload"] = {}
                return result

    def complete(
        self,
        *,
        job_id: int,
        lease_token: str,
        catalog_id: int,
        body_object_key: str,
        legacy_output_path: str,
        bytes_count: int,
        sha256: str,
    ) -> bool:
        if not str(body_object_key or "").strip():
            raise ValueError("正文对象键不能为空")
        with self.mysql.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT library_id, status, category, body_available
                    FROM books
                    WHERE id=%s
                    FOR UPDATE
                    """,
                    (int(catalog_id),),
                )
                previous = cursor.fetchone()
                if not previous:
                    return False
                cursor.execute(
                    """
                    UPDATE download_jobs
                    SET status='done',
                        completed_at=UTC_TIMESTAMP(6),
                        lease_owner=NULL,
                        lease_token=NULL,
                        lease_expires_at=NULL,
                        last_error=NULL
                    WHERE id=%s
                      AND status='downloading'
                      AND lease_token=%s
                    """,
                    (int(job_id), lease_token),
                )
                if cursor.rowcount != 1:
                    return False
                cursor.execute(
                    """
                    UPDATE books
                    SET status='done',
                        body_object_key=%s,
                        legacy_output_path=%s,
                        bytes=%s,
                        sha256=%s,
                        last_error=NULL,
                        updated_at=UTC_TIMESTAMP(6),
                        row_version=row_version+1
                    WHERE id=%s
                    """,
                    (
                        body_object_key or None,
                        legacy_output_path or None,
                        max(int(bytes_count), 0),
                        sha256 or None,
                        int(catalog_id),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO object_assets (
                        catalog_id, asset_type, object_key,
                        storage_backend, content_type, bytes, sha256, state
                    ) VALUES (
                        %s, 'body', %s, 'nas',
                        'text/plain; charset=utf-8', %s, %s, 'available'
                    )
                    ON DUPLICATE KEY UPDATE
                        object_key=VALUES(object_key),
                        bytes=VALUES(bytes),
                        sha256=VALUES(sha256),
                        state='available'
                    """,
                    (
                        int(catalog_id),
                        body_object_key,
                        max(int(bytes_count), 0),
                        sha256 or None,
                    ),
                )
                cursor.execute(
                    """
                    SELECT library_id, status, category, body_available
                    FROM books
                    WHERE id=%s
                    """,
                    (int(catalog_id),),
                )
                current = cursor.fetchone()
                self._refresh_book_aggregates(
                    cursor,
                    status_keys={
                        (
                            str(previous["library_id"]),
                            str(previous["status"]),
                        ),
                        (
                            str(current["library_id"]),
                            str(current["status"]),
                        ),
                    },
                    facet_keys={
                        (
                            str(previous["library_id"]),
                            int(previous["body_available"]),
                            str(previous["category"] or "未分类"),
                        ),
                        (
                            str(current["library_id"]),
                            int(current["body_available"]),
                            str(current["category"] or "未分类"),
                        ),
                    },
                )
        self._invalidate_catalog_cache()
        return True

    def complete_duplicate(
        self,
        *,
        job_id: int,
        lease_token: str,
        catalog_id: int,
        kept_catalog_id: int,
    ) -> bool:
        with self.mysql.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT library_id, status, category, body_available
                    FROM books
                    WHERE id=%s
                    FOR UPDATE
                    """,
                    (int(catalog_id),),
                )
                book = cursor.fetchone()
                if not book:
                    return False
                cursor.execute(
                    """
                    UPDATE download_jobs
                    SET status='done',
                        completed_at=UTC_TIMESTAMP(6),
                        lease_owner=NULL,
                        lease_token=NULL,
                        lease_expires_at=NULL,
                        last_error=%s
                    WHERE id=%s
                      AND status='downloading'
                      AND lease_token=%s
                    """,
                    (
                        f"与书目 {int(kept_catalog_id)} 重复，未重复下载",
                        int(job_id),
                        lease_token,
                    ),
                )
                if cursor.rowcount != 1:
                    return False
                cursor.execute(
                    """
                    UPDATE books
                    SET status='duplicate',
                        last_error=NULL,
                        updated_at=UTC_TIMESTAMP(6),
                        row_version=row_version+1
                    WHERE id=%s
                    """,
                    (int(catalog_id),),
                )
                library_id = str(book["library_id"])
                self._refresh_book_aggregates(
                    cursor,
                    status_keys={
                        (library_id, str(book["status"])),
                        (library_id, "duplicate"),
                    },
                    facet_keys={
                        (
                            library_id,
                            int(book["body_available"]),
                            str(book["category"] or "未分类"),
                        )
                    },
                )
        self._invalidate_catalog_cache()
        return True

    def fail(
        self,
        *,
        job_id: int,
        lease_token: str,
        error: str,
    ) -> bool:
        with self.mysql.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT attempts, max_attempts, catalog_id, source_name, "
                    "source_id, payload "
                    "FROM download_jobs "
                    "WHERE id=%s AND status='downloading' "
                    "AND lease_token=%s FOR UPDATE",
                    (int(job_id), lease_token),
                )
                row = cursor.fetchone()
                if not row:
                    return False
                attempts = int(row["attempts"]) + 1
                terminal = attempts >= int(row["max_attempts"])
                payload = row.get("payload")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        payload = {}
                serialized_refresh = bool(
                    isinstance(payload, dict)
                    and payload.get("update_mode") == "serialized_refresh"
                )
                delay = min(30 * (2 ** min(attempts - 1, 8)), 7200)
                cursor.execute(
                    """
                    UPDATE download_jobs
                    SET status=%s,
                        attempts=%s,
                        available_at=UTC_TIMESTAMP(6)
                            + INTERVAL %s SECOND,
                        lease_owner=NULL,
                        lease_token=NULL,
                        lease_expires_at=NULL,
                        last_error=%s
                    WHERE id=%s
                    """,
                    (
                        "dead" if terminal else "pending",
                        attempts,
                        delay,
                        error[:2000],
                        int(job_id),
                    ),
                )
                if serialized_refresh:
                    cursor.execute(
                        """
                        UPDATE authorized_source_updates
                        SET state=%s, last_error=%s,
                            last_seen_at=UTC_TIMESTAMP(6)
                        WHERE source_name=%s AND source_id=%s
                        """,
                        (
                            "failed" if terminal else "queued",
                            error[:2000],
                            str(row["source_name"]),
                            str(row["source_id"]),
                        ),
                    )
                cursor.execute(
                    """
                    UPDATE books
                    SET status=%s,
                        attempts=%s,
                        last_error=%s,
                        updated_at=UTC_TIMESTAMP(6),
                        row_version=row_version+1
                    WHERE id=%s
                    """,
                    (
                        "done" if serialized_refresh else "failed",
                        attempts,
                        error[:2000],
                        int(row["catalog_id"]),
                    ),
                )
                cursor.execute(
                    """
                    SELECT library_id, status, category, body_available
                    FROM books
                    WHERE id=%s
                    """,
                    (int(row["catalog_id"]),),
                )
                current = cursor.fetchone()
                self._refresh_book_aggregates(
                    cursor,
                    status_keys={
                        (
                            str(current["library_id"]),
                            "discovered",
                        ),
                        (
                            str(current["library_id"]),
                            "failed",
                        ),
                    },
                    facet_keys={
                        (
                            str(current["library_id"]),
                            int(current["body_available"]),
                            str(current["category"] or "未分类"),
                        )
                    },
                )
        self._invalidate_catalog_cache()
        return True

    def defer(
        self,
        *,
        job_id: int,
        lease_token: str,
        reason: str,
        delay_seconds: int = 10,
    ) -> bool:
        with self.mysql.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE download_jobs
                    SET status='pending',
                        available_at=UTC_TIMESTAMP(6)
                            + INTERVAL %s SECOND,
                        lease_owner=NULL,
                        lease_token=NULL,
                        lease_expires_at=NULL,
                        last_error=%s
                    WHERE id=%s
                      AND status='downloading'
                      AND lease_token=%s
                    """,
                    (
                        min(max(int(delay_seconds), 1), 300),
                        reason[:2000],
                        int(job_id),
                        lease_token,
                    ),
                )
                return cursor.rowcount == 1

    _ACQUIRE_SEMAPHORE_SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local expires = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local token = ARGV[4]
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
    if redis.call('ZCARD', key) >= limit then
      return 0
    end
    redis.call('ZADD', key, expires, token)
    redis.call('PEXPIRE', key, math.max(expires - now, 1000))
    return 1
    """

    def acquire_source_slot(
        self,
        *,
        source_name: str,
        limit: int,
        ttl_seconds: int = 1800,
    ) -> str | None:
        token = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        expires_ms = now_ms + max(int(ttl_seconds), 60) * 1000
        acquired = self.redis.client.eval(
            self._ACQUIRE_SEMAPHORE_SCRIPT,
            1,
            self.redis.key(f"semaphore:{source_name}"),
            now_ms,
            expires_ms,
            max(int(limit), 1),
            token,
        )
        return token if int(acquired) == 1 else None

    def release_source_slot(self, source_name: str, token: str) -> None:
        self.redis.client.zrem(
            self.redis.key(f"semaphore:{source_name}"),
            token,
        )
