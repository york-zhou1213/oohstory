from __future__ import annotations

from base64 import b64encode
from contextlib import asynccontextmanager
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from html import escape
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Callable
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, UUID4, field_validator

from .library import InputError, LibraryError, LibraryRepository, NotFoundError
from .accounts import AccountError, AccountStore
from .category_management import CategoryManager
from .settings import PROJECT_ROOT, load_settings
from .user_api import create_user_router


HOME_FEATURED_BOOK_COUNT = 14
HOME_PRIMARY_CACHE_SECONDS = 60
HOME_SECONDARY_CACHE_SECONDS = 300
STATIC_ROOT = PROJECT_ROOT / "static"
STRUCTURED_DATA_PATTERN = re.compile(
    r'<script id="structured-data" type="application/ld\+json">(.*?)</script>',
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
    "OOH Story, 免费小说阅读, 免费小说下载, 中文小说, 全本小说, "
    "TXT电子书, 深度拆书"
)
SEO_CSP_HASH_HEADER = "X-OOHStory-SEO-CSP-Hash"


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
    digest = b64encode(sha256(json_ld.encode("utf-8")).digest()).decode("ascii")
    return HTMLResponse(
        source,
        headers={
            "Cache-Control": "public, max-age=300",
            SEO_CSP_HASH_HEADER: f"'sha256-{digest}'",
        },
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
                *(
                    _seo_text(item, 40)
                    for item in (book.get("genre_tags") or [])
                ),
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
        except Exception:
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
_home_primary_snapshot = StaleWhileRevalidateSnapshot(
    HOME_PRIMARY_CACHE_SECONDS
)
_home_secondary_snapshot = StaleWhileRevalidateSnapshot(
    HOME_SECONDARY_CACHE_SECONDS
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Each Uvicorn worker owns its own tiny snapshot. Prime both halves without
    # delaying readiness so the first visitor normally receives a warm result.
    def prime_home_snapshots() -> None:
        try:
            home_primary()
            home_secondary()
        except Exception:
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


@lru_cache(maxsize=1)
def repository() -> LibraryRepository:
    return LibraryRepository(settings)


@lru_cache(maxsize=1)
def account_store() -> AccountStore:
    return AccountStore(
        settings.user_database_path,
        session_ttl_seconds=settings.session_ttl_seconds,
    )


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
)
app.include_router(user_router)
app.add_api_route(
    "/",
    getattr(user_router, "google_redirect_handler"),
    methods=["POST"],
    include_in_schema=False,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    dynamic_script_hash = response.headers.get(SEO_CSP_HASH_HEADER, "")
    if SEO_CSP_HASH_HEADER in response.headers:
        del response.headers[SEO_CSP_HASH_HEADER]
    script_sources = ["'self'"]
    if settings.google_web_client_id:
        script_sources.append("https://accounts.google.com/gsi/client")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type == "text/html":
        script_sources.extend([
            dynamic_script_hash or STRUCTURED_DATA_CSP_HASH,
            f"'nonce-{secrets.token_urlsafe(24)}'",
        ])
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
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        f"script-src {' '.join(script_sources)}; script-src-attr 'none'; "
        "connect-src 'self' "
        + ("https://accounts.google.com; " if settings.google_web_client_id else "; ")
        + ("frame-src https://accounts.google.com; " if settings.google_web_client_id else "")
        + "font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    if (
        request.method not in {"GET", "HEAD"}
        or request.url.path.startswith("/api/v1/auth/")
        or request.url.path.startswith("/api/v1/me/")
    ):
        response.headers["Cache-Control"] = "no-store"
    elif (
        request.url.path in {
            "/api/v1/home",
            "/api/v1/home/primary",
            "/api/v1/home/secondary",
            "/api/v1/recommendations",
            "/api/v1/rankings",
        }
        or re.fullmatch(
            r"/api/v1/books/[A-Za-z0-9_-]{22}/metrics",
            request.url.path,
        )
    ):
        response.headers["Cache-Control"] = "no-cache"
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
            directive.casefold() == "no-transform"
            for directive in cache_directives
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
        "recommendations": category_manager.decorate_books(repo.random_recommendations(14)),
    }


def _build_home_secondary(repo: LibraryRepository) -> dict[str, Any]:
    return {
        "long_novels": category_manager.decorate_books(repo.random_recommendations(7, words="over_1m")),
        "short_novels": category_manager.decorate_books(repo.random_recommendations(30, words="under_100k")),
        "category_books": category_manager.decorate_category_books(repo.category_books(10)),
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
    return {"items": category_manager.decorate_books(repository().random_recommendations(14))}


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
def cover(book_id: str, v: str = ""):
    book_id = require_public_book_id(book_id)
    path, current_version = repository().cover_path_and_version(book_id)
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
        headers={
            "Cache-Control": "public, max-age=31536000, immutable, no-transform"
        },
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
def chapter(book_id: str, chapter_id: int):
    book_id = require_public_book_id(book_id)
    return repository().reader_chapter(book_id, chapter_id)


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


@app.get("/api/v1/deconstructions")
def deconstructions():
    return {"items": repository().list_deconstructions()}


@app.get("/api/v1/deconstructions/{slug}")
def deconstruction(slug: str):
    return repository().get_deconstruction(slug)


@app.get("/api/v1/deconstructions/{slug}/file/{subpath:path}")
def deconstruction_file(slug: str, subpath: str):
    return repository().get_deconstruction_file(slug, subpath)


TTS_VOICES = {
    "nuanxi": {"id": "zh-CN-XiaoxiaoNeural", "label": "暖溪", "gender": "female", "desc": "温婉知性"},
    "lingxian": {"id": "zh-CN-XiaoyiNeural", "label": "灵弦", "gender": "female", "desc": "灵动俏皮"},
    "shuanger": {"id": "zh-CN-liaoning-XiaobeiNeural", "label": "霜儿", "gender": "female", "desc": "爽朗飒然"},
    "yanzhi": {"id": "zh-CN-shaanxi-XiaoniNeural", "label": "燕知", "gender": "female", "desc": "清亮质朴"},
    "wanqing": {"id": "zh-HK-HiuGaaiNeural", "label": "晚晴", "gender": "female", "desc": "柔婉细腻"},
    "muyao": {"id": "zh-HK-HiuMaanNeural", "label": "沐瑶", "gender": "female", "desc": "端庄优雅"},
    "qianyu": {"id": "zh-TW-HsiaoChenNeural", "label": "浅语", "gender": "female", "desc": "温润恬静"},
    "ruoxi": {"id": "zh-TW-HsiaoYuNeural", "label": "若汐", "gender": "female", "desc": "甜美亲和"},
    "kuangyun": {"id": "zh-CN-YunjianNeural", "label": "旷云", "gender": "male", "desc": "热血豪迈"},
    "qingyan": {"id": "zh-CN-YunxiNeural", "label": "清砚", "gender": "male", "desc": "少年朗逸"},
    "tongzhen": {"id": "zh-CN-YunxiaNeural", "label": "童真", "gender": "male", "desc": "稚气天真"},
    "mocheng": {"id": "zh-CN-YunyangNeural", "label": "墨澄", "gender": "male", "desc": "沉稳儒雅"},
    "yueming": {"id": "zh-HK-WanLungNeural", "label": "岳鸣", "gender": "male", "desc": "浑厚磁性"},
    "hanfeng": {"id": "zh-TW-YunJheNeural", "label": "寒枫", "gender": "male", "desc": "清冷内敛"},
}

TTS_EMOTIONS = {
    "neutral": {"label": "平稳", "desc": "自然克制的标准叙述", "rate": 0, "pitch": 0, "volume": 0},
    "gentle": {"label": "温柔", "desc": "柔和亲近、低压舒缓", "rate": -8, "pitch": 1, "volume": -4},
    "joyful": {"label": "喜悦", "desc": "明快轻盈、笑意充沛", "rate": 9, "pitch": 4, "volume": 3},
    "excited": {"label": "激昂", "desc": "高能振奋、节奏强烈", "rate": 18, "pitch": 6, "volume": 8},
    "angry": {"label": "愤怒", "desc": "强硬紧迫、压迫感突出", "rate": 12, "pitch": -3, "volume": 10},
    "sad": {"label": "悲伤", "desc": "低沉迟缓、情绪下坠", "rate": -18, "pitch": -5, "volume": -8},
    "fearful": {"label": "惊惧", "desc": "呼吸急促、音线绷紧", "rate": 13, "pitch": 7, "volume": -2},
    "tense": {"label": "紧张", "desc": "节奏收紧、悬念增强", "rate": 8, "pitch": 3, "volume": 2},
    "mysterious": {"label": "神秘", "desc": "低声慢述、幽微莫测", "rate": -14, "pitch": -7, "volume": -10},
    "solemn": {"label": "庄重", "desc": "沉稳肃穆、字句清晰", "rate": -12, "pitch": -5, "volume": 1},
    "affectionate": {"label": "深情", "desc": "细腻温暖、情感绵长", "rate": -10, "pitch": 2, "volume": -3},
    "humorous": {"label": "诙谐", "desc": "灵动俏皮、节拍跳跃", "rate": 10, "pitch": 6, "volume": 1},
    "weary": {"label": "疲惫", "desc": "缓慢虚弱、气息低落", "rate": -22, "pitch": -6, "volume": -12},
}


@app.get("/api/v1/tts/voices")
def tts_voices():
    emotion_keys = list(TTS_EMOTIONS)
    return {
        "voices": [
            {
                "key": k,
                "label": v["label"],
                "gender": v["gender"],
                "desc": v["desc"],
                "emotion_count": len(emotion_keys),
                "emotions": emotion_keys,
            }
            for k, v in TTS_VOICES.items()
        ],
        "emotions": [
            {"key": key, "label": item["label"], "desc": item["desc"]}
            for key, item in TTS_EMOTIONS.items()
        ],
    }


import edge_tts


@app.get("/api/v1/tts/speak")
async def tts_speak(
    text: str = Query(..., max_length=2000),
    voice: str = Query("nuanxi"),
    rate: str = Query("+0%"),
    pitch: str = Query("+0Hz"),
    volume: str = Query("+0%"),
    emotion: str = Query("neutral"),
    style: str = Query(""),
):
    import re as _re
    voice_entry = TTS_VOICES.get(voice)
    if not voice_entry:
        raise HTTPException(400, "不支持的语音")
    clean = text.strip()
    if not clean:
        raise HTTPException(400, "文本不能为空")
    if not _re.fullmatch(r"[+-]\d{1,3}%", rate):
        raise HTTPException(400, "语速格式无效")
    if pitch and not _re.fullmatch(r"[+-]\d{1,3}Hz", pitch):
        raise HTTPException(400, "音调格式无效")
    if volume and not _re.fullmatch(r"[+-]\d{1,3}%", volume):
        raise HTTPException(400, "音量格式无效")

    selected_emotion = emotion.strip().lower()
    if selected_emotion == "neutral" and style.strip().lower() in TTS_EMOTIONS:
        selected_emotion = style.strip().lower()
    profile = TTS_EMOTIONS.get(selected_emotion)
    if not profile:
        raise HTTPException(400, "不支持的情感模式")

    def adjusted(value: str, suffix: str, offset: int, lower: int, upper: int) -> str:
        base = int(value.removesuffix(suffix))
        result = max(lower, min(upper, base + offset))
        return f"{result:+d}{suffix}"

    final_rate = adjusted(rate, "%", profile["rate"], -90, 200)
    final_pitch = adjusted(pitch, "Hz", profile["pitch"], -50, 50)
    final_volume = adjusted(volume, "%", profile["volume"], -50, 50)

    voice_id = voice_entry["id"]

    try:
        comm = edge_tts.Communicate(
            clean,
            voice_id,
            rate=final_rate,
            pitch=final_pitch,
            volume=final_volume,
        )
        audio_data = b""
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
    except Exception:
        raise HTTPException(500, "语音合成失败")

    if not audio_data:
        raise HTTPException(500, "语音合成结果为空")

    return StreamingResponse(
        iter([audio_data]),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-TTS-Emotion": selected_emotion,
        },
    )


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return f"User-agent: *\nAllow: /\nSitemap: {SITE_ORIGIN}/sitemap.xml\n"


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


@app.get("/sitemap.xml")
def sitemap():
    urls = (
        f"{SITE_ORIGIN}/",
        f"{SITE_ORIGIN}/library",
        f"{SITE_ORIGIN}/deconstructions",
    )
    locations = "".join(
        f"<url><loc>{escape(url, quote=True)}</loc></url>" for url in urls
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{locations}</urlset>"
    )
    return PlainTextResponse(body, media_type="application/xml")


def spa_index() -> HTMLResponse:
    source = index_html_template().replace(
        "https://reader.example.com", SITE_ORIGIN
    )
    match = STRUCTURED_DATA_PATTERN.search(source)
    if match is None:
        raise RuntimeError("structured-data JSON-LD script is missing")
    digest = b64encode(
        sha256(match.group(1).encode("utf-8")).digest()
    ).decode("ascii")
    return HTMLResponse(
        source,
        headers={
            "Cache-Control": "no-cache",
            SEO_CSP_HASH_HEADER: f"'sha256-{digest}'",
        },
    )


@app.get("/", response_class=HTMLResponse)
def index_page():
    return spa_index()


@app.get("/library", response_class=HTMLResponse)
def library_page():
    return spa_index()


@app.get("/books/{book_id}", response_class=HTMLResponse)
def book_page(book_id: str):
    require_public_book_id(book_id)
    book = repository().get_book(book_id)
    canonical = f"{SITE_ORIGIN}/books/{book_id}"
    image = _public_url(
        book.get("cover_url"),
        "/icon-512.png",
    )
    description = _book_seo_description(book)
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
    )


@app.get("/rankings", response_class=HTMLResponse)
def rankings_page():
    return spa_index()


@app.get("/deconstructions", response_class=HTMLResponse)
def deconstructions_page():
    return spa_index()


@app.get("/account", response_class=HTMLResponse)
def account_page():
    return spa_index()


@app.get("/about", response_class=HTMLResponse)
@app.get("/disclaimer", response_class=HTMLResponse)
@app.get("/guide", response_class=HTMLResponse)
@app.get("/contact", response_class=HTMLResponse)
@app.get("/client", response_class=HTMLResponse)
def static_information_page():
    return spa_index()


@app.get("/deconstructions/{slug}", response_class=HTMLResponse)
def deconstruction_page(slug: str):
    return spa_index()


app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
