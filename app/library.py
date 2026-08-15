from __future__ import annotations

import base64
import json
import hashlib
import math
import os
import random
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .mysql_catalog import MySQLPublicCatalog
from .settings import Settings


READER_HEADING_LINE = re.compile(
    r"^\s*(?:(?:正文)\s*)?"
    r"(?:(?:第\s*[0-9零一二三四五六七八九十百千万两〇○ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVXLC]+\s*卷"
    r"[^\n]{0,80}?)\s+)?"
    r"(?P<label>第\s*[0-9零一二三四五六七八九十百千万两〇○]+\s*[章回节])"
    r"(?P<title>[^\n]{0,120})\s*$",
    re.IGNORECASE,
)
READER_NUMERIC_HEADING_LINE = re.compile(
    r"^\s*(?P<number>0*[1-9]\d{0,5})"
    r"(?:[、.．:：\-—]|\s)+"
    r"(?P<title>\S[^\n]{0,119})\s*$"
)
READER_INDEX_SCHEMA_VERSION = 5
READER_LN_ILLUSTRATION_REF = re.compile(r"^\[本地插图：(.+?)\]\s*$", re.MULTILINE)
READER_LN_CHAPTER_FILE = re.compile(r"^(\d+)-(\d+)-(.+)-([0-9a-f]{10})\.txt$")
READER_FALLBACK_CHUNK_BYTES = 256 * 1024
READER_NOISE_LINE = re.compile(
    r"^\s*(?:第?\s*[\(（]\s*\d+\s*/\s*\d+\s*[\)）]\s*页?"
    r"|(?:www\.)?txt(?:80|02)\.(?:com|cc).*)\s*$",
    re.IGNORECASE,
)
DOCUMENTS = (
    ("拆文报告.md", "完整拆文报告"),
    ("概要.md", "全书概要"),
    ("文风.md", "文风拆解"),
)
GOLDEN_THREE_FILES = tuple(f"章节/第{chapter}章_深度拆解.md" for chapter in range(1, 4))
SAFE_COVER_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
OOHSTORY_DEFAULT_COVER_SHA256 = (
    "d421cee15a266d258979455101443085bbc686504ee802c55686cbfc92d0b09e"
)
OOHSTORY_DEFAULT_COVER_URL = (
    f"/api/v1/assets/default-cover?v={OOHSTORY_DEFAULT_COVER_SHA256[:16]}"
)
PUBLIC_VISITOR_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
PUBLIC_BOOK_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
_LOCAL_PUBLIC_ID_MASK = 0xA9D4F17C62E308B5
_LOCAL_PUBLIC_ID_KEY = b"oohstory-local-public-id"


def _encode_public_id(value: bytes) -> str:
    raw = bytes(value)
    if len(raw) != 16:
        raise LibraryError("作品公开标识无效")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cover_fingerprint(object_key: Any, row_version: Any = 0) -> str:
    identity = f"{str(object_key or '').strip()}\0{str(row_version or 0)}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _public_cover_url(
    book_id: str,
    object_key: Any = None,
    row_version: Any = 0,
) -> str:
    if not str(object_key or "").strip():
        return OOHSTORY_DEFAULT_COVER_URL
    version = _cover_fingerprint(object_key, row_version)
    return f"/api/v1/books/{book_id}/cover?v={version}"


def _decode_public_id(value: str) -> bytes:
    text = str(value or "")
    if not PUBLIC_BOOK_ID.fullmatch(text):
        raise NotFoundError("作品不存在或正文尚未就绪")
    try:
        raw = base64.b64decode(text + "==", altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise NotFoundError("作品不存在或正文尚未就绪") from None
    if len(raw) != 16 or _encode_public_id(raw) != text:
        raise NotFoundError("作品不存在或正文尚未就绪")
    return raw


def _local_public_id(catalog_id: int) -> str:
    obscured = (int(catalog_id) ^ _LOCAL_PUBLIC_ID_MASK).to_bytes(8, "big")
    signature = hashlib.blake2s(
        obscured, key=_LOCAL_PUBLIC_ID_KEY, digest_size=8
    ).digest()
    return _encode_public_id(obscured + signature)


def _local_catalog_id(public_id: str | int) -> int:
    # Direct repository callers may use an internal integer in local/test mode.
    # HTTP handlers reject numeric strings before reaching this layer, so public
    # routes never expose this compatibility path.
    if isinstance(public_id, int):
        if public_id <= 0:
            raise NotFoundError("作品不存在或正文尚未就绪")
        return public_id
    raw = _decode_public_id(public_id)
    expected = hashlib.blake2s(
        raw[:8], key=_LOCAL_PUBLIC_ID_KEY, digest_size=8
    ).digest()
    if raw[8:] != expected:
        raise NotFoundError("作品不存在或正文尚未就绪")
    catalog_id = int.from_bytes(raw[:8], "big") ^ _LOCAL_PUBLIC_ID_MASK
    if catalog_id <= 0:
        raise NotFoundError("作品不存在或正文尚未就绪")
    return catalog_id


WORD_FILTERS: dict[str, tuple[int | None, int | None]] = {
    "under_100k": (None, 100_000),
    "over_100k": (100_000, None),
    "over_200k": (200_000, None),
    "over_300k": (300_000, None),
    "over_500k": (500_000, None),
    "over_1m": (1_000_000, None),
    "over_2m": (2_000_000, None),
}

# TXT80 advertises its catalog as full completed novels. These few non-TXT80
# imports were verified against their source page or a clear completion marker
# in the local text. Unknown future imports default to ongoing until verified.
SERIALIZATION_STATUS_SQL = """
CASE
  WHEN COALESCE(b.detail_url,'') LIKE '%txt80.%'
    OR COALESCE(b.source_id,'') IN (
      'fanqie-7246248681131740175',
      'fanqie-7361653460183288894',
      'user-upload-e307980843eb',
      'xbiquge-85680'
    )
  THEN 'finished'
  ELSE 'ongoing'
END
"""


class LibraryError(RuntimeError):
    pass


class NotFoundError(LibraryError):
    pass


class UnsafePathError(LibraryError):
    pass


class InputError(LibraryError):
    pass


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _clean_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))
    return text.strip()[:limit]


def _clean_summary(value: Any) -> str:
    lines = []
    sanitized = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f]",
        "",
        str(value or ""),
    )
    for raw in sanitized.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"\[本地(?:分卷封面|插图)：[^\]\r\n]+\]", line) or line in {
            "分卷封面",
            "插画",
            "插图",
        }:
            continue
        if re.search(
            r"(txt80|txt02|八零电子书|用户上传|版权归|本站只提供)",
            line,
            re.IGNORECASE,
        ):
            continue
        lines.append(line)
    return "\n".join(lines)[:6000]


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [_clean_text(item, 40) for item in parsed[:16] if _clean_text(item, 40)]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LibraryRepository:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.reader_index_root.mkdir(parents=True, exist_ok=True)
        self._mysql = (
            MySQLPublicCatalog(settings)
            if settings.catalog_backend == "mysql"
            else None
        )
        self._index_locks: dict[int, threading.Lock] = {}
        self._index_locks_guard = threading.Lock()
        self._categories_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._categories_guard = threading.Lock()
        self._validate_layout()
        if self._mysql is None:
            self._initialize_sqlite_metrics()

    def _validate_layout(self) -> None:
        required = (
            ((self.settings.object_root or self.settings.library_root),)
            if self._mysql is not None
            else (
                self.settings.library_root,
                self.settings.catalog_path,
                self.settings.books_root,
            )
        )
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError(f"书库文件不存在：{', '.join(missing)}")
        if self._mysql is not None:
            self._mysql.health()

    @property
    def _sqlite_metrics_path(self) -> Path:
        return self.settings.state_root / "public-metrics.sqlite3"

    def _initialize_sqlite_metrics(self) -> None:
        self.settings.state_root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._sqlite_metrics_path, timeout=8) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                CREATE TABLE IF NOT EXISTS book_public_metrics (
                  catalog_id INTEGER PRIMARY KEY,
                  read_count INTEGER NOT NULL DEFAULT 0 CHECK(read_count >= 0),
                  download_count INTEGER NOT NULL DEFAULT 0 CHECK(download_count >= 0),
                  recommend_count INTEGER NOT NULL DEFAULT 0 CHECK(recommend_count >= 0),
                  favorite_count INTEGER NOT NULL DEFAULT 0 CHECK(favorite_count >= 0),
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS book_public_metric_visitors (
                  catalog_id INTEGER NOT NULL,
                  visitor_hash BLOB NOT NULL CHECK(length(visitor_hash)=32),
                  first_read_at TEXT,
                  first_download_at TEXT,
                  first_recommend_at TEXT,
                  first_favorite_at TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (catalog_id, visitor_hash)
                );
                """
            )
            metric_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(book_public_metrics)")
            }
            visitor_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(book_public_metric_visitors)"
                )
            }
            for column in ("recommend_count", "favorite_count"):
                if column not in metric_columns:
                    connection.execute(
                        f"ALTER TABLE book_public_metrics ADD COLUMN {column} "
                        "INTEGER NOT NULL DEFAULT 0 CHECK("
                        f"{column} >= 0)"
                    )
            for column in ("first_recommend_at", "first_favorite_at"):
                if column not in visitor_columns:
                    connection.execute(
                        f"ALTER TABLE book_public_metric_visitors ADD COLUMN {column} TEXT"
                    )

    @contextmanager
    def _ro_connection(
        self,
        path: Path,
        *,
        immutable: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        if not path.is_file():
            raise NotFoundError("索引尚未生成")
        query = "mode=ro&immutable=1" if immutable else "mode=ro"
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?{query}",
            uri=True,
            timeout=8,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=8000")
        try:
            yield connection
        finally:
            connection.close()

    def _book_path(self, raw_path: str) -> Path:
        if self._mysql is not None:
            root = (self.settings.object_root or self.settings.library_root).resolve()
            raw = Path(str(raw_path or ""))
            if raw.is_absolute():
                raise UnsafePathError("正文对象键必须是相对路径")
            path = (root / raw).resolve()
            if (
                not _within(path, root)
                or path.suffix.lower() != ".txt"
                or not path.is_file()
            ):
                raise NotFoundError("正文文件暂不可用")
            size = path.stat().st_size
            if size <= 0 or size > self.settings.max_source_bytes:
                raise InputError("正文文件大小超出阅读安全范围")
            return path
        root = self.settings.books_root.resolve()
        raw = Path(raw_path).expanduser()
        path = raw.resolve()
        if not _within(path, root) and self.settings.catalog_source_root is not None:
            source_books_root = (self.settings.catalog_source_root / "书籍").resolve()
            lexical_path = Path(os.path.abspath(str(raw)))
            if _within(lexical_path, source_books_root):
                relative = lexical_path.relative_to(source_books_root)
                path = (root / relative).resolve()
        if not _within(path, root) or path.suffix.lower() != ".txt":
            raise UnsafePathError("正文路径不在公开书库范围内")
        if not path.is_file():
            raise NotFoundError("正文文件暂不可用")
        size = path.stat().st_size
        if size <= 0 or size > self.settings.max_source_bytes:
            raise InputError("正文文件大小超出阅读安全范围")
        return path

    def stats(self) -> dict[str, Any]:
        if self._mysql is not None:
            result = self._mysql.stats()
            result["deconstruction_total"] = len(self._deconstruction_names())
            return result
        with self._ro_connection(self.settings.catalog_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='done'
                         AND NULLIF(TRIM(COALESCE(output_path,'')), '') IS NOT NULL
                         THEN 1 ELSE 0 END) AS readable,
                       COUNT(DISTINCT COALESCE(category,'未分类')) AS categories
                FROM books WHERE status != 'duplicate'
                """
            ).fetchone()
        deconstructions = len(self._deconstruction_names())
        return {
            "catalog_total": int(row["total"] or 0),
            "readable_total": int(row["readable"] or 0),
            "category_total": int(row["categories"] or 0),
            "deconstruction_total": deconstructions,
        }

    def categories(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._categories_guard:
            expires_at, cached = self._categories_cache
            if cached and now < expires_at:
                return [dict(item) for item in cached]
        if self._mysql is not None:
            result = self._mysql.categories()
        else:
            with self._ro_connection(self.settings.catalog_path) as conn:
                rows = conn.execute(
                    """
                    SELECT COALESCE(category,'未分类') AS name, COUNT(*) AS count
                    FROM books
                    WHERE status='done'
                      AND NULLIF(TRIM(COALESCE(output_path,'')), '') IS NOT NULL
                    GROUP BY COALESCE(category,'未分类')
                    ORDER BY count DESC, name
                    """
                ).fetchall()
            result = [
                {
                    "name": _clean_text(row["name"], 40),
                    "count": int(row["count"]),
                }
                for row in rows
            ]
        with self._categories_guard:
            self._categories_cache = (now + 300, result)
        return [dict(item) for item in result]

    def category_books(self, per_category: int = 5) -> dict[str, list[dict[str, Any]]]:
        if self._mysql is not None:
            raw = self._mysql.category_books(per_category)
            result: dict[str, list[dict[str, Any]]] = {}
            for cat, books in raw.items():
                enriched = []
                for b in books:
                    pid = _encode_public_id(bytes(b["public_id"]))
                    enriched.append(
                        {
                            "title": b["title"],
                            "author": b.get("author", ""),
                            "public_id": pid,
                            "cover_url": _public_cover_url(
                                pid,
                                b.get("cover_object_key"),
                                b.get("row_version"),
                            ),
                        }
                    )
                result[cat] = enriched
            return result
        with self._ro_connection(self.settings.catalog_path) as conn:
            rows = conn.execute(
                """
                SELECT category, title, id AS catalog_id
                FROM (
                    SELECT COALESCE(category,'未分类') AS category,
                           title, id,
                           ROW_NUMBER() OVER (
                               PARTITION BY COALESCE(category,'未分类')
                               ORDER BY CAST(COALESCE(bytes,0) / 3 AS INTEGER) DESC
                           ) AS rn
                    FROM books
                    WHERE status='done'
                      AND NULLIF(TRIM(COALESCE(output_path,'')), '') IS NOT NULL
                )
                WHERE rn <= ?
                ORDER BY category, rn
                """,
                (per_category,),
            ).fetchall()
        result = {}
        for row in rows:
            cat = _clean_text(row["category"], 40)
            if cat not in result:
                result[cat] = []
            result[cat].append(
                {
                    "title": _clean_text(row["title"], 200),
                    "public_id": _local_public_id(int(row["catalog_id"])),
                }
            )
        return result

    @staticmethod
    def _search_query(query: str) -> tuple[str, str]:
        query = _clean_text(query, 80)
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return query, f"%{escaped}%"

    def list_books(
        self,
        *,
        query: str = "",
        category: str = "",
        words: str = "",
        serialization: str = "",
        page: int = 1,
        page_size: int = 24,
        sort: str = "recent",
    ) -> dict[str, Any]:
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 48)
        query, like_query = self._search_query(query)
        category = _clean_text(category, 40)
        allowed_categories = {item["name"] for item in self.categories()}
        if category and category not in allowed_categories:
            raise InputError("未知分类")
        words = _clean_text(words, 20)
        serialization = _clean_text(serialization, 20)
        if words and words not in WORD_FILTERS:
            raise InputError("未知字数范围")
        if serialization not in {"", "finished", "ongoing"}:
            raise InputError("未知连载状态")
        if self._mysql is not None:
            result = self._mysql.list_books(
                query=query,
                category=category,
                words=words,
                serialization=serialization,
                page=page,
                page_size=page_size,
                sort=sort,
            )
            items = result["items"]
            self._enrich_books(items)
            return {
                **result,
                "query": query,
                "category": category,
                "words": words,
                "serialization": serialization,
                "sort": sort,
            }
        has_index = self.settings.index_path.is_file()
        word_expression = (
            "COALESCE(NULLIF(li.approx_word_count,0), "
            "CAST(COALESCE(b.bytes,0) / 3 AS INTEGER), 0)"
            if has_index
            else "CAST(COALESCE(b.bytes,0) / 3 AS INTEGER)"
        )
        order_map = {
            "recent": "b.id DESC",
            "title": "b.title COLLATE NOCASE ASC, b.id DESC",
            "long": f"{word_expression} DESC, b.id DESC",
        }
        if sort not in order_map:
            raise InputError("未知排序方式")
        conditions = [
            "b.status='done'",
            "NULLIF(TRIM(COALESCE(b.output_path,'')), '') IS NOT NULL",
        ]
        params: list[Any] = []
        if category:
            conditions.append("COALESCE(b.category,'未分类')=?")
            params.append(category)
        if query:
            conditions.append(
                "(COALESCE(b.title,'') LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR COALESCE(b.author,'') LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            params.extend((like_query, like_query))
        if words:
            minimum, maximum = WORD_FILTERS[words]
            if minimum is not None:
                conditions.append(f"{word_expression} >= ?")
                params.append(minimum)
            if maximum is not None:
                conditions.append(f"{word_expression} < ?")
                params.append(maximum)
        if serialization:
            conditions.append(f"({SERIALIZATION_STATUS_SQL}) = ?")
            params.append(serialization)
        where = " AND ".join(conditions)
        offset = (page - 1) * page_size
        with self._ro_connection(self.settings.catalog_path) as conn:
            from_clause = "books b"
            if has_index:
                conn.execute(
                    "ATTACH DATABASE ? AS global_index",
                    (
                        f"file:{self.settings.index_path.as_posix()}"
                        "?mode=ro&immutable=1",
                    ),
                )
                from_clause += (
                    " LEFT JOIN global_index.library_index li ON li.catalog_id=b.id"
                )
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {from_clause} WHERE {where}",
                    params,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT b.id AS catalog_id,
                       COALESCE(b.source_id,b.id) AS source_id,
                       b.title, b.author,
                       COALESCE(b.category,'未分类') AS category,
                       COALESCE(b.bytes,0) AS source_bytes,
                       b.updated_at,
                       {word_expression} AS filtered_word_count,
                       {SERIALIZATION_STATUS_SQL} AS serialization_status
                FROM {from_clause}
                WHERE {where}
                ORDER BY {order_map[sort]}
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()
        items = [dict(row) for row in rows]
        self._enrich_books(items)
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "page_count": max(math.ceil(total / page_size), 1),
            "query": query,
            "category": category,
            "words": words,
            "serialization": serialization,
            "sort": sort,
        }

    def _all_readable_books(self, *, words: str = "") -> list[dict[str, Any]]:
        if self._mysql is not None:
            return self._mysql.list_books(
                query="",
                category="",
                words=words,
                serialization="",
                page=1,
                page_size=5000,
                sort="recent",
            )["items"]
        has_index = self.settings.index_path.is_file()
        word_expression = (
            "COALESCE(NULLIF(li.approx_word_count,0), "
            "CAST(COALESCE(b.bytes,0) / 3 AS INTEGER), 0)"
            if has_index
            else "CAST(COALESCE(b.bytes,0) / 3 AS INTEGER)"
        )
        word_conditions: list[str] = []
        if words and words in WORD_FILTERS:
            minimum, maximum = WORD_FILTERS[words]
            if minimum is not None:
                word_conditions.append(f"{word_expression} >= {minimum}")
            if maximum is not None:
                word_conditions.append(f"{word_expression} < {maximum}")
        with self._ro_connection(self.settings.catalog_path) as conn:
            from_clause = "books b"
            if has_index:
                conn.execute(
                    "ATTACH DATABASE ? AS global_index",
                    (
                        f"file:{self.settings.index_path.as_posix()}"
                        "?mode=ro&immutable=1",
                    ),
                )
                from_clause += (
                    " LEFT JOIN global_index.library_index li ON li.catalog_id=b.id"
                )
            where = (
                "b.status='done'"
                " AND NULLIF(TRIM(COALESCE(b.output_path,'')), '') IS NOT NULL"
            )
            if word_conditions:
                where += " AND " + " AND ".join(word_conditions)
            rows = conn.execute(
                f"""
                SELECT b.id AS catalog_id,
                       COALESCE(b.source_id,b.id) AS source_id,
                       b.title, b.author,
                       COALESCE(b.category,'未分类') AS category,
                       COALESCE(b.bytes,0) AS source_bytes,
                       b.updated_at,
                       {word_expression} AS filtered_word_count,
                       {SERIALIZATION_STATUS_SQL} AS serialization_status
                FROM {from_clause}
                WHERE {where}
                ORDER BY b.id
                """,
            ).fetchall()
        return [dict(row) for row in rows]

    def random_recommendations(
        self, count: int = 14, *, words: str = ""
    ) -> list[dict[str, Any]]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        all_items = self._all_readable_books(words=words)
        promoted: list[dict[str, Any]] = []
        if self._mysql is not None:
            promoted = self._mysql.engagement_recommendations(
                min(count * 2, 100), words=words,
            )
        max_per_cat = max(2, count // 5)
        cat_counts: dict[str, int] = {}

        def _accept(item: dict[str, Any]) -> bool:
            cat = str(item.get("category") or "未分类")
            if cat_counts.get(cat, 0) >= max_per_cat:
                return False
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            return True

        capped = [item for item in promoted if _accept(item)]
        picked_ids = {int(item["catalog_id"]) for item in capped}
        available = [
            item for item in all_items
            if int(item["catalog_id"]) not in picked_ids
        ]
        rng = random.Random(today + words)
        rng.shuffle(available)
        picked = capped[:count]
        for item in available:
            if len(picked) >= count:
                break
            if _accept(item):
                picked.append(item)
        if len(picked) < count:
            rest_ids = {int(item["catalog_id"]) for item in picked}
            rest = [
                item for item in available
                if int(item["catalog_id"]) not in rest_ids
            ]
            picked.extend(rest[: count - len(picked)])
        rng.shuffle(picked)
        self._enrich_books(picked)
        return picked

    def sitemap_book_ids(self, limit: int = 49_990) -> list[str]:
        safe_limit = min(max(int(limit), 1), 49_990)
        if self._mysql is not None:
            return [
                _encode_public_id(value)
                for value in self._mysql.sitemap_book_ids(safe_limit)
            ]
        with self._ro_connection(self.settings.catalog_path) as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM books
                WHERE status='done'
                  AND NULLIF(TRIM(COALESCE(output_path,'')), '') IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [_local_public_id(int(row["id"])) for row in rows]

    def sitemap_book_entries(self, limit: int = 49_990) -> list[tuple[str, str]]:
        safe_limit = min(max(int(limit), 1), 49_990)
        if self._mysql is not None:
            return [
                (_encode_public_id(public_id), lastmod)
                for public_id, lastmod in self._mysql.sitemap_book_entries(safe_limit)
            ]
        return [(book_id, "") for book_id in self.sitemap_book_ids(safe_limit)]

    def _enrich_books(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        ids = [int(item["catalog_id"]) for item in items]
        index_rows: dict[int, sqlite3.Row] = {}
        if self._mysql is None and self.settings.index_path.is_file():
            placeholders = ",".join("?" for _ in ids)
            with self._ro_connection(self.settings.index_path, immutable=True) as conn:
                for row in conn.execute(
                    f"""
                    SELECT catalog_id,summary,approx_word_count,
                           approx_chapter_count,genre_tags,tone_tags
                    FROM library_index WHERE catalog_id IN ({placeholders})
                    """,
                    ids,
                ):
                    index_rows[int(row["catalog_id"])] = row
        cover_ids: set[int] = set()
        if self._mysql is None and self.settings.cover_index_path.is_file():
            placeholders = ",".join("?" for _ in ids)
            with self._ro_connection(
                self.settings.cover_index_path,
                immutable=True,
            ) as conn:
                cover_ids = {
                    int(row[0])
                    for row in conn.execute(
                        f"""
                        SELECT catalog_id FROM covers
                        WHERE status='done' AND filename IS NOT NULL
                          AND catalog_id IN ({placeholders})
                        """,
                        ids,
                    )
                }
        deconstruction_slugs = {
            self._deconstruction_title(name): name
            for name in self._deconstruction_names()
        }
        for item in items:
            item.pop("engagement_score", None)
            catalog_id = int(item["catalog_id"])
            public_id = (
                _encode_public_id(bytes(item.pop("public_id")))
                if self._mysql is not None
                else _local_public_id(catalog_id)
            )
            indexed = index_rows.get(catalog_id)
            item["title"] = _clean_text(item.get("title"), 160) or "未命名作品"
            item["author"] = _clean_text(item.get("author"), 100) or "佚名"
            item["category"] = _clean_text(item.get("category"), 40) or "未分类"
            item["summary"] = _clean_summary(
                item.get("summary")
                if self._mysql is not None
                else (indexed["summary"] if indexed else "")
            )
            item["approx_word_count"] = int(
                (
                    item.get("filtered_word_count")
                    if self._mysql is not None
                    else (indexed["approx_word_count"] if indexed else 0)
                )
                or int(item.get("filtered_word_count") or 0)
                or max(int(item.get("source_bytes") or 0) // 3, 0)
            )
            item["approx_chapter_count"] = int(
                (
                    item.get("approx_chapter_count")
                    if self._mysql is not None
                    else (indexed["approx_chapter_count"] if indexed else 0)
                )
                or 0
            )
            item["genre_tags"] = _json_list(
                item.get("genre_tags")
                if self._mysql is not None
                else (indexed["genre_tags"] if indexed else "[]")
            )
            item["tone_tags"] = _json_list(
                item.get("tone_tags")
                if self._mysql is not None
                else (indexed["tone_tags"] if indexed else "[]")
            )
            cover_object_key = (
                item.get("cover_object_key")
                if self._mysql is not None
                else (f"local-cover:{catalog_id}" if catalog_id in cover_ids else None)
            )
            has_real_cover = bool(cover_object_key)
            item["cover_url"] = _public_cover_url(
                public_id,
                cover_object_key,
                item.get("row_version"),
            )
            item["cover_is_default"] = not has_real_cover
            item["deconstruction_slug"] = deconstruction_slugs.get(item["title"])
            item["has_deconstruction"] = bool(item["deconstruction_slug"])
            item["serialization_status"] = (
                "finished"
                if item.get("serialization_status") == "finished"
                else "ongoing"
            )
            item.pop("filtered_word_count", None)
            item.pop("cover_object_key", None)
            item.pop("row_version", None)
            item.pop("catalog_id", None)
            item["public_id"] = public_id

    def get_book(self, book_id: str) -> dict[str, Any]:
        if self._mysql is not None:
            row = self._mysql.get_book(_decode_public_id(book_id))
            if not row:
                raise NotFoundError("作品不存在或正文尚未就绪")
            item = dict(row)
            self._book_path(str(item.pop("body_object_key")))
            self._enrich_books([item])
            item["available_for_reading"] = True
            return item
        catalog_id = _local_catalog_id(book_id)
        with self._ro_connection(self.settings.catalog_path) as conn:
            row = conn.execute(
                f"""
                SELECT b.id AS catalog_id,
                       COALESCE(b.source_id,b.id) AS source_id,
                       b.title, b.author,
                       COALESCE(b.category,'未分类') AS category,
                       COALESCE(b.bytes,0) AS source_bytes, b.output_path,
                       b.discovered_at, b.updated_at,
                       {SERIALIZATION_STATUS_SQL} AS serialization_status
                FROM books b
                WHERE b.id=? AND b.status='done'
                  AND NULLIF(TRIM(COALESCE(b.output_path,'')), '') IS NOT NULL
                """,
                (catalog_id,),
            ).fetchone()
        if not row:
            raise NotFoundError("作品不存在或正文尚未就绪")
        item = dict(row)
        self._book_path(str(item.pop("output_path")))
        self._enrich_books([item])
        item["available_for_reading"] = True
        return item

    def account_state_books(self, book_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Hydrate private account cards from the catalog without per-book queries."""
        unique = list(dict.fromkeys(str(value) for value in book_ids))[:1000]
        if not unique:
            return {}
        items: list[dict[str, Any]] = []
        if self._mysql is not None:
            decoded: list[bytes] = []
            for value in unique:
                try:
                    decoded.append(_decode_public_id(value))
                except LibraryError:
                    continue
            items = [dict(row) for row in self._mysql.get_books(decoded)]
        else:
            catalog_ids: list[int] = []
            for value in unique:
                try:
                    catalog_ids.append(_local_catalog_id(value))
                except LibraryError:
                    continue
            if catalog_ids:
                placeholders = ",".join("?" for _ in catalog_ids)
                with self._ro_connection(self.settings.catalog_path) as conn:
                    items = [
                        dict(row)
                        for row in conn.execute(
                            f"""
                        SELECT b.id AS catalog_id,COALESCE(b.source_id,b.id) AS source_id,
                               b.title,b.author,COALESCE(b.category,'未分类') AS category,
                               COALESCE(b.bytes,0) AS source_bytes,b.output_path,b.updated_at,
                               0 AS approx_chapter_count,
                               {SERIALIZATION_STATUS_SQL} AS serialization_status
                        FROM books b WHERE b.id IN ({placeholders}) AND b.status='done'
                          AND NULLIF(TRIM(COALESCE(b.output_path,'')), '') IS NOT NULL
                        """,
                            catalog_ids,
                        )
                    ]
        self._enrich_books(items)
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            public_id = str(item.get("public_id") or "")
            if not public_id:
                continue
            chapter_count = max(int(item.get("approx_chapter_count") or 0), 0)
            result[public_id] = {
                "book_id": public_id,
                "title": item.get("title") or "未命名作品",
                "author": item.get("author") or "佚名",
                "cover_url": item.get("cover_url") or "",
                "serialization_status": item.get("serialization_status") or "ongoing",
                "chapter_count": chapter_count,
                "latest_chapter_id": chapter_count or None,
                "latest_chapter": f"第 {chapter_count} 章"
                if chapter_count
                else "最新章节待同步",
            }
        return result

    def cover_path_and_version(self, book_id: str) -> tuple[Path, str]:
        if self._mysql is not None:
            descriptor = self._mysql.cover_descriptor(_decode_public_id(book_id))
            if not descriptor:
                return (
                    self.shared_default_cover_path(),
                    OOHSTORY_DEFAULT_COVER_SHA256[:16],
                )
            object_key = str(descriptor["cover_object_key"])
            root = (self.settings.object_root or self.settings.library_root).resolve()
            candidate = root / object_key
            path = candidate.resolve()
            if candidate.is_symlink() or not _within(path, root) or not path.is_file():
                raise UnsafePathError("封面路径无效")
            return (
                self._validated_cover(path),
                _cover_fingerprint(object_key, descriptor.get("row_version")),
            )
        catalog_id = _local_catalog_id(book_id)
        if not self.settings.cover_index_path.is_file():
            raise NotFoundError("封面不存在")
        with self._ro_connection(
            self.settings.cover_index_path,
            immutable=True,
        ) as conn:
            row = conn.execute(
                "SELECT filename FROM covers WHERE catalog_id=? AND status='done'",
                (catalog_id,),
            ).fetchone()
        if not row:
            raise NotFoundError("封面不存在")
        root = self.settings.cover_root.resolve()
        candidate = root / str(row["filename"])
        path = candidate.resolve()
        if candidate.is_symlink() or not _within(path, root) or not path.is_file():
            raise UnsafePathError("封面路径无效")
        validated = self._validated_cover(path)
        stat = validated.stat()
        return (
            validated,
            _cover_fingerprint(
                str(row["filename"]),
                f"{stat.st_mtime_ns}:{stat.st_size}",
            ),
        )

    def cover_path(self, book_id: str) -> Path:
        return self.cover_path_and_version(book_id)[0]

    def shared_default_cover_path(self) -> Path:
        path = self.settings.default_cover_path
        if not path.is_file():
            raise NotFoundError("默认封面不存在")
        return self._validated_cover(path)

    def _validated_cover(self, path: Path) -> Path:
        if path.is_symlink() or path.suffix.casefold() not in SAFE_COVER_SUFFIXES:
            raise UnsafePathError("封面文件类型无效")
        size = path.stat().st_size
        if size <= 0 or size > self.settings.max_cover_bytes:
            raise UnsafePathError("封面文件大小无效")
        with path.open("rb") as handle:
            header = handle.read(32)
        valid_magic = (
            header.startswith(b"\xff\xd8\xff")
            or header.startswith(b"\x89PNG\r\n\x1a\n")
            or header.startswith((b"GIF87a", b"GIF89a"))
            or (
                len(header) >= 12
                and header.startswith(b"RIFF")
                and header[8:12] == b"WEBP"
            )
            or (
                len(header) >= 12
                and header[4:8] == b"ftyp"
                and header[8:12] in {b"avif", b"avis"}
            )
        )
        if not valid_magic:
            raise UnsafePathError("封面文件内容无效")
        return path

    def _source_for_book(self, book_id: str) -> tuple[dict[str, Any], Path, int, str]:
        if self._mysql is not None:
            public_raw = _decode_public_id(book_id)
            row = self._mysql.source_book(public_raw)
            if not row:
                raise NotFoundError("作品不存在或正文尚未就绪")
            book = dict(row)
            catalog_id = int(book.pop("catalog_id"))
            public_id = _encode_public_id(bytes(book.pop("public_id")))
            source = self._book_path(str(book.pop("body_object_key")))
            return book, source, catalog_id, public_id
        catalog_id = _local_catalog_id(book_id)
        with self._ro_connection(self.settings.catalog_path) as conn:
            row = conn.execute(
                """
                SELECT id AS catalog_id,title,author,
                       COALESCE(category,'未分类') AS category,
                       COALESCE(bytes,0) AS source_bytes,output_path
                FROM books
                WHERE id=? AND status='done'
                  AND NULLIF(TRIM(COALESCE(output_path,'')), '') IS NOT NULL
                """,
                (catalog_id,),
            ).fetchone()
        if not row:
            raise NotFoundError("作品不存在或正文尚未就绪")
        book = dict(row)
        source = self._book_path(str(book.pop("output_path")))
        book.pop("catalog_id", None)
        return book, source, catalog_id, _local_public_id(catalog_id)

    @staticmethod
    def _download_filename(title: Any, author: Any) -> str:
        title_text = _clean_text(title, 120) or "未命名作品"
        author_text = _clean_text(author, 60) or "佚名"
        stem = re.sub(
            r"[\x00-\x1f\x7f/\\:*?\"<>|]+",
            " ",
            f"{title_text} - {author_text}",
        )
        stem = re.sub(r"\s+", " ", stem).strip(" .")
        return f"{stem[:160] or 'OOH Story 电子书'}.txt"

    def download_source(self, book_id: str) -> tuple[Path, str]:
        book, source, _catalog_id, _public_id = self._source_for_book(book_id)
        filename = self._download_filename(book.get("title"), book.get("author"))
        return source, filename

    def public_metrics(self, book_id: str) -> dict[str, Any]:
        _book, _source, catalog_id, public_id = self._source_for_book(book_id)
        if self._mysql is not None:
            result = self._mysql.public_metrics(_decode_public_id(public_id))
            if result is None:
                raise NotFoundError("作品不存在或正文尚未就绪")
            return {
                "public_id": public_id,
                "read_count": int(result["read_count"]),
                "download_count": int(result["download_count"]),
                "recommend_count": int(result.get("recommend_count", 0)),
                "favorite_count": int(result.get("favorite_count", 0)),
            }
        with sqlite3.connect(self._sqlite_metrics_path, timeout=8) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT catalog_id, read_count, download_count,
                       recommend_count, favorite_count
                FROM book_public_metrics WHERE catalog_id=?
                """,
                (catalog_id,),
            ).fetchone()
        return {
            "public_id": public_id,
            "read_count": int(row["read_count"] if row else 0),
            "download_count": int(row["download_count"] if row else 0),
            "recommend_count": int(row["recommend_count"] if row else 0),
            "favorite_count": int(row["favorite_count"] if row else 0),
        }

    def set_favorite_count(self, book_id: str, favorite_count: int) -> dict[str, Any]:
        """Synchronize the public favorite metric from active account rows."""
        _book, _source, catalog_id, public_id = self._source_for_book(book_id)
        normalized = max(int(favorite_count), 0)
        if self._mysql is not None:
            result = self._mysql.set_favorite_count(
                _decode_public_id(public_id), normalized
            )
            if result is None:
                raise NotFoundError("作品不存在或正文尚未就绪")
            return {
                "public_id": public_id,
                "read_count": int(result["read_count"]),
                "download_count": int(result["download_count"]),
                "recommend_count": int(result["recommend_count"]),
                "favorite_count": int(result["favorite_count"]),
            }
        now = _utc_now()
        with sqlite3.connect(self._sqlite_metrics_path, timeout=8) as connection:
            connection.execute(
                """
                INSERT INTO book_public_metrics
                    (catalog_id,read_count,download_count,recommend_count,
                     favorite_count,updated_at)
                VALUES (?,0,0,0,?,?)
                ON CONFLICT(catalog_id) DO UPDATE SET
                    favorite_count=excluded.favorite_count,
                    updated_at=excluded.updated_at
                """,
                (catalog_id, normalized, now),
            )
        return self.public_metrics(public_id)

    def record_public_metric(
        self,
        book_id: str,
        visitor_id: str,
        event: str,
    ) -> dict[str, Any]:
        if event not in {"read", "download", "recommend"}:
            raise InputError("未知统计事件")
        if not isinstance(visitor_id, str) or not PUBLIC_VISITOR_UUID.fullmatch(
            visitor_id
        ):
            raise InputError("无效匿名访客编号")
        visitor_hash = hashlib.sha256(visitor_id.encode("ascii")).digest()
        _book, _source, catalog_id, public_id = self._source_for_book(book_id)
        if self._mysql is not None:
            result = self._mysql.record_public_metric(
                _decode_public_id(public_id), visitor_hash, event
            )
            if result is None:
                raise NotFoundError("作品不存在或正文尚未就绪")
            return {
                "public_id": public_id,
                "read_count": int(result["read_count"]),
                "download_count": int(result["download_count"]),
                "recommend_count": int(result.get("recommend_count", 0)),
                "favorite_count": int(result.get("favorite_count", 0)),
                "counted": bool(result["counted"]),
            }
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
        now = _utc_now()
        connection = sqlite3.connect(
            self._sqlite_metrics_path,
            timeout=8,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=8000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO book_public_metrics
                    (catalog_id, read_count, download_count, updated_at)
                VALUES (?, 0, 0, ?)
                """,
                (catalog_id, now),
            )
            cursor = connection.execute(
                f"""
                INSERT OR IGNORE INTO book_public_metric_visitors
                    (catalog_id, visitor_hash, {timestamp_column},
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (catalog_id, visitor_hash, now, now, now),
            )
            counted = cursor.rowcount == 1
            if not counted:
                cursor = connection.execute(
                    f"""
                    UPDATE book_public_metric_visitors
                    SET {timestamp_column}=?, updated_at=?
                    WHERE catalog_id=? AND visitor_hash=?
                      AND {timestamp_column} IS NULL
                    """,
                    (now, now, catalog_id, visitor_hash),
                )
                counted = cursor.rowcount == 1
            if counted:
                connection.execute(
                    f"""
                    UPDATE book_public_metrics
                    SET {count_column}={count_column}+1, updated_at=?
                    WHERE catalog_id=?
                    """,
                    (now, catalog_id),
                )
            row = connection.execute(
                """
                SELECT catalog_id, read_count, download_count,
                       recommend_count, favorite_count
                FROM book_public_metrics WHERE catalog_id=?
                """,
                (catalog_id,),
            ).fetchone()
            connection.commit()
            return {
                "public_id": public_id,
                "read_count": int(row["read_count"]),
                "download_count": int(row["download_count"]),
                "recommend_count": int(row["recommend_count"]),
                "favorite_count": int(row["favorite_count"]),
                "counted": counted,
            }
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def rankings(self, limit: int = 10) -> dict[str, list[dict]]:
        if self._mysql is not None:
            raw = self._mysql.rankings(limit)
            for key in raw:
                for item in raw[key]:
                    pid = _encode_public_id(item["public_id"])
                    item["public_id"] = pid
                    cover_object_key = item.pop("cover_object_key", None)
                    row_version = item.pop("row_version", 0)
                    has_real_cover = bool(cover_object_key)
                    item["cover_url"] = _public_cover_url(
                        pid, cover_object_key, row_version
                    )
                    item["cover_is_default"] = not has_real_cover
            return raw
        return {
            "weekly_clicks": [],
            "monthly_clicks": [],
            "monthly_recommends": [],
            "new_books": [],
            "favorites": [],
            "completed": [],
        }

    def _reader_index_path(self, book_id: int) -> Path:
        return self.settings.reader_index_root / f"{int(book_id)}.json"

    @staticmethod
    def _clean_reader_title(value: str) -> str:
        return re.sub(r"^[\s:：、—\-·.]+", "", value or "").strip()[:120]

    @classmethod
    def _heading_candidates(
        cls, source_path: Path
    ) -> tuple[list[dict[str, Any]], str, list[int]]:
        named: list[dict[str, Any]] = []
        numeric: list[dict[str, Any]] = []
        with source_path.open("rb") as handle:
            while True:
                offset = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                line = (
                    raw.decode("utf-8", errors="replace")
                    .lstrip("\ufeff")
                    .rstrip("\r\n")
                )
                stripped = line.strip()
                if not stripped or len(stripped) > 220:
                    continue
                if len(line) - len(line.lstrip(" \t")) > 2:
                    continue
                match = READER_HEADING_LINE.fullmatch(stripped)
                if match:
                    named.append(
                        {
                            "offset": offset,
                            "label": re.sub(r"\s+", "", match.group("label")),
                            "title": cls._clean_reader_title(match.group("title")),
                        }
                    )
                    continue
                match = READER_NUMERIC_HEADING_LINE.fullmatch(stripped)
                if match:
                    number = int(match.group("number"))
                    numeric.append(
                        {
                            "offset": offset,
                            "number": number,
                            "label": f"第{number}章",
                            "title": cls._clean_reader_title(match.group("title")),
                        }
                    )
        deduped: list[dict[str, Any]] = []
        for item in named:
            previous = deduped[-1] if deduped else None
            if (
                previous
                and previous["label"] == item["label"]
                and previous["title"] == item["title"]
                and int(item["offset"]) - int(previous["offset"]) <= 4096
            ):
                continue
            deduped.append(item)
        sequence: list[dict[str, Any]] = []
        start = next(
            (i for i, item in enumerate(numeric) if int(item["number"]) <= 5), None
        )
        if start is not None:
            previous_number = 0
            tail = numeric[start:]
            for index, item in enumerate(tail):
                number = int(item["number"])
                if number <= previous_number or (
                    previous_number and number - previous_number > 50
                ):
                    continue
                if (
                    previous_number
                    and number - previous_number > 1
                    and any(
                        previous_number < int(candidate["number"]) < number
                        for candidate in tail[index + 1 : index + 65]
                    )
                ):
                    continue
                sequence.append(item)
                previous_number = number
        if len(deduped) >= 8 and len(deduped) >= max(len(sequence) * 2, 8):
            return deduped, "named", [int(item["offset"]) for item in deduped]
        if len(sequence) >= 8:
            adjacent = sum(
                int(current["number"]) - int(previous["number"]) == 1
                for previous, current in zip(sequence, sequence[1:])
            )
            if adjacent / max(len(sequence) - 1, 1) >= 0.55:
                return sequence, "numeric", [int(item["offset"]) for item in sequence]
        return deduped, "named", [int(item["offset"]) for item in deduped]

    def _find_ln_source_dir(self, source: Path) -> Path | None:
        root = (self.settings.object_root or self.settings.library_root).resolve()
        light_novel_root = (root / "书籍" / "轻小说").resolve()
        source_parent = source.parent.resolve()
        if (
            source_parent.is_dir()
            and _within(source_parent, light_novel_root)
            and source_parent != light_novel_root
            and any(
                candidate.is_dir()
                and not candidate.is_symlink()
                and re.match(r"\d{3}-", candidate.name)
                and any(
                    (candidate / dirname).is_dir() for dirname in ("章节", "章节目录")
                )
                for candidate in source_parent.iterdir()
            )
        ):
            return source_parent
        with source.open("rb") as handle:
            for raw_line in handle:
                line = raw_line.decode("utf-8", errors="replace").strip()
                match = READER_LN_ILLUSTRATION_REF.match(line)
                if match:
                    rel = match.group(1)
                    dirname = rel.split("/")[0]
                    ln_dir = root / "书籍" / "轻小说" / dirname
                    if ln_dir.is_dir():
                        return ln_dir
                    break
        return None

    @staticmethod
    def _ln_content_dir(volume_dir: Path, names: tuple[str, ...]) -> Path | None:
        for name in names:
            candidate = volume_dir / name
            if candidate.is_dir() and not candidate.is_symlink():
                return candidate
        return None

    def _build_ln_reader_index(
        self, book_id: int, source: Path, ln_dir: Path, book_title: str
    ) -> dict[str, Any]:
        stat = source.stat()
        volumes: list[dict[str, Any]] = []
        chapters: list[dict[str, Any]] = []
        chapter_id = 1

        vol_title_map: dict[int, str] = {}
        with source.open("rb") as handle:
            for raw_line in handle:
                line = raw_line.decode("utf-8", errors="replace").strip()
                match = re.fullmatch(r"【(.+)】", line)
                if match:
                    text = match.group(1).strip()
                    if book_title and text.startswith(book_title[:6]):
                        vol_title_map[len(vol_title_map)] = text

        vol_dirs = sorted(
            d for d in ln_dir.iterdir() if d.is_dir() and re.match(r"\d{3}-", d.name)
        )

        for vol_idx, vol_dir in enumerate(vol_dirs):
            chapter_dir = self._ln_content_dir(vol_dir, ("章节", "章节目录"))
            illust_dir = self._ln_content_dir(vol_dir, ("插画", "插图"))
            if chapter_dir is None:
                continue

            vol_title = vol_title_map.get(vol_idx, "")
            if not vol_title:
                raw = re.sub(r"^\d{3}-", "", vol_dir.name)
                vol_title = raw.replace("-", " ").strip()

            illust_count = 0
            illust_paths: list[str] = []
            if illust_dir is not None:
                for f in sorted(illust_dir.iterdir()):
                    if f.is_file() and f.suffix.lower() in SAFE_COVER_SUFFIXES:
                        illust_count += 1
                        illust_paths.append(str(f.relative_to(ln_dir)))

            cover_dir = vol_dir / "封面"
            vol_cover_path = ""
            if cover_dir.is_dir():
                for f in cover_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in SAFE_COVER_SUFFIXES:
                        vol_cover_path = str(f.relative_to(ln_dir))
                        break

            vol_chapter_ids: list[int] = []
            chapter_files = sorted(
                f for f in chapter_dir.iterdir() if f.is_file() and f.suffix == ".txt"
            )

            for ch_file in chapter_files:
                match = READER_LN_CHAPTER_FILE.match(ch_file.name)
                if match:
                    title_part = match.group(3)
                else:
                    title_part = ch_file.stem

                if title_part in ("插图", "插画"):
                    continue

                ch = {
                    "id": chapter_id,
                    "label": vol_title,
                    "title": title_part,
                    "volume_id": len(volumes) + 1,
                    "file_path": str(ch_file.relative_to(ln_dir)),
                    "byte_count": ch_file.stat().st_size,
                }
                chapters.append(ch)
                vol_chapter_ids.append(chapter_id)
                chapter_id += 1

            if vol_chapter_ids:
                volumes.append(
                    {
                        "id": len(volumes) + 1,
                        "title": vol_title,
                        "chapter_ids": vol_chapter_ids,
                        "illustration_count": illust_count,
                        "cover_path": vol_cover_path,
                        "illustration_paths": illust_paths,
                    }
                )

        payload = {
            "schema_version": READER_INDEX_SCHEMA_VERSION,
            "catalog_id": int(book_id),
            "source_type": "light_novel",
            "source_dir": str(ln_dir),
            "source_path": str(source),
            "source_bytes": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
            "chapter_count": len(chapters),
            "heading_style": "light_novel",
            "chapters": chapters,
            "volumes": volumes,
            "indexed_at": _utc_now(),
        }
        if len(chapters) > self.settings.max_chapter_count:
            raise InputError("章节目录数量超出在线阅读安全范围")
        target = self._reader_index_path(book_id)
        temp = target.with_name(
            f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
        return payload

    def _build_reader_index(self, book_id: int, source: Path) -> dict[str, Any]:
        stat = source.stat()
        headings, style, boundaries = self._heading_candidates(source)
        sections: list[dict[str, Any]] = []
        if headings:
            first_offset = int(headings[0]["offset"])
            if first_offset:
                with source.open("rb") as handle:
                    intro = handle.read(min(first_offset, 64 * 1024)).decode(
                        "utf-8", errors="replace"
                    )
                if len(re.sub(r"\s+", "", intro)) >= 80:
                    sections.append(
                        {
                            "start": 0,
                            "end": first_offset,
                            "label": "序",
                            "title": "作品信息",
                        }
                    )
            for index, heading in enumerate(headings):
                next_heading = (
                    int(headings[index + 1]["offset"])
                    if index + 1 < len(headings)
                    else stat.st_size
                )
                next_boundary = next(
                    (
                        offset
                        for offset in boundaries
                        if int(heading["offset"]) < offset
                    ),
                    next_heading,
                )
                sections.append(
                    {
                        "start": int(heading["offset"]),
                        "end": min(next_heading, next_boundary),
                        "label": heading["label"],
                        "title": heading["title"] or heading["label"],
                    }
                )
        else:
            start = 0
            number = 1
            with source.open("rb") as handle:
                while True:
                    raw = handle.readline()
                    if not raw:
                        break
                    end = handle.tell()
                    if end - start < READER_FALLBACK_CHUNK_BYTES:
                        continue
                    sections.append(
                        {
                            "start": start,
                            "end": end,
                            "label": "正文",
                            "title": f"正文片段 {number}",
                        }
                    )
                    start = end
                    number += 1
            if start < stat.st_size or not sections:
                sections.append(
                    {
                        "start": start,
                        "end": stat.st_size,
                        "label": "正文",
                        "title": f"正文片段 {number}",
                    }
                )
        chapters = [
            {
                "id": index,
                "label": section["label"],
                "title": section["title"],
                "start": int(section["start"]),
                "end": int(section["end"]),
                "byte_count": max(int(section["end"]) - int(section["start"]), 0),
            }
            for index, section in enumerate(sections, 1)
        ]
        payload = {
            "schema_version": READER_INDEX_SCHEMA_VERSION,
            "catalog_id": int(book_id),
            "source_path": str(source),
            "source_bytes": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
            "chapter_count": len(chapters),
            "heading_style": style,
            "chapters": chapters,
            "indexed_at": _utc_now(),
        }
        if len(chapters) > self.settings.max_chapter_count:
            raise InputError("章节目录数量超出在线阅读安全范围")
        target = self._reader_index_path(book_id)
        temp = target.with_name(
            f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
        return payload

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _valid_reader_index(self, payload: dict[str, Any], source: Path) -> bool:
        stat = source.stat()
        try:
            cached_source = self._book_path(str(payload.get("source_path") or ""))
        except LibraryError:
            return False
        return bool(
            payload.get("schema_version") == READER_INDEX_SCHEMA_VERSION
            and cached_source == source
            and int(payload.get("source_bytes") or -1) == stat.st_size
            and int(payload.get("source_mtime_ns") or -1) == stat.st_mtime_ns
            and payload.get("chapters")
        )

    def _reader_index(
        self,
        book_id: int,
        source: Path,
        book: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for root in (
            self.settings.shared_reader_index_root,
            self.settings.reader_index_root,
        ):
            cached = self._read_json(root / f"{int(book_id)}.json")
            if self._valid_reader_index(cached, source):
                return cached
        with self._index_locks_guard:
            lock = self._index_locks.setdefault(int(book_id), threading.Lock())
        with lock:
            cached = self._read_json(self._reader_index_path(book_id))
            if self._valid_reader_index(cached, source):
                return cached
            if source.stat().st_size > self.settings.max_index_source_bytes:
                raise InputError("超大正文的章节目录尚未预生成")
            category = _clean_text((book or {}).get("category"), 40) or ""
            if category == "轻小说":
                ln_dir = self._find_ln_source_dir(source)
                if ln_dir is not None:
                    title = _clean_text((book or {}).get("title"), 160) or ""
                    return self._build_ln_reader_index(book_id, source, ln_dir, title)
            return self._build_reader_index(book_id, source)

    @staticmethod
    def _sort_chapters_by_number(chapters: list[dict]) -> list[dict]:
        """Repair dense numeric chapter order without trusting noisy labels.

        Malformed zero labels and duplicate numbers are source-integrity
        failures. Preserve ambiguous catalogs in source order, but tolerate a
        tiny one-for-one label substitution in long books. That lets us repair
        an obviously displaced chapter block while keeping the duplicate pair
        stable and adjacent instead of letting one bad label disable ordering
        for thousands of otherwise sequential chapters.
        """
        if len(chapters) < 10:
            return chapters
        import re
        number_pattern = re.compile(
            r'第\s*(\d+)\s*[章节回话篇]|(?:chapter|ch\.?)\s*(\d+)',
            re.IGNORECASE,
        )
        numbered: list[tuple[int, int, dict]] = []
        for i, ch in enumerate(chapters):
            m = number_pattern.search(ch.get("label", "")) or number_pattern.search(ch.get("title", ""))
            if m:
                number = int(m.group(1) or m.group(2))
                if number <= 0:
                    return chapters
                numbered.append((number, i, ch))
        if len(numbered) < len(chapters) * 0.6:
            return chapters
        numbers = [number for number, _, _ in numbered]
        span = max(numbers) - min(numbers) + 1
        if span > len(numbers) * 1.25:
            return chapters
        unique_count = len(set(numbers))
        duplicate_count = len(numbers) - unique_count
        if duplicate_count:
            missing_count = span - unique_count
            allowed_substitutions = max(1, len(numbers) // 1_000)
            is_large_near_sequence = (
                len(numbered) >= 1_000
                and min(numbers) == 1
                and len(numbers) == span
                and duplicate_count == missing_count
                and duplicate_count <= allowed_substitutions
            )
            if not is_large_near_sequence:
                return chapters
        ordered = sorted(numbered, key=lambda item: item[0])
        if [item[1] for item in ordered] == [item[1] for item in numbered]:
            return chapters
        repaired = list(chapters)
        slots = [index for _, index, _ in numbered]
        for slot, (_, _, chapter) in zip(slots, ordered):
            repaired[slot] = chapter
        return repaired

    def reader_catalog(self, book_id: str) -> dict[str, Any]:
        book, source, catalog_id, public_id = self._source_for_book(book_id)
        index = self._reader_index(catalog_id, source, book)
        if len(index["chapters"]) > self.settings.max_chapter_count:
            raise InputError("章节目录数量超出在线阅读安全范围")
        chapters = [
            {
                "id": int(chapter["id"]),
                "label": _clean_text(chapter["label"], 40),
                "title": _clean_text(chapter["title"], 160),
                "byte_count": int(chapter["byte_count"]),
                "is_front_matter": (
                    _clean_text(chapter["label"], 40) == "序"
                    and _clean_text(chapter["title"], 160) == "作品信息"
                ),
            }
            for chapter in index["chapters"]
        ]
        chapters = self._sort_chapters_by_number(chapters)
        story_chapters = [
            chapter for chapter in chapters if not chapter["is_front_matter"]
        ]
        result: dict[str, Any] = {
            "book": {
                "public_id": public_id,
                "title": _clean_text(book.get("title"), 160),
                "author": _clean_text(book.get("author"), 100),
                "category": _clean_text(book.get("category"), 40),
                "source_bytes": int(book.get("source_bytes") or 0),
            },
            "chapter_count": len(story_chapters),
            "section_count": len(chapters),
            "first_chapter_id": (
                int(story_chapters[0]["id"]) if story_chapters else None
            ),
            "chapters": chapters,
        }
        if index.get("volumes"):
            result["volumes"] = [
                {
                    "id": int(vol["id"]),
                    "title": _clean_text(vol["title"], 160),
                    "chapter_ids": [int(cid) for cid in vol["chapter_ids"]],
                    "illustration_count": int(vol.get("illustration_count") or 0),
                    "cover_path": vol.get("cover_path") or "",
                    "illustration_paths": vol.get("illustration_paths") or [],
                }
                for vol in index["volumes"]
            ]
        return result

    def audiobook_cast_scan_plan(self, book_id: str) -> dict[str, Any]:
        """Return a stable whole-book revision and ordered chapter plan."""
        book, source, catalog_id, public_id = self._source_for_book(book_id)
        index = self._reader_index(catalog_id, source, book)
        chapters = self._sort_chapters_by_number(list(index["chapters"]))
        chapter_rows = [
            {
                "id": int(chapter["id"]),
                "byte_count": int(chapter.get("byte_count") or 0),
                "title": str(chapter.get("title") or ""),
            }
            for chapter in chapters
        ]
        identity = {
            "schema_version": int(index.get("schema_version") or 0),
            "source_bytes": int(index.get("source_bytes") or source.stat().st_size),
            "source_mtime_ns": int(
                index.get("source_mtime_ns") or source.stat().st_mtime_ns
            ),
            "chapters": chapter_rows,
        }
        content_revision = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "catalog_id": int(catalog_id),
            "book_public_id": public_id,
            "content_revision": content_revision,
            "chapter_ids": [item["id"] for item in chapter_rows],
        }

    def reader_chapter_count(self, book_id: str) -> int:
        """Return the exact bounded count without building a chapter response."""
        book, source, catalog_id, _public_id = self._source_for_book(book_id)
        index = self._reader_index(catalog_id, source, book)
        try:
            chapter_count = int(index["chapter_count"])
        except (KeyError, TypeError, ValueError):
            raise InputError("章节目录数量无效") from None
        if chapter_count <= 0 or chapter_count > self.settings.max_chapter_count:
            raise InputError("章节目录数量超出在线阅读安全范围")
        return chapter_count

    def reader_chapter(self, book_id: str, chapter_id: int) -> dict[str, Any]:
        if int(chapter_id) <= 0:
            raise InputError("无效章节编号")
        book, source, catalog_id, public_id = self._source_for_book(book_id)
        index = self._reader_index(catalog_id, source, book)
        chapter = next(
            (item for item in index["chapters"] if int(item["id"]) == int(chapter_id)),
            None,
        )
        if not chapter:
            raise NotFoundError("章节不存在")

        is_ln = index.get("source_type") == "light_novel"
        if is_ln:
            ln_dir = Path(str(index["source_dir"]))
            ch_path = (ln_dir / str(chapter["file_path"])).resolve()
            if not ch_path.is_file() or not _within(ch_path, ln_dir.resolve()):
                raise NotFoundError("章节文件不可用")
            byte_count = ch_path.stat().st_size
            if byte_count <= 0 or byte_count > self.settings.max_chapter_bytes:
                raise InputError("章节体积超出在线阅读安全范围")
            content = ch_path.read_text(encoding="utf-8", errors="replace")
        else:
            byte_count = int(chapter["end"]) - int(chapter["start"])
            if byte_count <= 0 or byte_count > self.settings.max_chapter_bytes:
                raise InputError("章节体积超出在线阅读安全范围")
            with source.open("rb") as handle:
                handle.seek(int(chapter["start"]))
                content = handle.read(byte_count).decode("utf-8", errors="replace")

        content = content.lstrip("\ufeff\r\n")
        if not is_ln:
            for _ in range(2):
                first, separator, remainder = content.partition("\n")
                if not separator or not (
                    READER_HEADING_LINE.fullmatch(first.strip())
                    or READER_NUMERIC_HEADING_LINE.fullmatch(first.strip())
                ):
                    break
                content = remainder.lstrip("\r\n")
        content = "\n".join(
            line
            for line in content.splitlines()
            if not READER_NOISE_LINE.fullmatch(line)
        )

        if is_ln:

            def _rewrite_illust(m):
                rel = m.group(1)
                parts = rel.split("/", 1)
                return f"[illustration:{parts[1] if len(parts) > 1 else rel}]"

            content = READER_LN_ILLUSTRATION_REF.sub(_rewrite_illust, content)

        chapters = self._sort_chapters_by_number(list(index["chapters"]))
        position = next(
            idx
            for idx, item in enumerate(chapters)
            if int(item["id"]) == int(chapter_id)
        )
        return {
            "id": int(chapter["id"]),
            "label": _clean_text(chapter["label"], 40),
            "title": _clean_text(chapter["title"], 160),
            "content": content.rstrip(),
            "word_count": len(re.sub(r"\s+", "", content)),
            "previous_id": int(chapters[position - 1]["id"]) if position > 0 else None,
            "next_id": int(chapters[position + 1]["id"])
            if position + 1 < len(chapters)
            else None,
            "book": {
                "public_id": public_id,
                "title": _clean_text(book.get("title"), 160),
                "author": _clean_text(book.get("author"), 100),
            },
        }

    def illustration_path(self, book_id: str, subpath: str) -> Path:
        book, source, catalog_id, public_id = self._source_for_book(book_id)
        index = self._reader_index(catalog_id, source, book)
        if index.get("source_type") != "light_novel":
            raise NotFoundError("此作品不支持插画浏览")
        ln_dir = Path(str(index["source_dir"])).resolve()
        raw = Path(subpath)
        if raw.is_absolute() or ".." in raw.parts:
            raise NotFoundError("插画路径无效")
        path = (ln_dir / raw).resolve()
        if (
            not _within(path, ln_dir)
            or not path.is_file()
            or path.suffix.lower() not in SAFE_COVER_SUFFIXES
        ):
            raise NotFoundError("插画文件不存在")
        return path

    def _deconstruction_names(self) -> list[str]:
        root = self.settings.deconstruction_root
        if not root.is_dir():
            return []
        return sorted(
            item.name
            for item in root.iterdir()
            if (
                item.is_dir()
                and not item.is_symlink()
                and not item.name.startswith(".")
                and _within(item.resolve(), root.resolve())
            )
        )

    @staticmethod
    def _deconstruction_title(name: str) -> str:
        return re.sub(r"__\d+$", "", name).strip()

    def _cover_url_for_public_id(self, book_id: str) -> str:
        if self._mysql is not None:
            try:
                descriptor = self._mysql.cover_descriptor(_decode_public_id(book_id))
            except (OSError, TypeError, ValueError):
                descriptor = None
            return _public_cover_url(
                book_id,
                descriptor.get("cover_object_key") if descriptor else None,
                descriptor.get("row_version") if descriptor else 0,
            )
        catalog_id = _local_catalog_id(book_id)
        has_real_cover = False
        if self.settings.cover_index_path.is_file():
            with self._ro_connection(
                self.settings.cover_index_path, immutable=True
            ) as conn:
                row = conn.execute(
                    "SELECT filename FROM covers WHERE catalog_id=? AND status='done'",
                    (catalog_id,),
                ).fetchone()
            if row:
                candidate = self.settings.cover_root / str(row["filename"])
                has_real_cover = candidate.is_file() and not candidate.is_symlink()
        return _public_cover_url(
            book_id,
            f"local-cover:{catalog_id}" if has_real_cover else None,
        )

    def submission_cover(self, title: str) -> dict[str, Any]:
        """Resolve an account contribution to its authoritative catalog cover."""
        clean_title = self._deconstruction_title(_clean_text(title, 160))
        if not clean_title:
            return {
                "book_id": None,
                "cover_url": OOHSTORY_DEFAULT_COVER_URL,
                "cover_is_default": True,
            }
        try:
            book_id = self._find_public_id(clean_title)
            if not book_id:
                raise NotFoundError("作品不存在")
            book = self.get_book(book_id)
            return {
                "book_id": book_id,
                "cover_url": str(book.get("cover_url") or OOHSTORY_DEFAULT_COVER_URL),
                "cover_is_default": bool(book.get("cover_is_default", True)),
            }
        except (LibraryError, OSError, TypeError, ValueError):
            return {
                "book_id": None,
                "cover_url": OOHSTORY_DEFAULT_COVER_URL,
                "cover_is_default": True,
            }

    def list_deconstructions(self) -> list[dict[str, Any]]:
        items = []
        for name in self._deconstruction_names():
            root = (self.settings.deconstruction_root / name).resolve()
            docs = []
            if self._golden_three_items(root):
                docs.append({"filename": "黄金三章", "label": "黄金三章"})
            docs.extend(
                [
                    {"filename": filename, "label": label}
                    for filename, label in DOCUMENTS
                    if self._safe_deconstruction_file(root, filename) is not None
                ]
            )
            progress_path = self._safe_deconstruction_file(root, "_progress.md")
            progress = self._read_progress(progress_path)
            title = self._deconstruction_title(name)
            book_id = self._find_public_id(title)
            items.append(
                {
                    "slug": name,
                    "title": title,
                    "public_id": book_id,
                    "cover_url": (
                        self._cover_url_for_public_id(book_id)
                        if book_id is not None
                        else None
                    ),
                    "documents": docs,
                    **self._deconstruction_metadata(root),
                    **progress,
                }
            )
        return items

    def _golden_three_items(self, root: Path) -> list[dict[str, Any]]:
        items = []
        for relative in GOLDEN_THREE_FILES:
            path = self._safe_deconstruction_file(root, relative)
            if path is None:
                return []
            items.append(
                {
                    "filename": path.name,
                    "label": path.stem,
                    "type": "file",
                }
            )
        return items

    def _deconstruction_metadata(self, root: Path) -> dict[str, Any]:
        path = self._safe_deconstruction_file(root, "_submission.json")
        if path is None:
            return {"contributor_username": "", "contributed_by_user": False}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"contributor_username": "", "contributed_by_user": False}
        username = _clean_text(payload.get("contributor_username"), 40)
        return {
            "contributor_username": username,
            "contributed_by_user": bool(username),
        }

    @staticmethod
    def _safe_deconstruction_file(root: Path, filename: str) -> Path | None:
        candidate = root / filename
        if candidate.is_symlink():
            return None
        path = candidate.resolve()
        if not _within(path, root) or not path.is_file():
            return None
        return path

    def _find_public_id(self, title: str) -> str | None:
        if self._mysql is not None:
            value = self._mysql.find_public_id(title)
            return _encode_public_id(value) if value is not None else None
        with self._ro_connection(self.settings.catalog_path) as conn:
            row = conn.execute(
                """
                SELECT id FROM books
                WHERE title=? AND status='done'
                  AND NULLIF(TRIM(COALESCE(output_path,'')), '') IS NOT NULL
                ORDER BY bytes DESC LIMIT 1
                """,
                (title,),
            ).fetchone()
        return _local_public_id(int(row["id"])) if row else None

    @staticmethod
    def _read_progress(path: Path | None) -> dict[str, Any]:
        if path is None or not path.is_file() or path.is_symlink():
            return {"progress": "", "completed_chapters": 0, "total_chapters": 0}
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(80_000)
        stage_two_pairs: list[tuple[int, int]] = []
        for line in text.splitlines():
            if not line.lstrip().startswith("|"):
                continue
            cells = [part.strip() for part in line.strip().strip("|").split("|")]
            if not cells or not re.match(
                r"^(?:stage\s*)?2(?:\s|$|[：:（(])",
                cells[0],
                re.IGNORECASE,
            ):
                continue
            for done, total in re.findall(
                r"(\d+)\s*/\s*(\d+)",
                " | ".join(cells[1:]),
            ):
                completed_value = int(done)
                total_value = int(total)
                if total_value > 0 and completed_value <= total_value:
                    stage_two_pairs.append((completed_value, total_value))

        # Progress is a contract field, not a statistic to guess.  Never fall
        # back to arbitrary fractions elsewhere in the file: chapter-title
        # recovery notes (870/1019) and report link checks (15/15) are not
        # Stage 2 coverage.
        completed, total = stage_two_pairs[-1] if stage_two_pairs else (0, 0)
        percent = round(completed / total * 100, 1) if total else 0
        return {
            "progress": f"{completed}/{total}" if total else "",
            "progress_percent": percent,
            "completed_chapters": completed,
            "total_chapters": total,
        }

    def get_deconstruction(self, slug: str) -> dict[str, Any]:
        if slug not in self._deconstruction_names():
            raise NotFoundError("拆书档案不存在")
        root = (self.settings.deconstruction_root / slug).resolve()
        if not _within(root, self.settings.deconstruction_root.resolve()):
            raise UnsafePathError("拆书档案路径无效")
        base = next(
            item for item in self.list_deconstructions() if item["slug"] == slug
        )
        documents = []
        golden_items = self._golden_three_items(root)
        if golden_items:
            documents.append(
                {
                    "filename": "黄金三章",
                    "label": "黄金三章",
                    "subdirectory": "章节",
                    "items": golden_items,
                }
            )
        for filename, label in DOCUMENTS:
            path = self._safe_deconstruction_file(root, filename)
            if path is not None:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    content = handle.read(self.settings.max_report_chars)
                documents.append(
                    {
                        "filename": filename,
                        "label": label,
                        "content": content,
                    }
                )
        base["documents"] = documents
        base["has_chapter_summaries"] = (
            self._safe_deconstruction_file(root, "_章节摘要汇总.md") is not None
        )
        base["subdirectories"] = self._list_subdirectories(root)
        return base

    SUBDIR_LABELS: dict[str, str] = {
        "剧情": "剧情",
        "角色": "角色",
        "设定": "设定",
        "章节": "章节摘要",
    }

    def _list_subdirectories(self, root: Path) -> list[dict[str, Any]]:
        resolved_root = root.resolve()
        result = []
        for subdir_name, label in self.SUBDIR_LABELS.items():
            subdir = root / subdir_name
            if not subdir.is_dir() or subdir.is_symlink():
                continue
            if not _within(subdir.resolve(), resolved_root):
                continue
            entries = self._scan_subdir(subdir, resolved_root)
            if subdir_name == "章节":
                entries = [
                    entry
                    for entry in entries
                    if entry.get("type") == "directory"
                    or str(entry.get("filename") or "").endswith("_摘要.md")
                ]
            if entries:
                result.append({"name": subdir_name, "label": label, "items": entries})
        return result

    def _scan_subdir(self, directory: Path, root: Path) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for item in sorted(directory.iterdir(), key=self._deconstruction_sort_key):
            if item.name.startswith(".") or item.name.startswith("_"):
                continue
            if item.is_symlink():
                continue
            if not _within(item.resolve(), root):
                continue
            if item.is_file() and item.suffix == ".md":
                entries.append(
                    {
                        "filename": item.name,
                        "label": item.stem,
                        "type": "file",
                    }
                )
            elif item.is_dir():
                children = self._scan_subdir(item, root)
                if children:
                    entries.append(
                        {
                            "name": item.name,
                            "label": item.name,
                            "type": "directory",
                            "items": children,
                        }
                    )
        return entries

    @staticmethod
    def _deconstruction_sort_key(path: Path) -> tuple[Any, ...]:
        normalized = unicodedata.normalize("NFKC", path.stem)
        chapter = re.match(r"^第\s*(\d+)\s*章(?:[_\s-]*(摘要|深度拆解))?", normalized)
        if chapter:
            kind_order = 0 if chapter.group(2) == "深度拆解" else 1
            return (0, int(chapter.group(1)), kind_order, normalized.casefold())
        parts = re.split(r"(\d+)", normalized.casefold())
        natural = tuple(int(part) if part.isdigit() else part for part in parts)
        return (1, *natural)

    def get_deconstruction_file(self, slug: str, subpath: str) -> dict[str, Any]:
        if slug not in self._deconstruction_names():
            raise NotFoundError("拆书档案不存在")
        root = (self.settings.deconstruction_root / slug).resolve()
        if not _within(root, self.settings.deconstruction_root.resolve()):
            raise UnsafePathError("拆书档案路径无效")
        candidate = (root / subpath).resolve()
        if not _within(candidate, root):
            raise UnsafePathError("文件路径越界")
        if candidate.is_symlink() or not candidate.is_file():
            raise NotFoundError("文件不存在")
        if candidate.suffix != ".md":
            raise UnsafePathError("仅支持 Markdown 文件")
        with candidate.open("r", encoding="utf-8", errors="replace") as f:
            content = f.read(self.settings.max_report_chars)
        return {
            "filename": candidate.name,
            "label": candidate.stem,
            "content": content,
        }
