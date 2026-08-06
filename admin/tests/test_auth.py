from __future__ import annotations

from fastapi.testclient import TestClient

from oohstory_admin.audit import AuditLog
from oohstory_admin.config import Settings
from conftest import FakeLibrary, FakeReader, FakeSystemd, login


def test_default_deployment_is_loopback_http_compatible():
    settings = Settings()

    assert settings.cookie_secure is False
    assert settings.allowed_hosts == ("127.0.0.1", "localhost")


def test_no_default_login_is_disabled(tmp_path):
    from oohstory_admin.app import create_app

    settings = Settings(
        cookie_secure=False,
        database_path=tmp_path / "empty.db",
        base_path="/admin",
        allowed_hosts=("testserver",),
    )
    app = create_app(
        settings,
        reader=FakeReader(),
        library=FakeLibrary(),
        systemd=FakeSystemd(),
        audit=AuditLog(tmp_path / "empty-audit.db"),
    )
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 503
        response = login(client, username="admin", password="admin-password")
        assert response.status_code == 503
        assert settings.session_cookie not in response.cookies


def test_login_session_logout_and_cookie_security(client):
    assert client.get("/admin/").status_code == 200  # TestClient followed login redirect.
    response = login(client)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert client.get("/admin/").status_code == 200
    session = client.get("/api/admin/session")
    assert session.status_code == 200
    csrf = session.json()["csrf_token"]
    assert client.post("/admin/logout", data={"csrf_token": "wrong"}).status_code == 403
    assert client.post("/admin/logout", data={"csrf_token": csrf}, follow_redirects=False).status_code == 303
    assert client.get("/api/admin/session").status_code == 401


def test_rate_limit(settings, components):
    from dataclasses import replace
    from oohstory_admin.app import create_app

    reader, library, systemd, audit = components
    limited_settings = replace(settings, login_attempts=2)
    with TestClient(create_app(limited_settings, reader=reader, library=library, systemd=systemd, audit=audit)) as client:
        assert login(client, password="definitely wrong").status_code == 401
        second = login(client, password="definitely wrong")
        assert second.status_code == 429
        assert int(second.headers["retry-after"]) > 0


def test_mutation_requires_csrf(client):
    assert login(client).status_code == 303
    payload = {"action": "restart", "target": "oohstory-reader.service"}
    assert client.post("/api/admin/pipeline/actions", json=payload).status_code == 403
    csrf = client.get("/api/admin/session").json()["csrf_token"]
    response = client.post("/api/admin/pipeline/actions", json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert client.fake_systemd.calls == [("restart", "oohstory-reader.service")]
