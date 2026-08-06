from __future__ import annotations

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


def test_invites_are_hashed_and_google_requires_existing_account_link(tmp_path: Path) -> None:
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
    with pytest.raises(AccountError, match="尚未绑定"):
        store.google_login(claims)
    google_user = store.link_google(user["id"], claims)
    assert google_user["email_verified"] is True
    assert google_user["google_linked"] is True
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
