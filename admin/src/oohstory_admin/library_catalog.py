"""Read-only catalog facade for the OOHStory administration UI.

The original electronic-library page mixed project-writing features with the
shared catalog.  This module exposes only the reusable catalog surfaces that
belong in OOHStory Admin: source totals, readable bodies, the global tone
index, the global deconstruction library and the two bounded incremental-index
entry points.  It never calls the legacy web application and it never mutates
catalog rows.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .config import Settings
from .units import UNIT_ALLOWLIST
from oohstory_library.services.library_cache import (
    LibraryCacheSettings,
    RedisHotCache,
)
from oohstory_library.services.library_object_store import NasObjectStore
from oohstory_library.services.default_cover import (
    OOHSTORY_DEFAULT_COVER_SHA256,
    default_cover_template_path,
)


CATALOG_LIBRARIES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "local": MappingProxyType(
            {"id": "local", "label": "本地书库", "description": "本地 TXT80/TXT020 馆藏"}
        ),
        "fanqie": MappingProxyType(
            {
                "id": "fanqie",
                "label": "番茄/授权书库",
                "description": "番茄下载历史与授权/公版来源馆藏",
            }
        ),
    }
)
CATALOG_FILTERS = frozenset({"all", *CATALOG_LIBRARIES})
AVAILABILITY_FILTERS = frozenset({"all", "readable", "recovery"})
VIEWS: Mapping[str, str] = MappingProxyType(
    {
        "all": "书目总量",
        "local": "本地书库",
        "fanqie": "番茄书库",
        "readable": "正文可用",
        "tone": "基调索引",
        "plot": "剧情索引",
        "deconstruction": "全局拆书库",
    }
)


@dataclass(frozen=True, slots=True)
class IncrementalIndexTask:
    """One exact, non-parameterized systemd task exposed to the UI."""

    id: str
    label: str
    unit: str
    action: str
    description: str
    scope: str
    writes_catalog: bool = False
    project_tone_matching: bool = False


_INDEX_TASKS = (
    IncrementalIndexTask(
        id="incremental_index",
        label="增量更新索引",
        unit="oohstory-library-index-refresh.service",
        action="start",
        description="只处理尚未进入共享基调索引的正文；已有索引作为断点复用。",
        scope="global_tone_index",
    ),
    IncrementalIndexTask(
        id="ingestion_index",
        label="补齐新书可见索引",
        unit="oohstory-library-ingestion-index.service",
        action="start",
        description="消费新入库书目的持久队列，只补齐指定书目的轻量可见索引。",
        scope="newly_ingested_books",
    ),
)
INCREMENTAL_INDEX_TASKS: Mapping[str, IncrementalIndexTask] = MappingProxyType(
    {task.id: task for task in _INDEX_TASKS}
)
INDEX_TASK_UNIT_ALLOWLIST: Mapping[str, str] = MappingProxyType(
    {task.id: task.unit for task in _INDEX_TASKS}
)

for _task in _INDEX_TASKS:
    if _task.unit not in UNIT_ALLOWLIST:  # fail closed if the control plane drifts
        raise RuntimeError(f"incremental index unit is not allowlisted: {_task.unit}")


def incremental_index_task(task_id: str) -> dict[str, Any]:
    """Return a serializable exact task descriptor or reject an unknown ID."""

    task = INCREMENTAL_INDEX_TASKS.get(task_id)
    if task is None:
        raise KeyError(task_id)
    return asdict(task)


def incremental_index_tasks() -> list[dict[str, Any]]:
    return [asdict(task) for task in _INDEX_TASKS]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _public_id(value: object) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, (bytes, bytearray)) or len(value) != 16:
        return ""
    return base64.urlsafe_b64encode(bytes(value)).decode("ascii").rstrip("=")


def _json_list(value: object) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    return value if isinstance(value, list) else []


def _json_dict(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return value if isinstance(value, dict) else {}


def _json_file(path: Path, default: Mapping[str, Any]) -> dict[str, Any]:
    """Read one known, bounded, regular JSON status file without following links."""

    try:
        stat = path.lstat()
        if path.is_symlink() or not path.is_file() or stat.st_size > 2 * 1024 * 1024:
            return dict(default)
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default)
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return dict(default)


def hot_cache_from_settings(settings: Settings) -> RedisHotCache:
    return RedisHotCache(
        LibraryCacheSettings(
            enabled=settings.library_cache_redis_enabled,
            host=settings.library_cache_redis_host,
            port=settings.library_cache_redis_port,
            db=settings.library_cache_redis_db,
            password=settings.library_cache_redis_password,
            prefix=settings.library_cache_redis_prefix,
            connect_timeout_seconds=settings.library_cache_redis_connect_timeout,
            socket_timeout_seconds=settings.library_cache_redis_socket_timeout,
            max_payload_bytes=settings.library_cache_redis_max_payload_bytes,
        )
    )


class LibraryCatalogDatabase:
    """Minimal read-only MySQL access for catalog cards and browsing."""

    def __init__(
        self,
        settings: Settings,
        connector: Callable[..., Any] | None = None,
        *,
        cache: RedisHotCache | None = None,
    ):
        self.settings = settings
        self._connector = connector
        self.cache = cache or hot_cache_from_settings(settings)
        self._object_store = NasObjectStore(settings.library_object_root)

    @staticmethod
    def _image_media_type(path: Path) -> str:
        """Return a safe image MIME after checking size and file signature."""

        try:
            stat = path.stat()
            if not path.is_file() or not 0 < stat.st_size <= 25 * 1024 * 1024:
                return ""
            with path.open("rb") as reader:
                header = reader.read(16)
        except OSError:
            return ""
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return "image/webp"
        return ""

    def _cover_file_from_key(self, object_key: object) -> dict[str, Any] | None:
        """Resolve one catalog cover below the configured object root only."""

        key = str(object_key or "").strip()
        if not key:
            return None
        try:
            path = self._object_store.resolve(key)
            if (
                not path.is_file()
                and Path(key).name == key
                and key not in {".", ".."}
            ):
                path = self._object_store.resolve(f"封面/{key}")
            media_type = self._image_media_type(path)
            if not media_type:
                return None
            stat = path.stat()
        except (OSError, ValueError):
            return None
        return {
            "path": path,
            "media_type": media_type,
            "size": stat.st_size,
            "version": str(stat.st_mtime_ns)[:20],
        }

    def cover_file(self, catalog_id: int) -> dict[str, Any]:
        """Return a verified local cover descriptor without exposing its key."""

        if catalog_id < 1:
            raise KeyError(catalog_id)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                row = self._one(
                    cursor,
                    "SELECT cover_object_key FROM books WHERE id=%s "
                    "AND status <> 'duplicate' AND is_active=1 LIMIT 1",
                    (catalog_id,),
                )
            connection.rollback()
        finally:
            connection.close()
        object_key = str((row or {}).get("cover_object_key") or "").strip()
        cover = self._cover_file_from_key(object_key) if object_key else None
        if cover is None:
            if not row:
                raise KeyError(catalog_id)
            if object_key:
                raise KeyError(catalog_id)
            path = default_cover_template_path()
            if not path.is_file() or self._image_media_type(path) != "image/jpeg":
                raise KeyError(catalog_id)
            cover = {
                "path": path,
                "media_type": "image/jpeg",
                "size": path.stat().st_size,
                "version": OOHSTORY_DEFAULT_COVER_SHA256[:16],
                "is_default": True,
            }
        return cover

    def _shape_catalog_item(self, item: dict[str, Any]) -> None:
        item["catalog_id"] = _int(item.get("catalog_id"))
        item["public_id"] = _public_id(item.get("public_id"))
        item["source_bytes"] = _int(item.get("source_bytes"))
        item["body_available"] = bool(item.get("body_available"))
        item["readable"] = item["body_available"]
        item["available_for_reading"] = item["body_available"]
        raw_book_status = str(item.get("book_status") or "").strip().casefold()
        if any(marker in raw_book_status for marker in ("完结", "完本", "finished", "completed")):
            item["book_status_label"] = "已完结"
        elif any(marker in raw_book_status for marker in ("连载", "更新", "ongoing", "serial")):
            item["book_status_label"] = "连载中"
        elif item["body_available"]:
            item["book_status_label"] = "已收录"
        else:
            item["book_status_label"] = "待恢复"

        exact_chapters = _int(item.get("chapter_count"))
        approximate_chapters = _int(item.get("approx_chapter_count"))
        reader_source_bytes = _int(item.get("reader_source_bytes"))
        exact_is_current = exact_chapters > 0 and (
            reader_source_bytes <= 0 or reader_source_bytes == item["source_bytes"]
        )
        if exact_is_current:
            item["chapter_count"] = exact_chapters
            item["chapter_count_source"] = "reader_index"
        elif approximate_chapters > 0:
            item["chapter_count"] = approximate_chapters
            item["chapter_count_source"] = "catalog_estimate"
        else:
            item["chapter_count"] = 0
            item["chapter_count_source"] = "unavailable"
        item["chapter_count_known"] = item["chapter_count"] > 0
        item["chapter_count_label"] = (
            f"{item['chapter_count']:,} 章"
            if item["chapter_count_known"]
            else "未识别"
        )

        exact_words = _int(item.get("word_count"))
        approximate_words = _int(item.get("approx_word_count"))
        item["word_count"] = (
            exact_words if exact_is_current and exact_words > 0 else approximate_words or exact_words
        )
        item["word_count_known"] = item["word_count"] > 0
        item["section_count"] = _int(item.get("section_count"))
        item["segment_count"] = _int(item.get("segment_count"))
        item["tone_indexed"] = bool(item.get("indexed_at"))
        item["plot_indexed"] = bool(item.get("plot_indexed_at"))

        cover = self._cover_file_from_key(item.get("cover_object_key"))
        item["cover_available"] = True
        item["cover_is_default"] = cover is None
        item["cover_url"] = (
            f"/api/admin/books/catalog/{item['catalog_id']}/cover?v={cover['version']}"
            if cover is not None and item["catalog_id"]
            else (
                "/api/admin/library/default-cover?v="
                f"{OOHSTORY_DEFAULT_COVER_SHA256[:16]}"
            )
        )
        item.pop("cover_object_key", None)
        item.pop("body_object_key", None)
        item.pop("reader_source_bytes", None)

        for tag_key in (
            "genre_tags", "tone_tags", "primary_tone_tags", "secondary_tone_tags"
        ):
            item[tag_key] = _json_list(item.get(tag_key))
        item["tone_evidence"] = _json_dict(item.get("tone_evidence"))
        if item.get("updated_at") is not None:
            item["updated_at"] = str(item["updated_at"])
        item["updated_label"] = str(item.get("updated_at") or "")[:10] or "未知"

    def _connect(self):
        if self._connector is None:
            import pymysql
            from pymysql.cursors import DictCursor

            connector = pymysql.connect
            cursorclass = DictCursor
        else:
            connector = self._connector
            cursorclass = None
        options: dict[str, Any] = {
            "host": self.settings.library_mysql_host,
            "port": self.settings.library_mysql_port,
            "user": self.settings.library_mysql_user,
            "password": self.settings.library_mysql_password,
            "database": self.settings.library_mysql_database,
            "charset": "utf8mb4",
            "autocommit": False,
            "connect_timeout": 4,
            "read_timeout": 8,
            "write_timeout": 8,
        }
        if cursorclass is not None:
            options["cursorclass"] = cursorclass
        return connector(**options)

    @staticmethod
    def _all(cursor: Any, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _one(cursor: Any, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        cursor.execute(query, params)
        return dict(cursor.fetchone() or {})

    def overview_snapshot(self) -> dict[str, Any]:
        """Read all card counters in one explicit read-only transaction."""

        cache_query = {"database": self.settings.library_mysql_database}
        cached = self.cache.get_json("catalog", "admin-overview", cache_query)
        if cached is not None:
            cached["cache"] = "redis"
            return cached

        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                status_rows = self._all(
                    cursor,
                    "SELECT library_id,status,book_count AS count "
                    "FROM catalog_status_counts",
                )
                categories = self._all(
                    cursor,
                    "SELECT category AS name,SUM(book_count) AS count "
                    "FROM catalog_facets WHERE body_available=1 "
                    "GROUP BY category ORDER BY count DESC",
                )
                readable_rows = self._all(
                    cursor,
                    "SELECT library_id,SUM(book_count) AS count "
                    "FROM catalog_facets WHERE body_available=1 GROUP BY library_id",
                )
                assets = self._one(
                    cursor,
                    "SELECT "
                    "(SELECT COUNT(*) FROM book_metadata) AS tone_books,"
                    "(SELECT COUNT(*) FROM books "
                    " WHERE is_active=1 AND body_available=1) AS indexable_books",
                )
                deduplicated = self._one(
                    cursor,
                    "SELECT COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(state_value,'$.count')) "
                    "AS UNSIGNED),0) AS count FROM crawl_state "
                    "WHERE source_name='authorized-catalog' "
                    "AND state_key='deduplicated-total'",
                )
            connection.rollback()
        finally:
            connection.close()

        result = self._shape_overview(
            status_rows, categories, readable_rows, assets, deduplicated
        )
        result["cache"] = "mysql"
        self.cache.set_json(
            "catalog", "admin-overview", cache_query, result, ttl_seconds=60
        )
        return result

    @staticmethod
    def _shape_overview(
        status_rows: list[dict[str, Any]],
        categories: list[dict[str, Any]],
        readable_rows: list[dict[str, Any]],
        assets: dict[str, Any],
        deduplicated: dict[str, Any],
    ) -> dict[str, Any]:
        libraries = {
            key: {
                **dict(metadata),
                "total": 0,
                "readable": 0,
                "downloaded": 0,
                "pending": 0,
                "failed": 0,
                "recovery": 0,
            }
            for key, metadata in CATALOG_LIBRARIES.items()
        }
        by_status: dict[str, int] = {}
        stored_duplicates = 0
        for row in status_rows:
            library_id = str(row.get("library_id") or "")
            status = str(row.get("status") or "")
            count = _int(row.get("count"))
            by_status[status] = by_status.get(status, 0) + count
            if status == "duplicate":
                stored_duplicates += count
                continue
            library = libraries.get(library_id)
            if library is None:
                continue
            library["total"] += count
            if status == "done":
                library["downloaded"] += count
            elif status in {"discovered", "downloading"}:
                library["pending"] += count
            elif status == "failed":
                library["failed"] += count
        for row in readable_rows:
            library = libraries.get(str(row.get("library_id") or ""))
            if library is not None:
                library["readable"] = _int(row.get("count"))
        for library in libraries.values():
            library["recovery"] = max(library["total"] - library["readable"], 0)

        total = sum(item["total"] for item in libraries.values())
        readable = sum(item["readable"] for item in libraries.values())
        tone_books = _int(assets.get("tone_books"))
        indexable_books = _int(assets.get("indexable_books"))
        authorized_duplicates = _int(deduplicated.get("count"))
        return {
            "books": {
                "total": total,
                "raw_total": total + stored_duplicates,
                "readable": readable,
                "recovery": max(total - readable, 0),
                "duplicates": stored_duplicates,
                "intercepted_duplicates": stored_duplicates + authorized_duplicates,
                "by_status": by_status,
                "libraries": libraries,
            },
            "tone_index": {
                "count": tone_books,
                "indexable": indexable_books,
                "pending": max(indexable_books - tone_books, 0),
                "synchronized": tone_books == indexable_books,
                "global_shared": True,
                "source_catalog_read_only": True,
            },
            "categories": [
                {"name": str(row.get("name") or "未分类"), "count": _int(row.get("count"))}
                for row in categories
            ],
        }

    def browse(
        self,
        *,
        library: str = "all",
        availability: str = "all",
        query: str = "",
        category: str = "",
        page: int = 1,
        page_size: int = 28,
        index_kind: str = "",
        tag: str = "",
        include_ids: tuple[int, ...] = (),
        exclude_ids: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        """Browse the catalog with a fixed SQL shape and parameterized values."""

        if library not in CATALOG_FILTERS:
            raise ValueError("unknown library filter")
        if availability not in AVAILABILITY_FILTERS:
            raise ValueError("unknown availability filter")
        if not 1 <= page:
            raise ValueError("page must be positive")
        if not 1 <= page_size <= 60:
            raise ValueError("page_size must be between 1 and 60")
        if index_kind not in {"", "tone", "plot"}:
            raise ValueError("unknown index filter")
        query = " ".join(query.replace("\x00", "").split())[:100]
        category = " ".join(category.replace("\x00", "").split())[:100]
        tag = " ".join(tag.replace("\x00", "").split())[:120]
        include_ids = tuple(dict.fromkeys(value for value in include_ids if value > 0))
        exclude_ids = tuple(dict.fromkeys(value for value in exclude_ids if value > 0))
        if len(include_ids) > 2_000 or len(exclude_ids) > 2_000:
            raise ValueError("catalog id filter is too large")
        cache_query = {
            "card_schema": 2,
            "database": self.settings.library_mysql_database,
            "library": library,
            "availability": availability,
            "query": query,
            "category": category,
            "page": page,
            "page_size": page_size,
            "index_kind": index_kind,
            "tag": tag,
            "include_ids": include_ids,
            "exclude_ids": exclude_ids,
        }
        cache_scope = "tone" if index_kind == "tone" else "plot" if index_kind == "plot" else "catalog"
        cached = self.cache.get_json(cache_scope, "admin-browse", cache_query)
        if cached is not None:
            cached["cache"] = "redis"
            return cached

        conditions = ["b.status <> 'duplicate'", "b.is_active=1"]
        params: list[Any] = []
        if library != "all":
            conditions.append("b.library_id=%s")
            params.append(library)
        if availability == "readable":
            conditions.append("b.body_available=1")
        elif availability == "recovery":
            conditions.append("b.body_available=0")
        if index_kind == "tone":
            conditions.append("m.catalog_id IS NOT NULL")
        elif index_kind == "plot":
            conditions.append("p.catalog_id IS NOT NULL")
        if include_ids:
            conditions.append(f"b.id IN ({','.join('%s' for _ in include_ids)})")
            params.extend(include_ids)
        if exclude_ids:
            conditions.append(f"b.id NOT IN ({','.join('%s' for _ in exclude_ids)})")
            params.extend(exclude_ids)
        category_conditions = list(conditions)
        category_params = list(params)
        if query:
            conditions.append("(b.title LIKE %s OR b.author LIKE %s)")
            like = f"%{query}%"
            params.extend((like, like))
        if category:
            conditions.append("b.category=%s")
            params.append(category)
        if tag:
            if index_kind != "tone":
                raise ValueError("tags are available only for the tone index")
            conditions.append(
                "(JSON_CONTAINS(COALESCE(m.genre_tags,JSON_ARRAY()),JSON_QUOTE(%s)) "
                "OR JSON_CONTAINS(COALESCE(m.tone_tags,JSON_ARRAY()),JSON_QUOTE(%s)) "
                "OR JSON_CONTAINS(COALESCE(m.primary_tone_tags,JSON_ARRAY()),JSON_QUOTE(%s)) "
                "OR JSON_CONTAINS(COALESCE(m.secondary_tone_tags,JSON_ARRAY()),JSON_QUOTE(%s)))"
            )
            params.extend((tag, tag, tag, tag))
        where = " AND ".join(conditions)
        category_where = " AND ".join(category_conditions)
        item_joins = (
            "LEFT JOIN book_metadata m ON m.catalog_id=b.id "
            "LEFT JOIN plot_index_meta p ON p.catalog_id=b.id "
            "LEFT JOIN book_public_ids bp ON bp.catalog_id=b.id"
        )
        count_joins = (
            "LEFT JOIN book_metadata m ON m.catalog_id=b.id"
            if index_kind == "tone"
            else "LEFT JOIN plot_index_meta p ON p.catalog_id=b.id"
            if index_kind == "plot"
            else ""
        )

        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                total_row = self._one(
                    cursor,
                    f"SELECT COUNT(*) AS total FROM books b {count_joins} WHERE {where}",
                    tuple(params),
                )
                select_columns = (
                    "b.id AS catalog_id,b.source_id,b.title,b.author,b.category,"
                    "b.library_id,b.status AS download_status,b.body_available,"
                    "b.bytes AS source_bytes,b.book_status,b.cover_object_key,"
                    "b.body_object_key,b.last_error,b.approx_word_count,"
                    "b.approx_chapter_count,b.mysql_updated_at AS updated_at,"
                    "bp.public_id,m.summary,m.word_count,m.chapter_count,"
                    "m.reader_source_bytes,m.reader_index_status,m.reader_indexed_at,"
                    "m.section_count,m.genre_tags,m.tone_tags,m.primary_tone_tags,"
                    "m.secondary_tone_tags,m.tone_confidence,m.tone_source,"
                    "m.tone_evidence,m.tone_review_status,m.tone_review_model,"
                    "m.indexed_at,p.segment_count,"
                    "p.indexed_at AS plot_indexed_at"
                )
                if index_kind:
                    items = self._all(
                        cursor,
                        f"SELECT {select_columns} FROM books b {item_joins} "
                        f"WHERE {where} ORDER BY b.id DESC LIMIT %s OFFSET %s",
                        (*params, page_size, (page - 1) * page_size),
                    )
                else:
                    page_columns = (
                        "b.id,b.source_id,b.title,b.author,b.category,b.library_id,"
                        "b.status,b.body_available,b.bytes,b.book_status,b.cover_object_key,"
                        "b.body_object_key,b.last_error,b.approx_word_count,"
                        "b.approx_chapter_count,b.mysql_updated_at"
                    )
                    items = self._all(
                        cursor,
                        f"SELECT {select_columns} FROM (SELECT {page_columns} FROM books b "
                        f"WHERE {where} ORDER BY b.id DESC LIMIT %s OFFSET %s) b "
                        f"{item_joins} ORDER BY b.id DESC",
                        (*params, page_size, (page - 1) * page_size),
                    )
                if not query and not index_kind and not include_ids and not exclude_ids:
                    facet_conditions: list[str] = []
                    facet_params: list[Any] = []
                    if library != "all":
                        facet_conditions.append("library_id=%s")
                        facet_params.append(library)
                    if availability == "readable":
                        facet_conditions.append("body_available=1")
                    elif availability == "recovery":
                        facet_conditions.append("body_available=0")
                    facet_where = (
                        " WHERE " + " AND ".join(facet_conditions)
                        if facet_conditions
                        else ""
                    )
                    categories = self._all(
                        cursor,
                        "SELECT category,SUM(book_count) AS count FROM catalog_facets"
                        f"{facet_where} GROUP BY category ORDER BY count DESC,category",
                        tuple(facet_params),
                    )
                else:
                    categories = self._all(
                        cursor,
                        "SELECT COALESCE(NULLIF(b.category,''),'未分类') AS category,"
                        "COUNT(*) AS count "
                        f"FROM books b {count_joins} WHERE {category_where} "
                        "GROUP BY category ORDER BY count DESC,category",
                        tuple(category_params),
                    )
                evidence_total = 0
                if index_kind == "plot":
                    evidence_total = _int(
                        self._one(
                            cursor,
                            f"SELECT COALESCE(SUM(p.segment_count),0) AS total "
                            f"FROM books b {count_joins} WHERE {where}",
                            tuple(params),
                        ).get("total")
                    )
                tags = self._tone_tag_stats(cursor, library) if index_kind == "tone" else []
            connection.rollback()
        finally:
            connection.close()

        for item in items:
            self._shape_catalog_item(item)
        total = _int(total_row.get("total"))
        page_count = max(1, (total + page_size - 1) // page_size)
        result = {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "page_count": page_count,
            "range_start": (page - 1) * page_size + 1 if total else 0,
            "range_end": min(page * page_size, total),
            "filters": {
                "library": library,
                "availability": availability,
                "query": query,
                "category": category,
                "tag": tag,
            },
            "categories": [
                {"category": str(row.get("category") or "未分类"), "count": _int(row.get("count"))}
                for row in categories
            ],
            "tags": tags,
            "evidence_total": evidence_total,
            "source_read_only": True,
            "cache": "mysql",
        }
        self.cache.set_json(
            cache_scope,
            "admin-browse",
            cache_query,
            result,
            ttl_seconds=300 if index_kind else 60,
        )
        return result

    @staticmethod
    def _tone_tag_stats(cursor: Any, library: str) -> list[dict[str, Any]]:
        scope = "b.is_active=1"
        params: list[Any] = []
        if library != "all":
            scope += " AND b.library_id=%s"
            params.append(library)
        cursor.execute(
            f"""
            SELECT tag_name AS name,COUNT(DISTINCT catalog_id) AS count
            FROM (
              SELECT m.catalog_id,j.tag_name
              FROM book_metadata m JOIN books b ON b.id=m.catalog_id
              JOIN JSON_TABLE(
                JSON_MERGE_PRESERVE(
                  COALESCE(m.genre_tags,JSON_ARRAY()),
                  COALESCE(m.tone_tags,JSON_ARRAY()),
                  COALESCE(m.primary_tone_tags,JSON_ARRAY()),
                  COALESCE(m.secondary_tone_tags,JSON_ARRAY())
                ), '$[*]' COLUMNS(tag_name VARCHAR(120) PATH '$')
              ) j WHERE {scope}
            ) tags WHERE tag_name<>''
            GROUP BY tag_name ORDER BY count DESC,name LIMIT 100
            """,
            tuple(params),
        )
        return [
            {"name": str(row.get("name") or ""), "count": _int(row.get("count"))}
            for row in cursor.fetchall()
            if str(row.get("name") or "")
        ]

    def book(self, catalog_id: int) -> dict[str, Any]:
        if catalog_id < 1:
            raise KeyError(catalog_id)
        cache_query = {"catalog_id": int(catalog_id)}
        cached = self.cache.get_json("book", "admin-detail", cache_query)
        if cached is not None:
            if not cached.get("found"):
                raise KeyError(catalog_id)
            return dict(cached["value"])
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                row = self._one(
                    cursor,
                    "SELECT b.id AS catalog_id,b.source_id,b.title,b.author,b.category,"
                    "b.library_id,b.status AS download_status,b.body_available,"
                    "b.bytes AS source_bytes,b.book_status,b.last_error,b.cover_object_key,"
                    "b.approx_word_count,b.approx_chapter_count,"
                    "b.mysql_updated_at AS updated_at,bp.public_id,"
                    "m.summary,m.word_count,m.chapter_count,m.reader_source_bytes,"
                    "m.reader_index_status,m.reader_indexed_at,m.section_count,"
                    "m.genre_tags,m.tone_tags,m.primary_tone_tags,m.secondary_tone_tags,"
                    "m.tone_confidence,m.tone_source,m.tone_evidence,"
                    "m.tone_review_status,m.tone_review_model,m.indexed_at,"
                    "p.segment_count,p.indexed_at AS plot_indexed_at "
                    "FROM books b LEFT JOIN book_metadata m ON m.catalog_id=b.id "
                    "LEFT JOIN plot_index_meta p ON p.catalog_id=b.id "
                    "LEFT JOIN book_public_ids bp ON bp.catalog_id=b.id "
                    "WHERE b.id=%s AND b.status <> 'duplicate' AND b.is_active=1",
                    (catalog_id,),
                )
            connection.rollback()
        finally:
            connection.close()
        if not row:
            self.cache.set_json(
                "book",
                "admin-detail",
                cache_query,
                {"found": False, "value": None},
                ttl_seconds=30,
            )
            raise KeyError(catalog_id)
        self._shape_catalog_item(row)
        self.cache.set_json(
            "book",
            "admin-detail",
            cache_query,
            {"found": True, "value": row},
            ttl_seconds=300,
        )
        return row

    def plot_evidence(self, catalog_id: int, *, page: int = 1, page_size: int = 8) -> dict[str, Any]:
        if catalog_id < 1:
            raise KeyError(catalog_id)
        if page < 1 or not 1 <= page_size <= 20:
            raise ValueError("invalid evidence pagination")
        cache_query = {
            "catalog_id": int(catalog_id),
            "page": int(page),
            "page_size": int(page_size),
        }
        cached = self.cache.get_json("plot", "admin-evidence", cache_query)
        if cached is not None:
            cached["cache"] = "redis"
            return cached
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                book = self._one(
                    cursor,
                    "SELECT p.catalog_id,p.source_id,p.segment_count,p.indexed_at,"
                    "b.title,b.author,b.category FROM plot_index_meta p "
                    "JOIN books b ON b.id=p.catalog_id WHERE p.catalog_id=%s "
                    "AND b.is_active=1 LIMIT 1",
                    (catalog_id,),
                )
                if not book:
                    raise KeyError(catalog_id)
                rows = self._all(
                    cursor,
                    "SELECT id,location,motif_tags,content FROM plot_segments "
                    "WHERE catalog_id=%s ORDER BY id LIMIT %s OFFSET %s",
                    (catalog_id, page_size, (page - 1) * page_size),
                )
            connection.rollback()
        finally:
            connection.close()
        for row in rows:
            row["id"] = _int(row.get("id"))
            row["motif_tags"] = _json_list(row.get("motif_tags"))
            row["content"] = str(row.get("content") or "")[:12_000]
        total = _int(book.get("segment_count"))
        result = {
            "book": {**book, "catalog_id": _int(book.get("catalog_id")), "segment_count": total},
            "items": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "page_count": max(1, (total + page_size - 1) // page_size),
            "source_read_only": True,
            "cache": "mysql",
        }
        self.cache.set_json(
            "plot", "admin-evidence", cache_query, result, ttl_seconds=180
        )
        return result

    def resolve_deconstruction_records(
        self, records: list[dict[str, Any]]
    ) -> dict[int, dict[str, Any]]:
        """Resolve the bounded task/artifact set onto current MySQL catalog IDs."""

        records = records[:2_000]
        titles = sorted({str(item.get("title") or "").strip() for item in records if item.get("title")})
        ids = sorted({_int(item.get("book_id")) for item in records if _int(item.get("book_id"))})
        if not titles and not ids:
            return {}
        conditions: list[str] = []
        params: list[Any] = []
        if titles:
            conditions.append(f"b.title IN ({','.join('%s' for _ in titles)})")
            params.extend(titles)
        if ids:
            conditions.append(f"b.id IN ({','.join('%s' for _ in ids)})")
            params.extend(ids)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                rows = self._all(
                    cursor,
                    "SELECT b.id AS catalog_id,b.title,b.author FROM books b "
                    "WHERE b.is_active=1 AND b.status<>'duplicate' AND ("
                    + " OR ".join(conditions)
                    + ")",
                    tuple(params),
                )
            connection.rollback()
        finally:
            connection.close()
        by_title = {str(item.get("title") or "").casefold(): item for item in records}
        by_id = {_int(item.get("book_id")): item for item in records if _int(item.get("book_id"))}
        resolved: dict[int, dict[str, Any]] = {}
        for row in rows:
            catalog_id = _int(row.get("catalog_id"))
            record = by_id.get(catalog_id) or by_title.get(str(row.get("title") or "").casefold())
            if record:
                resolved[catalog_id] = dict(record)
        return resolved

    def active_counts(self) -> dict[str, int]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                row = self._one(
                    cursor,
                    "SELECT COUNT(*) AS total,COALESCE(SUM(body_available=1),0) AS readable "
                    "FROM books WHERE is_active=1 AND status<>'duplicate'",
                )
            connection.rollback()
        finally:
            connection.close()
        return {"total": _int(row.get("total")), "readable": _int(row.get("readable"))}


class GlobalDeconstructionReader:
    """Bounded, read-only projection of the shared global deconstruction root."""

    def __init__(
        self,
        root: Path,
        *,
        max_artifacts: int = 10_000,
        max_tasks: int = 2_000,
        cache: RedisHotCache | None = None,
    ):
        self.root = root
        self.max_artifacts = max_artifacts
        self.max_tasks = max_tasks
        self.cache = cache

    @staticmethod
    def _artifact_flags(path: Path) -> tuple[bool, bool]:
        quick = (path / "快速预览.md").is_file()
        full = any(
            (path / name).is_file()
            for name in ("拆文报告.md", "完整拆文报告.md", "全量拆文报告.md")
        )
        return quick or full, full

    def snapshot(self, *, item_limit: int = 100) -> dict[str, Any]:
        item_limit = max(0, min(item_limit, 500))
        cache_query = {"root": str(self.root), "item_limit": item_limit}
        if self.cache is not None:
            cached = self.cache.get_json(
                "deconstruction", "manifest-snapshot", cache_query
            )
            if cached is not None:
                cached["cache"] = "redis"
                return cached
        artifact_items: list[dict[str, Any]] = []
        artifact_names: set[str] = set()
        artifact_total = 0
        completed_artifacts = 0
        scan_artifacts = 0
        truncated = False
        try:
            with os.scandir(self.root) as entries:
                for entry in entries:
                    if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                        continue
                    if artifact_total >= self.max_artifacts:
                        truncated = True
                        break
                    artifact_total += 1
                    artifact_names.add(entry.name)
                    path = Path(entry.path)
                    quick, full = self._artifact_flags(path)
                    completed_artifacts += int(full)
                    scan_artifacts += int(quick)
                    if len(artifact_items) < item_limit:
                        artifact_items.append(
                            {
                                "id": entry.name,
                                "title": entry.name.rsplit("__", 1)[0],
                                "output_dir": str(path),
                                "status": "completed" if full else "scan" if quick else "discovered",
                                "has_quick_preview": quick,
                                "has_full_report": full,
                            }
                        )
        except OSError:
            pass

        task_counts: dict[str, int] = {}
        extra_task_keys: set[str] = set()
        task_root = self.root / ".tasks"
        scanned_tasks = 0
        try:
            with os.scandir(task_root) as entries:
                for entry in entries:
                    if scanned_tasks >= self.max_tasks:
                        truncated = True
                        break
                    if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(".json"):
                        continue
                    scanned_tasks += 1
                    value = _json_file(Path(entry.path), {})
                    status = str(value.get("status") or "unknown")
                    task_counts[status] = task_counts.get(status, 0) + 1
                    output_name = Path(str(value.get("output_dir") or "")).name
                    extra_task_keys.add(output_name if output_name else f"task:{entry.name}")
        except OSError:
            pass

        all_keys = artifact_names | extra_task_keys
        result = {
            "root": str(self.root),
            "total": len(all_keys),
            "artifact_total": artifact_total,
            "running": task_counts.get("queued", 0) + task_counts.get("running", 0),
            "completed": max(completed_artifacts, task_counts.get("completed", 0)),
            "scan_completed": scan_artifacts,
            "task_counts": task_counts,
            "items": artifact_items,
            "item_limit": item_limit,
            "truncated": truncated or artifact_total > item_limit,
            "source_read_only": True,
            "cache": "nas",
        }
        if self.cache is not None:
            self.cache.set_json(
                "deconstruction",
                "manifest-snapshot",
                cache_query,
                result,
                ttl_seconds=30,
            )
        return result

    @staticmethod
    def _state(item: Mapping[str, Any]) -> str:
        status = str(item.get("status") or "").lower()
        if status in {"queued", "running"}:
            return "running"
        if item.get("has_full_report") or status == "completed":
            return "full"
        if item.get("has_quick_preview"):
            return "scan"
        if status in {"error", "failed", "paused", "cancelled"} or item.get("can_resume"):
            return "error"
        return "unstarted"

    def records(self) -> list[dict[str, Any]]:
        """Return one bounded, merged status record per known book title."""

        cache_query = {"root": str(self.root), "limit": self.max_artifacts}
        if self.cache is not None:
            cached = self.cache.get_json(
                "deconstruction",
                "manifest-records",
                cache_query,
                expected_type=list,
            )
            if cached is not None:
                return cached

        by_title: dict[str, dict[str, Any]] = {}
        try:
            with os.scandir(self.root) as entries:
                scanned = 0
                for entry in entries:
                    if scanned >= self.max_artifacts:
                        break
                    if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                        continue
                    scanned += 1
                    title = entry.name.rsplit("__", 1)[0].strip()
                    if not title:
                        continue
                    quick, full = self._artifact_flags(Path(entry.path))
                    by_title[title.casefold()] = {
                        "id": entry.name,
                        "title": title,
                        "author": "",
                        "output_dir": str(Path(entry.path)),
                        "status": "completed" if full else "scan" if quick else "discovered",
                        "has_quick_preview": quick,
                        "has_full_report": full,
                        "progress": 100 if full else 35 if quick else 0,
                    }
        except OSError:
            pass

        task_root = self.root / ".tasks"
        try:
            with os.scandir(task_root) as entries:
                scanned = 0
                for entry in entries:
                    if scanned >= self.max_tasks:
                        break
                    if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(".json"):
                        continue
                    scanned += 1
                    task = _json_file(Path(entry.path), {})
                    title = str(task.get("title") or "").strip()
                    if not title:
                        title = Path(str(task.get("output_dir") or "")).name.rsplit("__", 1)[0].strip()
                    if not title:
                        continue
                    key = title.casefold()
                    current = dict(by_title.get(key) or {})
                    current_updated = str(current.get("updated_at") or "")
                    task_updated = str(task.get("updated_at") or task.get("created_at") or "")
                    if not current or task_updated >= current_updated:
                        preserved = {
                            "has_quick_preview": bool(current.get("has_quick_preview")),
                            "has_full_report": bool(current.get("has_full_report")),
                        }
                        current.update(
                            {
                                "id": str(task.get("id") or entry.name[:-5]),
                                "title": title,
                                "author": str(task.get("author") or ""),
                                "book_id": _int(task.get("book_id") or task.get("catalog_id")),
                                "output_dir": str(task.get("output_dir") or current.get("output_dir") or ""),
                                "status": str(task.get("status") or "unknown"),
                                "progress": _int(task.get("progress")),
                                "current_stage": str(task.get("current_stage") or ""),
                                "message": str(task.get("message") or ""),
                                "can_resume": bool(task.get("can_resume")),
                                "artifact_level": str(task.get("artifact_level") or ""),
                                "updated_at": task_updated,
                            }
                        )
                        current["has_quick_preview"] = preserved["has_quick_preview"] or bool(
                            task.get("has_quick_preview")
                        )
                        current["has_full_report"] = preserved["has_full_report"] or bool(
                            task.get("has_full_report")
                        )
                    by_title[key] = current
        except OSError:
            pass

        records = []
        for item in by_title.values():
            item = dict(item)
            item["state"] = self._state(item)
            records.append(item)
        records.sort(key=lambda item: (str(item.get("updated_at") or ""), item["title"]), reverse=True)
        if self.cache is not None:
            self.cache.set_json(
                "deconstruction",
                "manifest-records",
                cache_query,
                records,
                ttl_seconds=30,
            )
        return records

    def batches(self, *, limit: int = 30) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        try:
            with os.scandir(self.root / ".batches") as entries:
                for entry in entries:
                    if len(items) >= min(max(limit, 0), 100):
                        break
                    if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(".json"):
                        continue
                    value = _json_file(Path(entry.path), {})
                    if value:
                        items.append(
                            {
                                key: value.get(key)
                                for key in (
                                    "id", "mode", "status", "total", "finished", "failed",
                                    "current_stage", "message", "updated_at"
                                )
                            }
                        )
        except OSError:
            pass
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return items

    def browse(
        self,
        *,
        query: str = "",
        state: str = "all",
        page: int = 1,
        page_size: int = 24,
    ) -> dict[str, Any]:
        """Return a bounded page of artifact directories without deep traversal."""

        if state not in {"all", "unstarted", "scan", "full"}:
            raise ValueError("unknown deconstruction state")
        if page < 1 or not 1 <= page_size <= 60:
            raise ValueError("invalid pagination")
        query = " ".join(query.replace("\x00", "").split())[:100].casefold()
        cache_query = {
            "root": str(self.root),
            "query": query,
            "state": state,
            "page": page,
            "page_size": page_size,
        }
        if self.cache is not None:
            cached = self.cache.get_json(
                "deconstruction", "manifest-browse", cache_query
            )
            if cached is not None:
                cached["cache"] = "redis"
                return cached
        matches: list[dict[str, Any]] = []
        truncated = False
        try:
            with os.scandir(self.root) as entries:
                scanned = 0
                for entry in entries:
                    if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                        continue
                    if scanned >= self.max_artifacts:
                        truncated = True
                        break
                    scanned += 1
                    title = entry.name.rsplit("__", 1)[0]
                    if query and query not in title.casefold() and query not in entry.name.casefold():
                        continue
                    path = Path(entry.path)
                    quick, full = self._artifact_flags(path)
                    current_state = "full" if full else "scan" if quick else "unstarted"
                    if state != "all" and state != current_state:
                        continue
                    matches.append(
                        {
                            "id": entry.name,
                            "title": title,
                            "output_dir": str(path),
                            "state": current_state,
                            "has_quick_preview": quick,
                            "has_full_report": full,
                        }
                    )
        except OSError:
            pass
        matches.sort(key=lambda item: item["id"].casefold())
        total = len(matches)
        offset = (page - 1) * page_size
        result = {
            "items": matches[offset : offset + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "page_count": max(1, (total + page_size - 1) // page_size),
            "query": query,
            "state": state,
            "truncated": truncated,
            "source_read_only": True,
            "cache": "nas",
        }
        if self.cache is not None:
            self.cache.set_json(
                "deconstruction",
                "manifest-browse",
                cache_query,
                result,
                ttl_seconds=30,
            )
        return result


class LibraryCatalogFacade:
    """FastAPI-ready facade joining MySQL counters with known status files."""

    def __init__(
        self,
        settings: Settings,
        *,
        database: LibraryCatalogDatabase | Any | None = None,
        deconstructions: GlobalDeconstructionReader | Any | None = None,
        cache: RedisHotCache | None = None,
    ):
        self.settings = settings
        self.cache = cache or hot_cache_from_settings(settings)
        self.database = database or LibraryCatalogDatabase(
            settings, cache=self.cache
        )
        self.deconstructions = deconstructions or GlobalDeconstructionReader(
            settings.library_root / "全局拆书库", cache=self.cache
        )

    def overview(self) -> dict[str, Any]:
        result = self.database.overview_snapshot()
        index = _json_file(
            self.settings.library_runtime_dir / "electronic_library_index_status.json",
            {"status": "idle", "running": False, "message": "尚未建立共享基调索引"},
        )
        ingestion = _json_file(
            self.settings.library_runtime_dir / "electronic_library_ingestion_index_status.json",
            {"status": "idle", "running": False, "message": "没有待处理的新书轻量索引"},
        )
        result["tone_index"] = {**result["tone_index"], **index}
        result["global_deconstruction"] = self.deconstructions.snapshot()
        result["incremental_index"] = {
            "tasks": incremental_index_tasks(),
            "ingestion_status": ingestion,
            "project_tone_matching": False,
            "note": "只更新共享书籍索引，不执行对标项目基调匹配。",
        }
        result["source"] = "oohstory-backend"
        result["catalog_write_policy"] = "只读；书目写入仅由既有白名单流水线执行"
        return result

    def browse_catalog(self, **filters: Any) -> dict[str, Any]:
        return self.database.browse(**filters)

    def browse_deconstructions(self, **filters: Any) -> dict[str, Any]:
        return self.deconstructions.browse(**filters)

    def cover_file(self, catalog_id: int) -> dict[str, Any]:
        return self.database.cover_file(catalog_id)


class LibraryCatalog(LibraryCatalogFacade):
    """Compatibility surface consumed by the FastAPI routes and templates."""

    def browse(
        self,
        *,
        view: str = "all",
        query: str = "",
        category: str = "",
        availability: str = "all",
        source: str = "all",
        tag: str = "",
        state: str = "all",
        page: int = 1,
        page_size: int = 28,
    ) -> dict[str, Any]:
        if view not in VIEWS:
            raise ValueError("unknown catalog view")
        if view == "deconstruction":
            if state not in {"all", "unstarted", "running", "scan", "full", "error"}:
                raise ValueError("unknown deconstruction state")
            records = self.deconstructions.records()
            resolved = self.database.resolve_deconstruction_records(records)
            matched_ids = set(resolved)
            if state == "unstarted":
                include_ids: tuple[int, ...] = ()
                exclude_ids = tuple(sorted(matched_ids))
            elif state == "all":
                include_ids = ()
                exclude_ids = ()
            else:
                include_ids = tuple(
                    sorted(
                        catalog_id
                        for catalog_id, item in resolved.items()
                        if item.get("state") == state
                        or (state == "scan" and item.get("state") == "full")
                    )
                )
                exclude_ids = ()
            if state not in {"all", "unstarted"} and not include_ids:
                result = {
                    "items": [], "total": 0, "page": page, "page_size": page_size,
                    "page_count": 1, "categories": [],
                    "filters": {"query": query, "category": category},
                    "source_read_only": True,
                }
            else:
                result = self.database.browse(
                    library="all",
                    availability=availability,
                    query=query,
                    category=category,
                    page=page,
                    page_size=page_size,
                    include_ids=include_ids,
                    exclude_ids=exclude_ids,
                )
            for item in result["items"]:
                record = resolved.get(_int(item.get("catalog_id")))
                item["deconstruction"] = record
                item["deconstruction_state"] = str(record.get("state")) if record else "unstarted"
            counts = self.database.active_counts()
            state_counts = {
                "all": counts["total"],
                "unstarted": max(counts["total"] - len(matched_ids), 0),
                "running": sum(item.get("state") == "running" for item in resolved.values()),
                "scan": sum(item.get("state") in {"scan", "full"} for item in resolved.values()),
                "full": sum(item.get("state") == "full" for item in resolved.values()),
                "error": sum(item.get("state") == "error" for item in resolved.values()),
                "readable": counts["readable"],
            }
            result.update(
                {
                    "view": view,
                    "view_label": VIEWS[view],
                    "source": "mysql-plus-global-deconstruction-filesystem",
                    "state": state,
                    "state_counts": state_counts,
                    "batches": self.deconstructions.batches(),
                }
            )
            return result

        if source not in CATALOG_FILTERS:
            raise ValueError("unknown source filter")
        library = view if view in CATALOG_LIBRARIES else source
        if view == "readable":
            availability = "readable"
        index_kind = view if view in {"tone", "plot"} else ""
        result = self.database.browse(
            library=library,
            availability=availability,
            query=query,
            category=category,
            page=page,
            page_size=page_size,
            index_kind=index_kind,
            tag=tag,
        )
        result.update(
            {
                "view": view,
                "view_label": VIEWS[view],
                "source": "mysql-read-only",
            }
        )
        return result

    def book(self, catalog_id: int) -> dict[str, Any]:
        return self.database.book(catalog_id)

    def plot_evidence(self, catalog_id: int, *, page: int = 1, page_size: int = 8) -> dict[str, Any]:
        return self.database.plot_evidence(catalog_id, page=page, page_size=page_size)
