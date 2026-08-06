from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .audit import AuditLog
from .clients import JsonHttpClient, ReaderClient, UpstreamUnavailable
from .config import Settings
from .library_status import LibraryClient
from .library_catalog import LibraryCatalog, VIEWS, hot_cache_from_settings
from .library_actions import LibraryActionClient, LibraryActionError, SEARCH_SOURCES
from .operations import (
    MAX_MULTIPART_BYTES,
    OperationsClient,
    OperationsError,
    parse_multipart,
)
from .script_store import (
    ScriptConflictError,
    ScriptNotFoundError,
    ScriptStore,
    ScriptStoreError,
    ScriptValidationError,
)
from .security import LoginRateLimiter, SessionSigner, verify_password
from oohstory_library.services.default_cover import (
    default_cover_template_path,
)
from .system_stats import system_summary
from .systemd import ActionResult, SystemdController
from .units import ALLOWED_ACTIONS, UNIT_ALLOWLIST


PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_MUTATION_BODY = 64 * 1024
MAX_SCRIPT_MUTATION_BODY = 192 * 1024
MAX_COVER_UPLOAD_BODY = 12 * 1024 * 1024
MAX_CATALOG_DELETE_BOOKS = 100


def _format_bytes(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(number) < 1024 or unit == units[-1]:
            return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024
    return "—"


def _ui(settings: Settings, suffix: str = "") -> str:
    suffix = suffix if suffix.startswith("/") or not suffix else f"/{suffix}"
    if settings.base_path:
        return f"{settings.base_path}{suffix}" or settings.base_path
    return suffix or "/"


def _status_data(statuses: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    item = statuses.get(name) or {}
    data = item.get("data")
    return data if item.get("available") and isinstance(data, dict) else {}


def _progress(done: object, total: object) -> int:
    try:
        denominator = max(int(total or 0), 0)
        numerator = max(int(done or 0), 0)
    except (TypeError, ValueError):
        return 0
    return min(100, round(numerator / denominator * 100)) if denominator else 0


def _library_view_model(
    statuses: dict[str, dict[str, Any]], services: list[dict[str, Any]]
) -> dict[str, Any]:
    """Normalize the library engine status into the full administration view."""
    library = _status_data(statuses, "library")
    books = library.get("books") if isinstance(library.get("books"), dict) else {}
    libraries = books.get("libraries") if isinstance(books.get("libraries"), dict) else {}
    local = libraries.get("local") if isinstance(libraries.get("local"), dict) else {}
    fanqie = libraries.get("fanqie") if isinstance(libraries.get("fanqie"), dict) else {}
    tone = library.get("index") if isinstance(library.get("index"), dict) else _status_data(statuses, "book_index")
    plot = library.get("plot_index") if isinstance(library.get("plot_index"), dict) else _status_data(statuses, "plot_index")
    deconstruction = library.get("deconstruction") if isinstance(library.get("deconstruction"), dict) else {}
    downloads = library.get("download_jobs") if isinstance(library.get("download_jobs"), dict) else {}
    covers = library.get("cover_progress") if isinstance(library.get("cover_progress"), dict) else {}
    sync_controls = _status_data(statuses, "sync_controls")
    infrastructure = _status_data(statuses, "infrastructure")

    category_rows = library.get("categories") if isinstance(library.get("categories"), list) else []
    category_max = max((int(row.get("count") or 0) for row in category_rows), default=0)
    categories = [
        {
            "name": str(row.get("name") or "未分类"),
            "count": int(row.get("count") or 0),
            "percent": _progress(row.get("count"), category_max),
        }
        for row in category_rows[:12]
        if isinstance(row, dict)
    ]

    cover_definitions = (
        ("local_sync", "TXT80 / TXT020 封面", "本地封面同步与安全校验"),
        ("local_source_upgrade", "真实书源替换", "三站封面与正文升级"),
        ("fanqie_sync", "授权书源封面", "新笔趣阁、爱下、书宝与轻小说"),
        ("ai_redraw", "AI 封面兜底", "仅在真实来源无可用资源时运行"),
    )
    cover_cards = []
    for key, label, description in cover_definitions:
        row = covers.get(key) if isinstance(covers.get(key), dict) else {}
        total = int(row.get("total") or 0)
        done = int(row.get("done") or 0)
        cover_cards.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "total": total,
                "done": done,
                "pending": int(row.get("pending") or 0),
                "failed": int(row.get("failed") or 0),
                "percent": _progress(done, total),
                "details": row,
            }
        )

    service_by_unit = {str(item.get("unit") or ""): item for item in services}
    operation_units = (
        "oohstory-library-authorized-catalog-sync.service",
        "oohstory-library-authorized-download.service",
        "oohstory-library-local-source-upgrade.service",
        "oohstory-library-cover-sync.service",
        "oohstory-library-linovelib-cover-sync.service",
        "oohstory-library-index-refresh.service",
        "oohstory-library-derived-index.service",
        "oohstory-deconstruction-sync.service",
    )
    operations = [service_by_unit[unit] for unit in operation_units if unit in service_by_unit]

    queue_labels = {
        "done": "已完成",
        "discovered": "待下载",
        "queued": "排队中",
        "downloading": "下载中",
        "processing": "处理中",
        "failed": "失败",
        "retry": "待重试",
    }
    queue = [
        {"status": key, "label": queue_labels.get(str(key), str(key)), "count": int(value or 0)}
        for key, value in sorted(downloads.items(), key=lambda item: str(item[0]))
    ]

    errors = [
        str(item.get("error"))
        for item in statuses.values()
        if isinstance(item, dict) and not item.get("available") and item.get("error")
    ]
    active_queue = sum(
        int(downloads.get(state) or 0)
        for state in ("discovered", "queued", "downloading", "processing", "retry")
    )
    return {
        "statuses": statuses,
        "library": library,
        "books": books,
        "libraries": {"local": local, "fanqie": fanqie},
        "tone": tone,
        "plot": plot,
        "deconstruction": deconstruction,
        "downloads": downloads,
        "active_queue": active_queue,
        "queue": queue,
        "covers": cover_cards,
        "sync_controls": sync_controls,
        "infrastructure": infrastructure,
        "categories": categories,
        "operations": operations,
        "errors": errors,
        "metrics": (
            ("书目总量", int(books.get("total") or 0), "去重后的唯一书目", "catalog"),
            ("本地书库", int(local.get("total") or 0), f"正文 {int(local.get('downloaded') or 0):,}", "local"),
            ("授权书源", int(fanqie.get("total") or 0), f"正文 {int(fanqie.get('downloaded') or 0):,}", "fanqie"),
            ("正文可用", int(books.get("downloaded") or 0), "可用于阅读、索引与拆书", "readable"),
            ("基调索引", int(tone.get("count") or tone.get("indexed") or 0), "只读派生索引", "tone"),
            ("剧情索引", int(plot.get("books") or plot.get("indexed") or 0), f"{int(plot.get('segments') or 0):,} 个证据片段", "plot"),
            ("全局拆书库", int(deconstruction.get("total") or 0), f"完成 {int(deconstruction.get('completed') or 0):,}", "deconstruction"),
            ("运行中任务", active_queue + int(deconstruction.get("running") or 0), "下载、处理与拆书任务", "running"),
        ),
    }


async def _form_data(
    request: Request,
    *,
    max_body: int = MAX_MUTATION_BODY,
    max_fields: int = 20,
) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="仅支持表单编码")
    body = await request.body()
    if len(body) > max_body:
        raise HTTPException(status_code=413, detail="请求体过大")
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=max_fields)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="表单无效") from exc
    return {key: values[-1] for key, values in parsed.items() if values}


async def _form_multi(
    request: Request,
    *,
    max_body: int = MAX_MUTATION_BODY,
    max_fields: int = 520,
) -> dict[str, list[str]]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="仅支持表单编码")
    body = await request.body()
    if len(body) > max_body:
        raise HTTPException(status_code=413, detail="请求体过大")
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=max_fields)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="表单无效") from exc
    return {key: values for key, values in parsed.items() if values}


def _catalog_delete_request(
    values: list[str], confirmation: str
) -> tuple[list[int], str]:
    """Validate an explicit, bounded destructive selection at the web edge."""

    normalized: set[int] = set()
    for value in values:
        raw = str(value).strip()
        if not raw.isascii() or not raw.isdigit():
            raise ValueError("书目 ID 必须是正整数")
        catalog_id = int(raw)
        if catalog_id <= 0:
            raise ValueError("书目 ID 必须是正整数")
        normalized.add(catalog_id)
    catalog_ids = sorted(normalized)
    if not catalog_ids:
        raise ValueError("请至少明确选择一本小说")
    if len(catalog_ids) > MAX_CATALOG_DELETE_BOOKS:
        raise ValueError(
            f"单次最多删除 {MAX_CATALOG_DELETE_BOOKS} 本小说，禁止筛选全量删除"
        )
    expected = f"确认删除{len(catalog_ids)}本书"
    if str(confirmation or "").strip() != expected:
        raise ValueError(f"请输入确认短语：{expected}")
    return catalog_ids, expected


def _catalog_audit_target(catalog_ids: list[int]) -> str:
    """Keep the exact destructive selection in the durable audit trail."""

    return "catalog_ids:" + ",".join(str(value) for value in catalog_ids)


class PipelineActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=16)
    target: str = Field(min_length=1, max_length=160)


class CatalogWorkbenchActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=32)
    catalog_ids: list[StrictInt] = Field(default_factory=list, max_length=500)
    state: str = Field("unstarted", max_length=16)
    query: str = Field("", max_length=100)
    category: str = Field("", max_length=100)
    runner_id: str = Field("", max_length=64)
    profile_id: str = Field("", max_length=128)
    reasoning_effort: str = Field("", max_length=32)
    confirmation: str = Field("", max_length=64)


class DeconstructionActionRequest(BaseModel):
    operation: str = Field(min_length=1, max_length=16)
    catalog_id: int | None = Field(None, ge=1)
    task_id: str = Field("", max_length=12)
    runner_id: str = Field("", max_length=64)
    profile_id: str = Field("", max_length=128)
    reasoning_effort: str = Field("", max_length=32)


class LibraryImportRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    remote_id: str = Field(min_length=1, max_length=160)
    source_ref: str = Field("", max_length=1000)
    book_status: str = Field("", max_length=32)


class LibrarySyncControlRequest(BaseModel):
    library_id: str = Field(min_length=1, max_length=16)
    enabled: bool


class LibrarySiteFullSyncRequest(BaseModel):
    site_id: str = Field(min_length=1, max_length=16)
    enabled: bool
    books_per_cycle: int | None = Field(None, ge=1, le=500)


class LibraryCoverRedrawControlRequest(BaseModel):
    target_per_hour: int = Field(60, ge=50, le=160)
    enabled: bool | None = None


class LibraryIndexRefreshRequest(BaseModel):
    index_kind: str = Field(min_length=1, max_length=16)
    force: bool = False


class PlotQueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    limit: int = Field(8, ge=1, le=12)


class PlotPlanRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    catalog_ids: list[int] = Field(min_length=1, max_length=12)
    requirement: str = Field("", max_length=2000)
    target_mode: str = Field("ai_recommended", max_length=32)
    target_chapter: int | None = Field(None, ge=1, le=1_000_000)
    start_chapter: int | None = Field(None, ge=1, le=1_000_000)
    end_chapter: int | None = Field(None, ge=1, le=1_000_000)


class PlotPreviewRequest(BaseModel):
    plan_id: str = Field(min_length=12, max_length=12)
    apply_mode: str = Field("local_insert", max_length=32)


class PlotApplyRequest(BaseModel):
    plan_id: str = Field(min_length=12, max_length=12)
    operation: str = Field(min_length=1, max_length=16)


def create_app(
    settings: Settings | None = None,
    *,
    reader: ReaderClient | None = None,
    library: LibraryClient | None = None,
    systemd: SystemdController | None = None,
    audit: AuditLog | None = None,
    script_store: ScriptStore | None = None,
    catalog: LibraryCatalog | None = None,
    library_actions: LibraryActionClient | None = None,
    operations: OperationsClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    package_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(package_dir / "templates"))
    templates.env.filters["filesize"] = _format_bytes

    app = FastAPI(
        title="OOHStory Operations Admin",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    reader = reader or ReaderClient(
        JsonHttpClient(settings.reader_url, timeout=settings.upstream_timeout_seconds)
    )
    hot_cache = hot_cache_from_settings(settings)
    library = library or LibraryClient(settings, cache=hot_cache)
    catalog = catalog or LibraryCatalog(settings, cache=hot_cache)
    catalog_cache = getattr(catalog, "cache", hot_cache)
    library_actions = library_actions or LibraryActionClient(
        settings.library_action_helper_path,
        use_sudo_helper=settings.use_sudo_helper,
    )
    operations = operations or OperationsClient(
        library_actions,
        settings.library_upload_dir,
    )
    systemd = systemd or SystemdController(
        settings.systemctl_path,
        use_sudo_helper=settings.use_sudo_helper,
        helper_path=settings.systemctl_helper_path,
    )
    script_store = script_store or ScriptStore(
        settings.managed_script_root,
        use_sudo_helper=settings.use_sudo_helper,
        helper_path=settings.script_store_helper_path,
    )
    audit = audit or AuditLog(settings.database_path)
    audit.initialize()
    signer = SessionSigner(settings.session_secret, settings.session_ttl_seconds)
    limiter = LoginRateLimiter(settings.login_attempts, settings.login_window_seconds)

    app.state.settings = settings
    app.state.reader = reader
    app.state.library = library
    app.state.catalog = catalog
    app.state.library_actions = library_actions
    app.state.operations = operations
    app.state.systemd = systemd
    app.state.audit = audit
    app.state.script_store = script_store
    app.state.signer = signer
    app.state.login_limiter = limiter

    static_url = settings.static_path
    app.mount(static_url, StaticFiles(directory=str(package_dir / "static")), name="admin-static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            body_limit = (
                MAX_SCRIPT_MUTATION_BODY
                if "/pipeline/scripts/" in request.url.path
                else MAX_COVER_UPLOAD_BODY
                if request.url.path.endswith("/cover-upload")
                else MAX_MULTIPART_BYTES
                if request.url.path.endswith("/operations/novels/upload")
                else MAX_MUTATION_BODY
            )
            length = request.headers.get("content-length")
            if length:
                try:
                    if int(length) > body_limit:
                        return JSONResponse({"detail": "请求体过大"}, status_code=413)
                except ValueError:
                    return JSONResponse({"detail": "Content-Length 无效"}, status_code=400)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; object-src 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        # Cloudflare must not rewrite authenticated HTML or inject scripts that
        # would violate the strict CSP used by this administration surface.
        response.headers["Cache-Control"] = "no-store, no-transform"
        return response

    def session_for(request: Request) -> dict[str, Any] | None:
        token = request.cookies.get(settings.session_cookie, "")
        if not token or not settings.auth_configured:
            return None
        session = signer.verify(token)
        if not session or not hmac.compare_digest(session.get("sub", ""), settings.admin_username):
            return None
        return session

    def require_api_session(request: Request) -> dict[str, Any]:
        session = session_for(request)
        if not session:
            raise HTTPException(status_code=401, detail="需要登录")
        return session

    def require_csrf(request: Request, session: dict[str, Any] = Depends(require_api_session)) -> dict[str, Any]:
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        return session

    def template_context(request: Request, session: dict[str, Any], **extra: object) -> dict[str, object]:
        return {
            "request": request,
            "version": __version__,
            "username": session["sub"],
            "csrf_token": session["csrf"],
            "base_path": settings.base_path,
            "ui_root": settings.ui_root,
            "static_path": settings.static_path,
            **extra,
        }

    def dashboard_data() -> dict[str, Any]:
        functions = {
            "reader_health": reader.health,
            "reader_home": reader.home,
            "system": system_summary,
            "services": systemd.statuses,
        }
        output: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="admin-dashboard") as pool:
            futures = {pool.submit(function): name for name, function in functions.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    value = future.result()
                    if name.startswith("reader_"):
                        output[name] = {
                            "available": value.available,
                            "data": value.data,
                            "error": value.error,
                        }
                    else:
                        output[name] = value
                except Exception:
                    output[name] = {"available": False, "error": "状态读取失败"}
        return output

    def book_bundle(public_id: str) -> dict[str, Any]:
        functions = {
            "book": reader.book,
            "chapters": reader.chapters,
            "metrics": reader.metrics,
        }
        output: dict[str, Any] = {
            "public_id": public_id,
            "capabilities": {
                "read_only": True,
                "search": True,
                "detail": True,
                "metrics": True,
                "mutations": [],
                "reason": "当前 OOHStory reader API 仅提供只读接口",
            },
        }
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="admin-book") as pool:
            futures = {
                pool.submit(function, public_id): name for name, function in functions.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    output[name] = {"available": True, "data": future.result(), "error": None}
                except KeyError:
                    output[name] = {"available": False, "data": None, "error": "书籍不存在"}
                except UpstreamUnavailable as exc:
                    output[name] = {"available": False, "data": None, "error": str(exc)}
                except Exception:
                    output[name] = {"available": False, "data": None, "error": "上游读取失败"}
        return output

    def library_dashboard_data() -> dict[str, Any]:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="admin-library") as pool:
            status_future = pool.submit(library.statuses)
            service_future = pool.submit(systemd.statuses)
            try:
                statuses = status_future.result()
            except Exception:
                statuses = {
                    "library": {
                        "available": False,
                        "data": None,
                        "error": "电子书库状态读取失败",
                        "endpoint": "/api/library/status",
                    }
                }
            try:
                services = service_future.result()
            except Exception:
                services = []
        return _library_view_model(statuses, services)

    def pipeline_services() -> list[dict[str, Any]]:
        services: list[dict[str, Any]] = []
        for original in systemd.statuses():
            service = dict(original)
            managed = script_store.describe_unit(str(service.get("unit") or ""))
            if managed:
                runtime_path = str(service.get("runtime_path") or "")
                managed["runtime_matches"] = bool(
                    runtime_path
                    and Path(runtime_path).resolve(strict=False)
                    == Path(str(managed["absolute_path"])).resolve(strict=False)
                )
                service["script"] = managed
            services.append(service)
        return services

    def perform_action(actor: str, action: str, target: str) -> ActionResult:
        if action not in ALLOWED_ACTIONS or target not in UNIT_ALLOWLIST:
            audit.record(actor, action, target, "rejected:not_allowlisted")
            raise HTTPException(status_code=400, detail="操作或单元不在允许列表中")
        result = systemd.action(action, target)
        audit.record(
            actor,
            action,
            target,
            f"{'success' if result.ok else 'failure'}:{result.message}",
        )
        return result

    @app.get("/healthz")
    async def healthz():
        status = "ok" if settings.auth_configured else "degraded"
        body = {"status": status, "version": __version__, "auth_configured": settings.auth_configured}
        return JSONResponse(body, status_code=200 if settings.auth_configured else 503)

    if settings.base_path:
        @app.get("/", include_in_schema=False)
        async def root_redirect():
            return RedirectResponse(settings.ui_root, status_code=307)

    ui_router = APIRouter(prefix=settings.base_path)

    @ui_router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if session_for(request):
            return RedirectResponse(settings.ui_root, status_code=303)
        token = secrets.token_urlsafe(32)
        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "version": __version__,
                "login_path": settings.login_path,
                "static_path": settings.static_path,
                "login_csrf": token,
                "configured": settings.auth_configured,
                "error": None,
            },
        )
        response.set_cookie(
            f"{settings.session_cookie}_login_csrf",
            token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path=settings.base_path or "/",
            max_age=600,
        )
        return response

    @ui_router.post("/login", response_class=HTMLResponse)
    async def login(request: Request):
        form = await _form_data(request)
        username = form.get("username", "")[:128]
        password = form.get("password", "")[:4096]
        form_csrf = form.get("csrf_token", "")
        cookie_csrf = request.cookies.get(f"{settings.session_cookie}_login_csrf", "")
        if not form_csrf or not cookie_csrf or not hmac.compare_digest(form_csrf, cookie_csrf):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        if not settings.auth_configured:
            raise HTTPException(status_code=503, detail="管理员凭据尚未配置")
        client_host = request.client.host if request.client else "unknown"
        username_key = hashlib.sha256(username.lower().encode("utf-8")).hexdigest()
        keys = (f"ip:{client_host}", f"user:{username_key}")
        limited = limiter.check(*keys)
        if limited.limited:
            return JSONResponse(
                {"detail": "登录尝试过多，请稍后再试"},
                status_code=429,
                headers={"Retry-After": str(limited.retry_after)},
            )
        valid_user = hmac.compare_digest(username, settings.admin_username)
        valid_password = verify_password(password, settings.password_hash)
        if not (valid_user and valid_password):
            limited = limiter.fail(*keys)
            status_code = 429 if limited.limited else 401
            headers = {"Retry-After": str(limited.retry_after)} if limited.limited else None
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "version": __version__,
                    "login_path": settings.login_path,
                    "static_path": settings.static_path,
                    "login_csrf": form_csrf,
                    "configured": True,
                    "error": "用户名或密码错误" if status_code == 401 else "登录尝试过多，请稍后再试",
                },
                status_code=status_code,
                headers=headers,
            )
        limiter.clear(*keys)
        token, _ = signer.create(settings.admin_username)
        response = RedirectResponse(settings.ui_root, status_code=303)
        response.set_cookie(
            settings.session_cookie,
            token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path=settings.cookie_path,
            max_age=settings.session_ttl_seconds,
        )
        response.delete_cookie(
            f"{settings.session_cookie}_login_csrf",
            path=settings.base_path or "/",
        )
        return response

    @ui_router.post("/logout")
    async def logout(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        response = RedirectResponse(settings.login_path, status_code=303)
        response.delete_cookie(settings.session_cookie, path=settings.cookie_path)
        return response

    async def dashboard_page(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        data = await asyncio.to_thread(dashboard_data)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            template_context(request, session, page="dashboard", dashboard=data),
        )

    ui_router.add_api_route("/", dashboard_page, methods=["GET"], response_class=HTMLResponse)
    if settings.base_path:
        ui_router.add_api_route("", dashboard_page, methods=["GET"], response_class=HTMLResponse)

    @ui_router.get("/books", response_class=HTMLResponse)
    @ui_router.get("/books/catalog", response_class=HTMLResponse)
    async def books_page(
        request: Request,
        view: str = Query("all", max_length=32),
        result: str = Query("", max_length=16),
        q: str = Query("", max_length=100),
        category: str = Query("", max_length=100),
        availability: str = Query("all", max_length=16),
        source: str = Query("all", max_length=16),
        tag: str = Query("", max_length=120),
        state: str = Query("all", max_length=16),
        page: int = Query(1, ge=1, le=1_000_000),
        page_size: int = Query(28, ge=1, le=60),
    ):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        if view not in VIEWS:
            raise HTTPException(status_code=400, detail="未知书库视图")
        try:
            data = await asyncio.to_thread(
                catalog.browse,
                view=view,
                query=q,
                category=category,
                availability=availability,
                source=source,
                tag=tag,
                state=state,
                page=page,
                page_size=page_size,
            )
            catalog_result = {"available": True, "data": data, "error": None}
        except Exception as catalog_exc:
            # A fresh installation may bring the admin up before MySQL.  Keep
            # the old public Reader list as an honest all-books fallback.
            if view == "all":
                try:
                    data = await asyncio.to_thread(reader.books, q, category, page, page_size)
                    data.update({"view": "all", "view_label": "书目总量", "categories": [], "source": "reader-fallback"})
                    for item in data.get("items", []):
                        item.update(
                            {
                                "catalog_id": None,
                                "download_status": "done",
                                "body_available": True,
                                "readable": True,
                                "word_count": item.get("approx_word_count", 0),
                                "chapter_count": item.get("approx_chapter_count", 0),
                            }
                        )
                    catalog_result = {"available": True, "data": data, "error": None}
                except UpstreamUnavailable as exc:
                    catalog_result = {
                        "available": False,
                        "data": None,
                        "error": f"Reader API 不可用：{exc}",
                    }
            else:
                catalog_result = {
                    "available": False,
                    "data": None,
                    "error": f"电子书库目录不可用：{type(catalog_exc).__name__}",
                }
        try:
            workspace = await asyncio.to_thread(library_dashboard_data)
        except Exception:
            workspace = {"metrics": (), "tone": {}, "plot": {}, "deconstruction": {}}
        task_runners: dict[str, Any] = {"runners": [], "default_runner": "", "max_parallel": 0}
        if view == "deconstruction":
            try:
                task_runners = await asyncio.to_thread(library_actions.task_runners)
            except LibraryActionError:
                pass
        return templates.TemplateResponse(
            request,
            "books.html",
            template_context(
                request,
                session,
                page="books",
                nav_key="catalog",
                result=catalog_result,
                workspace=workspace,
                view=view,
                query=q,
                category=category,
                availability=availability,
                source=source,
                tag=tag,
                state=state,
                page_size=page_size,
                task_runners=task_runners,
                notice=("操作成功" if result == "ok" else "操作失败，请查看审计日志" if result == "failed" else ""),
            ),
        )

    @ui_router.post("/books/index")
    async def books_index_action(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        index_kind = form.get("index_kind", "")
        if index_kind not in {"tone", "plot"}:
            audit.record(session["sub"], "index_refresh", index_kind, "rejected:index-kind")
            raise HTTPException(status_code=400, detail="未知索引类型")
        force_value = form.get("force", "0")
        if force_value not in {"0", "1"}:
            raise HTTPException(status_code=400, detail="索引刷新模式无效")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "index_refresh",
                {"index_kind": index_kind, "force": force_value == "1"},
                timeout_seconds=90,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "index_refresh", index_kind, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(
            session["sub"],
            "index_refresh",
            index_kind,
            f"success:{'full' if force_value == '1' else 'incremental'}",
        )
        return RedirectResponse(
            f"{_ui(settings, '/books/catalog')}?view={index_kind}&result={'ok' if result.ok else 'failed'}",
            status_code=303,
        )

    @ui_router.post("/books/sync-control")
    async def books_sync_control(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        library_id = form.get("library_id", "")
        enabled_value = form.get("enabled", "")
        if library_id not in {"local", "fanqie"} or enabled_value not in {"0", "1"}:
            raise HTTPException(status_code=400, detail="同步开关参数无效")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "sync_control",
                {
                    "library_id": library_id,
                    "pipeline": "content",
                    "enabled": enabled_value == "1",
                },
                timeout_seconds=120,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "sync_control", library_id, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(
            session["sub"],
            "sync_control",
            library_id,
            f"success:{'enabled' if enabled_value == '1' else 'disabled'}",
        )
        return RedirectResponse(
            f"{_ui(settings, '/library/sync')}?result={'ok' if result.ok else 'failed'}",
            status_code=303,
        )

    @ui_router.post("/books/site-full-sync")
    async def books_site_full_sync(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        site_id = form.get("site_id", "")
        enabled_value = form.get("enabled", "")
        if site_id not in {"txt80", "xbiquge", "ixdzs", "shubaow", "linovelib"} or enabled_value not in {"0", "1"}:
            raise HTTPException(status_code=400, detail="站点全力同步参数无效")
        books_per_cycle: int | None = None
        try:
            books_per_cycle = int(form.get("books_per_cycle", ""))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="每轮本数必须是整数") from exc
        if not 1 <= books_per_cycle <= 500:
            raise HTTPException(status_code=400, detail="每轮本数必须在 1–500 之间")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "site_full_sync",
                {
                    "site_id": site_id,
                    "enabled": enabled_value == "1",
                    "books_per_cycle": books_per_cycle,
                },
                timeout_seconds=120,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "site_full_sync", site_id, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(
            session["sub"],
            "site_full_sync",
            site_id,
            f"success:{'enabled' if enabled_value == '1' else 'disabled'}",
        )
        return RedirectResponse(
            f"{_ui(settings, '/library/sync')}?result={'ok' if result.ok else 'failed'}",
            status_code=303,
        )

    @ui_router.post("/books/cover-redraw-control")
    async def books_cover_redraw_control(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        operation = form.get("operation", "save")
        if operation not in {"save", "start", "stop"}:
            raise HTTPException(status_code=400, detail="封面重绘操作无效")
        try:
            target_per_hour = int(form.get("target_per_hour", ""))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="每小时重绘目标必须是整数") from exc
        if not 50 <= target_per_hour <= 160:
            raise HTTPException(status_code=400, detail="每小时重绘目标必须在 50–160 之间")
        enabled = None if operation == "save" else operation == "start"
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "cover_redraw_control",
                {"target_per_hour": target_per_hour, "enabled": enabled},
                timeout_seconds=120,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "cover_redraw_control", "oohstory", f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(
            session["sub"],
            "cover_redraw_control",
            "oohstory",
            f"success:{operation}:{target_per_hour}",
        )
        return RedirectResponse(
            f"{_ui(settings, '/library/sync')}?result={'ok' if result.ok else 'failed'}",
            status_code=303,
        )

    @ui_router.post("/books/catalog-action")
    async def books_catalog_action(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_multi(request)
        supplied = (form.get("csrf_token") or [""])[-1]
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        view = (form.get("view") or ["all"])[-1]
        if view not in VIEWS:
            raise HTTPException(status_code=400, detail="未知书库视图")
        action = (form.get("action") or [""])[-1]
        if action == "delete_catalog_books":
            if view != "all":
                audit.record(
                    session["sub"], "delete_catalog_books", view,
                    "rejected:not-catalog-total-view",
                )
                raise HTTPException(status_code=400, detail="批量删除仅限书目总量页面")
            try:
                catalog_ids, confirmation = _catalog_delete_request(
                    form.get("catalog_id", []),
                    (form.get("confirmation") or [""])[-1],
                )
            except ValueError as exc:
                audit.record(
                    session["sub"], "delete_catalog_books", "catalog-selection",
                    f"rejected:{str(exc)[:220]}",
                )
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            try:
                catalog_ids = sorted(
                    {
                        int(value)
                        for value in form.get("catalog_id", [])
                        if str(value).isdigit() and int(value) > 0
                    }
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="书目标识无效") from exc
        filtered_batch = action in {"batch_filtered_scan", "batch_filtered_full"}
        if not catalog_ids and not filtered_batch:
            raise HTTPException(status_code=400, detail="请至少选择一本作品")
        payload: dict[str, Any] = {"catalog_ids": catalog_ids}
        helper_action = action
        if action in {"move_local", "move_fanqie"}:
            helper_action = "move"
            payload["target_library"] = action.removeprefix("move_")
        elif action in {"cover_sync", "cover_redraw"}:
            helper_action = "cover"
            payload["cover_action"] = action.removeprefix("cover_")
        elif action in {"batch_scan", "batch_full", "batch_filtered_scan", "batch_filtered_full"}:
            helper_action = "deconstruction_batch"
            payload["mode"] = action.rsplit("_", 1)[-1]
            payload["scope"] = "filtered" if filtered_batch else "selected"
            if filtered_batch:
                payload.update(
                    state=(form.get("state") or ["unstarted"])[-1][:16],
                    query=(form.get("query") or [""])[-1][:100],
                    category=(form.get("category") or [""])[-1][:100],
                )
            for field, maximum in (("runner_id", 64), ("profile_id", 128), ("reasoning_effort", 32)):
                payload[field] = (form.get(field) or [""])[-1][:maximum]
        elif action == "delete_catalog_books":
            helper_action = "delete_catalog_books"
            payload["confirmation"] = confirmation
        else:
            audit.record(session["sub"], "library_action", action, "rejected:not-allowlisted")
            raise HTTPException(status_code=400, detail="书库操作不在允许列表中")
        try:
            helper_timeout = 600 if helper_action == "delete_catalog_books" else None
            result = await asyncio.to_thread(
                library_actions.run,
                helper_action,
                payload,
                timeout_seconds=helper_timeout,
            )
        except LibraryActionError as exc:
            target = (
                _catalog_audit_target(catalog_ids)
                if helper_action == "delete_catalog_books"
                else f"catalog:{len(catalog_ids)}"
            )
            audit.record(
                session["sub"], helper_action, target, f"failure:{str(exc)[:240]}"
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target = (
            _catalog_audit_target(catalog_ids)
            if helper_action == "delete_catalog_books"
            else f"catalog:{len(catalog_ids)}"
        )
        audit.record(
            session["sub"], helper_action, target, f"success:{result.message[:240]}"
        )
        if helper_action == "delete_catalog_books":
            catalog_cache.invalidate(
                "catalog", "book", "cover", "tone", "plot", "deconstruction"
            )
        job_id = str(result.data.get("job_id") or "")
        if job_id:
            return RedirectResponse(_ui(settings, f"/books/jobs/{job_id}"), status_code=303)
        return RedirectResponse(
            f"{_ui(settings, '/books/catalog')}?view={view}&result=ok", status_code=303
        )

    @ui_router.post("/books/deconstruction-action")
    async def books_deconstruction_action(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        operation = form.get("operation", "")
        if operation == "resume":
            helper_action = "resume"
            payload = {"task_id": form.get("task_id", "")}
            target = "resume"
        elif operation in {"scan", "full"}:
            helper_action = "deconstruct"
            catalog_id = form.get("catalog_id", "")
            if not catalog_id.isdigit() or int(catalog_id) <= 0:
                raise HTTPException(status_code=400, detail="书目标识无效")
            payload = {"catalog_id": int(catalog_id), "mode": operation}
            payload.update(
                runner_id=form.get("runner_id", "")[:64],
                profile_id=form.get("profile_id", "")[:128],
                reasoning_effort=form.get("reasoning_effort", "")[:32],
            )
            target = f"catalog:{catalog_id}"
        else:
            raise HTTPException(status_code=400, detail="拆书操作不在允许列表中")
        try:
            result = await asyncio.to_thread(library_actions.run, helper_action, payload)
        except LibraryActionError as exc:
            audit.record(session["sub"], helper_action, target, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(session["sub"], helper_action, target, f"success:{result.message[:240]}")
        return RedirectResponse(
            f"{_ui(settings, '/books/catalog')}?view=deconstruction&result=ok", status_code=303
        )

    @ui_router.get("/books/search", response_class=HTMLResponse)
    async def books_global_search_page(
        request: Request,
        q: str = Query("", max_length=100),
        source: str = Query("local_txt80_catalog", max_length=64),
    ):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        if source not in SEARCH_SOURCES:
            raise HTTPException(status_code=400, detail="未知书源")
        search_result: dict[str, Any] | None = None
        search_error = ""
        if q.strip():
            if len(q.strip()) < 2:
                raise HTTPException(status_code=400, detail="作品名或作者名至少需要 2 个字符")
            try:
                search_result = await asyncio.to_thread(
                    library_actions.search, q.strip(), source, 24
                )
            except LibraryActionError as exc:
                search_error = str(exc)
            audit.record(
                session["sub"],
                "library_search",
                source,
                f"{'failure' if search_error else 'success'}:query-sha256={hashlib.sha256(q.strip().encode()).hexdigest()[:16]}",
            )
        return templates.TemplateResponse(
            request,
            "book_search.html",
            template_context(
                request,
                session,
                page="books",
                nav_key="search",
                query=q,
                selected_source=source,
                search_sources=(
                    ("local_txt80_catalog", "本地电子书库"),
                    ("authorized_xbiquge", "新笔趣阁全章节"),
                    ("authorized_ixdzs", "爱下电子书"),
                    ("authorized_shubaow", "书宝网全章节"),
                    ("linovelib", "哔哩轻小说全章节"),
                    ("authorized_txt80", "txt80.cc TXT"),
                    ("fanqie_desktop_bridge", "番茄小说下载器"),
                    ("authorized_zlibrary", "Z-Library"),
                    ("project_gutenberg", "Project Gutenberg"),
                ),
                search_result=search_result,
                search_error=search_error,
            ),
        )

    @ui_router.post("/books/import")
    async def books_import_action(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request, max_fields=12)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        provider = form.get("provider", "")
        if provider not in SEARCH_SOURCES or provider == "local_txt80_catalog":
            raise HTTPException(status_code=400, detail="未知或不可导入的书源")
        payload = {
            "provider": provider,
            "remote_id": form.get("remote_id", "")[:160],
            "source_ref": form.get("source_ref", "")[:1000],
            "book_status": form.get("book_status", "")[:32],
        }
        try:
            result = await asyncio.to_thread(library_actions.run, "import", payload)
        except LibraryActionError as exc:
            audit.record(session["sub"], "import", provider, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(session["sub"], "import", provider, "success:queued")
        return RedirectResponse(
            _ui(settings, f"/books/jobs/{result.data['job_id']}"), status_code=303
        )

    @ui_router.get("/books/jobs/{job_id}", response_class=HTMLResponse)
    async def books_job_page(request: Request, job_id: str):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        try:
            job = await asyncio.to_thread(library_actions.job, job_id)
        except LibraryActionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request,
            "library_job.html",
            template_context(request, session, page="books", job=job),
        )

    @ui_router.get("/books/tasks/{task_id}", response_class=HTMLResponse)
    async def books_task_page(request: Request, task_id: str):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        try:
            task = await asyncio.to_thread(library_actions.task, task_id)
        except LibraryActionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request,
            "library_task.html",
            template_context(request, session, page="books", task=task),
        )

    @ui_router.get("/books/catalog/{catalog_id:int}", response_class=HTMLResponse)
    async def catalog_book_page(request: Request, catalog_id: int):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        try:
            item = await asyncio.to_thread(catalog.book, catalog_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="书籍不存在") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="电子书库目录不可用") from exc
        return templates.TemplateResponse(
            request,
            "catalog_book.html",
            template_context(request, session, page="books", book=item),
        )

    @ui_router.get("/books/catalog/{catalog_id:int}/plot", response_class=HTMLResponse)
    async def catalog_plot_evidence_page(
        request: Request,
        catalog_id: int,
        page: int = Query(1, ge=1, le=1_000_000),
    ):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        try:
            evidence = await asyncio.to_thread(
                catalog.plot_evidence, catalog_id, page=page, page_size=8
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="剧情证据不存在") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="剧情证据索引不可用") from exc
        return templates.TemplateResponse(
            request,
            "plot_evidence.html",
            template_context(request, session, page="books", evidence=evidence),
        )

    async def render_plot_workbench(
        request: Request,
        session: dict[str, Any],
        *,
        query_result: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        preview: dict[str, Any] | None = None,
        applied: dict[str, Any] | None = None,
        selected_plan_id: str = "",
        notice: str = "",
        error: str = "",
    ):
        history: dict[str, Any] = {"items": [], "project": {}, "project_root": ""}
        try:
            history = (
                await asyncio.to_thread(
                    library_actions.run,
                    "plot_adaptations",
                    timeout_seconds=60,
                )
            ).data
        except LibraryActionError as exc:
            error = error or str(exc)
        if not plan and selected_plan_id:
            plan = next(
                (
                    item
                    for item in history.get("items", [])
                    if str(item.get("id") or "") == selected_plan_id
                ),
                None,
            )
        return templates.TemplateResponse(
            request,
            "plot_workbench.html",
            template_context(
                request,
                session,
                page="books",
                query_result=query_result,
                plan=plan,
                preview=preview,
                applied=applied,
                history=history,
                selected_plan_id=selected_plan_id,
                notice=notice,
                error=error,
            ),
        )

    @ui_router.get("/books/plot-workbench", response_class=HTMLResponse)
    async def plot_workbench_page(
        request: Request,
        plan_id: str = Query("", max_length=12),
    ):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        return await render_plot_workbench(
            request, session, selected_plan_id=plan_id
        )

    @ui_router.post("/books/plot-query", response_class=HTMLResponse)
    async def plot_query_action(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request, max_fields=5)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        question = form.get("question", "").strip()
        if len(question) < 2 or len(question) > 500:
            raise HTTPException(status_code=400, detail="剧情问题需为 2–500 个字符")
        try:
            limit = int(form.get("limit", "8"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="结果数量无效") from exc
        if limit < 1 or limit > 12:
            raise HTTPException(status_code=400, detail="结果数量必须为 1–12")
        digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "plot_query",
                {"question": question, "limit": limit},
                timeout_seconds=600,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "plot_query", digest, f"failure:{str(exc)[:240]}")
            return await render_plot_workbench(request, session, error=str(exc))
        audit.record(session["sub"], "plot_query", digest, "success")
        return await render_plot_workbench(
            request,
            session,
            query_result=result.data,
            notice="剧情问答已完成；答案仅基于本地索引证据。",
        )

    @ui_router.post("/books/plot-plan", response_class=HTMLResponse)
    async def plot_plan_action(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_multi(request, max_fields=24)
        supplied = (form.get("csrf_token") or [""])[-1]
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        question = (form.get("question") or [""])[-1].strip()
        requirement = (form.get("requirement") or [""])[-1].strip()
        target_mode = (form.get("target_mode") or ["ai_recommended"])[-1]
        if len(question) < 2 or len(question) > 500 or len(requirement) > 2000:
            raise HTTPException(status_code=400, detail="剧情方案内容长度无效")
        if target_mode not in {"ai_recommended", "specified_chapter", "plot_arc"}:
            raise HTTPException(status_code=400, detail="剧情融入模式无效")
        try:
            catalog_ids = sorted(
                {
                    int(value)
                    for value in form.get("catalog_id", [])
                    if str(value).isdigit() and int(value) > 0
                }
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="来源书目标识无效") from exc
        if not catalog_ids or len(catalog_ids) > 12:
            raise HTTPException(status_code=400, detail="请选择 1–12 本剧情来源作品")

        def chapter_value(name: str) -> int | None:
            raw = (form.get(name) or [""])[-1].strip()
            if not raw:
                return None
            if not raw.isdigit() or int(raw) <= 0 or int(raw) > 1_000_000:
                raise HTTPException(status_code=400, detail=f"{name} 必须是正整数")
            return int(raw)

        payload = {
            "question": question,
            "catalog_ids": catalog_ids,
            "requirement": requirement,
            "target_mode": target_mode,
            "target_chapter": chapter_value("target_chapter"),
            "start_chapter": chapter_value("start_chapter"),
            "end_chapter": chapter_value("end_chapter"),
        }
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "plot_plan",
                payload,
                timeout_seconds=600,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "plot_plan", f"catalog:{len(catalog_ids)}", f"failure:{str(exc)[:240]}")
            return await render_plot_workbench(request, session, error=str(exc))
        plan_id = str(result.data.get("id") or "")
        audit.record(session["sub"], "plot_plan", plan_id or f"catalog:{len(catalog_ids)}", "success")
        return await render_plot_workbench(
            request,
            session,
            plan=result.data,
            selected_plan_id=plan_id,
            notice="剧情素材已重组为可审阅方案，尚未修改项目正文。",
        )

    @ui_router.post("/books/plot-preview", response_class=HTMLResponse)
    async def plot_preview_action(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request, max_fields=6)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        plan_id = form.get("plan_id", "")
        apply_mode = form.get("apply_mode", "local_insert")
        if not re.fullmatch(r"[a-f0-9]{12}", plan_id) or apply_mode not in {"local_insert", "replace_chapter"}:
            raise HTTPException(status_code=400, detail="剧情预览参数无效")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "plot_preview",
                {"plan_id": plan_id, "apply_mode": apply_mode},
                timeout_seconds=600,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "plot_preview", plan_id, f"failure:{str(exc)[:240]}")
            return await render_plot_workbench(
                request, session, selected_plan_id=plan_id, error=str(exc)
            )
        audit.record(session["sub"], "plot_preview", plan_id, f"success:{apply_mode}")
        return await render_plot_workbench(
            request,
            session,
            preview=result.data,
            selected_plan_id=plan_id,
            notice="正文差异预览已生成；当前仍未写入正文。",
        )

    @ui_router.post("/books/plot-apply", response_class=HTMLResponse)
    async def plot_apply_action(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request, max_fields=6)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        plan_id = form.get("plan_id", "")
        operation = form.get("operation", "")
        if not re.fullmatch(r"[a-f0-9]{12}", plan_id) or operation not in {"bind", "commit"}:
            raise HTTPException(status_code=400, detail="剧情应用参数无效")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "plot_apply",
                {"plan_id": plan_id, "operation": operation},
                timeout_seconds=120,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "plot_apply", plan_id, f"failure:{str(exc)[:240]}")
            return await render_plot_workbench(
                request, session, selected_plan_id=plan_id, error=str(exc)
            )
        audit.record(session["sub"], "plot_apply", plan_id, f"success:{operation}")
        return await render_plot_workbench(
            request,
            session,
            applied=result.data,
            plan=result.data,
            selected_plan_id=plan_id,
            notice=(
                "方案已绑定到后续章节生成链路。"
                if operation == "bind"
                else "已按预览写入正文，并创建可恢复备份。"
            ),
        )

    @ui_router.get("/books/{public_id}", response_class=HTMLResponse)
    async def book_page(request: Request, public_id: str):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        if not PUBLIC_ID_RE.fullmatch(public_id):
            raise HTTPException(status_code=404, detail="书籍不存在")
        data = await asyncio.to_thread(book_bundle, public_id)
        return templates.TemplateResponse(
            request,
            "book_detail.html",
            template_context(request, session, page="books", bundle=data),
        )

    @ui_router.get("/pipeline", response_class=HTMLResponse)
    async def pipeline_page(request: Request, result: str = Query("", max_length=16)):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        statuses = await asyncio.to_thread(pipeline_services)
        return templates.TemplateResponse(
            request,
            "pipeline.html",
            template_context(
                request,
                session,
                page="pipeline",
                services=statuses,
                actions=sorted(ALLOWED_ACTIONS),
                action_path=_ui(settings, "/pipeline/action"),
                notice=("操作成功" if result == "ok" else "操作失败，请查看审计日志" if result == "failed" else ""),
            ),
        )

    @ui_router.post("/pipeline/action")
    async def pipeline_action_page(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        action, target = form.get("action", ""), form.get("target", "")
        result = await asyncio.to_thread(perform_action, session["sub"], action, target)
        return RedirectResponse(
            f"{_ui(settings, '/pipeline')}?result={'ok' if result.ok else 'failed'}",
            status_code=303,
        )

    async def render_script_editor(
        request: Request,
        session: dict[str, Any],
        script_id: str,
        *,
        content: str | None = None,
        expected_sha256: str | None = None,
        error: str = "",
        notice: str = "",
        status_code: int = 200,
    ):
        try:
            script = await asyncio.to_thread(script_store.read, script_id)
        except ScriptNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ScriptStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if content is not None:
            script["content"] = content
        if expected_sha256 is not None:
            script["sha256"] = expected_sha256
        statuses = await asyncio.to_thread(pipeline_services)
        affected = [item for item in statuses if item.get("unit") in script["units"]]
        return templates.TemplateResponse(
            request,
            "script_edit.html",
            template_context(
                request,
                session,
                page="pipeline",
                script=script,
                affected_services=affected,
                save_path=_ui(settings, f"/pipeline/scripts/{script_id}"),
                pipeline_path=_ui(settings, "/pipeline"),
                error=error,
                notice=notice,
            ),
            status_code=status_code,
        )

    @ui_router.get("/pipeline/scripts/{script_id}", response_class=HTMLResponse)
    async def pipeline_script_page(
        request: Request,
        script_id: str,
        saved: str = Query("", max_length=1),
    ):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        return await render_script_editor(
            request,
            session,
            script_id,
            notice="脚本已校验并原子保存；服务未自动重启" if saved == "1" else "",
        )

    @ui_router.post("/pipeline/scripts/{script_id}", response_class=HTMLResponse)
    async def pipeline_script_save(request: Request, script_id: str):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request, max_body=MAX_SCRIPT_MUTATION_BODY, max_fields=8)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            audit.record(session["sub"], "script_save", script_id, "rejected:csrf")
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        content = form.get("content", "")
        expected_sha256 = form.get("expected_sha256", "")
        try:
            result = await asyncio.to_thread(
                script_store.save,
                script_id,
                content,
                expected_sha256,
            )
        except ScriptNotFoundError as exc:
            audit.record(session["sub"], "script_save", script_id, "rejected:not_allowlisted")
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ScriptConflictError as exc:
            audit.record(session["sub"], "script_save", script_id, "rejected:sha_conflict")
            return await render_script_editor(
                request,
                session,
                script_id,
                content=content,
                expected_sha256=expected_sha256,
                error=str(exc),
                status_code=409,
            )
        except ScriptValidationError as exc:
            audit.record(session["sub"], "script_save", script_id, "rejected:validation")
            return await render_script_editor(
                request,
                session,
                script_id,
                content=content,
                expected_sha256=expected_sha256,
                error=str(exc),
                status_code=400,
            )
        except ScriptStoreError as exc:
            audit.record(session["sub"], "script_save", script_id, "failure:helper")
            return await render_script_editor(
                request,
                session,
                script_id,
                content=content,
                expected_sha256=expected_sha256,
                error=str(exc),
                status_code=502,
            )
        audit.record(
            session["sub"],
            "script_save",
            script_id,
            f"success:{result.old_sha256[:12]}->{result.new_sha256[:12]}",
        )
        return RedirectResponse(
            f"{_ui(settings, f'/pipeline/scripts/{script_id}')}?saved=1",
            status_code=303,
        )

    @ui_router.get("/library", response_class=HTMLResponse)
    async def library_page(request: Request, result: str = Query("", max_length=16)):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        dashboard = await asyncio.to_thread(library_dashboard_data)
        return templates.TemplateResponse(
            request,
            "library.html",
            template_context(
                request,
                session,
                page="library",
                nav_key="library",
                dashboard=dashboard,
                library_action_path=_ui(settings, "/library/action"),
                notice=("操作成功" if result == "ok" else "操作失败，请查看审计日志" if result == "failed" else ""),
            ),
        )

    @ui_router.get("/library/sync", response_class=HTMLResponse)
    async def library_sync_page(
        request: Request,
        result: str = Query("", max_length=16),
    ):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        dashboard = await asyncio.to_thread(library_dashboard_data)
        sync_controls: dict[str, Any] = {}
        sync_error = ""
        try:
            sync_controls = (
                await asyncio.to_thread(
                    library_actions.run,
                    "sync_status",
                    timeout_seconds=60,
                )
            ).data
        except LibraryActionError as exc:
            sync_error = str(exc)
        return templates.TemplateResponse(
            request,
            "library_sync.html",
            template_context(
                request,
                session,
                page="library_sync",
                nav_key="sync",
                dashboard=dashboard,
                sync_controls=sync_controls,
                sync_error=sync_error,
                library_action_path=_ui(settings, "/library/action"),
                notice=(
                    "操作成功"
                    if result == "ok"
                    else "操作失败，请查看审计日志"
                    if result == "failed"
                    else ""
                ),
            ),
        )

    @ui_router.get("/jobs", response_class=HTMLResponse)
    async def jobs_page(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        return RedirectResponse(_ui(settings, "/library"), status_code=308)

    async def render_operations_page(
        request: Request,
        session: dict[str, Any],
        *,
        query: str = "",
        status: str = "",
        book_query: str = "",
        publication: str = "",
        notice: str = "",
        error: str = "",
        invite_code: str = "",
    ) -> HTMLResponse:
        data: dict[str, Any] = {}
        if not error:
            try:
                data = await asyncio.to_thread(
                    operations.overview,
                    query=query,
                    status=status,
                )
                publication_data = await asyncio.to_thread(
                    operations.publication_overview,
                    query=book_query,
                    publication=publication,
                )
                data["books"] = publication_data.get("books", [])
                data["book_summary"] = publication_data.get("summary", {})
            except OperationsError as exc:
                error = str(exc)
        return templates.TemplateResponse(
            request,
            "operations.html",
            template_context(
                request,
                session,
                page="operations",
                operations=data,
                query=query,
                status_filter=status,
                book_query=book_query,
                publication_filter=publication,
                notice=notice,
                error=error,
                invite_code=invite_code,
            ),
        )

    def check_form_csrf(form: dict[str, str], session: dict[str, Any]) -> None:
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")

    @ui_router.get("/operations", response_class=HTMLResponse)
    async def operations_page(
        request: Request,
        q: str = Query("", max_length=100),
        status: str = Query("", max_length=16),
        book_q: str = Query("", max_length=100),
        publication: str = Query("", max_length=16),
        result: str = Query("", max_length=16),
    ):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        notice = "操作已完成" if result == "ok" else ""
        error = "操作失败，请查看审计日志" if result == "failed" else ""
        return await render_operations_page(
            request,
            session,
            query=q,
            status=status,
            book_query=book_q,
            publication=publication,
            notice=notice,
            error=error,
        )

    async def operations_form_action(
        request: Request,
        *,
        action: str,
        payload: dict[str, Any],
        target: str,
    ) -> RedirectResponse:
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        try:
            await asyncio.to_thread(operations.run, action, payload)
        except OperationsError as exc:
            audit.record(session["sub"], action, target, f"failure:{str(exc)[:240]}")
            return RedirectResponse(_ui(settings, "/operations?result=failed"), status_code=303)
        audit.record(session["sub"], action, target, "success")
        return RedirectResponse(_ui(settings, "/operations?result=ok"), status_code=303)

    @ui_router.post("/operations/invites/create", response_class=HTMLResponse)
    async def operations_invite_create(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        check_form_csrf(form, session)
        payload = {
            "label": form.get("label", ""),
            "max_uses": form.get("max_uses", "1"),
            "expires_in_days": form.get("expires_in_days", "30"),
        }
        try:
            created = await asyncio.to_thread(operations.run, "invite_create", payload)
        except (OperationsError, ValueError) as exc:
            audit.record(session["sub"], "invite_create", "registration_invite", f"failure:{str(exc)[:240]}")
            return await render_operations_page(request, session, error=str(exc))
        item = created.get("item") if isinstance(created.get("item"), dict) else {}
        audit.record(session["sub"], "invite_create", str(item.get("id") or "registration_invite"), "success")
        return await render_operations_page(
            request,
            session,
            notice="邀请码已生成；完整代码仅在本次显示",
            invite_code=str(created.get("code") or ""),
        )

    @ui_router.post("/operations/invites/{invite_id}/revoke")
    async def operations_invite_revoke(invite_id: str, request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        check_form_csrf(form, session)
        return await operations_form_action(
            request, action="invite_revoke", payload={"invite_id": invite_id}, target=invite_id
        )

    @ui_router.post("/operations/users/{user_id}/status")
    async def operations_user_status(user_id: str, request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        check_form_csrf(form, session)
        return await operations_form_action(
            request,
            action="registered_user_status",
            payload={"user_id": user_id, "status": form.get("status", "")},
            target=user_id,
        )

    @ui_router.post("/operations/users/{user_id}/sessions/revoke")
    async def operations_user_sessions_revoke(user_id: str, request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        check_form_csrf(form, session)
        return await operations_form_action(
            request,
            action="registered_user_sessions_revoke",
            payload={"user_id": user_id},
            target=user_id,
        )

    @ui_router.post("/operations/books/{catalog_id}/publication")
    async def operations_book_publication(catalog_id: int, request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        check_form_csrf(form, session)
        published_value = form.get("published", "")
        if published_value not in {"0", "1"}:
            raise HTTPException(status_code=400, detail="书籍上架状态无效")
        return await operations_form_action(
            request,
            action="book_publication_status",
            payload={
                "catalog_id": catalog_id,
                "published": published_value == "1",
                "reason": form.get("reason", ""),
            },
            target=str(catalog_id),
        )

    @ui_router.post("/operations/categories/create")
    async def operations_category_create(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        check_form_csrf(form, session)
        return await operations_form_action(
            request,
            action="category_create",
            payload={key: form.get(key, "") for key in ("name", "description", "sort_order")},
            target=form.get("name", "")[:80],
        )

    @ui_router.post("/operations/categories/{category_id}/update")
    async def operations_category_update(category_id: str, request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        check_form_csrf(form, session)
        return await operations_form_action(
            request,
            action="category_update",
            payload={
                "category_id": category_id,
                "display_name": form.get("display_name", ""),
                "description": form.get("description", ""),
                "enabled": form.get("enabled") == "1",
                "sort_order": form.get("sort_order", "100"),
            },
            target=category_id,
        )

    @ui_router.post("/operations/categories/{category_id}/delete")
    async def operations_category_delete(category_id: str, request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        check_form_csrf(form, session)
        return await operations_form_action(
            request,
            action="category_delete",
            payload={"category_id": category_id},
            target=category_id,
        )

    @ui_router.post("/operations/novels/upload")
    async def operations_novel_upload(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        token = ""
        try:
            parts = parse_multipart(
                request.headers.get("content-type", ""),
                await request.body(),
            )
            fields = {
                name: part.data.decode("utf-8").strip()
                for name, part in parts.items()
                if not part.filename
            }
            check_form_csrf(fields, session)
            manuscript = parts.get("manuscript")
            cover = parts.get("cover")
            if manuscript is None or cover is None or not manuscript.filename or not cover.filename:
                raise OperationsError("必须同时上传 TXT 正文与封面")
            token = await asyncio.to_thread(operations.stage_novel, manuscript.data, cover.data)
            payload = {
                "upload_token": token,
                "title": fields.get("title", ""),
                "author": fields.get("author", ""),
                "category": fields.get("category", ""),
                "serialization_status": fields.get("serialization_status", ""),
                "summary": fields.get("summary", ""),
                "source": fields.get("source", ""),
                "authorization": fields.get("authorization", ""),
                "manuscript_filename": manuscript.filename,
                "cover_content_type": cover.content_type,
            }
            queued = await asyncio.to_thread(operations.run, "admin_novel_upload", payload)
        except (OperationsError, UnicodeDecodeError, ValueError) as exc:
            if token:
                operations.discard_novel(token)
            audit.record(session["sub"], "admin_novel_upload", "staging", f"failure:{str(exc)[:240]}")
            return await render_operations_page(request, session, error=str(exc))
        audit.record(
            session["sub"],
            "admin_novel_upload",
            str(queued.get("job_id") or token),
            "success:queued",
        )
        return RedirectResponse(_ui(settings, "/operations?result=ok"), status_code=303)

    @ui_router.post("/library/action")
    async def library_action_page(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        form = await _form_data(request)
        supplied = form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        action, target = form.get("action", ""), form.get("target", "")
        if action not in {"start", "restart", "stop"}:
            audit.record(session["sub"], action, target, "rejected:library-surface")
            raise HTTPException(status_code=400, detail="电子书库页面只允许启动、重启或停止")
        result = await asyncio.to_thread(perform_action, session["sub"], action, target)
        return RedirectResponse(
            f"{_ui(settings, '/library/sync')}?result={'ok' if result.ok else 'failed'}",
            status_code=303,
        )

    @ui_router.get("/audit", response_class=HTMLResponse)
    async def audit_page(request: Request):
        session = session_for(request)
        if not session:
            return RedirectResponse(settings.login_path, status_code=303)
        entries = await asyncio.to_thread(audit.list, 100, 0)
        return templates.TemplateResponse(
            request,
            "audit.html",
            template_context(request, session, page="audit", entries=entries),
        )

    app.include_router(ui_router)

    api_router = APIRouter(prefix="/api/admin")

    @api_router.get("/session")
    async def api_session(session: dict[str, Any] = Depends(require_api_session)):
        return {"actor": session["sub"], "csrf_token": session["csrf"], "version": __version__}

    @api_router.get("/dashboard")
    async def api_dashboard(session: dict[str, Any] = Depends(require_api_session)):
        del session
        return await asyncio.to_thread(dashboard_data)

    @api_router.get("/books")
    async def api_books(
        view: str = Query("all", max_length=32),
        q: str = Query("", max_length=100),
        category: str = Query("", max_length=100),
        availability: str = Query("all", max_length=16),
        source: str = Query("all", max_length=16),
        tag: str = Query("", max_length=120),
        state: str = Query("all", max_length=16),
        page: int = Query(1, ge=1, le=1_000_000),
        page_size: int = Query(28, ge=1, le=60),
        session: dict[str, Any] = Depends(require_api_session),
    ):
        del session
        if view not in VIEWS:
            raise HTTPException(status_code=400, detail="未知书库视图")
        try:
            data = await asyncio.to_thread(
                catalog.browse,
                view=view,
                query=q,
                category=category,
                availability=availability,
                source=source,
                tag=tag,
                state=state,
                page=page,
                page_size=page_size,
            )
        except Exception as catalog_exc:
            if view != "all":
                raise HTTPException(status_code=503, detail="电子书库目录不可用") from catalog_exc
            try:
                data = await asyncio.to_thread(reader.books, q, category, page, page_size)
            except UpstreamUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        data["capabilities"] = {
            "read_only_catalog_queries": True,
            "index_modes": {"tone": ["incremental", "full"], "plot": ["incremental", "full"]},
            "sync_controls": ["local", "fanqie"],
            "mutations": [
                "catalog_move", "cover_sync", "cover_redraw", "remote_import",
                "catalog_delete_selected",
                "deconstruction_start", "deconstruction_resume", "deconstruction_batch",
                "plot_question", "plot_adaptation_plan", "plot_adaptation_preview",
                "plot_adaptation_bind", "plot_adaptation_commit",
            ],
            "project_tone_matching": False,
        }
        return data

    @api_router.get("/books/search")
    async def api_books_search(
        q: str = Query(..., min_length=2, max_length=100),
        source: str = Query("local_txt80_catalog", max_length=64),
        limit: int = Query(24, ge=1, le=100),
        session: dict[str, Any] = Depends(require_api_session),
    ):
        del session
        if source not in SEARCH_SOURCES:
            raise HTTPException(status_code=400, detail="未知书源")
        try:
            return await asyncio.to_thread(library_actions.search, q, source, limit)
        except LibraryActionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api_router.post("/books/catalog/actions")
    async def api_catalog_action(
        payload: CatalogWorkbenchActionRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        filtered_batch = payload.action in {"batch_filtered_scan", "batch_filtered_full"}
        if payload.action == "delete_catalog_books":
            try:
                catalog_ids, confirmation = _catalog_delete_request(
                    [str(value) for value in payload.catalog_ids], payload.confirmation
                )
            except ValueError as exc:
                audit.record(
                    session["sub"], "delete_catalog_books", "catalog-selection",
                    f"rejected:{str(exc)[:220]}",
                )
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            catalog_ids = payload.catalog_ids
        if not catalog_ids and not filtered_batch:
            raise HTTPException(status_code=400, detail="请至少选择一本作品")
        request_payload: dict[str, Any] = {"catalog_ids": catalog_ids}
        helper_action = payload.action
        if payload.action in {"move_local", "move_fanqie"}:
            helper_action = "move"
            request_payload["target_library"] = payload.action.removeprefix("move_")
        elif payload.action in {"cover_sync", "cover_redraw"}:
            helper_action = "cover"
            request_payload["cover_action"] = payload.action.removeprefix("cover_")
        elif payload.action in {
            "batch_scan", "batch_full", "batch_filtered_scan", "batch_filtered_full"
        }:
            helper_action = "deconstruction_batch"
            request_payload["mode"] = payload.action.rsplit("_", 1)[-1]
            request_payload["scope"] = "filtered" if filtered_batch else "selected"
            if filtered_batch:
                request_payload.update(
                    state=payload.state,
                    query=payload.query,
                    category=payload.category,
                )
            request_payload.update(
                runner_id=payload.runner_id,
                profile_id=payload.profile_id,
                reasoning_effort=payload.reasoning_effort,
            )
        elif payload.action == "delete_catalog_books":
            helper_action = "delete_catalog_books"
            request_payload["confirmation"] = confirmation
        else:
            raise HTTPException(status_code=400, detail="书库操作不在允许列表中")
        try:
            helper_timeout = 600 if helper_action == "delete_catalog_books" else None
            result = await asyncio.to_thread(
                library_actions.run,
                helper_action,
                request_payload,
                timeout_seconds=helper_timeout,
            )
        except LibraryActionError as exc:
            target = (
                _catalog_audit_target(catalog_ids)
                if helper_action == "delete_catalog_books"
                else f"catalog:{len(catalog_ids)}"
            )
            audit.record(session["sub"], helper_action, target, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if helper_action == "move":
            catalog_cache.invalidate("catalog", "book", "cover", "tone", "plot")
        elif helper_action == "cover":
            catalog_cache.invalidate("cover", "book", "catalog")
        elif helper_action == "deconstruction_batch":
            catalog_cache.invalidate("deconstruction")
        elif helper_action == "delete_catalog_books":
            catalog_cache.invalidate(
                "catalog", "book", "cover", "tone", "plot", "deconstruction"
            )
        target = (
            _catalog_audit_target(catalog_ids)
            if helper_action == "delete_catalog_books"
            else f"catalog:{len(catalog_ids)}"
        )
        audit.record(session["sub"], helper_action, target, "success")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.post("/books/deconstruction/actions")
    async def api_deconstruction_action(
        payload: DeconstructionActionRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        if payload.operation == "resume":
            helper_action = "resume"
            request_payload: dict[str, Any] = {"task_id": payload.task_id}
        elif payload.operation in {"scan", "full"} and payload.catalog_id:
            helper_action = "deconstruct"
            request_payload = {
                "catalog_id": payload.catalog_id,
                "mode": payload.operation,
                "runner_id": payload.runner_id,
                "profile_id": payload.profile_id,
                "reasoning_effort": payload.reasoning_effort,
            }
        else:
            raise HTTPException(status_code=400, detail="拆书操作不在允许列表中")
        try:
            result = await asyncio.to_thread(library_actions.run, helper_action, request_payload)
        except LibraryActionError as exc:
            audit.record(session["sub"], helper_action, "deconstruction", f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        catalog_cache.invalidate("deconstruction")
        audit.record(session["sub"], helper_action, "deconstruction", "success")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.post("/books/import")
    async def api_books_import(
        payload: LibraryImportRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        if payload.provider not in SEARCH_SOURCES or payload.provider == "local_txt80_catalog":
            raise HTTPException(status_code=400, detail="未知或不可导入的书源")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "import",
                {
                    "provider": payload.provider,
                    "remote_id": payload.remote_id,
                    "source_ref": payload.source_ref,
                    "book_status": payload.book_status,
                },
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "import", payload.provider, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        catalog_cache.invalidate("catalog", "book", "cover")
        audit.record(session["sub"], "import", payload.provider, "success:queued")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.get("/books/jobs/{job_id}")
    async def api_library_job(
        job_id: str,
        session: dict[str, Any] = Depends(require_api_session),
    ):
        del session
        try:
            return await asyncio.to_thread(library_actions.job, job_id)
        except LibraryActionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api_router.post("/books/catalog/{catalog_id}/cover-upload")
    async def api_catalog_cover_upload(
        request: Request,
        catalog_id: int,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"image/jpeg", "image/png", "image/gif"}:
            raise HTTPException(status_code=415, detail="仅支持 JPEG、PNG 或 GIF 封面")
        data = await request.body()
        if len(data) < 1024 or len(data) > MAX_COVER_UPLOAD_BODY:
            raise HTTPException(status_code=413, detail="封面大小必须在 1KB 至 12MB 之间")
        root = settings.library_upload_dir
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if root.is_symlink() or root.resolve(strict=True) != root:
                raise OSError("unsafe upload root")
            token = secrets.token_hex(16)
            path = root / f"{token}.bin"
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
            )
            try:
                view = memoryview(data)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short cover upload write")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="封面安全暂存区不可用") from exc
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "upload_cover",
                {"catalog_id": catalog_id, "upload_token": token, "content_type": content_type},
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "upload_cover", f"catalog:{catalog_id}", f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            path.unlink(missing_ok=True)
        catalog_cache.invalidate("cover", "book", "catalog")
        catalog_cache.schedule_warm(
            "book",
            "admin-detail",
            {"catalog_id": int(catalog_id)},
            lambda: {"found": True, "value": catalog.book(catalog_id)},
            ttl_seconds=300,
        )
        audit.record(session["sub"], "upload_cover", f"catalog:{catalog_id}", "success")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.get("/books/catalog/{catalog_id}")
    async def api_catalog_book(
        catalog_id: int,
        session: dict[str, Any] = Depends(require_api_session),
    ):
        del session
        try:
            return await asyncio.to_thread(catalog.book, catalog_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="书籍不存在") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="电子书库目录不可用") from exc

    @api_router.get("/books/catalog/{catalog_id}/cover", response_class=FileResponse)
    async def api_catalog_cover(
        catalog_id: int,
        session: dict[str, Any] = Depends(require_api_session),
    ):
        del session
        try:
            cover = await asyncio.to_thread(catalog.cover_file, catalog_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="封面不存在") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="封面服务不可用") from exc
        return FileResponse(
            cover["path"],
            media_type=cover["media_type"],
            headers={
                "Cache-Control": "no-store, no-transform",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api_router.get("/library/default-cover", response_class=FileResponse)
    async def api_shared_default_cover(
        session: dict[str, Any] = Depends(require_api_session),
    ):
        del session
        path = default_cover_template_path()
        if not path.is_file():
            raise HTTPException(status_code=503, detail="默认封面不可用")
        return FileResponse(
            path,
            media_type="image/jpeg",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @api_router.get("/books/catalog/{catalog_id}/plot")
    async def api_catalog_plot_evidence(
        catalog_id: int,
        page: int = Query(1, ge=1, le=1_000_000),
        page_size: int = Query(8, ge=1, le=20),
        session: dict[str, Any] = Depends(require_api_session),
    ):
        del session
        try:
            return await asyncio.to_thread(
                catalog.plot_evidence, catalog_id, page=page, page_size=page_size
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="剧情证据不存在") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="剧情证据索引不可用") from exc

    @api_router.get("/books/sync-controls")
    async def api_books_sync_controls(
        session: dict[str, Any] = Depends(require_api_session),
    ):
        del session
        try:
            return (
                await asyncio.to_thread(
                    library_actions.run,
                    "sync_status",
                    timeout_seconds=60,
                )
            ).data
        except LibraryActionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api_router.post("/books/sync-control")
    async def api_books_sync_control(
        payload: LibrarySyncControlRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        if payload.library_id not in {"local", "fanqie"}:
            raise HTTPException(status_code=400, detail="未知书库同步开关")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "sync_control",
                {
                    "library_id": payload.library_id,
                    "pipeline": "content",
                    "enabled": payload.enabled,
                },
                timeout_seconds=120,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "sync_control", payload.library_id, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(session["sub"], "sync_control", payload.library_id, f"success:{payload.enabled}")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.post("/books/site-full-sync")
    async def api_books_site_full_sync(
        payload: LibrarySiteFullSyncRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        if payload.site_id not in {"txt80", "xbiquge", "ixdzs", "shubaow", "linovelib"}:
            raise HTTPException(status_code=400, detail="未知正文同步站点")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "site_full_sync",
                {
                    "site_id": payload.site_id,
                    "enabled": payload.enabled,
                    "books_per_cycle": payload.books_per_cycle,
                },
                timeout_seconds=120,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "site_full_sync", payload.site_id, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(session["sub"], "site_full_sync", payload.site_id, f"success:{payload.enabled}")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.post("/books/cover-redraw-control")
    async def api_books_cover_redraw_control(
        payload: LibraryCoverRedrawControlRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "cover_redraw_control",
                {
                    "target_per_hour": payload.target_per_hour,
                    "enabled": payload.enabled,
                },
                timeout_seconds=120,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "cover_redraw_control", "oohstory", f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(
            session["sub"],
            "cover_redraw_control",
            "oohstory",
            f"success:{payload.enabled}:{payload.target_per_hour}",
        )
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.post("/books/indexes")
    async def api_books_index_refresh(
        payload: LibraryIndexRefreshRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        if payload.index_kind not in {"tone", "plot"}:
            raise HTTPException(status_code=400, detail="未知索引类型")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "index_refresh",
                {"index_kind": payload.index_kind, "force": payload.force},
                timeout_seconds=90,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "index_refresh", payload.index_kind, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(session["sub"], "index_refresh", payload.index_kind, f"success:{'full' if payload.force else 'incremental'}")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.post("/books/plot-query")
    async def api_books_plot_query(
        payload: PlotQueryRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        digest = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "plot_query",
                payload.model_dump(),
                timeout_seconds=600,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "plot_query", digest, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(session["sub"], "plot_query", digest, "success")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.post("/books/plot-adapt/plan")
    async def api_books_plot_plan(
        payload: PlotPlanRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        if payload.target_mode not in {"ai_recommended", "specified_chapter", "plot_arc"}:
            raise HTTPException(status_code=400, detail="剧情融入模式无效")
        if any(value <= 0 for value in payload.catalog_ids):
            raise HTTPException(status_code=400, detail="来源书目标识无效")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "plot_plan",
                payload.model_dump(),
                timeout_seconds=600,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "plot_plan", f"catalog:{len(payload.catalog_ids)}", f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        plan_id = str(result.data.get("id") or "")
        audit.record(session["sub"], "plot_plan", plan_id or f"catalog:{len(payload.catalog_ids)}", "success")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.post("/books/plot-adapt/preview")
    async def api_books_plot_preview(
        payload: PlotPreviewRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        if not re.fullmatch(r"[a-f0-9]{12}", payload.plan_id) or payload.apply_mode not in {"local_insert", "replace_chapter"}:
            raise HTTPException(status_code=400, detail="剧情预览参数无效")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "plot_preview",
                payload.model_dump(),
                timeout_seconds=600,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "plot_preview", payload.plan_id, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(session["sub"], "plot_preview", payload.plan_id, f"success:{payload.apply_mode}")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.post("/books/plot-adapt/apply")
    async def api_books_plot_apply(
        payload: PlotApplyRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        if not re.fullmatch(r"[a-f0-9]{12}", payload.plan_id) or payload.operation not in {"bind", "commit"}:
            raise HTTPException(status_code=400, detail="剧情应用参数无效")
        try:
            result = await asyncio.to_thread(
                library_actions.run,
                "plot_apply",
                payload.model_dump(),
                timeout_seconds=120,
            )
        except LibraryActionError as exc:
            audit.record(session["sub"], "plot_apply", payload.plan_id, f"failure:{str(exc)[:240]}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(session["sub"], "plot_apply", payload.plan_id, f"success:{payload.operation}")
        return {"ok": True, "message": result.message, "data": result.data}

    @api_router.get("/books/plot-adaptations")
    async def api_books_plot_adaptations(
        session: dict[str, Any] = Depends(require_api_session),
    ):
        del session
        try:
            return (
                await asyncio.to_thread(
                    library_actions.run,
                    "plot_adaptations",
                    timeout_seconds=60,
                )
            ).data
        except LibraryActionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api_router.get("/books/{public_id}")
    async def api_book(public_id: str, session: dict[str, Any] = Depends(require_api_session)):
        del session
        if not PUBLIC_ID_RE.fullmatch(public_id):
            raise HTTPException(status_code=404, detail="书籍不存在")
        return await asyncio.to_thread(book_bundle, public_id)

    @api_router.get("/pipeline")
    async def api_pipeline(session: dict[str, Any] = Depends(require_api_session)):
        del session
        return {
            "units": await asyncio.to_thread(pipeline_services),
            "allowlist": UNIT_ALLOWLIST,
            "actions": sorted(ALLOWED_ACTIONS),
        }

    @api_router.post("/pipeline/actions")
    async def api_pipeline_action(
        payload: PipelineActionRequest,
        session: dict[str, Any] = Depends(require_csrf),
    ):
        result = await asyncio.to_thread(perform_action, session["sub"], payload.action, payload.target)
        body = {
            "ok": result.ok,
            "action": result.action,
            "target": result.target,
            "message": result.message,
        }
        return JSONResponse(body, status_code=200 if result.ok else 502)

    @api_router.get("/jobs")
    async def api_jobs(session: dict[str, Any] = Depends(require_api_session)):
        del session
        return await asyncio.to_thread(library.statuses)

    @api_router.get("/library")
    async def api_library(session: dict[str, Any] = Depends(require_api_session)):
        del session
        return await asyncio.to_thread(library_dashboard_data)

    @api_router.get("/audit")
    async def api_audit(
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
        session: dict[str, Any] = Depends(require_api_session),
    ):
        del session
        return {"items": await asyncio.to_thread(audit.list, limit, offset)}

    app.include_router(api_router)
    return app
