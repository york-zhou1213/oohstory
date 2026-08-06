from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.accounts import AccountStore
from app.admin_api import create_admin_router
from app.category_management import CategoryManager
from app.review_worker import review_once
from app.settings import Settings
from app.upload_security import UploadSecurityScanner


class FakeRepository:
    def categories(self):
        return [{"name": "科幻", "count": 2}, {"name": "悬疑", "count": 0}]


def admin_client(tmp_path: Path):
    settings = Settings(
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        allowed_hosts=("testserver",),
        account_database=tmp_path / "accounts.sqlite3",
        upload_root=tmp_path / "uploads",
        submission_handoff_root=tmp_path / "handoff",
    )
    first = AccountStore(settings.user_database_path, session_ttl_seconds=3600)
    user, verification = first.register(
        "owner@example.com", "AdminPass#123", "站长", ""
    )
    first.verify_email(verification)
    # Administrative access is provisioned explicitly for the test fixture;
    # registering a user must never grant it automatically.
    with sqlite3.connect(settings.user_database_path) as connection:
        connection.execute("UPDATE users SET role='owner' WHERE id=?", (user["id"],))
    store = AccountStore(settings.user_database_path, session_ttl_seconds=3600)
    logged_in = store.password_login("owner@example.com", "AdminPass#123")
    assert logged_in["role"] == "owner"
    session = store.create_session(logged_in, client="web")
    repository = FakeRepository()
    categories = CategoryManager(lambda: store, lambda: repository)
    app = FastAPI()
    app.include_router(create_admin_router(
        settings, lambda: store, lambda: repository, categories
    ))
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set(settings.session_cookie, session.token)
    client.headers.update({"X-CSRF-Token": session.csrf_token})
    return client, store, settings


def test_admin_invites_users_and_categories_are_persistent(tmp_path: Path) -> None:
    client, store, _settings = admin_client(tmp_path)
    created = client.post("/api/v1/admin/invites", json={
        "label": "内测", "max_uses": 3, "expires_in_days": 14,
    })
    assert created.status_code == 201
    assert created.json()["code"].startswith("OOH-")
    assert client.get("/api/v1/admin/invites").json()["items"][0]["max_uses"] == 3

    users = client.get("/api/v1/admin/users").json()
    assert users["total"] == 1
    assert users["items"][0]["role"] == "owner"

    ordinary, verification = store.register(
        "reader@example.com", "ReaderPass#123", "普通读者", ""
    )
    store.verify_email(verification)
    changed = client.patch(f"/api/v1/admin/users/{ordinary['id']}", json={"status": "disabled"})
    assert changed.status_code == 200
    assert changed.json()["item"]["status"] == "disabled"
    assert changed.json()["item"]["role"] == "user"
    rejected = client.patch(f"/api/v1/admin/users/{ordinary['id']}", json={
        "status": "active", "role": "admin",
    })
    assert rejected.status_code == 422

    category = client.post("/api/v1/admin/categories", json={
        "name": "都市异能", "description": "现代超凡题材", "sort_order": 15,
    })
    assert category.status_code == 201
    item = category.json()["item"]
    updated = client.put(f"/api/v1/admin/categories/{item['id']}", json={
        "display_name": "都市超凡", "description": "都市异能与超凡故事",
        "enabled": True, "sort_order": 12,
    })
    assert updated.json()["item"]["source_name"] == "都市异能"
    assert updated.json()["item"]["display_name"] == "都市超凡"
    assert store.admin_summary()["active_invites"] == 1


def test_admin_novel_upload_creates_verified_ingestion_handoff(
    tmp_path: Path, monkeypatch
) -> None:
    client, store, settings = admin_client(tmp_path)

    def scan(_self, path, *, suffix, max_bytes):
        data = Path(path).read_bytes()
        return {"status": "clean", "engine": "test", "bytes": len(data), "sha256": sha256(data).hexdigest()}

    def scan_binary(_self, path, *, max_bytes):
        data = Path(path).read_bytes()
        return {"status": "clean", "engine": "test", "bytes": len(data), "sha256": sha256(data).hexdigest()}

    monkeypatch.setattr(UploadSecurityScanner, "scan", scan)
    monkeypatch.setattr(UploadSecurityScanner, "scan_binary", scan_binary)
    cover = BytesIO()
    Image.new("RGB", (320, 480), "#315d8b").save(cover, "PNG")
    metadata = {
        "title": "星海管理员上传", "author": "作者甲", "category": "悬疑",
        "serialization_status": "finished", "summary": "这是一部用于验证后台正式上传与入库交接流程的完整测试小说。",
        "source": "作者原创", "authorization": "作者确认授权本站公开发布与在线阅读。",
    }
    response = client.post(
        "/api/v1/admin/novels",
        data={"metadata": json.dumps(metadata, ensure_ascii=False)},
        files={
            "manuscript": ("book.txt", ("第一章\n星海故事正式开始。" * 200).encode(), "text/plain"),
            "cover": ("cover.png", cover.getvalue(), "image/png"),
        },
    )
    assert response.status_code == 202, response.text
    submission_id = response.json()["id"]
    assert response.json()["status"] == "ai_pending"
    assert store.admin_novel_submissions()[0]["status"] == "ai_pending"
    reviewed = review_once(settings)
    assert reviewed == {"id": submission_id, "type": "novel", "status": "approved"}
    ready = settings.user_submission_handoff_root / submission_id / "ready.json"
    assert ready.is_file()
    manifest = json.loads(ready.read_text(encoding="utf-8"))
    assert manifest["review"]["decision"] == "approve"
    assert manifest["metadata"]["category"] == "悬疑"
    assert {item["path"] for item in manifest["files"]} == {"manuscript.txt", "cover.png"}
    assert store.admin_novel_submissions()[0]["status"] == "approved"


def test_nginx_keeps_reader_admin_surface_private() -> None:
    nginx = (Path(__file__).parents[1] / "deploy/nginx-oohstory.conf").read_text(encoding="utf-8")
    assert "/api/v1/admin/(?:invites|categories|novels)" not in nginx
    assert "PATCH:/api/v1/admin/users" not in nginx
    assert "location = /api/v1/admin/novels {" not in nginx
    assert "location ^~ /api/v1/admin/ { return 404; }" in nginx
