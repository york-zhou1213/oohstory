"""Role-protected operational APIs for the OOHStory reader admin console."""

from __future__ import annotations

import asyncio
from io import BytesIO
import json
from pathlib import Path
import shutil
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from .accounts import ALLOWED_NOVEL_SUFFIXES, AccountError, AccountStore, SessionContext
from .category_management import CategoryManager
from .review_worker import reconcile_results
from .settings import Settings
from .submissions import NovelMetadata
from .upload_security import UploadSecurityError, UploadSecurityScanner


class InviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="", max_length=80)
    max_uses: int = Field(default=1, ge=1, le=100_000)
    expires_in_days: int = Field(default=30, ge=1, le=365)


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=240)
    sort_order: int = Field(default=100, ge=0, le=10_000)


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=240)
    enabled: bool = True
    sort_order: int = Field(default=100, ge=0, le=10_000)


def create_admin_router(
    settings: Settings,
    store_provider: Callable[[], AccountStore],
    repository_provider: Callable[[], Any],
    category_manager: CategoryManager,
    on_catalog_changed: Callable[[], None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin")

    def error(exc: AccountError) -> HTTPException:
        return HTTPException(status_code=exc.status_code, detail=exc.detail)

    def raw_session_token(request: Request) -> tuple[str, bool]:
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            return authorization[7:].strip(), False
        return request.cookies.get(settings.session_cookie, ""), True

    def admin(request: Request, *, mutation: bool = False, owner: bool = False) -> SessionContext:
        raw, cookie_auth = raw_session_token(request)
        session = store_provider().session(raw)
        if session is None:
            raise HTTPException(status_code=401, detail="请先登录")
        if session.role not in {"admin", "owner"} or (owner and session.role != "owner"):
            raise HTTPException(status_code=403, detail="需要管理员权限")
        if mutation and cookie_auth:
            try:
                store_provider().require_csrf(session, request.headers.get("x-csrf-token", ""))
            except AccountError as exc:
                raise error(exc) from exc
        return session

    def audit(
        session: SessionContext,
        action: str,
        resource_type: str,
        resource_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        store_provider().record_admin_audit(
            session.user_id, action, resource_type, resource_id, detail
        )

    @router.get("/summary")
    def summary(request: Request) -> dict[str, Any]:
        session = admin(request)
        return {
            "role": session.role,
            "summary": store_provider().admin_summary(),
            "categories": len(category_manager.items(include_disabled=True)),
        }

    @router.get("/invites")
    def invites(request: Request) -> dict[str, Any]:
        admin(request)
        return {"items": store_provider().list_invites()}

    @router.post("/invites", status_code=201)
    def create_invite(payload: InviteCreate, request: Request) -> dict[str, Any]:
        session = admin(request, mutation=True)
        try:
            code, item = store_provider().create_invite(**payload.model_dump())
            audit(session, "invite.create", "invite", item["id"], {
                "label": item["label"], "max_uses": item["max_uses"],
                "expires_at": item["expires_at"],
            })
            return {"code": code, "item": item, "message": "邀请码已生成；完整代码仅在本次显示"}
        except AccountError as exc:
            raise error(exc) from exc

    @router.delete("/invites/{invite_id}", status_code=204)
    def revoke_invite(invite_id: str, request: Request) -> None:
        session = admin(request, mutation=True)
        try:
            store_provider().revoke_invite(invite_id)
            audit(session, "invite.revoke", "invite", invite_id)
        except AccountError as exc:
            raise error(exc) from exc

    @router.get("/users")
    def users(
        request: Request,
        q: str = Query("", max_length=100),
        status: str = Query("", pattern="^(|active|disabled)$"),
        page: int = Query(1, ge=1, le=100_000),
        page_size: int = Query(20, ge=1, le=100),
    ) -> dict[str, Any]:
        admin(request)
        return store_provider().admin_users(
            query=q, status=status, page=page, page_size=page_size
        )

    @router.patch("/users/{user_id}")
    def update_user(user_id: str, payload: AdminUserUpdate, request: Request) -> dict[str, Any]:
        session = admin(request, mutation=True)
        try:
            item = store_provider().admin_update_user(
                session.user_id, user_id, status=payload.status
            )
            audit(session, "user.update", "user", user_id, {
                "status": payload.status,
            })
            return {"item": item, "message": "注册用户状态已更新"}
        except AccountError as exc:
            raise error(exc) from exc

    @router.get("/categories")
    def categories(request: Request) -> dict[str, Any]:
        admin(request)
        return {"items": category_manager.items(include_disabled=True)}

    @router.post("/categories", status_code=201)
    def create_category(payload: CategoryCreate, request: Request) -> dict[str, Any]:
        session = admin(request, mutation=True)
        try:
            item = store_provider().create_managed_category(**payload.model_dump())
            if on_catalog_changed is not None:
                on_catalog_changed()
            audit(session, "category.create", "category", item["id"], {
                "source_name": item["source_name"], "display_name": item["display_name"],
            })
            return {"item": item, "message": "分类已创建并可用于后台上传"}
        except AccountError as exc:
            raise error(exc) from exc

    @router.put("/categories/{category_id}")
    def update_category(
        category_id: str, payload: CategoryUpdate, request: Request
    ) -> dict[str, Any]:
        session = admin(request, mutation=True)
        try:
            item = store_provider().update_managed_category(
                category_id, **payload.model_dump()
            )
            if on_catalog_changed is not None:
                on_catalog_changed()
            audit(session, "category.update", "category", category_id, payload.model_dump())
            return {"item": item, "message": "分类设置已保存"}
        except AccountError as exc:
            raise error(exc) from exc

    @router.delete("/categories/{category_id}", status_code=204)
    def delete_category(category_id: str, request: Request) -> None:
        session = admin(request, mutation=True)
        try:
            item = category_manager.delete(category_id)
            if on_catalog_changed is not None:
                on_catalog_changed()
            audit(session, "category.delete", "category", category_id, {
                "source_name": item["source_name"],
            })
        except AccountError as exc:
            raise error(exc) from exc

    @router.get("/novels")
    def novels(request: Request) -> dict[str, Any]:
        admin(request)
        reconcile_results(store_provider(), settings)
        return {"items": store_provider().admin_novel_submissions()}

    async def save_upload(file: UploadFile, target: Path, limit: int) -> int:
        size = 0
        with target.open("xb") as handle:
            target.chmod(0o600)
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise UploadSecurityError("文件超过上传大小限制")
                handle.write(chunk)
        return size

    def decode_cover(raw: bytes) -> Image.Image:
        if not raw:
            raise UploadSecurityError("封面文件为空")
        try:
            probe = Image.open(BytesIO(raw))
            if str(probe.format or "").upper() not in {"JPEG", "PNG", "WEBP"}:
                raise UploadSecurityError("封面仅支持 JPEG、PNG 或 WebP")
            width, height = probe.size
            if width < 64 or height < 64 or width * height > settings.max_avatar_pixels:
                raise UploadSecurityError("封面尺寸不符合安全要求")
            probe.verify()
            decoded = Image.open(BytesIO(raw))
            decoded.load()
        except UploadSecurityError:
            raise
        except (UnidentifiedImageError, OSError) as exc:
            raise UploadSecurityError("封面无法安全解码") from exc
        return ImageOps.exif_transpose(decoded).convert("RGB")

    @router.post("/novels", status_code=202)
    async def upload_novel(
        request: Request,
        metadata: str = Form(...),
        manuscript: UploadFile = File(...),
        cover: UploadFile = File(...),
    ) -> dict[str, Any]:
        session = admin(request, mutation=True)
        try:
            parsed = NovelMetadata.model_validate_json(metadata).model_dump()
            parsed["category"] = category_manager.resolve_source(parsed["category"])
        except AccountError as exc:
            raise error(exc) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail="小说资料字段不完整或格式无效") from exc
        original = Path(str(manuscript.filename or "")).name
        suffix = Path(original).suffix.casefold()
        if not original or len(original) > 180 or suffix not in ALLOWED_NOVEL_SUFFIXES:
            raise HTTPException(status_code=415, detail="小说正文仅支持 TXT 或 EPUB")
        store = store_provider()
        submission_id = store.create_novel_submission(session.user_id, parsed, original)
        directory = settings.user_upload_root / session.user_id / submission_id
        source_path = directory / f"manuscript{suffix}"
        raw_cover_path = directory / ".cover-upload"
        cover_path = directory / "cover.png"
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        size = 0
        try:
            size = await save_upload(manuscript, source_path, settings.max_upload_bytes)
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
            scanner["cover"] = await asyncio.to_thread(
                UploadSecurityScanner().scan_binary,
                raw_cover_path,
                max_bytes=settings.max_avatar_bytes,
            )
            decoded = decode_cover(raw_cover)
            decoded.save(cover_path, "PNG", optimize=True)
            cover_path.chmod(0o600)
            raw_cover_path.unlink(missing_ok=True)
            store.finish_novel_submission(
                submission_id,
                session.user_id,
                manuscript_path=str(source_path.relative_to(settings.user_upload_root)),
                cover_path=str(cover_path.relative_to(settings.user_upload_root)),
                size=size,
                digest=str(scanner["sha256"]),
                scanner=scanner,
            )
            store.mark_admin_novel_submission(submission_id, session.user_id)
            audit(session, "novel.upload", "novel_submission", submission_id, {
                "title": parsed["title"], "author": parsed["author"],
                "category": parsed["category"], "bytes": size,
            })
            if on_catalog_changed is not None:
                on_catalog_changed()
        except (UploadSecurityError, AccountError, HTTPException, OSError) as exc:
            shutil.rmtree(directory, ignore_errors=True)
            reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
            store.reject_novel_submission(submission_id, session.user_id, reason)
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
            "bytes": size,
            "message": "安全扫描已通过，小说已进入隔离复核与正式书库入库队列",
        }

    return router
