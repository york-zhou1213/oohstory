from __future__ import annotations

from tests.frontend_contract_source import frontend_contract_source
from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.accounts import AccountStore, READING_LEVELS, reading_level_summary
from app.settings import Settings
from app.user_api import create_user_router, recommendation_visitor_id


BOOK_ID = "AAAAAAAAAAAAAAAAAAAAAA"
SECOND_BOOK_ID = "BBBBBBBBBBBBBBBBBBBBBB"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Books:
    def __init__(self) -> None:
        self.recommend_count = 0
        self.seen_recommendations: set[str] = set()

    def get_book(self, book_id: str):
        if book_id not in {BOOK_ID, SECOND_BOOK_ID}:
            raise ValueError("missing")
        return {"public_id": book_id}

    def public_metrics(self, book_id: str):
        self.get_book(book_id)
        return {
            "public_id": book_id,
            "read_count": 0,
            "download_count": 0,
            "recommend_count": self.recommend_count,
            "favorite_count": 0,
        }

    def record_public_metric(self, book_id: str, visitor_id: str, event: str):
        self.get_book(book_id)
        if event != "recommend":
            raise ValueError("unexpected metric")
        key = f"{book_id}:{visitor_id}"
        counted = key not in self.seen_recommendations
        if counted:
            self.seen_recommendations.add(key)
            self.recommend_count += 1
        return self.public_metrics(book_id) | {"counted": counted}

    def reader_chapter(self, book_id: str, chapter_id: int):
        if book_id != BOOK_ID or int(chapter_id) != 1:
            raise ValueError("missing")
        return {
            "id": 1,
            "content": "第一段星海启航。\n第二段风暴将至。\n[illustration:images/1.jpg]\n第三段归航。",
        }


def make_client(tmp_path: Path) -> tuple[TestClient, Settings, str]:
    settings = Settings(
        library_root=(tmp_path / "library").resolve(),
        state_root=(tmp_path / "state").resolve(),
        allowed_hosts=("testserver",),
        account_database=(tmp_path / "accounts.sqlite3").resolve(),
        avatar_root=(tmp_path / "avatars").resolve(),
        upload_root=(tmp_path / "uploads").resolve(),
    )
    app = FastAPI()
    books = Books()
    app.include_router(create_user_router(settings, lambda: books))
    store = AccountStore(settings.user_database_path, session_ttl_seconds=3600)
    invite, _item = store.create_invite(label="user-center", max_uses=5)
    return TestClient(app, base_url="https://testserver"), settings, invite


def register(browser: TestClient, invite: str) -> dict:
    response = browser.post("/api/v1/auth/register", json={
        "email": "reader@example.com",
        "password": "Correct-Horse-9-Battery",
        "display_name": "星海读者",
        "invite_code": invite,
        "client": "web",
    })
    assert response.status_code == 201
    return response.json()


def image_bytes(size: tuple[int, int] = (96, 96), format: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (38, 123, 179)).save(output, format=format)
    return output.getvalue()


def test_all_eighteen_reading_level_boundaries_are_exact() -> None:
    assert READING_LEVELS == (
        ("Ⅰ", "只如初见", 0),
        ("Ⅱ", "此去经年", 30),
        ("Ⅲ", "素心相赠", 100),
        ("Ⅳ", "犹故人归", 250),
        ("Ⅴ", "踏歌寻醉", 500),
        ("Ⅵ", "冷暖自知", 1_000),
        ("Ⅶ", "青青子衿", 1_800),
        ("Ⅷ", "似水流年", 3_000),
        ("Ⅸ", "不诉离殇", 5_000),
        ("Ⅹ", "近月侵衣", 8_000),
        ("Ⅺ", "对酒当歌", 12_000),
        ("Ⅻ", "长风万里", 18_000),
        ("ⅩⅢ", "知与谁同", 26_000),
        ("ⅩⅣ", "扶摇九霄", 36_000),
        ("ⅩⅤ", "凌云绝顶", 48_000),
        ("ⅩⅥ", "摘星揽月", 62_000),
        ("ⅩⅦ", "天人合一", 80_000),
        ("ⅩⅧ", "水月镜花", 100_000),
    )
    for index, (roman, name, threshold) in enumerate(READING_LEVELS):
        summary = reading_level_summary(threshold * 3600)
        assert summary["level"] == index + 1
        assert summary["roman"] == roman
        assert summary["name"] == name
        assert summary["threshold_hours"] == threshold
        if index:
            before = reading_level_summary(threshold * 3600 - 1)
            assert before["level"] == index
    maximum = reading_level_summary(200_000 * 3600)
    assert maximum["roman"] == "ⅩⅧ"
    assert maximum["name"] == "水月镜花"
    assert maximum["is_max"] is True
    assert maximum["next_threshold_hours"] is None

    fractional = reading_level_summary(2_160)
    assert fractional["active_minutes"] == 36
    assert fractional["minutes_to_next"] == 1_764
    exact_hour = reading_level_summary(3_600)
    assert exact_hour["active_minutes"] == 60


def test_legacy_comment_reactions_gain_cumulative_like_count(tmp_path: Path) -> None:
    database = tmp_path / "legacy-accounts.sqlite3"
    AccountStore(database, session_ttl_seconds=3600)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE paragraph_comment_thanks")
        connection.execute(
            "CREATE TABLE paragraph_comment_thanks ("
            "comment_id TEXT NOT NULL,user_id TEXT NOT NULL,created_at TEXT NOT NULL,"
            "PRIMARY KEY(comment_id,user_id))"
        )
    AccountStore(database, session_ttl_seconds=3600)
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]: row for row in connection.execute(
                "PRAGMA table_info(paragraph_comment_thanks)"
            )
        }
    assert "like_count" in columns
    assert columns["like_count"][4] == "1"


def test_profile_is_private_csrf_protected_and_allowlisted(tmp_path: Path) -> None:
    browser, _settings, invite = make_client(tmp_path)
    with browser:
        registration = register(browser, invite)
        assert browser.put("/api/v1/me/profile", json={
            "display_name": "新名字", "bio": "简介", "gender": "female",
            "birthday": "2000-01-02", "location": "上海",
        }).status_code == 403
        updated = browser.put(
            "/api/v1/me/profile",
            headers={"X-CSRF-Token": registration["csrf_token"]},
            json={
                "display_name": "新名字", "bio": "只公开我选择填写的资料",
                "gender": "female", "birthday": "2000-01-02", "location": "上海",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["profile"] == {
            "display_name": "新名字", "bio": "只公开我选择填写的资料",
            "gender": "female", "birthday": "2000-01-02", "location": "上海",
            "avatar_url": None,
        }
        assert "email" not in updated.json()["profile"]
        assert browser.put(
            "/api/v1/me/profile",
            headers={"X-CSRF-Token": registration["csrf_token"]},
            json={"display_name": "x", "bio": "x" * 501, "gender": "", "location": ""},
        ).status_code == 422


def test_password_change_keeps_current_session_and_revokes_others(tmp_path: Path) -> None:
    browser, settings, invite = make_client(tmp_path)
    with browser:
        registration = register(browser, invite)
        other = browser.post("/api/v1/auth/login", json={
            "email": "reader@example.com",
            "password": "Correct-Horse-9-Battery",
            "client": "android",
        }).json()["access_token"]
        wrong = browser.post(
            "/api/v1/me/password",
            headers={"X-CSRF-Token": registration["csrf_token"]},
            json={"current_password": "wrong", "new_password": "New-Correct-8-Password"},
        )
        assert wrong.status_code == 401
        with sqlite3.connect(settings.user_database_path) as connection:
            audit = connection.execute(
                "SELECT event,outcome FROM security_audit_events ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        assert audit == ("password_change", "invalid_current_password")
        assert b"wrong" not in settings.user_database_path.read_bytes()
        changed = browser.post(
            "/api/v1/me/password",
            headers={"X-CSRF-Token": registration["csrf_token"]},
            json={
                "current_password": "Correct-Horse-9-Battery",
                "new_password": "New-Correct-8-Password",
            },
        )
        assert changed.status_code == 200
        assert changed.json()["revoked_other_sessions"] >= 1
        assert browser.get("/api/v1/me/profile").status_code == 200
        assert browser.get(
            "/api/v1/me/profile", headers={"Authorization": f"Bearer {other}"}
        ).status_code == 401
        assert browser.post("/api/v1/auth/login", json={
            "email": "reader@example.com", "password": "Correct-Horse-9-Battery", "client": "android",
        }).status_code == 401
        assert browser.post("/api/v1/auth/login", json={
            "email": "reader@example.com", "password": "New-Correct-8-Password", "client": "android",
        }).status_code == 200


def test_avatar_decodes_content_strips_to_png_and_supports_replace_remove(tmp_path: Path) -> None:
    browser, settings, invite = make_client(tmp_path)
    with browser:
        registration = register(browser, invite)
        headers = {"X-CSRF-Token": registration["csrf_token"]}
        invalid = browser.post(
            "/api/v1/me/avatar", headers=headers,
            files={"file": ("avatar.png", b"not an image", "image/png")},
        )
        assert invalid.status_code == 422
        uploaded = browser.post(
            "/api/v1/me/avatar", headers=headers,
            files={"file": ("misleading.txt", image_bytes(format="JPEG"), "text/plain")},
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["avatar_url"].startswith("/api/v1/me/avatar?v=")
        avatar = browser.get("/api/v1/me/avatar")
        assert avatar.status_code == 200
        assert avatar.headers["content-type"] == "image/png"
        assert avatar.content.startswith(b"\x89PNG\r\n\x1a\n")
        path = settings.user_avatar_root / registration["user"]["id"] / "avatar.png"
        assert path.is_file() and oct(path.stat().st_mode & 0o777) == "0o600"
        public_avatar = browser.get(
            f"/api/v1/users/{registration['user']['id']}/avatar"
        )
        assert public_avatar.status_code == 200
        assert public_avatar.headers["cache-control"] == "public, max-age=604800, immutable"
        assert public_avatar.content == avatar.content
        too_large = browser.post(
            "/api/v1/me/avatar", headers=headers,
            files={"file": ("large.png", image_bytes((2100, 2100)), "image/png")},
        )
        assert too_large.status_code == 422
        assert browser.delete("/api/v1/me/avatar", headers=headers).status_code == 204
        assert browser.get("/api/v1/me/avatar").status_code == 404
        assert browser.get(
            f"/api/v1/users/{registration['user']['id']}/avatar"
        ).status_code == 404


def test_reading_heartbeat_is_idempotent_and_rapid_claims_are_capped(tmp_path: Path) -> None:
    browser, settings, invite = make_client(tmp_path)
    with browser:
        registration = register(browser, invite)
        headers = {"X-CSRF-Token": registration["csrf_token"]}
        event_id = str(uuid.uuid4())
        first = browser.post("/api/v1/me/reading-heartbeat", headers=headers, json={
            "event_id": event_id, "book_id": BOOK_ID, "active_seconds": 30,
        }).json()
        assert first["accepted_seconds"] == 30
        duplicate = browser.post("/api/v1/me/reading-heartbeat", headers=headers, json={
            "event_id": event_id, "book_id": BOOK_ID, "active_seconds": 60,
        }).json()
        assert duplicate["duplicate"] is True
        assert duplicate["active_seconds"] == 30
        rapid = browser.post("/api/v1/me/reading-heartbeat", headers=headers, json={
            "event_id": str(uuid.uuid4()), "book_id": BOOK_ID, "active_seconds": 60,
        }).json()
        assert rapid["accepted_seconds"] == 0
        with sqlite3.connect(settings.user_database_path) as connection:
            total = connection.execute("SELECT active_seconds FROM user_reading_totals").fetchone()[0]
        assert total == 30
        assert browser.post("/api/v1/me/reading-heartbeat", headers=headers, json={
            "event_id": str(uuid.uuid4()), "book_id": "C" * 22, "active_seconds": 30,
        }).status_code == 404


def test_recommendation_donation_is_atomic_idempotent_and_requires_one_hour(
    tmp_path: Path,
) -> None:
    browser, settings, invite = make_client(tmp_path)
    with browser:
        session = register(browser, invite)
        headers = {"X-CSRF-Token": session["csrf_token"]}
        user_id = session["user"]["id"]
        with sqlite3.connect(settings.user_database_path) as connection:
            connection.execute(
                "INSERT INTO user_reading_totals"
                "(user_id,active_seconds,last_heartbeat_at,updated_at) VALUES(?,?,NULL,?)",
                (user_id, 10_800, "2026-08-06T00:00:00+00:00"),
            )
            connection.commit()

        status = browser.get(f"/api/v1/books/{BOOK_ID}/recommendation")
        assert status.status_code == 200
        assert status.json()["recommended"] is False
        assert status.json()["boost_count"] == 0
        assert status.json()["active_seconds"] == 10_800

        first_event_id = str(uuid.uuid4())
        donated = browser.post(
            f"/api/v1/books/{BOOK_ID}/recommend",
            headers=headers,
            json={"event_id": first_event_id},
        )
        assert donated.status_code == 200
        assert donated.json()["new_donation"] is True
        assert donated.json()["donated_seconds"] == 3_600
        assert donated.json()["recommend_count"] == 1
        assert donated.json()["boost_count"] == 1
        assert donated.json()["reading"]["active_seconds"] == 7_200

        repeated = browser.post(
            f"/api/v1/books/{BOOK_ID}/recommend",
            headers=headers,
            json={"event_id": first_event_id},
        )
        assert repeated.status_code == 200
        assert repeated.json()["new_donation"] is False
        assert repeated.json()["donated_seconds"] == 0
        assert repeated.json()["recommend_count"] == 1
        assert repeated.json()["replayed_event"] is True
        assert repeated.json()["reading"]["active_seconds"] == 7_200

        second = browser.post(
            f"/api/v1/books/{BOOK_ID}/recommend",
            headers=headers,
            json={"event_id": str(uuid.uuid4())},
        )
        assert second.status_code == 200
        assert second.json()["new_donation"] is True
        assert second.json()["donated_seconds"] == 3_600
        assert second.json()["recommend_count"] == 2
        assert second.json()["boost_count"] == 2
        assert second.json()["reading"]["active_seconds"] == 3_600

        current = browser.get(f"/api/v1/books/{BOOK_ID}/recommendation").json()
        assert current["recommended"] is True
        assert current["boost_count"] == 2
        assert current["donated_seconds"] == 7_200
        assert current["active_seconds"] == 3_600

        with sqlite3.connect(settings.user_database_path) as connection:
            connection.execute(
                "UPDATE user_reading_totals SET active_seconds=3599 WHERE user_id=?",
                (user_id,),
            )
            connection.commit()
        insufficient = browser.post(
            f"/api/v1/books/{SECOND_BOOK_ID}/recommend",
            headers=headers,
            json={"event_id": str(uuid.uuid4())},
        )
        assert insufficient.status_code == 409
        assert "不足 1 小时" in insufficient.json()["detail"]
        with sqlite3.connect(settings.user_database_path) as connection:
            assert connection.execute(
                "SELECT active_seconds FROM user_reading_totals WHERE user_id=?",
                (user_id,),
            ).fetchone()[0] == 3_599
            assert connection.execute(
                "SELECT COUNT(*) FROM user_book_recommendations WHERE user_id=?",
                (user_id,),
            ).fetchone()[0] == 2


def test_recommendation_metric_visitor_is_stable_unique_and_uuid4() -> None:
    first_event = str(uuid.uuid4())
    second_event = str(uuid.uuid4())
    first = recommendation_visitor_id("reader-a", first_event)
    assert first == recommendation_visitor_id("reader-a", first_event)
    assert first != recommendation_visitor_id("reader-a", second_event)
    assert first != recommendation_visitor_id("reader-b", first_event)
    assert uuid.UUID(first).version == 4


def test_concurrent_recommendation_requests_charge_exactly_once(tmp_path: Path) -> None:
    browser, settings, invite = make_client(tmp_path)
    with browser:
        session = register(browser, invite)
    user_id = session["user"]["id"]
    with sqlite3.connect(settings.user_database_path) as connection:
        connection.execute(
            "INSERT INTO user_reading_totals"
            "(user_id,active_seconds,last_heartbeat_at,updated_at) VALUES(?,?,NULL,?)",
            (user_id, 7_200, "2026-08-06T00:00:00+00:00"),
        )
        connection.commit()

    store = AccountStore(settings.user_database_path, session_ttl_seconds=3600)
    event_id = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda _attempt: store.donate_recommendation(
                    user_id, BOOK_ID, event_id
                ),
                range(2),
            )
        )

    assert sorted(receipt["new_donation"] for receipt in receipts) == [False, True]
    assert sorted(receipt["donated_seconds"] for receipt in receipts) == [0, 3_600]
    with sqlite3.connect(settings.user_database_path) as connection:
        assert connection.execute(
            "SELECT active_seconds FROM user_reading_totals WHERE user_id=?", (user_id,)
        ).fetchone()[0] == 3_600
        assert connection.execute(
            "SELECT COUNT(*) FROM user_book_recommendations WHERE user_id=? AND book_id=?",
            (user_id, BOOK_ID),
        ).fetchone()[0] == 1


def test_distinct_concurrent_recommendation_events_each_charge_one_hour(
    tmp_path: Path,
) -> None:
    browser, settings, invite = make_client(tmp_path)
    with browser:
        session = register(browser, invite)
    user_id = session["user"]["id"]
    with sqlite3.connect(settings.user_database_path) as connection:
        connection.execute(
            "INSERT INTO user_reading_totals"
            "(user_id,active_seconds,last_heartbeat_at,updated_at) VALUES(?,?,NULL,?)",
            (user_id, 7_200, "2026-08-06T00:00:00+00:00"),
        )
        connection.commit()

    store = AccountStore(settings.user_database_path, session_ttl_seconds=3600)
    event_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda event_id: store.donate_recommendation(
                    user_id, BOOK_ID, event_id
                ),
                event_ids,
            )
        )

    assert all(receipt["new_donation"] for receipt in receipts)
    assert all(receipt["donated_seconds"] == 3_600 for receipt in receipts)
    with sqlite3.connect(settings.user_database_path) as connection:
        assert connection.execute(
            "SELECT active_seconds FROM user_reading_totals WHERE user_id=?", (user_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM user_book_recommendations "
            "WHERE user_id=? AND book_id=?",
            (user_id, BOOK_ID),
        ).fetchone()[0] == 2


def test_legacy_one_time_recommendation_rows_migrate_to_boost_events(
    tmp_path: Path,
) -> None:
    browser, settings, invite = make_client(tmp_path)
    with browser:
        session = register(browser, invite)
    user_id = session["user"]["id"]
    with sqlite3.connect(settings.user_database_path) as connection:
        connection.execute("DROP TABLE user_book_recommendations")
        connection.execute(
            "CREATE TABLE user_book_recommendations ("
            "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
            "book_id TEXT NOT NULL,"
            "spent_seconds INTEGER NOT NULL DEFAULT 3600 CHECK(spent_seconds = 3600),"
            "metric_applied INTEGER NOT NULL DEFAULT 0 CHECK(metric_applied IN (0,1)),"
            "created_at TEXT NOT NULL,"
            "applied_at TEXT,PRIMARY KEY(user_id,book_id))"
        )
        connection.execute(
            "INSERT INTO user_book_recommendations"
            "(user_id,book_id,spent_seconds,metric_applied,created_at,applied_at) "
            "VALUES(?,?,3600,1,?,?)",
            (
                user_id,
                BOOK_ID,
                "2026-08-06T00:00:00+00:00",
                "2026-08-06T00:00:01+00:00",
            ),
        )
        connection.commit()

    migrated = AccountStore(settings.user_database_path, session_ttl_seconds=3600)
    status = migrated.recommendation_status(user_id, BOOK_ID)
    assert status["recommended"] is True
    assert status["boost_count"] == 1
    assert status["donated_seconds"] == 3_600
    assert status["metric_applied"] is True
    with sqlite3.connect(settings.user_database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(user_book_recommendations)")
        }
        assert {"id", "request_id"}.issubset(columns)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_comment_routes_fail_closed_without_mounted_store(tmp_path: Path) -> None:
    browser, settings, invite = make_client(tmp_path)
    with browser:
        first = register(browser, invite)
        route = f"/api/v1/books/{BOOK_ID}/chapters/1/comments"
        assert browser.get(route).status_code == 503
        response = browser.post(
            route,
            headers={"X-CSRF-Token": first["csrf_token"]},
            json={"paragraph_index": 1, "content": "这里的伏笔终于连起来了。"},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "评论存储尚未启用"

    with sqlite3.connect(settings.user_database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paragraph_comments").fetchone()[0] == 0


def _legacy_paragraph_comment_contract_reference(tmp_path: Path) -> None:
    browser, settings, invite = make_client(tmp_path)
    with browser:
        first = register(browser, invite)
        route = f"/api/v1/books/{BOOK_ID}/chapters/1/comments"
        initial = browser.get(route)
        assert initial.json()["comment_count"] == 0
        assert initial.headers["cache-control"] == "private, no-store"
        assert initial.headers["vary"] == "Cookie, Authorization"
        assert browser.post(route, json={"paragraph_index": 0, "content": "很好"}).status_code == 403

        headers = {"X-CSRF-Token": first["csrf_token"]}
        assert browser.post(
            "/api/v1/me/avatar",
            headers=headers,
            files={"file": ("avatar.png", image_bytes(), "image/png")},
        ).status_code == 201
        for blocked, notice_kind in (
            ("你就是个傻 逼", "community"),
            ("来裸聊吧", "community"),
            ("跟我刷单返利", "community"),
            ("来博 彩下注", "community"),
            ("看 e x a m p l e 点 c o m", "promotion"),
        ):
            response = browser.post(
                route, headers=headers,
                json={"paragraph_index": 1, "content": blocked},
            )
            assert response.status_code == 422
            detail = response.json()["detail"]
            assert "422" not in detail
            if notice_kind == "promotion":
                assert detail.startswith("这条评论需要修改")
                assert "网站、联系方式或推广内容" in detail
            else:
                assert detail.startswith("这条评论暂时不能发布")
                assert "社区交流规范" in detail

        created = browser.post(
            route, headers=headers,
            json={"paragraph_index": 1, "content": "这里的伏笔终于连起来了。"},
        )
        assert created.status_code == 201
        comment_id = created.json()["created_comment_id"]
        thread = next(iter(created.json()["paragraphs"].values()))
        assert thread["paragraph_index"] == 1
        assert thread["count"] == 1
        assert thread["comments"][0]["author"]["display_name"] == "星海读者"
        assert thread["comments"][0]["author"]["avatar_url"].startswith(
            f"/api/v1/users/{first['user']['id']}/avatar?v="
        )
        assert thread["comments"][0]["author"]["reading"]["roman"] == "Ⅰ"
        assert thread["comments"][0]["is_own"] is True
        assert browser.post(
            f"/api/v1/paragraph-comments/{comment_id}/likes", headers=headers
        ).status_code == 400

        other = browser.post("/api/v1/auth/register", json={
            "email": "other@example.com",
            "password": "Other-Correct-8-Password",
            "display_name": "另一位读者",
            "invite_code": invite,
            "client": "android",
        })
        assert other.status_code == 201
        bearer = {"Authorization": f"Bearer {other.json()['access_token']}"}
        like_route = f"/api/v1/paragraph-comments/{comment_id}/likes"
        for expected in (1, 2, 3):
            liked = browser.post(like_route, headers=bearer)
            assert liked.status_code == 200
            assert liked.json()["liked"] is True
            assert liked.json()["like_count"] == expected
            assert liked.json()["viewer_like_count"] == expected
            assert liked.json()["thanks_count"] == expected
        limit = browser.post(like_route, headers=bearer)
        assert limit.status_code == 409
        assert "最多点赞 3 次" in limit.json()["detail"]
        loaded = browser.get(route, headers=bearer).json()
        loaded_thread = next(iter(loaded["paragraphs"].values()))
        assert loaded_thread["comments"][0]["like_count"] == 3
        assert loaded_thread["comments"][0]["viewer_like_count"] == 3
        assert loaded_thread["comments"][0]["thanked_by_me"] is True
        legacy_route = f"/api/v1/paragraph-comments/{comment_id}/thanks"
        for expected in (2, 1, 0):
            legacy = browser.delete(legacy_route, headers=bearer)
            assert legacy.status_code == 200
            assert legacy.json()["like_count"] == expected
            assert legacy.json()["viewer_like_count"] == expected

        with sqlite3.connect(settings.user_database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM paragraph_comments").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM paragraph_comment_thanks").fetchone()[0] == 0
            assert "like_count" in {
                row[1] for row in connection.execute("PRAGMA table_info(paragraph_comment_thanks)")
            }


def test_nginx_get_upload_history_does_not_consume_upload_quota() -> None:
    nginx = (PROJECT_ROOT / "deploy" / "nginx-oohstory.conf").read_text(encoding="utf-8")
    assert "map $request_method $oohstory_upload_rate_key" in nginx
    assert 'default "";' in nginx
    assert "POST $binary_remote_addr;" in nginx
    assert "limit_req_zone $oohstory_upload_rate_key zone=ohhstory_upload" in nginx
    uploads = nginx.split("location = /api/v1/me/uploads {", 1)[1].split("\n    }", 1)[0]
    assert (
        "limit_req_zone $oohstory_account_rate_key "
        "zone=ohhstory_account_v2:10m rate=20r/s;"
    ) in nginx
    assert "limit_req zone=ohhstory_account_v2 burst=40 nodelay;" in uploads
    assert "limit_req zone=ohhstory_upload" in uploads
    assert "real_ip_header CF-Connecting-IP;" in nginx
    assert "set_real_ip_from 173.245.48.0/20;" in nginx
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" not in nginx
    assert nginx.count("proxy_set_header X-Forwarded-For $remote_addr;") > 0
    assert nginx.count("proxy_set_header X-Forwarded-For $remote_addr;") == nginx.count(
        "proxy_set_header CF-Connecting-IP $remote_addr;"
    )
    for route in (
        "PUT:/api/v1/me/profile", "DELETE:/api/v1/me/avatar",
        "POST:/api/v1/me/(?:avatar|password(?:/setup)?|reading-heartbeat)",
        "POST:/api/v1/books/[A-Za-z0-9_-]{22}/chapters/[1-9][0-9]*/comments",
        "(?:POST|DELETE):/api/v1/paragraph-comments/[0-9a-f-]{36}/thanks",
        "POST:/api/v1/paragraph-comments/[0-9a-f-]{36}/likes",
        "POST:/api/v1/deconstructions/[^/]+/likes",
        "POST:/api/v1/deconstruction-tasks",
        "(?:POST|DELETE):/api/v1/deconstruction-tasks/[0-9a-f-]{36}/claim",
        "POST:/api/v1/me/wallet/convert-reading",
        "POST:/api/v1/me/deconstructions/[^/]+/purchase",
        "PATCH:/api/v1/me/deconstructions/[^/]+/price",
        "POST:/api/v1/books/[A-Za-z0-9_-]{22}/recommend",
    ):
        assert route in nginx
    recommendation_location = nginx.split(
        'location ~ "^/api/v1/books/[A-Za-z0-9_-]{22}/recommend$" {', 1
    )[1].split("\n    }", 1)[0]
    assert "limit_req zone=ohhstory_account_v2" in recommendation_location
    assert "proxy_pass http://oohstory_reader_backend;" in recommendation_location
    comment_location = nginx.split(
        'location ~ "^/api/v1/books/[A-Za-z0-9_-]{22}/chapters/[1-9][0-9]*/comments$" {',
        1,
    )[1].split("\n    }", 1)[0]
    assert "^(GET|HEAD|POST)$" in comment_location
    assert "client_max_body_size 2k;" in comment_location
    assert "limit_req zone=ohhstory_account_v2 burst=40 nodelay;" in comment_location
    thanks_location = nginx.split(
        'location ~ "^/api/v1/paragraph-comments/[0-9a-f-]{36}/thanks$" {',
        1,
    )[1].split("\n    }", 1)[0]
    assert "^(POST|DELETE)$" in thanks_location
    likes_location = nginx.split(
        'location ~ "^/api/v1/paragraph-comments/[0-9a-f-]{36}/likes$" {',
        1,
    )[1].split("\n    }", 1)[0]
    assert "^POST$" in likes_location
    deconstruction_likes_location = nginx.split(
        'location ~ "^/api/v1/deconstructions/[^/]+/likes$" {', 1
    )[1].split("\n    }", 1)[0]
    assert "^(GET|HEAD|POST)$" in deconstruction_likes_location
    assert "limit_req zone=ohhstory_account_v2 burst=40 nodelay;" in (
        deconstruction_likes_location
    )
    assert "proxy_pass http://oohstory_reader_backend;" in (
        deconstruction_likes_location
    )
    task_location = nginx.split(
        'location ~ "^/api/v1/deconstruction-tasks(?:/[0-9a-f-]{36}/claim)?$" {', 1
    )[1].split("\n    }", 1)[0]
    assert "^(GET|HEAD|POST|DELETE)$" in task_location
    assert "client_max_body_size 8k;" in task_location
    account_location = nginx.split(
        "location ^~ /api/v1/me/ {", 1
    )[1].split("\n    }", 1)[0]
    assert "^(GET|HEAD|POST|PUT|PATCH|DELETE)$" in account_location


def test_spa_has_real_account_routes_profile_controls_and_active_time_tracker() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    for route in (
        "#/account/history", "#/account/favorites", "#/account/bookshelf",
        "#/account/deconstruction-tasks", "#/account/submit",
        "#/account/submissions", "#/account/profile",
    ):
        assert route in script
    assert "async function loadAccountCollection(kind)" in script
    assert "async function loadProfilePage()" in script
    assert "async function loadDeconstructionTasksPage()" in script
    assert "async function loadSubmitPage()" in script
    assert "async function loadMySubmissionsPage()" in script
    assert "current_password" in script and "new_password" in script
    assert "image/jpeg,image/png,image/webp" in script
    assert "startReadingActivity(String(requestedBookId))" in script
    assert "document.visibilityState === 'visible'" in script
    assert "Date.now() - lastInteraction < 90_000" in script
    assert "response.status === 429 && ['GET', 'HEAD'].includes(method)" in script
    assert "window.setTimeout(resolve, 450)" in script
    assert ".account-book-grid" in styles
    assert ".reading-level-map" in styles
    assert "@media (max-width: 720px)" in styles
