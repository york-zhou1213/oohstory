from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from app.comments import CommentStore
from app.settings import Settings


class FakeRepository:
    _mysql = object()


def test_comment_body_is_written_atomically_under_mount_root(tmp_path: Path) -> None:
    store = CommentStore(FakeRepository(), tmp_path / "comments")
    key = "v1/AA/AAAAAAAAAAAAAAAAAAAAAA/book/11111111-1111-4111-8111-111111111111.json"
    payload = {"schema": "oohstory-comment-v1", "content": "真实评论正文"}
    digest, byte_count = store._write_body(key, payload)
    target = tmp_path / "comments" / key
    assert target.is_file()
    assert target.stat().st_size == byte_count
    assert sha256(target.read_bytes()).digest() == digest
    assert store._read_body(key, digest) == payload
    assert not list(target.parent.glob("*.part"))


def test_comment_root_defaults_to_mounted_object_root(tmp_path: Path) -> None:
    settings = Settings(
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        allowed_hosts=("testserver",),
        object_root=tmp_path / "mounted",
    )
    assert settings.comment_object_root == tmp_path / "mounted" / "comments"


def test_mysql_comment_schema_keeps_body_out_of_database() -> None:
    project = Path(__file__).resolve().parents[1]
    migration = (
        project / "admin" / "deploy" / "mysql" / "023_reader_comment_objects.sql"
    ).read_text(encoding="utf-8")
    initializer = (
        project / "admin" / "deploy" / "mysql" / "init.sql"
    ).read_text(encoding="utf-8")
    assert "object_key" in migration
    assert "object_sha256" in migration
    assert "content TEXT" not in migration
    assert "comment_provider=comment_store" in (
        Path(__file__).resolve().parents[1] / "app" / "main.py"
    ).read_text(encoding="utf-8")
    assert "oohstory_public_reader_role" in initializer
    assert "ON `oohstory_library`.reader_comments" in initializer


def test_web_and_app_expose_book_detail_comments() -> None:
    project = Path(__file__).resolve().parents[1]
    web = (project / "static" / "app.js").read_text(encoding="utf-8")
    app_root = project / "mobile"
    mobile = (
        app_root / "lib" / "screens" / "book_detail_screen.dart"
    ).read_text(encoding="utf-8")
    service = (
        app_root / "lib" / "services" / "account_service.dart"
    ).read_text(encoding="utf-8")
    assert "/api/v1/books/${bookId}/comments" in web
    assert "/api/v1/comments/${comment.id}/likes" in web
    assert "读者评论" in web
    assert "_buildBookComments" in mobile
    assert "createBookComment" in service


def test_all_book_comments_follow_chapter_or_volume_directory_on_web_and_app() -> None:
    project = Path(__file__).resolve().parents[1]
    web = (project / "static" / "app.js").read_text(encoding="utf-8")
    mobile = (
        project / "mobile" / "lib" / "screens" / "book_detail_screen.dart"
    ).read_text(encoding="utf-8")
    web_render = web.index("app.replaceChildren", web.index("async function loadBook"))
    web_volume_directory = web.index("hasVolumes ? chapterPanel : null", web_render)
    web_chapter_directory = web.index("hasVolumes ? null : chapterPanel", web_volume_directory)
    web_comments = web.index("bookCommentSection", web_chapter_directory)
    assert web_volume_directory < web_comments
    assert web_chapter_directory < web_comments
    mobile_directory = mobile.index("SliverToBoxAdapter(child: _buildChapterHeader(theme))")
    mobile_comments = mobile.index("SliverToBoxAdapter(child: _buildBookComments(theme))")
    assert mobile_directory < mobile_comments
