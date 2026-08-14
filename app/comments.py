"""MySQL-indexed comments with immutable bodies on the mounted library."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Any
from uuid import UUID, uuid4

from .accounts import AccountError
from .comment_moderation import moderate_comment
from .library import LibraryRepository, _decode_public_id


PUBLIC_BOOK_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")


def _utc_datetime(value: str | datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


class CommentStore:
    def __init__(self, repository: LibraryRepository, root: Path):
        mysql = getattr(repository, "_mysql", None)
        if mysql is None:
            raise RuntimeError("评论元数据要求 MySQL 目录后端")
        self.repository = repository
        self.mysql = mysql
        self.root = Path(root).resolve()

    def _catalog_id(self, book_id: str) -> int:
        if not PUBLIC_BOOK_ID.fullmatch(str(book_id)):
            raise AccountError("作品不存在", 404)
        row = self.mysql.get_book(_decode_public_id(str(book_id)))
        if not row:
            raise AccountError("作品不存在", 404)
        return int(row["catalog_id"])

    def _relative_key(self, book_id: str, scope: str, comment_id: str, chapter_id: int | None) -> str:
        UUID(comment_id)
        suffix = "book" if scope == "book" else f"chapter-{int(chapter_id or 0)}"
        return f"v1/{book_id[:2]}/{book_id}/{suffix}/{comment_id}.json"

    def _path(self, relative_key: str) -> Path:
        candidate = (self.root / str(relative_key)).resolve()
        if not candidate.is_relative_to(self.root):
            raise RuntimeError("评论对象路径越界")
        return candidate

    @staticmethod
    def _body_bytes(payload: dict[str, Any]) -> bytes:
        return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

    def _write_body(self, relative_key: str, payload: dict[str, Any]) -> tuple[bytes, int]:
        target = self._path(relative_key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        raw = self._body_bytes(payload)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid4().hex}.part")
        try:
            with temporary.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o640)
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return sha256(raw).digest(), len(raw)

    def _read_body(self, relative_key: str, expected_hash: bytes) -> dict[str, Any] | None:
        try:
            raw = self._path(relative_key).read_bytes()
        except (FileNotFoundError, OSError):
            return None
        if sha256(raw).digest() != bytes(expected_hash):
            return None
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def create(
        self,
        user_id: str,
        *,
        book_id: str,
        scope: str,
        content: str,
        chapter_id: int | None = None,
        paragraph_index: int | None = None,
        paragraph_key: str = "",
        paragraph_excerpt: str = "",
        comment_id: str | None = None,
        status: str = "visible",
        created_at: str | datetime | None = None,
        enforce_daily_limit: bool = True,
    ) -> str:
        if scope not in {"book", "paragraph"}:
            raise ValueError("评论范围无效")
        cleaned = unicodedata.normalize("NFKC", str(content or "")).replace("\x00", "").strip()
        if not cleaned:
            raise AccountError("评论不能为空")
        if len(cleaned) > 500:
            raise AccountError("评论不能超过 500 个字符")
        moderation = moderate_comment(cleaned)
        if not moderation.allowed:
            raise AccountError(moderation.detail, 422)
        if scope == "paragraph" and (not chapter_id or paragraph_index is None or not paragraph_key):
            raise ValueError("章节评论定位无效")
        catalog_id = self._catalog_id(book_id)
        identifier = str(UUID(comment_id)) if comment_id else str(uuid4())
        created = _utc_datetime(created_at)
        relative_key = self._relative_key(book_id, scope, identifier, chapter_id)
        body = {
            "schema": "oohstory-comment-v1",
            "id": identifier,
            "scope": scope,
            "book_id": book_id,
            "chapter_id": int(chapter_id) if chapter_id else None,
            "paragraph_index": int(paragraph_index) if paragraph_index is not None else None,
            "paragraph_key": str(paragraph_key)[:80],
            "paragraph_excerpt": str(paragraph_excerpt)[:160],
            "user_id": str(user_id),
            "content": cleaned,
            "created_at": created.replace(tzinfo=timezone.utc).isoformat(),
        }
        digest, byte_count = self._write_body(relative_key, body)
        try:
            with self.mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    if enforce_daily_limit:
                        cursor.execute(
                            "SELECT COUNT(*) AS total FROM reader_comments "
                            "WHERE user_id=%s AND created_at>=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 24 HOUR)",
                            (str(user_id),),
                        )
                        if int(cursor.fetchone()["total"]) >= 100:
                            raise AccountError("今日评论次数已达上限，请明天再试", 429)
                    cursor.execute(
                        "INSERT INTO reader_comments "
                        "(id,catalog_id,book_public_id,scope,user_id,chapter_id,paragraph_index,"
                        "paragraph_key,object_key,object_sha256,object_bytes,status,created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (identifier, catalog_id, book_id, scope, str(user_id), chapter_id,
                         paragraph_index, str(paragraph_key)[:80], relative_key, digest,
                         byte_count, status, created),
                    )
        except BaseException:
            try:
                self._path(relative_key).unlink()
            except FileNotFoundError:
                pass
            raise
        return identifier

    def import_legacy(self, **kwargs: Any) -> tuple[str, bool]:
        identifier = str(UUID(str(kwargs["comment_id"])))
        with self.mysql.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM reader_comments WHERE id=%s", (identifier,))
                if cursor.fetchone():
                    return identifier, False
        return self.create(enforce_daily_limit=False, **kwargs), True

    def _query(self, where: str, params: tuple[Any, ...], viewer_user_id: str | None, limit: int) -> list[dict[str, Any]]:
        viewer = str(viewer_user_id or "")
        bounded = min(max(int(limit), 1), 1000)
        with self.mysql.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT c.id,c.user_id,c.scope,c.chapter_id,c.paragraph_index,c.paragraph_key,"
                    "c.object_key,c.object_sha256,c.created_at,COALESCE(SUM(r.like_count),0) AS like_count,"
                    "COALESCE(MAX(CASE WHEN r.user_id=%s THEN r.like_count ELSE 0 END),0) AS viewer_like_count "
                    "FROM reader_comments c LEFT JOIN reader_comment_reactions r ON r.comment_id=c.id "
                    f"WHERE c.status='visible' AND {where} GROUP BY c.id "
                    "ORDER BY c.created_at DESC,c.id DESC LIMIT %s",
                    (viewer, *params, bounded),
                )
                rows = list(cursor.fetchall())
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = self._read_body(str(row["object_key"]), bytes(row["object_sha256"]))
            if payload is None:
                continue
            result.append({
                "id": str(row["id"]), "user_id": str(row["user_id"]),
                "scope": str(row["scope"]), "chapter_id": row["chapter_id"],
                "paragraph_index": row["paragraph_index"], "paragraph_key": str(row["paragraph_key"] or ""),
                "paragraph_excerpt": str(payload.get("paragraph_excerpt") or ""),
                "content": str(payload.get("content") or ""),
                "created_at": row["created_at"].replace(tzinfo=timezone.utc).isoformat() if isinstance(row["created_at"], datetime) else str(row["created_at"]),
                "like_count": int(row["like_count"]),
                "viewer_like_count": int(row["viewer_like_count"]),
            })
        return result

    def book_comments(self, book_id: str, viewer_user_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        self._catalog_id(book_id)
        return self._query("c.book_public_id=%s AND c.scope='book'", (book_id,), viewer_user_id, limit)

    def paragraph_comments(self, book_id: str, chapter_id: int, paragraph_keys: list[str], viewer_user_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        self._catalog_id(book_id)
        keys = [str(item)[:80] for item in paragraph_keys if item]
        if not keys:
            return []
        placeholders = ",".join(["%s"] * len(keys))
        return self._query(
            f"c.book_public_id=%s AND c.scope='paragraph' AND c.chapter_id=%s AND c.paragraph_key IN ({placeholders})",
            (book_id, int(chapter_id), *keys), viewer_user_id, limit,
        )

    def adjust_like(self, user_id: str, comment_id: str, delta: int) -> dict[str, Any]:
        if int(delta) not in {-1, 1}:
            raise ValueError("点赞增量必须为 -1 或 1")
        identifier = str(UUID(str(comment_id)))
        with self.mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT user_id FROM reader_comments WHERE id=%s AND status='visible' FOR UPDATE",
                    (identifier,),
                )
                row = cursor.fetchone()
                if not row:
                    raise AccountError("评论不存在", 404)
                if str(row["user_id"]) == str(user_id):
                    raise AccountError("不能给自己的评论点赞")
                cursor.execute(
                    "SELECT like_count FROM reader_comment_reactions "
                    "WHERE comment_id=%s AND user_id=%s FOR UPDATE",
                    (identifier, str(user_id)),
                )
                reaction = cursor.fetchone()
                viewer_count = int(reaction["like_count"]) if reaction else 0
                if delta > 0:
                    if viewer_count >= 3:
                        raise AccountError("每位用户对同一条评论最多点赞 3 次", 409)
                    cursor.execute(
                        "INSERT INTO reader_comment_reactions(comment_id,user_id,like_count,created_at) "
                        "VALUES (%s,%s,1,UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE "
                        "like_count=like_count+1,updated_at=UTC_TIMESTAMP(6)",
                        (identifier, str(user_id)),
                    )
                    viewer_count += 1
                elif viewer_count <= 1:
                    cursor.execute(
                        "DELETE FROM reader_comment_reactions WHERE comment_id=%s AND user_id=%s",
                        (identifier, str(user_id)),
                    )
                    viewer_count = 0
                else:
                    cursor.execute(
                        "UPDATE reader_comment_reactions SET like_count=like_count-1,updated_at=UTC_TIMESTAMP(6) "
                        "WHERE comment_id=%s AND user_id=%s",
                        (identifier, str(user_id)),
                    )
                    viewer_count -= 1
                cursor.execute(
                    "SELECT COALESCE(SUM(like_count),0) AS total FROM reader_comment_reactions WHERE comment_id=%s",
                    (identifier,),
                )
                total = int(cursor.fetchone()["total"])
        return {"liked": viewer_count > 0, "like_count": total, "viewer_like_count": viewer_count,
                "thanked": viewer_count > 0, "thanks_count": total}

    def import_reaction(
        self, comment_id: str, user_id: str, like_count: int, created_at: str | datetime
    ) -> None:
        identifier = str(UUID(str(comment_id)))
        count = min(max(int(like_count), 1), 3)
        created = _utc_datetime(created_at)
        with self.mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO reader_comment_reactions"
                    "(comment_id,user_id,like_count,created_at) VALUES (%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE like_count=GREATEST(like_count,VALUES(like_count))",
                    (identifier, str(user_id), count, created),
                )
