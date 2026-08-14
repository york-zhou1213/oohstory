from __future__ import annotations

from oohstory_library.services.error_boundaries import RECOVERABLE_OPERATION_ERRORS

from pathlib import Path
import importlib.machinery
import importlib.util
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from conftest import FakeLibrary, FakeReader, FakeSystemd, login
from oohstory_admin.app import create_app
from oohstory_admin.audit import AuditLog
from oohstory_admin.operations import OperationsClient, parse_multipart
from oohstory_library.services.library_catalog_mysql import MySQLCatalogStore


class FakeOperations:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.staged: list[tuple[bytes, bytes]] = []
        self.discarded: list[str] = []

    def overview(self, *, query="", status=""):
        self.calls.append(("operations_overview", {"query": query, "status": status}))
        return {
            "summary": {"users": 1, "active_users": 1},
            "users": [{
                "id": "registered-1", "email": "reader@example.com",
                "display_name": "真实读者", "status": "active",
                "email_verified_at": "2026-08-06T00:00:00+00:00",
                "created_at": "2026-08-06T00:00:00+00:00",
                "updated_at": "2026-08-06T00:00:00+00:00",
                "last_login_at": None, "active_sessions": 2,
            }],
            "invites": [],
            "categories": [{
                "id": "category-1", "source_name": "科幻", "display_name": "科幻",
                "description": "", "enabled": True, "sort_order": 10,
                "is_custom": True,
            }],
            "uploads": [],
        }

    def publication_overview(self, *, query="", publication=""):
        self.calls.append(
            (
                "book_publication_overview",
                {"query": query, "publication": publication},
            )
        )
        return {
            "summary": {
                "books": 2,
                "published_books": 1,
                "unpublished_books": 1,
            },
            "books": [
                {
                    "catalog_id": 42,
                    "title": "测试故事",
                    "author": "作者",
                    "category": "科幻",
                    "book_status": "已完结",
                    "status": "done",
                    "body_available": 1,
                    "is_published": 1,
                    "updated_at": "2026-08-06T00:00:00+00:00",
                    "publication_reason": None,
                },
                {
                    "catalog_id": 43,
                    "title": "已下架故事",
                    "author": "作者乙",
                    "category": "悬疑",
                    "book_status": "连载中",
                    "status": "done",
                    "body_available": 1,
                    "is_published": 0,
                    "updated_at": "2026-08-06T00:00:00+00:00",
                    "publication_reason": "版权复核",
                },
            ],
        }

    def run(self, action, payload=None):
        payload = dict(payload or {})
        self.calls.append((action, payload))
        if action == "invite_create":
            return {"code": "OOH-test-only-code", "item": {"id": "invite-1"}}
        if action == "admin_novel_upload":
            return {"job_id": "a" * 20, "status": "running"}
        return {"ok": True}

    def stage_novel(self, manuscript, cover):
        self.staged.append((manuscript, cover))
        return "b" * 32

    def discard_novel(self, token):
        self.discarded.append(token)


def make_client(settings, tmp_path: Path):
    operations = FakeOperations()
    app = create_app(
        settings,
        reader=FakeReader(),
        library=FakeLibrary(),
        systemd=FakeSystemd(),
        audit=AuditLog(tmp_path / "operations-audit.db"),
        operations=operations,
    )
    return TestClient(app), operations


def csrf(client: TestClient) -> str:
    return client.get("/api/admin/session").json()["csrf_token"]


def test_operations_is_inside_independent_admin_and_lists_only_registered_users(settings, tmp_path):
    client, operations = make_client(settings, tmp_path)
    with client:
        assert client.get("/admin/operations", follow_redirects=False).status_code == 303
        assert login(client).status_code == 303
        page = client.get(
            "/admin/operations?q=reader&status=active&book_q=故事&publication=unpublished"
        )
        assert page.status_code == 200
        assert "真实读者" in page.text
        assert "reader@example.com" in page.text
        assert 'name="role"' not in page.text
        assert "/admin/operations" in page.text
        assert operations.calls[-2] == (
            "operations_overview", {"query": "reader", "status": "active"}
        )
        assert operations.calls[-1] == (
            "book_publication_overview",
            {"query": "故事", "publication": "unpublished"},
        )
        assert "书籍上下架" in page.text
        assert "已下架故事" in page.text
        assert "/admin/operations/books/42/publication" in page.text
        assert 'class="operations-nav"' in page.text
        assert 'class="operations-workspace"' in page.text
        assert 'class="table-wrap operations-table"' in page.text
        assert 'data-label="注册用户"' in page.text
        assert 'data-label="操作"' in page.text


def test_book_publication_mutation_is_csrf_protected_and_reversible(settings, tmp_path):
    client, operations = make_client(settings, tmp_path)
    with client:
        assert login(client).status_code == 303
        path = "/admin/operations/books/42/publication"
        assert client.post(
            path, data={"published": "0", "reason": "版权复核"}
        ).status_code == 403
        response = client.post(
            path,
            data={
                "csrf_token": csrf(client),
                "published": "0",
                "reason": "版权复核",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert operations.calls[-1] == (
            "book_publication_status",
            {"catalog_id": 42, "published": False, "reason": "版权复核"},
        )
        response = client.post(
            path,
            data={
                "csrf_token": csrf(client),
                "published": "1",
                "reason": "复核通过",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert operations.calls[-1] == (
            "book_publication_status",
            {"catalog_id": 42, "published": True, "reason": "复核通过"},
        )


def test_registered_user_mutation_is_csrf_protected_and_has_no_role_payload(settings, tmp_path):
    client, operations = make_client(settings, tmp_path)
    with client:
        assert login(client).status_code == 303
        path = "/admin/operations/users/registered-1/status"
        assert client.post(path, data={"status": "disabled"}).status_code == 403
        response = client.post(
            path,
            data={"csrf_token": csrf(client), "status": "disabled"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert operations.calls[-1] == (
            "registered_user_status",
            {"user_id": "registered-1", "status": "disabled"},
        )
        assert "role" not in operations.calls[-1][1]


def test_invite_is_created_by_helper_and_secret_is_only_in_post_response(settings, tmp_path):
    client, operations = make_client(settings, tmp_path)
    with client:
        assert login(client).status_code == 303
        response = client.post(
            "/admin/operations/invites/create",
            data={
                "csrf_token": csrf(client), "label": "内测",
                "max_uses": "5", "expires_in_days": "14",
            },
        )
        assert response.status_code == 200
        assert "OOH-test-only-code" in response.text
        assert operations.calls[-3] == (
            "invite_create",
            {"label": "内测", "max_uses": "5", "expires_in_days": "14"},
        )


def test_admin_novel_upload_stages_files_then_queues_formal_ingestion(settings, tmp_path):
    client, operations = make_client(settings, tmp_path)
    with client:
        assert login(client).status_code == 303
        response = client.post(
            "/admin/operations/novels/upload",
            data={
                "csrf_token": csrf(client), "title": "星港", "author": "作者甲",
                "category": "科幻", "serialization_status": "finished",
                "summary": "简介", "source": "作者本人原创",
                "authorization": "confirmed",
            },
            files={
                "manuscript": ("星港.txt", ("正文段落。" * 40).encode(), "text/plain"),
                "cover": ("cover.png", b"\x89PNG\r\n\x1a\n" + b"x" * 2048, "image/png"),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert len(operations.staged) == 1
        action, payload = operations.calls[-1]
        assert action == "admin_novel_upload"
        assert payload["upload_token"] == "b" * 32
        assert payload["authorization"] == "confirmed"
        assert payload["manuscript_filename"] == "星港.txt"


def test_operations_client_stages_bounded_private_files(tmp_path):
    class NoActions:
        pass

    client = OperationsClient(NoActions(), tmp_path / "uploads")
    token = client.stage_novel(b"x" * 100, b"y" * 1024)
    body = tmp_path / "uploads" / f"{token}.novel-body"
    cover = tmp_path / "uploads" / f"{token}.novel-cover"
    assert body.read_bytes() == b"x" * 100
    assert cover.read_bytes() == b"y" * 1024
    assert body.stat().st_mode & 0o777 == 0o600
    client.discard_novel(token)
    assert not body.exists() and not cover.exists()


def test_privileged_helper_forbids_account_creation_and_role_mutation():
    source = Path("ops/oohstory-admin-library-action-runner").read_text(encoding="utf-8")
    assert "INSERT INTO users" not in source
    assert "UPDATE users SET role" not in source
    assert 'set(payload) - {"action", "user_id", "status"}' in source
    assert 'set(payload) - {"action", "catalog_id", "published", "reason"}' in source
    assert '"book_publication_status"' in source
    launcher = Path("ops/oohstory-admin-library-action").read_text(encoding="utf-8")
    assert "/var/lib/oohstory-reader" in launcher


def test_standard_library_multipart_parser_rejects_duplicate_fields():
    boundary = "unit-boundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\na\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nb\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    try:
        parse_multipart(f"multipart/form-data; boundary={boundary}", body)
    except RECOVERABLE_OPERATION_ERRORS as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("duplicate multipart field must be rejected")


def load_runner():
    loader = importlib.machinery.SourceFileLoader(
        "oohstory_admin_action_runner_test",
        str(Path("ops/oohstory-admin-library-action-runner").resolve()),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def account_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE users (
              id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,display_name TEXT NOT NULL,
              status TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',email_verified_at TEXT,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_login_at TEXT
            );
            CREATE TABLE user_sessions (
              id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
              expires_at TEXT NOT NULL,revoked_at TEXT
            );
            CREATE TABLE registration_invites (
              id TEXT PRIMARY KEY,code_hash BLOB NOT NULL UNIQUE,label TEXT NOT NULL DEFAULT '',
              max_uses INTEGER NOT NULL,used_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,
              expires_at TEXT,disabled_at TEXT,last_used_at TEXT
            );
            CREATE TABLE managed_categories (
              id TEXT PRIMARY KEY,source_name TEXT NOT NULL UNIQUE,display_name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 1,
              sort_order INTEGER NOT NULL DEFAULT 100,is_custom INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL
            );
            INSERT INTO users(id,email,display_name,status,role,created_at,updated_at)
            VALUES('u1','reader@example.com','读者','active','user','2026-08-06','2026-08-06');
            INSERT INTO user_sessions(id,user_id,expires_at) VALUES('s1','u1','2099-01-01T00:00:00+00:00');
            """
        )


def test_helper_mutates_real_registration_tables_without_touching_role(tmp_path):
    runner = load_runner()
    database = tmp_path / "accounts.sqlite3"
    account_database(database)
    runner.ACCOUNTS_DATABASE = database

    overview = runner.operations_overview({"query": "reader", "status": "active"})
    assert overview["summary"] == {"users": 1, "active_users": 1}
    assert overview["users"][0]["email"] == "reader@example.com"
    assert "role" not in overview["users"][0]

    changed = runner.update_registered_user(
        {"action": "registered_user_status", "user_id": "u1", "status": "disabled"}
    )
    assert changed["status"] == "disabled"
    assert changed["revoked_sessions"] == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status,role FROM users WHERE id='u1'").fetchone() == (
            "disabled", "user"
        )
        assert connection.execute("SELECT revoked_at IS NOT NULL FROM user_sessions").fetchone()[0] == 1


def test_helper_creates_hashed_invite_and_custom_category_in_reader_database(tmp_path):
    runner = load_runner()
    database = tmp_path / "accounts.sqlite3"
    account_database(database)
    runner.ACCOUNTS_DATABASE = database

    invite = runner.create_registration_invite(
        {"label": "内测", "max_uses": 3, "expires_in_days": 7}
    )
    category = runner.mutate_managed_category(
        "category_create",
        {"name": "硬核科幻", "description": "星际与工程", "sort_order": 20},
    )
    with sqlite3.connect(database) as connection:
        code_hash = connection.execute("SELECT code_hash FROM registration_invites").fetchone()[0]
        stored = connection.execute(
            "SELECT source_name,is_custom FROM managed_categories WHERE id=?", (category["id"],)
        ).fetchone()
    assert invite["code"].startswith("OOH-")
    assert invite["code"].encode() not in bytes(code_hash)
    assert stored == ("硬核科幻", 1)


def test_helper_delegates_bounded_book_publication_actions(monkeypatch):
    runner = load_runner()

    class FakeCatalog:
        def __init__(self):
            self.calls = []

        def operations_publication_books(self, **payload):
            self.calls.append(("overview", payload))
            return {"summary": {"unpublished_books": 1}, "books": []}

        def set_book_publication(self, catalog_id, **payload):
            self.calls.append(("status", {"catalog_id": catalog_id, **payload}))
            return {"catalog_id": catalog_id, **payload, "changed": True}

    catalog = FakeCatalog()
    monkeypatch.setattr(
        runner,
        "service_instance",
        lambda: type("Service", (), {"mysql_catalog": catalog})(),
    )
    overview = runner.book_publication_overview(
        {"action": "book_publication_overview", "query": "故事", "publication": "unpublished"}
    )
    changed = runner.update_book_publication(
        {
            "action": "book_publication_status",
            "catalog_id": 42,
            "published": False,
            "reason": "版权复核",
        }
    )
    assert overview["summary"]["unpublished_books"] == 1
    assert changed["changed"] is True
    assert catalog.calls == [
        ("overview", {"query": "故事", "publication": "unpublished", "limit": 100}),
        (
            "status",
            {"catalog_id": 42, "published": False, "reason": "版权复核"},
        ),
    ]


def test_publication_migration_is_reversible_and_updates_public_facets():
    sql = Path("deploy/mysql/022_book_publication_controls.sql").read_text(
        encoding="utf-8"
    )
    assert "ADD COLUMN is_published" in sql
    assert "CREATE TABLE book_publication_events" in sql
    assert "OLD.is_published <> NEW.is_published" in sql
    assert "is_active=1 AND body_available=1 AND is_published=1" in sql


def test_mysql_publication_store_lists_and_changes_without_deleting_assets():
    from datetime import datetime, timezone

    class Cursor:
        def __init__(self):
            self.query = ""
            self.params = None
            self.rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query, params=None):
            self.query = " ".join(str(query).split())
            self.params = params
            self.rowcount = 1 if self.query.startswith("UPDATE books") else 0

        def fetchone(self):
            if "SUM(b.body_available=1" in self.query:
                return {"books": 2, "published_books": 1, "unpublished_books": 1}
            if "FOR UPDATE" in self.query:
                return {
                    "id": 42,
                    "title": "测试故事",
                    "author": "作者",
                    "category": "科幻",
                    "status": "done",
                    "body_available": 1,
                    "is_published": 1,
                }
            return None

        def fetchall(self):
            return [
                {
                    "catalog_id": 42,
                    "title": "测试故事",
                    "is_published": 1,
                    "updated_at": datetime(
                        2026, 8, 6, 5, 0, tzinfo=timezone.utc
                    ),
                }
            ]

    class Connection:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

    class Pool:
        def __init__(self):
            self.cursors = []
            self.readonly = []

        @contextmanager
        def connection(self, *, readonly=False):
            cursor = Cursor()
            self.cursors.append(cursor)
            self.readonly.append(readonly)
            yield Connection(cursor)

    invalidated = []
    pool = Pool()
    store = MySQLCatalogStore(
        SimpleNamespace(mysql_database="test"),
        pool=pool,
        cache_client=SimpleNamespace(
            invalidate=lambda *scopes: invalidated.extend(scopes)
        ),
    )
    overview = store.operations_publication_books(
        query="测试", publication="published"
    )
    changed = store.set_book_publication(
        42, published=False, reason="版权复核"
    )
    assert overview["summary"] == {
        "books": 2,
        "published_books": 1,
        "unpublished_books": 1,
    }
    assert overview["books"][0]["catalog_id"] == 42
    assert overview["books"][0]["updated_at"] == "2026-08-06T05:00:00+00:00"
    assert changed["published"] is False and changed["changed"] is True
    assert pool.readonly == [True, False]
    write_queries = " ".join(cursor.query for cursor in pool.cursors)
    assert "INSERT INTO book_publication_events" in write_queries
    assert invalidated == ["catalog", "book", "cover", "tone", "plot"]
