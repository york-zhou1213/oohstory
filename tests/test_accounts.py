from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest

from app.accounts import AccountError, AccountStore, token_hash


def make_store(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "state" / "accounts.sqlite3", session_ttl_seconds=3600)


def invite(store: AccountStore, *, max_uses: int = 1) -> str:
    code, _item = store.create_invite(label="pytest", max_uses=max_uses)
    return code


def test_password_registration_uses_argon2_and_revocable_opaque_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    user, verification = store.register(
        "Reader@Example.COM",
        "Correct-Horse-9-Battery",
        "星海读者",
        invite(store),
    )

    assert user["email"] == "reader@example.com"
    assert user["email_verified"] is False
    raw = store.path.read_bytes()
    assert b"Correct-Horse-9-Battery" not in raw
    with sqlite3.connect(store.path) as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["id"],)
        ).fetchone()[0]
    assert password_hash.startswith("$argon2id$")

    verified = store.verify_email(verification)
    assert verified["email_verified"] is True
    logged_in = store.password_login("reader@example.com", "Correct-Horse-9-Battery")
    session = store.create_session(logged_in, client="android")
    with sqlite3.connect(store.path) as connection:
        stored_token_hash = connection.execute(
            "SELECT token_hash FROM user_sessions WHERE user_id=?", (user["id"],)
        ).fetchone()[0]
    assert stored_token_hash == token_hash(session.token)
    assert store.session(session.token).user_id == user["id"]

    store.revoke_session(session.token)
    assert store.session(session.token) is None


def test_registration_never_auto_promotes_the_only_verified_user(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    user, verification = store.register(
        "reader@example.com",
        "Correct-Horse-9-Battery",
        "唯一读者",
        invite(store),
    )
    store.verify_email(verification)

    reopened = AccountStore(store.path, session_ttl_seconds=3600)
    logged_in = reopened.password_login(
        "reader@example.com", "Correct-Horse-9-Battery"
    )

    assert logged_in["id"] == user["id"]
    assert logged_in["role"] == "user"


def test_duplicate_email_and_weak_password_are_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    code = invite(store, max_uses=2)
    with pytest.raises(AccountError, match="密码"):
        store.register("reader@example.com", "password1234", "读者", code)

    store.register("reader@example.com", "Correct-Horse-9-Battery", "读者", code)
    with pytest.raises(AccountError, match="已经注册"):
        store.register("READER@example.com", "Another-Good-8-Password", "读者", code)


def test_weighted_rate_limit_enforces_character_budget(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.enforce_rate_limit("tts-owner", limit=10, window=60, cost=6)
    store.enforce_rate_limit("tts-owner", limit=10, window=60, cost=4)

    with pytest.raises(AccountError) as blocked:
        store.enforce_rate_limit("tts-owner", limit=10, window=60, cost=1)

    assert blocked.value.status_code == 429


def test_display_names_and_direct_comment_writes_share_content_guard(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    code = invite(store, max_uses=3)
    with pytest.raises(AccountError, match="昵称") as blocked_registration:
        store.register(
            "blocked@example.com",
            "Correct-Horse-9-Battery",
            "玩球+我vXxXx赚钱",
            code,
        )
    assert blocked_registration.value.status_code == 422

    user, _ = store.register(
        "reader@example.com", "Correct-Horse-9-Battery", "星海读者", code
    )
    with pytest.raises(AccountError, match="昵称") as blocked_update:
        store.update_profile(
            user["id"],
            display_name="兼职日结联系VX88888",
            bio="",
            gender="",
            birthday=None,
            location="",
        )
    assert blocked_update.value.status_code == 422

    with pytest.raises(AccountError, match="这条评论需要修改") as blocked_comment:
        store.create_paragraph_comment(
            user["id"],
            book_id="AAAAAAAAAAAAAAAAAAAAAA",
            chapter_id=1,
            paragraph_index=0,
            paragraph_key="p0-test",
            paragraph_excerpt="测试段落",
            content="请看 e x a m p l e c o m",
        )
    assert blocked_comment.value.status_code == 422
    assert "网站、联系方式或推广内容" in str(blocked_comment.value)


def test_private_state_isolated_per_user_and_merges_latest_progress(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    code = invite(store, max_uses=2)
    first, _ = store.register("first@example.com", "Correct-Horse-9-Battery", "甲", code)
    second, _ = store.register("second@example.com", "Another-Good-8-Password", "乙", code)
    book_id = "AAAAAAAAAAAAAAAAAAAAAA"

    store.sync_state(first["id"], {
        "history": [{
            "book_id": book_id,
            "chapter_id": 8,
            "progress": 0.6,
            "title": "星海",
            "updated_at": "2026-08-04T01:00:00+00:00",
        }],
        "favorites": [{"book_id": book_id, "title": "星海"}],
        "bookshelf": [{"book_id": book_id, "title": "星海", "note": "待追更"}],
    })
    store.sync_state(first["id"], {
        "history": [{
            "book_id": book_id,
            "chapter_id": 2,
            "progress": 0.1,
            "updated_at": "2026-08-03T01:00:00+00:00",
        }],
        "favorites": [],
        "bookshelf": [],
    })

    state = store.state(first["id"])
    assert state["history"][0]["chapter_id"] == 8
    assert state["favorites"][0]["book_id"] == book_id
    assert state["bookshelf"][0]["note"] == "待追更"
    assert store.state(second["id"]) == {"history": [], "favorites": [], "bookshelf": []}


def test_cookie_csrf_token_is_compared_as_a_hash(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    user, _ = store.register(
        "reader@example.com", "Correct-Horse-9-Battery", "读者", invite(store)
    )
    session = store.create_session(user, client="web")
    loaded = store.session(session.token)

    store.require_csrf(loaded, session.csrf_token)
    with pytest.raises(AccountError, match="安全令牌"):
        store.require_csrf(loaded, "wrong-token")


def test_invites_are_hashed_and_google_first_login_is_direct_but_never_auto_merges(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    code = invite(store)
    assert code.encode() not in store.path.read_bytes()

    with pytest.raises(AccountError, match="邀请码"):
        store.register(
            "blocked@example.com", "Correct-Horse-9-Battery", "读者", "x" * 24
        )

    user, _ = store.register(
        "reader@example.com", "Correct-Horse-9-Battery", "读者", code
    )
    assert user["email"] == "reader@example.com"
    with pytest.raises(AccountError, match="邀请码"):
        store.register(
            "second@example.com", "Another-Good-8-Password", "读者", code
        )

    claims = {
        "sub": "google-new-user",
        "email": "reader@example.com",
        "email_verified": True,
        "name": "Google Reader",
    }
    with pytest.raises(AccountError, match="密码账户"):
        store.google_login(claims)

    direct_claims = claims | {
        "sub": "google-direct-user",
        "email": "direct@example.com",
        "name": "Direct Reader",
    }
    google_user = store.google_login(direct_claims)
    assert google_user["email_verified"] is True
    assert google_user["google_linked"] is True
    assert google_user["password_login_enabled"] is False
    assert store.google_login(direct_claims)["id"] == google_user["id"]
    assert store.login_methods(google_user["id"]) == {
        "google": True,
        "password": False,
    }
    with sqlite3.connect(store.path) as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM users WHERE id=?", (google_user["id"],)
        ).fetchone()[0]
        created = connection.execute(
            "SELECT COUNT(*) FROM users WHERE email='direct@example.com'"
        ).fetchone()[0]
    assert password_hash is None
    assert created == 1

    direct_session = store.create_session(google_user, client="web")
    with pytest.raises(AccountError, match="身份确认失败"):
        store.setup_password(
            google_user["id"],
            direct_session.session_id,
            direct_claims | {"sub": "different-google-subject"},
            "Google-Local-8-Password",
        )
    store.setup_password(
        google_user["id"],
        direct_session.session_id,
        direct_claims,
        "Google-Local-8-Password",
    )
    assert store.login_methods(google_user["id"]) == {
        "google": True,
        "password": True,
    }
    assert store.password_login(
        "direct@example.com", "Google-Local-8-Password"
    )["id"] == google_user["id"]

    linked_user = store.link_google(user["id"], claims)
    assert linked_user["email_verified"] is True
    assert linked_user["google_linked"] is True
    assert linked_user["password_login_enabled"] is True
    assert store.google_login(claims)["id"] == user["id"]

    with pytest.raises(AccountError, match="邮箱必须"):
        store.link_google(user["id"], claims | {"sub": "other", "email": "other@example.com"})


def test_web_google_link_token_is_single_use(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    user, _ = store.register(
        "reader@example.com",
        "Correct-Horse-9-Battery",
        "读者",
        invite(store),
    )
    claims = {
        "sub": "google-subject",
        "email": "reader@example.com",
        "email_verified": True,
    }
    token = store.create_google_link_token(user["id"])
    linked = store.link_google_with_token(token, claims)
    assert linked["google_linked"] is True
    with pytest.raises(AccountError, match="无效或已过期"):
        store.link_google_with_token(token, claims)


def test_concurrent_google_first_login_creates_exactly_one_user(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    claims = {
        "sub": "google-concurrent-subject",
        "email": "concurrent@example.com",
        "email_verified": True,
        "name": "Concurrent Reader",
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        users = list(pool.map(lambda _index: store.google_login(claims), range(8)))

    assert len({user["id"] for user in users}) == 1
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM users WHERE email='concurrent@example.com'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM user_identities "
            "WHERE provider='google' AND subject='google-concurrent-subject'"
        ).fetchone()[0] == 1


def test_deconstruction_likes_are_unique_and_toggleable(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    user, _ = store.register(
        "reader@example.com",
        "Correct-Horse-9-Battery",
        "读者",
        invite(store),
    )
    first = store.toggle_deconstruction_like(user["id"], "样本书")
    assert first == {"slug": "样本书", "liked": True, "like_count": 1}
    engagement = store.deconstruction_engagement(
        ["样本书"], viewer_user_id=user["id"]
    )
    assert engagement["样本书"] == {
        "like_count": 1,
        "viewer_liked": True,
        "download_count": 0,
    }
    second = store.toggle_deconstruction_like(user["id"], "样本书")
    assert second == {"slug": "样本书", "liked": False, "like_count": 0}
