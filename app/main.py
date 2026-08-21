from __future__ import annotations

from app.error_boundaries import RECOVERABLE_INTEGRATION_ERRORS

import asyncio
from base64 import b64decode, b64encode
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
import fcntl
from functools import lru_cache
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any, Callable
from uuid import UUID
import wave

import edge_tts
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, UUID4, field_validator

from .library import InputError, LibraryError, LibraryRepository, NotFoundError
from .accounts import AccountError, AccountStore
from .category_management import CategoryManager
from .settings import PROJECT_ROOT, load_settings
from .user_api import create_user_router
from .admin_api import create_admin_router
from .audiobook import (
    AudiobookService,
    has_spoken_content,
    mp3_duration_ms,
)
from .audiobook_policy import (
    AudiobookContractError,
    LOCAL_TTS_PROVIDER,
    MODE_LANGUAGE,
    POLICY_VERSION as AUDIOBOOK_POLICY_VERSION,
    TTS_VOICES,
    validate_voice_selection,
    voice_language,
)
from .comments import CommentStore
from .rate_limiter import RateLimiter


HOME_FEATURED_BOOK_COUNT = 14
HOME_PRIMARY_CACHE_SECONDS = 60
HOME_SECONDARY_CACHE_SECONDS = 300
STATIC_ROOT = PROJECT_ROOT / "static"
MAINTENANCE_PAGE = STATIC_ROOT / "maintenance.html"
MAINTENANCE_STATIC_PATHS = frozenset(
    {"/icon-192.png", "/favicon.ico", "/favicon-32.png"}
)
STRUCTURED_DATA_PATTERN = re.compile(
    r'<script id="structured-data" type="application/ld\+json">(.*?)</script>',
    re.DOTALL,
)
EARLY_BOOTSTRAP_PATTERN = re.compile(
    r'<script id="early-bootstrap">(.*?)</script>',
    re.DOTALL,
)
CANONICAL_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
PUBLIC_BOOK_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
SITE_ORIGIN = os.getenv(
    "OOHSTORY_PUBLIC_ORIGIN", "http://localhost:8091"
).strip().rstrip("/")
SITE_NAME = "OOH Story"
SITE_DEFAULT_IMAGE = f"{SITE_ORIGIN}/icon-512.png"
SEARCH_FAVICON_CACHE_CONTROL = "public, max-age=300, must-revalidate, no-transform"
SEARCH_FAVICON_CDN_CACHE_CONTROL = "public, max-age=300, must-revalidate"
SITE_DESCRIPTION = (
    "OOH Story 是免费、开源、自托管的中文小说阅读站，提供全本小说在线阅读、"
    "书库检索与深度拆书档案。"
)
SITE_KEYWORDS = (
    "OOH Story, 免费小说阅读, 免费小说下载, 中文小说, 全本小说, TXT电子书, 深度拆书"
)
AUDIOBOOK_CLIENT_COOKIE = "oohstory_audiobook_client"
AUDIOBOOK_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
SEO_CSP_HASH_HEADER = "X-OOHStory-SEO-CSP-Hash"
ANDROID_LATEST_VERSION_NAME = "1.18.21"
ANDROID_LATEST_VERSION_CODE = 65
ANDROID_LATEST_RELEASE_DATE = "2026-08-21"
ANDROID_LATEST_APK_PATH = STATIC_ROOT / "downloads" / "android" / "latest.apk"
ANDROID_LATEST_DOWNLOAD_URL = f"{SITE_ORIGIN}/downloads/android/latest.apk"
ANDROID_LATEST_RELEASE_NOTES_PUBLIC = (
    "修复快速翻章时旧章节请求和阅读记录回写抢占新章节的问题。",
    "修复移动端重复点击、滑动翻页时章节来回跳转的问题。",
    "后台听书改为单章节连续媒体源，不再在五段边界自动暂停。",
    "重复媒体请求返回同一段音频，修复播放进度与正文光标错位。",
    "播放光标只按服务端精确音频时长推进，不再使用估算时长抢跑。",
    "普通听书会重新创建本章第 0 段播放，不再恢复旧听书会话。",
    "从此处听书继续按所选段落起播，与普通听书入口分离。",
    "听书播放光标会跟随真实音频段位同步滚动。",
    "从此处听书会由服务端识别所选段落，避免选段起播错位。",
    "听书 MP3 不再跨会话持久复用，退出听书会清理本地音频缓存。",
    "服务端连续播放改为五段滚动窗口，避免超长听书请求失败。",
    "继续保留当前书籍封面的后台播放通知展示。",
)


@lru_cache(maxsize=8)
def _file_sha256(path: str, mtime_ns: int, size: int) -> str:
    _ = (mtime_ns, size)
    return sha256(Path(path).read_bytes()).hexdigest()


def _android_latest_apk_metadata() -> dict[str, Any]:
    size = 0
    digest = ""
    if ANDROID_LATEST_APK_PATH.is_file():
        stat_result = ANDROID_LATEST_APK_PATH.stat()
        size = int(stat_result.st_size)
        digest = _file_sha256(
            str(ANDROID_LATEST_APK_PATH),
            int(stat_result.st_mtime_ns),
            size,
        )
    return {
        "url": ANDROID_LATEST_DOWNLOAD_URL,
        "sha256": digest,
        "size_bytes": size,
    }


@lru_cache(maxsize=1)
def index_html_template() -> str:
    return (STATIC_ROOT / "index.html").read_text(encoding="utf-8")


def _seo_text(value: Any, limit: int = 180) -> str:
    text = re.sub(
        r"[\x00-\x1f\x7f]+",
        " ",
        str(value or ""),
    )
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def _public_url(value: Any, fallback: str = "/") -> str:
    candidate = str(value or fallback).strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return f"{SITE_ORIGIN}{candidate}"
    if candidate == SITE_ORIGIN or candidate.startswith(f"{SITE_ORIGIN}/"):
        return candidate.split("#", 1)[0]
    return f"{SITE_ORIGIN}{fallback}"


def _book_seo_title(book: dict[str, Any]) -> str:
    title = _seo_text(book.get("title"), 70) or "未命名作品"
    author = _seo_text(book.get("author"), 30) or "佚名"
    return _seo_text(
        f"{title}全文在线阅读_免费TXT下载_{author}｜{SITE_NAME}",
        100,
    )


def _book_seo_keywords(book: dict[str, Any]) -> str:
    title = _seo_text(book.get("title"), 70) or "未命名作品"
    author = _seo_text(book.get("author"), 30) or "佚名"
    category = _seo_text(book.get("category"), 30) or "中文小说"
    values = (
        title,
        f"{title}在线阅读",
        f"{title}全文阅读",
        f"{title}TXT下载",
        author,
        f"{author}小说",
        category,
        "免费小说阅读",
        "免费小说下载",
        "TXT电子书",
        SITE_NAME,
    )
    return ", ".join(dict.fromkeys(value for value in values if value))


def _book_seo_description(book: dict[str, Any]) -> str:
    title = _seo_text(book.get("title"), 70) or "未命名作品"
    author = _seo_text(book.get("author"), 30) or "佚名"
    category = _seo_text(book.get("category"), 30) or "中文小说"
    summary = _seo_text(book.get("summary"), 150)
    prefix = (
        f"《{title}》是{author}创作的{category}。{SITE_NAME}提供全文在线阅读、"
        "章节目录与TXT电子书下载。"
    )
    return _seo_text(f"{prefix}{summary}", 180)


def _replace_head_value(source: str, pattern: str, replacement: str) -> str:
    updated, count = re.subn(
        pattern,
        lambda _match: replacement,
        source,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError(f"SEO head template mismatch: {pattern}")
    return updated


def _structured_graph(
    *,
    title: str,
    description: str,
    canonical: str,
    entity: dict[str, Any],
) -> str:
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": f"{SITE_ORIGIN}/#organization",
                "url": f"{SITE_ORIGIN}/",
                "name": SITE_NAME,
                "logo": {
                    "@type": "ImageObject",
                    "url": SITE_DEFAULT_IMAGE,
                    "width": 512,
                    "height": 512,
                },
            },
            {
                "@type": "WebSite",
                "@id": f"{SITE_ORIGIN}/#website",
                "url": f"{SITE_ORIGIN}/",
                "name": SITE_NAME,
                "alternateName": "OOH STORY",
                "description": SITE_DESCRIPTION,
                "inLanguage": "zh-CN",
                "image": SITE_DEFAULT_IMAGE,
                "publisher": {"@id": f"{SITE_ORIGIN}/#organization"},
            },
            {
                "@type": "WebPage",
                "@id": f"{canonical}#webpage",
                "url": canonical,
                "name": title,
                "description": description,
                "isPartOf": {"@id": f"{SITE_ORIGIN}/#website"},
                "inLanguage": "zh-CN",
            },
            entity,
        ],
    }
    # Prevent database text from ever terminating the JSON-LD script element.
    return json.dumps(graph, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )


def _seo_html_response(
    *,
    title: str,
    description: str,
    keywords: str,
    canonical: str,
    page_type: str,
    image: str,
    image_alt: str,
    author: str,
    entity: dict[str, Any],
    fallback_html: str = "",
) -> HTMLResponse:
    safe_title = _seo_text(title, 100) or SITE_NAME
    safe_description = _seo_text(description, 180) or SITE_DESCRIPTION
    safe_keywords = _seo_text(keywords, 500) or SITE_KEYWORDS
    safe_author = _seo_text(author, 100) or SITE_NAME
    safe_image_alt = _seo_text(image_alt, 120) or "OOH Story 品牌图标"
    canonical_url = _public_url(canonical)
    image_url = _public_url(image, "/icon-512.png")
    json_ld = _structured_graph(
        title=safe_title,
        description=safe_description,
        canonical=canonical_url,
        entity=entity,
    )
    replacements = (
        (r"<title>.*?</title>", f"<title>{escape(safe_title)}</title>"),
        (
            r'<meta name="description" content="[^"]*">',
            f'<meta name="description" content="{escape(safe_description, quote=True)}">',
        ),
        (
            r'<meta name="keywords" content="[^"]*">',
            f'<meta name="keywords" content="{escape(safe_keywords, quote=True)}">',
        ),
        (
            r'<meta name="author" content="[^"]*">',
            f'<meta name="author" content="{escape(safe_author, quote=True)}">',
        ),
        (
            r'<link rel="canonical" href="[^"]*">',
            f'<link rel="canonical" href="{escape(canonical_url, quote=True)}">',
        ),
        (
            r'<meta property="og:type" content="[^"]*">',
            f'<meta property="og:type" content="{escape(page_type, quote=True)}">',
        ),
        (
            r'<meta property="og:title" content="[^"]*">',
            f'<meta property="og:title" content="{escape(safe_title, quote=True)}">',
        ),
        (
            r'<meta property="og:description" content="[^"]*">',
            f'<meta property="og:description" content="{escape(safe_description, quote=True)}">',
        ),
        (
            r'<meta property="og:url" content="[^"]*">',
            f'<meta property="og:url" content="{escape(canonical_url, quote=True)}">',
        ),
        (
            r'<meta property="og:image" content="[^"]*">',
            f'<meta property="og:image" content="{escape(image_url, quote=True)}">',
        ),
        (
            r'<meta property="og:image:alt" content="[^"]*">',
            f'<meta property="og:image:alt" content="{escape(safe_image_alt, quote=True)}">',
        ),
        (
            r'<meta name="twitter:title" content="[^"]*">',
            f'<meta name="twitter:title" content="{escape(safe_title, quote=True)}">',
        ),
        (
            r'<meta name="twitter:description" content="[^"]*">',
            f'<meta name="twitter:description" content="{escape(safe_description, quote=True)}">',
        ),
        (
            r'<meta name="twitter:image" content="[^"]*">',
            f'<meta name="twitter:image" content="{escape(image_url, quote=True)}">',
        ),
        (
            r'<meta name="twitter:image:alt" content="[^"]*">',
            f'<meta name="twitter:image:alt" content="{escape(safe_image_alt, quote=True)}">',
        ),
    )
    source = index_html_template()
    for pattern, replacement in replacements:
        source = _replace_head_value(source, pattern, replacement)
    source = _replace_head_value(
        source,
        r'<script id="structured-data" type="application/ld\+json">.*?</script>',
        f'<script id="structured-data" type="application/ld+json">{json_ld}</script>',
    )
    if fallback_html:
        loading_html = (
            '<section class="page-state seo-app-loading" role="status" '
            'aria-live="polite">'
            '<span class="loader" aria-hidden="true"></span>'
            '<p>正在打开故事宇宙…</p></section>'
        )
        source, count = re.subn(
            r'<main id="app" tabindex="-1">.*?</main>',
            f'<main id="app" tabindex="-1">{loading_html}{fallback_html}</main>',
            source,
            count=1,
            flags=re.DOTALL,
        )
        if count != 1:
            raise RuntimeError("SEO body template mismatch")
    digest = b64encode(sha256(json_ld.encode("utf-8")).digest()).decode("ascii")
    return HTMLResponse(
        source,
        headers={
            "Cache-Control": "public, max-age=300",
            SEO_CSP_HASH_HEADER: f"'sha256-{digest}'",
        },
    )


def _seo_fallback_article(
    heading: str,
    paragraphs: tuple[str, ...] | list[str],
    links: tuple[tuple[str, str], ...] | list[tuple[str, str]] = (),
) -> str:
    """Build safe, useful first-response content for crawlers and no-JS readers."""
    safe_paragraphs = "".join(
        f"<p>{escape(_seo_text(paragraph, 1_600))}</p>"
        for paragraph in paragraphs
        if _seo_text(paragraph, 1_600)
    )
    safe_links = " · ".join(
        f'<a href="{escape(_public_url(href), quote=True)}">'
        f"{escape(_seo_text(label, 80))}</a>"
        for href, label in links
        if _seo_text(label, 80)
    )
    navigation = f'<nav aria-label="页面导航">{safe_links}</nav>' if safe_links else ""
    return (
        '<article class="seo-fallback">'
        f"<h1>{escape(_seo_text(heading, 160) or SITE_NAME)}</h1>"
        f"{safe_paragraphs}{navigation}</article>"
    )


def _book_entity(
    book: dict[str, Any],
    *,
    canonical: str,
    description: str,
    image: str,
) -> dict[str, Any]:
    genres = list(
        dict.fromkeys(
            value
            for value in (
                _seo_text(book.get("category"), 40),
                *(_seo_text(item, 40) for item in (book.get("genre_tags") or [])),
            )
            if value
        )
    )
    return {
        "@type": "Book",
        "@id": f"{canonical}#book",
        "url": canonical,
        "name": _seo_text(book.get("title"), 160) or "未命名作品",
        "author": {
            "@type": "Person",
            "name": _seo_text(book.get("author"), 100) or "佚名",
        },
        "description": description,
        "genre": genres,
        "image": image,
        "inLanguage": "zh-CN",
    }


def structured_data_csp_hash() -> str:
    index_html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    match = STRUCTURED_DATA_PATTERN.search(index_html)
    if match is None:
        raise RuntimeError("structured-data JSON-LD script is missing")
    digest = b64encode(sha256(match.group(1).encode("utf-8")).digest()).decode("ascii")
    return f"'sha256-{digest}'"


STRUCTURED_DATA_CSP_HASH = structured_data_csp_hash()


def early_bootstrap_csp_hash() -> str:
    index_html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    match = EARLY_BOOTSTRAP_PATTERN.search(index_html)
    if match is None:
        raise RuntimeError("early-bootstrap script is missing")
    digest = b64encode(sha256(match.group(1).encode("utf-8")).digest()).decode("ascii")
    return f"'sha256-{digest}'"


EARLY_BOOTSTRAP_CSP_HASH = early_bootstrap_csp_hash()


def require_public_book_id(value: str) -> str:
    if not PUBLIC_BOOK_ID.fullmatch(value):
        raise HTTPException(status_code=404, detail="作品不存在或正文尚未就绪")
    return value


class PublicMetricEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visitor_id: UUID4

    @field_validator("visitor_id", mode="before")
    @classmethod
    def canonical_uuid4(cls, value: object) -> object:
        if not isinstance(value, str) or not CANONICAL_UUID.fullmatch(value):
            raise ValueError("visitor_id 必须是规范 UUID v4")
        # Parse here as well as through UUID4 so malformed variant/version
        # combinations cannot reach the repository.
        parsed = UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("visitor_id 必须是规范 UUID v4")
        return value


class AudiobookSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str
    chapter_id: int
    client_id: str
    mode: str = "smart"
    narrator: str = "mocheng"
    voice: str = "nuanxi"
    emotion: str = "auto"
    rate: float = 1.0
    resume: bool = True
    start_paragraph_index: int = 0

    @field_validator("book_id")
    @classmethod
    def valid_book_id(cls, value: str) -> str:
        if not PUBLIC_BOOK_ID.fullmatch(value):
            raise ValueError("作品标识无效")
        return value

    @field_validator("chapter_id")
    @classmethod
    def valid_chapter_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("章节标识无效")
        return value

    @field_validator("client_id")
    @classmethod
    def valid_client_id(cls, value: str) -> str:
        clean = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", clean):
            raise ValueError("客户端标识无效")
        return clean

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in {"normal", "smart", "cantonese", "hokkien"}:
            raise ValueError("听书模式无效")
        return value

    @field_validator("emotion")
    @classmethod
    def valid_emotion(cls, value: str) -> str:
        if value != "auto" and value not in TTS_EMOTIONS:
            raise ValueError("情感模式无效")
        return value

    @field_validator("rate")
    @classmethod
    def valid_rate(cls, value: float) -> float:
        if value < 0.5 or value > 3:
            raise ValueError("语速无效")
        return round(value, 2)

    @field_validator("start_paragraph_index")
    @classmethod
    def valid_start_paragraph_index(cls, value: int) -> int:
        if value < 0 or value > 200_000:
            raise ValueError("起始段落无效")
        return value


class AudiobookProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: int
    paragraph_index: int = 0
    item_index: int = 0
    audio_offset_ms: int = 0
    manifest_hash: str | None = None
    stream_id: str | None = None

    @field_validator("chapter_id")
    @classmethod
    def valid_progress_chapter_id(cls, value: int) -> int:
        if value < 1:
            raise ValueError("chapter_id must be positive")
        return value

    @field_validator("paragraph_index", "item_index", "audio_offset_ms")
    @classmethod
    def non_negative_progress(cls, value: int) -> int:
        if value < 0:
            raise ValueError("progress values must be non-negative")
        return value

    @field_validator("manifest_hash")
    @classmethod
    def valid_progress_manifest_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", clean):
            raise ValueError("manifest_hash is invalid")
        return clean

    @field_validator("stream_id")
    @classmethod
    def valid_progress_stream_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean or len(clean) > 128:
            raise ValueError("stream_id is invalid")
        return clean


class AudiobookHlsQueueRequest(BaseModel):
    """Create one owner-bound native media queue for iOS WebKit."""

    model_config = ConfigDict(extra="forbid")

    manifest_hash: str
    start_index: int = 0
    offset_ms: int = 0

    @field_validator("manifest_hash")
    @classmethod
    def valid_hls_manifest_hash(cls, value: str) -> str:
        clean = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", clean):
            raise ValueError("manifest_hash is invalid")
        return clean

    @field_validator("start_index", "offset_ms")
    @classmethod
    def non_negative_hls_position(cls, value: int) -> int:
        if value < 0:
            raise ValueError("HLS playback positions must be non-negative")
        return value


class StaleWhileRevalidateSnapshot:
    """Small per-worker snapshot with a stale-while-revalidate refresh path."""

    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self._condition = threading.Condition()
        self._namespace: object | None = None
        self._value: dict[str, Any] | None = None
        self._expires_at = 0.0
        self._refreshing = False

    def _refresh(
        self,
        builder: Callable[[], dict[str, Any]],
        namespace: object,
        *,
        raise_errors: bool,
    ) -> dict[str, Any] | None:
        try:
            value = builder()
        except RECOVERABLE_INTEGRATION_ERRORS:
            with self._condition:
                if self._namespace is namespace:
                    self._refreshing = False
                    self._condition.notify_all()
            if raise_errors:
                raise
            return None
        with self._condition:
            if self._namespace is namespace:
                self._value = value
                self._expires_at = time.monotonic() + self.ttl_seconds
                self._refreshing = False
                self._condition.notify_all()
        return deepcopy(value)

    def _background_refresh(
        self,
        builder: Callable[[], dict[str, Any]],
        namespace: object,
    ) -> None:
        self._refresh(builder, namespace, raise_errors=False)

    def get(
        self,
        builder: Callable[[], dict[str, Any]],
        namespace: object,
    ) -> dict[str, Any]:
        with self._condition:
            if self._namespace is not namespace:
                self._namespace = namespace
                self._value = None
                self._expires_at = 0.0
                self._refreshing = False
            now = time.monotonic()
            if self._value is not None:
                value = deepcopy(self._value)
                if now >= self._expires_at and not self._refreshing:
                    self._refreshing = True
                    threading.Thread(
                        target=self._background_refresh,
                        args=(builder, namespace),
                        name="oohstory-home-snapshot-refresh",
                        daemon=True,
                    ).start()
                return value
            if self._refreshing:
                self._condition.wait_for(
                    lambda: self._value is not None or not self._refreshing,
                    timeout=35,
                )
                if self._value is not None:
                    return deepcopy(self._value)
            self._refreshing = True
        value = self._refresh(builder, namespace, raise_errors=True)
        if value is None:  # pragma: no cover - raise_errors always raises
            raise RuntimeError("首页快照生成失败")
        return value

    def invalidate(self) -> None:
        """Drop a snapshot after an explicit reader engagement mutation."""
        with self._condition:
            self._namespace = None
            self._value = None
            self._expires_at = 0.0
            self._refreshing = False
            self._condition.notify_all()


settings = load_settings()
_home_primary_snapshot = StaleWhileRevalidateSnapshot(HOME_PRIMARY_CACHE_SECONDS)
_home_secondary_snapshot = StaleWhileRevalidateSnapshot(HOME_SECONDARY_CACHE_SECONDS)


AUDIOBOOK_STREAM_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
AUDIOBOOK_MANIFEST_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
AUDIOBOOK_STREAM_BATCH_SEGMENTS = 5


def _normalize_audiobook_stream_id(stream_id: str | None) -> str | None:
    raw = str(stream_id or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if AUDIOBOOK_STREAM_ID_PATTERN.fullmatch(lowered):
        return lowered
    if len(raw) > 128:
        return None
    return sha256(f"legacy-stream:{raw}".encode("utf-8", "surrogatepass")).hexdigest()[
        :32
    ]


def _audiobook_stream_receipt_path(
    session_id: str, manifest_hash: str, stream_id: str
) -> Path:
    if not AUDIOBOOK_STREAM_ID_PATTERN.fullmatch(session_id):
        raise ValueError("invalid audiobook session id")
    if not AUDIOBOOK_MANIFEST_HASH_PATTERN.fullmatch(manifest_hash):
        raise ValueError("invalid audiobook manifest hash")
    if not AUDIOBOOK_STREAM_ID_PATTERN.fullmatch(stream_id):
        raise ValueError("invalid audiobook stream id")
    return (
        settings.audiobook_storage_root
        / "audiobook-stream-receipts"
        / session_id
        / f"{manifest_hash}.{stream_id}.complete"
    )


def _audiobook_stream_intent_path(
    session_id: str, manifest_hash: str, stream_id: str
) -> Path:
    receipt = _audiobook_stream_receipt_path(session_id, manifest_hash, stream_id)
    return receipt.with_suffix(".intent")


def _audiobook_stream_cursor_path(
    session_id: str, manifest_hash: str, stream_id: str
) -> Path:
    receipt = _audiobook_stream_receipt_path(session_id, manifest_hash, stream_id)
    return receipt.with_suffix(".cursor")


def _read_audiobook_stream_cursor(
    session_id: str, manifest_hash: str, stream_id: str
) -> tuple[int, int] | None:
    target = _audiobook_stream_cursor_path(session_id, manifest_hash, stream_id)
    try:
        raw = target.read_text(encoding="ascii").strip()
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        try:
            return max(0, int(raw)), 0
        except ValueError:
            return None
    if not isinstance(payload, dict):
        return None
    try:
        segment_index = max(0, int(payload.get("segment_index") or 0))
        audio_offset_ms = max(0, int(payload.get("audio_offset_ms") or 0))
        started_at_ms = max(0, int(payload.get("started_at_ms") or 0))
        duration_ms = max(0, int(payload.get("duration_ms") or 0))
        next_start = max(segment_index, int(payload.get("next_start") or segment_index))
    except (TypeError, ValueError):
        return None
    if started_at_ms and duration_ms:
        elapsed_ms = max(0, int(time.time() * 1000) - started_at_ms)
        audio_offset_ms = min(duration_ms, audio_offset_ms + elapsed_ms)
        if audio_offset_ms >= duration_ms:
            return next_start, 0
    return segment_index, audio_offset_ms


def _write_audiobook_stream_cursor(
    session_id: str,
    manifest_hash: str,
    stream_id: str,
    segment_index: int,
    *,
    audio_offset_ms: int = 0,
    duration_ms: int = 0,
    started_at_ms: int | None = None,
    next_start: int | None = None,
    source: str = "stream",
) -> None:
    target = _audiobook_stream_cursor_path(session_id, manifest_hash, stream_id)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = target.with_suffix(".cursor.lock")
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        now_ms = int(time.time() * 1000)
        if source == "stream":
            try:
                existing = json.loads(target.read_text(encoding="ascii"))
            except (OSError, TypeError, ValueError):
                existing = {}
            try:
                existing_updated_at_ms = int(existing.get("updated_at_ms") or 0)
            except (AttributeError, TypeError, ValueError):
                existing_updated_at_ms = 0
            if (
                isinstance(existing, dict)
                and existing.get("source") == "playback"
                and now_ms - existing_updated_at_ms < 15_000
            ):
                return
        payload = {
            "segment_index": max(0, int(segment_index)),
            "audio_offset_ms": max(0, int(audio_offset_ms)),
            "duration_ms": max(0, int(duration_ms)),
            "started_at_ms": max(0, int(started_at_ms or 0)),
            "next_start": max(
                0,
                int(segment_index if next_start is None else next_start),
            ),
            "source": "playback" if source == "playback" else "stream",
            "updated_at_ms": now_ms,
        }
        temporary = target.with_name(
            f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}"
        )
        try:
            with temporary.open("x", encoding="ascii") as handle:
                handle.write(json.dumps(payload, separators=(",", ":")))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _write_audiobook_stream_cursor_safely(*args: Any, **kwargs: Any) -> bool:
    try:
        _write_audiobook_stream_cursor(*args, **kwargs)
    except OSError:
        return False
    return True


def _claim_audiobook_stream_intent(
    session_id: str, manifest_hash: str, stream_id: str | None
) -> bool:
    """Claim one logical play intent across workers and media reconnects.

    Browsers may repeat the same media URL while playing. Only the first
    request for a client-generated stream ID consumes request quota; later
    requests resume from its confirmed playback cursor.
    """
    if not stream_id:
        return True
    target = _audiobook_stream_intent_path(session_id, manifest_hash, stream_id)
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError:
        # Fail safe: a storage fault must not disable abuse protection.
        return True
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write("intent\n")
    return True


def _mp3_frame_payload(audio: bytes) -> bytes:
    """Remove leading ID3v2 metadata before concatenating MP3 segments.

    Every independently generated segment can carry its own ID3 header. Raw
    byte concatenation leaves those headers in the middle of the chapter and
    mobile decoders may skip the following dialogue as corrupt data. MP3 audio
    frames are safe to concatenate after all leading ID3v2 tags are removed.
    """
    payload = bytes(audio)
    while payload.startswith(b"ID3") and len(payload) >= 10:
        if any(byte & 0x80 for byte in payload[6:10]):
            break
        tag_size = sum(
            (payload[6 + index] & 0x7F) << (21 - 7 * index) for index in range(4)
        )
        total = 10 + tag_size + (10 if payload[5] & 0x10 else 0)
        if total <= 10 or total >= len(payload):
            break
        payload = payload[total:]
    return payload


def _mark_audiobook_stream_complete(
    session_id: str, manifest_hash: str, stream_id: str
) -> None:
    target = _audiobook_stream_receipt_path(session_id, manifest_hash, stream_id)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        with temporary.open("x", encoding="ascii") as handle:
            handle.write("complete\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _consume_audiobook_stream_receipt(
    session_id: str, manifest_hash: str, stream_id: str
) -> bool:
    target = _audiobook_stream_receipt_path(session_id, manifest_hash, stream_id)
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    try:
        target.parent.rmdir()
    except OSError:
        pass
    return True


def _delete_audiobook_session_receipts(session_id: str) -> None:
    """Remove transient receipts and session-scoped audio owned by one session."""
    if not AUDIOBOOK_STREAM_ID_PATTERN.fullmatch(session_id):
        return
    directory = (
        settings.audiobook_storage_root / "audiobook-stream-receipts" / session_id
    )
    shutil.rmtree(directory, ignore_errors=True)


def _audiobook_session_segment_paths(
    session_id: str,
    manifest_hash: str,
    segment: dict[str, Any],
) -> tuple[Path, Path]:
    if not AUDIOBOOK_STREAM_ID_PATTERN.fullmatch(session_id):
        raise ValueError("invalid audiobook session id")
    if not AUDIOBOOK_MANIFEST_HASH_PATTERN.fullmatch(manifest_hash):
        raise ValueError("invalid audiobook manifest hash")
    segment_hash = str(segment.get("sha256") or "")
    if not AUDIOBOOK_MANIFEST_HASH_PATTERN.fullmatch(segment_hash):
        segment_hash = sha256(
            json.dumps(segment, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
    directory = (
        settings.audiobook_storage_root
        / "audiobook-stream-receipts"
        / session_id
        / "audio"
        / manifest_hash
    )
    target = directory / f"{segment_hash}.mp3"
    return target, target.with_suffix(".lock")


def _read_audiobook_session_segment(target: Path) -> bytes | None:
    try:
        audio = target.read_bytes()
    except OSError:
        return None
    return audio or None


def _claim_audiobook_session_segment(lock: Path) -> bool:
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime > 300:
                lock.unlink()
        except OSError:
            pass
        return False
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(f"{os.getpid()}\n")
    return True


def _publish_audiobook_session_segment(target: Path, audio: bytes) -> None:
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        with temporary.open("x+b") as handle:
            handle.write(audio)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _audiobook_hls_queue_path(session_id: str, queue_id: str) -> Path:
    if not AUDIOBOOK_STREAM_ID_PATTERN.fullmatch(session_id):
        raise ValueError("invalid audiobook session id")
    if not AUDIOBOOK_STREAM_ID_PATTERN.fullmatch(queue_id):
        raise ValueError("invalid audiobook HLS queue id")
    return (
        settings.audiobook_storage_root
        / "audiobook-stream-receipts"
        / session_id
        / "hls-queues"
        / f"{queue_id}.json"
    )


def _write_json_atomically(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}"
    )
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_audiobook_hls_queue(target: Path) -> dict[str, Any]:
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise KeyError("HLS queue not found") from exc
    if not isinstance(payload, dict):
        raise KeyError("HLS queue not found")
    return payload


def _audiobook_hls_queue_snapshot(
    session_id: str,
    queue_id: str,
    owner: str,
    *,
    initial: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read and gently extend one append-only HLS event playlist.

    The media playlist always retains its original prefix, so WebKit sees a
    stable media timeline. Initial creation attaches four chapters. Later
    playlist/status reloads append at most one chapter every ten seconds,
    keeping metadata work bounded while staying far ahead of playback.
    """

    target = _audiobook_hls_queue_path(session_id, queue_id)
    lock_path = target.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        queue = _read_audiobook_hls_queue(target)
        hashes = [str(value).lower() for value in queue.get("manifest_hashes") or []]
        if not hashes or any(
            not AUDIOBOOK_MANIFEST_HASH_PATTERN.fullmatch(value) for value in hashes
        ):
            raise KeyError("HLS queue is invalid")
        service = audiobook_service()
        now = int(time.time())
        try:
            expanded_at = int(queue.get("expanded_at") or 0)
        except (TypeError, ValueError):
            expanded_at = 0
        target_count = max(len(hashes), 4) if initial else len(hashes)
        if not initial and not queue.get("complete") and now - expanded_at >= 10:
            target_count = min(512, len(hashes) + 1)
        while len(hashes) < target_count and not queue.get("complete"):
            current = service.manifest(session_id, hashes[-1], owner)
            following_id = int(current.get("next_chapter_id") or 0)
            if following_id <= 0:
                queue["complete"] = True
                break
            following = service.prefetch_next(
                session_id,
                owner,
                from_chapter_id=int(current.get("chapter_id") or 0),
            )
            if not following:
                queue["complete"] = True
                break
            following_hash = str(following.get("manifest_hash") or "").lower()
            if (
                not AUDIOBOOK_MANIFEST_HASH_PATTERN.fullmatch(following_hash)
                or following_hash in hashes
            ):
                queue["complete"] = True
                break
            hashes.append(following_hash)
        if len(hashes) >= 512:
            # A playback session expires after twelve hours. 512 chapters is
            # safely beyond that horizon and bounds an adversarial playlist.
            queue["complete"] = True
        queue["manifest_hashes"] = hashes
        queue["expanded_at"] = now
        queue["updated_at"] = now
        _write_json_atomically(target, queue)
    manifests = [
        audiobook_service().manifest(session_id, manifest_hash, owner)
        for manifest_hash in hashes
    ]
    return queue, manifests


def _audiobook_hls_segment_duration(
    segment: dict[str, Any], measured_ms: int = 0
) -> float:
    if measured_ms > 0:
        return max(0.1, measured_ms / 1000)
    text = list(str(segment.get("text") or ""))
    punctuation = sum(1 for char in text if char in "，。！？；：,.!?;:…—")
    try:
        rate = max(0.5, min(float(segment.get("rate") or 1), 3.0))
    except (TypeError, ValueError):
        rate = 1.0
    return max(0.8, ((len(text) / 4.45) + (punctuation * 0.12)) / rate)


def _audiobook_hls_queue_items(
    _session_id: str,
    _owner: str,
    queue: dict[str, Any],
    manifests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        first_start = max(0, int(queue.get("start_index") or 0))
    except (TypeError, ValueError):
        first_start = 0
    elapsed = 0.0
    items: list[dict[str, Any]] = []
    for chapter_position, manifest in enumerate(manifests):
        segments = list(manifest.get("segments") or [])
        for fallback_index, segment in enumerate(segments):
            absolute_index = int(segment.get("index") or fallback_index)
            if chapter_position == 0 and absolute_index < first_start:
                continue
            # Keep EXTINF values immutable for the life of the EVENT playlist.
            # Updating an estimate after synthesis would shift every following
            # media timestamp and makes AVFoundation restart or stall.
            duration = _audiobook_hls_segment_duration(segment)
            items.append(
                {
                    "chapter_id": int(manifest.get("chapter_id") or 0),
                    "chapter_title": str(manifest.get("title") or ""),
                    "next_chapter_id": int(manifest.get("next_chapter_id") or 0),
                    "manifest_hash": str(manifest.get("manifest_hash") or ""),
                    "index": absolute_index,
                    "paragraph_index": int(segment.get("paragraph_index") or 0),
                    "text": str(segment.get("text") or ""),
                    "emotion": str(segment.get("emotion") or "neutral"),
                    "rate": float(segment.get("rate") or 1),
                    "duration_seconds": round(duration, 3),
                    "duration_exact": False,
                    "queue_offset_seconds": round(elapsed, 3),
                }
            )
            elapsed += duration
    return items


def _audiobook_hls_queue_response(
    session_id: str,
    queue_id: str,
    owner: str,
    *,
    initial: bool = False,
) -> dict[str, Any]:
    queue, manifests = _audiobook_hls_queue_snapshot(
        session_id, queue_id, owner, initial=initial
    )
    return {
        "queue_id": queue_id,
        "playlist_endpoint": (
            f"/api/v1/audiobook/sessions/{session_id}/hls/queues/{queue_id}.m3u8"
        ),
        "status_endpoint": (
            f"/api/v1/audiobook/sessions/{session_id}/hls/queues/{queue_id}/status"
        ),
        "offset_ms": max(0, int(queue.get("offset_ms") or 0)),
        "complete": bool(queue.get("complete")),
        "items": _audiobook_hls_queue_items(session_id, owner, queue, manifests),
    }


def _audiobook_hls_segment_paths(
    session_id: str,
    manifest_hash: str,
    segment: dict[str, Any],
) -> tuple[Path, Path]:
    target, _mp3_lock = _audiobook_session_segment_paths(
        session_id, manifest_hash, segment
    )
    hls_target = target.parent / "hls" / f"{target.stem}.ts"
    return hls_target, hls_target.with_suffix(".lock")


def _transcode_audiobook_hls_segment(audio: bytes) -> bytes:
    completed = subprocess.run(
        [
            "/usr/bin/ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-vn",
            "-map_metadata",
            "-1",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-mpegts_flags",
            "+resend_headers",
            "-muxdelay",
            "0",
            "-muxpreload",
            "0",
            "-f",
            "mpegts",
            "pipe:1",
        ],
        input=audio,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError("HLS audio transcoding failed")
    return completed.stdout


def _audiobook_byte_range_response(
    data: bytes,
    request: Request,
    *,
    media_type: str,
    headers: dict[str, str],
) -> Response:
    response_headers = {**headers, "Accept-Ranges": "bytes"}
    range_header = str(request.headers.get("range") or "").strip()
    if not range_header:
        return Response(content=data, media_type=media_type, headers=response_headers)
    matched = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
    if not matched or not data:
        return Response(
            status_code=416,
            headers={**response_headers, "Content-Range": f"bytes */{len(data)}"},
        )
    raw_start, raw_end = matched.groups()
    if raw_start:
        start = int(raw_start)
        end = int(raw_end) if raw_end else len(data) - 1
    elif raw_end:
        suffix = int(raw_end)
        start = max(0, len(data) - suffix)
        end = len(data) - 1
    else:
        start, end = 0, len(data) - 1
    if start >= len(data) or start < 0 or end < start:
        return Response(
            status_code=416,
            headers={**response_headers, "Content-Range": f"bytes */{len(data)}"},
        )
    end = min(end, len(data) - 1)
    return Response(
        content=data[start : end + 1],
        status_code=206,
        media_type=media_type,
        headers={
            **response_headers,
            "Content-Range": f"bytes {start}-{end}/{len(data)}",
        },
    )


def _purge_cancelled_audiobook_session(session_id: str, owner_key: str) -> None:
    """Physically remove the exact cancelled session after access is revoked."""
    if not AUDIOBOOK_STREAM_ID_PATTERN.fullmatch(
        session_id
    ) or not AUDIOBOOK_MANIFEST_HASH_PATTERN.fullmatch(owner_key):
        return
    repository_owner = getattr(audiobook_service(), "repository", None)
    mysql = getattr(repository_owner, "_mysql", None)
    if mysql is None:
        return
    with mysql.pool.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM audiobook_sessions WHERE session_id=%s "
                "AND owner_hash=UNHEX(%s) AND cancelled=1",
                (session_id, owner_key),
            )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Each Uvicorn worker owns its own tiny snapshot. Prime both halves without
    # delaying readiness so the first visitor normally receives a warm result.
    def prime_home_snapshots() -> None:
        try:
            home_primary()
            home_secondary()
        except RECOVERABLE_INTEGRATION_ERRORS:
            # A later request performs the same guarded cold build. Readiness
            # and unrelated routes must not fail because optional prewarming did.
            return

    threading.Thread(
        target=prime_home_snapshots,
        name="oohstory-home-snapshots-prime",
        daemon=True,
    ).start()
    yield


app = FastAPI(
    title="OOH Story",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=list(settings.allowed_hosts),
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

_rate_limiter = RateLimiter(
    global_rate=2.0,
    global_burst=15,
    sitemap_rate=0.05,
    sitemap_burst=2,
    chapter_rate=0.2,
    chapter_burst=3,
    ban_threshold=10,
    ban_duration=7200,
    reader_guard_enabled=True,
)


@lru_cache(maxsize=1)
def repository() -> LibraryRepository:
    return LibraryRepository(settings)


@lru_cache(maxsize=1)
def audiobook_service() -> AudiobookService:
    return AudiobookService(repository())


@lru_cache(maxsize=1)
def account_store() -> AccountStore:
    return AccountStore(
        settings.user_database_path,
        session_ttl_seconds=settings.session_ttl_seconds,
    )


@lru_cache(maxsize=1)
def comment_store() -> CommentStore:
    return CommentStore(repository(), settings.comment_object_root)


category_manager = CategoryManager(lambda: account_store(), lambda: repository())


def invalidate_home_snapshots() -> None:
    _home_primary_snapshot.invalidate()
    _home_secondary_snapshot.invalidate()


user_router = create_user_router(
    settings,
    repository,
    on_public_metrics_changed=invalidate_home_snapshots,
    store_provider=account_store,
    category_provider=lambda: category_manager.items(),
    on_logout=lambda _raw, session, request: _cancel_audiobook_logout(
        int(session.user_id), request
    ),
    comment_provider=comment_store,
)
app.include_router(user_router)

admin_router = create_admin_router(
    settings,
    store_provider=account_store,
    repository_provider=repository,
    category_manager=category_manager,
    on_catalog_changed=invalidate_home_snapshots,
)
app.include_router(admin_router)

app.add_api_route(
    "/",
    getattr(user_router, "google_redirect_handler"),
    methods=["POST"],
    include_in_schema=False,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    blocked = _rate_limiter.check(request)
    if blocked is not None:
        return blocked
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("origin")
        if origin:
            allowed_origins = {SITE_ORIGIN, SITE_ORIGIN.replace("://", "://www.")}
            if request.url.path == "/":
                allowed_origins.add("https://accounts.google.com")
            content_type = (
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            )
            google_null_origin_form = (
                request.method == "POST"
                and request.url.path == "/"
                and origin == "null"
                and content_type
                in {
                    "application/x-www-form-urlencoded",
                    "multipart/form-data",
                }
            )
            if (
                origin.rstrip("/") not in allowed_origins
                and not google_null_origin_form
            ):
                return JSONResponse(
                    {"detail": "跨站请求已拒绝"},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
    maintenance_enabled = settings.maintenance_flag_path.is_file()
    maintenance_bypass = request.url.path == "/healthz" or (
        request.method in {"GET", "HEAD"}
        and request.url.path in MAINTENANCE_STATIC_PATHS
    )
    if maintenance_enabled and not maintenance_bypass:
        if request.method in {"GET", "HEAD"} and not request.url.path.startswith(
            "/api/"
        ):
            response = HTMLResponse(
                MAINTENANCE_PAGE.read_text(encoding="utf-8"),
                status_code=503,
            )
        else:
            response = JSONResponse(
                {
                    "detail": "OOHStory 正在维护，请稍后重试",
                    "maintenance": True,
                },
                status_code=503,
            )
        response.headers["Retry-After"] = "300"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response.headers["Content-Language"] = "zh-CN"
    else:
        response = await call_next(request)
    audiobook_client = request.headers.get("x-audiobook-client", "")
    if (
        request.url.path.startswith("/api/v1/audiobook/")
        and request.method != "DELETE"
        and AUDIOBOOK_CLIENT_ID_RE.fullmatch(audiobook_client)
        and AUDIOBOOK_CLIENT_COOKIE not in response.headers.get("set-cookie", "")
    ):
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        response.set_cookie(
            AUDIOBOOK_CLIENT_COOKIE,
            audiobook_client,
            max_age=43_200,
            httponly=True,
            secure=request.url.scheme == "https" or forwarded_proto == "https",
            samesite="lax",
            path="/api/v1/audiobook/",
        )
    dynamic_script_hash = response.headers.get(SEO_CSP_HASH_HEADER, "")
    if SEO_CSP_HASH_HEADER in response.headers:
        del response.headers[SEO_CSP_HASH_HEADER]
    script_sources = [
        "'self'",
        "https://static.cloudflareinsights.com",
        "https://challenges.cloudflare.com",
    ]
    if settings.google_web_client_id:
        script_sources.append("https://accounts.google.com/gsi/client")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type == "text/html":
        # Cloudflare JavaScript Detections copies a nonce found in the CSP
        # response header onto the inline challenge script it injects.
        script_sources.extend(
            [
                EARLY_BOOTSTRAP_CSP_HASH,
                dynamic_script_hash or STRUCTURED_DATA_CSP_HASH,
                f"'nonce-{secrets.token_urlsafe(24)}'",
            ]
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    # Google Identity Services opens its account chooser in a popup and must
    # keep a reference to the opener long enough to return the ID token.  A
    # strict `same-origin` policy severs that channel and leaves some browsers
    # (notably iOS WebKit) showing an empty popup.
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob: data:; "
        "style-src 'self' 'unsafe-inline'; "
        f"script-src {' '.join(script_sources)}; script-src-attr 'none'; "
        "connect-src 'self' https://cloudflareinsights.com https://*.cloudflareinsights.com "
        + ("https://accounts.google.com; " if settings.google_web_client_id else "; ")
        + (
            "frame-src https://accounts.google.com; "
            if settings.google_web_client_id
            else ""
        )
        + "font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    if (
        request.method not in {"GET", "HEAD"}
        or request.url.path.startswith("/api/v1/auth/")
        or request.url.path.startswith("/api/v1/me/")
    ):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path == "/api/v1/home/primary":
        response.headers["Cache-Control"] = (
            "public, max-age=60, stale-while-revalidate=300"
        )
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    elif request.url.path in {
        "/api/v1/home",
        "/api/v1/home/secondary",
        "/api/v1/recommendations",
        "/api/v1/rankings",
    } or re.fullmatch(
        r"/api/v1/books/[A-Za-z0-9_-]{22}/metrics",
        request.url.path,
    ):
        response.headers["Cache-Control"] = "no-cache"
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    elif response.status_code < 400 and re.fullmatch(
        r"/books/[A-Za-z0-9_-]{22}/chapters/[1-9][0-9]*",
        request.url.path,
    ):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["CDN-Cache-Control"] = "no-store"
        response.headers["Cloudflare-CDN-Cache-Control"] = "no-store"
    elif re.fullmatch(
        r"/api/v1/books/[A-Za-z0-9_-]{22}/chapters/[1-9][0-9]*",
        request.url.path,
    ):
        # Chapter bodies must reach the origin so source-IP blocks and future
        # behavior controls cannot be bypassed by an edge cache. The Web and
        # mobile readers already keep their own bounded chapter caches.
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["CDN-Cache-Control"] = "no-store"
        response.headers["Cloudflare-CDN-Cache-Control"] = "no-store"
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    elif request.url.path.startswith("/api/") and response.status_code < 400:
        response.headers.setdefault("Cache-Control", "public, max-age=60")
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    elif response.status_code == 404 and "/cover" in request.url.path:
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif response.status_code >= 400:
        response.headers["Cache-Control"] = "no-store"
    if content_type == "text/html":
        # Cloudflare JavaScript Detections rewrites eligible HTML responses and
        # currently injects a deprecated StorageType.persistent call. The
        # standard no-transform directive keeps origin HTML intact without
        # weakening CSP or changing API/static asset caching.
        cache_directives = [
            directive.strip()
            for directive in response.headers.get("Cache-Control", "").split(",")
            if directive.strip()
        ]
        if not any(
            directive.casefold() == "no-transform" for directive in cache_directives
        ):
            cache_directives.append("no-transform")
        response.headers["Cache-Control"] = ", ".join(cache_directives)
    return response


@app.exception_handler(NotFoundError)
async def not_found_handler(_request: Request, exc: NotFoundError):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(InputError)
async def input_handler(_request: Request, exc: InputError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(LibraryError)
async def library_handler(_request: Request, _exc: LibraryError):
    return JSONResponse({"detail": "书库请求无法完成"}, status_code=400)


@app.exception_handler(RequestValidationError)
async def validation_handler(_request: Request, _exc: RequestValidationError):
    return JSONResponse({"detail": "请求参数无效"}, status_code=422)


@app.get("/healthz")
def health():
    repository()
    return {"status": "ok"}


@app.get("/api/v1/app/android/latest")
def android_latest_app(
    version_code: int = Query(default=0, ge=0),
    version_name: str = Query(default="", max_length=32),
):
    apk = _android_latest_apk_metadata()
    available = version_code < ANDROID_LATEST_VERSION_CODE
    return {
        "platform": "android",
        "available": available,
        "current": {
            "version_name": version_name,
            "version_code": version_code,
        },
        "latest": {
            "version_name": ANDROID_LATEST_VERSION_NAME,
            "version_code": ANDROID_LATEST_VERSION_CODE,
            "release_date": ANDROID_LATEST_RELEASE_DATE,
            "download_url": apk["url"],
            "sha256": apk["sha256"],
            "size_bytes": apk["size_bytes"],
            "release_notes_public": list(ANDROID_LATEST_RELEASE_NOTES_PUBLIC),
        },
    }


@app.get("/api/v1/admin/rate-limit-stats")
def rate_limit_stats(request: Request):
    token = request.headers.get("authorization", "")
    if token.startswith("Bearer "):
        token = token[7:].strip()
    try:
        session = account_store().validate_session(token)
        if session.role not in {"admin", "owner"}:
            raise HTTPException(status_code=403, detail="Forbidden")
    except Exception:
        raise HTTPException(status_code=403, detail="Forbidden")
    return _rate_limiter.stats()


def _build_home_primary(repo: LibraryRepository) -> dict[str, Any]:
    featured = repo.list_books(
        page=1,
        page_size=HOME_FEATURED_BOOK_COUNT,
        sort="recent",
    )["items"]
    for book in featured:
        try:
            chapter_count = repo.reader_chapter_count(book["public_id"])
        except (LibraryError, OSError, KeyError, TypeError, ValueError):
            continue
        book["chapter_count"] = chapter_count
        book["approx_chapter_count"] = chapter_count
    return {
        "stats": repo.stats(),
        "categories": category_manager.public_items(),
        "featured": category_manager.decorate_books(featured),
        "recommendations": category_manager.decorate_books(
            repo.random_recommendations(14)
        ),
    }


def _build_home_secondary(repo: LibraryRepository) -> dict[str, Any]:
    return {
        "long_novels": category_manager.decorate_books(
            repo.random_recommendations(7, words="over_1m")
        ),
        "short_novels": category_manager.decorate_books(
            repo.random_recommendations(30, words="under_100k")
        ),
        "category_books": category_manager.decorate_category_books(
            repo.category_books(10)
        ),
        "rankings": {
            key: category_manager.decorate_books(items)
            for key, items in repo.rankings(10).items()
        },
        "deconstructions": repo.list_deconstructions(),
    }


@app.get("/api/v1/home/primary")
def home_primary():
    repo = repository()
    return _home_primary_snapshot.get(
        lambda: _build_home_primary(repo),
        repo,
    )


@lru_cache(maxsize=32)
def _mobile_hero_cover_bytes(
    path_text: str,
    modified_ns: int,
    source_size: int,
) -> bytes:
    del modified_ns, source_size
    with Image.open(path_text) as source:
        normalized = ImageOps.exif_transpose(source).convert("RGB")
        mobile = ImageOps.fit(
            normalized,
            (160, 240),
            method=Image.Resampling.LANCZOS,
        )
        output = BytesIO()
        mobile.save(
            output,
            format="JPEG",
            quality=48,
            optimize=True,
            progressive=True,
        )
    return output.getvalue()


@lru_cache(maxsize=64)
def _media_cover_art_bytes(
    path_text: str,
    modified_ns: int,
    source_size: int,
) -> bytes:
    del modified_ns, source_size
    with Image.open(path_text) as source:
        normalized = ImageOps.exif_transpose(source).convert("RGB")
        normalized.thumbnail((512, 512), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (512, 512), (246, 247, 249))
        left = (512 - normalized.width) // 2
        top = (512 - normalized.height) // 2
        canvas.paste(normalized, (left, top))
        output = BytesIO()
        canvas.save(
            output,
            format="JPEG",
            quality=82,
            optimize=True,
            progressive=True,
        )
    return output.getvalue()


@app.api_route(
    "/api/v1/home/hero-cover",
    methods=["GET", "HEAD"],
)
def home_hero_cover(
    variant: str = Query("", pattern="^(|mobile)$"),
):
    """Serve the current lead cover from a stable, preloadable URL."""
    featured = home_primary().get("featured", [])
    if not featured:
        raise HTTPException(status_code=404, detail="当前没有推荐作品")
    lead = featured[0]
    cover_url = str(lead.get("cover_url") or "")
    repo = repository()
    if cover_url.startswith("/api/v1/assets/default-cover"):
        path = repo.shared_default_cover_path()
    else:
        book_id = require_public_book_id(str(lead.get("public_id") or ""))
        path, _version = repo.cover_path_and_version(book_id)
    if variant == "mobile":
        stat = path.stat()
        return Response(
            _mobile_hero_cover_bytes(
                str(path),
                stat.st_mtime_ns,
                stat.st_size,
            ),
            media_type="image/jpeg",
            headers={
                "Cache-Control": (
                    "public, max-age=60, stale-while-revalidate=300, no-transform"
                )
            },
        )
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": (
                "public, max-age=60, stale-while-revalidate=300, no-transform"
            )
        },
    )


@app.get("/api/v1/home/secondary")
def home_secondary():
    repo = repository()
    return _home_secondary_snapshot.get(
        lambda: _build_home_secondary(repo),
        repo,
    )


@app.get("/api/v1/home")
def home():
    # Backwards-compatible aggregate for older clients. New clients request the
    # primary snapshot first and lazy-load the secondary snapshot near view.
    return {**home_primary(), **home_secondary()}


@app.get("/api/v1/recommendations")
def recommendations():
    return {
        "items": category_manager.decorate_books(
            repository().random_recommendations(14)
        )
    }


@app.get("/api/v1/categories")
def categories():
    return {"items": category_manager.public_items()}


@app.get("/api/v1/books")
def books(
    q: str = Query("", max_length=80),
    category: str = Query("", max_length=40),
    words: str = Query(
        "",
        pattern="^(|under_100k|over_100k|over_200k|over_300k|over_500k|over_1m|over_2m)$",
    ),
    serialization: str = Query("", pattern="^(|finished|ongoing)$"),
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(24, ge=1, le=48),
    sort: str = Query("recent", pattern="^(recent|title|long)$"),
):
    try:
        source_category = category_manager.resolve_source(category) if category else ""
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    result = repository().list_books(
        query=q,
        category=source_category,
        words=words,
        serialization=serialization,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    result["items"] = category_manager.decorate_books(result["items"])
    result["category"] = category
    return result


@app.get("/api/v1/books/{book_id}")
def book(book_id: str):
    book_id = require_public_book_id(book_id)
    return category_manager.decorate_book(repository().get_book(book_id))


@app.get("/api/v1/books/{book_id}/metrics")
def book_metrics(book_id: str):
    book_id = require_public_book_id(book_id)
    return repository().public_metrics(book_id)


@app.post("/api/v1/books/{book_id}/metrics/{event}")
def record_book_metric(
    book_id: str,
    event: str,
    payload: PublicMetricEvent,
    request: Request,
):
    book_id = require_public_book_id(book_id)
    if event == "recommend":
        raise HTTPException(
            status_code=401,
            detail="请登录后捐赠 1 小时阅读经验时长，为这本好书助力推荐",
        )
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="仅接受 application/json")
    origin = request.headers.get("origin")
    if origin:
        same_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origin.rstrip("/") != same_origin:
            raise HTTPException(status_code=403, detail="跨站请求已拒绝")
    result = repository().record_public_metric(
        book_id,
        str(payload.visitor_id),
        event,
    )
    return result


@app.get("/api/v1/books/{book_id}/cover")
def cover(
    book_id: str,
    v: str = "",
    variant: str = Query("", pattern="^(|media-art)$"),
):
    book_id = require_public_book_id(book_id)
    path, current_version = repository().cover_path_and_version(book_id)
    if variant == "media-art":
        stat = path.stat()
        return Response(
            _media_cover_art_bytes(str(path), stat.st_mtime_ns, stat.st_size),
            media_type="image/jpeg",
            headers={
                "Cache-Control": (
                    "public, max-age=300, stale-while-revalidate=3600, no-transform"
                )
            },
        )
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    cache_control = (
        "public, max-age=31536000, immutable, no-transform"
        if secrets.compare_digest(v, current_version)
        else "public, max-age=0, must-revalidate"
    )
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": cache_control},
    )


@app.get("/api/v1/assets/default-cover")
def default_cover():
    path = repository().shared_default_cover_path()
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable, no-transform"},
    )


@app.api_route(
    "/api/v1/books/{book_id}/download",
    methods=["GET", "HEAD"],
)
def download_book(book_id: str):
    book_id = require_public_book_id(book_id)
    path, filename = repository().download_source(book_id)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Download-Options": "noopen",
        },
    )


@app.get("/api/v1/books/{book_id}/chapters")
def chapter_catalog(book_id: str):
    book_id = require_public_book_id(book_id)
    return repository().reader_catalog(book_id)


@app.get("/api/v1/books/{book_id}/chapters/{chapter_id}")
def chapter(request: Request, book_id: str, chapter_id: int):
    book_id = require_public_book_id(book_id)
    payload = dict(repository().reader_chapter(book_id, chapter_id))
    probe = _rate_limiter.reader_probe_path(request, book_id, chapter_id)
    if probe:
        payload["_reader_probe"] = probe
    return payload


@app.get(
    "/api/v1/reader-probe/{book_id}/{chapter_id}/{token}",
    include_in_schema=False,
)
def reader_probe(
    request: Request,
    book_id: str,
    chapter_id: int,
    token: str,
):
    book_id = require_public_book_id(book_id)
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise HTTPException(status_code=404, detail="Not Found")
    _rate_limiter.record_reader_probe(request, book_id, chapter_id, token)
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/api/v1/books/{book_id}/illustrations/{subpath:path}")
def illustration(book_id: str, subpath: str):
    book_id = require_public_book_id(book_id)
    path = repository().illustration_path(book_id, subpath)
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@app.get("/api/v1/rankings")
def rankings():
    return {
        key: category_manager.decorate_books(items)
        for key, items in repository().rankings(10).items()
    }


def _deconstruction_viewer(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    raw = (
        authorization[7:].strip()
        if authorization.startswith("Bearer ")
        else request.cookies.get(settings.session_cookie, "")
    )
    session = account_store().session(raw) if raw else None
    return session.user_id if session else None


def _enrich_deconstruction_engagement(
    items: list[dict[str, Any]], request: Request
) -> list[dict[str, Any]]:
    engagement = account_store().deconstruction_engagement(
        [str(item.get("slug") or "") for item in items],
        viewer_user_id=_deconstruction_viewer(request),
    )
    return [item | engagement.get(str(item.get("slug") or ""), {}) for item in items]


@app.get("/api/v1/deconstructions")
def deconstructions(request: Request):
    return {
        "items": _enrich_deconstruction_engagement(
            repository().list_deconstructions(), request
        )
    }


@app.get("/api/v1/deconstructions/{slug}")
def deconstruction(slug: str, request: Request):
    return _enrich_deconstruction_engagement(
        [repository().get_deconstruction(slug)], request
    )[0]


@app.get("/api/v1/deconstructions/{slug}/file/{subpath:path}")
def deconstruction_file(slug: str, subpath: str):
    return repository().get_deconstruction_file(slug, subpath)


TTS_EMOTIONS = {
    "neutral": {
        "label": "平稳",
        "desc": "自然克制的标准叙述",
        "rate": 0,
        "pitch": 0,
        "volume": 0,
    },
    "gentle": {
        "label": "温柔",
        "desc": "柔和亲近、低压舒缓",
        "rate": -8,
        "pitch": 1,
        "volume": -4,
    },
    "joyful": {
        "label": "喜悦",
        "desc": "明快轻盈、笑意充沛",
        "rate": 9,
        "pitch": 4,
        "volume": 3,
    },
    "excited": {
        "label": "激昂",
        "desc": "高能振奋、节奏强烈",
        "rate": 18,
        "pitch": 6,
        "volume": 8,
    },
    "angry": {
        "label": "愤怒",
        "desc": "强硬紧迫、压迫感突出",
        "rate": 12,
        "pitch": -3,
        "volume": 10,
    },
    "sad": {
        "label": "悲伤",
        "desc": "低沉迟缓、情绪下坠",
        "rate": -18,
        "pitch": -5,
        "volume": -8,
    },
    "fearful": {
        "label": "惊惧",
        "desc": "呼吸急促、音线绷紧",
        "rate": 13,
        "pitch": 7,
        "volume": -2,
    },
    "tense": {
        "label": "紧张",
        "desc": "节奏收紧、悬念增强",
        "rate": 8,
        "pitch": 3,
        "volume": 2,
    },
    "mysterious": {
        "label": "神秘",
        "desc": "低声慢述、幽微莫测",
        "rate": -14,
        "pitch": -7,
        "volume": -10,
    },
    "solemn": {
        "label": "庄重",
        "desc": "沉稳肃穆、字句清晰",
        "rate": -12,
        "pitch": -5,
        "volume": 1,
    },
    "affectionate": {
        "label": "深情",
        "desc": "细腻温暖、情感绵长",
        "rate": -10,
        "pitch": 2,
        "volume": -3,
    },
    "humorous": {
        "label": "诙谐",
        "desc": "灵动俏皮、节拍跳跃",
        "rate": 10,
        "pitch": 6,
        "volume": 1,
    },
    "weary": {
        "label": "疲惫",
        "desc": "缓慢虚弱、气息低落",
        "rate": -22,
        "pitch": -6,
        "volume": -12,
    },
    "surprised": {
        "label": "惊讶",
        "desc": "语调上扬、反应鲜明",
        "rate": 10,
        "pitch": 8,
        "volume": 3,
    },
    "comforting": {
        "label": "安抚",
        "desc": "柔和放慢、给予安全感",
        "rate": -12,
        "pitch": 1,
        "volume": -5,
    },
    "confident": {
        "label": "笃定",
        "desc": "节奏稳健、语气有力",
        "rate": 2,
        "pitch": -2,
        "volume": 5,
    },
    "shy": {
        "label": "羞怯",
        "desc": "轻柔迟疑、情绪内收",
        "rate": -9,
        "pitch": 4,
        "volume": -7,
    },
    "disgusted": {
        "label": "厌恶",
        "desc": "冷硬克制、排斥感突出",
        "rate": -3,
        "pitch": -5,
        "volume": 1,
    },
    "whispering": {
        "label": "低语",
        "desc": "压低声线、贴近耳语",
        "rate": -16,
        "pitch": -4,
        "volume": -18,
    },
}

EDGE_MULTILINGUAL_CHINESE_VOICES = frozenset({"ava", "emma", "andrew", "brian"})
EDGE_MULTILINGUAL_VOICE_BASE_PROSODY = {
    "ava": {"rate": -1, "pitch": -1, "volume": -1},
    "emma": {"rate": -2, "pitch": -2, "volume": -1},
    "andrew": {"rate": -4, "pitch": -3, "volume": 1},
    "brian": {"rate": -5, "pitch": -4, "volume": 2},
}
EDGE_MULTILINGUAL_CHINESE_EMOTIONS = {
    "neutral": {"rate": 0, "pitch": 0, "volume": 0},
    "gentle": {"rate": -7, "pitch": -1, "volume": -4},
    "joyful": {"rate": 7, "pitch": 2, "volume": 1},
    "excited": {"rate": 12, "pitch": 3, "volume": 5},
    "angry": {"rate": 8, "pitch": -4, "volume": 7},
    "sad": {"rate": -13, "pitch": -4, "volume": -7},
    "fearful": {"rate": 9, "pitch": 4, "volume": -2},
    "tense": {"rate": 6, "pitch": 1, "volume": 2},
    "mysterious": {"rate": -11, "pitch": -5, "volume": -8},
    "solemn": {"rate": -10, "pitch": -5, "volume": 1},
    "affectionate": {"rate": -8, "pitch": 0, "volume": -4},
    "humorous": {"rate": 7, "pitch": 3, "volume": 0},
    "weary": {"rate": -16, "pitch": -5, "volume": -11},
    "surprised": {"rate": 8, "pitch": 4, "volume": 2},
    "comforting": {"rate": -9, "pitch": -1, "volume": -5},
    "confident": {"rate": 1, "pitch": -3, "volume": 4},
    "shy": {"rate": -8, "pitch": 2, "volume": -7},
    "disgusted": {"rate": -3, "pitch": -5, "volume": 1},
    "whispering": {"rate": -13, "pitch": -5, "volume": -16},
}
EDGE_MULTILINGUAL_VOICE_EMOTION_PROSODY = {
    "ava": {
        "joyful": {"rate": 1, "pitch": 1, "volume": 0},
        "humorous": {"rate": 1, "pitch": 1, "volume": 0},
        "surprised": {"rate": 0, "pitch": 1, "volume": 0},
    },
    "emma": {
        "gentle": {"rate": -1, "pitch": 0, "volume": -1},
        "affectionate": {"rate": -1, "pitch": 0, "volume": -1},
        "comforting": {"rate": -1, "pitch": 0, "volume": -1},
    },
    "andrew": {
        "angry": {"rate": 0, "pitch": -1, "volume": 1},
        "confident": {"rate": 0, "pitch": 0, "volume": 1},
        "solemn": {"rate": -1, "pitch": -1, "volume": 1},
    },
    "brian": {
        "sad": {"rate": -1, "pitch": 0, "volume": 0},
        "mysterious": {"rate": -1, "pitch": -1, "volume": -1},
        "whispering": {"rate": -1, "pitch": -1, "volume": -1},
    },
}
PROSODY_FIELDS = ("rate", "pitch", "volume")


def _edge_multilingual_chinese_profile(voice: str, emotion: str) -> dict[str, int]:
    base = EDGE_MULTILINGUAL_VOICE_BASE_PROSODY.get(
        voice, {"rate": 0, "pitch": 0, "volume": 0}
    )
    tuned = EDGE_MULTILINGUAL_CHINESE_EMOTIONS.get(emotion, TTS_EMOTIONS[emotion])
    override = EDGE_MULTILINGUAL_VOICE_EMOTION_PROSODY.get(voice, {}).get(emotion, {})
    return {
        field: int(base.get(field, 0))
        + int(tuned.get(field, 0))
        + int(override.get(field, 0))
        for field in PROSODY_FIELDS
    }


LOCAL_TTS_DEFAULT_ROOT = Path(
    "/srv/oohstory/library/oohstory/tts-models/"
    "vits-icefall-zh-aishell3"
)
_local_tts_engine: Any | None = None
_local_tts_engine_lock = threading.Lock()
_local_tts_synth_lock = threading.Lock()


def _get_local_tts_engine() -> Any:
    """Lazily load the bounded, offline Mandarin multi-speaker engine."""
    global _local_tts_engine
    if _local_tts_engine is not None:
        return _local_tts_engine
    with _local_tts_engine_lock:
        if _local_tts_engine is not None:
            return _local_tts_engine
        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise RuntimeError("本地普通话声线运行库未安装") from exc
        root = Path(
            os.getenv("OOHSTORY_LOCAL_TTS_MODEL_ROOT", str(LOCAL_TTS_DEFAULT_ROOT))
        ).resolve()
        required = {
            "model": root / "model.onnx",
            "lexicon": root / "lexicon.txt",
            "tokens": root / "tokens.txt",
            "phone": root / "phone.fst",
            "date": root / "date.fst",
            "number": root / "number.fst",
        }
        missing = [str(path) for path in required.values() if not path.is_file()]
        if missing:
            raise RuntimeError("本地普通话声线模型不完整")
        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(required["model"]),
            lexicon=str(required["lexicon"]),
            tokens=str(required["tokens"]),
        )
        model = sherpa_onnx.OfflineTtsModelConfig(
            vits=vits,
            num_threads=min(4, max(1, os.cpu_count() or 1)),
            debug=False,
            provider="cpu",
        )
        config = sherpa_onnx.OfflineTtsConfig(
            model=model,
            rule_fsts=",".join(
                str(required[key]) for key in ("phone", "date", "number")
            ),
            max_num_sentences=1,
            silence_scale=0.2,
        )
        if not config.validate():
            raise RuntimeError("本地普通话声线模型配置无效")
        engine = sherpa_onnx.OfflineTts(config)
        if int(engine.num_speakers) <= 33:
            raise RuntimeError("本地普通话声线缺少所需说话人")
        _local_tts_engine = engine
        return _local_tts_engine


def _local_tts_wav_bytes(samples: Any, sample_rate: int) -> bytes:
    import numpy as np

    pcm = (np.clip(np.asarray(samples), -1.0, 1.0) * 32767).astype("<i2")
    output = BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return output.getvalue()


def _synthesize_local_tts_bytes(
    text: str,
    *,
    speaker_id: int,
    speed: float,
    pitch_hz: int,
    volume_percent: int,
) -> bytes:
    """Generate one local Mandarin segment and encode it as browser-safe MP3."""
    with _local_tts_synth_lock:
        audio = _get_local_tts_engine().generate(
            text,
            sid=int(speaker_id),
            speed=max(0.5, min(2.0, float(speed))),
        )
    wav_bytes = _local_tts_wav_bytes(audio.samples, int(audio.sample_rate))
    pitch_factor = 2 ** (max(-30, min(30, int(pitch_hz))) / 120.0)
    volume_factor = max(0.2, min(2.0, 1.0 + int(volume_percent) / 100.0))
    command = [
        "/usr/bin/ffmpeg",
        "-v",
        "error",
        "-f",
        "wav",
        "-i",
        "pipe:0",
        "-af",
        f"rubberband=pitch={pitch_factor:.6f},volume={volume_factor:.4f}",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "48k",
        "-f",
        "mp3",
        "pipe:1",
    ]
    process = subprocess.run(
        command,
        input=wav_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if process.returncode != 0 or not process.stdout:
        raise RuntimeError("本地普通话声线编码失败")
    return bytes(process.stdout)


# Existing immutable v7.3 manifests may contain punctuation-only segments such
# as an em-dash. Edge TTS returns NoAudioReceived for these, so preserve the
# old timing with a tiny valid MP3 pause while v7.4 omits them from new plans.
TTS_PUNCTUATION_PAUSE_MP3 = b64decode(
    "SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjYwLjE2LjEwMAAAAAAAAAAAAAAA//NExAAAAANI"
    "AAAAAExBTUUzLjEwMFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVTEFNRTMu//NExFMAAANIAAAAADEwMFVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVV//NExKYAAANIAAAAAFVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVV//NExKwAAANIAAAAAFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
)


@app.get("/api/v1/tts/voices")
def tts_voices():
    emotion_keys = list(TTS_EMOTIONS)
    return {
        "policy_version": AUDIOBOOK_POLICY_VERSION,
        "mode_languages": MODE_LANGUAGE,
        "mode_defaults": {
            "normal": "nuanxi",
            "smart": "nuanxi",
            "cantonese": "wanqing",
            "hokkien": "qianyu",
        },
        "voices": [
            {
                "key": k,
                "label": v["label"],
                "gender": v["gender"],
                "desc": v["desc"],
                "language": voice_language(k),
                "roles": ["narrator", "character"],
                "emotion_tuning": (
                    "edge-multilingual-chinese"
                    if k in EDGE_MULTILINGUAL_CHINESE_VOICES
                    else "standard"
                ),
                "emotion_count": len(emotion_keys),
                "emotions": emotion_keys,
            }
            for k, v in TTS_VOICES.items()
            if not bool(v.get("hidden"))
        ],
        "emotions": [
            {"key": key, "label": item["label"], "desc": item["desc"]}
            for key, item in TTS_EMOTIONS.items()
        ],
    }


@app.get("/api/v1/audiobook/capacity")
def audiobook_prefetch_capacity():
    """Expose only the bounded signal clients need for optional prefetch."""
    return JSONResponse(
        tts_concurrency_limiter().capacity(),
        headers={"Cache-Control": "private, no-store"},
    )


def _audiobook_owner(request: Request, client_id: str = "") -> str:
    raw = request.headers.get("authorization", "")
    if raw.startswith("Bearer "):
        raw = raw[7:].strip()
    else:
        raw = request.cookies.get(settings.session_cookie, "")
    session = account_store().session(raw) if raw else None
    if session is not None:
        # A user ID is stable across browsers and login sessions, enabling
        # true cross-device audiobook progress without sharing playback state.
        identity = f"user:{session.user_id}"
    else:
        identity = (
            client_id
            or request.headers.get("x-audiobook-client", "")
            or request.cookies.get(AUDIOBOOK_CLIENT_COOKIE, "")
        )
        if not AUDIOBOOK_CLIENT_ID_RE.fullmatch(identity):
            if raw:
                raise HTTPException(401, "登录状态已失效")
            raise HTTPException(401, "缺少有效的听书客户端标识")
    if not identity:
        raise HTTPException(401, "缺少听书客户端标识")
    return sha256(identity.encode("utf-8")).hexdigest()


def _audiobook_device(request: Request, client_id: str = "") -> str:
    identity = (
        client_id
        or request.headers.get("x-audiobook-client", "")
        or request.cookies.get(AUDIOBOOK_CLIENT_COOKIE, "")
    )
    if not AUDIOBOOK_CLIENT_ID_RE.fullmatch(identity):
        raise HTTPException(401, "缺少有效的听书设备标识")
    return sha256(f"device:{identity}".encode("utf-8")).hexdigest()


def _audiobook_session_owner(request: Request, session_id: str) -> str:
    owner = _audiobook_owner(request)
    try:
        device = _audiobook_device(request)
    except HTTPException:
        device = ""
    resolver = getattr(audiobook_service(), "session_owner", None)
    if not callable(resolver):
        return owner
    resolved = resolver(session_id, owner_key=owner, device_key=device)
    if not resolved:
        raise HTTPException(404, "听书会话不存在或已失效")
    return resolved


def _cancel_audiobook_logout(user_id: int, request: Request) -> int:
    """Cancel this browser's playback without disturbing another device."""
    owner = sha256(f"user:{int(user_id)}".encode("utf-8")).hexdigest()
    client_id = request.headers.get("x-audiobook-client", "")
    if re.fullmatch(r"[A-Za-z0-9_-]{16,96}", client_id):
        device = sha256(f"device:{client_id}".encode("utf-8")).hexdigest()
        return audiobook_service().cancel_device(owner, device)
    # Old clients do not provide a device ID.  Account-wide cancellation is
    # the secure fallback on logout; current clients remain device-scoped.
    return audiobook_service().cancel_owner(owner)


def _request_ip(request: Request) -> str:
    return str(request.client.host if request.client else "unknown")[:100]


def _audiobook_quota(
    owner: str,
    ip: str,
    bucket: str,
    *,
    owner_limit: int,
    ip_limit: int,
    window: int,
    cost: int = 1,
) -> None:
    try:
        store = account_store()
        store.enforce_rate_limit(
            f"audiobook:{bucket}:owner:{owner}",
            limit=owner_limit,
            window=window,
            cost=cost,
        )
        store.enforce_rate_limit(
            f"audiobook:{bucket}:ip:{ip}",
            limit=ip_limit,
            window=window,
            cost=cost,
        )
    except AccountError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


class TtsConcurrencyLimiter:
    lease_minutes = 5
    renew_interval_seconds = 60.0

    def __init__(
        self, owner_limit: int = 3, ip_limit: int = 6, site_limit: int = 8
    ) -> None:
        self.owner_limit = owner_limit
        self.ip_limit = ip_limit
        self.site_limit = site_limit
        self._owner_counts: dict[str, int] = {}
        self._ip_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def _distributed_acquire(self, owner: str, ip: str) -> str | None:
        mysql = getattr(
            getattr(audiobook_service(), "repository", None), "_mysql", None
        )
        if mysql is None:
            return None
        token = secrets.token_hex(16)
        ip_hash = sha256(ip.encode("utf-8")).hexdigest()
        with mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT guard_id FROM audiobook_tts_concurrency_guard "
                    "WHERE guard_id=1 FOR UPDATE"
                )
                if not cursor.fetchone():
                    raise RuntimeError("audiobook TTS concurrency guard missing")
                cursor.execute(
                    "DELETE FROM audiobook_tts_leases "
                    "WHERE expires_at<=UTC_TIMESTAMP(6)"
                )
                cursor.execute(
                    "SELECT COUNT(*) AS amount FROM audiobook_tts_leases "
                    "WHERE expires_at>UTC_TIMESTAMP(6)"
                )
                if int((cursor.fetchone() or {}).get("amount") or 0) >= self.site_limit:
                    raise HTTPException(429, "听书生成服务繁忙，请稍后再试")
                cursor.execute(
                    "SELECT COUNT(*) AS amount FROM audiobook_tts_leases "
                    "WHERE owner_hash=UNHEX(%s) AND expires_at>UTC_TIMESTAMP(6)",
                    (owner,),
                )
                if (
                    int((cursor.fetchone() or {}).get("amount") or 0)
                    >= self.owner_limit
                ):
                    raise HTTPException(429, "听书生成并发过高，请稍后再试")
                cursor.execute(
                    "SELECT COUNT(*) AS amount FROM audiobook_tts_leases "
                    "WHERE ip_hash=UNHEX(%s) AND expires_at>UTC_TIMESTAMP(6)",
                    (ip_hash,),
                )
                if int((cursor.fetchone() or {}).get("amount") or 0) >= self.ip_limit:
                    raise HTTPException(429, "当前网络的听书生成并发过高")
                cursor.execute(
                    "INSERT INTO audiobook_tts_leases "
                    "(lease_token,owner_hash,ip_hash,expires_at) "
                    "VALUES (%s,UNHEX(%s),UNHEX(%s),"
                    "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s MINUTE))",
                    (token, owner, ip_hash, self.lease_minutes),
                )
        return token

    def _distributed_renew(self, token: str) -> bool:
        mysql = getattr(
            getattr(audiobook_service(), "repository", None), "_mysql", None
        )
        if mysql is None:
            return False
        with mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE audiobook_tts_leases SET "
                    "expires_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s MINUTE) "
                    "WHERE lease_token=%s AND expires_at>UTC_TIMESTAMP(6)",
                    (self.lease_minutes, token),
                )
                return cursor.rowcount == 1

    async def _renew_lease(
        self,
        token: str,
        stopped: asyncio.Event,
        lease_lost: asyncio.Event,
        owner_task: asyncio.Task[Any],
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stopped.wait(), timeout=self.renew_interval_seconds
                )
                return
            except TimeoutError:
                try:
                    renewed = await asyncio.to_thread(self._distributed_renew, token)
                except Exception:
                    renewed = False
                if not renewed:
                    # Capacity protection is meaningful only while the active
                    # synthesis owns a live lease.  Stop the request task as
                    # soon as renewal is lost so a replacement lease cannot
                    # push actual work beyond the global limit.
                    lease_lost.set()
                    owner_task.cancel()
                    return

    def _distributed_release(self, token: str) -> None:
        mysql = getattr(
            getattr(audiobook_service(), "repository", None), "_mysql", None
        )
        if mysql is None:
            return
        with mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM audiobook_tts_leases WHERE lease_token=%s",
                    (token,),
                )

    def capacity(self) -> dict[str, Any]:
        mysql = getattr(
            getattr(audiobook_service(), "repository", None), "_mysql", None
        )
        active = 0
        if mysql is not None:
            with mysql.pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT COUNT(*) AS amount FROM audiobook_tts_leases "
                        "WHERE expires_at>UTC_TIMESTAMP(6)"
                    )
                    active = int((cursor.fetchone() or {}).get("amount") or 0)
        cpu_count = max(1, int(os.cpu_count() or 1))
        try:
            normalized_load = max(0.0, float(os.getloadavg()[0]) / cpu_count)
        except (AttributeError, OSError):
            normalized_load = 0.0
        allow = active < max(1, self.site_limit // 2) and normalized_load < 0.8
        return {
            "allow_prefetch": allow,
            "active_tts": active,
            "site_limit": self.site_limit,
            "load_ratio": round(normalized_load, 3),
        }

    @asynccontextmanager
    async def slot(self, owner: str, ip: str):
        distributed = await asyncio.to_thread(self._distributed_acquire, owner, ip)
        if distributed is not None:
            stopped = asyncio.Event()
            lease_lost = asyncio.Event()
            owner_task = asyncio.current_task()
            if owner_task is None:
                raise RuntimeError("TTS request task is unavailable")
            heartbeat = asyncio.create_task(
                self._renew_lease(distributed, stopped, lease_lost, owner_task)
            )
            try:
                yield
            except asyncio.CancelledError:
                if lease_lost.is_set():
                    raise RuntimeError(
                        "audiobook TTS concurrency lease was lost"
                    ) from None
                raise
            finally:
                stopped.set()
                heartbeat.cancel()
                try:
                    with suppress(asyncio.CancelledError):
                        await heartbeat
                finally:
                    # StreamingResponse cancellation can cancel this task while
                    # its generator is unwinding.  Shield the database release
                    # so an abandoned client never leaves a five-minute ghost
                    # lease occupying global TTS capacity.
                    release = asyncio.create_task(
                        asyncio.to_thread(self._distributed_release, distributed)
                    )
                    try:
                        await asyncio.shield(release)
                    except asyncio.CancelledError:
                        with suppress(asyncio.CancelledError):
                            await release
            return
        with self._lock:
            if self._owner_counts.get(owner, 0) >= self.owner_limit:
                raise HTTPException(429, "听书生成并发过高，请稍后再试")
            if self._ip_counts.get(ip, 0) >= self.ip_limit:
                raise HTTPException(429, "当前网络的听书生成并发过高")
            self._owner_counts[owner] = self._owner_counts.get(owner, 0) + 1
            self._ip_counts[ip] = self._ip_counts.get(ip, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._owner_counts[owner] -= 1
                self._ip_counts[ip] -= 1
                if self._owner_counts[owner] == 0:
                    self._owner_counts.pop(owner)
                if self._ip_counts[ip] == 0:
                    self._ip_counts.pop(ip)


@lru_cache(maxsize=1)
def tts_concurrency_limiter() -> TtsConcurrencyLimiter:
    return TtsConcurrencyLimiter()


async def _synthesize_tts_bytes(
    text: str,
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
    emotion: str,
) -> tuple[bytes, str]:
    voice_entry = TTS_VOICES.get(voice)
    if not voice_entry:
        raise HTTPException(400, "不支持的语音")
    clean = text.strip()
    if not clean:
        raise HTTPException(400, "文本不能为空")
    if not re.fullmatch(r"[+-]\d{1,3}%", rate):
        raise HTTPException(400, "语速格式无效")
    if pitch and not re.fullmatch(r"[+-]\d{1,3}Hz", pitch):
        raise HTTPException(400, "音调格式无效")
    if volume and not re.fullmatch(r"[+-]\d{1,3}%", volume):
        raise HTTPException(400, "音量格式无效")
    if emotion not in TTS_EMOTIONS:
        raise HTTPException(400, "不支持的情感模式")
    profile = (
        _edge_multilingual_chinese_profile(voice, emotion)
        if voice in EDGE_MULTILINGUAL_CHINESE_VOICES
        else TTS_EMOTIONS[emotion]
    )

    def adjusted(value: str, suffix: str, offset: int, lower: int, upper: int) -> str:
        base = int(value.removesuffix(suffix))
        return f"{max(lower, min(upper, base + offset)):+d}{suffix}"

    adjusted_rate = adjusted(rate, "%", profile["rate"], -90, 200)
    adjusted_pitch = adjusted(pitch, "Hz", profile["pitch"], -50, 50)
    adjusted_volume = adjusted(volume, "%", profile["volume"], -50, 50)
    if voice_entry.get("provider") == LOCAL_TTS_PROVIDER:
        try:
            audio = await asyncio.to_thread(
                _synthesize_local_tts_bytes,
                clean,
                speaker_id=int(voice_entry["speaker_id"]),
                speed=max(0.5, (100 + int(adjusted_rate.removesuffix("%"))) / 100),
                pitch_hz=int(adjusted_pitch.removesuffix("Hz")),
                volume_percent=int(adjusted_volume.removesuffix("%")),
            )
        except Exception as exc:
            raise HTTPException(500, "本地普通话语音合成失败") from exc
        return audio, emotion

    last_error: Exception | None = None
    for attempt in range(4):
        try:
            communicate = edge_tts.Communicate(
                clean,
                voice_entry["id"],
                rate=adjusted_rate,
                pitch=adjusted_pitch,
                volume=adjusted_volume,
            )
            chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            audio = b"".join(chunks)
            if audio:
                return audio, emotion
            last_error = RuntimeError("empty TTS audio")
        except HTTPException:
            raise
        except RECOVERABLE_INTEGRATION_ERRORS as exc:
            last_error = exc
        if attempt < 3:
            await asyncio.sleep(0.25 * (2**attempt))
    raise HTTPException(500, "语音合成失败") from last_error


async def _audiobook_segment_bytes(segment: dict[str, Any]) -> bytes:
    if not has_spoken_content(str(segment.get("text") or "")):
        return TTS_PUNCTUATION_PAUSE_MP3
    rate_number = max(0.5, min(float(segment.get("rate") or 1), 3.0))
    rate_percent = round((rate_number - 1) * 100)

    async def build() -> bytes:
        result, _ = await _synthesize_tts_bytes(
            str(segment["text"]),
            str(segment["voice"]),
            f"{rate_percent:+d}%",
            "+0Hz",
            "+0%",
            str(segment["emotion"]),
        )
        return result

    return await build()


def _record_audiobook_segment_duration(
    segment: dict[str, Any], audio: bytes, duration_ms: int | None = None
) -> None:
    key = str(segment.get("sha256") or "")
    if not AUDIOBOOK_MANIFEST_HASH_PATTERN.fullmatch(key):
        return
    measured_ms = int(
        duration_ms if duration_ms is not None else mp3_duration_ms(audio)
    )
    if measured_ms <= 0:
        return
    repository_owner = getattr(audiobook_service(), "repository", None)
    mysql = getattr(repository_owner, "_mysql", None)
    if mysql is None:
        return
    with mysql.pool.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO audiobook_audio_jobs "
                "(segment_hash,status,ref_count,audio_hash,byte_count,duration_ms,expires_at) "
                "VALUES (UNHEX(%s),'complete',0,%s,%s,%s,"
                "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 6 HOUR)) "
                "ON DUPLICATE KEY UPDATE status='complete',"
                "audio_hash=VALUES(audio_hash),byte_count=VALUES(byte_count),"
                "duration_ms=VALUES(duration_ms),expires_at=VALUES(expires_at)",
                (key, sha256(audio).digest(), len(audio), measured_ms),
            )


async def _audiobook_segment_while_active(
    segment: dict[str, Any],
    request: Request,
    session_id: str,
    manifest_hash: str,
    owner: str,
) -> bytes | None:
    """Cancel an unshared in-flight synthesis when its playback session dies."""
    task = asyncio.create_task(_audiobook_segment_bytes(segment))
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=1.0)
            if done:
                return await task
            if await request.is_disconnected():
                task.cancel()
                return None
            try:
                await asyncio.to_thread(
                    audiobook_service().manifest,
                    session_id,
                    manifest_hash,
                    owner,
                )
            except KeyError:
                task.cancel()
                return None
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _audiobook_stream_segment_while_active(
    segment: dict[str, Any],
    request: Request,
    session_id: str,
    manifest_hash: str,
    owner: str,
    ip: str,
) -> bytes | None:
    """Return one byte-stable session segment across media-stack reconnects."""
    target, lock = _audiobook_session_segment_paths(session_id, manifest_hash, segment)
    cached = await asyncio.to_thread(_read_audiobook_session_segment, target)
    if cached is not None:
        return cached
    while True:
        cached = await asyncio.to_thread(_read_audiobook_session_segment, target)
        if cached is not None:
            return cached
        if await asyncio.to_thread(_claim_audiobook_session_segment, lock):
            cached = await asyncio.to_thread(_read_audiobook_session_segment, target)
            if cached is not None:
                try:
                    lock.unlink()
                except OSError:
                    pass
                return cached
            break
        if await request.is_disconnected():
            return None
        try:
            audiobook_service().manifest(session_id, manifest_hash, owner)
        except KeyError:
            return None
        await asyncio.sleep(0.1)

    attempt = 0
    try:
        while True:
            if await request.is_disconnected():
                return None
            try:
                async with tts_concurrency_limiter().slot(owner, ip):
                    audio = await _audiobook_segment_while_active(
                        segment, request, session_id, manifest_hash, owner
                    )
                if audio is not None:
                    await asyncio.to_thread(
                        _publish_audiobook_session_segment, target, audio
                    )
                return audio
            except HTTPException as exc:
                if exc.status_code != 429:
                    raise
            except RuntimeError as exc:
                if "concurrency lease was lost" not in str(exc):
                    raise
            attempt += 1
            await asyncio.sleep(min(2.0, 0.2 * attempt))
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


async def _audiobook_hls_segment_while_active(
    segment: dict[str, Any],
    request: Request,
    session_id: str,
    manifest_hash: str,
    owner: str,
    ip: str,
) -> tuple[bytes, int, bytes] | None:
    """Return one fixed-length MPEG-TS segment for native iOS playback."""

    target, lock = _audiobook_hls_segment_paths(
        session_id, manifest_hash, segment
    )
    cached = await asyncio.to_thread(_read_audiobook_session_segment, target)
    if cached is not None:
        mp3_target, _unused = _audiobook_session_segment_paths(
            session_id, manifest_hash, segment
        )
        mp3_audio = await asyncio.to_thread(
            _read_audiobook_session_segment, mp3_target
        )
        duration_ms = (
            await asyncio.to_thread(mp3_duration_ms, mp3_audio)
            if mp3_audio
            else 0
        )
        return cached, duration_ms, mp3_audio or b""
    while True:
        cached = await asyncio.to_thread(_read_audiobook_session_segment, target)
        if cached is not None:
            mp3_target, _unused = _audiobook_session_segment_paths(
                session_id, manifest_hash, segment
            )
            mp3_audio = await asyncio.to_thread(
                _read_audiobook_session_segment, mp3_target
            )
            duration_ms = (
                await asyncio.to_thread(mp3_duration_ms, mp3_audio)
                if mp3_audio
                else 0
            )
            return cached, duration_ms, mp3_audio or b""
        if await asyncio.to_thread(_claim_audiobook_session_segment, lock):
            break
        if await request.is_disconnected():
            return None
        try:
            audiobook_service().manifest(session_id, manifest_hash, owner)
        except KeyError:
            return None
        await asyncio.sleep(0.1)
    try:
        mp3_audio = await _audiobook_stream_segment_while_active(
            segment, request, session_id, manifest_hash, owner, ip
        )
        if mp3_audio is None:
            return None
        duration_ms = await asyncio.to_thread(mp3_duration_ms, mp3_audio)
        transport_stream = await asyncio.to_thread(
            _transcode_audiobook_hls_segment, mp3_audio
        )
        await asyncio.to_thread(
            _publish_audiobook_session_segment, target, transport_stream
        )
        return transport_stream, duration_ms, mp3_audio
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _trim_mp3_audio(audio: bytes, offset_ms: int) -> bytes:
    """Trim only a resume segment; frame-copy keeps CPU and latency bounded."""
    if offset_ms <= 0 or not audio:
        return audio
    completed = subprocess.run(
        [
            "/usr/bin/ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{offset_ms / 1000:.3f}",
            "-i",
            "pipe:0",
            "-map_metadata",
            "-1",
            "-c:a",
            "copy",
            "-f",
            "mp3",
            "pipe:1",
        ],
        input=audio,
        capture_output=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0 or not completed.stdout:
        return audio
    return completed.stdout


@app.post("/api/v1/audiobook/sessions", status_code=201)
def create_audiobook_session(
    payload: AudiobookSessionRequest, request: Request, response: Response
):
    try:
        validate_voice_selection(
            mode=payload.mode,
            narrator=payload.narrator,
            voice=payload.voice,
        )
    except AudiobookContractError as exc:
        raise HTTPException(400, "所选音源与听书模式不匹配") from exc
    settings_payload = {
        "mode": payload.mode,
        "narrator": payload.narrator,
        "voice": payload.voice,
        "emotion": payload.emotion,
        "rate": payload.rate,
    }
    owner = _audiobook_owner(request, payload.client_id)
    device = _audiobook_device(request, payload.client_id)
    _audiobook_quota(
        owner,
        _request_ip(request),
        "session",
        owner_limit=20,
        ip_limit=40,
        window=3600,
    )
    try:
        result = audiobook_service().create(
            owner_key=owner,
            device_key=device,
            book_id=payload.book_id,
            chapter_id=payload.chapter_id,
            settings=settings_payload,
            resume_existing=payload.resume,
            start_paragraph_index=payload.start_paragraph_index,
        )
    except AudiobookContractError as exc:
        raise HTTPException(409, "听书清单已失效，请刷新后重试") from exc
    except NotFoundError as exc:
        raise HTTPException(404, "章节不存在") from exc
    except KeyError as exc:
        raise HTTPException(404, "作品不存在") from exc
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        AUDIOBOOK_CLIENT_COOKIE,
        payload.client_id,
        max_age=43_200,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        samesite="lax",
        path="/api/v1/audiobook/",
    )
    return result


@app.delete("/api/v1/audiobook/sessions/{session_id}", status_code=204)
async def cancel_audiobook_session(session_id: str, request: Request):
    owner = _audiobook_session_owner(request, session_id)
    if not audiobook_service().cancel(session_id, owner):
        raise HTTPException(404, "听书会话不存在")
    await asyncio.to_thread(_purge_cancelled_audiobook_session, session_id, owner)
    await asyncio.to_thread(_delete_audiobook_session_receipts, session_id)
    response = PlainTextResponse("", status_code=204)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.delete_cookie(
        AUDIOBOOK_CLIENT_COOKIE,
        path="/api/v1/audiobook/",
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/v1/audiobook/sessions/{session_id}/hls/queues", status_code=201)
def create_audiobook_hls_queue(
    session_id: str,
    payload: AudiobookHlsQueueRequest,
    request: Request,
):
    """Create an append-only, owner-bound playlist for native iOS media."""

    owner = _audiobook_session_owner(request, session_id)
    ip = _request_ip(request)
    _audiobook_quota(
        owner,
        ip,
        "hls-queue",
        owner_limit=20,
        ip_limit=40,
        window=3600,
    )
    try:
        manifest = audiobook_service().manifest(
            session_id, payload.manifest_hash, owner
        )
    except AudiobookContractError as exc:
        raise HTTPException(409, "听书清单已失效，请刷新后重试") from exc
    except KeyError as exc:
        raise HTTPException(404, "听书会话或章节不存在") from exc
    segments = list(manifest.get("segments") or [])
    if payload.start_index >= len(segments):
        raise HTTPException(404, "听书起始片段不存在")
    queue_id = secrets.token_hex(16)
    target = _audiobook_hls_queue_path(session_id, queue_id)
    _write_json_atomically(
        target,
        {
            "version": 1,
            "session_id": session_id,
            "queue_id": queue_id,
            "manifest_hashes": [payload.manifest_hash],
            "start_index": payload.start_index,
            "offset_ms": min(payload.offset_ms, 3_600_000),
            "complete": not bool(manifest.get("next_chapter_id")),
            "created_at": int(time.time()),
            "expanded_at": 0,
        },
    )
    try:
        return _audiobook_hls_queue_response(
            session_id, queue_id, owner, initial=True
        )
    except (AudiobookContractError, KeyError) as exc:
        try:
            target.unlink()
        except OSError:
            pass
        raise HTTPException(409, "后台听书队列创建失败，请重试") from exc


@app.get("/api/v1/audiobook/sessions/{session_id}/hls/queues/{queue_id}/status")
def audiobook_hls_queue_status(
    session_id: str,
    queue_id: str,
    request: Request,
):
    owner = _audiobook_session_owner(request, session_id)
    try:
        return _audiobook_hls_queue_response(session_id, queue_id, owner)
    except (AudiobookContractError, KeyError, ValueError) as exc:
        raise HTTPException(404, "后台听书队列不存在或已失效") from exc


@app.get(
    "/api/v1/audiobook/sessions/{session_id}/hls/queues/{queue_id}.m3u8"
)
def audiobook_hls_queue_playlist(
    session_id: str,
    queue_id: str,
    request: Request,
):
    owner = _audiobook_session_owner(request, session_id)
    try:
        payload = _audiobook_hls_queue_response(session_id, queue_id, owner)
    except (AudiobookContractError, KeyError, ValueError) as exc:
        raise HTTPException(404, "后台听书队列不存在或已失效") from exc
    items = list(payload.get("items") or [])
    if not items:
        raise HTTPException(409, "后台听书队列暂无可播放内容")
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:60",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    offset_seconds = max(0, int(payload.get("offset_ms") or 0)) / 1000
    # An EVENT playlist without EXT-X-START is treated as a live stream by
    # AVFoundation.  In that case Safari is allowed to choose a point near the
    # live edge, which skips the requested first paragraph even though the
    # playlist itself was correctly trimmed to start_index.  Pin every newly
    # created queue to its media-time origin, including an explicit zero.
    lines.append(
        f"#EXT-X-START:TIME-OFFSET={offset_seconds:.3f},PRECISE=YES"
    )
    for position, item in enumerate(items):
        if position:
            # Independently transcoded transport streams restart their media
            # timestamps. The discontinuity marker lets AVFoundation advance
            # natively without asking page JavaScript to replace audio.src.
            lines.append("#EXT-X-DISCONTINUITY")
        duration = max(0.1, min(float(item["duration_seconds"]), 59.9))
        lines.append(f"#EXTINF:{duration:.3f},")
        lines.append(
            f"/api/v1/audiobook/sessions/{session_id}/hls/queues/{queue_id}/"
            f"segments/{item['manifest_hash']}/{item['index']}.ts"
        )
    if payload.get("complete"):
        lines.append("#EXT-X-ENDLIST")
    return Response(
        content="\n".join(lines) + "\n",
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Encoding": "identity",
            "X-Content-Type-Options": "nosniff",
            "X-Audiobook-HLS-Queue": queue_id,
        },
    )


@app.get(
    "/api/v1/audiobook/sessions/{session_id}/hls/queues/{queue_id}/"
    "segments/{manifest_hash}/{segment_index}.ts"
)
async def audiobook_hls_queue_segment(
    session_id: str,
    queue_id: str,
    manifest_hash: str,
    segment_index: int,
    request: Request,
):
    owner = _audiobook_session_owner(request, session_id)
    try:
        queue = _read_audiobook_hls_queue(
            _audiobook_hls_queue_path(session_id, queue_id)
        )
        if manifest_hash not in set(queue.get("manifest_hashes") or []):
            raise KeyError("manifest is outside HLS queue")
        manifest = audiobook_service().manifest(session_id, manifest_hash, owner)
        segment = audiobook_service().segment(
            session_id, manifest_hash, segment_index, owner
        )
    except (AudiobookContractError, KeyError, ValueError) as exc:
        raise HTTPException(404, "后台听书片段不存在或已失效") from exc
    _audiobook_quota(
        owner,
        _request_ip(request),
        "hls-segment-requests",
        owner_limit=240,
        ip_limit=480,
        window=60,
    )
    result = await _audiobook_hls_segment_while_active(
        segment,
        request,
        session_id,
        manifest_hash,
        owner,
        _request_ip(request),
    )
    if result is None:
        raise HTTPException(409, "听书会话已结束")
    transport_stream, duration_ms, source_audio = result
    if source_audio and duration_ms > 0:
        await asyncio.to_thread(
            _record_audiobook_segment_duration,
            segment,
            source_audio,
            duration_ms,
        )
    if segment_index == 0:
        try:
            await asyncio.to_thread(
                audiobook_service().activate_chapter,
                session_id,
                int(manifest.get("chapter_id") or 0),
                owner,
            )
        except KeyError:
            raise HTTPException(409, "后台听书章节无法激活") from None
    if segment_index >= max(0, len(manifest.get("segments") or []) - 2):
        try:
            await asyncio.to_thread(
                audiobook_service().prefetch_next,
                session_id,
                owner,
                from_chapter_id=int(manifest.get("chapter_id") or 0),
            )
        except (AudiobookContractError, KeyError):
            pass
    return _audiobook_byte_range_response(
        transport_stream,
        request,
        media_type="video/mp2t",
        headers={
            "Cache-Control": "private, max-age=21600, immutable",
            "Content-Encoding": "identity",
            "Content-Disposition": "inline",
            "X-Audiobook-HLS-Queue": queue_id,
            "X-Audiobook-Manifest": manifest_hash,
            "X-Audiobook-Segment": str(segment_index),
            "X-Audio-Duration-Ms": str(duration_ms),
        },
    )


@app.post("/api/v1/audiobook/sessions/{session_id}/next")
def prefetch_next_audiobook_chapter(
    session_id: str,
    request: Request,
    from_chapter_id: int | None = Query(default=None, ge=1),
):
    owner = _audiobook_session_owner(request, session_id)
    _audiobook_quota(
        owner,
        _request_ip(request),
        "prefetch",
        owner_limit=60,
        ip_limit=120,
        window=3600,
    )
    try:
        manifest = audiobook_service().prefetch_next(
            session_id, owner, from_chapter_id=from_chapter_id
        )
    except AudiobookContractError as exc:
        raise HTTPException(409, "听书预载清单校验失败，请刷新后重试") from exc
    except KeyError as exc:
        raise HTTPException(404, "听书会话不存在") from exc
    return {"next": manifest}


@app.post(
    "/api/v1/audiobook/sessions/{session_id}/chapters/{chapter_id}/activate",
    status_code=204,
)
def activate_audiobook_chapter(session_id: str, chapter_id: int, request: Request):
    owner = _audiobook_session_owner(request, session_id)
    try:
        audiobook_service().activate_chapter(session_id, chapter_id, owner)
    except KeyError as exc:
        raise HTTPException(404, "听书章节不存在或尚未预加载") from exc
    return PlainTextResponse("", status_code=204)


@app.get("/api/v1/audiobook/progress/{book_id}")
def get_audiobook_progress(book_id: str, request: Request):
    owner = _audiobook_owner(request)
    device = _audiobook_device(request)
    try:
        return {
            "progress": audiobook_service().progress(owner, book_id, device_key=device)
        }
    except KeyError as exc:
        raise HTTPException(404, "书籍不存在") from exc


@app.put("/api/v1/audiobook/sessions/{session_id}/progress", status_code=204)
def save_audiobook_progress(
    session_id: str, payload: AudiobookProgressRequest, request: Request
):
    owner = _audiobook_session_owner(request, session_id)
    device = _audiobook_device(request)
    if bool(payload.manifest_hash) != bool(payload.stream_id):
        raise HTTPException(400, "听书流进度标识不完整")
    try:
        audiobook_service().save_progress(
            session_id,
            owner,
            device_key=device,
            chapter_id=payload.chapter_id,
            paragraph_index=payload.paragraph_index,
            item_index=payload.item_index,
            audio_offset_ms=payload.audio_offset_ms,
        )
        if payload.manifest_hash and payload.stream_id:
            manifest = audiobook_service().manifest(
                session_id, payload.manifest_hash, owner
            )
            if int(manifest.get("chapter_id") or 0) != payload.chapter_id:
                raise KeyError("progress chapter does not match manifest")
            stream_id = _normalize_audiobook_stream_id(payload.stream_id)
            if not stream_id:
                raise KeyError("stream id is invalid")
            _write_audiobook_stream_cursor_safely(
                session_id,
                payload.manifest_hash,
                stream_id,
                payload.item_index,
                audio_offset_ms=payload.audio_offset_ms,
                next_start=payload.item_index,
                source="playback",
            )
    except KeyError as exc:
        raise HTTPException(404, "听书会话不存在") from exc
    return PlainTextResponse("", status_code=204)


@app.post(
    "/api/v1/audiobook/sessions/{session_id}/segments/{manifest_hash}/{segment_index}"
)
async def audiobook_segment_audio(
    session_id: str,
    manifest_hash: str,
    segment_index: int,
    request: Request,
):
    owner = _audiobook_session_owner(request, session_id)
    ip = _request_ip(request)
    try:
        segment = audiobook_service().segment(
            session_id, manifest_hash, segment_index, owner
        )
    except AudiobookContractError as exc:
        raise HTTPException(409, "听书预载清单校验失败，请刷新后重试") from exc
    except KeyError as exc:
        raise HTTPException(404, "听书片段不存在或已取消") from exc
    _audiobook_quota(
        owner,
        ip,
        "segment-requests",
        owner_limit=180,
        ip_limit=360,
        window=60,
    )
    # This endpoint delivers immutable server-owned book audio. Charging the
    # source text on every cache read or reconnect eventually blocks ordinary
    # listening even though no new TTS work is performed. Abuse is bounded by
    # the request quota above and by the owner/IP/site synthesis leases below.
    async with tts_concurrency_limiter().slot(owner, ip):
        audio = await _audiobook_segment_while_active(
            segment,
            request,
            session_id,
            manifest_hash,
            owner,
        )
    if audio is None:
        raise HTTPException(409, "听书会话已结束")
    duration_ms = mp3_duration_ms(audio)
    await asyncio.to_thread(
        _record_audiobook_segment_duration, segment, audio, duration_ms
    )
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-SHA256": sha256(audio).hexdigest(),
            "X-TTS-Segment": segment["sha256"],
            "X-Audio-Duration-Ms": str(duration_ms),
        },
    )


@app.get("/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3")
async def audiobook_chapter_stream(
    session_id: str,
    manifest_hash: str,
    request: Request,
    start: int = Query(default=0, ge=0),
    offset_ms: int = Query(default=0, ge=0, le=3_600_000),
    stream_id: str | None = Query(default=None, max_length=128),
    continuous: bool = Query(default=False),
    full_chapter: bool = Query(default=False),
):
    """Stream audiobook audio through an idempotent media URL.

    Ordinary clients receive one five-segment window. Background-capable
    clients may request the remaining chapter through one media URL; synthesis
    is still bounded to five concurrent segments at a time. Media stacks may
    silently GET that URL again without rewinding ``currentTime``. A repeated
    stream ID therefore resumes from the latest client-confirmed playback
    cursor instead of replaying the stale query-string start.
    """
    owner = _audiobook_session_owner(request, session_id)
    ip = _request_ip(request)
    try:
        manifest = audiobook_service().manifest(session_id, manifest_hash, owner)
    except AudiobookContractError as exc:
        raise HTTPException(409, "听书预载清单校验失败，请刷新后重试") from exc
    except KeyError as exc:
        raise HTTPException(404, "听书章节不存在或已取消") from exc
    segments = list(manifest.get("segments") or [])
    if start >= len(segments):
        raise HTTPException(404, "听书起始片段不存在")
    requested_start = start
    requested_offset_ms = offset_ms
    stream_id = _normalize_audiobook_stream_id(stream_id)
    claimed_stream_intent = await asyncio.to_thread(
        _claim_audiobook_stream_intent,
        session_id,
        manifest_hash,
        stream_id,
    )
    replay_resumed = False
    if continuous and stream_id and not claimed_stream_intent:
        replay_cursor = await asyncio.to_thread(
            _read_audiobook_stream_cursor,
            session_id,
            manifest_hash,
            stream_id,
        )
        if replay_cursor is not None:
            cursor_start, cursor_offset_ms = replay_cursor
            if cursor_start >= len(segments):
                cursor_start = len(segments) - 1
                cached_target, _cached_lock = _audiobook_session_segment_paths(
                    session_id, manifest_hash, segments[cursor_start]
                )
                cached_audio = await asyncio.to_thread(
                    _read_audiobook_session_segment, cached_target
                )
                if cached_audio:
                    cached_duration_ms = await asyncio.to_thread(
                        mp3_duration_ms, cached_audio
                    )
                    cursor_offset_ms = max(0, cached_duration_ms - 100)
            if cursor_start > start or (
                cursor_start == start and cursor_offset_ms > offset_ms
            ):
                start = cursor_start
                offset_ms = cursor_offset_ms
                replay_resumed = True
    batch = segments[start : start + AUDIOBOOK_STREAM_BATCH_SEGMENTS]
    batch_end = start + len(batch)
    response_end = len(segments) if full_chapter else batch_end
    chapter_complete = response_end >= len(segments)
    if claimed_stream_intent:
        try:
            _audiobook_quota(
                owner,
                ip,
                "chapter-stream-requests",
                owner_limit=30,
                ip_limit=60,
                window=60,
            )
        except HTTPException:
            if stream_id:
                try:
                    _audiobook_stream_intent_path(
                        session_id, manifest_hash, stream_id
                    ).unlink()
                except OSError:
                    pass
            raise

    common_headers = {
        "Content-Encoding": "identity",
        "Content-Disposition": "inline",
        "X-Audiobook-Manifest": manifest_hash,
        "X-Audiobook-Start-Segment": str(start),
        "X-Audiobook-Resume-Offset-Ms": str(offset_ms),
        "X-Audiobook-Requested-Start": str(requested_start),
        "X-Audiobook-Requested-Offset-Ms": str(requested_offset_ms),
        "X-Audiobook-Replay-Resumed": "1" if replay_resumed else "0",
        "X-Audiobook-Batch-Start": str(start),
        "X-Audiobook-Batch-End": str(response_end),
        "X-Audiobook-Batch-Size": str(len(batch)),
        "X-Audiobook-Stream-End": str(response_end),
        "X-Audiobook-Chapter-Complete": "1" if chapter_complete else "0",
        "X-Audiobook-Continuous": "1" if continuous else "0",
        "X-Audiobook-Full-Chapter": "1" if full_chapter else "0",
    }

    async def audio_chunks():
        completed = False
        playback_boundary_ms = int(time.time() * 1000)
        first_stream_segment = True
        try:
            if continuous and stream_id:
                await asyncio.to_thread(
                    _write_audiobook_stream_cursor_safely,
                    session_id,
                    manifest_hash,
                    stream_id,
                    start,
                    audio_offset_ms=offset_ms,
                    next_start=start,
                )
            starts = range(
                start,
                response_end,
                AUDIOBOOK_STREAM_BATCH_SEGMENTS,
            )
            for batch_start in starts:
                batch_segments = segments[
                    batch_start : batch_start + AUDIOBOOK_STREAM_BATCH_SEGMENTS
                ]
                first_batch = batch_start == start
                tasks: list[asyncio.Task[bytes | None]] = []
                try:
                    tasks = [
                        asyncio.create_task(
                            _audiobook_stream_segment_while_active(
                                segment,
                                request,
                                session_id,
                                manifest_hash,
                                owner,
                                ip,
                            )
                        )
                        for segment in batch_segments
                    ]
                    for position, (segment, task) in enumerate(
                        zip(batch_segments, tasks)
                    ):
                        if await request.is_disconnected():
                            return
                        if position % 3 == 0:
                            try:
                                audiobook_service().manifest(
                                    session_id, manifest_hash, owner
                                )
                            except KeyError:
                                return
                        # Capacity protects synthesis, not network delivery. If every
                        # slot is momentarily busy, wait here instead of raising after
                        # StreamingResponse has already sent HTTP 200 headers.
                        audio = await task
                        if audio is None:
                            return
                        duration_ms = await asyncio.to_thread(mp3_duration_ms, audio)
                        await asyncio.to_thread(
                            _record_audiobook_segment_duration,
                            segment,
                            audio,
                            duration_ms,
                        )
                        frame_audio = _mp3_frame_payload(audio)
                        segment_index = int(
                            segment.get("index")
                            if segment.get("index") is not None
                            else batch_start + position
                        )
                        resume_offset_ms = (
                            offset_ms if first_batch and position == 0 else 0
                        )
                        remaining_duration_ms = max(0, duration_ms - resume_offset_ms)
                        if continuous and stream_id and not first_stream_segment:
                            prebuffer_ms = min(750, remaining_duration_ms // 4)
                            wait_ms = playback_boundary_ms - int(time.time() * 1000)
                            if wait_ms > prebuffer_ms:
                                await asyncio.sleep((wait_ms - prebuffer_ms) / 1000)
                            if await request.is_disconnected():
                                return
                        if resume_offset_ms > 0:
                            outgoing_audio = _mp3_frame_payload(
                                await asyncio.to_thread(
                                    _trim_mp3_audio, audio, resume_offset_ms
                                )
                            )
                        else:
                            outgoing_audio = frame_audio
                        yield outgoing_audio
                        if continuous and stream_id:
                            if not first_stream_segment:
                                wait_ms = playback_boundary_ms - int(time.time() * 1000)
                                if wait_ms > 0:
                                    await asyncio.sleep(wait_ms / 1000)
                            segment_started_at_ms = (
                                int(time.time() * 1000)
                                if first_stream_segment
                                else playback_boundary_ms
                            )
                            await asyncio.to_thread(
                                _write_audiobook_stream_cursor_safely,
                                session_id,
                                manifest_hash,
                                stream_id,
                                segment_index,
                                audio_offset_ms=resume_offset_ms,
                                duration_ms=duration_ms,
                                started_at_ms=segment_started_at_ms,
                                next_start=min(segment_index + 1, len(segments)),
                            )
                            playback_boundary_ms = (
                                segment_started_at_ms + remaining_duration_ms
                            )
                        first_stream_segment = False
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
            completed = True
        finally:
            if completed and stream_id:
                await asyncio.to_thread(
                    _mark_audiobook_stream_complete,
                    session_id,
                    manifest_hash,
                    stream_id,
                )

    return StreamingResponse(
        audio_chunks(),
        media_type="audio/mpeg",
        headers={
            **common_headers,
            "Accept-Ranges": "none",
            "Cache-Control": "private, no-store",
            "X-Accel-Buffering": "no",
            "X-Audiobook-Stream-Mode": "continuous" if continuous else "live",
        },
    )


@app.get(
    "/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/streams/{stream_id}"
)
def audiobook_chapter_stream_status(
    session_id: str,
    manifest_hash: str,
    stream_id: str,
    request: Request,
):
    """Confirm that the exact five-segment media batch reached server EOF."""
    owner = _audiobook_session_owner(request, session_id)
    stream_id = _normalize_audiobook_stream_id(stream_id)
    if not stream_id:
        raise HTTPException(404, "听书音频流不存在或已取消")
    try:
        audiobook_service().manifest(session_id, manifest_hash, owner)
        completed = _consume_audiobook_stream_receipt(
            session_id, manifest_hash, stream_id
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, "听书音频流不存在或已取消") from exc
    return {"complete": completed}


@app.get("/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/timeline")
def audiobook_chapter_timeline(
    session_id: str,
    manifest_hash: str,
    request: Request,
    start: int = Query(default=0, ge=0),
    limit: int = Query(default=AUDIOBOOK_STREAM_BATCH_SEGMENTS, ge=1, le=64),
):
    owner = _audiobook_session_owner(request, session_id)
    try:
        result = audiobook_service().timeline(
            session_id, manifest_hash, owner, start=start, limit=limit
        )
    except KeyError as exc:
        raise HTTPException(404, "听书时间轴不存在或已取消") from exc
    return JSONResponse(result, headers={"Cache-Control": "private, no-store"})


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return f"User-agent: *\nAllow: /\nSitemap: {SITE_ORIGIN}/sitemap.xml\n"


@app.get("/wp-admin", include_in_schema=False)
@app.get("/wp-login.php", include_in_schema=False)
@app.get("/.env", include_in_schema=False)
@app.get("/xmlrpc.php", include_in_schema=False)
@app.get("/admin/config", include_in_schema=False)
def honeypot_trap(request: Request):
    _rate_limiter.record_honeypot(request)
    raise HTTPException(status_code=404, detail="Not Found")


def _search_favicon(filename: str, media_type: str) -> FileResponse:
    return FileResponse(
        STATIC_ROOT / filename,
        media_type=media_type,
        headers={
            "Cache-Control": SEARCH_FAVICON_CACHE_CONTROL,
            "CDN-Cache-Control": SEARCH_FAVICON_CDN_CACHE_CONTROL,
            "Cloudflare-CDN-Cache-Control": SEARCH_FAVICON_CDN_CACHE_CONTROL,
        },
    )


@app.api_route(
    "/favicon.ico",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
def search_favicon_ico():
    return _search_favicon("favicon.ico", "image/vnd.microsoft.icon")


@app.api_route(
    "/oohstory-favicon-48.png",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
def search_favicon_48():
    return _search_favicon("favicon-48.png", "image/png")


@app.api_route(
    "/oohstory-favicon-96.png",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
def search_favicon_96():
    return _search_favicon("favicon-96.png", "image/png")


def _sitemap_urlset(urls: tuple[str, ...] | list[str]) -> PlainTextResponse:
    locations = "".join(
        f"<url><loc>{escape(url, quote=True)}</loc></url>" for url in urls
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{locations}</urlset>"
    )
    return PlainTextResponse(
        body,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


SITEMAP_BOOKS_PER_FILE = 40_000


@app.get("/sitemaps/static.xml")
def static_sitemap():
    urls = (
        f"{SITE_ORIGIN}/",
        f"{SITE_ORIGIN}/library",
        f"{SITE_ORIGIN}/rankings",
        f"{SITE_ORIGIN}/deconstructions",
        f"{SITE_ORIGIN}/guide",
        f"{SITE_ORIGIN}/about",
        f"{SITE_ORIGIN}/contact",
        f"{SITE_ORIGIN}/client",
    )
    return _sitemap_urlset(urls)


@app.get("/sitemaps/books/{book_id}.xml")
def book_sitemap(book_id: str):
    require_public_book_id(book_id)
    repo = repository()
    repo.get_book(book_id)
    urls = [f"{SITE_ORIGIN}/books/{book_id}"]
    try:
        catalog = repo.reader_catalog(book_id)
    except InputError:
        catalog = {"chapters": []}
    urls.extend(
        f"{SITE_ORIGIN}/books/{book_id}/chapters/{int(chapter['id'])}"
        for chapter in (catalog.get("chapters") or [])
        if int(chapter.get("id") or 0) > 0
    )
    return _sitemap_urlset(urls)


@app.get("/sitemaps/library-{page}.xml")
def library_sitemap(page: int):
    if page < 1:
        raise HTTPException(status_code=404, detail="站点地图不存在")
    repo = repository()
    book_ids = repo.sitemap_book_ids(
        SITEMAP_BOOKS_PER_FILE,
        (page - 1) * SITEMAP_BOOKS_PER_FILE,
    )
    if not book_ids:
        raise HTTPException(status_code=404, detail="站点地图不存在")
    urls = [f"{SITE_ORIGIN}/books/{book_id}" for book_id in book_ids]
    return _sitemap_urlset(urls)


@app.get("/sitemap.xml")
def sitemap():
    sitemap_urls = [f"{SITE_ORIGIN}/sitemaps/static.xml"]
    book_count = repository().sitemap_book_count()
    sitemap_urls.extend(
        f"{SITE_ORIGIN}/sitemaps/library-{page}.xml"
        for page in range(
            1,
            math.ceil(book_count / SITEMAP_BOOKS_PER_FILE) + 1,
        )
    )
    locations = "".join(
        f"<sitemap><loc>{escape(url, quote=True)}</loc></sitemap>"
        for url in sitemap_urls
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{locations}</sitemapindex>"
    )
    return PlainTextResponse(
        body,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def spa_index() -> FileResponse:
    return FileResponse(
        STATIC_ROOT / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


STATIC_SEO_PAGES: dict[str, tuple[str, str]] = {
    "/library": (
        "全局书库｜免费中文小说在线阅读与TXT下载 - OOH Story",
        "浏览 OOH Story 全局书库，按书名、作者、分类与完结状态检索中文小说，在线阅读全文或下载 TXT 电子书。",
    ),
    "/rankings": (
        "热门小说排行榜｜OOH Story",
        "查看 OOH Story 周点击榜、月点击榜、推荐榜、新书榜、收藏榜与完本榜，发现值得追读的中文小说。",
    ),
    "/deconstructions": (
        "拆书档案｜网文结构与写作技法分析 - OOH Story",
        "浏览 OOH Story 深度拆书档案，查看小说结构、情节节奏、人物关系与写作技法分析。",
    ),
    "/about": (
        "关于 OOH Story",
        "了解 OOH Story 免费、开源、自托管的中文小说阅读与拆书档案服务。",
    ),
    "/disclaimer": (
        "免责声明｜OOH Story",
        "查看 OOH Story 的内容来源、版权与使用边界说明。",
    ),
    "/guide": (
        "使用指南｜OOH Story",
        "了解如何使用 OOH Story 检索书库、在线阅读、下载电子书及浏览拆书档案。",
    ),
    "/contact": ("联系我们｜OOH Story", "查看 OOH Story 的联系与问题反馈方式。"),
    "/client": (
        "客户端下载｜OOH Story",
        "下载 OOH Story 官方客户端，获得移动阅读与智能听书体验。",
    ),
}


def _static_seo_page(path: str) -> HTMLResponse:
    title, description = STATIC_SEO_PAGES[path]
    canonical = f"{SITE_ORIGIN}{path}"
    return _seo_html_response(
        title=title,
        description=description,
        keywords=SITE_KEYWORDS,
        canonical=canonical,
        page_type="website",
        image=SITE_DEFAULT_IMAGE,
        image_alt="OOH Story 品牌图标",
        author=SITE_NAME,
        entity={
            "@type": "CollectionPage"
            if path in {"/library", "/rankings", "/deconstructions"}
            else "WebPage",
            "@id": f"{canonical}#page",
            "url": canonical,
            "name": title,
            "description": description,
            "inLanguage": "zh-CN",
        },
        fallback_html=_seo_fallback_article(
            title,
            [description],
            [("/library", "浏览全局书库"), ("/deconstructions", "浏览拆书档案")],
        ),
    )


@app.get("/library", response_class=HTMLResponse)
def library_page():
    return _static_seo_page("/library")


@app.get("/books/{book_id}", response_class=HTMLResponse)
def book_page(book_id: str):
    require_public_book_id(book_id)
    repo = repository()
    book = repo.get_book(book_id)
    canonical = f"{SITE_ORIGIN}/books/{book_id}"
    image = _public_url(
        book.get("cover_url"),
        "/icon-512.png",
    )
    description = _book_seo_description(book)
    links = [("/library", "返回全局书库")]
    try:
        catalog = repo.reader_catalog(book_id)
    except InputError:
        catalog = {"chapters": []}
    first_chapter_id = catalog.get("first_chapter_id")
    if not first_chapter_id:
        first_chapter_id = next(
            (
                int(chapter.get("id") or 0)
                for chapter in (catalog.get("chapters") or [])
                if int(chapter.get("id") or 0) > 0
            ),
            None,
        )
    if first_chapter_id:
        links.append(
            (
                f"{canonical}/chapters/{int(first_chapter_id)}",
                "开始阅读第一章",
            )
        )
    return _seo_html_response(
        title=_book_seo_title(book),
        description=description,
        keywords=_book_seo_keywords(book),
        canonical=canonical,
        page_type="book",
        image=image,
        image_alt=f"《{_seo_text(book.get('title'), 80)}》封面",
        author=_seo_text(book.get("author"), 100) or "佚名",
        entity=_book_entity(
            book,
            canonical=canonical,
            description=description,
            image=image,
        ),
        fallback_html=_seo_fallback_article(
            f"《{_seo_text(book.get('title'), 160) or '未命名作品'}》",
            [
                description,
                f"作者：{_seo_text(book.get('author'), 100) or '佚名'} · "
                f"分类：{_seo_text(book.get('category'), 40) or '中文小说'}",
            ],
            links,
        ),
    )


def _chapter_heading(chapter: dict[str, Any], position: int) -> str:
    label = _seo_text(chapter.get("label"), 40)
    title = _seo_text(chapter.get("title"), 100)
    fallback = f"第 {position + 1} 章"
    if title and title not in {label, "原文未标注章名"}:
        return f"{label} · {title}" if label else title
    if label and label != "正文":
        return label
    return fallback


@app.get("/books/{book_id}/chapters/{chapter_id}", response_class=HTMLResponse)
def reader_page(book_id: str, chapter_id: int):
    require_public_book_id(book_id)
    if chapter_id <= 0:
        raise HTTPException(status_code=404, detail="章节不存在")
    repo = repository()
    book = repo.get_book(book_id)
    catalog = repo.reader_catalog(book_id)
    chapter_position = next(
        (
            position
            for position, item in enumerate(catalog.get("chapters") or [])
            if int(item.get("id") or 0) == chapter_id
        ),
        None,
    )
    if chapter_position is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    chapter = catalog["chapters"][chapter_position]
    chapter_payload = repo.reader_chapter(book_id, chapter_id)
    heading = _chapter_heading(chapter, chapter_position)
    book_title = _seo_text(book.get("title"), 70) or "未命名作品"
    author = _seo_text(book.get("author"), 30) or "佚名"
    canonical = f"{SITE_ORIGIN}/books/{book_id}/chapters/{chapter_id}"
    parent_canonical = f"{SITE_ORIGIN}/books/{book_id}"
    image = _public_url(
        book.get("cover_url"),
        "/icon-512.png",
    )
    title = _seo_text(
        f"{heading} - 《{book_title}》全文在线阅读｜{SITE_NAME}",
        100,
    )
    description = _seo_text(
        f"《{book_title}》{heading}在线阅读。作者：{author}。"
        f"{SITE_NAME}提供本章纯文本阅读与前后章节导航。",
        180,
    )
    keywords = f"{heading}, {book_title}章节, {_book_seo_keywords(book)}"
    entity = {
        "@type": "Chapter",
        "@id": f"{canonical}#chapter",
        "url": canonical,
        "name": heading,
        "position": chapter_position + 1,
        "isPartOf": {
            "@type": "Book",
            "@id": f"{parent_canonical}#book",
            "url": parent_canonical,
            "name": book_title,
            "author": {"@type": "Person", "name": author},
        },
        "inLanguage": "zh-CN",
    }
    excerpt = _seo_text(chapter_payload.get("content"), 1_200)
    chapter_links: list[tuple[str, str]] = [(parent_canonical, f"《{book_title}》目录")]
    previous_id = chapter_payload.get("previous_id")
    next_id = chapter_payload.get("next_id")
    if previous_id:
        chapter_links.append(
            (f"{parent_canonical}/chapters/{int(previous_id)}", "上一章")
        )
    if next_id:
        chapter_links.append((f"{parent_canonical}/chapters/{int(next_id)}", "下一章"))
    return _seo_html_response(
        title=title,
        description=description,
        keywords=keywords,
        canonical=canonical,
        page_type="article",
        image=image,
        image_alt=f"《{book_title}》封面",
        author=author,
        entity=entity,
        fallback_html=_seo_fallback_article(
            f"《{book_title}》{heading}",
            [f"作者：{author}", excerpt],
            chapter_links,
        ),
    )


@app.get("/books/{book_id}/volumes/{volume_id}", response_class=HTMLResponse)
def volume_page(book_id: str, volume_id: int):
    require_public_book_id(book_id)
    if volume_id <= 0:
        raise HTTPException(status_code=404, detail="分卷不存在")
    repo = repository()
    book = repo.get_book(book_id)
    catalog = repo.reader_catalog(book_id)
    volume = next(
        (
            item
            for item in (catalog.get("volumes") or [])
            if int(item.get("id") or 0) == volume_id
        ),
        None,
    )
    if volume is None:
        raise HTTPException(status_code=404, detail="分卷不存在")
    book_title = _seo_text(book.get("title"), 70) or "未命名作品"
    author = _seo_text(book.get("author"), 30) or "佚名"
    volume_title = _seo_text(volume.get("title"), 80) or f"第 {volume_id} 卷"
    display_title = (
        f"{book_title} {volume_title}"
        if re.fullmatch(r"第[0-9０-９一二三四五六七八九十百零〇两]+卷", volume_title)
        else volume_title
    )
    chapter_count = len(volume.get("chapter_ids") or [])
    canonical = f"{SITE_ORIGIN}/books/{book_id}/volumes/{volume_id}"
    parent_canonical = f"{SITE_ORIGIN}/books/{book_id}"
    image = _public_url(
        book.get("cover_url"),
        "/icon-512.png",
    )
    title = _seo_text(
        f"{display_title} - 《{book_title}》全文在线阅读｜{SITE_NAME}",
        100,
    )
    description = _seo_text(
        f"《{book_title}》{display_title}在线阅读，共收录{chapter_count}章。"
        f"作者：{author}。{SITE_NAME}提供分卷目录与TXT电子书下载。",
        180,
    )
    entity = {
        "@type": "CollectionPage",
        "@id": f"{canonical}#volume",
        "url": canonical,
        "name": display_title,
        "numberOfItems": chapter_count,
        "isPartOf": {
            "@type": "Book",
            "@id": f"{parent_canonical}#book",
            "url": parent_canonical,
            "name": book_title,
            "author": {"@type": "Person", "name": author},
        },
        "inLanguage": "zh-CN",
    }
    chapter_map = {
        int(item.get("id") or 0): item for item in (catalog.get("chapters") or [])
    }
    volume_chapters = [
        _chapter_heading(chapter_map[chapter_id], position)
        for position, chapter_id in enumerate(volume.get("chapter_ids") or [])
        if chapter_id in chapter_map
    ]
    return _seo_html_response(
        title=title,
        description=description,
        keywords=f"{display_title}, {book_title}分卷, {_book_seo_keywords(book)}",
        canonical=canonical,
        page_type="book",
        image=image,
        image_alt=f"《{book_title}》封面",
        author=author,
        entity=entity,
        fallback_html=_seo_fallback_article(
            display_title,
            [
                f"《{book_title}》分卷目录，共收录 {chapter_count} 章。作者：{author}。",
                "、".join(volume_chapters[:24]),
            ],
            [(parent_canonical, f"返回《{book_title}》目录")],
        ),
    )


@app.get("/rankings", response_class=HTMLResponse)
def rankings_page():
    return _static_seo_page("/rankings")


@app.get("/deconstructions", response_class=HTMLResponse)
def deconstructions_page():
    return _static_seo_page("/deconstructions")


@app.get("/account", response_class=FileResponse)
def account_page():
    return spa_index()


@app.get("/admin", response_class=FileResponse)
def admin_page():
    return spa_index()


@app.get("/about", response_class=HTMLResponse)
@app.get("/disclaimer", response_class=HTMLResponse)
@app.get("/guide", response_class=HTMLResponse)
@app.get("/contact", response_class=HTMLResponse)
@app.get("/client", response_class=HTMLResponse)
def static_information_page(request: Request):
    return _static_seo_page(request.url.path)


@app.get("/deconstructions/{slug}", response_class=FileResponse)
def deconstruction_page(slug: str):
    return spa_index()


app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
