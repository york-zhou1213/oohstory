"""Download and persist verified source covers for electronic-library books."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from oohstory_library.services.cover_failure_policy import (
    is_missing_cover_placeholder_image,
    is_missing_cover_placeholder_sha256,
)
from oohstory_library.services.default_cover import (
    delete_missing_placeholder_if_unreferenced,
    materialize_default_cover,
)
from oohstory_library.services.download_security import DownloadSecurityScanner
from oohstory_library.services.library_database import LibraryInfrastructureSettings
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime


FANQIE_PAGE_HOSTS = {"fanqienovel.com", "www.fanqienovel.com"}
FANQIE_IMAGE_HOST_PATTERN = re.compile(
    r"^p\d+-(?:novel|novel-sign)\.byteimg\.com$"
)
COVER_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;
CREATE TABLE IF NOT EXISTS covers (
  catalog_id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT NOT NULL,
  detail_url TEXT NOT NULL,
  cover_url TEXT,
  filename TEXT,
  sha256 TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_covers_filename
  ON covers(filename) WHERE filename IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_covers_status ON covers(status, attempts);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _mysql_runtime() -> MySQLLibraryRuntime | None:
    settings = LibraryInfrastructureSettings.from_env()
    return MySQLLibraryRuntime(settings) if settings.catalog_backend == "mysql" else None


def _identity_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", normalized)


def _image_extension(data: bytes, content_type: str) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError(
        f"番茄封面响应不是支持的图片格式：{content_type or 'unknown'}"
    )


def _request_bytes(url: str, *, referer: str = "", timeout: int = 30) -> tuple[bytes, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        data = response.read(12 * 1024 * 1024 + 1)
        content_type = str(response.headers.get("Content-Type") or "")
    return data, content_type


def _request_html(url: str, *, timeout: int = 30) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(8 * 1024 * 1024).decode(charset, errors="replace")


def _scan_cover_bytes(
    data: bytes,
    extension: str,
    cover_index_path: Path,
) -> dict[str, Any]:
    scanner = DownloadSecurityScanner(
        Path(cover_index_path).resolve().parent / ".download-security-staging"
    )
    return scanner.scan_bytes(
        data,
        extension=extension,
        source="remote_cover",
    )


def _install_mysql_missing_default(
    *,
    runtime: MySQLLibraryRuntime,
    cover_root: Path,
    catalog_id: int,
    source_digest: str,
) -> dict[str, Any]:
    reason = (
        "原站返回缺失封面指纹，已改用 OOHStory 默认封面并排队重绘："
        f"{source_digest}"
    )
    default = materialize_default_cover(
        cover_root=cover_root,
        catalog_id=int(catalog_id),
    )
    persisted = runtime.persist_missing_cover_default(
        catalog_id=int(catalog_id),
        filename=str(default["filename"]),
        sha256=str(default["sha256"]),
        bytes_count=int(default["bytes"]),
        content_type=str(default["content_type"]),
        reason=reason,
    )
    original_filename = str(persisted.get("original_filename") or "")
    original_sha256 = str(persisted.get("original_sha256") or "")
    cleanup: dict[str, Any] = {"status": "not_applicable"}
    if (
        original_filename
        and original_filename != str(default["filename"])
        and is_missing_cover_placeholder_sha256(original_sha256)
    ):
        cleanup = delete_missing_placeholder_if_unreferenced(
            runtime=runtime,
            cover_root=cover_root,
            catalog_id=int(catalog_id),
            filename=original_filename,
        )
    return {
        **default,
        "status": "defaulted_missing_placeholder",
        "source_sha256": source_digest,
        "cleanup": cleanup,
    }


KNOWN_COVER_WATERMARKS = (
    "txt80",
    "txt02",
    "txt8080",
    "xbiquge",
    "ixdzs",
    "shubaow",
    "www.",
    ".com",
    ".net",
    ".org",
    ".cc",
)


def _cover_watermark_text(data: bytes, extension: str) -> str:
    """Return bounded English OCR used only for known download-site marks."""

    with tempfile.NamedTemporaryFile(suffix=extension) as source:
        source.write(data)
        source.flush()
        result = subprocess.run(
            ["tesseract", source.name, "stdout", "-l", "eng"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    if result.returncode:
        detail = (result.stderr or result.stdout or "封面水印 OCR 失败").strip()
        raise RuntimeError(detail[:300])
    return re.sub(r"\s+", " ", result.stdout).strip().casefold()[:4000]


def prepare_alternate_remote_cover(
    *,
    origin_detail_url: str,
    cover_url: str,
    cover_index_path: Path,
    allowed_hosts: Iterable[str] = (),
    allowed_host_suffixes: Iterable[str] = (),
    request_bytes: Callable[[str], tuple[bytes, str]] | None = None,
    reject_known_watermarks: bool = False,
) -> dict[str, Any]:
    """Fetch and validate once so source selection can stop before persistence."""

    parsed = urlparse(str(cover_url or "").strip())
    host = (parsed.hostname or "").lower()
    trusted_hosts = {
        str(value).strip().lower() for value in allowed_hosts if str(value).strip()
    }
    trusted_suffixes = tuple(
        str(value).strip().lower()
        for value in allowed_host_suffixes
        if str(value).strip()
    )
    if (
        parsed.scheme != "https"
        or not host
        or not (
            host in trusted_hosts
            or any(host.endswith(suffix) for suffix in trusted_suffixes)
        )
    ):
        raise ValueError("备用封面图片不在当前书源的可信 HTTPS 主机内")

    data, content_type = (
        request_bytes(cover_url)
        if request_bytes is not None
        else _request_bytes(cover_url, referer=origin_detail_url)
    )
    if len(data) < 1024 or len(data) > 12 * 1024 * 1024:
        raise ValueError(f"备用封面文件大小异常：{len(data)}")
    extension = _image_extension(data, content_type)
    _scan_cover_bytes(data, extension, cover_index_path)
    watermark_text = ""
    if reject_known_watermarks:
        watermark_text = _cover_watermark_text(data, extension)
        detected = [
            mark for mark in KNOWN_COVER_WATERMARKS if mark in watermark_text
        ]
        if detected:
            raise ValueError(
                "备用封面仍含下载站水印：" + ",".join(detected[:4])
            )
    digest = hashlib.sha256(data).hexdigest()
    if is_missing_cover_placeholder_image(data, sha256=digest):
        return {
            "cover_url": str(cover_url),
            "origin_detail_url": str(origin_detail_url),
            "missing_placeholder": True,
            "source_sha256": digest,
        }
    return {
        "cover_url": str(cover_url),
        "origin_detail_url": str(origin_detail_url),
        "data": data,
        "content_type": str(content_type or ""),
        "extension": extension,
        "sha256": digest,
        "watermark_checked": bool(reject_known_watermarks),
    }


def sync_remote_cover(
    *,
    catalog_id: int,
    source_id: str,
    title: str,
    author: str,
    detail_url: str,
    cover_url: str,
    cover_root: Path,
    cover_index_path: Path,
    allowed_hosts: Iterable[str] = (),
    allowed_host_suffixes: Iterable[str] = (),
    force: bool = False,
    request_bytes: Callable[[str], tuple[bytes, str]] | None = None,
) -> dict[str, Any]:
    """Persist one cover URL returned by a trusted search/download adapter."""
    catalog_id = int(catalog_id)
    source_id = str(source_id or "").strip()
    detail_url = str(detail_url or "").strip()
    cover_url = str(cover_url or "").strip()
    if not source_id or not detail_url or not cover_url:
        raise ValueError("搜索下载结果缺少封面绑定信息")
    parsed = urlparse(cover_url)
    host = (parsed.hostname or "").lower()
    trusted_hosts = {
        str(value).strip().lower() for value in allowed_hosts if str(value).strip()
    }
    trusted_suffixes = tuple(
        str(value).strip().lower()
        for value in allowed_host_suffixes
        if str(value).strip()
    )
    if (
        parsed.scheme != "https"
        or not host
        or not (
            host in trusted_hosts
            or any(host.endswith(suffix) for suffix in trusted_suffixes)
        )
    ):
        raise ValueError("封面图片不在当前书源的可信 HTTPS 主机内")

    cover_root = Path(cover_root).resolve()
    cover_index_path = Path(cover_index_path).resolve()
    cover_root.mkdir(parents=True, exist_ok=True)
    mysql_runtime = _mysql_runtime()
    if mysql_runtime is not None:
        existing = mysql_runtime.existing_cover(
            catalog_id=catalog_id,
            source_id=source_id,
            title=title,
            author=author,
        )
        if existing and not force:
            path = cover_root / str(existing["filename"])
            if path.is_file():
                return {
                    "status": "already_available",
                    "filename": str(existing["filename"]),
                    "sha256": str(existing["sha256"] or ""),
                    "cover_url": str(existing["cover_url"] or ""),
                }
        mysql_runtime.prepare_cover(
            catalog_id=catalog_id,
            source_id=source_id,
            title=title,
            author=author,
            detail_url=detail_url,
            cover_url=cover_url,
        )
    else:
        with sqlite3.connect(cover_index_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(COVER_SCHEMA)
            existing = conn.execute(
                """
                SELECT filename,sha256,cover_url FROM covers
                WHERE catalog_id=? AND source_id=? AND title=? AND author=?
                  AND status='done'
                """,
                (catalog_id, source_id, title, author),
            ).fetchone()
            if existing and not force:
                path = cover_root / str(existing["filename"])
                if path.is_file():
                    return {
                        "status": "already_available",
                        "filename": str(existing["filename"]),
                        "sha256": str(existing["sha256"] or ""),
                        "cover_url": str(existing["cover_url"] or ""),
                    }
            conn.execute(
                """
                INSERT INTO covers
                  (catalog_id,source_id,title,author,detail_url,cover_url,
                   status,updated_at)
                VALUES (?,?,?,?,?,?,'pending',?)
                ON CONFLICT(catalog_id) DO UPDATE SET
                  source_id=excluded.source_id,title=excluded.title,
                  author=excluded.author,detail_url=excluded.detail_url,
                  cover_url=excluded.cover_url,status='pending',
                  last_error=NULL,updated_at=excluded.updated_at
                """,
                (
                    catalog_id, source_id, title, author, detail_url,
                    cover_url, _now(),
                ),
            )
            conn.commit()

    try:
        data, content_type = (
            request_bytes(cover_url)
            if request_bytes is not None
            else _request_bytes(cover_url, referer=detail_url)
        )
        if len(data) < 1024 or len(data) > 12 * 1024 * 1024:
            raise ValueError(f"封面文件大小异常：{len(data)}")
        extension = _image_extension(data, content_type)
        _scan_cover_bytes(data, extension, cover_index_path)
        digest = hashlib.sha256(data).hexdigest()
        if is_missing_cover_placeholder_image(data, sha256=digest):
            if mysql_runtime is None:
                raise ValueError(
                    "SQLite 目录不支持缺失封面默认图切换，请迁移到 MySQL"
                )
            return _install_mysql_missing_default(
                runtime=mysql_runtime,
                cover_root=cover_root,
                catalog_id=catalog_id,
                source_digest=digest,
            )
        safe_source = re.sub(r"[^A-Za-z0-9_-]+", "-", source_id).strip("-")
        filename = f"{catalog_id}-{safe_source[:80]}-{digest[:16]}{extension}"
        target = cover_root / filename
        if not target.exists():
            temp = target.with_suffix(target.suffix + ".part")
            temp.write_bytes(data)
            temp.replace(target)
        if mysql_runtime is not None:
            mysql_runtime.persist_cover_result(
                catalog_id=catalog_id,
                cover_url=cover_url,
                filename=filename,
                sha256=digest,
            )
        else:
            with sqlite3.connect(cover_index_path, timeout=30) as conn:
                conn.executescript(COVER_SCHEMA)
                conn.execute(
                    """
                    UPDATE covers SET cover_url=?,filename=?,sha256=?,
                      status='done',attempts=attempts+1,last_error=NULL,
                      updated_at=? WHERE catalog_id=?
                    """,
                    (cover_url, filename, digest, _now(), catalog_id),
                )
                conn.commit()
        return {
            "status": "downloaded",
            "filename": filename,
            "sha256": digest,
            "cover_url": cover_url,
            "bytes": len(data),
        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        if mysql_runtime is not None:
            mysql_runtime.persist_cover_result(
                catalog_id=catalog_id,
                error=error,
            )
        else:
            with sqlite3.connect(cover_index_path, timeout=30) as conn:
                conn.executescript(COVER_SCHEMA)
                conn.execute(
                    """
                    UPDATE covers SET status='failed',attempts=attempts+1,
                      last_error=?,updated_at=? WHERE catalog_id=?
                    """,
                    (error, _now(), catalog_id),
                )
                conn.commit()
        raise


def superseded_cover_retiring_path(
    cover_root: Path,
    catalog_id: int,
    original_filename: str,
) -> Path:
    """Return the bounded staging path used by source-cover retirement."""

    cover_root = Path(cover_root).resolve()
    original_filename = str(original_filename or "")
    retiring_name = (
        f".retiring-source-{int(catalog_id)}-"
        f"{hashlib.sha256(original_filename.encode('utf-8')).hexdigest()[:16]}"
        f"{Path(original_filename).suffix.casefold()}"
    )
    path = (cover_root / retiring_name).resolve()
    if path.parent != cover_root:
        raise ValueError("旧封面暂存路径越界")
    return path


def retire_superseded_cover(
    *,
    runtime: Any,
    cover_root: Path,
    catalog_id: int,
    original_filename: str,
    replacement_filename: str,
) -> dict[str, Any]:
    """Atomically retire one predecessor and persist the cleanup marker."""

    catalog_id = int(catalog_id)
    original_filename = str(original_filename or "").strip()
    replacement_filename = str(replacement_filename or "").strip()
    cover_root = Path(cover_root).resolve()
    if (
        not replacement_filename
        or Path(replacement_filename).name != replacement_filename
    ):
        raise ValueError("当前封面文件名不安全")
    replacement_path = (cover_root / replacement_filename).resolve()
    if replacement_path.parent != cover_root or not replacement_path.is_file():
        raise FileNotFoundError("当前封面文件不存在，拒绝清理旧封面")

    if not original_filename or original_filename == replacement_filename:
        marked = runtime.mark_clean_cover_deleted(
            catalog_id=catalog_id,
            original_filename=original_filename,
            replacement_filename=replacement_filename,
        )
        return {
            "status": "no_superseded_original",
            "marked": bool(marked),
            "references": {},
        }
    if Path(original_filename).name != original_filename:
        raise ValueError("旧封面文件名不安全")
    original_path = (cover_root / original_filename).resolve()
    if original_path.parent != cover_root:
        raise ValueError("旧封面目标路径越界")

    references = runtime.clean_cover_original_references(
        filename=original_filename,
        catalog_id=catalog_id,
    )
    if any(references.values()):
        return {
            "status": "retained_referenced",
            "marked": False,
            "references": references,
        }

    retiring_path = superseded_cover_retiring_path(
        cover_root,
        catalog_id,
        original_filename,
    )
    if original_path.is_symlink() or retiring_path.is_symlink():
        raise ValueError("旧封面回收拒绝符号链接")
    if original_path.is_file():
        retiring_path.unlink(missing_ok=True)
        os.replace(original_path, retiring_path)
        status = "deleted"
    elif retiring_path.is_file():
        status = "deleted_recovered_staging"
    else:
        status = "already_missing"

    references = runtime.clean_cover_original_references(
        filename=original_filename,
        catalog_id=catalog_id,
    )
    if any(references.values()):
        if retiring_path.is_file() and not original_path.exists():
            os.replace(retiring_path, original_path)
        return {
            "status": "restored_referenced",
            "marked": False,
            "references": references,
        }
    retiring_path.unlink(missing_ok=True)
    marked = runtime.mark_clean_cover_deleted(
        catalog_id=catalog_id,
        original_filename=original_filename,
        replacement_filename=replacement_filename,
    )
    return {
        "status": status,
        "marked": bool(marked),
        "references": references,
    }


def sync_alternate_remote_cover(
    *,
    catalog_id: int,
    catalog_source_id: str,
    origin_source_id: str,
    title: str,
    author: str,
    origin_detail_url: str,
    cover_url: str,
    cover_root: Path,
    cover_index_path: Path,
    allowed_hosts: Iterable[str] = (),
    allowed_host_suffixes: Iterable[str] = (),
    request_bytes: Callable[[str], tuple[bytes, str]] | None = None,
    prepared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify first, then atomically replace a local watermarked cover."""

    runtime = _mysql_runtime()
    if runtime is None:
        raise RuntimeError("跨书源封面替换要求 MySQL 目录后端")
    prepared_cover = prepared or prepare_alternate_remote_cover(
        origin_detail_url=origin_detail_url,
        cover_url=cover_url,
        cover_index_path=cover_index_path,
        allowed_hosts=allowed_hosts,
        allowed_host_suffixes=allowed_host_suffixes,
        request_bytes=request_bytes,
    )
    if (
        str(prepared_cover.get("cover_url") or "") != str(cover_url)
        or str(prepared_cover.get("origin_detail_url") or "")
        != str(origin_detail_url)
    ):
        raise ValueError("预检封面与当前书源详情不一致")
    if prepared_cover.get("missing_placeholder"):
        return _install_mysql_missing_default(
            runtime=runtime,
            cover_root=Path(cover_root),
            catalog_id=int(catalog_id),
            source_digest=str(prepared_cover.get("source_sha256") or ""),
        )
    data = prepared_cover.get("data")
    if not isinstance(data, bytes):
        raise ValueError("预检封面缺少图片数据")
    content_type = str(prepared_cover.get("content_type") or "")
    extension = str(prepared_cover.get("extension") or "")
    digest = hashlib.sha256(data).hexdigest()
    if digest != str(prepared_cover.get("sha256") or ""):
        raise ValueError("预检封面哈希不一致")
    # The source identity belongs in MySQL metadata, not in the physical
    # filename.  A content-addressed name prevents the same verified image
    # from being stored once per matching remote source.
    filename = f"{int(catalog_id)}-cover-{digest[:16]}{extension}"
    cover_root = Path(cover_root).resolve()
    cover_root.mkdir(parents=True, exist_ok=True)
    target = (cover_root / filename).resolve()
    if target.parent != cover_root:
        raise ValueError("备用封面目标路径越界")
    created = False
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(target)
        created = True
    try:
        persisted = runtime.persist_alternate_cover_result(
            catalog_id=int(catalog_id),
            catalog_source_id=str(catalog_source_id),
            origin_source_id=str(origin_source_id),
            title=str(title),
            author=str(author),
            origin_detail_url=str(origin_detail_url),
            cover_url=str(cover_url),
            filename=filename,
            sha256=digest,
            bytes_count=len(data),
            content_type=(
                content_type.split(";", 1)[0].strip()
                or {
                    ".jpg": "image/jpeg",
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }[extension]
            ),
        )
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise
    original_filename = str(persisted.get("original_filename") or "")
    try:
        cleanup = retire_superseded_cover(
            runtime=runtime,
            cover_root=cover_root,
            catalog_id=int(catalog_id),
            original_filename=original_filename,
            replacement_filename=filename,
        )
    except Exception as exc:
        cleanup = {
            "status": "cleanup_deferred",
            "marked": False,
            "references": {},
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }
    return {
        **persisted,
        "status": "downloaded",
        "sha256": digest,
        "cover_url": cover_url,
        "bytes": len(data),
        "deleted_original": cleanup.get("status") in {
            "deleted", "deleted_recovered_staging"
        },
        "deletion_references": cleanup.get("references") or {},
        "cleanup": cleanup,
    }


def _fanqie_page_metadata(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    page_title = ""
    page_author = ""
    image_urls: list[str] = []
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.string or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        headline = str(payload.get("headline") or payload.get("title") or "")
        if headline and not page_title:
            page_title = re.split(
                r"(?:完整版|免费阅读|小说_|小说$)", headline, maxsplit=1
            )[0].strip()
        authors = payload.get("author") or []
        if isinstance(authors, dict):
            authors = [authors]
        for author in authors:
            if isinstance(author, dict) and author.get("name"):
                page_author = str(author["name"]).strip()
                break
        images = payload.get("image") or payload.get("images") or []
        if isinstance(images, str):
            images = [images]
        image_urls.extend(str(value) for value in images if value)
    cover_node = soup.select_one("img.book-cover-img[alt]")
    if cover_node and cover_node.get("alt"):
        page_title = str(cover_node["alt"]).strip()
    state_match = re.search(
        r'"thumbUrl"\s*:\s*"(?P<url>(?:\\.|[^"])*)"', html
    )
    if state_match:
        try:
            image_urls.append(
                json.loads(f'"{state_match.group("url")}"')
            )
        except (ValueError, json.JSONDecodeError):
            pass
    return {
        "title": page_title,
        "author": page_author,
        "image_urls": list(dict.fromkeys(image_urls)),
    }


def _fanqie_image_candidates(image_url: str) -> list[str]:
    parsed = urlparse(str(image_url or ""))
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not FANQIE_IMAGE_HOST_PATTERN.fullmatch(hostname):
        return []
    candidates: list[str] = []
    host_match = re.match(r"^p(?P<number>\d+)-novel-sign\.byteimg\.com$", hostname)
    if host_match:
        for number in (host_match.group("number"), "3", "6"):
            candidates.append(
                urlunparse(
                    parsed._replace(
                        netloc=f"p{number}-novel.byteimg.com",
                        query="",
                    )
                )
            )
    candidates.append(image_url)
    return list(dict.fromkeys(candidates))


def sync_fanqie_cover(
    *,
    catalog_id: int,
    book_id: str,
    title: str,
    author: str,
    catalog_source_id: str = "",
    cover_root: Path,
    cover_index_path: Path,
    force: bool = False,
    allow_title_alias: bool = False,
) -> dict[str, Any]:
    """Fetch the exact Fanqie cover and bind it to one catalog identity."""
    catalog_id = int(catalog_id)
    book_id = str(book_id).strip()
    if not re.fullmatch(r"\d{10,24}", book_id):
        raise ValueError("番茄封面同步的作品 ID 无效")
    fanqie_source_id = f"fanqie-{book_id}"
    source_id = str(catalog_source_id or fanqie_source_id).strip()
    if not source_id:
        raise ValueError("番茄封面同步缺少目录来源标识")
    detail_url = f"https://fanqienovel.com/page/{book_id}"
    cover_root = Path(cover_root).resolve()
    cover_index_path = Path(cover_index_path).resolve()
    cover_root.mkdir(parents=True, exist_ok=True)
    mysql_runtime = _mysql_runtime()
    if mysql_runtime is not None:
        existing = mysql_runtime.existing_cover(
            catalog_id=catalog_id,
            source_id=source_id,
            title=title,
            author=author,
        )
        if existing and not force:
            path = cover_root / str(existing["filename"])
            if path.is_file():
                return {
                    "status": "already_available",
                    "filename": str(existing["filename"]),
                    "sha256": str(existing["sha256"] or ""),
                    "cover_url": str(existing["cover_url"] or ""),
                }
        mysql_runtime.prepare_cover(
            catalog_id=catalog_id,
            source_id=source_id,
            title=title,
            author=author,
            detail_url=detail_url,
        )
    else:
        with sqlite3.connect(cover_index_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(COVER_SCHEMA)
            existing = conn.execute(
                """
                SELECT filename,sha256,cover_url FROM covers
                WHERE catalog_id=? AND source_id=? AND title=? AND author=?
                  AND status='done'
                """,
                (catalog_id, source_id, title, author),
            ).fetchone()
            if existing and not force:
                path = cover_root / str(existing["filename"])
                if path.is_file():
                    return {
                        "status": "already_available",
                        "filename": str(existing["filename"]),
                        "sha256": str(existing["sha256"] or ""),
                        "cover_url": str(existing["cover_url"] or ""),
                    }
            conn.execute(
                """
                INSERT INTO covers
                  (catalog_id,source_id,title,author,detail_url,status,updated_at)
                VALUES (?,?,?,?,?,'pending',?)
                ON CONFLICT(catalog_id) DO UPDATE SET
                  source_id=excluded.source_id,title=excluded.title,
                  author=excluded.author,detail_url=excluded.detail_url,
                  status='pending',last_error=NULL,updated_at=excluded.updated_at
                """,
                (catalog_id, source_id, title, author, detail_url, _now()),
            )
            conn.commit()

    try:
        metadata = _fanqie_page_metadata(_request_html(detail_url))
        expected_title = _identity_text(title)
        page_title = _identity_text(str(metadata.get("title") or ""))
        if (
            not expected_title
            or (
                expected_title not in page_title
                and not allow_title_alias
            )
        ):
            raise ValueError(
                f"番茄详情页书名不一致：{metadata.get('title') or '空'}"
            )
        expected_author = _identity_text(author)
        page_author = _identity_text(str(metadata.get("author") or ""))
        if expected_author and page_author and expected_author != page_author:
            raise ValueError(
                f"番茄详情页作者不一致：{metadata.get('author') or '空'}"
            )
        candidates = [
            candidate
            for image_url in metadata.get("image_urls") or []
            for candidate in _fanqie_image_candidates(str(image_url))
        ]
        if not candidates:
            raise ValueError("番茄详情页没有可验证的真实封面图")
        last_error = ""
        for candidate in list(dict.fromkeys(candidates)):
            try:
                data, content_type = _request_bytes(
                    candidate, referer=detail_url
                )
                if len(data) < 1024 or len(data) > 12 * 1024 * 1024:
                    raise ValueError(f"封面文件大小异常：{len(data)}")
                extension = _image_extension(data, content_type)
                _scan_cover_bytes(data, extension, cover_index_path)
                digest = hashlib.sha256(data).hexdigest()
                if is_missing_cover_placeholder_image(data, sha256=digest):
                    if mysql_runtime is None:
                        raise ValueError(
                            "SQLite 目录不支持缺失封面默认图切换，请迁移到 MySQL"
                        )
                    return _install_mysql_missing_default(
                        runtime=mysql_runtime,
                        cover_root=cover_root,
                        catalog_id=catalog_id,
                        source_digest=digest,
                    )
                filename = (
                    f"{catalog_id}-{fanqie_source_id}-{digest[:16]}{extension}"
                )
                target = cover_root / filename
                if not target.exists():
                    temp = target.with_suffix(target.suffix + ".part")
                    temp.write_bytes(data)
                    temp.replace(target)
                if mysql_runtime is not None:
                    mysql_runtime.persist_cover_result(
                        catalog_id=catalog_id,
                        cover_url=candidate,
                        filename=filename,
                        sha256=digest,
                    )
                else:
                    with sqlite3.connect(cover_index_path, timeout=30) as conn:
                        conn.executescript(COVER_SCHEMA)
                        conn.execute(
                            """
                            UPDATE covers SET cover_url=?,filename=?,sha256=?,
                              status='done',attempts=attempts+1,last_error=NULL,
                              updated_at=? WHERE catalog_id=?
                            """,
                            (candidate, filename, digest, _now(), catalog_id),
                        )
                        conn.commit()
                return {
                    "status": "downloaded",
                    "filename": filename,
                    "sha256": digest,
                    "cover_url": candidate,
                    "bytes": len(data),
                }
            except Exception as exc:  # try the next trusted CDN variant
                last_error = f"{type(exc).__name__}: {str(exc)[:240]}"
        raise ValueError(last_error or "番茄封面下载失败")
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        if mysql_runtime is not None:
            mysql_runtime.persist_cover_result(
                catalog_id=catalog_id,
                error=error,
            )
        else:
            with sqlite3.connect(cover_index_path, timeout=30) as conn:
                conn.executescript(COVER_SCHEMA)
                conn.execute(
                    """
                    UPDATE covers SET status='failed',attempts=attempts+1,
                      last_error=?,updated_at=? WHERE catalog_id=?
                    """,
                    (error, _now(), catalog_id),
                )
                conn.commit()
        raise
