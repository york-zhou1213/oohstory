"""Public reader account API shared by Web, Android, and iOS."""

from __future__ import annotations

from app.error_boundaries import RECOVERABLE_INTEGRATION_ERRORS

import asyncio
from io import BytesIO
import os
import re
import secrets
import shutil
import tempfile
import uuid
import warnings
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, UUID4, field_validator

from .accounts import (
    ALLOWED_CLIENTS,
    ALLOWED_NOVEL_SUFFIXES,
    ALLOWED_UPLOAD_SUFFIXES,
    AccountError,
    AccountStore,
    SessionContext,
)
from .settings import Settings
from .email_delivery import send_verification, smtp_configured
from .upload_security import UploadSecurityError, UploadSecurityScanner
from .upload_worker import inspect_upload_once
from .submissions import NovelMetadata
from .review_worker import reconcile_results
from .comment_moderation import chapter_paragraphs, moderate_comment


PUBLIC_BOOK_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
GOOGLE_REDIRECT_STATE = "oohstory-web-redirect-v1"
GOOGLE_LINK_STATE = "oohstory-web-link-v1"
GOOGLE_INVITE_COOKIE = "oohstory_google_invite"
GOOGLE_LINK_COOKIE = "oohstory_google_link"
RECOMMENDATION_VISITOR_NAMESPACE = uuid.UUID("a533cc90-f860-4e42-9a34-70a22085eb1c")


def recommendation_visitor_id(user_id: str, event_id: str) -> str:
    """Map one account boost event to a stable UUIDv4-shaped metric visitor."""
    seed = bytearray(
        uuid.uuid5(
            RECOMMENDATION_VISITOR_NAMESPACE,
            f"{str(user_id)}:{str(event_id)}",
        ).bytes
    )
    seed[6] = (seed[6] & 0x0F) | 0x40
    seed[8] = (seed[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(seed)))


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    client: str = "web"

    @field_validator("client")
    @classmethod
    def valid_client(cls, value: str) -> str:
        if value not in ALLOWED_CLIENTS:
            raise ValueError("客户端类型无效")
        return value


class Registration(Credentials):
    display_name: str = Field(default="", max_length=40)
    invite_code: str = Field(default="", max_length=128)


class GoogleCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_token: str = Field(min_length=100, max_length=8192)
    client: str = "web"
    invite_code: str = Field(default="", max_length=128)

    @field_validator("client")
    @classmethod
    def valid_client(cls, value: str) -> str:
        if value not in ALLOWED_CLIENTS:
            raise ValueError("客户端类型无效")
        return value


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=256)


class StateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    book_id: str
    title: str = Field(default="", max_length=200)
    author: str = Field(default="", max_length=100)
    cover_url: str = Field(default="", max_length=500)
    chapter_id: int = Field(default=1, ge=1, le=100_000)
    progress: float = Field(default=0, ge=0, le=1)
    note: str = Field(default="", max_length=500)
    created_at: str = Field(default="", max_length=40)
    updated_at: str = Field(default="", max_length=40)

    @field_validator("book_id")
    @classmethod
    def valid_book_id(cls, value: str) -> str:
        if not PUBLIC_BOOK_ID.fullmatch(value):
            raise ValueError("作品标识无效")
        return value


class StateSync(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history: list[StateItem] = Field(default_factory=list, max_length=500)
    favorites: list[StateItem] = Field(default_factory=list, max_length=1000)
    bookshelf: list[StateItem] = Field(default_factory=list, max_length=1000)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=40)
    bio: str = Field(default="", max_length=500)
    gender: str = Field(default="", max_length=20)
    birthday: str | None = Field(default=None, max_length=10)
    location: str = Field(default="", max_length=80)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class PasswordSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_token: str = Field(min_length=100, max_length=8192)
    new_password: str = Field(min_length=12, max_length=128)
    client: str = "web"

    @field_validator("client")
    @classmethod
    def valid_client(cls, value: str) -> str:
        if value not in ALLOWED_CLIENTS:
            raise ValueError("客户端类型无效")
        return value


class ReadingHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID4
    book_id: str
    active_seconds: int = Field(ge=1, le=60)

    @field_validator("event_id", mode="before")
    @classmethod
    def canonical_event_id(cls, value: object) -> object:
        candidate = str(value or "")
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            candidate,
        ):
            raise ValueError("事件标识无效")
        return candidate

    @field_validator("book_id")
    @classmethod
    def heartbeat_book_id(cls, value: str) -> str:
        if not PUBLIC_BOOK_ID.fullmatch(value):
            raise ValueError("作品标识无效")
        return value


class RecommendationDonation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID4

    @field_validator("event_id", mode="before")
    @classmethod
    def canonical_event_id(cls, value: object) -> object:
        candidate = str(value or "")
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            candidate,
        ):
            raise ValueError("助力事件标识无效")
        return candidate


class ParagraphCommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paragraph_index: int = Field(ge=0, le=200_000)
    content: str = Field(min_length=1, max_length=500)


class BookCommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=500)


def create_user_router(
    settings: Settings,
    repository_provider: Callable[[], Any],
    *,
    on_public_metrics_changed: Callable[[], None] | None = None,
    store_provider: Callable[[], AccountStore] | None = None,
    category_provider: Callable[[], list[dict[str, Any]]] | None = None,
    on_logout: Callable[[str, SessionContext, Request], None] | None = None,
    comment_provider: Callable[[], Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @lru_cache(maxsize=1)
    def default_store() -> AccountStore:
        return AccountStore(
            settings.user_database_path,
            session_ttl_seconds=settings.session_ttl_seconds,
        )

    def store() -> AccountStore:
        return store_provider() if store_provider is not None else default_store()

    def comments() -> Any | None:
        return comment_provider() if comment_provider is not None else None

    def required_comments() -> Any:
        backend = comments()
        if backend is None:
            raise HTTPException(status_code=503, detail="评论存储尚未启用")
        return backend

    def enrich_comments(
        rows: list[dict[str, Any]], viewer_user_id: str | None
    ) -> list[dict[str, Any]]:
        authors = store().comment_authors(
            [str(row.get("user_id") or "") for row in rows]
        )
        result = []
        for row in rows:
            user_id = str(row.get("user_id") or "")
            author = authors.get(user_id)
            if author is None:
                continue
            avatar_version = int(author["avatar_version"])
            viewer_like_count = int(row.get("viewer_like_count") or 0)
            item = {key: value for key, value in row.items() if key != "user_id"}
            item.update(
                {
                    "is_own": bool(viewer_user_id and user_id == viewer_user_id),
                    "thanks_count": int(row.get("like_count") or 0),
                    "thanked_by_me": viewer_like_count > 0,
                    "author": {
                        "display_name": author["display_name"],
                        "avatar_url": (
                            f"/api/v1/users/{user_id}/avatar?v={avatar_version}"
                            if avatar_version > 0
                            else None
                        ),
                        "reading": {
                            key: author["reading"][key]
                            for key in ("level", "roman", "name")
                        },
                    },
                }
            )
            result.append(item)
        return result

    def paragraph_comment_response(
        rows: list[dict[str, Any]], viewer_user_id: str | None
    ) -> dict[str, Any]:
        paragraphs: dict[str, dict[str, Any]] = {}
        for comment in reversed(enrich_comments(rows, viewer_user_id)):
            key = str(comment.get("paragraph_key") or "")
            thread = paragraphs.setdefault(
                key,
                {
                    "paragraph_index": int(comment.get("paragraph_index") or 0),
                    "paragraph_key": key,
                    "excerpt": str(comment.get("paragraph_excerpt") or ""),
                    "count": 0,
                    "total_thanks": 0,
                    "comments": [],
                },
            )
            thread["comments"].append(comment)
            thread["count"] += 1
            thread["total_thanks"] += int(comment.get("like_count") or 0)
        return {
            "paragraphs": paragraphs,
            "comment_count": sum(int(item["count"]) for item in paragraphs.values()),
        }

    def error(exc: AccountError) -> HTTPException:
        return HTTPException(status_code=exc.status_code, detail=exc.detail)

    def request_ip(request: Request) -> str:
        return str(request.client.host if request.client else "")[:100]

    def raw_session_token(request: Request) -> tuple[str, bool]:
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            return authorization[7:].strip(), False
        return request.cookies.get(settings.session_cookie, ""), True

    def auth(request: Request, *, mutation: bool = False) -> SessionContext:
        raw, cookie_auth = raw_session_token(request)
        session = store().session(raw)
        if session is None:
            raise HTTPException(status_code=401, detail="请先登录")
        if mutation and cookie_auth:
            try:
                store().require_csrf(session, request.headers.get("x-csrf-token", ""))
            except AccountError as exc:
                raise error(exc) from exc
        return session

    def optional_auth(request: Request) -> SessionContext | None:
        raw, _cookie_auth = raw_session_token(request)
        return store().session(raw) if raw else None

    def sync_recommendation_metric(
        session: SessionContext, book_id: str, event_id: str
    ) -> tuple[dict[str, Any], bool]:
        repository = repository_provider()
        visitor_id = recommendation_visitor_id(session.user_id, event_id)
        metrics = repository.record_public_metric(book_id, visitor_id, "recommend")
        store().mark_recommendation_applied(session.user_id, event_id)
        if metrics.get("counted") and on_public_metrics_changed is not None:
            on_public_metrics_changed()
        return metrics, False

    def paragraph_context(
        book_id: str, chapter_id: int
    ) -> tuple[dict[str, Any], list[dict[str, object]]]:
        if not PUBLIC_BOOK_ID.fullmatch(str(book_id)) or int(chapter_id) <= 0:
            raise HTTPException(status_code=404, detail="章节不存在")
        try:
            chapter = repository_provider().reader_chapter(
                str(book_id), int(chapter_id)
            )
        except RECOVERABLE_INTEGRATION_ERRORS as exc:
            raise HTTPException(status_code=404, detail="章节不存在") from exc
        paragraphs = chapter_paragraphs(str(chapter.get("content") or ""))
        return chapter, paragraphs

    def issue_session(
        response: Response,
        request: Request,
        user: dict[str, Any],
        client: str,
    ) -> dict[str, Any]:
        session = store().create_session(
            user,
            client=client,
            user_agent=request.headers.get("user-agent", ""),
            ip=request_ip(request),
        )
        result: dict[str, Any] = {
            "user": session.public_user(),
            "csrf_token": session.csrf_token,
            "expires_at": session.expires_at,
        }
        if client == "web":
            response.set_cookie(
                settings.session_cookie,
                session.token,
                max_age=settings.session_ttl_seconds,
                httponly=True,
                secure=True,
                samesite="lax",
                path="/",
            )
        else:
            result["access_token"] = session.token
            result["token_type"] = "Bearer"
        return result

    def avatar_path(user_id: str) -> Path:
        return settings.user_avatar_root / str(user_id) / "avatar.png"

    def authoritative_state(
        user_id: str, catalog_items: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        state = store().state(user_id)
        ids = list(
            dict.fromkeys(
                str(item.get("book_id") or "")
                for values in state.values()
                for item in values
                if item.get("book_id")
            )
        )
        repository = repository_provider()
        if catalog_items is not None:
            authoritative = catalog_items
        elif hasattr(repository, "account_state_books"):
            authoritative = repository.account_state_books(ids)
        else:
            authoritative = {}
            for book_id in ids[:100]:
                try:
                    book = repository.get_book(book_id)
                except RECOVERABLE_INTEGRATION_ERRORS:
                    continue
                authoritative[book_id] = {
                    "book_id": book_id,
                    "title": book.get("title") or "",
                    "author": book.get("author") or "",
                    "cover_url": book.get("cover_url") or "",
                    "serialization_status": book.get("serialization_status")
                    or "ongoing",
                    "chapter_count": int(book.get("approx_chapter_count") or 0),
                }
        for kind, values in state.items():
            for item in values:
                catalog = authoritative.get(str(item.get("book_id") or ""))
                if not catalog:
                    item["catalog_available"] = False
                    continue
                item.update(catalog)
                item["catalog_available"] = True
                if kind == "history":
                    chapter_id = max(int(item.get("chapter_id") or 1), 1)
                    within = max(0.0, min(float(item.get("progress") or 0), 1.0))
                    count = max(int(catalog.get("chapter_count") or 0), 0)
                    item["chapter_progress"] = within
                    item["overall_progress"] = (
                        max(0.0, min((chapter_id - 1 + within) / count, 1.0))
                        if count
                        else 0.0
                    )
                    item["current_chapter"] = f"第 {chapter_id} 章"
        return state

    def sync_favorite_metrics(book_ids: list[str]) -> None:
        repository = repository_provider()
        if not hasattr(repository, "set_favorite_count"):
            return
        for book_id in dict.fromkeys(book_ids):
            repository.set_favorite_count(book_id, store().favorite_count(book_id))
        if book_ids and on_public_metrics_changed is not None:
            on_public_metrics_changed()

    def profile_payload(user_id: str) -> dict[str, Any]:
        profile = store().profile(user_id)
        version = int(profile.pop("avatar_version", 0))
        exists = avatar_path(user_id).is_file()
        profile["avatar_url"] = f"/api/v1/me/avatar?v={version}" if exists else None
        return {
            "profile": profile,
            "reading": store().reading_summary(user_id),
            "login_methods": store().login_methods(user_id),
        }

    def decode_image_bytes(raw: bytes | bytearray) -> Image.Image:
        if not raw:
            raise HTTPException(status_code=422, detail="图片文件为空")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                probe = Image.open(BytesIO(raw))
                if str(probe.format or "").upper() not in {"JPEG", "PNG", "WEBP"}:
                    raise HTTPException(
                        status_code=415, detail="仅支持 JPEG、PNG 或 WebP 图片"
                    )
                width, height = probe.size
                if (
                    width < 32
                    or height < 32
                    or width * height > settings.max_avatar_pixels
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="图片尺寸须不小于 32×32 且不超过像素上限",
                    )
                probe.verify()
                decoded = Image.open(BytesIO(raw))
                decoded.load()
        except HTTPException:
            raise
        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise HTTPException(status_code=422, detail="图片无法安全解码") from exc
        decoded = ImageOps.exif_transpose(decoded)
        if decoded.mode not in {"RGB", "RGBA"}:
            decoded = decoded.convert("RGBA" if "A" in decoded.getbands() else "RGB")
        decoded.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        return decoded

    async def decode_avatar(file: UploadFile) -> Image.Image:
        raw = bytearray()
        try:
            while chunk := await file.read(256 * 1024):
                raw.extend(chunk)
                if len(raw) > settings.max_avatar_bytes:
                    raise HTTPException(
                        status_code=413, detail="头像文件不能超过上传大小限制"
                    )
        finally:
            await file.close()
        return decode_image_bytes(raw)

    @router.get("/auth/config")
    def auth_config() -> dict[str, Any]:
        return {
            "registration_enabled": True,
            "registration_mode": "open",
            "invite_required": False,
            "email_verification_delivery": smtp_configured(settings),
            "google": {
                "web_enabled": bool(settings.google_web_client_id),
                "web_client_id": settings.google_web_client_id,
                "android_enabled": bool(settings.google_android_client_id),
                "ios_enabled": bool(settings.google_ios_client_id),
                "first_login_creates_account": True,
                "existing_account_link_required": False,
                "local_password_optional": True,
            },
            "upload": {
                "max_bytes": settings.max_upload_bytes,
                "extensions": sorted(ALLOWED_UPLOAD_SUFFIXES),
                "verification_required": True,
            },
        }

    @router.post("/auth/register", status_code=201)
    def register(
        payload: Registration, request: Request, response: Response
    ) -> dict[str, Any]:
        try:
            email_key = payload.email.strip().casefold()
            store().enforce_rate_limit(
                f"register:{request_ip(request)}:{email_key}", limit=5
            )
            user, verification = store().register(
                payload.email,
                payload.password,
                payload.display_name,
                payload.invite_code,
            )
            result = issue_session(response, request, user, payload.client)
            result["verification_required"] = True
            try:
                result["verification_sent"] = send_verification(
                    settings, user["email"], verification
                )
            except RECOVERABLE_INTEGRATION_ERRORS:
                result["verification_sent"] = False
            return result
        except AccountError as exc:
            raise error(exc) from exc

    @router.post("/auth/login")
    def login(
        payload: Credentials, request: Request, response: Response
    ) -> dict[str, Any]:
        try:
            store().enforce_rate_limit(
                f"login:{request_ip(request)}:{payload.email.strip().casefold()}",
                limit=8,
            )
            user = store().password_login(payload.email, payload.password)
            return issue_session(response, request, user, payload.client)
        except AccountError as exc:
            raise error(exc) from exc

    def google_claims(
        id_token: str,
        request: Request,
        client: str,
    ) -> dict[str, Any]:
        platform_audience = {
            "web": settings.google_web_client_id,
            "android": settings.google_android_client_id,
            "ios": settings.google_ios_client_id,
        }[client]
        audiences = tuple(
            dict.fromkeys(
                value
                for value in (platform_audience, settings.google_web_client_id)
                if value
            )
        )
        if not audiences:
            raise HTTPException(status_code=503, detail="Google 登录尚未配置")
        store().enforce_rate_limit(f"google:{request_ip(request)}", limit=12)
        claims = None
        for audience in audiences:
            try:
                claims = google_id_token.verify_oauth2_token(
                    id_token,
                    GoogleRequest(),
                    audience=audience,
                    clock_skew_in_seconds=30,
                )
                break
            except RECOVERABLE_INTEGRATION_ERRORS:
                continue
        if claims is None:
            raise AccountError("Google 登录凭据的接收方无效", 401)
        if claims.get("iss") not in {
            "accounts.google.com",
            "https://accounts.google.com",
        }:
            raise AccountError("Google 身份签发方无效", 401)
        return claims

    @router.post("/auth/google")
    def google_login(
        payload: GoogleCredential, request: Request, response: Response
    ) -> dict[str, Any]:
        try:
            claims = google_claims(
                payload.id_token,
                request,
                payload.client,
            )
            user = store().google_login(claims)
            return issue_session(response, request, user, payload.client)
        except AccountError as exc:
            raise error(exc) from exc
        except HTTPException:
            raise
        except RECOVERABLE_INTEGRATION_ERRORS as exc:
            raise HTTPException(status_code=401, detail="Google 登录凭据无效") from exc

    @router.post("/auth/google/link")
    def link_google(
        payload: GoogleCredential,
        request: Request,
    ) -> dict[str, Any]:
        session = auth(request, mutation=True)
        try:
            claims = google_claims(payload.id_token, request, payload.client)
            return {"user": store().link_google(session.user_id, claims)}
        except AccountError as exc:
            raise error(exc) from exc
        except HTTPException:
            raise
        except RECOVERABLE_INTEGRATION_ERRORS as exc:
            raise HTTPException(status_code=401, detail="Google 登录凭据无效") from exc

    @router.post("/auth/google/link/start")
    def start_google_link(request: Request, response: Response) -> dict[str, Any]:
        session = auth(request, mutation=True)
        try:
            link_token = store().create_google_link_token(session.user_id)
        except AccountError as exc:
            raise error(exc) from exc
        response.set_cookie(
            GOOGLE_LINK_COOKIE,
            link_token,
            max_age=600,
            httponly=True,
            secure=True,
            samesite="none",
            path="/",
        )
        return {"state": GOOGLE_LINK_STATE, "expires_in": 600}

    def redirect_error(detail: str) -> RedirectResponse:
        response = RedirectResponse(
            url=f"/?google_error={quote(detail, safe='')}#/",
            status_code=303,
        )
        response.delete_cookie(
            GOOGLE_INVITE_COOKIE, path="/", secure=True, samesite="lax"
        )
        response.delete_cookie(
            GOOGLE_LINK_COOKIE, path="/", secure=True, samesite="none"
        )
        return response

    async def google_redirect(request: Request) -> Response:
        """Receive GIS redirect UX at the already-authorized site root."""

        form = await request.form()
        csrf_cookie = request.cookies.get("g_csrf_token", "")
        csrf_body = str(form.get("g_csrf_token", ""))
        if (
            not csrf_cookie
            or not csrf_body
            or not secrets.compare_digest(
                csrf_cookie,
                csrf_body,
            )
        ):
            return redirect_error("Google 登录安全校验失败，请重新尝试")
        state = str(form.get("state", ""))
        if state not in {GOOGLE_REDIRECT_STATE, GOOGLE_LINK_STATE}:
            return redirect_error("Google 登录状态无效，请重新尝试")
        credential = str(form.get("credential", ""))
        if not 100 <= len(credential) <= 8192:
            return redirect_error("Google 登录凭据无效")
        try:
            claims = google_claims(credential, request, "web")
            if state == GOOGLE_LINK_STATE:
                link_token = request.cookies.get(GOOGLE_LINK_COOKIE, "")[:128]
                store().link_google_with_token(link_token, claims)
                response = RedirectResponse(
                    url="/?google_linked=1#/account",
                    status_code=303,
                )
            else:
                user = store().google_login(claims)
                response = RedirectResponse(url="/#/account", status_code=303)
                issue_session(response, request, user, "web")
            response.delete_cookie(
                GOOGLE_INVITE_COOKIE,
                path="/",
                secure=True,
                samesite="lax",
            )
            response.delete_cookie(
                GOOGLE_LINK_COOKIE,
                path="/",
                secure=True,
                samesite="none",
            )
            return response
        except AccountError as exc:
            return redirect_error(exc.detail)
        except RECOVERABLE_INTEGRATION_ERRORS:
            return redirect_error("Google 登录凭据无效")

    @router.post("/auth/verify-email")
    def verify_email(payload: Verification) -> dict[str, Any]:
        try:
            return {"user": store().verify_email(payload.token)}
        except AccountError as exc:
            raise error(exc) from exc

    @router.post("/auth/resend-verification")
    def resend_verification(request: Request) -> dict[str, Any]:
        session = auth(request, mutation=True)
        if not smtp_configured(settings):
            raise HTTPException(status_code=503, detail="邮箱验证服务尚未配置")
        try:
            store().enforce_rate_limit(
                f"verification:{session.user_id}", limit=3, window=3600
            )
            verification = store().create_verification_token(session.user_id)
            sent = send_verification(settings, session.email, verification)
            if not sent:
                raise HTTPException(status_code=503, detail="邮箱验证服务暂时不可用")
            return {"sent": True, "message": "验证邮件已发送，请在 24 小时内完成验证"}
        except AccountError as exc:
            raise error(exc) from exc

    @router.get("/auth/session")
    def current_session(request: Request) -> dict[str, Any]:
        raw, _cookie_auth = raw_session_token(request)
        session = store().session(raw)
        if session is None:
            return {
                "authenticated": False,
                "user": None,
                "expires_at": None,
                "csrf_token": "",
            }
        return {
            "authenticated": True,
            "user": session.public_user(),
            "expires_at": session.expires_at,
            "csrf_token": store().rotate_csrf(raw),
        }

    @router.post("/auth/logout", status_code=204)
    def logout(request: Request, response: Response) -> Response:
        session = auth(request, mutation=True)
        raw, cookie_auth = raw_session_token(request)
        _ = session
        if on_logout is not None:
            try:
                on_logout(raw, session, request)
            except RECOVERABLE_INTEGRATION_ERRORS:
                # Revoking the account session is authoritative. Audiobook
                # cancellation is best-effort and must never trap a user in a
                # logged-in state during a temporary MySQL failure.
                pass
        store().revoke_session(raw)
        if cookie_auth:
            response.delete_cookie(settings.session_cookie, path="/")
        response.status_code = 204
        return response

    @router.get("/me/profile")
    def get_profile(request: Request) -> dict[str, Any]:
        return profile_payload(auth(request).user_id)

    @router.put("/me/profile")
    def update_profile(payload: ProfileUpdate, request: Request) -> dict[str, Any]:
        session = auth(request, mutation=True)
        try:
            store().update_profile(session.user_id, **payload.model_dump())
            result = profile_payload(session.user_id)
            result["user"] = {
                **session.public_user(),
                "display_name": result["profile"]["display_name"],
            }
            return result
        except AccountError as exc:
            raise error(exc) from exc

    @router.post("/me/password")
    def change_password(payload: PasswordChange, request: Request) -> dict[str, Any]:
        session = auth(request, mutation=True)
        try:
            store().enforce_rate_limit(
                f"password-change:{session.user_id}", limit=6, window=3600
            )
            revoked = store().change_password(
                session.user_id,
                session.session_id,
                payload.current_password,
                payload.new_password,
            )
            return {
                "changed": True,
                "created": False,
                "revoked_other_sessions": revoked,
                "login_methods": store().login_methods(session.user_id),
                "message": "密码已更新，其他设备的登录状态已安全退出",
            }
        except AccountError as exc:
            raise error(exc) from exc

    @router.post("/me/password/setup")
    def setup_password(payload: PasswordSetup, request: Request) -> dict[str, Any]:
        session = auth(request, mutation=True)
        try:
            store().enforce_rate_limit(
                f"password-setup:{session.user_id}", limit=6, window=3600
            )
            claims = google_claims(payload.id_token, request, payload.client)
            revoked = store().setup_password(
                session.user_id,
                session.session_id,
                claims,
                payload.new_password,
            )
            return {
                "changed": True,
                "created": True,
                "revoked_other_sessions": revoked,
                "login_methods": store().login_methods(session.user_id),
                "message": "邮箱密码登录已启用，其他设备的旧登录状态已安全退出",
            }
        except AccountError as exc:
            raise error(exc) from exc
        except HTTPException:
            raise
        except RECOVERABLE_INTEGRATION_ERRORS as exc:
            raise HTTPException(status_code=401, detail="Google 登录凭据无效") from exc

    @router.get("/me/avatar")
    def get_avatar(request: Request) -> FileResponse:
        session = auth(request)
        path = avatar_path(session.user_id)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="尚未上传头像")
        return FileResponse(
            path,
            media_type="image/png",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": 'inline; filename="avatar.png"',
            },
        )

    @router.get("/users/{user_id}/avatar")
    def get_public_avatar(user_id: UUID4) -> FileResponse:
        canonical_user_id = str(user_id)
        try:
            store().active_avatar_version(canonical_user_id)
        except AccountError as exc:
            raise error(exc) from exc
        path = avatar_path(canonical_user_id)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="用户尚未上传头像")
        return FileResponse(
            path,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=604800, immutable",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": 'inline; filename="avatar.png"',
            },
        )

    @router.post("/me/avatar", status_code=201)
    async def upload_avatar(
        request: Request, file: UploadFile = File(...)
    ) -> dict[str, Any]:
        session = auth(request, mutation=True)
        image = await decode_avatar(file)
        directory = settings.user_avatar_root / session.user_id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        target = avatar_path(session.user_id)
        temporary = directory / f".avatar-{secrets.token_hex(8)}.tmp"
        try:
            with temporary.open("xb") as handle:
                image.save(handle, format="PNG", optimize=True)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
            image.close()
        version = store().bump_avatar_version(session.user_id)
        return {
            "avatar_url": f"/api/v1/me/avatar?v={version}",
            "message": "头像已更新",
        }

    @router.delete("/me/avatar", status_code=204)
    def remove_avatar(request: Request) -> Response:
        session = auth(request, mutation=True)
        path = avatar_path(session.user_id)
        path.unlink(missing_ok=True)
        store().bump_avatar_version(session.user_id)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return Response(status_code=204)

    @router.get("/me/reading-level")
    def reading_level(request: Request) -> dict[str, Any]:
        return store().reading_summary(auth(request).user_id)

    @router.post("/me/reading-heartbeat")
    def reading_heartbeat(
        payload: ReadingHeartbeat, request: Request
    ) -> dict[str, Any]:
        session = auth(request, mutation=True)
        try:
            repository_provider().get_book(payload.book_id)
        except RECOVERABLE_INTEGRATION_ERRORS as exc:
            raise HTTPException(status_code=404, detail="作品不存在") from exc
        return store().accept_reading_heartbeat(
            session.user_id,
            event_id=str(payload.event_id),
            book_id=payload.book_id,
            claimed_seconds=payload.active_seconds,
        )

    @router.get("/books/{book_id}/recommendation")
    def recommendation_status(
        book_id: str, request: Request, response: Response
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie, Authorization"
        session = auth(request)
        if not PUBLIC_BOOK_ID.fullmatch(book_id):
            raise HTTPException(status_code=404, detail="作品不存在")
        repository = repository_provider()
        try:
            repository.get_book(book_id)
        except RECOVERABLE_INTEGRATION_ERRORS as exc:
            raise HTTPException(status_code=404, detail="作品不存在") from exc
        status = store().recommendation_status(session.user_id, book_id)
        pending_event_ids = store().pending_recommendation_events(
            session.user_id, book_id
        )
        sync_pending = bool(pending_event_ids)
        for event_id in pending_event_ids:
            try:
                _metrics, _still_pending = sync_recommendation_metric(
                    session, book_id, event_id
                )
            except RECOVERABLE_INTEGRATION_ERRORS:
                sync_pending = True
                break
        else:
            sync_pending = False
        if not sync_pending:
            status["metric_applied"] = True
            status["pending_count"] = 0
        return status | {"sync_pending": sync_pending}

    @router.post("/books/{book_id}/recommend")
    def donate_book_recommendation(
        book_id: str,
        payload: RecommendationDonation,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie, Authorization"
        session = auth(request, mutation=True)
        if not PUBLIC_BOOK_ID.fullmatch(book_id):
            raise HTTPException(status_code=404, detail="作品不存在")
        repository = repository_provider()
        try:
            repository.get_book(book_id)
        except RECOVERABLE_INTEGRATION_ERRORS as exc:
            raise HTTPException(status_code=404, detail="作品不存在") from exc
        try:
            receipt = store().donate_recommendation(
                session.user_id, book_id, str(payload.event_id)
            )
        except AccountError as exc:
            raise error(exc) from exc

        sync_pending = bool(not receipt["metric_applied"])
        try:
            if sync_pending:
                metrics, sync_pending = sync_recommendation_metric(
                    session, book_id, str(receipt["event_id"])
                )
            else:
                metrics = repository.public_metrics(book_id)
        except RECOVERABLE_INTEGRATION_ERRORS:
            sync_pending = True
            try:
                metrics = repository.public_metrics(book_id)
            except RECOVERABLE_INTEGRATION_ERRORS:
                metrics = {"public_id": book_id, "recommend_count": 0}

        new_donation = bool(receipt["new_donation"])
        if sync_pending and new_donation:
            message = "已捐赠 1 小时阅读经验时长，助力正在同步到推荐榜"
        elif new_donation:
            message = "助力推荐已送达，已捐赠 1 小时阅读经验时长"
        else:
            message = "本次助力已记录，不会重复扣除阅读经验时长"
        return metrics | {
            "recommended": True,
            "new_donation": new_donation,
            "replayed_event": not new_donation,
            "boost_count": int(receipt["boost_count"]),
            "donated_seconds": int(receipt["donated_seconds"]),
            "sync_pending": sync_pending,
            "reading": store().reading_summary(session.user_id),
            "message": message,
        }

    @router.get("/books/{book_id}/comments")
    def book_comments(
        book_id: str, request: Request, response: Response
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie, Authorization"
        viewer = optional_auth(request)
        backend = required_comments()
        try:
            rows = backend.book_comments(book_id, viewer.user_id if viewer else None)
        except AccountError as exc:
            raise error(exc) from exc
        enriched = enrich_comments(rows, viewer.user_id if viewer else None)
        return {
            "book_id": book_id,
            "authenticated": viewer is not None,
            "comments": enriched,
            "comment_count": len(enriched),
        }

    @router.post("/books/{book_id}/comments", status_code=201)
    def create_book_comment(
        book_id: str, payload: BookCommentCreate, request: Request
    ) -> dict[str, Any]:
        session = auth(request, mutation=True)
        moderation = moderate_comment(payload.content)
        if not moderation.allowed:
            raise HTTPException(status_code=422, detail=moderation.detail)
        backend = required_comments()
        try:
            store().enforce_rate_limit(
                f"book-comment:{session.user_id}:{request_ip(request)}",
                limit=8,
                window=60,
            )
            comment_id = backend.create(
                session.user_id,
                book_id=book_id,
                scope="book",
                content=payload.content,
            )
            rows = backend.book_comments(book_id, session.user_id)
            enriched = enrich_comments(rows, session.user_id)
            return {
                "book_id": book_id,
                "authenticated": True,
                "comments": enriched,
                "comment_count": len(enriched),
                "created_comment_id": comment_id,
            }
        except AccountError as exc:
            raise error(exc) from exc

    @router.get("/books/{book_id}/chapters/{chapter_id}/comments")
    def chapter_comments(
        book_id: str, chapter_id: int, request: Request, response: Response
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie, Authorization"
        _chapter, paragraphs = paragraph_context(book_id, chapter_id)
        viewer = optional_auth(request)
        backend = required_comments()
        try:
            rows = backend.paragraph_comments(
                book_id,
                chapter_id,
                [str(item["key"]) for item in paragraphs],
                viewer.user_id if viewer else None,
            )
        except AccountError as exc:
            raise error(exc) from exc
        result = paragraph_comment_response(rows, viewer.user_id if viewer else None)
        return result | {
            "book_id": book_id,
            "chapter_id": int(chapter_id),
            "authenticated": viewer is not None,
        }

    @router.post("/books/{book_id}/chapters/{chapter_id}/comments", status_code=201)
    def create_chapter_comment(
        book_id: str,
        chapter_id: int,
        payload: ParagraphCommentCreate,
        request: Request,
    ) -> dict[str, Any]:
        session = auth(request, mutation=True)
        _chapter, paragraphs = paragraph_context(book_id, chapter_id)
        if payload.paragraph_index >= len(paragraphs):
            raise HTTPException(status_code=422, detail="所选段落不存在或已更新")
        moderation = moderate_comment(payload.content)
        if not moderation.allowed:
            raise HTTPException(status_code=422, detail=moderation.detail)
        try:
            store().enforce_rate_limit(
                f"paragraph-comment:{session.user_id}:{request_ip(request)}",
                limit=8,
                window=60,
            )
            paragraph = paragraphs[payload.paragraph_index]
            backend = required_comments()
            comment_id = backend.create(
                session.user_id,
                book_id=book_id,
                scope="paragraph",
                chapter_id=chapter_id,
                paragraph_index=payload.paragraph_index,
                paragraph_key=str(paragraph["key"]),
                paragraph_excerpt=str(paragraph["excerpt"]),
                content=payload.content,
            )
            rows = backend.paragraph_comments(
                book_id,
                chapter_id,
                [str(item["key"]) for item in paragraphs],
                session.user_id,
            )
            result = paragraph_comment_response(rows, session.user_id)
            return result | {"created_comment_id": comment_id}
        except AccountError as exc:
            raise error(exc) from exc

    @router.post("/paragraph-comments/{comment_id}/likes")
    def like_paragraph_comment(comment_id: UUID4, request: Request) -> dict[str, Any]:
        session = auth(request, mutation=True)
        try:
            store().enforce_rate_limit(
                f"paragraph-like:{session.user_id}:{request_ip(request)}",
                limit=40,
                window=60,
            )
            return required_comments().adjust_like(session.user_id, str(comment_id), 1)
        except AccountError as exc:
            raise error(exc) from exc

    @router.post("/paragraph-comments/{comment_id}/thanks")
    def thank_paragraph_comment(comment_id: UUID4, request: Request) -> dict[str, Any]:
        session = auth(request, mutation=True)
        try:
            store().enforce_rate_limit(
                f"paragraph-thank:{session.user_id}:{request_ip(request)}",
                limit=40,
                window=60,
            )
            return required_comments().adjust_like(session.user_id, str(comment_id), 1)
        except AccountError as exc:
            raise error(exc) from exc

    @router.delete("/paragraph-comments/{comment_id}/thanks")
    def unthank_paragraph_comment(
        comment_id: UUID4, request: Request
    ) -> dict[str, Any]:
        session = auth(request, mutation=True)
        try:
            return required_comments().adjust_like(session.user_id, str(comment_id), -1)
        except AccountError as exc:
            raise error(exc) from exc

    @router.post("/comments/{comment_id}/likes")
    def like_comment(comment_id: UUID4, request: Request) -> dict[str, Any]:
        session = auth(request, mutation=True)
        backend = required_comments()
        try:
            store().enforce_rate_limit(
                f"comment-like:{session.user_id}:{request_ip(request)}",
                limit=40,
                window=60,
            )
            return backend.adjust_like(session.user_id, str(comment_id), 1)
        except AccountError as exc:
            raise error(exc) from exc

    @router.get("/me/state")
    def user_state(request: Request) -> dict[str, Any]:
        return authoritative_state(auth(request).user_id)

    @router.put("/me/state")
    def sync_state(payload: StateSync, request: Request) -> dict[str, Any]:
        session = auth(request, mutation=True)
        data = payload.model_dump()
        requested_ids = list(
            dict.fromkeys(
                item["book_id"] for values in data.values() for item in values
            )
        )
        repository = repository_provider()
        if hasattr(repository, "account_state_books"):
            available = repository.account_state_books(requested_ids)
            if any(book_id not in available for book_id in requested_ids):
                raise HTTPException(status_code=404, detail="同步数据包含不存在的作品")
        else:
            for book_id in requested_ids:
                try:
                    repository.get_book(book_id)
                except RECOVERABLE_INTEGRATION_ERRORS as exc:
                    raise HTTPException(
                        status_code=404, detail="同步数据包含不存在的作品"
                    ) from exc
        try:
            store().sync_state(session.user_id, data)
            sync_favorite_metrics(
                [item["book_id"] for item in data.get("favorites", [])]
            )
            return authoritative_state(
                session.user_id,
                available if hasattr(repository, "account_state_books") else None,
            )
        except AccountError as exc:
            raise error(exc) from exc

    @router.delete("/me/state/{kind}/{book_id}", status_code=204)
    def remove_state(kind: str, book_id: str, request: Request) -> Response:
        session = auth(request, mutation=True)
        if not PUBLIC_BOOK_ID.fullmatch(book_id):
            raise HTTPException(status_code=404, detail="作品不存在")
        try:
            remaining_favorites = store().remove_state_item(
                session.user_id, kind, book_id
            )
            if kind == "favorites":
                repository = repository_provider()
                if hasattr(repository, "set_favorite_count"):
                    repository.set_favorite_count(
                        book_id, int(remaining_favorites or 0)
                    )
                    if on_public_metrics_changed is not None:
                        on_public_metrics_changed()
        except AccountError as exc:
            raise error(exc) from exc
        return Response(status_code=204)

    @router.get("/me/uploads")
    def upload_history(request: Request) -> dict[str, Any]:
        session = auth(request)
        reconcile_results(store(), settings, user_id=session.user_id)
        return {"items": store().uploads(session.user_id)}

    @router.get("/me/notifications")
    def notification_history(
        request: Request, limit: int = 100, page: int = 1
    ) -> dict[str, Any]:
        session = auth(request)
        reconcile_results(store(), settings, user_id=session.user_id)
        page_size = min(max(int(limit), 1), 200)
        current_page = min(max(int(page), 1), 10_000)
        result = store().notifications(
            session.user_id,
            limit=page_size,
            offset=(current_page - 1) * page_size,
        )
        total = int(result.get("total_count") or 0)
        return {
            **result,
            "page": current_page,
            "page_size": page_size,
            "page_count": max(1, (total + page_size - 1) // page_size),
        }

    @router.post("/me/notifications/read")
    def mark_all_notifications_read(request: Request) -> dict[str, Any]:
        session = auth(request, mutation=True)
        return {"updated": store().mark_notification_read(session.user_id)}

    @router.post("/me/notifications/{notification_id}/read")
    def mark_one_notification_read(
        notification_id: UUID4, request: Request
    ) -> dict[str, Any]:
        session = auth(request, mutation=True)
        return {
            "updated": store().mark_notification_read(
                session.user_id, str(notification_id)
            )
        }

    @router.post("/me/uploads", status_code=201)
    async def upload_deconstruction_source(
        request: Request,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        session = auth(request, mutation=True)
        if not session.email_verified:
            raise HTTPException(
                status_code=403, detail="验证邮箱或使用 Google 登录后才能上传"
            )
        try:
            store().enforce_submission_quota(
                session.user_id,
                max_files=settings.upload_daily_files,
                max_bytes=settings.upload_daily_bytes,
            )
        except AccountError as exc:
            raise error(exc) from exc
        original = Path(str(file.filename or "")).name
        suffix = Path(original).suffix.casefold()
        if not original or len(original) > 180 or suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise HTTPException(
                status_code=415, detail="请上传 oh-story-claudecode 结构的 ZIP 文件"
            )
        upload_id = store().create_upload(
            session.user_id, original, file.content_type or ""
        )
        directory = settings.user_upload_root / session.user_id / upload_id
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        target = directory / "source.zip"
        size = 0
        try:
            with target.open("xb") as handle:
                target.chmod(0o600)
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise UploadSecurityError("文件超过上传大小限制")
                    handle.write(chunk)
            store().receive_upload(
                upload_id,
                session.user_id,
                stored_filename=str(target.relative_to(settings.user_upload_root)),
                size=size,
            )
        except (UploadSecurityError, AccountError, OSError) as exc:
            shutil.rmtree(directory, ignore_errors=True)
            store().reject_upload(upload_id, session.user_id, str(exc))
            if isinstance(exc, AccountError):
                raise error(exc) from exc
            status_code = 422 if isinstance(exc, UploadSecurityError) else 503
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        finally:
            await file.close()
        account_store = store()
        account_store.create_notification(
            session.user_id,
            kind="submission_received",
            title="拆书文上传成功",
            message=f"“{original}”上传成功，正在等待审核。",
            action_url="#/account/submissions",
            resource_type="deconstruction",
            resource_id=upload_id,
            dedupe_key=f"received:deconstruction:{upload_id}",
        )
        background_tasks.add_task(
            inspect_upload_once,
            settings,
            account_store,
            upload_id=upload_id,
        )
        return {
            "id": upload_id,
            "status": "quarantined",
            "bytes": size,
            "original_filename": original,
            "message": "上传成功，正在等待审核",
        }

    @router.get("/me/novel-submissions")
    def novel_submission_history(request: Request) -> dict[str, Any]:
        session = auth(request)
        reconcile_results(store(), settings, user_id=session.user_id)
        return {"items": store().novel_submissions(session.user_id)}

    @router.post("/me/novel-submissions", status_code=201)
    async def submit_novel(
        request: Request,
        metadata: str = Form(...),
        manuscript: UploadFile = File(...),
        cover: UploadFile = File(...),
    ) -> dict[str, Any]:
        session = auth(request, mutation=True)
        if not session.email_verified:
            raise HTTPException(
                status_code=403, detail="验证邮箱或使用 Google 登录后才能投稿"
            )
        try:
            parsed = NovelMetadata.model_validate_json(metadata).model_dump()
            allowed_categories = {
                str(item.get("source_name") or item.get("name") or "").strip()
                for item in (
                    category_provider()
                    if category_provider is not None
                    else repository_provider().categories()
                )
                if str(item.get("name") or "").strip()
            }
            if parsed["category"] not in allowed_categories:
                raise HTTPException(status_code=422, detail="请选择当前书库已有分类")
            store().enforce_submission_quota(
                session.user_id,
                max_files=settings.upload_daily_files,
                max_bytes=settings.upload_daily_bytes,
            )
        except HTTPException:
            raise
        except AccountError as exc:
            raise error(exc) from exc
        except RECOVERABLE_INTEGRATION_ERRORS as exc:
            raise HTTPException(
                status_code=422, detail="投稿资料字段不完整或格式无效"
            ) from exc
        original = Path(str(manuscript.filename or "")).name
        suffix = Path(original).suffix.casefold()
        if not original or len(original) > 180 or suffix not in ALLOWED_NOVEL_SUFFIXES:
            raise HTTPException(status_code=415, detail="小说正文仅支持 TXT 或 EPUB")
        submission_id = store().create_novel_submission(
            session.user_id, parsed, original
        )
        directory = settings.user_upload_root / session.user_id / submission_id
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        source_path = directory / f"manuscript{suffix}"
        cover_path = directory / "cover.png"
        raw_cover_path = directory / ".cover-upload"
        size = 0
        try:
            with source_path.open("xb") as handle:
                source_path.chmod(0o600)
                while chunk := await manuscript.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise UploadSecurityError("正文超过上传大小限制")
                    handle.write(chunk)
            scanner = await asyncio.to_thread(
                UploadSecurityScanner().scan,
                source_path,
                suffix=suffix,
                max_bytes=settings.max_upload_bytes,
            )
            raw_cover = await cover.read(settings.max_avatar_bytes + 1)
            if len(raw_cover) > settings.max_avatar_bytes:
                raise UploadSecurityError("封面超过上传大小限制")
            raw_cover_path.write_bytes(raw_cover)
            raw_cover_path.chmod(0o600)
            cover_scan = await asyncio.to_thread(
                UploadSecurityScanner().scan_binary,
                raw_cover_path,
                max_bytes=settings.max_avatar_bytes,
            )
            decoded = decode_image_bytes(raw_cover)
            decoded.convert("RGB").save(cover_path, "PNG", optimize=True)
            cover_path.chmod(0o600)
            raw_cover_path.unlink(missing_ok=True)
            scanner["cover"] = cover_scan
            store().finish_novel_submission(
                submission_id,
                session.user_id,
                manuscript_path=str(source_path.relative_to(settings.user_upload_root)),
                cover_path=str(cover_path.relative_to(settings.user_upload_root)),
                size=size,
                digest=str(scanner["sha256"]),
                scanner=scanner,
            )
        except (UploadSecurityError, AccountError, HTTPException) as exc:
            shutil.rmtree(directory, ignore_errors=True)
            reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
            store().reject_novel_submission(submission_id, session.user_id, reason)
            if isinstance(exc, HTTPException):
                raise
            if isinstance(exc, AccountError):
                raise error(exc) from exc
            raise HTTPException(status_code=422, detail=reason) from exc
        finally:
            raw_cover_path.unlink(missing_ok=True)
            await manuscript.close()
            await cover.close()
        return {
            "id": submission_id,
            "status": "ai_pending",
            "message": "投稿已安全入队，将审核正文完整性、内容与授权说明",
        }

    @router.get("/me/deconstructions/{slug}/download")
    def download_deconstruction(
        slug: str,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> FileResponse:
        auth(request)
        allowed = {
            str(item.get("slug"))
            for item in repository_provider().list_deconstructions()
        }
        if slug not in allowed:
            raise HTTPException(status_code=404, detail="拆书档案不存在")
        root = (settings.deconstruction_root / slug).resolve()
        if root.parent != settings.deconstruction_root.resolve() or not root.is_dir():
            raise HTTPException(status_code=404, detail="拆书档案不存在")
        handle, archive_name = tempfile.mkstemp(
            prefix="oohstory-deconstruction-", suffix=".zip"
        )
        os.close(handle)
        try:
            with zipfile.ZipFile(
                archive_name, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                total = 0
                count = 0
                for candidate in sorted(path for path in root.rglob("*") if path.is_file()):
                    resolved = candidate.resolve()
                    relative = resolved.relative_to(root) if resolved.is_relative_to(root) else None
                    if (
                        candidate.is_symlink()
                        or relative is None
                        or candidate.name == "_submission.json"
                        or any(part.startswith(".") for part in relative.parts)
                    ):
                        continue
                    count += 1
                    total += resolved.stat().st_size
                    if count > 2000 or total > 256 * 1024 * 1024:
                        raise HTTPException(
                            status_code=413, detail="拆书档案超过下载安全上限"
                        )
                    archive.write(resolved, relative)
            background_tasks.add_task(Path(archive_name).unlink, missing_ok=True)
            return FileResponse(
                archive_name,
                filename=f"{slug}.zip",
                media_type="application/zip",
                headers={
                    "Cache-Control": "private, no-store",
                    "X-Download-Options": "noopen",
                },
            )
        except BaseException:
            Path(archive_name).unlink(missing_ok=True)
            raise

    @router.get("/deconstructions/{slug}/likes")
    def deconstruction_likes(slug: str, request: Request) -> dict[str, Any]:
        allowed = {
            str(item.get("slug"))
            for item in repository_provider().list_deconstructions()
        }
        if slug not in allowed:
            raise HTTPException(status_code=404, detail="拆书档案不存在")
        session = optional_auth(request)
        return store().deconstruction_engagement(
            [slug], viewer_user_id=session.user_id if session else None
        )[slug]

    @router.post("/deconstructions/{slug}/likes")
    def toggle_deconstruction_like(slug: str, request: Request) -> dict[str, Any]:
        session = auth(request, mutation=True)
        allowed = {
            str(item.get("slug"))
            for item in repository_provider().list_deconstructions()
        }
        if slug not in allowed:
            raise HTTPException(status_code=404, detail="拆书档案不存在")
        return store().toggle_deconstruction_like(session.user_id, slug)

    setattr(router, "google_redirect_handler", google_redirect)
    return router
