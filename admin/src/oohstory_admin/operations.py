"""Privilege-separated client for OOHStory business administration.

The web process never opens the reader account database or the MySQL catalog.
It may only stage bounded uploads below its private state directory and submit
structured actions to the root-owned library helper.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from .library_actions import LibraryActionClient, LibraryActionError


UPLOAD_TOKEN_RE = re.compile(r"^[a-f0-9]{32}$")
MAX_MANUSCRIPT_BYTES = 40 * 1024 * 1024
MAX_NOVEL_COVER_BYTES = 12 * 1024 * 1024
MAX_MULTIPART_BYTES = MAX_MANUSCRIPT_BYTES + MAX_NOVEL_COVER_BYTES + 512 * 1024


class OperationsError(RuntimeError):
    """An exact business operation was rejected or failed."""


@dataclass(frozen=True, slots=True)
class MultipartPart:
    name: str
    filename: str
    content_type: str
    data: bytes


def parse_multipart(content_type: str, body: bytes) -> dict[str, MultipartPart]:
    """Parse one bounded browser form without adding a multipart dependency."""

    if not content_type.casefold().startswith("multipart/form-data;"):
        raise OperationsError("小说上传必须使用 multipart/form-data")
    if len(body) > MAX_MULTIPART_BYTES:
        raise OperationsError("小说上传请求超过 52.5MB 安全上限")
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii", "strict") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise OperationsError("小说上传表单结构无效")
    result: dict[str, MultipartPart] = {}
    parts = list(message.iter_parts())
    if len(parts) > 20:
        raise OperationsError("小说上传字段过多")
    for part in parts:
        if part.get_content_disposition() != "form-data":
            continue
        name = str(part.get_param("name", header="content-disposition") or "")
        if not name or name in result or len(name) > 64:
            raise OperationsError("小说上传字段无效或重复")
        filename = Path(str(part.get_filename() or "")).name[:180]
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise OperationsError("小说上传字段无法解码")
        result[name] = MultipartPart(
            name=name,
            filename=filename,
            content_type=str(part.get_content_type() or "application/octet-stream")[:100],
            data=payload,
        )
    return result


class OperationsClient:
    def __init__(self, actions: LibraryActionClient, upload_root: Path):
        self.actions = actions
        self.upload_root = Path(upload_root).resolve()

    def run(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self.actions.run(action, payload or {}, timeout_seconds=600).data
        except LibraryActionError as exc:
            raise OperationsError(str(exc)) from exc

    def overview(self, *, query: str = "", status: str = "") -> dict[str, Any]:
        return self.run(
            "operations_overview",
            {"query": str(query)[:100], "status": str(status)[:16]},
        )

    def publication_overview(
        self,
        *,
        query: str = "",
        publication: str = "",
    ) -> dict[str, Any]:
        return self.run(
            "book_publication_overview",
            {
                "query": str(query)[:100],
                "publication": str(publication)[:16],
            },
        )

    def stage_novel(self, manuscript: bytes, cover: bytes) -> str:
        if not 100 <= len(manuscript) <= MAX_MANUSCRIPT_BYTES:
            raise OperationsError("正文必须在 100 字节至 40MB 之间")
        if not 1024 <= len(cover) <= MAX_NOVEL_COVER_BYTES:
            raise OperationsError("封面必须在 1KB 至 12MB 之间")
        self.upload_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.upload_root, 0o700)
        token = secrets.token_hex(16)
        if not UPLOAD_TOKEN_RE.fullmatch(token):  # defensive invariant
            raise OperationsError("无法创建安全上传标识")
        paths = (
            (self.upload_root / f"{token}.novel-body", manuscript),
            (self.upload_root / f"{token}.novel-cover", cover),
        )
        created: list[Path] = []
        try:
            for path, data in paths:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created.append(path)
                try:
                    with os.fdopen(descriptor, "wb", closefd=True) as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
        except Exception as exc:
            for path in created:
                path.unlink(missing_ok=True)
            raise OperationsError("无法写入隔离上传区") from exc
        return token

    def discard_novel(self, token: str) -> None:
        if not UPLOAD_TOKEN_RE.fullmatch(str(token)):
            return
        for suffix in ("novel-body", "novel-cover"):
            (self.upload_root / f"{token}.{suffix}").unlink(missing_ok=True)
