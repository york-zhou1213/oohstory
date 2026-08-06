from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oohstory_admin.audit import AuditLog
from oohstory_admin.clients import UpstreamResult
from oohstory_admin.config import Settings
from oohstory_admin.security import hash_password
from oohstory_admin.systemd import ActionResult


class FakeReader:
    def health(self):
        return UpstreamResult(True, {"status": "ok"})

    def home(self):
        return UpstreamResult(
            True,
            {"stats": {"catalog_total": 10, "readable_total": 8, "category_total": 2, "deconstruction_total": 1}},
        )

    def books(self, query="", category="", page=1, page_size=24, sort="recent"):
        return {
            "items": [
                {
                    "public_id": "book_1",
                    "title": "测试故事",
                    "author": "作者",
                    "category": "科幻灵异",
                    "serialization_status": "finished",
                    "approx_word_count": 12345,
                    "approx_chapter_count": 3,
                    "summary": "真实上游书籍摘要",
                }
            ],
            "total": 1,
            "page": page,
            "page_size": page_size,
            "page_count": 1,
            "query": query,
            "category": category,
        }

    def book(self, public_id):
        return {
            "public_id": public_id,
            "title": "测试故事",
            "author": "作者",
            "category": "科幻灵异",
            "source_bytes": 1000,
            "approx_word_count": 300,
            "summary": "简介",
        }

    def chapters(self, public_id):
        return {"book": {"public_id": public_id}, "chapter_count": 3, "chapters": []}

    def metrics(self, public_id):
        return {"public_id": public_id, "read_count": 4, "download_count": 2}


class FakeLibrary:
    def statuses(self):
        return {
            "health": {"available": True, "data": {"status": "healthy"}, "error": None, "endpoint": "/api/health"},
            "book_index": {
                "available": True,
                "data": {"status": "completed", "running": False, "processed": 8, "total": 8, "indexed": 2},
                "error": None,
                "endpoint": "/api/library/index/status",
            },
        }


class FakeSystemd:
    def __init__(self):
        self.calls = []

    def statuses(self):
        return [
            {
                "unit": "oohstory-reader.service",
                "label": "OOHStory 阅读服务",
                "available": True,
                "active": "active",
                "sub": "running",
                "enabled": "enabled",
                "pid": "123",
                "memory_bytes": 4096,
                "restarts": "0",
            }
        ]

    def action(self, action, target):
        self.calls.append((action, target))
        return ActionResult(True, action, target, "操作成功", ("systemctl", action, target))


@pytest.fixture(scope="session")
def password_hash():
    return hash_password("correct horse battery staple")


@pytest.fixture
def settings(tmp_path: Path, password_hash: str):
    return Settings(
        admin_username="operator",
        password_hash=password_hash,
        session_secret="test-session-secret-with-at-least-32-bytes",
        cookie_secure=False,
        database_path=tmp_path / "audit.db",
        library_upload_dir=tmp_path / "cover-uploads",
        base_path="/admin",
        login_attempts=3,
        login_window_seconds=300,
        allowed_hosts=("testserver", "127.0.0.1", "localhost"),
    )


@pytest.fixture
def components(tmp_path: Path):
    return FakeReader(), FakeLibrary(), FakeSystemd(), AuditLog(tmp_path / "audit-component.db")


@pytest.fixture
def client(settings, components):
    from oohstory_admin.app import create_app

    reader, library, systemd, audit = components
    app = create_app(settings, reader=reader, library=library, systemd=systemd, audit=audit)
    with TestClient(app) as test_client:
        test_client.fake_systemd = systemd
        test_client.audit_log = audit
        yield test_client


def login(client: TestClient, username="operator", password="correct horse battery staple"):
    page = client.get("/admin/login")
    marker = 'name="csrf_token" value="'
    token = page.text.split(marker, 1)[1].split('"', 1)[0]
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "username": username, "password": password},
        follow_redirects=False,
    )
