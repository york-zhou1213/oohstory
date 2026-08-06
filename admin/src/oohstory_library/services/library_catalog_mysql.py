"""MySQL-backed catalog queries for million-scale library browsing."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, Iterable

from oohstory_library.services.library_database import (
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
)
from oohstory_library.services.library_cache import RedisHotCache
from oohstory_library.services.library_catalog import normalize_catalog_title
from oohstory_library.services.library_identity_claims import (
    bind_global_book_identity,
    book_identity_hash,
    book_identity_key,
    claim_global_book_identity,
    normalize_book_identity,
)


class MySQLCatalogStore:
    def __init__(
        self,
        settings: LibraryInfrastructureSettings,
        pool: MySQLConnectionPool | None = None,
        cache_client: RedisHotCache | None = None,
    ):
        self.settings = settings
        self.pool = pool or MySQLConnectionPool(settings)
        self.cache = cache_client

    @staticmethod
    def _cache_scope(payload: dict[str, Any]) -> str:
        kind = payload.get("kind")
        if kind == "tone-tag-stats":
            return "tone"
        if kind == "assets":
            return "tone" if payload.get("asset") == "tone" else "plot"
        if kind == "recommendations":
            return "tone"
        return "catalog"

    def _invalidate_cache(self, *scopes: str) -> None:
        if self.cache is None:
            return
        expanded: list[str] = []
        for scope in scopes or ("catalog",):
            if scope == "derived":
                expanded.extend(("tone", "plot"))
            elif scope == "catalog":
                expanded.extend(("catalog", "book", "cover"))
            else:
                expanded.append(scope)
        self.cache.invalidate(*expanded)

    def operations_publication_books(
        self,
        *,
        query: str = "",
        publication: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return a bounded operator view including unpublished books."""

        normalized_query = " ".join(str(query or "").split())[:100]
        normalized_publication = str(publication or "").strip()
        if normalized_publication not in {"", "published", "unpublished"}:
            raise ValueError("书籍上架状态筛选无效")
        conditions = ["b.status <> 'duplicate'", "b.body_available=1"]
        params: list[Any] = []
        if normalized_query:
            escaped = (
                normalized_query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            if normalized_query.isdecimal():
                conditions.append(
                    "(b.id=%s OR b.title LIKE %s ESCAPE '\\\\' "
                    "OR b.author LIKE %s ESCAPE '\\\\')"
                )
                params.extend(
                    (int(normalized_query), f"%{escaped}%", f"%{escaped}%")
                )
            else:
                conditions.append(
                    "(b.title LIKE %s ESCAPE '\\\\' "
                    "OR b.author LIKE %s ESCAPE '\\\\')"
                )
                params.extend((f"%{escaped}%", f"%{escaped}%"))
        if normalized_publication:
            conditions.append("b.is_published=%s")
            params.append(1 if normalized_publication == "published" else 0)
        bounded_limit = max(1, min(int(limit), 100))
        where = " AND ".join(conditions)
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        SUM(b.body_available=1 AND b.status<>'duplicate') AS books,
                        SUM(b.body_available=1 AND b.status<>'duplicate'
                            AND b.is_published=1) AS published_books,
                        SUM(b.body_available=1 AND b.status<>'duplicate'
                            AND b.is_published=0) AS unpublished_books
                    FROM books b
                    """
                )
                counts = dict(cursor.fetchone() or {})
                cursor.execute(
                    f"""
                    SELECT b.id AS catalog_id,b.title,b.author,b.category,
                           b.book_status,b.status,b.body_available,b.is_published,
                           b.mysql_updated_at AS updated_at,
                           (
                             SELECT e.reason
                             FROM book_publication_events e
                             WHERE e.catalog_id=b.id
                             ORDER BY e.id DESC LIMIT 1
                           ) AS publication_reason
                    FROM books b
                    WHERE {where}
                    ORDER BY b.is_published ASC,b.mysql_updated_at DESC,b.id DESC
                    LIMIT %s
                    """,
                    [*params, bounded_limit],
                )
                books = [dict(row) for row in cursor.fetchall()]
                for book in books:
                    updated_at = book.get("updated_at")
                    if updated_at is not None and hasattr(updated_at, "isoformat"):
                        book["updated_at"] = updated_at.isoformat()
        return {
            "summary": {
                "books": int(counts.get("books") or 0),
                "published_books": int(counts.get("published_books") or 0),
                "unpublished_books": int(counts.get("unpublished_books") or 0),
            },
            "books": books,
        }

    def set_book_publication(
        self,
        catalog_id: int,
        *,
        published: bool,
        reason: str,
    ) -> dict[str, Any]:
        """Reversibly publish or unpublish one readable catalog book."""

        normalized_id = int(catalog_id)
        normalized_reason = " ".join(str(reason or "").split())
        if normalized_id <= 0:
            raise ValueError("书籍目录标识无效")
        if not normalized_reason or len(normalized_reason) > 240:
            raise ValueError("请填写 1 至 240 字的下架或上架原因")
        target = int(bool(published))
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id,title,author,category,status,body_available,
                           is_published
                    FROM books WHERE id=%s AND status<>'duplicate'
                    LIMIT 1 FOR UPDATE
                    """,
                    (normalized_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise KeyError("书籍不存在")
                item = dict(row)
                if not bool(item.get("body_available")):
                    raise ValueError("书籍正文尚不可用，不能执行上架状态变更")
                previous = int(bool(item.get("is_published")))
                if previous == target:
                    return item | {
                        "catalog_id": normalized_id,
                        "published": bool(target),
                        "changed": False,
                        "reason": normalized_reason,
                    }
                cursor.execute(
                    """
                    UPDATE books
                    SET is_published=%s,row_version=row_version+1
                    WHERE id=%s
                    """,
                    (target, normalized_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("书籍上架状态更新失败")
                cursor.execute(
                    """
                    INSERT INTO book_publication_events
                        (catalog_id,previous_published,target_published,reason)
                    VALUES (%s,%s,%s,%s)
                    """,
                    (normalized_id, previous, target, normalized_reason),
                )
        self._invalidate_cache("catalog", "derived")
        return item | {
            "catalog_id": normalized_id,
            "published": bool(target),
            "changed": True,
            "reason": normalized_reason,
        }

    def _cache_get(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self.cache is None:
            return None
        return self.cache.get_json(
            self._cache_scope(payload),
            str(payload.get("kind") or "browse"),
            payload,
        )

    def _cache_set(
        self,
        cache_payload: dict[str, Any],
        value: dict[str, Any],
    ) -> None:
        if self.cache is None:
            return
        scope = self._cache_scope(cache_payload)
        ttl = 3600 if scope == "tone" else (300 if scope == "plot" else 60)
        self.cache.set_json(
            scope,
            str(cache_payload.get("kind") or "browse"),
            cache_payload,
            value,
            ttl_seconds=ttl,
        )

    @staticmethod
    def _identity(value: Any) -> str:
        return normalize_book_identity(value)

    @staticmethod
    def _identity_key(title: Any, author: Any) -> str:
        return book_identity_key(title, author)

    @staticmethod
    def _identity_hash(identity_key: str) -> bytes:
        return book_identity_hash(identity_key)

    @staticmethod
    def _canonical_body_object_key(value: Any) -> str:
        raw = str(value or "").strip()
        key = PurePosixPath(raw)
        if (
            not raw
            or key.is_absolute()
            or len(key.parts) < 3
            or key.parts[0] != "书籍"
            or any(part in {"", ".", ".."} for part in key.parts)
            or key.as_posix() != raw
        ):
            raise ValueError("正文对象键必须指向 txt80/书籍/ 下的分类文件")
        return raw

    @staticmethod
    def _refresh_facets(
        cursor: Any,
        keys: set[tuple[str, int, str]],
    ) -> None:
        # Migration 007 installs row triggers that maintain the materialized
        # counters incrementally. Retain this compatibility hook without ever
        # rescanning a potentially hundred-million-row catalog.
        del cursor, keys

    @staticmethod
    def _refresh_status_counts(
        cursor: Any,
        keys: set[tuple[str, str]],
    ) -> None:
        # Maintained by trg_books_catalog_counts_*.
        del cursor, keys

    def register_authorized_items(
        self,
        items: list[dict[str, Any]],
        *,
        stamp: Any,
        invalid: int = 0,
    ) -> dict[str, Any]:
        items = [
            {**item, "title": normalize_catalog_title(item.get("title"))}
            for item in items
        ]
        if not items:
            return {
                "seen": 0,
                "added": 0,
                "updated": 0,
                "known": 0,
                "duplicates": 0,
                "invalid": invalid,
            }
        source_ids = [str(item["source_id"]) for item in items]
        detail_urls = [str(item["detail_url"]) for item in items]
        source_placeholders = ", ".join(["%s"] * len(source_ids))
        detail_placeholders = ", ".join(["%s"] * len(detail_urls))
        added = 0
        updated = 0
        known = 0
        duplicates = 0
        mutated_duplicate = False
        facet_keys: set[tuple[str, int, str]] = set()
        status_keys: set[tuple[str, str]] = set()
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        id, source_id, detail_url, title, author, category,
                        status, library_id, body_available
                    FROM books
                    WHERE source_id IN ({source_placeholders})
                       OR detail_url IN ({detail_placeholders})
                    FOR UPDATE
                    """,
                    [*source_ids, *detail_urls],
                )
                existing_rows = [dict(row) for row in cursor.fetchall()]
                by_source = {
                    str(row["source_id"]): row
                    for row in existing_rows
                    if row.get("source_id")
                }
                by_detail = {
                    str(row["detail_url"]): row
                    for row in existing_rows
                    if row.get("detail_url")
                }
                for item in items:
                    existing = (
                        by_source.get(str(item["source_id"]))
                        or by_detail.get(str(item["detail_url"]))
                    )
                    if existing:
                        if str(existing["status"]) in {"done", "duplicate"}:
                            known += 1
                            continue
                        changed = any(
                            str(existing.get(field) or "")
                            != str(item.get(field) or "")
                            for field in (
                                "title",
                                "author",
                                "category",
                                "detail_url",
                            )
                        )
                        if changed:
                            facet_keys.add(
                                (
                                    str(existing["library_id"]),
                                    int(existing["body_available"]),
                                    str(existing["category"] or "未分类"),
                                )
                            )
                            status_keys.add(
                                (
                                    str(existing["library_id"]),
                                    str(existing["status"]),
                                )
                            )
                            previous_identity_key = self._identity_key(
                                existing["title"], existing["author"]
                            )
                            identity_key = self._identity_key(
                                item["title"], item["author"]
                            )
                            if identity_key != previous_identity_key:
                                claimed, canonical_id = claim_global_book_identity(
                                    cursor,
                                    identity_key=identity_key,
                                    catalog_id=int(existing["id"]),
                                )
                                if not claimed and canonical_id is None:
                                    raise RuntimeError(
                                        "全库书目身份占位异常，已拒绝目录更新"
                                    )
                                if (
                                    not claimed
                                    and int(canonical_id or 0)
                                    != int(existing["id"])
                                ):
                                    cursor.execute(
                                        """
                                        UPDATE books
                                        SET title=%s,author=%s,category=%s,
                                            detail_url=%s,
                                            download_page_url=%s,
                                            updated_at=%s,book_status=%s,
                                            identity_key=%s,title_key=%s,
                                            library_id=%s,status='duplicate',
                                            last_error=%s,
                                            row_version=row_version+1
                                        WHERE id=%s
                                        """,
                                        (
                                            item["title"],
                                            item["author"],
                                            item["category"],
                                            item["detail_url"],
                                            item["detail_url"],
                                            stamp,
                                            item["book_status"],
                                            identity_key,
                                            self._identity(item["title"]),
                                            item.get("library_id") or "fanqie",
                                            (
                                                "全书库身份重复，复用 canonical "
                                                f"catalog {int(canonical_id)}"
                                            ),
                                            int(existing["id"]),
                                        ),
                                    )
                                    cursor.execute(
                                        """
                                        UPDATE download_jobs
                                        SET status='done',completed_at=%s,
                                            lease_owner=NULL,lease_token=NULL,
                                            lease_expires_at=NULL,
                                            last_error=%s
                                        WHERE catalog_id=%s
                                        """,
                                        (
                                            stamp,
                                            (
                                                "全书库身份重复，复用 canonical "
                                                f"catalog {int(canonical_id)}"
                                            ),
                                            int(existing["id"]),
                                        ),
                                    )
                                    cursor.execute(
                                        """
                                        DELETE FROM global_book_identity_claims
                                        WHERE identity_hash=%s AND catalog_id=%s
                                        """,
                                        (
                                            self._identity_hash(
                                                previous_identity_key
                                            ),
                                            int(existing["id"]),
                                        ),
                                    )
                                    duplicates += 1
                                    status_keys.add(
                                        (
                                            str(
                                                item.get("library_id")
                                                or "fanqie"
                                            ),
                                            "duplicate",
                                        )
                                    )
                                    facet_keys.add(
                                        (
                                            str(
                                                item.get("library_id")
                                                or "fanqie"
                                            ),
                                            int(existing["body_available"]),
                                            str(item["category"] or "未分类"),
                                        )
                                    )
                                    mutated_duplicate = True
                                    continue
                                cursor.execute(
                                    """
                                    DELETE FROM global_book_identity_claims
                                    WHERE identity_hash=%s AND catalog_id=%s
                                    """,
                                    (
                                        self._identity_hash(
                                            previous_identity_key
                                        ),
                                        int(existing["id"]),
                                    ),
                                )
                            cursor.execute(
                                """
                                UPDATE books
                                SET title=%s,
                                    author=%s,
                                    category=%s,
                                    detail_url=%s,
                                    download_page_url=%s,
                                    updated_at=%s,
                                    book_status=%s,
                                    identity_key=%s,
                                    title_key=%s,
                                    library_id=%s,
                                    row_version=row_version+1
                                WHERE id=%s
                                """,
                                (
                                    item["title"],
                                    item["author"],
                                    item["category"],
                                    item["detail_url"],
                                    item["detail_url"],
                                    stamp,
                                    item["book_status"],
                                    identity_key,
                                    self._identity(item["title"]),
                                    item.get("library_id") or "fanqie",
                                    int(existing["id"]),
                                ),
                            )
                            updated += 1
                        else:
                            known += 1
                        catalog_id = int(existing["id"])
                    else:
                        identity_key = self._identity_key(
                            item["title"], item["author"]
                        )
                        claimed, _canonical_id = claim_global_book_identity(
                            cursor,
                            identity_key=identity_key,
                        )
                        if not claimed:
                            duplicates += 1
                            continue
                        explicit_id = int(item.get("catalog_id") or 0)
                        insert_columns = (
                            "id, " if explicit_id > 0 else ""
                        )
                        insert_id_placeholder = (
                            "%s, " if explicit_id > 0 else ""
                        )
                        cursor.execute(
                            f"""
                            INSERT INTO books (
                                {insert_columns}
                                source_id, detail_url, title, author, category,
                                download_page_url, status, attempts,
                                discovered_at, updated_at, book_status,
                                identity_key, title_key, library_id,
                                expected_size
                            ) VALUES (
                                {insert_id_placeholder}
                                %s, %s, %s, %s, %s,
                                %s, 'discovered', 0,
                                %s, %s, %s,
                                %s, %s, %s, %s
                            )
                            """,
                            (
                                *((explicit_id,) if explicit_id > 0 else ()),
                                item["source_id"],
                                item["detail_url"],
                                item["title"],
                                item["author"],
                                item["category"],
                                item["detail_url"],
                                stamp,
                                stamp,
                                item["book_status"],
                                identity_key,
                                self._identity(item["title"]),
                                item.get("library_id") or "fanqie",
                                item.get("expected_size") or None,
                            ),
                        )
                        catalog_id = explicit_id or int(cursor.lastrowid)
                        bind_global_book_identity(
                            cursor,
                            identity_key=identity_key,
                            catalog_id=catalog_id,
                        )
                        added += 1
                        status_keys.add(
                            (
                                str(item.get("library_id") or "fanqie"),
                                "discovered",
                            )
                        )
                    source_name = (
                        "xbiquge"
                        if str(item["source_id"]).startswith("xbiquge-")
                        else "ixdzs"
                        if str(item["source_id"]).startswith("ixdzs-")
                        else "shubaow"
                        if str(item["source_id"]).startswith("shubaow-")
                        else "linovelib"
                        if str(item["source_id"]).startswith("linovelib-")
                        else "txt80"
                    )
                    cursor.execute(
                        """
                        INSERT INTO download_jobs (
                            catalog_id, source_id, source_name, status,
                            priority, attempts, max_attempts,
                            available_at, payload
                        ) VALUES (
                            %s, %s, %s, 'pending',
                            50, 0, 8,
                            UTC_TIMESTAMP(6), %s
                        )
                        ON DUPLICATE KEY UPDATE
                            source_id=VALUES(source_id),
                            source_name=VALUES(source_name),
                            payload=VALUES(payload),
                            updated_at=UTC_TIMESTAMP(6)
                        """,
                        (
                            catalog_id,
                            item["source_id"],
                            source_name,
                            json.dumps(item, ensure_ascii=False),
                        ),
                    )
                    facet_keys.add(
                        (
                            str(item.get("library_id") or "fanqie"),
                            0,
                            str(item["category"] or "未分类"),
                        )
                    )
                self._refresh_facets(cursor, facet_keys)
                self._refresh_status_counts(cursor, status_keys)
        if added or updated or mutated_duplicate:
            self._invalidate_cache()
        return {
            "seen": len(items),
            "added": added,
            "updated": updated,
            "known": known,
            "duplicates": duplicates,
            "invalid": invalid,
        }

    def mirror_imported_book(
        self,
        *,
        catalog_id: int | None,
        source_id: str,
        detail_url: str,
        title: str,
        author: str,
        category: str,
        file_url: str,
        legacy_output_path: str,
        body_object_key: str,
        bytes_count: int,
        sha256: str,
        book_status: str,
        library_id: str,
        stamp: Any,
        rebind_source: bool = False,
    ) -> int:
        body_object_key = self._canonical_body_object_key(body_object_key)
        title = normalize_catalog_title(title)
        identity_key = self._identity_key(title, author)
        source_name = (
            "xbiquge"
            if source_id.startswith("xbiquge-")
            else "ixdzs"
            if source_id.startswith("ixdzs-")
            else "shubaow"
            if source_id.startswith("shubaow-")
            else "linovelib"
            if source_id.startswith("linovelib-")
            else "other"
        )
        facet_keys: set[tuple[str, int, str]] = set()
        status_keys: set[tuple[str, str]] = set()
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        id, library_id, body_available, category, status
                    FROM books
                    WHERE id=%s OR source_id=%s OR detail_url_hash=UNHEX(
                        SHA2(%s, 256)
                    )
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (int(catalog_id or 0), source_id, detail_url),
                )
                previous = cursor.fetchone()
                if previous:
                    facet_keys.add(
                        (
                            str(previous["library_id"]),
                            int(previous["body_available"]),
                            str(previous["category"] or "未分类"),
                        )
                    )
                    status_keys.add(
                        (
                            str(previous["library_id"]),
                            str(previous["status"]),
                        )
                    )
                target_id = (
                    int(previous["id"])
                    if previous
                    else int(catalog_id or 0)
                )
                claimed, canonical_id = claim_global_book_identity(
                    cursor,
                    identity_key=identity_key,
                    catalog_id=target_id or None,
                )
                if (
                    not claimed
                    and canonical_id
                    and int(canonical_id) != target_id
                ):
                    return int(canonical_id)
                if not claimed and canonical_id is None:
                    raise RuntimeError("全库书目身份占位异常，已拒绝写入重复书目")
                insert_id_column = "id, " if target_id > 0 else ""
                insert_id_value = "%s, " if target_id > 0 else ""
                cursor.execute(
                    f"""
                    INSERT INTO books (
                        {insert_id_column}
                        source_id, detail_url, title, author, category,
                        download_page_url, file_url, legacy_output_path,
                        body_object_key, status, attempts, bytes, sha256,
                        last_error, discovered_at, updated_at, book_status,
                        identity_key, title_key, library_id
                    ) VALUES (
                        {insert_id_value}
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, 'done', 1, %s, %s,
                        NULL, %s, %s, %s,
                        %s, %s, %s
                    )
                    ON DUPLICATE KEY UPDATE
                        source_id=IF(%s, VALUES(source_id), source_id),
                        title=VALUES(title),
                        author=VALUES(author),
                        category=VALUES(category),
                        detail_url=IF(
                            %s OR source_id=VALUES(source_id),
                            VALUES(detail_url), detail_url
                        ),
                        file_url=IF(
                            %s OR source_id=VALUES(source_id),
                            VALUES(file_url), file_url
                        ),
                        legacy_output_path=VALUES(legacy_output_path),
                        body_object_key=VALUES(body_object_key),
                        status='done',
                        attempts=GREATEST(attempts, 1),
                        bytes=VALUES(bytes),
                        sha256=VALUES(sha256),
                        last_error=NULL,
                        updated_at=VALUES(updated_at),
                        book_status=VALUES(book_status),
                        identity_key=VALUES(identity_key),
                        title_key=VALUES(title_key),
                        library_id=VALUES(library_id),
                        row_version=row_version+1
                    """,
                    (
                        *((target_id,) if target_id > 0 else ()),
                        source_id,
                        detail_url,
                        title,
                        author,
                        category,
                        detail_url,
                        file_url or None,
                        legacy_output_path or None,
                        body_object_key or None,
                        max(int(bytes_count), 0),
                        sha256 or None,
                        stamp,
                        stamp,
                        book_status,
                        identity_key,
                        self._identity(title),
                        library_id,
                        int(bool(rebind_source)),
                        int(bool(rebind_source)),
                        int(bool(rebind_source)),
                    ),
                )
                if target_id <= 0:
                    target_id = int(cursor.lastrowid)
                    bind_global_book_identity(
                        cursor,
                        identity_key=identity_key,
                        catalog_id=target_id,
                    )
                if body_object_key:
                    cursor.execute(
                        """
                        INSERT INTO object_assets (
                            catalog_id, asset_type, object_key,
                            storage_backend, content_type, bytes, sha256, state
                        ) VALUES (
                            %s, 'body', %s,
                            'nas', 'text/plain; charset=utf-8',
                            %s, %s, 'available'
                        )
                        ON DUPLICATE KEY UPDATE
                            object_key=VALUES(object_key),
                            bytes=VALUES(bytes),
                            sha256=VALUES(sha256),
                            state='available'
                        """,
                        (
                            target_id,
                            body_object_key,
                            max(int(bytes_count), 0),
                            sha256 or None,
                        ),
                    )
                cursor.execute(
                    """
                    UPDATE download_jobs
                    SET status='done',
                        completed_at=UTC_TIMESTAMP(6),
                        lease_owner=NULL,
                        lease_token=NULL,
                        lease_expires_at=NULL,
                        last_error=NULL
                    WHERE catalog_id=%s
                    """,
                    (target_id,),
                )
                facet_keys.add((library_id, 1, category or "未分类"))
                self._refresh_facets(cursor, facet_keys)
                status_keys.add((library_id, "done"))
                self._refresh_status_counts(cursor, status_keys)
        self._invalidate_cache()
        return target_id

    @staticmethod
    def _book_projection_sql() -> str:
        return """
            SELECT
                id AS catalog_id,
                COALESCE(source_id, CAST(id AS CHAR)) AS source_id,
                detail_url,
                title,
                author,
                category,
                expected_size,
                legacy_output_path AS source_path,
                body_object_key,
                cover_object_key,
                bytes AS source_bytes,
                status AS download_status,
                sha256,
                updated_at,
                book_status,
                row_version
            FROM books
        """

    def list_book_projection(
        self,
        *,
        include_unavailable: bool = False,
        catalog_ids: Iterable[int] | None = None,
    ) -> list[dict[str, Any]]:
        conditions = ["is_active=1"]
        params: list[Any] = []
        if not include_unavailable:
            conditions.append("body_available=1")
        ids = sorted(
            {
                int(value)
                for value in (catalog_ids or ())
                if int(value) > 0
            }
        )
        if ids:
            if len(ids) > 1000:
                rows: list[dict[str, Any]] = []
                for start in range(0, len(ids), 1000):
                    rows.extend(
                        self.list_book_projection(
                            include_unavailable=include_unavailable,
                            catalog_ids=ids[start : start + 1000],
                        )
                    )
                rows.sort(key=lambda row: int(row["catalog_id"]))
                return rows
            conditions.append(
                "id IN (" + ", ".join(["%s"] * len(ids)) + ")"
            )
            params.extend(ids)
        elif catalog_ids is not None:
            return []
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._book_projection_sql()
                    + " WHERE "
                    + " AND ".join(conditions)
                    + " ORDER BY id",
                    params,
                )
                return [dict(row) for row in cursor.fetchall()]

    def search_book_projection(
        self,
        query: str,
        *,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        search_sql, search_params, order_sql, order_params = (
            self._search_clause(query)
        )
        if not search_sql:
            return []
        limit_sql = ""
        params = [*search_params, *order_params]
        if limit > 0:
            limit_sql = " LIMIT %s"
            params.append(min(max(int(limit), 1), 5000))
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._book_projection_sql()
                    + " WHERE is_active=1"
                    + search_sql
                    + " ORDER BY "
                    + order_sql
                    + limit_sql,
                    params,
                )
                return [dict(row) for row in cursor.fetchall()]

    def get_done_book(self, catalog_id: int) -> dict[str, Any] | None:
        cache_query = {"catalog_id": int(catalog_id)}
        if self.cache is not None:
            cached = self.cache.get_json("book", "detail", cache_query)
            if cached is not None:
                return dict(cached.get("value")) if cached.get("found") else None
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._book_projection_sql()
                    + " WHERE id=%s AND body_available=1 LIMIT 1",
                    (int(catalog_id),),
                )
                row = cursor.fetchone()
                value = dict(row) if row else None
        if self.cache is not None:
            self.cache.set_json(
                "book",
                "detail",
                cache_query,
                {"found": value is not None, "value": value},
                ttl_seconds=300 if value is not None else 30,
            )
        return value

    def existing_done_source(self, source_id: str) -> dict[str, Any] | None:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._book_projection_sql()
                    + " WHERE source_id=%s AND body_available=1 LIMIT 1",
                    (source_id,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def existing_done_identity(
        self,
        title: str,
        author: str,
    ) -> dict[str, Any] | None:
        identity_hash = self._identity_hash(
            self._identity_key(title, author)
        )
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._book_projection_sql()
                    + """
                      WHERE identity_hash=%s
                        AND body_available=1
                      ORDER BY id
                      LIMIT 1
                    """,
                    (identity_hash,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def update_book_status(
        self,
        catalog_id: int,
        book_status: str,
        *,
        stamp: Any,
    ) -> bool:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE books
                    SET book_status=%s,
                        updated_at=%s,
                        row_version=row_version+1
                    WHERE id=%s
                    """,
                    (book_status, stamp, int(catalog_id)),
                )
                changed = cursor.rowcount == 1
        if changed:
            self._invalidate_cache()
            if self.cache is not None:
                self.cache.schedule_warm(
                    "book",
                    "detail",
                    {"catalog_id": int(catalog_id)},
                    lambda: {
                        "found": (value := self.get_done_book(catalog_id))
                        is not None,
                        "value": value,
                    },
                    ttl_seconds=300,
                )
        return changed

    def find_book_identities(
        self,
        *,
        source_ids: Iterable[str],
        titles: Iterable[str],
    ) -> list[dict[str, Any]]:
        normalized_sources = sorted(
            {str(value).strip() for value in source_ids if str(value).strip()}
        )
        normalized_titles = sorted(
            {str(value).strip() for value in titles if str(value).strip()}
        )
        conditions: list[str] = []
        params: list[Any] = []
        if normalized_sources:
            conditions.append(
                "source_id IN ("
                + ", ".join(["%s"] * len(normalized_sources))
                + ")"
            )
            params.extend(normalized_sources)
        if normalized_titles:
            conditions.append(
                "title IN ("
                + ", ".join(["%s"] * len(normalized_titles))
                + ")"
            )
            params.extend(normalized_titles)
        if not conditions:
            return []
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        id AS catalog_id,
                        COALESCE(source_id, CAST(id AS CHAR)) AS source_id,
                        title,
                        author,
                        category,
                        legacy_output_path AS source_path,
                        body_object_key,
                        bytes AS source_bytes
                    FROM books
                    WHERE is_active=1
                      AND (
                    """
                    + " OR ".join(conditions)
                    + ")",
                    params,
                )
                return [dict(row) for row in cursor.fetchall()]

    def browse_deconstruction_projection(
        self,
        *,
        query: str,
        category: str,
        page: int,
        page_size: int,
        include_ids: Iterable[int] | None = None,
        exclude_ids: Iterable[int] = (),
        priority_by_id: dict[int, int] | None = None,
    ) -> dict[str, Any]:
        conditions = ["is_active=1"]
        params: list[Any] = []
        normalized_include = (
            sorted({int(value) for value in include_ids if int(value) > 0})
            if include_ids is not None
            else None
        )
        normalized_exclude = sorted(
            {int(value) for value in exclude_ids if int(value) > 0}
        )
        if normalized_include is not None:
            if not normalized_include:
                return {
                    "rows": [],
                    "total": 0,
                    "categories": [],
                }
            conditions.append(
                "id IN ("
                + ", ".join(["%s"] * len(normalized_include))
                + ")"
            )
            params.extend(normalized_include)
        if normalized_exclude:
            conditions.append(
                "id NOT IN ("
                + ", ".join(["%s"] * len(normalized_exclude))
                + ")"
            )
            params.extend(normalized_exclude)
        search_sql, search_params, _, _ = self._search_clause(query)
        if search_sql:
            conditions.append(search_sql.removeprefix(" AND "))
            params.extend(search_params)
        base_where = " AND ".join(conditions)
        item_where = base_where
        item_params = list(params)
        if category:
            item_where += " AND category=%s"
            item_params.append(category)
        ordering = "id"
        order_params: list[Any] = []
        priorities = {
            int(catalog_id): int(rank)
            for catalog_id, rank in (priority_by_id or {}).items()
            if int(catalog_id) > 0
        }
        if normalized_include is not None:
            included = set(normalized_include)
            priorities = {
                catalog_id: rank
                for catalog_id, rank in priorities.items()
                if catalog_id in included
            }
        if normalized_exclude:
            excluded = set(normalized_exclude)
            priorities = {
                catalog_id: rank
                for catalog_id, rank in priorities.items()
                if catalog_id not in excluded
            }
        if priorities:
            cases = " ".join(
                "WHEN %s THEN %s"
                for _ in priorities
            )
            ordering = f"CASE id {cases} ELSE 4 END, id"
            for catalog_id, rank in sorted(priorities.items()):
                order_params.extend([catalog_id, rank])
        offset = (max(int(page), 1) - 1) * max(int(page_size), 1)
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                if not query and normalized_include is None:
                    cursor.execute(
                        """
                        SELECT category, SUM(book_count) AS count
                        FROM catalog_facets
                        GROUP BY category
                        """
                    )
                    category_counts = {
                        str(row["category"] or "未分类"): int(row["count"])
                        for row in cursor.fetchall()
                    }
                    if normalized_exclude:
                        placeholders = ", ".join(
                            ["%s"] * len(normalized_exclude)
                        )
                        cursor.execute(
                            f"""
                            SELECT category, COUNT(*) AS count
                            FROM books
                            WHERE is_active=1
                              AND id IN ({placeholders})
                            GROUP BY category
                            """,
                            normalized_exclude,
                        )
                        for row in cursor.fetchall():
                            name = str(row["category"] or "未分类")
                            category_counts[name] = max(
                                category_counts.get(name, 0)
                                - int(row["count"]),
                                0,
                            )
                    categories = [
                        {"name": name, "count": count}
                        for name, count in sorted(
                            category_counts.items(),
                            key=lambda item: (-item[1], item[0]),
                        )
                        if count
                    ]
                else:
                    cursor.execute(
                        """
                        SELECT category, COUNT(*) AS count
                        FROM books
                        WHERE
                        """
                        + base_where
                        + " GROUP BY category ORDER BY count DESC, category",
                        params,
                    )
                    categories = [
                        {
                            "name": str(row["category"] or "未分类"),
                            "count": int(row["count"]),
                        }
                        for row in cursor.fetchall()
                    ]
                if category:
                    total = next(
                        (
                            int(row["count"])
                            for row in categories
                            if row["name"] == category
                        ),
                        0,
                    )
                else:
                    total = sum(int(row["count"]) for row in categories)
                if priorities and normalized_include is None:
                    priority_ids = sorted(priorities)
                    priority_placeholders = ", ".join(
                        ["%s"] * len(priority_ids)
                    )
                    cursor.execute(
                        self._book_projection_sql()
                        + " WHERE "
                        + item_where
                        + f" AND id IN ({priority_placeholders})"
                        + " ORDER BY "
                        + ordering,
                        [
                            *item_params,
                            *priority_ids,
                            *order_params,
                        ],
                    )
                    prioritized_rows = [
                        dict(row) for row in cursor.fetchall()
                    ]
                    rows = prioritized_rows[
                        offset : offset + max(int(page_size), 1)
                    ]
                    remaining = max(int(page_size), 1) - len(rows)
                    if remaining:
                        regular_offset = max(
                            offset - len(prioritized_rows),
                            0,
                        )
                        cursor.execute(
                            self._book_projection_sql()
                            + " WHERE "
                            + item_where
                            + f" AND id NOT IN ({priority_placeholders})"
                            + " ORDER BY id LIMIT %s OFFSET %s",
                            [
                                *item_params,
                                *priority_ids,
                                remaining,
                                regular_offset,
                            ],
                        )
                        rows.extend(dict(row) for row in cursor.fetchall())
                else:
                    cursor.execute(
                        self._book_projection_sql()
                        + " WHERE "
                        + item_where
                        + " ORDER BY "
                        + ordering
                        + " LIMIT %s OFFSET %s",
                        [
                            *item_params,
                            *order_params,
                            max(int(page_size), 1),
                            offset,
                        ],
                    )
                    rows = [dict(row) for row in cursor.fetchall()]
        return {
            "rows": rows,
            "total": total,
            "categories": categories,
        }

    def active_totals(self) -> dict[str, int]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        COALESCE(SUM(book_count), 0) AS total,
                        COALESCE(SUM(
                            CASE WHEN body_available=1
                                 THEN book_count ELSE 0 END
                        ), 0) AS readable
                    FROM catalog_facets
                    """
                )
                row = cursor.fetchone()
                return {
                    "all": int(row["total"]),
                    "readable": int(row["readable"]),
                }

    @staticmethod
    def _availability_clause(availability: str) -> tuple[str, list[Any]]:
        if availability == "all":
            return "", []
        if availability == "readable":
            return " AND body_available=1", []
        if availability == "recovery":
            return " AND body_available=0", []
        raise ValueError("正文状态必须是 all、readable 或 recovery")

    @staticmethod
    def _search_clause(query: str) -> tuple[str, list[Any], str, list[Any]]:
        text = query.strip()
        if not text:
            return "", [], "id DESC", []
        # MySQL's ngram parser indexes Chinese two-character tokens. One
        # character cannot use FULLTEXT, so retain a bounded LIKE fallback.
        if len(text) >= 2:
            boolean_query = f'"{text.replace(chr(34), " ")}"'
            escaped = (
                text.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            return (
                " AND MATCH(title, author) "
                "AGAINST (%s IN BOOLEAN MODE)",
                [boolean_query],
                "CASE WHEN title=%s THEN 0 WHEN author=%s THEN 1 "
                "WHEN title LIKE %s ESCAPE '\\\\' THEN 2 ELSE 3 END, id DESC",
                [text, text, f"{escaped}%"],
            )
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        return (
            " AND (title LIKE %s ESCAPE '\\\\' OR author LIKE %s ESCAPE '\\\\')",
            [pattern, pattern],
            "CASE WHEN title=%s THEN 0 WHEN author=%s THEN 1 "
            "WHEN title LIKE %s ESCAPE '\\\\' THEN 2 ELSE 3 END, id DESC",
            [text, text, f"{escaped}%"],
        )

    def browse_catalog(
        self,
        *,
        library: str,
        query: str,
        category: str,
        availability: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        if library not in {"all", "local", "fanqie"}:
            raise ValueError("书库来源必须是 all、local 或 fanqie")
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 60)
        category = category.strip()
        query = query.strip()
        cache_payload = {
            "database": self.settings.mysql_database,
            "library": library,
            "query": query,
            "category": category,
            "availability": availability,
            "page": page,
            "page_size": page_size,
        }
        cached = self._cache_get(cache_payload)
        if cached is not None:
            cached["cache"] = "redis"
            return cached
        base_sql = "is_active=1"
        base_params: list[Any] = []
        if library != "all":
            base_sql += " AND library_id=%s"
            base_params.append(library)
        availability_sql, availability_params = self._availability_clause(
            availability
        )
        search_sql, search_params, order_sql, order_params = self._search_clause(
            query
        )
        item_sql = base_sql + availability_sql + search_sql
        item_params = [
            *base_params,
            *availability_params,
            *search_params,
        ]
        if category:
            item_sql += " AND category=%s"
            item_params.append(category)
        offset = (page - 1) * page_size

        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                facet_conditions: list[str] = []
                facet_params: list[Any] = []
                if library != "all":
                    facet_conditions.append("library_id=%s")
                    facet_params.append(library)
                facet_where = (
                    " WHERE " + " AND ".join(facet_conditions)
                    if facet_conditions
                    else ""
                )
                cursor.execute(
                    f"""
                    SELECT library_id, body_available, category, book_count
                    FROM catalog_facets
                    {facet_where}
                    """,
                    facet_params,
                )
                facet_rows = [dict(row) for row in cursor.fetchall()]
                status_counts = {
                    "readable": sum(
                        int(row["book_count"])
                        for row in facet_rows
                        if int(row["body_available"]) == 1
                    ),
                    "recovery": sum(
                        int(row["book_count"])
                        for row in facet_rows
                        if int(row["body_available"]) == 0
                    ),
                }
                status_counts["all"] = (
                    status_counts["readable"] + status_counts["recovery"]
                )
                selected_body = (
                    None
                    if availability == "all"
                    else (1 if availability == "readable" else 0)
                )
                if not query:
                    selected_facets = [
                        row
                        for row in facet_rows
                        if (
                            selected_body is None
                            or int(row["body_available"]) == selected_body
                        )
                    ]
                    category_counts: dict[str, int] = {}
                    for row in selected_facets:
                        category_name = str(row["category"] or "未分类")
                        category_counts[category_name] = (
                            category_counts.get(category_name, 0)
                            + int(row["book_count"])
                        )
                    categories = [
                        {"name": name, "count": count}
                        for name, count in sorted(
                            category_counts.items(),
                            key=lambda item: (-item[1], item[0]),
                        )
                    ]
                    total = (
                        int(category_counts.get(category, 0))
                        if category
                        else sum(category_counts.values())
                    )
                else:
                    cursor.execute(
                        f"SELECT COUNT(*) AS total "
                        f"FROM books WHERE {item_sql}",
                        item_params,
                    )
                    total = int(cursor.fetchone()["total"])
                    categories = [
                        {"name": name, "count": count}
                        for name, count in sorted(
                            {
                                str(row["category"] or "未分类"): sum(
                                    int(candidate["book_count"])
                                    for candidate in facet_rows
                                    if str(
                                        candidate["category"] or "未分类"
                                    )
                                    == str(row["category"] or "未分类")
                                    and (
                                        selected_body is None
                                        or int(candidate["body_available"])
                                        == selected_body
                                    )
                                )
                                for row in facet_rows
                                if (
                                    selected_body is None
                                    or int(row["body_available"])
                                    == selected_body
                                )
                            }.items(),
                            key=lambda item: (-item[1], item[0]),
                        )
                    ]
                cursor.execute(
                    f"""
                    SELECT
                        id AS catalog_id,
                        COALESCE(source_id, CAST(id AS CHAR)) AS source_id,
                        library_id,
                        title,
                        author,
                        category,
                        expected_size,
                        legacy_output_path AS source_path,
                        body_object_key,
                        cover_object_key,
                        bytes AS source_bytes,
                        approx_word_count,
                        approx_chapter_count,
                        status AS download_status,
                        last_error,
                        discovered_at,
                        updated_at,
                        detail_url,
                        file_url,
                        book_status
                    FROM books
                    WHERE {item_sql}
                    ORDER BY {order_sql}
                    LIMIT %s OFFSET %s
                    """,
                    [
                        *item_params,
                        *order_params,
                        page_size,
                        offset,
                    ],
                )
                rows = [dict(row) for row in cursor.fetchall()]
        result = {
            "rows": rows,
            "total": total,
            "status_counts": {
                "all": int(status_counts["all"]),
                "readable": int(status_counts["readable"]),
                "recovery": int(status_counts["recovery"]),
            },
            "categories": categories,
            "page": page,
            "page_size": page_size,
            "query": query,
            "category": category,
            "availability": availability,
            "cache": "mysql",
        }
        self._cache_set(cache_payload, result)
        return result

    @staticmethod
    def _recommendation_score(
        profile: dict[str, Any],
        query: str,
    ) -> tuple[str, list[Any]]:
        parts = ["10"]
        params: list[Any] = []
        genre = str(profile.get("genre") or "").strip()
        if genre:
            parts.append(
                """
                CASE
                  WHEN b.category=%s THEN 45
                  WHEN JSON_CONTAINS(
                    COALESCE(m.genre_tags, JSON_ARRAY()),
                    JSON_QUOTE(%s)
                  ) THEN 32
                  ELSE 0
                END
                """
            )
            params.extend([genre, genre])
        tones = sorted(
            {
                str(value).strip()
                for value in (
                    list(profile.get("tone_tags") or [])
                    + [profile.get("tone")]
                )
                if str(value or "").strip()
            }
        )[:6]
        for tone in tones:
            parts.append(
                """
                CASE WHEN JSON_CONTAINS(
                    COALESCE(m.tone_tags, JSON_ARRAY()),
                    JSON_QUOTE(%s)
                ) THEN 12 ELSE 0 END
                """
            )
            params.append(tone)
        keyword_terms = sorted(
            {
                str(value).strip()
                for value in profile.get("keywords", [])
                if 2 <= len(str(value).strip()) <= 24
            }
        )[:8]
        for keyword in keyword_terms:
            path = '$."' + keyword.replace("\\", "\\\\").replace('"', '\\"') + '"'
            parts.append(
                """
                CASE WHEN JSON_CONTAINS_PATH(
                    COALESCE(m.keyword_counts, JSON_OBJECT()),
                    'one',
                    %s
                ) THEN 4 ELSE 0 END
                """
            )
            params.append(path)
        if query:
            escaped = (
                query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            parts.append(
                """
                CASE
                  WHEN b.title=%s THEN 90
                  WHEN b.author=%s THEN 82
                  WHEN b.title LIKE %s ESCAPE '\\\\' THEN 75
                  ELSE 55
                END
                """
            )
            params.extend([query, query, f"{escaped}%"])
        return "LEAST(100, " + " + ".join(parts) + ")", params

    def browse_recommendations(
        self,
        *,
        profile: dict[str, Any],
        query: str,
        category: str,
        min_score: int,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Score and page recommendations inside MySQL.

        Only the visible page crosses the database boundary. The previous
        implementation fetched every metadata row and then paged in Python.
        """
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 100)
        min_score = min(max(int(min_score), 0), 100)
        query = query.strip()
        category = category.strip()
        cache_payload = {
            "kind": "recommendations",
            "schema_version": 1,
            "profile": {
                "genre": str(profile.get("genre") or ""),
                "substyle": str(profile.get("substyle") or ""),
                "tone": str(profile.get("tone") or ""),
                "tone_tags": list(profile.get("tone_tags") or []),
                "keywords": list(profile.get("keywords") or []),
            },
            "query": query,
            "category": category,
            "min_score": min_score,
            "page": page,
            "page_size": page_size,
        }
        cached = self._cache_get(cache_payload)
        if cached is not None:
            cached["cache"] = "redis"
            return cached
        score_sql, score_params = self._recommendation_score(profile, query)
        conditions = ["b.is_active=1", "b.body_available=1"]
        where_params: list[Any] = []
        if category:
            conditions.append("b.category=%s")
            where_params.append(category)
        if query:
            if len(query) >= 2:
                boolean_query = f'"{query.replace(chr(34), " ")}"'
                conditions.append(
                    """
                    (
                      MATCH(b.title,b.author)
                        AGAINST (%s IN BOOLEAN MODE)
                      OR MATCH(m.summary)
                        AGAINST (%s IN BOOLEAN MODE)
                    )
                    """
                )
                where_params.extend([boolean_query, boolean_query])
            else:
                escaped = (
                    query.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                pattern = f"%{escaped}%"
                conditions.append(
                    """
                    (
                      b.title LIKE %s ESCAPE '\\\\'
                      OR b.author LIKE %s ESCAPE '\\\\'
                      OR m.summary LIKE %s ESCAPE '\\\\'
                    )
                    """
                )
                where_params.extend([pattern, pattern, pattern])
        ranked_sql = f"""
            SELECT
                b.id AS catalog_id,
                COALESCE(b.source_id, CAST(b.id AS CHAR)) AS source_id,
                b.detail_url,
                b.title,
                b.author,
                b.category,
                b.expected_size,
                b.legacy_output_path AS source_path,
                b.body_object_key,
                b.cover_object_key,
                b.bytes AS source_bytes,
                b.status AS download_status,
                b.sha256,
                b.updated_at,
                b.book_status,
                b.approx_word_count,
                b.approx_chapter_count,
                m.summary,
                m.genre_tags,
                m.tone_tags,
                m.keyword_counts,
                m.section_count,
                m.reader_index_status,
                m.reader_schema_version,
                m.reader_indexed_at,
                m.indexed_at,
                {score_sql} AS match_score
            FROM books b
            JOIN book_metadata m ON m.catalog_id=b.id
            WHERE {" AND ".join(conditions)}
        """
        params = [*score_params, *where_params]
        offset = (page - 1) * page_size
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS total
                    FROM ({ranked_sql}) ranked
                    WHERE match_score >= %s
                    """,
                    [*params, min_score],
                )
                total = int(cursor.fetchone()["total"])
                cursor.execute(
                    f"""
                    SELECT *
                    FROM ({ranked_sql}) ranked
                    WHERE match_score >= %s
                    ORDER BY match_score DESC, source_bytes DESC, catalog_id DESC
                    LIMIT %s OFFSET %s
                    """,
                    [*params, min_score, page_size, offset],
                )
                rows = [dict(row) for row in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT category AS name, SUM(book_count) AS count
                    FROM public_catalog_facets
                    WHERE book_count>0
                    GROUP BY category
                    ORDER BY count DESC, name
                    """
                )
                categories = [
                    {
                        "name": str(row["name"] or "未分类"),
                        "count": int(row["count"]),
                    }
                    for row in cursor.fetchall()
                ]
        result = {
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "categories": categories,
            "cache": "mysql",
        }
        self._cache_set(cache_payload, result)
        return result

    def asset_counts(self) -> dict[str, int]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM book_metadata) AS tone_books,
                      (SELECT COUNT(*) FROM plot_index_meta) AS plot_books,
                      (SELECT COUNT(*) FROM plot_segments) AS plot_segments
                    """
                )
                row = cursor.fetchone()
                return {
                    "tone_books": int(row["tone_books"]),
                    "plot_books": int(row["plot_books"]),
                    "plot_segments": int(row["plot_segments"]),
                }

    def authorized_deduplicated_count(self) -> int:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COALESCE(
                        CAST(
                            JSON_UNQUOTE(
                                JSON_EXTRACT(state_value, '$.count')
                            ) AS UNSIGNED
                        ),
                        0
                    ) AS count
                    FROM crawl_state
                    WHERE source_name='authorized-catalog'
                      AND state_key='deduplicated-total'
                    """
                )
                row = cursor.fetchone()
        return int(row["count"]) if row else 0

    def set_authorized_deduplicated_count(self, total: int) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO crawl_state (
                      source_name, state_key, state_value
                    ) VALUES (
                      'authorized-catalog',
                      'deduplicated-total',
                      JSON_OBJECT('count', %s)
                    )
                    ON DUPLICATE KEY UPDATE
                      state_value=VALUES(state_value),
                      updated_at=UTC_TIMESTAMP(6)
                    """,
                    (max(0, int(total)),),
                )

    def metadata_state_get(self, key: str, default: Any = None) -> Any:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT state_value
                    FROM crawl_state
                    WHERE source_name='library-metadata' AND state_key=%s
                    """,
                    (str(key),),
                )
                row = cursor.fetchone()
        if not row:
            return default
        value = row["state_value"]
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default
        return value

    def metadata_state_set(self, key: str, value: Any) -> None:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO crawl_state (
                      source_name, state_key, state_value
                    ) VALUES ('library-metadata', %s, %s)
                    ON DUPLICATE KEY UPDATE
                      state_value=VALUES(state_value),
                      updated_at=UTC_TIMESTAMP(6)
                    """,
                    (str(key), encoded),
                )

    def metadata_fingerprints(self) -> dict[str, dict[str, Any]]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      b.id AS catalog_id,
                      COALESCE(b.source_id, CAST(b.id AS CHAR)) AS source_id,
                      m.reader_source_path AS source_path,
                      m.reader_source_bytes AS source_bytes,
                      m.source_mtime_ns
                    FROM book_metadata m
                    JOIN books b ON b.id=m.catalog_id
                    """
                )
                return {
                    str(row["source_id"]): dict(row)
                    for row in cursor.fetchall()
                }

    def derived_index_catalog_total(self) -> int:
        """Return the current number of readable books for progress baselines."""
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM books
                    WHERE is_active=1 AND body_available=1
                    """
                )
                row = cursor.fetchone()
        return int(row["count"] if row else 0)

    def list_tone_index_candidates(
        self,
        *,
        rule_version: str,
        force: bool = False,
        catalog_ids: Iterable[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Return only books whose durable tone checkpoint is stale."""
        condition = "1=1" if force else """
            (
              m.catalog_id IS NULL
              OR m.tone_rule_version <> %s
              OR (
                COALESCE(b.sha256, '') <> ''
                AND m.source_sha256 <> b.sha256
              )
              OR (
                COALESCE(b.sha256, '') = ''
                AND m.reader_source_bytes <> b.source_bytes
              )
            )
        """
        params: list[Any] = [] if force else [str(rule_version)[:64]]
        ids = sorted(
            {
                int(value)
                for value in (catalog_ids or ())
                if int(value) > 0
            }
        )
        if catalog_ids is not None and not ids:
            return []
        target_condition = ""
        if ids:
            target_condition = (
                " AND b.catalog_id IN ("
                + ", ".join(["%s"] * len(ids))
                + ")"
            )
            params.extend(ids)
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT b.* FROM ("
                    + self._book_projection_sql()
                    + " WHERE is_active=1 AND body_available=1) b"
                    + " LEFT JOIN book_metadata m ON m.catalog_id=b.catalog_id"
                    + " WHERE "
                    + condition
                    + target_condition
                    + " ORDER BY b.catalog_id",
                    params,
                )
                return [dict(row) for row in cursor.fetchall()]

    def list_plot_index_candidates(
        self,
        *,
        rule_version: str,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """Return only books whose durable plot checkpoint is stale."""
        condition = "1=1" if force else """
            (
              p.catalog_id IS NULL
              OR p.plot_rule_version <> %s
              OR (
                COALESCE(b.sha256, '') <> ''
                AND p.source_sha256 <> b.sha256
              )
              OR (
                COALESCE(b.sha256, '') = ''
                AND p.source_bytes <> b.source_bytes
              )
            )
        """
        params: list[Any] = [] if force else [str(rule_version)[:64]]
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT b.* FROM ("
                    + self._book_projection_sql()
                    + " WHERE is_active=1 AND body_available=1) b"
                    + " LEFT JOIN plot_index_meta p ON p.catalog_id=b.catalog_id"
                    + " WHERE "
                    + condition
                    + " ORDER BY b.catalog_id",
                    params,
                )
                return [dict(row) for row in cursor.fetchall()]

    def metadata_search_texts(self) -> list[dict[str, Any]]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      b.title,
                      CONCAT_WS(
                        ' ',
                        m.summary,
                        JSON_UNQUOTE(m.genre_tags),
                        JSON_UNQUOTE(m.tone_tags)
                      ) AS searchable_text
                    FROM book_metadata m
                    JOIN books b ON b.id=m.catalog_id
                    WHERE NULLIF(TRIM(COALESCE(m.summary, '')), '') IS NOT NULL
                    """
                )
                return [dict(row) for row in cursor.fetchall()]

    def upsert_metadata_batch(
        self,
        records: Iterable[dict[str, Any]],
    ) -> int:
        rows = [dict(record) for record in records]
        if not rows:
            return 0
        metadata_values = []
        book_values = []
        for row in rows:
            metadata_values.append(
                (
                    int(row["catalog_id"]),
                    max(int(row.get("source_mtime_ns") or 0), 0),
                    str(row.get("source_sha256") or "")[:64],
                    str(row.get("tone_rule_version") or "")[:64],
                    str(row.get("source_path") or "") or None,
                    max(int(row.get("source_bytes") or 0), 0),
                    max(int(row.get("word_count") or 0), 0),
                    max(int(row.get("chapter_count") or 0), 0),
                    str(row.get("summary") or ""),
                    json.dumps(
                        row.get("genre_tags") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        row.get("tone_tags") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        row.get("keyword_counts") or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        row.get("primary_tone_tags") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        row.get("secondary_tone_tags") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    float(row.get("tone_confidence") or 0),
                    str(row.get("tone_source") or "local")[:32],
                    json.dumps(
                        row.get("tone_evidence") or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    str(row.get("tone_review_status") or "pending")[:32],
                    str(row.get("tone_review_model") or "")[:255],
                    row.get("tone_reviewed_at"),
                    max(int(row.get("section_count") or 0), 0),
                    str(row.get("reader_index_status") or "")[:32],
                    max(int(row.get("reader_schema_version") or 0), 0),
                    row.get("reader_indexed_at"),
                    row.get("indexed_at"),
                )
            )
            book_values.append(
                (
                    max(int(row.get("word_count") or 0), 0),
                    max(int(row.get("chapter_count") or 0), 0),
                    int(row["catalog_id"]),
                )
            )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO book_metadata (
                      catalog_id, source_mtime_ns,
                      source_sha256, tone_rule_version,
                      reader_source_path, reader_source_bytes,
                      word_count, chapter_count, summary,
                      genre_tags, tone_tags, keyword_counts,
                      primary_tone_tags, secondary_tone_tags,
                      tone_confidence, tone_source, tone_evidence,
                      tone_review_status, tone_review_model,
                      tone_reviewed_at, section_count,
                      reader_index_status, reader_schema_version,
                      reader_indexed_at, indexed_at
                    ) VALUES (
                      %s, %s, %s, %s,
                      %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s,
                      %s, %s, %s,
                      %s, %s,
                      %s, %s,
                      %s, %s,
                      %s, %s
                    )
                    ON DUPLICATE KEY UPDATE
                      source_mtime_ns=VALUES(source_mtime_ns),
                      source_sha256=VALUES(source_sha256),
                      tone_rule_version=VALUES(tone_rule_version),
                      reader_source_path=VALUES(reader_source_path),
                      reader_source_bytes=VALUES(reader_source_bytes),
                      word_count=VALUES(word_count),
                      chapter_count=VALUES(chapter_count),
                      summary=VALUES(summary),
                      genre_tags=VALUES(genre_tags),
                      tone_tags=VALUES(tone_tags),
                      keyword_counts=VALUES(keyword_counts),
                      primary_tone_tags=VALUES(primary_tone_tags),
                      secondary_tone_tags=VALUES(secondary_tone_tags),
                      tone_confidence=VALUES(tone_confidence),
                      tone_source=VALUES(tone_source),
                      tone_evidence=VALUES(tone_evidence),
                      tone_review_status=VALUES(tone_review_status),
                      tone_review_model=VALUES(tone_review_model),
                      tone_reviewed_at=VALUES(tone_reviewed_at),
                      section_count=VALUES(section_count),
                      reader_index_status=VALUES(reader_index_status),
                      reader_schema_version=VALUES(reader_schema_version),
                      reader_indexed_at=VALUES(reader_indexed_at),
                      indexed_at=VALUES(indexed_at)
                    """,
                    metadata_values,
                )
                cursor.executemany(
                    """
                    UPDATE books
                    SET approx_word_count=%s,
                        approx_chapter_count=%s,
                        row_version=row_version+1
                    WHERE id=%s
                    """,
                    book_values,
                )
        self._invalidate_cache("catalog", "tone")
        if self.cache is not None:
            # Warm only a bounded changed set.  Large index batches still
            # invalidate atomically, while Redis repopulates the remaining
            # cold rows on demand instead of flooding the cache workers.
            for row in rows[:32]:
                catalog_id = int(row["catalog_id"])
                self.cache.schedule_warm(
                    "book",
                    "detail",
                    {"catalog_id": catalog_id},
                    lambda catalog_id=catalog_id: {
                        "found": (
                            value := self.get_done_book(catalog_id)
                        )
                        is not None,
                        "value": value,
                    },
                    ttl_seconds=300,
                )
        return len(rows)

    def remove_stale_metadata(self, catalog_ids: Iterable[int]) -> int:
        ids = sorted({int(value) for value in catalog_ids if int(value) > 0})
        if not ids:
            return 0
        removed = 0
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                for start in range(0, len(ids), 1000):
                    batch = ids[start : start + 1000]
                    placeholders = ", ".join(["%s"] * len(batch))
                    cursor.execute(
                        f"""
                        DELETE FROM book_metadata
                        WHERE catalog_id IN ({placeholders})
                        """,
                        batch,
                    )
                    removed += int(cursor.rowcount)
        if removed:
            self._invalidate_cache("catalog", "derived")
        return removed

    def remove_unavailable_metadata(self) -> int:
        """Remove tone rows whose catalog body is no longer readable."""
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE m
                    FROM book_metadata m
                    LEFT JOIN books b ON b.id=m.catalog_id
                    WHERE b.id IS NULL OR b.is_active<>1 OR b.body_available<>1
                    """
                )
                removed = int(cursor.rowcount)
        if removed:
            self._invalidate_cache("catalog", "derived")
        return removed

    def pending_tone_rows(self, *, limit: int = 0) -> list[dict[str, Any]]:
        limit_sql = ""
        params: list[Any] = []
        if int(limit) > 0:
            limit_sql = " LIMIT %s"
            params.append(int(limit))
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                      COALESCE(b.source_id, CAST(b.id AS CHAR)) AS source_id,
                      b.title, b.author, b.category,
                      m.summary, m.genre_tags,
                      m.primary_tone_tags, m.secondary_tone_tags,
                      m.tone_confidence, m.tone_evidence
                    FROM book_metadata m
                    JOIN books b ON b.id=m.catalog_id
                    WHERE m.tone_review_status='pending'
                    ORDER BY m.tone_confidence, b.id
                    {limit_sql}
                    """,
                    params,
                )
                return [dict(row) for row in cursor.fetchall()]

    def tone_evidence_for_sources(
        self,
        source_ids: Iterable[str],
    ) -> dict[str, Any]:
        values = sorted(
            {str(value).strip() for value in source_ids if str(value).strip()}
        )
        if not values:
            return {}
        result: dict[str, Any] = {}
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                for start in range(0, len(values), 1000):
                    batch = values[start : start + 1000]
                    placeholders = ", ".join(["%s"] * len(batch))
                    cursor.execute(
                        f"""
                        SELECT b.source_id, m.tone_evidence
                        FROM book_metadata m
                        JOIN books b ON b.id=m.catalog_id
                        WHERE b.source_id IN ({placeholders})
                        """,
                        batch,
                    )
                    for row in cursor.fetchall():
                        result[str(row["source_id"])] = row["tone_evidence"]
        return result

    def apply_tone_review_rows(
        self,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        values = []
        for row in rows:
            values.append(
                (
                    json.dumps(
                        row.get("tone_tags") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        row.get("primary_tone_tags") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        row.get("secondary_tone_tags") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    float(row.get("tone_confidence") or 0),
                    json.dumps(
                        row.get("tone_evidence") or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    str(row.get("tone_review_model") or "")[:255],
                    row.get("tone_reviewed_at"),
                    str(row.get("source_id") or ""),
                )
            )
        if not values:
            return 0
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    UPDATE book_metadata m
                    JOIN books b ON b.id=m.catalog_id
                    SET m.tone_tags=%s,
                        m.primary_tone_tags=%s,
                        m.secondary_tone_tags=%s,
                        m.tone_confidence=%s,
                        m.tone_source='model_review',
                        m.tone_evidence=%s,
                        m.tone_review_status='reviewed',
                        m.tone_review_model=%s,
                        m.tone_reviewed_at=%s
                    WHERE b.source_id=%s
                    """,
                    values,
                )
                applied = int(cursor.rowcount)
        if applied:
            self._invalidate_cache("tone", "book")
        return applied

    def sync_reader_metrics(
        self,
        catalog_id: int,
        reader_index: dict[str, Any],
    ) -> None:
        word_count = max(int(reader_index.get("word_count") or 0), 0)
        chapter_count = max(
            int(reader_index.get("chapter_count") or 0),
            0,
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO book_metadata (
                      catalog_id, source_mtime_ns,
                      reader_source_path, reader_source_bytes,
                      word_count, chapter_count, section_count,
                      reader_index_status, reader_schema_version,
                      reader_indexed_at, indexed_at
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s
                    )
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
                        max(
                            int(reader_index.get("source_mtime_ns") or 0),
                            0,
                        ),
                        str(reader_index.get("source_path") or "") or None,
                        max(
                            int(reader_index.get("source_bytes") or 0),
                            0,
                        ),
                        word_count,
                        chapter_count,
                        max(
                            int(reader_index.get("section_count") or 0),
                            0,
                        ),
                        str(reader_index.get("index_status") or "")[:32],
                        max(
                            int(reader_index.get("schema_version") or 0),
                            0,
                        ),
                        reader_index.get("indexed_at"),
                        reader_index.get("indexed_at"),
                    ),
                )
                cursor.execute(
                    """
                    UPDATE books
                    SET approx_word_count=%s,
                        approx_chapter_count=%s,
                        row_version=row_version+1
                    WHERE id=%s
                    """,
                    (word_count, chapter_count, int(catalog_id)),
                )
        self._invalidate_cache("catalog")

    def get_cover_asset(self, catalog_id: int) -> dict[str, Any] | None:
        cache_query = {"catalog_id": int(catalog_id)}
        if self.cache is not None:
            cached = self.cache.get_json("cover", "mapping", cache_query)
            if cached is not None:
                return dict(cached.get("value")) if cached.get("found") else None
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      b.id AS catalog_id,
                      b.cover_object_key,
                      o.object_key,
                      o.content_type,
                      o.bytes,
                      o.sha256,
                      o.state,
                      o.updated_at
                    FROM books b
                    LEFT JOIN object_assets o
                      ON o.catalog_id=b.id AND o.asset_type='cover'
                    WHERE b.id=%s
                    LIMIT 1
                    """,
                    (int(catalog_id),),
                )
                row = cursor.fetchone()
                value = dict(row) if row else None
        if self.cache is not None:
            self.cache.set_json(
                "cover",
                "mapping",
                cache_query,
                {"found": value is not None, "value": value},
                ttl_seconds=600 if value is not None else 45,
            )
        return value

    def upsert_cover_asset(
        self,
        *,
        catalog_id: int,
        object_key: str,
        bytes_count: int,
        sha256: str,
        content_type: str,
    ) -> None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE books
                    SET cover_object_key=%s,
                        row_version=row_version+1
                    WHERE id=%s
                    """,
                    (object_key, int(catalog_id)),
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"书目不存在：{catalog_id}")
                cursor.execute(
                    """
                    INSERT INTO object_assets (
                      catalog_id, asset_type, object_key,
                      storage_backend, content_type, bytes, sha256, state
                    ) VALUES (
                      %s, 'cover', %s,
                      'nas', %s, %s, %s, 'available'
                    )
                    ON DUPLICATE KEY UPDATE
                      object_key=VALUES(object_key),
                      content_type=VALUES(content_type),
                      bytes=VALUES(bytes),
                      sha256=VALUES(sha256),
                      state='available'
                    """,
                    (
                        int(catalog_id),
                        object_key,
                        content_type or None,
                        max(int(bytes_count), 0),
                        sha256 or None,
                    ),
                )
        self._invalidate_cache("catalog")
        if self.cache is not None:
            self.cache.schedule_warm(
                "cover",
                "mapping",
                {"catalog_id": int(catalog_id)},
                lambda: {
                    "found": (value := self.get_cover_asset(catalog_id))
                    is not None,
                    "value": value,
                },
                ttl_seconds=600,
            )

    def metadata_for_ids(
        self,
        catalog_ids: Iterable[int],
    ) -> dict[int, dict[str, Any]]:
        ids = sorted({int(value) for value in catalog_ids if int(value) > 0})
        if not ids:
            return {}
        result: dict[int, dict[str, Any]] = {}
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                for start in range(0, len(ids), 1000):
                    batch = ids[start : start + 1000]
                    placeholders = ", ".join(["%s"] * len(batch))
                    cursor.execute(
                        f"""
                        SELECT
                          m.catalog_id, m.summary, m.genre_tags, m.tone_tags,
                          keyword_counts, primary_tone_tags,
                          secondary_tone_tags, tone_confidence, tone_source,
                          tone_evidence, tone_review_status,
                          tone_review_model, tone_reviewed_at,
                          section_count, reader_index_status,
                          reader_schema_version, reader_indexed_at, indexed_at,
                          m.reader_source_path AS source_path,
                          m.reader_source_bytes AS source_bytes,
                          m.source_mtime_ns,
                          m.word_count,
                          m.chapter_count
                        FROM book_metadata m
                        JOIN books b ON b.id=m.catalog_id
                        WHERE m.catalog_id IN ({placeholders})
                        """,
                        batch,
                    )
                    for row in cursor.fetchall():
                        result[int(row["catalog_id"])] = dict(row)
        return result

    def tone_tag_stats(self, *, source: str = "all") -> list[dict[str, Any]]:
        if source not in {"all", "local", "fanqie"}:
            raise ValueError("来源必须是 all、local 或 fanqie")
        cache_payload = {"kind": "tone-tag-stats", "source": source}
        cached = self._cache_get(cache_payload)
        if cached is not None:
            return list(cached.get("items") or [])
        scope_sql = "b.is_active=1"
        scope_params: list[Any] = []
        if source != "all":
            scope_sql += " AND b.library_id=%s"
            scope_params.append(source)
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT tag_name AS name, COUNT(DISTINCT catalog_id) AS count
                    FROM (
                      SELECT m.catalog_id, genre_tag.tag_name
                      FROM book_metadata m
                      JOIN books b ON b.id=m.catalog_id
                      JOIN JSON_TABLE(
                        COALESCE(m.genre_tags, JSON_ARRAY()),
                        '$[*]' COLUMNS (
                          tag_name VARCHAR(120) PATH '$'
                        )
                      ) genre_tag
                      WHERE {scope_sql}
                      UNION ALL
                      SELECT m.catalog_id, tone_tag.tag_name
                      FROM book_metadata m
                      JOIN books b ON b.id=m.catalog_id
                      JOIN JSON_TABLE(
                        COALESCE(m.tone_tags, JSON_ARRAY()),
                        '$[*]' COLUMNS (
                          tag_name VARCHAR(120) PATH '$'
                        )
                      ) tone_tag
                      WHERE {scope_sql}
                    ) tags
                    WHERE tag_name<>''
                    GROUP BY tag_name
                    ORDER BY count DESC, name
                    LIMIT 100
                    """,
                    [*scope_params, *scope_params],
                )
                items = [
                    {
                        "name": str(row["name"]),
                        "count": int(row["count"]),
                    }
                    for row in cursor.fetchall()
                ]
        self._cache_set(cache_payload, {"items": items})
        return items

    def tone_review_counts(self) -> dict[str, int]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      COALESCE(SUM(tone_review_status='reviewed'), 0)
                        AS cumulative_reviewed,
                      COALESCE(SUM(tone_review_status='pending'), 0)
                        AS pending,
                      COALESCE(SUM(
                        tone_review_status='not_needed'
                        AND tone_confidence>=0.72
                      ), 0) AS local_high_confidence
                    FROM book_metadata
                    """
                )
                row = cursor.fetchone()
                return {
                    "cumulative_reviewed": int(
                        row["cumulative_reviewed"]
                    ),
                    "pending": int(row["pending"]),
                    "local_high_confidence": int(
                        row["local_high_confidence"]
                    ),
                }

    def browse_assets(
        self,
        *,
        asset: str,
        query: str,
        category: str,
        tag: str,
        source: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        if asset not in {"tone", "plot"}:
            raise ValueError("索引类型必须是 tone 或 plot")
        if source not in {"all", "local", "fanqie"}:
            raise ValueError("来源必须是 all、local 或 fanqie")
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 60)
        query, category, tag = query.strip(), category.strip(), tag.strip()
        cache_payload = {
            "kind": "assets",
            "schema_version": 2,
            "asset": asset,
            "query": query,
            "category": category,
            "tag": tag,
            "source": source,
            "page": page,
            "page_size": page_size,
        }
        cached = self._cache_get(cache_payload)
        if cached is not None:
            cached["cache"] = "redis"
            return cached
        if asset == "tone":
            from_sql = "book_metadata m JOIN books b ON b.id=m.catalog_id"
        else:
            from_sql = (
                "plot_index_meta p JOIN books b ON b.id=p.catalog_id "
                "LEFT JOIN book_metadata m ON m.catalog_id=b.id"
            )
        conditions = ["b.is_active=1"]
        params: list[Any] = []
        if source != "all":
            conditions.append("b.library_id=%s")
            params.append(source)
        if category:
            conditions.append("b.category=%s")
            params.append(category)
        if query:
            if len(query) >= 2:
                boolean_query = f'"{query.replace(chr(34), " ")}"'
                conditions.append(
                    """
                    (
                      MATCH(b.title,b.author)
                        AGAINST (%s IN BOOLEAN MODE)
                      OR MATCH(m.summary)
                        AGAINST (%s IN BOOLEAN MODE)
                    )
                    """
                )
                params.extend([boolean_query, boolean_query])
            else:
                escaped = (
                    query.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                pattern = f"%{escaped}%"
                conditions.append(
                    """
                    (
                      b.title LIKE %s ESCAPE '\\\\'
                      OR b.author LIKE %s ESCAPE '\\\\'
                      OR m.summary LIKE %s ESCAPE '\\\\'
                    )
                    """
                )
                params.extend([pattern, pattern, pattern])
        if tag:
            if asset != "tone":
                raise ValueError("剧情母题请在单书证据详情中查看")
            conditions.append(
                """
                (
                  JSON_CONTAINS(
                    COALESCE(m.genre_tags, JSON_ARRAY()),
                    JSON_QUOTE(%s)
                  )
                  OR JSON_CONTAINS(
                    COALESCE(m.tone_tags, JSON_ARRAY()),
                    JSON_QUOTE(%s)
                  )
                )
                """
            )
            params.extend([tag, tag])
        where_sql = " AND ".join(conditions)
        row_fields = """
            b.id AS catalog_id,
            COALESCE(b.source_id, CAST(b.id AS CHAR)) AS source_id,
            b.library_id,
            b.title,
            b.author,
            b.category,
            b.legacy_output_path AS source_path,
            b.body_object_key,
            b.cover_object_key,
            b.bytes AS source_bytes,
            b.status AS download_status,
            b.body_available AS readable,
            b.approx_word_count,
            b.approx_chapter_count,
            m.summary,
            m.genre_tags,
            m.tone_tags,
            m.primary_tone_tags,
            m.secondary_tone_tags,
            m.tone_confidence,
            m.tone_source,
            m.tone_evidence,
            m.tone_review_status,
            m.tone_review_model,
            m.tone_reviewed_at,
            m.indexed_at
        """
        if asset == "plot":
            row_fields += ", p.segment_count, p.indexed_at AS plot_indexed_at"
        ordering = (
            "m.indexed_at DESC, b.id DESC"
            if asset == "tone"
            else "p.indexed_at DESC, p.segment_count DESC, b.id DESC"
        )
        offset = (page - 1) * page_size
        evidence_total = 0
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) AS total FROM {from_sql} WHERE {where_sql}",
                    params,
                )
                total = int(cursor.fetchone()["total"])
                if asset == "plot":
                    cursor.execute(
                        f"""
                        SELECT COALESCE(SUM(p.segment_count), 0) AS total
                        FROM {from_sql}
                        WHERE {where_sql}
                        """,
                        params,
                    )
                    evidence_total = int(cursor.fetchone()["total"])
                cursor.execute(
                    f"""
                    SELECT {row_fields}
                    FROM {from_sql}
                    WHERE {where_sql}
                    ORDER BY {ordering}
                    LIMIT %s OFFSET %s
                    """,
                    [*params, page_size, offset],
                )
                rows = [dict(row) for row in cursor.fetchall()]
                scope = ["b.is_active=1"]
                scope_params: list[Any] = []
                if source != "all":
                    scope.append("b.library_id=%s")
                    scope_params.append(source)
                cursor.execute(
                    f"""
                    SELECT b.category AS name, COUNT(*) AS count
                    FROM {from_sql}
                    WHERE {" AND ".join(scope)}
                    GROUP BY b.category
                    ORDER BY count DESC, name
                    LIMIT 100
                    """,
                    scope_params,
                )
                categories = [
                    {
                        "name": str(row["name"] or "未分类"),
                        "count": int(row["count"]),
                    }
                    for row in cursor.fetchall()
                ]
        tags = self.tone_tag_stats(source=source) if asset == "tone" else []
        result = {
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "categories": categories,
            "tags": tags,
            "evidence_total": evidence_total,
            "cache": "mysql",
        }
        self._cache_set(cache_payload, result)
        return result

    def plot_evidence_page(
        self,
        catalog_id: int,
        *,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 20)
        cache_query = {
            "catalog_id": int(catalog_id),
            "page": page,
            "page_size": page_size,
        }
        if self.cache is not None:
            cached = self.cache.get_json("plot", "evidence-page", cache_query)
            if cached is not None:
                return cached
        offset = (page - 1) * page_size
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      p.catalog_id, p.source_id, p.segment_count, p.indexed_at,
                      b.title, b.author, b.category
                    FROM plot_index_meta p
                    JOIN books b ON b.id=p.catalog_id
                    WHERE p.catalog_id=%s
                    LIMIT 1
                    """,
                    (int(catalog_id),),
                )
                book = cursor.fetchone()
                if not book:
                    raise KeyError(f"未找到作品 {catalog_id} 的剧情索引")
                cursor.execute(
                    """
                    SELECT id, location, motif_tags, content
                    FROM plot_segments
                    WHERE catalog_id=%s
                    ORDER BY id
                    LIMIT %s OFFSET %s
                    """,
                    (int(catalog_id), page_size, offset),
                )
                rows = [dict(row) for row in cursor.fetchall()]
        result = {
            "book": dict(book),
            "rows": rows,
            "total": int(book["segment_count"]),
            "page": page,
            "page_size": page_size,
        }
        if self.cache is not None:
            self.cache.set_json(
                "plot", "evidence-page", cache_query, result, ttl_seconds=180
            )
        return result

    def replace_plot_book(
        self,
        *,
        catalog_id: int,
        source_id: str,
        source_bytes: int,
        source_mtime_ns: int,
        source_sha256: str,
        plot_rule_version: str,
        segments: list[dict[str, Any]],
        indexed_at: Any,
    ) -> None:
        prepared = []
        for segment in segments:
            tags = [
                str(value).strip()[:120]
                for value in segment.get("motif_tags", [])
                if str(value).strip()
            ]
            prepared.append(
                (
                    int(catalog_id),
                    source_id[:255],
                    str(segment.get("location") or "")[:512],
                    json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
                    " ".join(tags)[:1024],
                    str(segment.get("content") or ""),
                )
            )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM plot_segments WHERE catalog_id=%s",
                    (int(catalog_id),),
                )
                if prepared:
                    cursor.executemany(
                        """
                        INSERT INTO plot_segments (
                          catalog_id, source_id, location,
                          motif_tags, motif_text, content
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        prepared,
                    )
                cursor.execute(
                    """
                    INSERT INTO plot_index_meta (
                      catalog_id, source_id, source_bytes,
                      source_mtime_ns, source_sha256, plot_rule_version,
                      segment_count, indexed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      source_id=VALUES(source_id),
                      source_bytes=VALUES(source_bytes),
                      source_mtime_ns=VALUES(source_mtime_ns),
                      source_sha256=VALUES(source_sha256),
                      plot_rule_version=VALUES(plot_rule_version),
                      segment_count=VALUES(segment_count),
                      indexed_at=VALUES(indexed_at)
                    """,
                    (
                        int(catalog_id),
                        source_id[:255],
                        max(int(source_bytes), 0),
                        max(int(source_mtime_ns), 0),
                        str(source_sha256 or "")[:64],
                        str(plot_rule_version or "")[:64],
                        len(prepared),
                        indexed_at,
                    ),
                )
        self._invalidate_cache("derived")
        if self.cache is not None:
            self.cache.schedule_warm(
                "plot",
                "evidence-page",
                {"catalog_id": int(catalog_id), "page": 1, "page_size": 8},
                lambda: self.plot_evidence_page(
                    int(catalog_id), page=1, page_size=8
                ),
                ttl_seconds=180,
            )

    def clear_plot_index(self) -> None:
        # Delete plot_segments one catalog_id at a time to avoid FULLTEXT
        # ngram SYNC accumulating a massive undo log (see Phase 2 comment in
        # remove_unavailable_plot_index).
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT catalog_id FROM plot_segments"
                )
                catalog_ids = [
                    int(row["catalog_id"]) for row in cursor.fetchall()
                ]
        for cid in catalog_ids:
            with self.pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM plot_segments WHERE catalog_id=%s",
                        (cid,),
                    )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM plot_index_meta")
        self._invalidate_cache("derived")

    def plot_fingerprints(self) -> dict[str, tuple[int, int]]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT source_id, source_bytes, source_mtime_ns
                    FROM plot_index_meta
                    """
                )
                return {
                    str(row["source_id"]): (
                        int(row["source_bytes"]),
                        int(row["source_mtime_ns"]),
                    )
                    for row in cursor.fetchall()
                }

    def remove_stale_plot_sources(self, source_ids: Iterable[str]) -> int:
        values = sorted(
            {str(value).strip() for value in source_ids if str(value).strip()}
        )
        if not values:
            return 0
        removed = 0
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                for start in range(0, len(values), 1000):
                    batch = values[start : start + 1000]
                    placeholders = ", ".join(["%s"] * len(batch))
                    cursor.execute(
                        f"DELETE FROM plot_index_meta "
                        f"WHERE source_id IN ({placeholders})",
                        batch,
                    )
                    removed += cursor.rowcount
        if removed:
            self._invalidate_cache("derived")
        return removed

    def remove_unavailable_plot_index(self) -> int:
        """Remove plot evidence for catalog bodies that became unavailable."""

        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ps.catalog_id
                    FROM (
                        SELECT DISTINCT catalog_id FROM plot_segments
                    ) ps
                    WHERE ps.catalog_id NOT IN (
                        SELECT b.id FROM books b
                        WHERE b.is_active=1 AND b.body_available=1
                    )
                    """
                )
                orphan_catalog_ids = [
                    int(row["catalog_id"]) for row in cursor.fetchall()
                ]

        batch_size = 50
        for start in range(0, len(orphan_catalog_ids), batch_size):
            batch = orphan_catalog_ids[start : start + batch_size]
            with self.pool.connection() as connection:
                with connection.cursor() as cursor:
                    placeholders = ",".join(["%s"] * len(batch))
                    cursor.execute(
                        f"DELETE FROM plot_segments "
                        f"WHERE catalog_id IN ({placeholders})",
                        batch,
                    )

        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ps.catalog_id
                    FROM (
                        SELECT DISTINCT catalog_id FROM plot_index_meta
                    ) ps
                    WHERE ps.catalog_id NOT IN (
                        SELECT b.id FROM books b
                        WHERE b.is_active=1 AND b.body_available=1
                    )
                    """
                )
                orphan_meta_ids = [
                    int(row["catalog_id"]) for row in cursor.fetchall()
                ]

        for start in range(0, len(orphan_meta_ids), batch_size):
            batch = orphan_meta_ids[start : start + batch_size]
            with self.pool.connection() as connection:
                with connection.cursor() as cursor:
                    placeholders = ",".join(["%s"] * len(batch))
                    cursor.execute(
                        f"DELETE FROM plot_index_meta "
                        f"WHERE catalog_id IN ({placeholders})",
                        batch,
                    )

        if orphan_catalog_ids or orphan_meta_ids:
            self._invalidate_cache("derived")
        return len(orphan_meta_ids)

    def search_plot_candidates(
        self,
        *,
        terms: Iterable[str],
        motif_tags: Iterable[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        terms = sorted({
            str(value).strip()
            for value in terms
            if len(str(value).strip()) >= 2
        })[:20]
        motifs = sorted({
            str(value).strip()
            for value in motif_tags
            if str(value).strip()
        })[:12]
        conditions: list[str] = []
        params: list[Any] = []
        if terms:
            boolean_query = " ".join(
                f'"{term.replace(chr(34), " ")}"' for term in terms
            )
            conditions.append(
                """
                MATCH(ps.location,ps.motif_text,ps.content)
                  AGAINST (%s IN BOOLEAN MODE)
                """
            )
            params.append(boolean_query)
        if motifs:
            motif_conditions = []
            for motif in motifs:
                motif_conditions.append(
                    "JSON_CONTAINS(ps.motif_tags, JSON_QUOTE(%s))"
                )
                params.append(motif)
            conditions.append("(" + " OR ".join(motif_conditions) + ")")
        if not conditions:
            return []
        bounded_limit = min(max(int(limit), 1), 1000)
        cache_query = {
            "terms": terms,
            "motif_tags": motifs,
            "limit": bounded_limit,
        }
        if self.cache is not None and bounded_limit <= 100:
            cached = self.cache.get_json(
                "plot", "normalized-search", cache_query, expected_type=list
            )
            if cached is not None:
                return cached
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                      ps.id, ps.catalog_id, ps.source_id,
                      b.title, b.author, b.category,
                      ps.location, ps.motif_tags, ps.content
                    FROM plot_segments ps
                    JOIN books b ON b.id=ps.catalog_id
                    WHERE {" OR ".join(conditions)}
                    ORDER BY ps.id DESC
                    LIMIT %s
                    """,
                    [*params, bounded_limit],
                )
                rows = [dict(row) for row in cursor.fetchall()]
        if self.cache is not None and bounded_limit <= 100:
            self.cache.set_json(
                "plot",
                "normalized-search",
                cache_query,
                rows,
                ttl_seconds=180,
            )
        return rows

    def move_books(
        self,
        *,
        catalog_ids: Iterable[int],
        target_library: str,
        moved_at: Any,
    ) -> list[dict[str, Any]]:
        if target_library not in {"local", "fanqie"}:
            raise ValueError("目标书库必须是 local 或 fanqie")
        ids = sorted({int(value) for value in catalog_ids if int(value) > 0})
        if not ids:
            raise ValueError("请至少选择一本小说")
        if len(ids) > 500:
            raise ValueError("单次最多移动 500 本小说")
        placeholders = ", ".join(["%s"] * len(ids))
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id AS catalog_id, source_id, title, author, library_id
                    FROM books
                    WHERE id IN ({placeholders}) AND status<>'duplicate'
                    FOR UPDATE
                    """,
                    ids,
                )
                rows = [dict(row) for row in cursor.fetchall()]
                found = {int(row["catalog_id"]) for row in rows}
                missing = [catalog_id for catalog_id in ids if catalog_id not in found]
                if missing:
                    raise ValueError(
                        "以下书目不存在或已去重："
                        + "、".join(str(value) for value in missing[:20])
                    )
                events = [
                    (
                        int(row["catalog_id"]),
                        str(row["library_id"]),
                        target_library,
                        "manual",
                        moved_at,
                    )
                    for row in rows
                    if str(row["library_id"]) != target_library
                ]
                if events:
                    cursor.executemany(
                        """
                        INSERT INTO library_membership_events (
                            catalog_id, previous_library_id, target_library_id,
                            reason, moved_at
                        ) VALUES (%s, %s, %s, %s, %s)
                        """,
                        events,
                    )
                    cursor.execute(
                        f"""
                        UPDATE books
                        SET library_id=%s, row_version=row_version+1
                        WHERE id IN ({placeholders})
                        """,
                        [target_library, *ids],
                    )
                    # Migrations 007 and 009 maintain all catalog counters
                    # incrementally.  Rebuilding them here would turn a move
                    # of a few books into a full-table scan at production
                    # scale.
        for row in rows:
            row["previous_library"] = str(row["library_id"])
            row["library_id"] = target_library
        if rows:
            self._invalidate_cache("catalog", "derived")
        return rows

    def prepare_book_deletion(
        self,
        catalog_ids: Iterable[int],
    ) -> dict[str, Any]:
        """Resolve exact selected rows and assets without mutating state."""

        ids = sorted({int(value) for value in catalog_ids if int(value) > 0})
        if not ids or len(ids) > 100:
            raise ValueError("批量删除书目数量必须在 1..100")
        placeholders = ", ".join(["%s"] * len(ids))
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT b.id AS catalog_id,b.source_id,b.detail_url,
                           b.title,b.author,b.category,b.library_id,b.status,
                           b.body_object_key,b.cover_object_key,
                           b.legacy_output_path,b.bytes,b.sha256,
                           m.reader_source_path
                    FROM books b
                    LEFT JOIN book_metadata m ON m.catalog_id=b.id
                    WHERE b.id IN ({placeholders}) AND b.is_active=1
                    ORDER BY b.id
                    """,
                    ids,
                )
                rows = [dict(row) for row in cursor.fetchall()]
                found = {int(row["catalog_id"]) for row in rows}
                missing = [value for value in ids if value not in found]
                if missing:
                    raise ValueError(
                        "以下书目不存在或已删除："
                        + "、".join(str(value) for value in missing[:20])
                    )
                cursor.execute(
                    f"""
                    SELECT catalog_id,asset_type,object_key,bytes,sha256
                    FROM object_assets
                    WHERE catalog_id IN ({placeholders})
                    ORDER BY catalog_id,asset_type
                    """,
                    ids,
                )
                object_assets = [dict(row) for row in cursor.fetchall()]

                object_keys = sorted(
                    {
                        str(value).strip()
                        for value in [
                            *(
                                item.get(field)
                                for item in rows
                                for field in ("body_object_key", "cover_object_key")
                            ),
                            *(item.get("object_key") for item in object_assets),
                        ]
                        if str(value or "").strip()
                    }
                )
                shared_object_keys: set[str] = set()
                if object_keys:
                    object_placeholders = ", ".join(["%s"] * len(object_keys))
                    cursor.execute(
                        f"""
                        SELECT object_key FROM (
                          SELECT body_object_key AS object_key FROM books
                          WHERE id NOT IN ({placeholders})
                            AND body_object_key IN ({object_placeholders})
                          UNION ALL
                          SELECT cover_object_key AS object_key FROM books
                          WHERE id NOT IN ({placeholders})
                            AND cover_object_key IN ({object_placeholders})
                          UNION ALL
                          SELECT object_key FROM object_assets
                          WHERE catalog_id NOT IN ({placeholders})
                            AND object_key IN ({object_placeholders})
                        ) shared
                        """,
                        [
                            *ids,
                            *object_keys,
                            *ids,
                            *object_keys,
                            *ids,
                            *object_keys,
                        ],
                    )
                    shared_object_keys = {
                        str(row["object_key"]) for row in cursor.fetchall()
                    }

                legacy_paths = sorted(
                    {
                        str(value).strip()
                        for row in rows
                        for value in (
                            row.get("legacy_output_path"),
                            row.get("reader_source_path"),
                        )
                        if str(value or "").strip()
                    }
                )
                shared_legacy_paths: set[str] = set()
                if legacy_paths:
                    path_placeholders = ", ".join(["%s"] * len(legacy_paths))
                    cursor.execute(
                        f"""
                        SELECT source_path FROM (
                          SELECT legacy_output_path AS source_path FROM books
                          WHERE id NOT IN ({placeholders})
                            AND legacy_output_path IN ({path_placeholders})
                          UNION ALL
                          SELECT reader_source_path AS source_path
                          FROM book_metadata
                          WHERE catalog_id NOT IN ({placeholders})
                            AND reader_source_path IN ({path_placeholders})
                        ) shared
                        """,
                        [*ids, *legacy_paths, *ids, *legacy_paths],
                    )
                    shared_legacy_paths = {
                        str(row["source_path"]) for row in cursor.fetchall()
                    }

        return {
            "books": rows,
            "object_assets": object_assets,
            "exclusive_object_keys": [
                value for value in object_keys if value not in shared_object_keys
            ],
            "exclusive_legacy_paths": [
                value for value in legacy_paths if value not in shared_legacy_paths
            ],
        }

    def delete_books(self, catalog_ids: Iterable[int]) -> dict[str, Any]:
        """Delete exact catalog rows; foreign keys cascade derived state."""

        ids = sorted({int(value) for value in catalog_ids if int(value) > 0})
        if not ids or len(ids) > 100:
            raise ValueError("批量删除书目数量必须在 1..100")
        placeholders = ", ".join(["%s"] * len(ids))
        related_tables = (
            "book_metadata",
            "plot_index_meta",
            "plot_segments",
            "object_assets",
            "download_jobs",
            "library_postprocess_jobs",
            "library_covers",
            "library_fanqie_cover_jobs",
            "library_clean_cover_jobs",
            "local_source_upgrade_jobs",
            "library_membership_events",
            "book_public_ids",
            "global_book_identity_claims",
            "book_public_metrics",
            "book_public_metric_visitors",
            "authorized_source_updates",
        )
        related_counts: dict[str, int] = {}
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id,status FROM books
                    WHERE id IN ({placeholders}) AND is_active=1
                    FOR UPDATE
                    """,
                    ids,
                )
                rows = [dict(row) for row in cursor.fetchall()]
                found = {int(row["id"]) for row in rows}
                if found != set(ids):
                    missing = sorted(set(ids) - found)
                    raise ValueError(
                        "以下书目不存在或已删除："
                        + "、".join(str(value) for value in missing[:20])
                    )

                running_checks = (
                    ("download_jobs", "status IN ('processing','running','leased')"),
                    ("library_postprocess_jobs", "status='processing'"),
                    ("library_covers", "status='processing'"),
                    ("library_fanqie_cover_jobs", "status='processing'"),
                    ("library_clean_cover_jobs", "status='processing'"),
                    ("local_source_upgrade_jobs", "status='processing'"),
                )
                for table, condition in running_checks:
                    cursor.execute(
                        f"SELECT COUNT(*) AS total FROM {table} "
                        f"WHERE catalog_id IN ({placeholders}) AND {condition}",
                        ids,
                    )
                    if int(cursor.fetchone()["total"] or 0):
                        raise ValueError("所选书目仍有运行中的下载、封面或索引任务")

                for table in related_tables:
                    cursor.execute(
                        f"SELECT COUNT(*) AS total FROM {table} "
                        f"WHERE catalog_id IN ({placeholders})",
                        ids,
                    )
                    related_counts[table] = int(cursor.fetchone()["total"] or 0)

                related_counts["authorized_source_updates_detached"] = (
                    related_counts.pop("authorized_source_updates", 0)
                )
                cursor.execute(
                    f"""
                    DELETE FROM duplicate_books
                    WHERE duplicate_book_id IN ({placeholders})
                       OR kept_book_id IN ({placeholders})
                    """,
                    [*ids, *ids],
                )
                related_counts["duplicate_books"] = int(cursor.rowcount or 0)
                cursor.execute(
                    f"DELETE FROM books WHERE id IN ({placeholders})",
                    ids,
                )
                if int(cursor.rowcount or 0) != len(ids):
                    raise RuntimeError("书目删除数量与明确选择不一致，事务已回滚")
        self._invalidate_cache("catalog", "derived", "tone-facets")
        return {"deleted": len(ids), "related_counts": related_counts}
