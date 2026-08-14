#!/usr/bin/env python3
"""Synchronize real source book covers with the local catalog.

Only a cover parsed from the catalog row's own detail URL is accepted. The
catalog id, source id, title and author are persisted together, so a stale or
mis-associated image can never be returned for another book.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from txt80_crawler import (  # noqa: E402
    HttpClient,
    clean_text,
    normalize_title,
    scalable_identity_part,
    source_id,
)

from project_paths import APP_ROOT  # noqa: E402
sys.path.insert(0, str(APP_ROOT / "src"))
from oohstory_library.services.ixdzs_provider import AuthorizedIxdzsProvider  # noqa: E402
from oohstory_library.services.cover_failure_policy import (  # noqa: E402
    MAX_SOURCE_COVER_ATTEMPTS,
    is_missing_cover_placeholder_image,
    is_missing_cover_placeholder_sha256,
    should_generate_ai_fallback,
)
from oohstory_library.services.default_cover import (  # noqa: E402
    materialize_default_cover,
)
from oohstory_library.services.library_database import LibraryInfrastructureSettings  # noqa: E402
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402
from oohstory_library.services.linovelib_provider import LinovelibProvider  # noqa: E402
from oohstory_library.services.shubaow_provider import AuthorizedShubaowProvider  # noqa: E402


LIBRARY_ROOT = APP_ROOT / "electronic-library"
CATALOG_PATH = LIBRARY_ROOT / "catalog.sqlite3"
COVER_ROOT = LIBRARY_ROOT / "封面"
INDEX_PATH = Path(
    os.getenv(
        "WEBNOVEL_COVER_INDEX_PATH",
        str(LIBRARY_ROOT / "全局索引" / "cover_index.sqlite3"),
    )
).expanduser().resolve()
TXT80_IMAGE_HOSTS = {"img.txt80.cc", "www.txt80.cc", "txt80.cc"}
XBIQUGE_HOSTS = {"www.xbiquge.info", "xbiquge.info"}
IXDZS_PAGE_HOSTS = {"ixdzs8.com"}
IXDZS_IMAGE_SUFFIXES = (".ixdzs.com", ".ixdzs8.com")
IXDZS_PROVIDER = AuthorizedIxdzsProvider()
SHUBAOW_PAGE_HOSTS = {"www.shubaow.org", "shubaow.org"}
SHUBAOW_IMAGE_HOSTS = {
    "pic.shubaow.org",
    "www.shubaow.org",
    "shubaow.org",
}
SHUBAOW_PROVIDER = AuthorizedShubaowProvider()
LINOVELIB_PAGE_HOSTS = {"www.linovelib.com", "linovelib.com"}
LINOVELIB_IMAGE_HOSTS = {
    "www.linovelib.com",
    "linovelib.com",
    "www.bilinovel.com",
    "bilinovel.com",
}
LINOVELIB_PROVIDER = LinovelibProvider()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;
CREATE TABLE IF NOT EXISTS covers (
  catalog_id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT NOT NULL,
  detail_url TEXT NOT NULL,
  cover_url TEXT,
  filename TEXT,
  sha256 TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_covers_filename
  ON covers(filename) WHERE filename IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_covers_status ON covers(status, attempts);
CREATE TABLE IF NOT EXISTS clean_cover_jobs (
  catalog_id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  original_filename TEXT,
  replacement_url TEXT,
  replacement_filename TEXT,
  verification_source TEXT,
  last_error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  ai_session_id TEXT,
  source_width INTEGER,
  source_height INTEGER,
  generated_width INTEGER,
  generated_height INTEGER,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clean_cover_jobs_status
  ON clean_cover_jobs(status, catalog_id);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def finish_mysql_cover(
    runtime: MySQLLibraryRuntime,
    row: dict[str, Any],
    *,
    result: dict[str, Any] | None = None,
    error: str = "",
    ai_fallback: bool = False,
    max_attempts: int = 3,
) -> bool:
    """Persist one result without letting a transient row lock kill watch mode."""

    retryable = {1205, 1213, 2006, 2013}
    for attempt in range(max(1, int(max_attempts))):
        try:
            runtime.finish_cover_job(
                row,
                result=result,
                error=error,
                ai_fallback=ai_fallback,
            )
            return True
        except Exception as exc:
            code = exc.args[0] if exc.args else None
            if code not in retryable or attempt + 1 >= max_attempts:
                print(
                    {
                        "catalog_id": int(row["catalog_id"]),
                        "status": "persist_deferred",
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    },
                    flush=True,
                )
                return False
            time.sleep(2**attempt)
    return False


def connect_index() -> sqlite3.Connection:
    COVER_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(INDEX_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(clean_cover_jobs)")
    }
    additions = {
        "attempts": "INTEGER NOT NULL DEFAULT 0",
        "ai_session_id": "TEXT",
        "source_width": "INTEGER",
        "source_height": "INTEGER",
        "generated_width": "INTEGER",
        "generated_height": "INTEGER",
    }
    for name, declaration in additions.items():
        if name not in columns:
            conn.execute(
                f"ALTER TABLE clean_cover_jobs ADD COLUMN {name} {declaration}"
            )
    conn.commit()
    return conn


def enqueue_sqlite_generated_cover(
    conn: sqlite3.Connection,
    row: sqlite3.Row | dict[str, Any],
    *,
    reason: str,
) -> None:
    """Queue one missing real cover for idempotent title-based generation."""

    conn.execute(
        """
        INSERT INTO clean_cover_jobs (
          catalog_id,source_id,title,author,status,original_filename,
          attempts,last_error,updated_at
        ) VALUES (?,?,?,?, 'generate_pending',NULL,0,?,?)
        ON CONFLICT(catalog_id) DO UPDATE SET
          source_id=excluded.source_id,title=excluded.title,
          author=excluded.author,
          status=CASE
            WHEN clean_cover_jobs.status IN (
              'done','processing','pending','manual_pending',
              'generate_pending','failed'
            ) THEN clean_cover_jobs.status
            ELSE 'generate_pending'
          END,
          original_filename=CASE
            WHEN clean_cover_jobs.status IN (
              'done','processing','pending','manual_pending',
              'generate_pending','failed'
            ) THEN clean_cover_jobs.original_filename
            ELSE NULL
          END,
          attempts=CASE
            WHEN clean_cover_jobs.status IN (
              'done','processing','pending','manual_pending',
              'generate_pending','failed'
            ) THEN clean_cover_jobs.attempts
            ELSE 0
          END,
          last_error=CASE
            WHEN clean_cover_jobs.status IN (
              'done','processing','pending','manual_pending',
              'generate_pending','failed'
            ) THEN clean_cover_jobs.last_error
            ELSE excluded.last_error
          END,
          updated_at=excluded.updated_at
        """,
        (
            int(row["catalog_id"]),
            str(row["source_id"]),
            str(row["title"]),
            str(row["author"]),
            f"原站封面安全失败，转 AI 文生图：{str(reason)[:1800]}",
            now(),
        ),
    )


def seed_index(*, library_id: str = "", source: str = "") -> int:
    if LibraryInfrastructureSettings.from_env().catalog_backend == "mysql":
        runtime = MySQLLibraryRuntime()
        return runtime.seed_cover_jobs(
            library_id=library_id,
            source=source,
        ) + runtime.seed_terminal_cover_fallback_jobs(
            library_id=library_id,
            source=source,
        )
    catalog_uri = f"{CATALOG_PATH.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(catalog_uri, uri=True, timeout=30) as catalog:
        catalog.row_factory = sqlite3.Row
        rows = catalog.execute(
            """
            SELECT id AS catalog_id, CAST(source_id AS TEXT) AS source_id,
                   COALESCE(title, '') AS title,
                   COALESCE(author, '') AS author, detail_url
            FROM books
            WHERE status != 'duplicate'
              AND (
                (
                  detail_url LIKE 'https://www.txt80.cc/%/txt%.html'
                  AND CAST(source_id AS TEXT) GLOB '[0-9]*'
                )
                OR (
                  CAST(source_id AS TEXT) LIKE 'xbiquge-%'
                  AND (
                    detail_url LIKE 'https://www.xbiquge.info/%'
                    OR detail_url LIKE 'https://xbiquge.info/%'
                  )
                )
                OR (
                  status='done'
                  AND CAST(source_id AS TEXT) LIKE 'ixdzs-%'
                  AND detail_url LIKE 'https://ixdzs8.com/read/%/'
                )
                OR (
                  status='done'
                  AND CAST(source_id AS TEXT) LIKE 'shubaow-%'
                  AND (
                    detail_url LIKE 'https://www.shubaow.org/book%.html'
                    OR detail_url LIKE 'https://shubaow.org/book%.html'
                  )
                )
              )
            """
        ).fetchall()
    if source:
        rows = [row for row in rows if cover_source(row) == source]
    with connect_index() as conn:
        before = conn.total_changes
        for row in rows:
            values = dict(row)
            current = conn.execute(
                "SELECT source_id,title,author,detail_url FROM covers WHERE catalog_id=?",
                (values["catalog_id"],),
            ).fetchone()
            identity = (
                values["source_id"], values["title"], values["author"],
                values["detail_url"],
            )
            if current and tuple(current) == identity:
                continue
            conn.execute(
                """
                INSERT INTO covers
                  (catalog_id,source_id,title,author,detail_url,status,updated_at)
                VALUES (?,?,?,?,?,'pending',?)
                ON CONFLICT(catalog_id) DO UPDATE SET
                  source_id=excluded.source_id,title=excluded.title,
                  author=excluded.author,detail_url=excluded.detail_url,
                  cover_url=NULL,filename=NULL,sha256=NULL,status='pending',
                  attempts=0,last_error=NULL,updated_at=excluded.updated_at
                """,
                (values["catalog_id"], *identity, now()),
            )
        conn.commit()
        return conn.total_changes - before


def txt80_page_identity(soup: BeautifulSoup) -> tuple[str, str]:
    title_node = soup.select_one("div.detail dd.bt h2")
    title = normalize_title(title_node.get_text(" ", strip=True) if title_node else "")
    author = ""
    for node in soup.select("div.detail dd.db"):
        text = clean_text(node.get_text(" ", strip=True))
        if text.startswith("小说作者"):
            author = clean_text(re.split(r"[：:]", text, maxsplit=1)[-1])
            break
    return title, author


def meta_content(soup: BeautifulSoup, property_name: str) -> str:
    node = soup.select_one(f'meta[property="{property_name}"]')
    return clean_text(str(node.get("content") or "")) if node else ""


def xbiquge_page_identity(soup: BeautifulSoup) -> tuple[str, str]:
    title = normalize_title(meta_content(soup, "og:novel:book_name"))
    author = clean_text(meta_content(soup, "og:novel:author"))
    return title, author


def cover_source(row: Any) -> str:
    """Return the one trusted source encoded by both id and detail URL."""

    detail_url = str(row["detail_url"])
    expected_id = str(row["source_id"])
    parsed = urlparse(detail_url)
    if (
        parsed.hostname in TXT80_IMAGE_HOSTS
        and expected_id.isdigit()
        and source_id(detail_url) == expected_id
    ):
        return "txt80"
    if parsed.hostname in XBIQUGE_HOSTS and expected_id.startswith("xbiquge-"):
        remote_id = expected_id.removeprefix("xbiquge-")
        match = re.fullmatch(r"/(\d+)/(\d+)/", parsed.path)
        return "xbiquge" if match and match.group(2) == remote_id else ""
    if parsed.hostname in IXDZS_PAGE_HOSTS and expected_id.startswith("ixdzs-"):
        remote_id = expected_id.removeprefix("ixdzs-")
        try:
            IXDZS_PROVIDER.validate_source_ref(remote_id, parsed.path)
        except ValueError:
            return ""
        return "ixdzs"
    if (
        parsed.hostname in SHUBAOW_PAGE_HOSTS
        and expected_id.startswith("shubaow-")
    ):
        remote_id = expected_id.removeprefix("shubaow-")
        try:
            SHUBAOW_PROVIDER.validate_source_ref(remote_id, parsed.path)
        except ValueError:
            return ""
        return "shubaow"
    if (
        parsed.hostname in LINOVELIB_PAGE_HOSTS
        and expected_id.startswith("linovelib-")
    ):
        remote_id = expected_id.removeprefix("linovelib-")
        try:
            LINOVELIB_PROVIDER.validate_source_ref(remote_id, parsed.path)
        except ValueError:
            return ""
        return "linovelib"
    return ""


def image_extension(data: bytes, content_type: str) -> str:
    content_type = content_type.lower()
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError(f"响应不是支持的图片格式：{content_type or 'unknown'}")


def fetch_cover(client: HttpClient, row: sqlite3.Row) -> dict[str, Any]:
    detail_url = str(row["detail_url"])
    expected_id = str(row["source_id"])
    parsed_detail = urlparse(detail_url)
    provider = cover_source(row)
    allowed_image_hosts: set[str]
    if provider == "txt80":
        allowed_image_hosts = TXT80_IMAGE_HOSTS
    elif provider == "xbiquge":
        allowed_image_hosts = XBIQUGE_HOSTS
    elif provider == "ixdzs":
        allowed_image_hosts = IXDZS_PAGE_HOSTS
    elif provider == "shubaow":
        allowed_image_hosts = SHUBAOW_IMAGE_HOSTS
    elif provider == "linovelib":
        allowed_image_hosts = LINOVELIB_IMAGE_HOSTS
    else:
        raise ValueError("目录来源尚未配置可信封面适配器")

    image_url = ""
    image = None
    if provider == "ixdzs":
        detail = IXDZS_PROVIDER.detail(
            expected_id.removeprefix("ixdzs-"),
            parsed_detail.path,
        )
        page_title = normalize_title(str(detail.get("title") or ""))
        page_author = clean_text(str(detail.get("author") or ""))
        image_url = str(detail.get("cover_url") or "")
    elif provider == "shubaow":
        detail = SHUBAOW_PROVIDER.detail(
            expected_id.removeprefix("shubaow-"),
            parsed_detail.path,
            include_chapters=False,
        )
        page_title = normalize_title(str(detail.get("title") or ""))
        page_author = clean_text(str(detail.get("author") or ""))
        image_url = str(detail.get("cover_url") or "")
    elif provider == "linovelib":
        detail = LINOVELIB_PROVIDER.detail(
            expected_id.removeprefix("linovelib-"),
            parsed_detail.path,
            include_chapters=False,
        )
        page_title = normalize_title(str(detail.get("title") or ""))
        page_author = clean_text(str(detail.get("author") or ""))
        image_url = str(detail.get("cover_url") or "")
    else:
        response = client.get(detail_url)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        if provider == "txt80":
            page_title, page_author = txt80_page_identity(soup)
            image = soup.select_one("div.detail img.pics3[src], img.pics3[src]")
        else:
            page_title, page_author = xbiquge_page_identity(soup)
            image_url = meta_content(soup, "og:image")
            image = (
                soup.select_one("div.book_info img[src]")
                if not image_url
                else None
            )
    if scalable_identity_part(str(row["title"])) != scalable_identity_part(
        page_title
    ):
        raise ValueError(f"详情页书名不一致：{page_title or '空'}")
    catalog_author = clean_text(str(row["author"]))
    if (
        catalog_author
        and page_author
        and scalable_identity_part(catalog_author)
        != scalable_identity_part(page_author)
    ):
        raise ValueError(f"详情页作者不一致：{page_author}")
    if not image_url and image:
        image_url = str(image.get("src") or "")
    if not image_url:
        raise ValueError("详情页没有真实封面图")
    cover_url = urljoin(detail_url, image_url)
    parsed = urlparse(cover_url)
    trusted_image_host = (
        parsed.hostname in allowed_image_hosts
        or (
            provider == "ixdzs"
            and any(
                str(parsed.hostname or "").endswith(suffix)
                for suffix in IXDZS_IMAGE_SUFFIXES
            )
        )
    )
    if parsed.scheme != "https" or not trusted_image_host:
        raise ValueError("封面图片不在当前授权来源的 HTTPS 主机内")
    if provider == "shubaow":
        data, content_type = SHUBAOW_PROVIDER.download_cover(cover_url)
    elif provider == "linovelib":
        data, content_type = LINOVELIB_PROVIDER.download_cover(cover_url)
    else:
        image_response = client.get(cover_url)
        data = image_response.content
        content_type = image_response.headers.get("Content-Type", "")
    if len(data) < 1024 or len(data) > 12 * 1024 * 1024:
        raise ValueError(f"封面文件大小异常：{len(data)}")
    extension = image_extension(data, content_type)
    digest = hashlib.sha256(data).hexdigest()
    if is_missing_cover_placeholder_image(data, sha256=digest):
        default = materialize_default_cover(
            cover_root=COVER_ROOT,
            catalog_id=int(row["catalog_id"]),
        )
        return {
            "catalog_id": int(row["catalog_id"]),
            "cover_url": str(default["cover_url"]),
            "filename": str(default["filename"]),
            "sha256": str(default["sha256"]),
            "bytes": int(default["bytes"]),
            "content_type": str(default["content_type"]),
            "missing_placeholder": True,
            "missing_reason": (
                "原站返回缺失封面指纹，已改用 OOHStory 默认封面并排队重绘："
                f"{digest}"
            ),
            "needs_clean_replacement": False,
        }
    filename = f"{int(row['catalog_id'])}-{expected_id}-{digest[:16]}{extension}"
    target = COVER_ROOT / filename
    if not target.exists():
        temp = target.with_suffix(target.suffix + ".part")
        temp.write_bytes(data)
        temp.replace(target)
    return {
        "catalog_id": int(row["catalog_id"]),
        "cover_url": cover_url,
        "filename": filename,
        "sha256": digest,
        "needs_clean_replacement": provider == "txt80",
    }


def run_batch(
    *,
    limit: int,
    workers: int,
    delay: float,
    seed: bool = True,
    library_id: str = "",
    source: str = "",
) -> dict[str, int]:
    if seed:
        seed_index(library_id=library_id, source=source)
    settings = LibraryInfrastructureSettings.from_env()
    runtime = MySQLLibraryRuntime() if settings.catalog_backend == "mysql" else None
    if runtime is not None:
        rows = runtime.claim_cover_jobs(
            limit=max(1, limit),
            worker_id=runtime.worker_id(
                f"cover-sync-{source or 'all'}"
            ),
            library_id=library_id,
            source=source,
        )
    else:
        source_filter_map = {
            "txt80": """
              detail_url LIKE 'https://www.txt80.cc/%/txt%.html'
              AND source_id GLOB '[0-9]*'
            """,
            "xbiquge": """
              source_id LIKE 'xbiquge-%'
              AND (
                detail_url LIKE 'https://www.xbiquge.info/%'
                OR detail_url LIKE 'https://xbiquge.info/%'
              )
            """,
            "ixdzs": """
              source_id LIKE 'ixdzs-%'
              AND detail_url LIKE 'https://ixdzs8.com/read/%/'
            """,
            "shubaow": """
              source_id LIKE 'shubaow-%'
              AND (
                detail_url LIKE 'https://www.shubaow.org/book%.html'
                OR detail_url LIKE 'https://shubaow.org/book%.html'
              )
            """,
            "linovelib": """
              source_id LIKE 'linovelib-%'
              AND (
                detail_url LIKE 'https://www.linovelib.com/novel/%.html'
                OR detail_url LIKE 'https://linovelib.com/novel/%.html'
              )
            """,
        }
        source_filter = (
            f" AND ({source_filter_map[source]})" if source else ""
        )
        with connect_index() as conn:
            rows = conn.execute(
            f"""
            SELECT * FROM covers
            WHERE status IN ('pending','failed') AND attempts < 5
              {source_filter}
              AND (
                (
                  detail_url LIKE 'https://www.txt80.cc/%/txt%.html'
                  AND source_id GLOB '[0-9]*'
                )
                OR (
                  source_id LIKE 'xbiquge-%'
                  AND (
                    detail_url LIKE 'https://www.xbiquge.info/%'
                    OR detail_url LIKE 'https://xbiquge.info/%'
                  )
                )
                OR (
                  source_id LIKE 'ixdzs-%'
                  AND detail_url LIKE 'https://ixdzs8.com/read/%/'
                )
                OR (
                  source_id LIKE 'shubaow-%'
                  AND (
                    detail_url LIKE 'https://www.shubaow.org/book%.html'
                    OR detail_url LIKE 'https://shubaow.org/book%.html'
                  )
                )
                OR (
                  source_id LIKE 'linovelib-%'
                  AND (
                    detail_url LIKE 'https://www.linovelib.com/novel/%.html'
                    OR detail_url LIKE 'https://linovelib.com/novel/%.html'
                  )
                )
              )
            ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, catalog_id DESC
            LIMIT ?
            """,
            (max(1, limit),),
            ).fetchall()
    if not rows:
        return {"queued": 0, "done": 0, "failed": 0}
    client = HttpClient(delay=max(delay, 0.03), timeout=60)
    done = failed = 0
    with ThreadPoolExecutor(max_workers=min(max(workers, 1), 12)) as pool:
        futures = {pool.submit(fetch_cover, client, row): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
                if runtime is not None:
                    if finish_mysql_cover(runtime, row, result=result):
                        done += 1
                    else:
                        failed += 1
                    continue
                with connect_index() as conn:
                    conn.execute(
                        """
                        UPDATE covers SET cover_url=?,filename=?,sha256=?,
                          status='done',attempts=attempts+1,last_error=NULL,
                          updated_at=? WHERE catalog_id=?
                        """,
                        (
                            result["cover_url"], result["filename"],
                            result["sha256"], now(), result["catalog_id"],
                        ),
                    )
                    if result.get("missing_placeholder"):
                        enqueue_sqlite_generated_cover(
                            conn,
                            row,
                            reason=str(result.get("missing_reason") or ""),
                        )
                    elif result["needs_clean_replacement"]:
                        conn.execute(
                            """
                            INSERT INTO clean_cover_jobs
                              (catalog_id,source_id,title,author,status,
                               original_filename,updated_at)
                            VALUES (?,?,?,?, 'pending',?,?)
                            ON CONFLICT(catalog_id) DO UPDATE SET
                              source_id=excluded.source_id,
                              title=excluded.title,
                              author=excluded.author,
                              status=CASE
                                WHEN clean_cover_jobs.original_filename
                                     != excluded.original_filename
                                THEN 'pending'
                                ELSE clean_cover_jobs.status
                              END,
                              original_filename=excluded.original_filename,
                              replacement_url=CASE
                                WHEN clean_cover_jobs.original_filename
                                     != excluded.original_filename
                                THEN NULL
                                ELSE clean_cover_jobs.replacement_url
                              END,
                              replacement_filename=CASE
                                WHEN clean_cover_jobs.original_filename
                                     != excluded.original_filename
                                THEN NULL
                                ELSE clean_cover_jobs.replacement_filename
                              END,
                              verification_source=CASE
                                WHEN clean_cover_jobs.original_filename
                                     != excluded.original_filename
                                THEN NULL
                                ELSE clean_cover_jobs.verification_source
                              END,
                              attempts=CASE
                                WHEN clean_cover_jobs.original_filename
                                     != excluded.original_filename
                                THEN 0
                                ELSE clean_cover_jobs.attempts
                              END,
                              last_error=NULL,
                              updated_at=excluded.updated_at
                            """,
                            (
                                result["catalog_id"], str(row["source_id"]),
                                str(row["title"]), str(row["author"]),
                                result["filename"], now(),
                            ),
                        )
                    conn.commit()
                done += 1
            except Exception as exc:
                message = f"{type(exc).__name__}: {str(exc)[:300]}"
                next_attempt = int(dict(row).get("attempts") or 0) + 1
                ai_fallback = should_generate_ai_fallback(
                    message,
                    attempts=next_attempt,
                    max_attempts=MAX_SOURCE_COVER_ATTEMPTS,
                )
                if runtime is not None:
                    if ai_fallback:
                        try:
                            default = materialize_default_cover(
                                cover_root=COVER_ROOT,
                                catalog_id=int(row["catalog_id"]),
                            )
                            persisted = finish_mysql_cover(
                                runtime,
                                row,
                                result={
                                    **default,
                                    "missing_placeholder": True,
                                    "missing_reason": (
                                        "原站没有可用封面，已改用 OOHStory "
                                        f"默认封面并排队重绘：{message}"
                                    ),
                                },
                            )
                        except Exception as default_error:
                            print(
                                {
                                    "catalog_id": int(row["catalog_id"]),
                                    "status": "default_cover_failed",
                                    "error": (
                                        f"{type(default_error).__name__}: "
                                        f"{str(default_error)[:300]}"
                                    ),
                                },
                                flush=True,
                            )
                        else:
                            if persisted:
                                done += 1
                            else:
                                failed += 1
                            continue
                    finish_mysql_cover(
                        runtime,
                        row,
                        error=message,
                        ai_fallback=ai_fallback,
                    )
                    failed += 1
                    continue
                with connect_index() as conn:
                    conn.execute(
                        """
                        UPDATE covers SET status=?,attempts=attempts+1,
                          last_error=?,updated_at=? WHERE catalog_id=?
                        """,
                        (
                            "ai_fallback" if ai_fallback else "failed",
                            message,
                            now(),
                            row["catalog_id"],
                        ),
                    )
                    if ai_fallback:
                        enqueue_sqlite_generated_cover(
                            conn,
                            row,
                            reason=message,
                        )
                    conn.commit()
                failed += 1
    return {"queued": len(rows), "done": done, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description="同步各授权来源的真实小说封面")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--delay", type=float, default=0.08)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--seed-interval", type=int, default=3600)
    parser.add_argument(
        "--library",
        choices=("all", "local", "fanqie"),
        default="all",
    )
    parser.add_argument(
        "--source",
        choices=(
            "all", "txt80", "xbiquge", "ixdzs", "shubaow", "linovelib"
        ),
        default="all",
        help="只播种并认领指定真实来源；线上生产 worker 必须显式指定",
    )
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
            workers=args.workers,
            delay=args.delay,
            seed=seed_due,
            library_id="" if args.library == "all" else args.library,
            source="" if args.source == "all" else args.source,
        )
        if seed_due:
            last_seed_at = time.monotonic()
        print(result, flush=True)
        if not args.watch:
            return 0 if result["failed"] == 0 else 1
        time.sleep(5 if result["queued"] else max(args.interval, 30))


if __name__ == "__main__":
    raise SystemExit(main())
