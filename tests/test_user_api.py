from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.accounts import AccountStore
from app.settings import Settings
from app.user_api import GOOGLE_LINK_STATE, GOOGLE_REDIRECT_STATE, create_user_router
from app import user_api


BOOK_ID = "AAAAAAAAAAAAAAAAAAAAAA"


class Books:
    def __init__(self):
        self.favorite_counts: list[tuple[str, int]] = []

    def get_book(self, book_id: str):
        if book_id != BOOK_ID:
            raise ValueError("missing")
        return {"public_id": book_id}

    def set_favorite_count(self, book_id: str, count: int):
        self.favorite_counts.append((book_id, count))
        return {"public_id": book_id, "favorite_count": count}


def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        library_root=(tmp_path / "library").resolve(),
        state_root=(tmp_path / "state").resolve(),
        allowed_hosts=("testserver",),
        account_database=(tmp_path / "accounts.sqlite3").resolve(),
        upload_root=(tmp_path / "uploads").resolve(),
    )
    app = FastAPI()
    books = Books()
    app.include_router(create_user_router(settings, lambda: books))
    browser = TestClient(app, base_url="https://testserver")
    code, _item = AccountStore(
        settings.user_database_path, session_ttl_seconds=3600
    ).create_invite(label="pytest", max_uses=20)
    browser.test_invite = code
    browser.test_account_database = settings.user_database_path
    browser.test_books = books
    return browser


def test_web_registration_issues_httponly_cookie_and_csrf_protects_state(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        registered = browser.post("/api/v1/auth/register", json={
            "email": "reader@example.com",
            "password": "Correct-Horse-9-Battery",
            "display_name": "星海读者",
            "invite_code": browser.test_invite,
            "client": "web",
        })
        assert registered.status_code == 201
        assert registered.json()["user"]["email"] == "reader@example.com"
        assert "HttpOnly" in registered.headers["set-cookie"]
        assert "Secure" in registered.headers["set-cookie"]
        assert "access_token" not in registered.json()

        session = browser.get("/api/v1/auth/session")
        assert session.status_code == 200
        csrf = session.json()["csrf_token"]

        rejected = browser.put("/api/v1/me/state", json={
            "history": [], "favorites": [], "bookshelf": []
        })
        assert rejected.status_code == 403

        synced = browser.put(
            "/api/v1/me/state",
            headers={"X-CSRF-Token": csrf},
            json={
                "history": [{
                    "book_id": BOOK_ID,
                    "chapter_id": 7,
                    "progress": 0.4,
                    "title": "星海",
                }],
                "favorites": [{"book_id": BOOK_ID, "title": "星海"}],
                "bookshelf": [{"book_id": BOOK_ID, "title": "星海"}],
            },
        )
        assert synced.status_code == 200
        assert synced.json()["history"][0]["chapter_id"] == 7
        assert browser.test_books.favorite_counts[-1] == (BOOK_ID, 1)

        removed = browser.delete(
            f"/api/v1/me/state/favorites/{BOOK_ID}",
            headers={"X-CSRF-Token": csrf},
        )
        assert removed.status_code == 204
        assert browser.test_books.favorite_counts[-1] == (BOOK_ID, 0)


def test_anonymous_session_probe_is_200_but_private_state_stays_protected(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        session = browser.get("/api/v1/auth/session")
        assert session.status_code == 200
        assert session.json() == {
            "authenticated": False,
            "user": None,
            "expires_at": None,
            "csrf_token": "",
        }
        assert browser.get("/api/v1/me/state").status_code == 401


def test_mobile_login_returns_bearer_token_and_google_is_fail_closed_without_config(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        browser.post("/api/v1/auth/register", json={
            "email": "reader@example.com",
            "password": "Correct-Horse-9-Battery",
            "display_name": "读者",
            "invite_code": browser.test_invite,
            "client": "web",
        })
        logged_in = browser.post("/api/v1/auth/login", json={
            "email": "reader@example.com",
            "password": "Correct-Horse-9-Battery",
            "client": "android",
        })
        assert logged_in.status_code == 200
        assert logged_in.json()["token_type"] == "Bearer"
        assert len(logged_in.json()["access_token"]) >= 32

        google = browser.post("/api/v1/auth/google", json={
            "id_token": "x" * 200,
            "client": "android",
        })
        assert google.status_code == 503


def test_google_config_exposes_only_public_client_capabilities(tmp_path: Path) -> None:
    settings = Settings(
        library_root=(tmp_path / "library").resolve(),
        state_root=(tmp_path / "state").resolve(),
        allowed_hosts=("testserver",),
        account_database=(tmp_path / "accounts.sqlite3").resolve(),
        google_web_client_id="web.apps.googleusercontent.com",
        google_android_client_id="android.apps.googleusercontent.com",
    )
    app = FastAPI()
    app.include_router(create_user_router(settings, Books))
    with TestClient(app, base_url="https://testserver") as browser:
        google = browser.get("/api/v1/auth/config").json()["google"]
    assert google == {
        "web_enabled": True,
        "web_client_id": "web.apps.googleusercontent.com",
        "android_enabled": True,
        "ios_enabled": False,
        "existing_account_link_required": True,
    }


def test_google_redirect_checks_double_submit_csrf_and_issues_web_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        library_root=(tmp_path / "library").resolve(),
        state_root=(tmp_path / "state").resolve(),
        allowed_hosts=("testserver",),
        account_database=(tmp_path / "accounts.sqlite3").resolve(),
        google_web_client_id="web.apps.googleusercontent.com",
    )
    router = create_user_router(settings, Books)
    app = FastAPI()
    app.include_router(router)
    app.add_api_route(
        "/",
        getattr(router, "google_redirect_handler"),
        methods=["POST"],
    )
    account_store = AccountStore(
        settings.user_database_path,
        session_ttl_seconds=3600,
    )
    code, _item = account_store.create_invite(
        label="google-redirect",
        max_uses=1,
    )
    registered, _verification = account_store.register(
        "google-reader@example.com",
        "Correct-Horse-9-Battery",
        "Google Reader",
        code,
    )
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "google-subject-1",
        "email": "google-reader@example.com",
        "email_verified": True,
        "name": "Google Reader",
    }
    account_store.link_google(registered["id"], claims)
    monkeypatch.setattr(
        user_api.google_id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: claims,
    )

    with TestClient(app, base_url="https://testserver") as browser:
        browser.cookies.set("g_csrf_token", "csrf-value")
        response = browser.post(
            "/",
            data={
                "credential": "x" * 200,
                "g_csrf_token": "csrf-value",
                "state": GOOGLE_REDIRECT_STATE,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/#/account"
        assert "oohstory_session=" in response.headers["set-cookie"]
        assert browser.get("/api/v1/auth/session").status_code == 200


def test_google_redirect_rejects_csrf_mismatch_without_session(tmp_path: Path) -> None:
    settings = Settings(
        library_root=(tmp_path / "library").resolve(),
        state_root=(tmp_path / "state").resolve(),
        allowed_hosts=("testserver",),
        account_database=(tmp_path / "accounts.sqlite3").resolve(),
        google_web_client_id="web.apps.googleusercontent.com",
    )
    router = create_user_router(settings, Books)
    app = FastAPI()
    app.include_router(router)
    app.add_api_route(
        "/",
        getattr(router, "google_redirect_handler"),
        methods=["POST"],
    )

    with TestClient(app, base_url="https://testserver") as browser:
        browser.cookies.set("g_csrf_token", "cookie-value")
        response = browser.post(
            "/",
            data={
                "credential": "x" * 200,
                "g_csrf_token": "different-value",
                "state": GOOGLE_REDIRECT_STATE,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith("/?google_error=")
        assert settings.session_cookie not in response.headers.get("set-cookie", "")


def test_registered_user_can_link_google_then_login_on_mobile(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        library_root=(tmp_path / "library").resolve(),
        state_root=(tmp_path / "state").resolve(),
        allowed_hosts=("testserver",),
        account_database=(tmp_path / "accounts.sqlite3").resolve(),
        google_web_client_id="web.apps.googleusercontent.com",
        google_android_client_id="android.apps.googleusercontent.com",
    )
    app = FastAPI()
    app.include_router(create_user_router(settings, Books))
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "google-mobile-subject",
        "email": "reader@example.com",
        "email_verified": True,
        "name": "Reader",
    }
    monkeypatch.setattr(
        user_api.google_id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: claims,
    )
    account_store = AccountStore(settings.user_database_path, session_ttl_seconds=3600)
    code, _item = account_store.create_invite(label="mobile-link")

    with TestClient(app, base_url="https://testserver") as browser:
        registered = browser.post("/api/v1/auth/register", json={
            "email": "reader@example.com",
            "password": "Correct-Horse-9-Battery",
            "display_name": "读者",
            "invite_code": code,
            "client": "android",
        }).json()
        token = registered["access_token"]

        before_link = browser.post("/api/v1/auth/google", json={
            "id_token": "x" * 200,
            "client": "android",
        })
        assert before_link.status_code == 409

        linked = browser.post(
            "/api/v1/auth/google/link",
            headers={"Authorization": f"Bearer {token}"},
            json={"id_token": "x" * 200, "client": "android"},
        )
        assert linked.status_code == 200
        assert linked.json()["user"]["google_linked"] is True

        google_login = browser.post("/api/v1/auth/google", json={
            "id_token": "x" * 200,
            "client": "android",
        })
        assert google_login.status_code == 200
        assert google_login.json()["user"]["email"] == "reader@example.com"


def test_web_google_link_redirect_consumes_short_lived_link_cookie(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        library_root=(tmp_path / "library").resolve(),
        state_root=(tmp_path / "state").resolve(),
        allowed_hosts=("testserver",),
        account_database=(tmp_path / "accounts.sqlite3").resolve(),
        google_web_client_id="web.apps.googleusercontent.com",
    )
    router = create_user_router(settings, Books)
    app = FastAPI()
    app.include_router(router)
    app.add_api_route("/", getattr(router, "google_redirect_handler"), methods=["POST"])
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "google-web-link-subject",
        "email": "reader@example.com",
        "email_verified": True,
        "name": "Reader",
    }
    monkeypatch.setattr(
        user_api.google_id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: claims,
    )
    account_store = AccountStore(settings.user_database_path, session_ttl_seconds=3600)
    code, _item = account_store.create_invite(label="web-link")

    with TestClient(app, base_url="https://testserver") as browser:
        registered = browser.post("/api/v1/auth/register", json={
            "email": "reader@example.com",
            "password": "Correct-Horse-9-Battery",
            "display_name": "读者",
            "invite_code": code,
            "client": "web",
        }).json()
        started = browser.post(
            "/api/v1/auth/google/link/start",
            headers={"X-CSRF-Token": registered["csrf_token"]},
        )
        assert started.status_code == 200
        assert started.json()["state"] == GOOGLE_LINK_STATE

        browser.cookies.set("g_csrf_token", "csrf-value")
        response = browser.post(
            "/",
            data={
                "credential": "x" * 200,
                "g_csrf_token": "csrf-value",
                "state": GOOGLE_LINK_STATE,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/?google_linked=1#/account"
        assert browser.get("/api/v1/auth/session").json()["user"]["google_linked"] is True


def test_unverified_account_cannot_open_upload_pipeline(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        registered = browser.post("/api/v1/auth/register", json={
            "email": "reader@example.com",
            "password": "Correct-Horse-9-Battery",
            "display_name": "读者",
            "invite_code": browser.test_invite,
            "client": "web",
        }).json()
        response = browser.post(
            "/api/v1/me/uploads",
            headers={"X-CSRF-Token": registered["csrf_token"]},
            files={"file": ("novel.txt", b"chapter" * 100, "text/plain")},
        )
        assert response.status_code == 403
        assert "验证" in response.json()["detail"]


def test_registration_is_open_with_optional_invite(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        config = browser.get("/api/v1/auth/config")
        assert config.json()["registration_mode"] == "open"
        assert config.json()["invite_required"] is False

        missing = browser.post("/api/v1/auth/register", json={
            "email": "missing@example.com",
            "password": "Correct-Horse-9-Battery",
            "display_name": "读者",
            "client": "web",
        })
        assert missing.status_code == 201
        assert missing.json()["user"]["email"] == "missing@example.com"

        invites = AccountStore(
            browser.test_account_database, session_ttl_seconds=3600
        ).list_invites()
        assert invites[0]["used_count"] == 0

        invalid = browser.post("/api/v1/auth/register", json={
            "email": "invalid@example.com",
            "password": "Correct-Horse-9-Battery",
            "display_name": "读者",
            "invite_code": "x" * 24,
            "client": "web",
        })
        assert invalid.status_code == 403
        assert "邀请码" in invalid.json()["detail"]
