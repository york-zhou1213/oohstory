from __future__ import annotations

import math
import queue
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from .settings import Settings


WORD_FILTERS: dict[str, tuple[int | None, int | None]] = {
    "under_100k": (None, 100_000),
    "over_100k": (100_000, None),
    "over_200k": (200_000, None),
    "over_300k": (300_000, None),
    "over_500k": (500_000, None),
    "over_1m": (1_000_000, None),
    "over_2m": (2_000_000, None),
}

WORD_BUCKET_MINIMUMS = {
    "under_100k": (0, 0),
    "over_100k": (1, 6),
    "over_200k": (2, 6),
    "over_300k": (3, 6),
    "over_500k": (4, 6),
    "over_1m": (5, 6),
    "over_2m": (6, 6),
}


class MySQLReadPool:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.size = settings.mysql_pool_size
        self._available: queue.LifoQueue[pymysql.Connection] = queue.LifoQueue(
            maxsize=self.size
        )
        self._created = 0

    def _new_connection(self) -> pymysql.Connection:
        password_file = self.settings.mysql_password_file
        if password_file is None or not password_file.is_file():
            raise RuntimeError("OOH Story MySQL 密码文件不存在")
        password = password_file.read_text(encoding="utf-8").strip()
        return pymysql.connect(
            host=self.settings.mysql_host,
            port=self.settings.mysql_port,
            user=self.settings.mysql_user,
            password=password,
            database=self.settings.mysql_database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=5,
            program_name="oohstory-reader",
            init_command=(
                "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED"
            ),
        )

    def _acquire(self) -> pymysql.Connection:
        try:
            connection = self._available.get_nowait()
        except queue.Empty:
            if self._created < self.size:
                self._created += 1
                try:
                    return self._new_connection()
                except Exception:
                    self._created -= 1
                    raise
            connection = self._available.get(timeout=5)
        try:
            connection.ping(reconnect=True)
        except Exception:
            self._created -= 1
            return self._new_connection()
        return connection

    @contextmanager
    def connection(self) -> Iterator[pymysql.Connection]:
        connection = self._acquire()
        try:
            yield connection
        finally:
            try:
                # A borrower may have used an explicit transaction.  Always
                # end any unfinished transaction before another request can
                # receive this connection from the pool.
                connection.rollback()
                self._available.put_nowait(connection)
            except Exception:
                connection.close()
                self._created -= 1

    @contextmanager
    def transaction(self) -> Iterator[pymysql.Connection]:
        with self.connection() as connection:
            connection.begin()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise


class MySQLPublicCatalog:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.pool = MySQLReadPool(settings)

    @staticmethod
    def _search_clause(query: str) -> tuple[str, list[Any], str, list[Any]]:
        if not query:
            return "", [], "b.id DESC", []
        escaped = (
            query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        if len(query) >= 2:
            boolean_query = f'"{query.replace(chr(34), " ")}"'
            return (
                " AND MATCH(b.title,b.author) "
                "AGAINST (%s IN BOOLEAN MODE)",
                [boolean_query],
                "CASE WHEN b.title=%s THEN 0 WHEN b.author=%s THEN 1 "
                "WHEN b.title LIKE %s ESCAPE '\\\\' THEN 2 ELSE 3 END, "
                "b.id DESC",
                [query, query, f"{escaped}%"],
            )
        pattern = f"%{escaped}%"
        return (
            " AND (b.title LIKE %s ESCAPE '\\\\' "
            "OR b.author LIKE %s ESCAPE '\\\\')",
            [pattern, pattern],
            "CASE WHEN b.title=%s THEN 0 WHEN b.author=%s THEN 1 "
            "WHEN b.title LIKE %s ESCAPE '\\\\' THEN 2 ELSE 3 END, "
            "b.id DESC",
            [query, query, f"{escaped}%"],
        )

    def health(self) -> dict[str, Any]:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT @@version AS version, "
                    "DATABASE() AS database_name, "
                    "COUNT(*) AS readable "
                    "FROM books WHERE is_active=1 AND body_available=1 "
                    "AND is_published=1"
                )
                return dict(cursor.fetchone())

    def stats(self) -> dict[str, int]:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        COALESCE(SUM(book_count), 0) AS catalog_total,
                        COALESCE(SUM(
                            CASE WHEN body_available=1
                                 THEN book_count ELSE 0 END
                        ), 0) AS readable_total,
                        COUNT(DISTINCT CASE
                            WHEN body_available=1 AND book_count>0
                            THEN category
                        END) AS category_total
                    FROM catalog_facets
                    """
                )
                row = cursor.fetchone()
                return {
                    "catalog_total": int(row["catalog_total"]),
                    "readable_total": int(row["readable_total"]),
                    "category_total": int(row["category_total"]),
                }

    def categories(self) -> list[dict[str, Any]]:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT category AS name, SUM(book_count) AS count
                    FROM public_catalog_facets
                    WHERE book_count>0
                    GROUP BY category
                    ORDER BY count DESC, name
                    """
                )
                return [
                    {"name": str(row["name"]), "count": int(row["count"])}
                    for row in cursor.fetchall()
                ]

    def category_books(self, per_category: int = 5) -> dict[str, list[dict[str, Any]]]:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT t.category, t.title, t.author, t.public_id,
                           t.cover_object_key, t.row_version, t.cat_total
                    FROM (
                        SELECT b.category, b.title, b.author, p.public_id,
                               b.cover_object_key, b.row_version,
                               COUNT(*) OVER (PARTITION BY b.category) AS cat_total,
                               ROW_NUMBER() OVER (
                                   PARTITION BY b.category
                                   ORDER BY COALESCE(b.effective_word_count, 0) DESC
                               ) AS rn
                        FROM books b
                        INNER JOIN book_public_ids p ON p.catalog_id=b.id
                        WHERE b.is_active=1 AND b.body_available=1
                          AND b.is_published=1
                    ) t
                    WHERE t.rn <= %s
                    ORDER BY t.cat_total DESC, t.category, t.rn
                    """,
                    (per_category,),
                )
                result: dict[str, list[dict[str, Any]]] = {}
                for row in cursor.fetchall():
                    cat = str(row["category"])
                    if cat not in result:
                        result[cat] = []
                    result[cat].append({
                        "title": str(row["title"]),
                        "author": str(row["author"]),
                        "public_id": row["public_id"],
                        "cover_object_key": row.get("cover_object_key"),
                        "row_version": int(row.get("row_version") or 0),
                    })
                return result

    def list_books(
        self,
        *,
        query: str,
        category: str,
        words: str,
        serialization: str,
        page: int,
        page_size: int,
        sort: str,
    ) -> dict[str, Any]:
        conditions = [
            "b.is_active=1",
            "b.body_available=1",
            "b.is_published=1",
        ]
        params: list[Any] = []
        if category:
            conditions.append("b.category=%s")
            params.append(category)
        search_sql, search_params, search_order, search_order_params = (
            self._search_clause(query)
        )
        if search_sql:
            conditions.append(search_sql.removeprefix(" AND "))
            params.extend(search_params)
        if words:
            minimum, maximum = WORD_FILTERS[words]
            if minimum is not None:
                conditions.append("b.effective_word_count >= %s")
                params.append(minimum)
            if maximum is not None:
                conditions.append("b.effective_word_count < %s")
                params.append(maximum)
        if serialization:
            conditions.append("b.serialization_code=%s")
            params.append(1 if serialization == "finished" else 0)
        where = " AND ".join(conditions)
        order_map = {
            "recent": "b.id DESC",
            "title": "b.title ASC, b.id DESC",
            "long": "b.effective_word_count DESC, b.id DESC",
        }
        order_sql = search_order if query else order_map[sort]
        order_params = search_order_params if query else []
        index_name = ""
        if not query:
            if sort == "long":
                index_name = "idx_books_readable_words"
            elif sort == "title":
                index_name = "idx_books_readable_title"
            elif category:
                index_name = "idx_books_readable_category_recent"
            elif serialization:
                index_name = "idx_books_readable_serialization"
            else:
                index_name = "idx_books_readable_recent"
        books_from = (
            f"books b FORCE INDEX ({index_name})"
            if index_name
            else "books b"
        )
        offset = (page - 1) * page_size
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                if not query:
                    facet_conditions = ["book_count>0"]
                    facet_params: list[Any] = []
                    if category:
                        facet_conditions.append("category=%s")
                        facet_params.append(category)
                    if serialization:
                        facet_conditions.append("serialization_code=%s")
                        facet_params.append(
                            1 if serialization == "finished" else 0
                        )
                    if words:
                        minimum_bucket, maximum_bucket = WORD_BUCKET_MINIMUMS[
                            words
                        ]
                        facet_conditions.append(
                            "word_bucket BETWEEN %s AND %s"
                        )
                        facet_params.extend(
                            [minimum_bucket, maximum_bucket]
                        )
                    cursor.execute(
                        "SELECT COALESCE(SUM(book_count),0) AS total "
                        "FROM public_catalog_facets WHERE "
                        + " AND ".join(facet_conditions),
                        facet_params,
                    )
                    total = int(cursor.fetchone()["total"])
                else:
                    cursor.execute(
                        f"SELECT COUNT(*) AS total "
                        f"FROM {books_from} WHERE {where}",
                        params,
                    )
                    total = int(cursor.fetchone()["total"])
                cursor.execute(
                    f"""
                    SELECT
                        b.id AS catalog_id,
                        p.public_id,
                        COALESCE(b.source_id, CAST(b.id AS CHAR)) AS source_id,
                        b.title,
                        b.author,
                        b.category,
                        b.bytes AS source_bytes,
                        b.mysql_updated_at AS updated_at,
                        b.effective_word_count AS filtered_word_count,
                        b.approx_chapter_count,
                        b.row_version,
                        CASE WHEN b.serialization_code=1
                             THEN 'finished' ELSE 'ongoing' END
                            AS serialization_status,
                        b.cover_object_key,
                        m.summary,
                        m.genre_tags,
                        m.tone_tags
                    FROM {books_from}
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    LEFT JOIN book_metadata m ON m.catalog_id=b.id
                    WHERE {where}
                    ORDER BY {order_sql}
                    LIMIT %s OFFSET %s
                    """,
                    [*params, *order_params, page_size, offset],
                )
                items = [dict(row) for row in cursor.fetchall()]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "page_count": max(math.ceil(total / page_size), 1),
        }

    def get_book(self, public_id: bytes) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        b.id AS catalog_id,
                        p.public_id,
                        COALESCE(b.source_id, CAST(b.id AS CHAR)) AS source_id,
                        b.title,
                        b.author,
                        b.category,
                        b.bytes AS source_bytes,
                        b.body_object_key,
                        b.cover_object_key,
                        b.row_version,
                        b.discovered_at,
                        b.mysql_updated_at AS updated_at,
                        b.effective_word_count AS filtered_word_count,
                        b.approx_chapter_count,
                        CASE WHEN b.serialization_code=1
                             THEN 'finished' ELSE 'ongoing' END
                            AS serialization_status,
                        m.summary,
                        m.genre_tags,
                        m.tone_tags
                    FROM books b
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    LEFT JOIN book_metadata m ON m.catalog_id=b.id
                    WHERE p.public_id=%s
                      AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    LIMIT 1
                    """,
                    (public_id,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def get_books(self, public_ids: list[bytes]) -> list[dict[str, Any]]:
        """Fetch account-card metadata in one bounded query."""
        unique = list(dict.fromkeys(public_ids))[:1000]
        if not unique:
            return []
        placeholders = ",".join(["%s"] * len(unique))
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT b.id AS catalog_id,p.public_id,b.title,b.author,b.category,
                           b.bytes AS source_bytes,b.body_object_key,b.cover_object_key,
                           b.row_version,b.effective_word_count AS filtered_word_count,
                           b.approx_chapter_count,
                           CASE WHEN b.serialization_code=1 THEN 'finished' ELSE 'ongoing' END
                             AS serialization_status,
                           m.summary,m.genre_tags,m.tone_tags
                    FROM books b
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    LEFT JOIN book_metadata m ON m.catalog_id=b.id
                    WHERE p.public_id IN ({placeholders})
                      AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    """,
                    unique,
                )
                return [dict(row) for row in cursor.fetchall()]

    def source_book(self, public_id: bytes) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        b.id AS catalog_id,
                        p.public_id,
                        b.title,
                        b.author,
                        b.category,
                        b.bytes AS source_bytes,
                        b.body_object_key
                    FROM books b
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    WHERE p.public_id=%s
                      AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    LIMIT 1
                    """,
                    (public_id,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def cover_descriptor(self, public_id: bytes) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT b.cover_object_key, b.row_version
                    FROM books b
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    WHERE p.public_id=%s
                      AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                      AND NULLIF(TRIM(COALESCE(b.cover_object_key,'')), '')
                          IS NOT NULL
                    LIMIT 1
                    """,
                    (public_id,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def find_public_id(self, title: str) -> bytes | None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT p.public_id
                    FROM books b
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    WHERE b.title=%s AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    ORDER BY b.bytes DESC, b.id
                    LIMIT 1
                    """,
                    (title,),
                )
                row = cursor.fetchone()
                return bytes(row["public_id"]) if row else None

    def public_metrics(self, public_id: bytes) -> dict[str, int] | None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        b.id AS catalog_id,
                        COALESCE(pm.read_count, 0) AS read_count,
                        COALESCE(pm.download_count, 0) AS download_count,
                        COALESCE(pm.recommend_count, 0) AS recommend_count,
                        COALESCE(pm.favorite_count, 0) AS favorite_count
                    FROM books b
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    LEFT JOIN book_public_metrics pm ON pm.catalog_id=b.id
                    WHERE p.public_id=%s
                      AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    LIMIT 1
                    """,
                    (public_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    "catalog_id": int(row["catalog_id"]),
                    "read_count": int(row["read_count"] or 0),
                    "download_count": int(row["download_count"] or 0),
                    "recommend_count": int(row["recommend_count"] or 0),
                    "favorite_count": int(row["favorite_count"] or 0),
                }

    def set_favorite_count(
        self, public_id: bytes, favorite_count: int
    ) -> dict[str, int] | None:
        """Set favorites from the account database instead of anonymous events."""
        normalized = max(int(favorite_count), 0)
        with self.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT b.id
                    FROM books b
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    WHERE p.public_id=%s
                      AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    LIMIT 1
                    FOR SHARE
                    """,
                    (public_id,),
                )
                book = cursor.fetchone()
                if not book:
                    return None
                catalog_id = int(book["id"])
                cursor.execute(
                    """
                    INSERT INTO book_public_metrics
                        (catalog_id, read_count, download_count,
                         recommend_count, favorite_count, updated_at)
                    VALUES (%s, 0, 0, 0, %s, UTC_TIMESTAMP(6))
                    ON DUPLICATE KEY UPDATE
                        favorite_count=VALUES(favorite_count),
                        updated_at=VALUES(updated_at)
                    """,
                    (catalog_id, normalized),
                )
                cursor.execute(
                    """
                    SELECT catalog_id,read_count,download_count,
                           recommend_count,favorite_count
                    FROM book_public_metrics WHERE catalog_id=%s
                    """,
                    (catalog_id,),
                )
                row = cursor.fetchone()
                return {
                    "catalog_id": int(row["catalog_id"]),
                    "read_count": int(row["read_count"] or 0),
                    "download_count": int(row["download_count"] or 0),
                    "recommend_count": int(row["recommend_count"] or 0),
                    "favorite_count": int(row["favorite_count"] or 0),
                }

    def engagement_recommendations(
        self, limit: int, *, words: str = ""
    ) -> list[dict[str, Any]]:
        """Return the strongest real reader signals for recommendation slots."""
        conditions = [
            "b.is_active=1",
            "b.body_available=1",
            "b.is_published=1",
            "(pm.recommend_count>0 OR pm.favorite_count>0 OR pm.read_count>0)",
        ]
        params: list[Any] = []
        if words:
            minimum, maximum = WORD_FILTERS[words]
            if minimum is not None:
                conditions.append("b.effective_word_count >= %s")
                params.append(minimum)
            if maximum is not None:
                conditions.append("b.effective_word_count < %s")
                params.append(maximum)
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT b.id AS catalog_id,p.public_id,
                           COALESCE(b.source_id,CAST(b.id AS CHAR)) AS source_id,
                           b.title,b.author,b.category,b.bytes AS source_bytes,
                           b.mysql_updated_at AS updated_at,
                           b.effective_word_count AS filtered_word_count,
                           b.approx_chapter_count,b.row_version,
                           CASE WHEN b.serialization_code=1
                                THEN 'finished' ELSE 'ongoing' END
                             AS serialization_status,
                           b.cover_object_key,m.summary,m.genre_tags,m.tone_tags,
                           (pm.recommend_count * 12000 + pm.favorite_count * 10000 +
                            LEAST(pm.read_count, 5000)) AS engagement_score
                    FROM book_public_metrics pm
                    INNER JOIN books b ON b.id=pm.catalog_id
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    LEFT JOIN book_metadata m ON m.catalog_id=b.id
                    WHERE {' AND '.join(conditions)}
                    ORDER BY engagement_score DESC,pm.updated_at DESC,b.id DESC
                    LIMIT %s
                    """,
                    [*params, max(1, min(int(limit), 100))],
                )
                return [dict(row) for row in cursor.fetchall()]

    def record_public_metric(
        self,
        public_id: bytes,
        visitor_hash: bytes,
        event: str,
    ) -> dict[str, Any] | None:
        timestamp_column = {
            "read": "first_read_at",
            "download": "first_download_at",
            "recommend": "first_recommend_at",
            "favorite": "first_favorite_at",
        }[event]
        count_column = {
            "read": "read_count",
            "download": "download_count",
            "recommend": "recommend_count",
            "favorite": "favorite_count",
        }[event]
        with self.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT b.id
                    FROM books b
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    WHERE p.public_id=%s
                      AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    LIMIT 1
                    FOR SHARE
                    """,
                    (public_id,),
                )
                book_row = cursor.fetchone()
                if not book_row:
                    return None
                book_id = int(book_row["id"])
                cursor.execute(
                    """
                    INSERT INTO book_public_metrics
                        (catalog_id, read_count, download_count,
                         recommend_count, favorite_count, updated_at)
                    VALUES (%s, 0, 0, 0, 0, UTC_TIMESTAMP(6))
                    ON DUPLICATE KEY UPDATE catalog_id=VALUES(catalog_id)
                    """,
                    (int(book_id),),
                )
                cursor.execute(
                    f"""
                    INSERT IGNORE INTO book_public_metric_visitors
                        (catalog_id, visitor_hash, {timestamp_column},
                         created_at, updated_at)
                    VALUES (%s, %s, UTC_TIMESTAMP(6),
                            UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
                    """,
                    (int(book_id), visitor_hash),
                )
                counted = cursor.rowcount == 1
                if not counted:
                    cursor.execute(
                        f"""
                        UPDATE book_public_metric_visitors
                        SET {timestamp_column}=UTC_TIMESTAMP(6),
                            updated_at=UTC_TIMESTAMP(6)
                        WHERE catalog_id=%s AND visitor_hash=%s
                          AND {timestamp_column} IS NULL
                        """,
                        (int(book_id), visitor_hash),
                    )
                    counted = cursor.rowcount == 1
                if counted:
                    cursor.execute(
                        f"""
                        UPDATE book_public_metrics
                        SET {count_column}={count_column}+1,
                            updated_at=UTC_TIMESTAMP(6)
                        WHERE catalog_id=%s
                        """,
                        (int(book_id),),
                    )
                cursor.execute(
                    """
                    SELECT catalog_id, read_count, download_count,
                           recommend_count, favorite_count
                    FROM book_public_metrics
                    WHERE catalog_id=%s
                    """,
                    (int(book_id),),
                )
                row = cursor.fetchone()
                return {
                    "catalog_id": int(row["catalog_id"]),
                    "read_count": int(row["read_count"] or 0),
                    "download_count": int(row["download_count"] or 0),
                    "recommend_count": int(row["recommend_count"] or 0),
                    "favorite_count": int(row["favorite_count"] or 0),
                    "counted": counted,
                }

    def rankings(self, limit: int = 10) -> dict[str, list[dict]]:
        def _row(r, value_key: str) -> dict:
            return {
                "title": r["title"], "author": r["author"],
                "category": r["category"],
                "public_id": bytes(r["public_id"]),
                "cover_object_key": r.get("cover_object_key"),
                "row_version": int(r.get("row_version") or 0),
                "value": int(r[value_key] or 0),
            }

        results: dict[str, list[dict]] = {}
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT v.catalog_id, b.title, b.author, b.category,
                           b.cover_object_key, b.row_version,
                           p.public_id, COUNT(*) AS cnt
                    FROM book_public_metric_visitors v
                    INNER JOIN books b ON b.id=v.catalog_id
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    WHERE v.first_read_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 7 DAY)
                      AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    GROUP BY v.catalog_id
                    ORDER BY cnt DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                results["weekly_clicks"] = [_row(r, "cnt") for r in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT v.catalog_id, b.title, b.author, b.category,
                           b.cover_object_key, b.row_version,
                           p.public_id, COUNT(*) AS cnt
                    FROM book_public_metric_visitors v
                    INNER JOIN books b ON b.id=v.catalog_id
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    WHERE v.first_read_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY)
                      AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    GROUP BY v.catalog_id
                    ORDER BY cnt DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                results["monthly_clicks"] = [_row(r, "cnt") for r in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT v.catalog_id, b.title, b.author, b.category,
                           b.cover_object_key, b.row_version,
                           p.public_id, COUNT(*) AS cnt
                    FROM book_public_metric_visitors v
                    INNER JOIN books b ON b.id=v.catalog_id
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    WHERE v.first_recommend_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY)
                      AND b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    GROUP BY v.catalog_id
                    ORDER BY cnt DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                results["monthly_recommends"] = [_row(r, "cnt") for r in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT b.id, b.title, b.author, b.category,
                           b.cover_object_key, b.row_version, p.public_id,
                           b.effective_word_count
                    FROM books b
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    WHERE b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                    ORDER BY b.id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                results["new_books"] = [_row(r, "effective_word_count") for r in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT pm.catalog_id, b.title, b.author, b.category,
                           b.cover_object_key, b.row_version,
                           p.public_id, pm.favorite_count
                    FROM book_public_metrics pm
                    INNER JOIN books b ON b.id=pm.catalog_id
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    WHERE b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                      AND pm.favorite_count > 0
                    ORDER BY pm.favorite_count DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                results["favorites"] = [_row(r, "favorite_count") for r in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT b.id, b.title, b.author, b.category,
                           b.cover_object_key, b.row_version, p.public_id,
                           COALESCE(pm.read_count, 0) AS read_count
                    FROM books b
                    INNER JOIN book_public_ids p ON p.catalog_id=b.id
                    LEFT JOIN book_public_metrics pm ON pm.catalog_id=b.id
                    WHERE b.is_active=1 AND b.body_available=1
                      AND b.is_published=1
                      AND b.serialization_code=1
                    ORDER BY COALESCE(pm.read_count, 0) DESC,
                             b.effective_word_count DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                results["completed"] = [_row(r, "read_count") for r in cursor.fetchall()]
        return results
