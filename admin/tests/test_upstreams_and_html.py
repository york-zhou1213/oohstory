from __future__ import annotations

import socket

from fastapi.testclient import TestClient

from oohstory_admin.clients import JsonHttpClient, UpstreamUnavailable
from conftest import login


def test_proxy_timeout_becomes_unavailable(monkeypatch):
    def timeout(*args, **kwargs):
        raise socket.timeout("slow")

    monkeypatch.setattr("urllib.request.urlopen", timeout)
    client = JsonHttpClient("http://127.0.0.1:8091", timeout=0.1)
    result = client.optional("/healthz")
    assert not result.available
    assert "不可用" in result.error or "超时" in result.error


def test_books_upstream_unavailable_is_honest(settings, components):
    from oohstory_admin.app import create_app

    reader, library, systemd, audit = components

    def unavailable(*args, **kwargs):
        raise UpstreamUnavailable("reader timed out")

    reader.books = unavailable
    with TestClient(create_app(settings, reader=reader, library=library, systemd=systemd, audit=audit)) as client:
        assert login(client).status_code == 303
        page = client.get("/admin/books")
        assert page.status_code == 200
        assert "Reader API 不可用" in page.text
        assert client.get("/api/admin/books").status_code == 503


def test_dashboard_and_books_html_smoke(client):
    assert login(client).status_code == 303
    dashboard = client.get("/admin/")
    assert dashboard.status_code == 200
    assert "系统概览" in dashboard.text
    assert "OOHStory 阅读服务" in dashboard.text
    books = client.get("/admin/books?q=测试")
    assert books.status_code == 200
    assert "测试故事" in books.text
    detail = client.get("/admin/books/book_1")
    assert detail.status_code == 200
    assert "章节数" in detail.text
    assert "3" in detail.text
    for response in (dashboard, books, detail):
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store, no-transform"


def test_full_library_workspace_and_legacy_jobs_redirect(client):
    assert login(client).status_code == 303
    page = client.get("/admin/library")
    assert page.status_code == 200
    assert "LOCAL STORY INTELLIGENCE" in page.text
    assert "来源同步与正文管道" in page.text
    assert "作品、作者与馆藏检索" in page.text
    assert "封面同步与重绘" in page.text
    assert "同步调度" in page.text
    assert "拆书与索引" in page.text
    assert 'aria-current="page" href="/admin/library"' in page.text

    legacy = client.get("/admin/jobs", follow_redirects=False)
    assert legacy.status_code == 308
    assert legacy.headers["location"] == "/admin/library"

    payload = client.get("/api/admin/library")
    assert payload.status_code == 200
    assert "metrics" in payload.json()


def test_mobile_form_controls_prevent_focus_zoom():
    from pathlib import Path

    styles = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "oohstory_admin"
        / "static"
        / "admin.css"
    ).read_text(encoding="utf-8")

    assert "input, select { min-width: 0; max-width: 100%; font-size: 16px; }" in styles


def test_base_path_root_redirect(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin"


def test_empty_base_path_supports_local_root(settings, components):
    from dataclasses import replace
    from oohstory_admin.app import create_app

    reader, library, systemd, audit = components
    local_settings = replace(settings, base_path="")
    with TestClient(create_app(local_settings, reader=reader, library=library, systemd=systemd, audit=audit)) as local:
        assert local.get("/admin/login").status_code == 404  # /admin is intentionally absent.
        page = local.get("/login")
        marker = 'name="csrf_token" value="'
        token = page.text.split(marker, 1)[1].split('"', 1)[0]
        response = local.post(
            "/login",
            data={"csrf_token": token, "username": "operator", "password": "correct horse battery staple"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert local.get("/").status_code == 200
