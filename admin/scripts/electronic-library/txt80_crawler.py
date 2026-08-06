#!/usr/bin/env python3
"""Resumable local-library crawler plus authorized remote-source importers.

Pipeline:
  homepage category discovery -> every category/page -> de-duplicated catalog
  -> book detail -> download page -> UTF-8 TXT file

Every page/book transition is persisted in SQLite. Re-running the command
continues unfinished work and never downloads an already completed file.

Remote expansion supports Project Gutenberg public-domain TXT and the
site-owner-authorized z-library.im adapter. Z-Library access is explicit,
rate-limited, browser-verified, and limited to TXT/EPUB under the size cap.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "https://www.txt80.cc"
from project_paths import APP_ROOT  # noqa: E402
ROOT = APP_ROOT / "electronic-library" / "txt80"
BOOK_ROOT = ROOT / "书籍"
DB_PATH = ROOT / "catalog.sqlite3"
CSV_PATH = ROOT / "catalog.csv"
USER_AGENT = (
    "Mozilla/5.0 (compatible; Txt80LibraryBackup/1.0; "
    "+https://www.txt80.cc/; site-owner-test)"
)
LEGACY_LIBRARY_NOTICE = "本书为八零电子书(txt8080.com)"
PUBLIC_DOMAIN = os.getenv("OOHSTORY_PUBLIC_DOMAIN", "reader.example.com").strip() or "reader.example.com"
LEGACY_REBRANDED_NOTICE = f"本书为八零电子书({PUBLIC_DOMAIN})"
PUBLIC_LIBRARY_NOTICE = f"本书为Ooh！好故事({PUBLIC_DOMAIN})"
IXDZS_PROMOTIONAL_NOTICE = (
    "爱下电子书Txt版阅读,下载和分享更多电子书请访问，"
    "简体:https://ixdzs8.com,繁体:https://ixdzs8.tw,"
    "E-mail:support@ixdzs.com"
)
OOHSTORY_EBOOK_NOTICE = f"{PUBLIC_DOMAIN}，好故事电子书"
LEGACY_LIBRARY_DOMAIN_RE = re.compile(
    r"(?:(?:www\.)?txt80\.cc|(?:www\.)?txt02\.com|ohhstory\.com)",
    re.IGNORECASE,
)

sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.library_catalog import (  # noqa: E402
    book_identity,
    normalize_catalog_title,
)
from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
    RedisQueueClient,
)
from oohstory_library.services.library_download_queue import LibraryDownloadQueue  # noqa: E402
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;

CREATE TABLE IF NOT EXISTS pages (
  page INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  book_count INTEGER,
  last_error TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS catalog_sections (
  section_key TEXT PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  label TEXT,
  total_pages INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS listing_pages (
  section_key TEXT NOT NULL,
  page INTEGER NOT NULL,
  url TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  book_count INTEGER,
  last_error TEXT,
  updated_at TEXT,
  PRIMARY KEY (section_key, page)
);

CREATE TABLE IF NOT EXISTS crawl_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS books (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT,
  detail_url TEXT NOT NULL UNIQUE,
  title TEXT,
  author TEXT,
  category TEXT,
  expected_size TEXT,
  download_page_url TEXT,
  file_url TEXT,
  output_path TEXT,
  status TEXT NOT NULL DEFAULT 'discovered',
  book_status TEXT NOT NULL DEFAULT '已完结',
  attempts INTEGER NOT NULL DEFAULT 0,
  bytes INTEGER,
  sha256 TEXT,
  last_error TEXT,
  discovered_at TEXT,
  updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_books_status ON books(status, attempts);
CREATE INDEX IF NOT EXISTS idx_books_category ON books(category);
CREATE INDEX IF NOT EXISTS idx_books_source_id ON books(source_id);
CREATE INDEX IF NOT EXISTS idx_books_txt80_download_queue
  ON books(status, attempts, id)
  WHERE detail_url LIKE 'https://www.txt80.cc/%';
CREATE INDEX IF NOT EXISTS idx_listing_pages_status
  ON listing_pages(status, attempts);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def db_connect() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    book_columns = {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(books)")
    }
    if "book_status" not in book_columns:
        conn.execute(
            "ALTER TABLE books "
            "ADD COLUMN book_status TEXT NOT NULL DEFAULT '已完结'"
        )
    # Preserve the completed /all/ checkpoints produced by crawler versions
    # that only knew about the aggregate listing.
    conn.execute(
        """
        INSERT OR IGNORE INTO listing_pages
          (section_key, page, url, status, attempts, book_count, last_error,
           updated_at)
        SELECT 'all', page, url, status, attempts, book_count, last_error,
               updated_at
        FROM pages
        """
    )
    conn.commit()
    return conn


def sqlite_write_retry(
    conn: sqlite3.Connection,
    operation,
    *,
    attempts: int = 8,
):
    """Serialize short write transactions through transient WAL contention."""
    for retry in range(attempts):
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = operation()
            conn.commit()
            return result
        except sqlite3.OperationalError as exc:
            conn.rollback()
            if "locked" not in str(exc).lower() or retry + 1 >= attempts:
                raise
            time.sleep(min(0.25 * (2**retry), 5.0))
        except Exception:
            conn.rollback()
            raise
    raise RuntimeError("SQLite write retry exhausted")


class HostRateLimiter:
    def __init__(self, delay: float):
        self.delay = max(0.0, delay)
        self.lock = threading.Lock()
        self.next_allowed: dict[str, float] = {}

    def wait(self, url: str):
        host = urlparse(url).netloc.lower()
        with self.lock:
            current = time.monotonic()
            slot = max(current, self.next_allowed.get(host, current))
            self.next_allowed[host] = slot + self.delay
        sleep_for = slot - current
        if sleep_for > 0:
            time.sleep(sleep_for)


class HttpClient:
    def __init__(self, delay: float, timeout: int):
        self.timeout = timeout
        self.limiter = HostRateLimiter(delay)
        self.local = threading.local()

    def session(self) -> requests.Session:
        session = getattr(self.local, "session", None)
        if session is not None:
            return session
        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        retry = Retry(
            total=5,
            connect=5,
            read=5,
            status=5,
            backoff_factor=1.0,
            status_forcelist=(408, 425, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
        )
        session.mount(
            "https://",
            HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16),
        )
        self.local.session = session
        return session

    def get(self, url: str, *, stream: bool = False) -> requests.Response:
        self.limiter.wait(url)
        response = self.session().get(
            url,
            timeout=(10, self.timeout),
            allow_redirects=True,
            stream=stream,
        )
        response.raise_for_status()
        return response

    def html(self, url: str) -> str:
        response = self.get(url)
        response.encoding = response.apparent_encoding or "utf-8"
        if len(response.content) < 300:
            raise ValueError(f"HTML response too small: {len(response.content)}")
        return response.text


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_title(value: str) -> str:
    value = clean_text(value)
    value = value.removeprefix("《").replace("》", "", 1)
    value = re.sub(
        r"(?:全文|全集|全本)?TXT(?:电子书|小说)?下载.*$", "", value,
        flags=re.IGNORECASE,
    )
    return normalize_catalog_title(value.strip("《》 []"))


def scalable_identity_part(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def update_scalable_catalog_keys(
    conn: sqlite3.Connection,
    book_id: int,
    book: dict,
    columns: set[str],
) -> None:
    if not {"identity_key", "title_key", "library_id"}.issubset(columns):
        return
    title_key = scalable_identity_part(book.get("title"))
    author_key = scalable_identity_part(book.get("author"))
    conn.execute(
        """
        UPDATE books
        SET identity_key=?,
            title_key=?,
            library_id=COALESCE(NULLIF(library_id, ''), 'local')
        WHERE id=?
        """,
        (
            f"{title_key}\x1f{author_key}" if title_key else "",
            title_key,
            int(book_id),
        ),
    )


def safe_name(value: str, fallback: str, max_len: int = 100) -> str:
    value = unicodedata.normalize("NFKC", clean_text(value))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = value.strip(" .")
    return (value or fallback)[:max_len]


def source_id(detail_url: str) -> str:
    match = re.search(r"txt(\d+)\.html", detail_url)
    return match.group(1) if match else hashlib.sha1(
        detail_url.encode("utf-8")
    ).hexdigest()[:12]


def canonical_detail_url(detail_url: str) -> str:
    parsed = urlparse(urljoin(BASE_URL, detail_url))
    path = re.sub(r"/+", "/", parsed.path)
    return f"{BASE_URL}{path}"


def list_url(section_key: str, page: int) -> str:
    section_key = clean_text(section_key).strip("/")
    if not re.fullmatch(r"[a-z0-9_-]+", section_key):
        raise ValueError(f"invalid txt80 section: {section_key!r}")
    return (
        f"{BASE_URL}/{section_key}/"
        if page == 1
        else f"{BASE_URL}/{section_key}/index_{page}.html"
    )


def parse_category_sections(home_html: str) -> list[dict[str, str]]:
    """Find actual genre entrances from the site's category blocks.

    The homepage also links to rankings, searches and recommendation feeds.
    Those are intentionally excluded: only ``div.sort`` category headers are
    accepted, while /all/ is kept as a compatibility/coverage source.
    """

    soup = BeautifulSoup(home_html, "html.parser")
    sections = [
        {"key": "all", "path": "/all/", "label": "书库大全"},
    ]
    seen = {"all"}
    for anchor in soup.select("div.sort h2 > a[href]"):
        href = clean_text(anchor.get("href", ""))
        match = re.fullmatch(r"/([a-z0-9_-]+)/", href)
        if not match:
            continue
        key = match.group(1)
        if key in seen:
            continue
        seen.add(key)
        sections.append(
            {
                "key": key,
                "path": f"/{key}/",
                "label": clean_text(anchor.get_text(" ", strip=True)),
            }
        )
    if len(sections) == 1:
        raise ValueError("no txt80 genre category entrances found")
    return sections


def parse_total_pages(html: str, section_key: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(
        rf"^/{re.escape(section_key)}/index_(\d+)\.html$"
    )
    pages = [1]
    for anchor in soup.select("a[href]"):
        match = pattern.match(clean_text(anchor.get("href", "")))
        if match:
            pages.append(int(match.group(1)))
    return max(pages)


def parse_list_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    books = []
    for node in soup.select("div.slist"):
        anchor = node.select_one("div.info h4 a[href*='txt']")
        if not anchor:
            continue
        detail_url = canonical_detail_url(anchor.get("href", "").strip())
        title = normalize_title(anchor.get_text(" ", strip=True))
        meta_links = node.select("p.xm a")
        category = clean_text(meta_links[0].get_text()) if meta_links else ""
        author = (
            clean_text(meta_links[1].get_text())
            if len(meta_links) > 1 else ""
        )
        info = clean_text(
            node.select_one("p.l").get_text(" ", strip=True)
            if node.select_one("p.l") else ""
        )
        size_match = re.search(r"小说大小[：:]\s*([^|]+)", info)
        books.append(
            {
                "source_id": source_id(detail_url),
                "detail_url": detail_url,
                "title": title,
                "author": author,
                "category": category,
                "expected_size": (
                    clean_text(size_match.group(1)) if size_match else ""
                ),
            }
        )
    return books


def parse_detail_page(html: str, fallback: dict) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.select_one("div.detail dd.bt h2")
    title = normalize_title(
        title_node.get_text(" ", strip=True)
        if title_node else fallback["title"]
    )
    author = fallback["author"]
    category = fallback["category"]
    for node in soup.select("div.detail dd.db"):
        text = clean_text(node.get_text(" ", strip=True))
        if text.startswith("小说作者"):
            author = clean_text(text.split("：", 1)[-1])
        elif text.startswith("小说分类"):
            category = clean_text(text.split("：", 1)[-1])
    down_anchor = soup.select_one(
        "div.downlinks a[href*='/down/'], a[href*='/down/'][href$='.html']"
    )
    if not down_anchor:
        raise ValueError("download page link not found")
    return {
        "title": title,
        "author": author,
        "category": category,
        "download_page_url": urljoin(
            BASE_URL, down_anchor.get("href", "").strip()
        ),
    }


def parse_download_page(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for anchor in soup.select("div.downlist a[href]"):
        href = clean_text(anchor.get("href", ""))
        if href.lower().endswith(".txt"):
            urls.append(urljoin(BASE_URL, href))
    if not urls:
        raise ValueError("TXT file link not found")
    return list(dict.fromkeys(urls))


def discover_page(
    client: HttpClient,
    section_key: str,
    page: int,
) -> tuple[str, int, list[dict]]:
    html = client.html(list_url(section_key, page))
    books = parse_list_page(html)
    # 站点个别历史分页实际不足 10 条（当前 index_2434.html 为 9 条）。
    # 非空页应照常入库，否则整页会因固定数量校验永久缺失。
    if not books:
        raise ValueError("expected a non-empty book list, found 0")
    if len(books) > 10:
        raise ValueError(f"expected at most 10 books, found {len(books)}")
    return section_key, page, books


class DiscoveryRegistry:
    """In-memory keys for deterministic cross-section de-duplication."""

    def __init__(self, conn: sqlite3.Connection):
        self.by_source: dict[str, int] = {}
        self.by_url: dict[str, int] = {}
        self.by_identity: dict[tuple[str, str], int] = {}
        for row in conn.execute(
            """
            SELECT id, source_id, detail_url, title, author, status
            FROM books
            ORDER BY CASE WHEN status='done' THEN 0 ELSE 1 END, id
            """
        ):
            book_id = int(row["id"])
            remote_id = clean_text(row["source_id"])
            if remote_id:
                self.by_source.setdefault(remote_id, book_id)
            detail_url = canonical_detail_url(str(row["detail_url"] or ""))
            if detail_url:
                self.by_url.setdefault(detail_url, book_id)
            identity = book_identity(row["title"], row["author"])
            if identity[0] and identity[1]:
                self.by_identity.setdefault(identity, book_id)

    def match(self, book: dict) -> int | None:
        remote_id = clean_text(book.get("source_id"))
        detail_url = canonical_detail_url(str(book.get("detail_url") or ""))
        identity = book_identity(book.get("title"), book.get("author"))
        return (
            self.by_source.get(remote_id)
            or self.by_url.get(detail_url)
            or (
                self.by_identity.get(identity)
                if identity[0] and identity[1]
                else None
            )
        )

    def add(self, book_id: int, book: dict) -> None:
        remote_id = clean_text(book.get("source_id"))
        if remote_id:
            self.by_source.setdefault(remote_id, book_id)
        self.by_url.setdefault(
            canonical_detail_url(str(book.get("detail_url") or "")),
            book_id,
        )
        identity = book_identity(book.get("title"), book.get("author"))
        if identity[0] and identity[1]:
            self.by_identity.setdefault(identity, book_id)


def upsert_discovered(
    conn: sqlite3.Connection,
    section_key: str,
    page: int,
    books: list[dict],
    registry: DiscoveryRegistry,
):
    stamp = now()
    catalog_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(books)")
    }
    for book in books:
        book = {**book, "detail_url": canonical_detail_url(book["detail_url"])}
        existing_id = registry.match(book)
        if existing_id is None:
            cursor = conn.execute(
                """
                INSERT INTO books
                  (source_id, detail_url, title, author, category,
                   expected_size, status, book_status, discovered_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'discovered', '已完结', ?, ?)
                """,
                (
                    book["source_id"],
                    book["detail_url"],
                    book["title"],
                    book["author"],
                    book["category"],
                    book["expected_size"],
                    stamp,
                    stamp,
                ),
            )
            inserted_id = int(cursor.lastrowid)
            update_scalable_catalog_keys(
                conn,
                inserted_id,
                book,
                catalog_columns,
            )
            registry.add(inserted_id, book)
            continue

        current = conn.execute(
            """
            SELECT title, author, category, expected_size, status
            FROM books WHERE id=?
            """,
            (existing_id,),
        ).fetchone()
        size_changed = bool(
            current
            and current["status"] == "done"
            and book["expected_size"]
            and current["expected_size"]
            and book["expected_size"] != current["expected_size"]
        )
        conn.execute(
            """
            UPDATE books SET
              title=CASE WHEN title='' THEN ? ELSE title END,
              author=CASE WHEN author='' THEN ? ELSE author END,
              category=CASE WHEN category='' THEN ? ELSE category END,
              expected_size=CASE WHEN ?='' THEN expected_size ELSE ? END,
              status=CASE WHEN ? THEN 'refresh' ELSE status END,
              attempts=CASE WHEN ? THEN 0 ELSE attempts END,
              updated_at=?
            WHERE id=?
            """,
            (
                book["title"],
                book["author"],
                book["category"],
                book["expected_size"],
                book["expected_size"],
                int(size_changed),
                int(size_changed),
                stamp,
                existing_id,
            ),
        )
        update_scalable_catalog_keys(
            conn,
            existing_id,
            book,
            catalog_columns,
        )
    conn.execute(
        """
        INSERT INTO listing_pages
          (section_key, page, url, status, attempts, book_count, updated_at)
        VALUES (?, ?, ?, 'done', 1, ?, ?)
        ON CONFLICT(section_key, page) DO UPDATE SET
          status='done',
          attempts=listing_pages.attempts + 1,
          book_count=excluded.book_count,
          last_error=NULL,
          updated_at=excluded.updated_at
        """,
        (
            section_key,
            page,
            list_url(section_key, page),
            len(books),
            stamp,
        ),
    )
    # Continue updating the legacy table for /all/ so older reporting tools
    # remain useful during the migration.
    if section_key == "all":
        conn.execute(
            """
            INSERT INTO pages
              (page, url, status, attempts, book_count, updated_at)
            VALUES (?, ?, 'done', 1, ?, ?)
            ON CONFLICT(page) DO UPDATE SET
              status='done', attempts=pages.attempts + 1,
              book_count=excluded.book_count, last_error=NULL,
              updated_at=excluded.updated_at
            """,
            (page, list_url(section_key, page), len(books), stamp),
        )
    conn.commit()


def mark_page_failed(
    conn: sqlite3.Connection,
    section_key: str,
    page: int,
    error: str,
):
    conn.execute(
        """
        INSERT INTO listing_pages
          (section_key, page, url, status, attempts, last_error, updated_at)
        VALUES (?, ?, ?, 'failed', 1, ?, ?)
        ON CONFLICT(section_key, page) DO UPDATE SET
          status='failed',
          attempts=listing_pages.attempts + 1,
          last_error=excluded.last_error,
          updated_at=excluded.updated_at
        """,
        (
            section_key,
            page,
            list_url(section_key, page),
            error[:500],
            now(),
        ),
    )
    conn.commit()


def discover_catalog_sections(
    conn: sqlite3.Connection,
    client: HttpClient,
) -> list[dict]:
    """Refresh the category/page manifest without losing prior checkpoints."""

    try:
        home_html = client.html(f"{BASE_URL}/")
        discovered = parse_category_sections(home_html)
        _set_crawl_state(conn, "catalog_manifest_error", "")
    except Exception as exc:
        _set_crawl_state(
            conn,
            "catalog_manifest_error",
            f"{type(exc).__name__}: {exc}"[:500],
        )
        existing = load_catalog_sections(conn)
        if existing:
            print(
                "[sections] homepage refresh failed; using saved manifest: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            return existing
        raise
    stamp = now()
    for item in discovered:
        key = item["key"]
        try:
            html = client.html(list_url(key, 1))
            books = parse_list_page(html)
            if not books:
                raise ValueError("category landing page contains no books")
            total_pages = parse_total_pages(html, key)
            conn.execute(
                """
                INSERT INTO catalog_sections
                  (section_key, path, label, total_pages, status, attempts,
                   updated_at)
                VALUES (?, ?, ?, ?, 'done', 1, ?)
                ON CONFLICT(section_key) DO UPDATE SET
                  path=excluded.path, label=excluded.label,
                  total_pages=excluded.total_pages, status='done',
                  attempts=catalog_sections.attempts + 1,
                  last_error=NULL, updated_at=excluded.updated_at
                """,
                (
                    key,
                    item["path"],
                    item["label"],
                    total_pages,
                    stamp,
                ),
            )
        except Exception as exc:
            conn.execute(
                """
                INSERT INTO catalog_sections
                  (section_key, path, label, status, attempts, last_error,
                   updated_at)
                VALUES (?, ?, ?, 'failed', 1, ?, ?)
                ON CONFLICT(section_key) DO UPDATE SET
                  status='failed', attempts=catalog_sections.attempts + 1,
                  last_error=excluded.last_error,
                  updated_at=excluded.updated_at
                """,
                (
                    key,
                    item["path"],
                    item["label"],
                    f"{type(exc).__name__}: {exc}"[:500],
                    stamp,
                ),
            )
        conn.commit()
    return load_catalog_sections(conn)


def load_catalog_sections(conn: sqlite3.Connection) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT section_key, path, label, total_pages
            FROM catalog_sections
            WHERE total_pages > 0
            ORDER BY CASE WHEN section_key='all' THEN 0 ELSE 1 END,
                     section_key
            """
        )
    ]


def _crawl_state(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute(
        "SELECT value FROM crawl_state WHERE key=?",
        (key,),
    ).fetchone()
    return str(row["value"]) if row else ""


def _set_crawl_state(
    conn: sqlite3.Connection,
    key: str,
    value: str,
) -> None:
    conn.execute(
        """
        INSERT INTO crawl_state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
          value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, now()),
    )
    conn.commit()


def begin_catalog_refresh(conn: sqlite3.Connection) -> bool:
    """Start one refresh generation, or resume the interrupted generation."""

    if _crawl_state(conn, "catalog_refresh_active") == "1":
        # A previous process stopped after exhausting its in-process retries.
        # Keep completed pages intact, but make failed pages retryable again.
        conn.execute(
            """
            UPDATE listing_pages
            SET attempts=0, updated_at=?
            WHERE status='failed'
            """,
            (now(),),
        )
        conn.commit()
        return False
    conn.execute(
        """
        UPDATE listing_pages
        SET status='pending', attempts=0, last_error=NULL, updated_at=?
        """,
        (now(),),
    )
    _set_crawl_state(conn, "catalog_refresh_active", "1")
    return True


def discover(
    conn: sqlite3.Connection,
    client: HttpClient,
    sections: list[dict],
    workers: int,
    max_attempts: int,
    page_cap: int | None = None,
):
    selected = {
        str(item["section_key"]): min(
            int(item["total_pages"]),
            page_cap if page_cap and page_cap > 0 else int(item["total_pages"]),
        )
        for item in sections
    }
    states = {
        (str(row["section_key"]), int(row["page"])): (
            str(row["status"]),
            int(row["attempts"]),
        )
        for row in conn.execute(
            "SELECT section_key, page, status, attempts FROM listing_pages"
        )
    }
    pending = [
        (section_key, page)
        for section_key, total_pages in selected.items()
        for page in range(1, total_pages + 1)
        if states.get((section_key, page), ("pending", 0))[0] != "done"
        and states.get((section_key, page), ("pending", 0))[1] < max_attempts
    ]
    completed = sum(
        1
        for section_key, total_pages in selected.items()
        for page in range(1, total_pages + 1)
        if states.get((section_key, page), ("pending", 0))[0] == "done"
    )
    print(
        f"[discover] sections={len(selected)} queued={len(pending)} "
        f"completed={completed}",
        flush=True,
    )
    if not pending:
        return
    registry = DiscoveryRegistry(conn)
    done = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(discover_page, client, section_key, page):
                (section_key, page)
            for section_key, page in pending
        }
        for future in as_completed(futures):
            section_key, page = futures[future]
            try:
                _, _, books = future.result()
                upsert_discovered(
                    conn,
                    section_key,
                    page,
                    books,
                    registry,
                )
                status = f"ok books={len(books)}"
            except Exception as exc:
                mark_page_failed(
                    conn,
                    section_key,
                    page,
                    f"{type(exc).__name__}: {exc}",
                )
                status = f"failed {type(exc).__name__}: {exc}"
            done += 1
            rate = done / max(0.01, time.time() - started)
            print(
                f"[discover {done}/{len(pending)}] "
                f"section={section_key} page={page} "
                f"{status} rate={rate:.2f}/s",
                flush=True,
            )


def disk_free_gb() -> float:
    return shutil.disk_usage(ROOT).free / (1024 ** 3)


def normalize_txt_to_utf8(path: Path):
    """Normalize the site's mixed UTF-8/GB18030 TXT corpus to UTF-8."""
    raw = path.read_bytes()
    decoded = None
    used_encoding = None
    for encoding in ("utf-8-sig", "gb18030", "big5", "utf-16"):
        try:
            decoded = raw.decode(encoding)
            used_encoding = encoding
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        tolerant = raw.decode("gb18030", errors="replace")
        replacement_ratio = tolerant.count("\ufffd") / max(len(tolerant), 1)
        if replacement_ratio <= 0.001:
            decoded = tolerant
            used_encoding = "gb18030-tolerant"
    if decoded is None:
        try:
            from charset_normalizer import from_bytes

            match = from_bytes(raw).best()
            if match is not None:
                decoded = str(match)
                used_encoding = str(match.encoding or "charset-normalizer")
        except Exception:
            decoded = None
    if decoded is None:
        raise UnicodeError("unsupported TXT encoding")
    normalized = (
        decoded.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace(LEGACY_LIBRARY_NOTICE, PUBLIC_LIBRARY_NOTICE)
        .replace(IXDZS_PROMOTIONAL_NOTICE, OOHSTORY_EBOOK_NOTICE)
    )
    normalized = LEGACY_LIBRARY_DOMAIN_RE.sub(PUBLIC_DOMAIN, normalized)
    normalized = normalized.replace(
        LEGACY_REBRANDED_NOTICE,
        PUBLIC_LIBRARY_NOTICE,
    )
    data = normalized.encode("utf-8")
    path.write_bytes(data)
    return used_encoding, len(data), hashlib.sha256(data).hexdigest()


def download_file(
    client: HttpClient,
    urls: Iterable[str],
    output_path: Path,
) -> tuple[str, int, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".part")
    errors = []
    for url in urls:
        try:
            response = client.get(url, stream=True)
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                raise ValueError(f"unexpected content-type {content_type}")
            digest = hashlib.sha256()
            size = 0
            with temp_path.open("wb") as f:
                for chunk in response.iter_content(128 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if size < 128:
                raise ValueError(f"TXT file too small: {size}")
            _, size, normalized_hash = normalize_txt_to_utf8(temp_path)
            os.replace(temp_path, output_path)
            return url, size, normalized_hash
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
            temp_path.unlink(missing_ok=True)
    raise RuntimeError(" | ".join(errors))


def process_book(client: HttpClient, row: sqlite3.Row) -> dict:
    book = dict(row)
    detail = parse_detail_page(client.html(book["detail_url"]), book)
    file_urls = parse_download_page(
        client.html(detail["download_page_url"])
    )
    category = safe_name(detail["category"], "未分类", 60)
    title = safe_name(detail["title"], f"未命名-{book['source_id']}")
    author = safe_name(detail["author"], "未知作者", 60)
    filename = f"{title}__{author}__{book['source_id']}.txt"
    output_path = BOOK_ROOT / category / filename
    if (
        book.get("status") != "refresh"
        and output_path.exists()
        and output_path.stat().st_size >= 128
    ):
        _, size, sha256 = normalize_txt_to_utf8(output_path)
        return {
            **detail,
            "file_url": file_urls[0],
            "output_path": str(output_path),
            "bytes": size,
            "sha256": sha256,
        }
    file_url, size, sha256 = download_file(
        client, file_urls, output_path
    )
    return {
        **detail,
        "file_url": file_url,
        "output_path": str(output_path),
        "bytes": size,
        "sha256": sha256,
    }


def mark_book_done(conn: sqlite3.Connection, book_id: int, result: dict):
    def operation():
        conn.execute(
            """
            UPDATE books SET
              title=?, author=?, category=?, download_page_url=?, file_url=?,
              output_path=?, status='done', attempts=attempts+1, bytes=?,
              sha256=?, last_error=NULL, updated_at=?
            WHERE id=?
            """,
            (
                result["title"], result["author"], result["category"],
                result["download_page_url"], result["file_url"],
                result["output_path"], result["bytes"], result["sha256"],
                now(), book_id,
            ),
        )

    sqlite_write_retry(conn, operation)


def mark_book_failed(conn: sqlite3.Connection, book_id: int, error: str):
    def operation():
        existing = conn.execute(
            "SELECT output_path FROM books WHERE id=?",
            (book_id,),
        ).fetchone()
        retained = bool(
            existing
            and existing["output_path"]
            and Path(str(existing["output_path"])).is_file()
            and Path(str(existing["output_path"])).stat().st_size >= 128
        )
        permanent_content_failure = (
            "UnicodeError: unsupported TXT encoding" in error
        )
        conn.execute(
            """
            UPDATE books SET
              status=?,
              attempts=attempts+1,
              last_error=?,
              updated_at=?
            WHERE id=?
            """,
            (
                (
                    "done"
                    if retained
                    else (
                        "quarantined"
                        if permanent_content_failure
                        else "failed"
                    )
                ),
                (
                    f"刷新失败，保留现有正文：{error}"
                    if retained
                    else error
                )[:1000],
                now(),
                book_id,
            ),
        )

    sqlite_write_retry(conn, operation)


def _expected_size_bytes(value: str | None) -> int:
    match = re.search(
        r"([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB)",
        str(value or ""),
        re.IGNORECASE,
    )
    if not match:
        return 0
    factor = {
        "B": 1,
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
    }[match.group(2).upper()]
    return int(float(match.group(1)) * factor)


def mark_known_duplicates(conn: sqlite3.Connection) -> int:
    """Skip remote records that cannot be newer than an existing local work."""

    available: dict[tuple[str, str], sqlite3.Row] = {}
    for row in conn.execute(
        """
        SELECT id, source_id, title, author, bytes
        FROM books WHERE status='done'
        ORDER BY COALESCE(bytes, 0) DESC, id DESC
        """
    ):
        identity = book_identity(row["title"], row["author"])
        if (
            identity[0]
            and identity[1] not in {"", "未知作者", "作者未知"}
            and identity not in available
        ):
            available[identity] = row

    duplicate_ids: list[tuple[str, str, int]] = []
    for row in conn.execute(
        """
        SELECT id, source_id, title, author, expected_size
        FROM books
        WHERE status IN ('discovered', 'failed')
        """
    ):
        identity = book_identity(row["title"], row["author"])
        kept = available.get(identity)
        if not kept or str(kept["source_id"]) == str(row["source_id"]):
            continue
        expected = _expected_size_bytes(row["expected_size"])
        existing = int(kept["bytes"] or 0)
        if expected and existing and expected > int(existing * 1.02):
            continue
        duplicate_ids.append(
            (
                f"下载前去重；已保留 catalog_id={kept['id']} "
                f"source_id={kept['source_id']}",
                now(),
                int(row["id"]),
            )
        )
    if duplicate_ids:
        conn.executemany(
            """
            UPDATE books
            SET status='duplicate', last_error=?, updated_at=?
            WHERE id=?
            """,
            duplicate_ids,
        )
        conn.commit()
    return len(duplicate_ids)


def next_book_batch(
    conn: sqlite3.Connection,
    *,
    max_attempts: int,
    batch_size: int,
) -> list[sqlite3.Row]:
    """Keep fresh catalog downloads moving before retrying known failures."""
    return conn.execute(
        """
        SELECT * FROM books
        WHERE status IN ('discovered', 'failed', 'refresh')
          AND attempts < ?
          AND detail_url LIKE 'https://www.txt80.cc/%'
        ORDER BY
          CASE status
            WHEN 'discovered' THEN 0
            WHEN 'refresh' THEN 1
            ELSE 2
          END,
          attempts,
          id
        LIMIT ?
        """,
        (max_attempts, batch_size),
    ).fetchall()


def download_books(
    conn: sqlite3.Connection,
    client: HttpClient,
    workers: int,
    max_attempts: int,
    min_free_gb: float,
    limit_books: int | None,
    watch: bool,
    expected_pages: int,
):
    processed = 0
    started = time.time()
    preflight_duplicates = mark_known_duplicates(conn)
    if preflight_duplicates:
        print(
            f"[books] preflight duplicates skipped={preflight_duplicates}",
            flush=True,
        )
    while True:
        if disk_free_gb() < min_free_gb:
            raise RuntimeError(
                f"low disk space: {disk_free_gb():.1f} GB free "
                f"(minimum {min_free_gb:.1f} GB)"
            )
        remaining_limit = (
            None if limit_books is None else limit_books - processed
        )
        if remaining_limit is not None and remaining_limit <= 0:
            break
        batch_size = min(200, remaining_limit or 200)
        rows = next_book_batch(
            conn,
            max_attempts=max_attempts,
            batch_size=batch_size,
        )
        if not rows:
            if watch:
                completed_pages = conn.execute(
                    "SELECT COUNT(*) FROM listing_pages WHERE status='done'"
                ).fetchone()[0]
                if completed_pages < expected_pages:
                    print(
                        f"[books] queue empty; waiting for discovery "
                        f"({completed_pages}/{expected_pages} pages)",
                        flush=True,
                    )
                    time.sleep(15)
                    continue
            break
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(process_book, client, row): row
                for row in rows
            }
            for future in as_completed(futures):
                row = futures[future]
                try:
                    result = future.result()
                    mark_book_done(conn, row["id"], result)
                    status = (
                        f"done {result['bytes'] / 1024:.0f}KB "
                        f"{result['title']}"
                    )
                except Exception as exc:
                    mark_book_failed(
                        conn, row["id"],
                        f"{type(exc).__name__}: {exc}",
                    )
                    status = (
                        f"failed {row['title']} "
                        f"{type(exc).__name__}: {exc}"
                    )
                processed += 1
                rate = processed / max(0.01, time.time() - started)
                print(
                    f"[books {processed}] {status} "
                    f"rate={rate:.2f}/s free={disk_free_gb():.1f}GB",
                    flush=True,
                )


def export_catalog(conn: sqlite3.Connection):
    rows = conn.execute(
        """
        SELECT source_id, title, author, category, expected_size,
               detail_url, download_page_url, file_url, output_path,
               status, attempts, bytes, sha256, last_error, updated_at
        FROM books
        ORDER BY id
        """
    ).fetchall()
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(rows[0].keys() if rows else [
            "source_id", "title", "author", "category", "expected_size",
            "detail_url", "download_page_url", "file_url", "output_path",
            "status", "attempts", "bytes", "sha256", "last_error",
            "updated_at",
        ])
        writer.writerows([tuple(row) for row in rows])
    print(f"[export] {len(rows)} rows -> {CSV_PATH}", flush=True)


def print_stats(conn: sqlite3.Connection):
    sections = {
        str(row["section_key"]): int(row["total_pages"])
        for row in conn.execute(
            """
            SELECT section_key, total_pages
            FROM catalog_sections
            WHERE total_pages > 0
            ORDER BY section_key
            """
        )
    }
    pages = dict(
        conn.execute(
            "SELECT status, COUNT(*) FROM listing_pages GROUP BY status"
        ).fetchall()
    )


def discover_catalog_sections_mysql(
    runtime: MySQLLibraryRuntime,
    client: HttpClient,
) -> list[dict[str, Any]]:
    try:
        discovered = parse_category_sections(client.html(f"{BASE_URL}/"))
    except Exception:
        existing = runtime.load_catalog_sections("txt80")
        if existing:
            return existing
        raise
    for item in discovered:
        record = {
            "section_key": item["key"],
            "path": item["path"],
            "label": item["label"],
            "total_pages": 0,
        }
        error = ""
        try:
            html = client.html(list_url(item["key"], 1))
            if not parse_list_page(html):
                raise ValueError("category landing page contains no books")
            record["total_pages"] = parse_total_pages(html, item["key"])
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:500]}"
        runtime.upsert_catalog_section("txt80", record, error=error)
    return runtime.load_catalog_sections("txt80")


def mysql_main(args: argparse.Namespace, client: HttpClient) -> None:
    """Run the txt80 catalog producer without opening legacy SQLite."""
    settings = LibraryInfrastructureSettings.from_env()
    redis_queue = RedisQueueClient(settings)
    runtime = MySQLLibraryRuntime(
        settings,
        MySQLConnectionPool(settings),
        redis_queue,
    )
    if args.stats:
        print(
            json.dumps(
                runtime.catalog_runtime_stats("txt80"),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    sections = (
        runtime.load_catalog_sections("txt80")
        if args.skip_discovery
        else discover_catalog_sections_mysql(runtime, client)
    )
    if args.skip_discovery and not sections:
        sections = [
            {
                "section_key": "all",
                "path": "/all/",
                "label": "书库大全",
                "total_pages": 3200,
            }
        ]
    requested = {
        value.strip() for value in args.sections.split(",") if value.strip()
    }
    if requested:
        sections = [
            item
            for item in sections
            if str(item["section_key"]) in requested
        ]
    if not sections:
        raise RuntimeError("txt80 category manifest is empty")

    if not args.skip_discovery:
        states = runtime.crawl_page_states("txt80")
        pending = []
        for item in sections:
            section_key = str(item["section_key"])
            total = int(item["total_pages"])
            if args.pages and args.pages > 0:
                total = min(total, args.pages)
            for page in range(1, total + 1):
                status, attempts = states.get(
                    (section_key, page),
                    ("pending", 0),
                )
                if (
                    args.refresh_catalog
                    or (status != "done" and attempts < args.max_attempts)
                ):
                    pending.append((section_key, page))
        print(
            f"[discover] sections={len(sections)} queued={len(pending)}",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(discover_page, client, section_key, page):
                (section_key, page)
                for section_key, page in pending
            }
            for index, future in enumerate(as_completed(futures), start=1):
                section_key, page = futures[future]
                try:
                    _, _, books = future.result()
                    runtime.register_txt80_page(
                        section_key=section_key,
                        page=page,
                        url=list_url(section_key, page),
                        books=books,
                    )
                    status = f"ok books={len(books)}"
                except Exception as exc:
                    runtime.fail_crawl_page(
                        source_name="txt80",
                        section_key=section_key,
                        page=page,
                        url=list_url(section_key, page),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    status = f"failed {type(exc).__name__}: {exc}"
                print(
                    f"[discover {index}/{len(pending)}] "
                    f"section={section_key} page={page} {status}",
                    flush=True,
                )
    if not args.discover_only:
        dispatched = LibraryDownloadQueue(
            runtime.pool,
            redis_queue,
        ).dispatch(limit=max(1, min(args.limit_books or 1000, 10000)))
        print(
            json.dumps(
                {"download_dispatch": dispatched},
                ensure_ascii=False,
            ),
            flush=True,
        )
    stats = runtime.catalog_runtime_stats("txt80")
    print(json.dumps(stats, ensure_ascii=False), flush=True)
    pages = stats.get("pages", {})
    books = stats.get("books", {})
    total_bytes = int(stats.get("downloaded_bytes", 0) or 0)
    print(
        f"[stats] sections={len(sections)} pages={pages} books={books} "
        f"downloaded={total_bytes / (1024 ** 3):.2f}GB "
        f"disk_free={disk_free_gb():.1f}GB",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pages",
        type=int,
        help="optional per-category page cap (mainly for smoke tests)",
    )
    parser.add_argument(
        "--sections",
        default="",
        help="optional comma-separated section keys, e.g. all,xuanhuan",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-attempts", type=int, default=8)
    parser.add_argument("--min-free-gb", type=float, default=15.0)
    parser.add_argument("--limit-books", type=int)
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--skip-discovery", action="store_true")
    parser.add_argument(
        "--refresh-catalog",
        action="store_true",
        help="re-scan all txt80 listing pages and detect newly updated editions",
    )
    parser.add_argument("--stats", action="store_true")
    parser.add_argument(
        "--search-public",
        metavar="TITLE_OR_AUTHOR",
        help="search Project Gutenberg public-domain books",
    )
    parser.add_argument(
        "--import-gutenberg",
        type=int,
        metavar="BOOK_ID",
        help="download a Project Gutenberg TXT and classify it with configured AI",
    )
    parser.add_argument(
        "--search-zlibrary",
        metavar="TITLE_OR_AUTHOR",
        help="search the site-owner-authorized z-library.im source",
    )
    parser.add_argument(
        "--search-txt80-online",
        metavar="TITLE_OR_AUTHOR",
        help="search txt80.cc live instead of only the local catalog",
    )
    parser.add_argument(
        "--import-zlibrary",
        metavar="BOOK_SLUG",
        help="download an authorized Z-Library TXT/EPUB and classify it",
    )
    parser.add_argument(
        "--import-txt80",
        metavar="BOOK_ID",
        help="download a txt80.cc TXT and classify it",
    )
    parser.add_argument(
        "--zlibrary-source-ref",
        default="",
        metavar="DETAIL_PATH",
        help="validated /book/<slug>/<title>.html path returned by Z-Library search",
    )
    parser.add_argument(
        "--txt80-source-ref",
        default="",
        metavar="DETAIL_PATH",
        help="validated txt80.cc detail path returned by online search",
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="keep consuming books while the discovery service is running",
    )
    args = parser.parse_args()

    if (
        args.search_public
        or args.import_gutenberg
        or args.search_zlibrary
        or args.import_zlibrary
        or args.search_txt80_online
        or args.import_txt80
    ):
        app_root = APP_ROOT
        sys.path.insert(0, str(app_root / "src"))
        from oohstory_library.services.electronic_library import ElectronicLibraryService

        service = ElectronicLibraryService()
        if args.search_public:
            print(
                json.dumps(
                    service.search_public_catalog(args.search_public, limit=3),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if args.search_txt80_online:
            print(
                json.dumps(
                    service.txt80_provider.search(
                        args.search_txt80_online, limit=3
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if args.search_zlibrary:
            print(
                json.dumps(
                    service.zlibrary_provider.search(
                        args.search_zlibrary, limit=3
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        from oohstory_library.services.ai_service import get_ai_service

        result = asyncio.run(
            service.import_public_book(
                provider=(
                    "authorized_zlibrary"
                    if args.import_zlibrary
                    else (
                        "authorized_txt80"
                        if args.import_txt80
                        else "project_gutenberg"
                    )
                ),
                remote_id=(
                    args.import_zlibrary
                    or args.import_txt80
                    or args.import_gutenberg
                ),
                source_ref=(
                    args.zlibrary_source_ref
                    if args.import_zlibrary
                    else (
                        args.txt80_source_ref
                        if args.import_txt80
                        else ""
                    )
                ),
                defer_postprocess=True,
                ai_service=get_ai_service(),
            )
        )
        service.start_queued_ingestion_index_refresh(wait=False)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    BOOK_ROOT.mkdir(parents=True, exist_ok=True)
    client = HttpClient(args.delay, args.timeout)
    if LibraryInfrastructureSettings.from_env().catalog_backend == "mysql":
        mysql_main(args, client)
        return
    with db_connect() as conn:
        if args.stats:
            print_stats(conn)
            return
        sections = (
            load_catalog_sections(conn)
            if args.skip_discovery
            else discover_catalog_sections(conn, client)
        )
        if args.skip_discovery and not sections:
            # Upgrade-safe fallback: consuming an existing download queue must
            # not depend on the homepage being reachable or on a manifest
            # having been created by the newer discovery pass.
            known_all_pages = conn.execute(
                """
                SELECT COALESCE(MAX(page), 0)
                FROM listing_pages
                WHERE section_key='all'
                """
            ).fetchone()[0]
            sections = [
                {
                    "section_key": "all",
                    "path": "/all/",
                    "label": "书库大全",
                    "total_pages": int(known_all_pages or 3200),
                }
            ]
        requested_sections = {
            value.strip()
            for value in args.sections.split(",")
            if value.strip()
        }
        if requested_sections:
            sections = [
                item for item in sections
                if item["section_key"] in requested_sections
            ]
            missing = requested_sections - {
                str(item["section_key"]) for item in sections
            }
            if missing:
                raise ValueError(
                    "unknown/unavailable txt80 sections: "
                    + ", ".join(sorted(missing))
                )
        if not sections:
            raise RuntimeError("txt80 category manifest is empty")
        if args.refresh_catalog and not args.skip_discovery:
            started = begin_catalog_refresh(conn)
            print(
                "[refresh] "
                + (
                    "new catalog refresh generation"
                    if started
                    else "resuming interrupted catalog refresh"
                ),
                flush=True,
            )
        if not args.skip_discovery:
            for _ in range(args.max_attempts):
                discover(
                    conn,
                    client,
                    sections,
                    args.workers,
                    args.max_attempts,
                    args.pages,
                )
                retryable = 0
                for item in sections:
                    total_pages = int(item["total_pages"])
                    if args.pages and args.pages > 0:
                        total_pages = min(total_pages, args.pages)
                    for page in range(1, total_pages + 1):
                        row = conn.execute(
                            """
                            SELECT status, attempts FROM listing_pages
                            WHERE section_key=? AND page=?
                            """,
                            (item["section_key"], page),
                        ).fetchone()
                        if (
                            not row
                            or (
                                row["status"] != "done"
                                and int(row["attempts"]) < args.max_attempts
                            )
                        ):
                            retryable += 1
                if retryable == 0:
                    break
            incomplete = conn.execute(
                """
                SELECT COUNT(*) FROM listing_pages
                WHERE status != 'done' AND attempts >= ?
                """,
                (args.max_attempts,),
            ).fetchone()[0]
            if retryable == 0 and incomplete == 0:
                _set_crawl_state(conn, "catalog_refresh_active", "0")
        if not args.discover_only:
            expected_pages = sum(
                min(
                    int(item["total_pages"]),
                    args.pages
                    if args.pages and args.pages > 0
                    else int(item["total_pages"]),
                )
                for item in sections
            )
            download_books(
                conn, client, args.workers, args.max_attempts,
                args.min_free_gb, args.limit_books, args.watch,
                expected_pages,
            )
        export_catalog(conn)
        print_stats(conn)


if __name__ == "__main__":
    main()
