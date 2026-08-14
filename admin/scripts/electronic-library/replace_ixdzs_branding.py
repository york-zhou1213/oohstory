#!/usr/bin/env python3
"""Safely normalize source-specific notices in NAS library TXT files.

The command is a dry-run unless ``--apply`` is supplied.  Mutable legacy
copies below ``书籍/`` can be changed after they are backed up.  Immutable
content-addressed objects below ``body/`` are reported but make ``--apply``
fail closed because changing them also requires a coordinated object-key and
MySQL metadata update.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("/srv/oohstory/library")
OLD_NOTICE = (
    "爱下电子书Txt版阅读,下载和分享更多电子书请访问，"
    "简体:https://ixdzs8.com,繁体:https://ixdzs8.tw,"
    "E-mail:support@ixdzs.com"
)
NEW_NOTICE = "reader.example.com，好故事电子书"
ALLOWED_AREAS = ("body", "书籍")
BODY_OBJECT_RE = re.compile(r"^[0-9a-f]{64}\.txt$")
MYSQL_LOCK_NAME = "oohstory:replace-ixdzs-branding:v1"
MYSQL_ARCHIVE_LOCK_NAME = "oohstory:archive-old-branding-bodies:v1"
PRODUCTION_ENV_FILE = Path("/etc/oohstory-admin/library-infrastructure.env")
DEFAULT_CATALOG_SOURCE_PREFIX = "ixdzs-"
DEFAULT_PROFILE = "ixdzs"
PROFILE_SOURCE_PREFIXES = {
    "ixdzs": "ixdzs-",
    "shubaow": "shubaow-",
    "xbiquge": "xbiquge-",
}
SHUBAOW_DETAIL_URL_RE = re.compile(
    r"https://(?:www\.)?shubaow\.org/book[0-9]+\.html"
)
XBIQUGE_SOURCE_HEADER_RE = re.compile(
    r"来源[：:]\s*https://(?:www\.)?xbiquge\.info/\S+"
)
CANONICAL_TXT80_ALIAS = Path(
    "/srv/oohstory/library"
)


class SafetyError(RuntimeError):
    """Raised when applying would cross a safety boundary."""


@dataclass(frozen=True)
class EncodingSpec:
    name: str
    codec: str
    bom: bytes = b""

    def decode(self, data: bytes) -> str:
        payload = data
        if self.bom:
            if not data.startswith(self.bom):
                raise UnicodeDecodeError(
                    self.codec, data, 0, min(len(data), len(self.bom)), "missing BOM"
                )
            payload = data[len(self.bom) :]
        return payload.decode(self.codec, errors="strict")

    def encode(self, text: str) -> bytes:
        return self.bom + text.encode(self.codec, errors="strict")


@dataclass(frozen=True)
class Candidate:
    path: Path
    relative_path: str
    area: str
    count: int
    encoding: str
    before_sha256: str
    after_sha256: str
    device: int
    inode: int
    mode: int
    atime_ns: int
    mtime_ns: int
    size: int
    profile: str = DEFAULT_PROFILE


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    relative_path: str
    area: str
    sha256: str
    device: int
    inode: int
    mode: int
    atime_ns: int
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class MySQLMigrationPlan:
    body: Candidate
    legacy: Candidate | FileSnapshot
    catalog_id: int
    asset_id: int
    old_object_key: str
    new_object_key: str
    changed_body: bytes
    old_storage_format: str
    legacy_already_normalized: bool


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _existing_components_have_no_symlink(path: Path) -> None:
    current = Path(path.anchor or "/")
    for part in path.absolute().parts[1:]:
        current /= part
        if not current.exists() and not current.is_symlink():
            break
        if current.is_symlink():
            raise SafetyError(f"路径包含符号链接: {current}")


def _validated_root(root: Path) -> Path:
    root = root.expanduser().absolute()
    _existing_components_have_no_symlink(root)
    try:
        metadata = root.lstat()
    except FileNotFoundError as exc:
        raise SafetyError(f"书库根目录不存在: {root}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SafetyError(f"书库根目录必须是真实目录: {root}")
    return root.resolve(strict=True)


def _read_regular_file(path: Path, allowed_root: Path) -> tuple[bytes, os.stat_result]:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise SafetyError(f"文件越出允许目录: {path}") from exc
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SafetyError(f"不是普通文件: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as reader:
            data = reader.read()
    finally:
        os.close(descriptor)
    return data, metadata


def _encoding_candidates(data: bytes) -> list[EncodingSpec]:
    if data.startswith(b"\xef\xbb\xbf"):
        return [EncodingSpec("utf-8-sig", "utf-8", b"\xef\xbb\xbf")]
    if data.startswith(b"\xff\xfe"):
        return [EncodingSpec("utf-16-le", "utf-16-le", b"\xff\xfe")]
    if data.startswith(b"\xfe\xff"):
        return [EncodingSpec("utf-16-be", "utf-16-be", b"\xfe\xff")]
    candidates = [
        EncodingSpec("utf-8", "utf-8"),
        EncodingSpec("gb18030", "gb18030"),
    ]
    if len(data) % 2 == 0 and data.count(b"\x00") > max(8, len(data) // 10):
        candidates.extend(
            [
                EncodingSpec("utf-16-le", "utf-16-le"),
                EncodingSpec("utf-16-be", "utf-16-be"),
            ]
        )
    return candidates


def _validate_profile(profile: str) -> str:
    profile = str(profile or "").strip().lower()
    if profile not in PROFILE_SOURCE_PREFIXES:
        raise SafetyError(f"不支持的替换规则: {profile}")
    return profile


def _strip_shubaow_source_header(text: str) -> tuple[str, int]:
    """Remove only the generated Shubaow source field near the TXT header."""

    lines = text.splitlines(keepends=True)
    for index in range(min(len(lines), 12)):
        value = lines[index].strip()
        same_line = re.fullmatch(
            r"来源[：:]\s*(https://(?:www\.)?shubaow\.org/book[0-9]+\.html)",
            value,
        )
        if same_line:
            del lines[index]
            return "".join(lines), 1
        if value not in {"来源：", "来源:"} or index + 1 >= len(lines):
            continue
        if SHUBAOW_DETAIL_URL_RE.fullmatch(lines[index + 1].strip()):
            del lines[index : index + 2]
            return "".join(lines), 1
    return text, 0


def _strip_xbiquge_source_header(text: str) -> tuple[str, int]:
    """Remove only the generated xbiquge source field near the TXT header."""

    lines = text.splitlines(keepends=True)
    for index in range(min(len(lines), 12)):
        if XBIQUGE_SOURCE_HEADER_RE.fullmatch(lines[index].strip()):
            del lines[index]
            return "".join(lines), 1
    return text, 0


def _replacement(
    data: bytes,
    profile: str = DEFAULT_PROFILE,
) -> tuple[bytes, int, str] | None:
    profile = _validate_profile(profile)
    matches: list[tuple[bytes, int, str]] = []
    for encoding in _encoding_candidates(data):
        try:
            text = encoding.decode(data)
        except UnicodeDecodeError:
            continue
        if profile == "shubaow":
            changed_text, count = _strip_shubaow_source_header(text)
        elif profile == "xbiquge":
            changed_text, count = _strip_xbiquge_source_header(text)
        else:
            count = text.count(OLD_NOTICE)
            changed_text = text.replace(OLD_NOTICE, NEW_NOTICE)
        if count:
            changed = encoding.encode(changed_text)
            matches.append((changed, count, encoding.name))
    if not matches:
        return None
    if len(matches) != 1:
        raise SafetyError("文件编码识别不唯一，拒绝替换")
    return matches[0]


def _validate_body_object(candidate: Candidate) -> str | None:
    parts = Path(candidate.relative_path).parts
    if len(parts) != 4 or parts[0] != "body":
        return "正文对象路径不是 body/<2>/<2>/<sha256>.txt"
    first, second, filename = parts[1:]
    if not BODY_OBJECT_RE.fullmatch(filename):
        return "正文对象文件名不是 64 位小写 SHA-256"
    digest = filename[:-4]
    if first != digest[:2] or second != digest[2:4]:
        return "正文对象分片目录与文件名 SHA-256 不一致"
    if candidate.before_sha256 != digest:
        return "正文对象内容 SHA-256 与内容寻址文件名不一致"
    return None


def scan(root: Path, *, profile: str = DEFAULT_PROFILE) -> dict[str, Any]:
    profile = _validate_profile(profile)
    root = _validated_root(root)
    candidates: list[Candidate] = []
    rejected: list[dict[str, str]] = []
    files_seen = 0
    bytes_scanned = 0

    for area in ALLOWED_AREAS:
        area_root = root / area
        if not area_root.exists():
            continue
        if area_root.is_symlink() or not area_root.is_dir():
            rejected.append({"path": area, "reason": "允许目录必须是真实目录"})
            continue
        allowed_root = area_root.resolve(strict=True)
        try:
            allowed_root.relative_to(root)
        except ValueError:
            rejected.append({"path": area, "reason": "允许目录越出书库根目录"})
            continue
        for directory, dirnames, filenames in os.walk(area_root, followlinks=False):
            directory_path = Path(directory)
            safe_dirs = []
            for name in dirnames:
                child = directory_path / name
                if child.is_symlink():
                    rejected.append(
                        {
                            "path": child.relative_to(root).as_posix(),
                            "reason": "拒绝符号链接目录",
                        }
                    )
                else:
                    safe_dirs.append(name)
            dirnames[:] = safe_dirs
            for name in filenames:
                if not name.endswith(".txt"):
                    continue
                path = directory_path / name
                relative = path.relative_to(root).as_posix()
                try:
                    metadata = path.lstat()
                    if stat.S_ISLNK(metadata.st_mode):
                        raise SafetyError("拒绝符号链接文件")
                    data, metadata = _read_regular_file(path, allowed_root)
                    files_seen += 1
                    bytes_scanned += len(data)
                    result = _replacement(data, profile)
                    if result is None:
                        continue
                    changed, count, encoding = result
                    candidate = Candidate(
                        path=path,
                        relative_path=relative,
                        area=area,
                        count=count,
                        encoding=encoding,
                        before_sha256=_sha256(data),
                        after_sha256=_sha256(changed),
                        device=metadata.st_dev,
                        inode=metadata.st_ino,
                        mode=stat.S_IMODE(metadata.st_mode),
                        atime_ns=metadata.st_atime_ns,
                        mtime_ns=metadata.st_mtime_ns,
                        size=metadata.st_size,
                        profile=profile,
                    )
                    if area == "body":
                        problem = _validate_body_object(candidate)
                        if problem:
                            rejected.append({"path": relative, "reason": problem})
                    candidates.append(candidate)
                except (OSError, SafetyError) as exc:
                    rejected.append({"path": relative, "reason": str(exc)})

    return {
        "root": root,
        "candidates": candidates,
        "rejected": rejected,
        "files_seen": files_seen,
        "bytes_scanned": bytes_scanned,
        "replacement_profile": profile,
    }


def _validate_source_prefix(prefix: str) -> str:
    prefix = str(prefix or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,63}", prefix):
        raise SafetyError("catalog source prefix 格式无效")
    return prefix


def _catalog_scope_rows(
    pool: Any,
    source_prefix: str,
    catalog_id: int = 0,
) -> list[dict[str, Any]]:
    source_prefix = _validate_source_prefix(source_prefix)
    with pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            source_filter = (
                "id=%s" if int(catalog_id) > 0 else "source_id LIKE %s"
            )
            params: tuple[Any, ...] = (
                (int(catalog_id),)
                if int(catalog_id) > 0
                else (source_prefix + "%",)
            )
            cursor.execute(
                """
                SELECT id AS catalog_id, body_object_key, legacy_output_path
                FROM books
                WHERE status='done'
                  AND """
                + source_filter
                + """
                  AND NULLIF(TRIM(body_object_key), '') IS NOT NULL
                  AND NULLIF(TRIM(legacy_output_path), '') IS NOT NULL
                ORDER BY id
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]


def _scoped_candidate(
    root: Path,
    *,
    area: str,
    path: Path,
    profile: str = DEFAULT_PROFILE,
) -> tuple[Candidate | None, FileSnapshot, int]:
    profile = _validate_profile(profile)
    allowed_root = (root / area).resolve(strict=True)
    if path.suffix != ".txt":
        raise SafetyError(f"catalog 路径不是 .txt: {path}")
    _existing_components_have_no_symlink(path.absolute())
    data, metadata = _read_regular_file(path, allowed_root)
    digest = _sha256(data)
    resolved_path = path.resolve(strict=True)
    relative = resolved_path.relative_to(root).as_posix()
    if area == "body":
        parts = Path(relative).parts
        if (
            len(parts) != 4
            or parts[0] != "body"
            or not BODY_OBJECT_RE.fullmatch(parts[3])
        ):
            raise SafetyError(
                "catalog body 路径不是 body/<2>/<2>/<sha256>.txt"
            )
        filename_digest = Path(parts[3]).stem
        if (
            parts[1] != filename_digest[:2]
            or parts[2] != filename_digest[2:4]
            or digest != filename_digest
        ):
            raise SafetyError("catalog body 路径或内容不符合 SHA-256 内容寻址")
    snapshot = FileSnapshot(
        path=resolved_path,
        relative_path=relative,
        area=area,
        sha256=digest,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=stat.S_IMODE(metadata.st_mode),
        atime_ns=metadata.st_atime_ns,
        mtime_ns=metadata.st_mtime_ns,
        size=metadata.st_size,
    )
    replacement = _replacement(data, profile)
    if replacement is None:
        return None, snapshot, len(data)
    changed, count, encoding = replacement
    candidate = Candidate(
        path=resolved_path,
        relative_path=relative,
        area=area,
        count=count,
        encoding=encoding,
        before_sha256=digest,
        after_sha256=_sha256(changed),
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=stat.S_IMODE(metadata.st_mode),
        atime_ns=metadata.st_atime_ns,
        mtime_ns=metadata.st_mtime_ns,
        size=metadata.st_size,
        profile=profile,
    )
    return candidate, snapshot, len(data)


def scan_catalog_scope(
    root: Path,
    *,
    pool: Any,
    source_prefix: str = DEFAULT_CATALOG_SOURCE_PREFIX,
    catalog_id: int = 0,
    profile: str = DEFAULT_PROFILE,
) -> dict[str, Any]:
    """Read only MySQL-selected body/legacy pairs instead of the full corpus."""
    root = _validated_root(root)
    profile = _validate_profile(profile)
    body_root = root / "body"
    books_root = root / "书籍"
    for allowed in (body_root, books_root):
        if allowed.is_symlink() or not allowed.is_dir():
            raise SafetyError(f"catalog scoped scan 目录无效: {allowed}")
        allowed.resolve(strict=True).relative_to(root)

    rows = _catalog_scope_rows(pool, source_prefix, catalog_id)
    scoped_paths: dict[tuple[str, Path], None] = {}
    catalog_mappings: list[dict[str, Any]] = []
    for row in rows:
        raw_key = str(row.get("body_object_key") or "").strip()
        key_path = Path(raw_key)
        if (
            not raw_key
            or key_path.is_absolute()
            or ".." in key_path.parts
            or not key_path.parts
        ):
            raise SafetyError(
                f"catalog_id={int(row.get('catalog_id') or 0)} body_object_key 无效"
            )
        legacy_path = _resolve_legacy_path(
            root,
            str(row.get("legacy_output_path") or ""),
            allow_canonical_alias=True,
        )
        if key_path.parts[0] == "body":
            source_area = "body"
            source_path = root / key_path
            storage_format = "content_addressed"
        elif key_path.parts[0] == "书籍":
            source_area = "书籍"
            source_path = (root / key_path).resolve(strict=True)
            if source_path != legacy_path:
                raise SafetyError(
                    f"catalog_id={int(row.get('catalog_id') or 0)} "
                    "legacy object_key 与 legacy_output_path 不是同一文件"
                )
            storage_format = "legacy_key"
        else:
            raise SafetyError(
                f"catalog_id={int(row.get('catalog_id') or 0)} "
                "body_object_key 既不是 body/ 也不是 书籍/"
            )
        scoped_paths[(source_area, source_path)] = None
        scoped_paths[("书籍", legacy_path)] = None
        catalog_mappings.append(
            {
                "catalog_id": int(row.get("catalog_id") or 0),
                "old_object_key": raw_key,
                "old_storage_format": storage_format,
                "source_path": source_path,
                "legacy_path": legacy_path,
            }
        )

    candidates: list[Candidate] = []
    file_snapshots: dict[Path, FileSnapshot] = {}
    bytes_scanned = 0
    for area, path in scoped_paths:
        try:
            candidate, snapshot, size = _scoped_candidate(
                root, area=area, path=path, profile=profile
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise SafetyError(f"catalog scoped 文件校验失败: {path}: {exc}") from exc
        bytes_scanned += size
        file_snapshots[snapshot.path] = snapshot
        if candidate is not None:
            candidates.append(candidate)
    return {
        "root": root,
        "candidates": candidates,
        "rejected": [],
        "files_seen": len(scoped_paths),
        "bytes_scanned": bytes_scanned,
        "catalog_rows": len(rows),
        "catalog_source_prefix": _validate_source_prefix(source_prefix),
        "catalog_scoped": True,
        "catalog_mappings": catalog_mappings,
        "catalog_file_snapshots": file_snapshots,
        "replacement_profile": profile,
    }


def _atomic_write(
    path: Path,
    data: bytes,
    *,
    mode: int,
    atime_ns: int,
    mtime_ns: int,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as writer:
            writer.write(data)
            writer.flush()
            os.fchmod(writer.fileno(), mode)
            os.fsync(writer.fileno())
        os.replace(temporary_name, path)
        os.utime(path, ns=(atime_ns, mtime_ns), follow_symlinks=False)
        _fsync_directory(path.parent)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_manifest(path: Path, payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".manifest.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as writer:
            writer.write(data)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary_name, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _default_backup_dir(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return root / ".oohstory-branding-backups" / stamp


def _prepare_backup_dir(root: Path, requested: Path | None) -> Path:
    backup_dir = (requested or _default_backup_dir(root)).expanduser().absolute()
    _existing_components_have_no_symlink(backup_dir)
    for area in ALLOWED_AREAS:
        allowed = (root / area).resolve(strict=False)
        try:
            backup_dir.resolve(strict=False).relative_to(allowed)
        except ValueError:
            continue
        raise SafetyError("备份目录不能位于 body/ 或 书籍/ 内")
    if backup_dir.exists():
        raise SafetyError(f"备份目录已存在，拒绝覆盖: {backup_dir}")
    backup_dir.mkdir(parents=True, mode=0o700)
    _fsync_directory(backup_dir.parent)
    return backup_dir.resolve(strict=True)


def _candidate_data(candidate: Candidate, root: Path) -> tuple[bytes, bytes, os.stat_result]:
    allowed_root = (root / candidate.area).resolve(strict=True)
    data, metadata = _read_regular_file(candidate.path, allowed_root)
    if (
        metadata.st_dev != candidate.device
        or metadata.st_ino != candidate.inode
        or metadata.st_size != candidate.size
        or _sha256(data) != candidate.before_sha256
    ):
        raise SafetyError(f"扫描后文件发生变化: {candidate.relative_path}")
    result = _replacement(data, candidate.profile)
    if result is None:
        raise SafetyError(f"扫描后目标文本消失: {candidate.relative_path}")
    changed, count, encoding = result
    if count != candidate.count or encoding != candidate.encoding:
        raise SafetyError(f"扫描后匹配结果变化: {candidate.relative_path}")
    return data, changed, metadata


def _snapshot_data(snapshot: FileSnapshot, root: Path) -> tuple[bytes, os.stat_result]:
    allowed_root = (root / snapshot.area).resolve(strict=True)
    data, metadata = _read_regular_file(snapshot.path, allowed_root)
    if (
        metadata.st_dev != snapshot.device
        or metadata.st_ino != snapshot.inode
        or metadata.st_size != snapshot.size
        or _sha256(data) != snapshot.sha256
    ):
        raise SafetyError(f"扫描后文件发生变化: {snapshot.relative_path}")
    return data, metadata


def _resolve_legacy_path(
    root: Path,
    raw_path: str,
    *,
    allow_canonical_alias: bool = False,
) -> Path:
    value = Path(str(raw_path or "").strip()).expanduser()
    if not str(value):
        raise SafetyError("数据库 legacy_output_path 为空")
    if value.is_absolute() and allow_canonical_alias:
        try:
            alias_relative = value.absolute().relative_to(
                CANONICAL_TXT80_ALIAS
            )
        except ValueError:
            path = value
        else:
            path = root / alias_relative
    else:
        path = value if value.is_absolute() else root / value
    books_root = (root / "书籍").resolve(strict=True)
    _existing_components_have_no_symlink(path.absolute())
    try:
        path.resolve(strict=True).relative_to(books_root)
    except (FileNotFoundError, ValueError) as exc:
        raise SafetyError("legacy_output_path 不在 root/书籍 或文件不存在") from exc
    if path.is_symlink() or not path.is_file():
        raise SafetyError("legacy_output_path 必须是真实普通文件")
    return path.resolve(strict=True)


def _lock_value(row: Any, key: str) -> int:
    if isinstance(row, dict):
        return int(row.get(key) or 0)
    if isinstance(row, (tuple, list)) and row:
        return int(row[0] or 0)
    return 0


def _select_body_mapping(cursor: Any, object_key: str) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
            b.id AS catalog_id,
            b.body_object_key,
            b.legacy_output_path,
            b.sha256 AS book_sha256,
            b.bytes AS book_bytes,
            a.id AS asset_id,
            a.object_key AS asset_object_key,
            a.sha256 AS asset_sha256,
            a.bytes AS asset_bytes
        FROM books AS b
        LEFT JOIN object_assets AS a
          ON a.catalog_id=b.id AND a.asset_type='body'
        WHERE b.body_object_key=%s
        FOR UPDATE
        """,
        (object_key,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _validate_mapping(
    row: dict[str, Any],
    body: Candidate,
    legacy_by_path: dict[Path, Candidate],
    snapshot_by_path: dict[Path, FileSnapshot],
    root: Path,
    *,
    allow_canonical_alias: bool = False,
    old_storage_format: str = "content_addressed",
) -> MySQLMigrationPlan:
    old_key = body.relative_path
    if str(row.get("body_object_key") or "") != old_key:
        raise SafetyError("books.body_object_key 在锁定后发生变化")
    asset_id = int(row.get("asset_id") or 0)
    if asset_id <= 0 or str(row.get("asset_object_key") or "") != old_key:
        raise SafetyError("body 对象缺少一致的 object_assets 映射")
    for field in ("book_sha256", "asset_sha256"):
        value = str(row.get(field) or "").strip().lower()
        if value and value != body.before_sha256:
            raise SafetyError(f"{field} 与旧正文对象 SHA-256 不一致")
    for field in ("book_bytes", "asset_bytes"):
        value = int(row.get(field) or 0)
        if value and value != body.size:
            raise SafetyError(f"{field} 与旧正文对象大小不一致")

    legacy_path = _resolve_legacy_path(
        root,
        str(row.get("legacy_output_path") or ""),
        allow_canonical_alias=allow_canonical_alias,
    )
    legacy = legacy_by_path.get(legacy_path)
    body_original, body_changed, _ = _candidate_data(body, root)
    legacy_already_normalized = False
    if legacy is None:
        legacy_snapshot = snapshot_by_path.get(legacy_path)
        if legacy_snapshot is None:
            raise SafetyError("legacy_output_path 不在 catalog scoped 扫描快照中")
        legacy_original, _ = _snapshot_data(legacy_snapshot, root)
        if legacy_original != body_changed:
            raise SafetyError(
                "legacy TXT 既不含旧文案，也不等于 body 的替换后完整内容"
            )
        legacy = legacy_snapshot
        legacy_changed = legacy_original
        legacy_already_normalized = True
    elif legacy.path == body.path:
        legacy_original, legacy_changed = body_original, body_changed
    else:
        legacy_original, legacy_changed, _ = _candidate_data(legacy, root)
    if legacy_already_normalized:
        if (
            body_changed != legacy_original
            or body.after_sha256 != legacy.sha256
        ):
            raise SafetyError("已归一化 legacy TXT 与新 body 内容不一致")
    else:
        if (
            body_original != legacy_original
            or body.before_sha256 != legacy.before_sha256
        ):
            raise SafetyError("legacy TXT 内容与旧 body 对象不一致")
        if (
            body_changed != legacy_changed
            or body.after_sha256 != legacy.after_sha256
        ):
            raise SafetyError("legacy TXT 替换结果与新 body 对象不一致")
    new_key = legacy.relative_path
    if not new_key.startswith("书籍/"):
        raise SafetyError("新正文对象键必须指向电子书库根目录的书籍/分类文件")
    return MySQLMigrationPlan(
        body=body,
        legacy=legacy,
        catalog_id=int(row["catalog_id"]),
        asset_id=asset_id,
        old_object_key=old_key,
        new_object_key=new_key,
        changed_body=body_changed,
        old_storage_format=old_storage_format,
        legacy_already_normalized=legacy_already_normalized,
    )


def _update_mysql_mapping(cursor: Any, plan: MySQLMigrationPlan) -> None:
    cursor.execute(
        """
        UPDATE books
        SET body_object_key=%s,
            sha256=%s,
            bytes=%s,
            row_version=row_version+1,
            updated_at=UTC_TIMESTAMP(6)
        WHERE id=%s AND body_object_key=%s
        """,
        (
            plan.new_object_key,
            plan.body.after_sha256,
            len(plan.changed_body),
            plan.catalog_id,
            plan.old_object_key,
        ),
    )
    if int(cursor.rowcount) != 1:
        raise SafetyError("books 条件更新失败，事务已拒绝")
    cursor.execute(
        """
        UPDATE object_assets
        SET object_key=%s,
            sha256=%s,
            bytes=%s
        WHERE id=%s AND catalog_id=%s AND asset_type='body'
          AND object_key=%s
        """,
        (
            plan.new_object_key,
            plan.body.after_sha256,
            len(plan.changed_body),
            plan.asset_id,
            plan.catalog_id,
            plan.old_object_key,
        ),
    )
    if int(cursor.rowcount) != 1:
        raise SafetyError("object_assets 条件更新失败，事务已拒绝")


def _restore_legacy_files(
    plans: list[MySQLMigrationPlan],
    backup_dir: Path,
    changed_paths: set[Path],
) -> list[str]:
    failures: list[str] = []
    for plan in reversed(plans):
        if plan.legacy.path not in changed_paths:
            continue
        backup = backup_dir / "files" / Path(plan.legacy.relative_path)
        try:
            original = backup.read_bytes()
            if _sha256(original) != plan.legacy.before_sha256:
                raise SafetyError("备份 SHA-256 不一致")
            _atomic_write(
                plan.legacy.path,
                original,
                mode=plan.legacy.mode,
                atime_ns=plan.legacy.atime_ns,
                mtime_ns=plan.legacy.mtime_ns,
            )
        except Exception as exc:
            failures.append(f"{plan.legacy.relative_path}: {exc}")
    return failures


def apply_with_mysql(
    scan_result: dict[str, Any],
    backup_dir: Path | None,
    *,
    pool: Any,
    catalog_backend: str,
) -> dict[str, Any]:
    if str(catalog_backend).strip().lower() != "mysql":
        raise SafetyError("--update-mysql 仅允许 WEBNOVEL_CATALOG_BACKEND=mysql")
    root: Path = scan_result["root"]
    candidates: list[Candidate] = scan_result["candidates"]
    if scan_result["rejected"]:
        raise SafetyError("发现符号链接、越界或无效文件，MySQL apply 已拒绝")
    body_candidates = [item for item in candidates if item.area == "body"]
    legacy_candidates = [item for item in candidates if item.area == "书籍"]
    catalog_scoped = bool(scan_result.get("catalog_scoped"))
    migration_sources: list[tuple[Candidate, str]] = []
    if catalog_scoped:
        candidate_by_relative = {item.relative_path: item for item in candidates}
        scoped_source_formats: dict[str, str] = {}
        for mapping in scan_result.get("catalog_mappings", []):
            old_key = str(mapping.get("old_object_key") or "")
            storage_format = str(mapping.get("old_storage_format") or "")
            previous = scoped_source_formats.setdefault(old_key, storage_format)
            if previous != storage_format:
                raise SafetyError("同一 catalog object key 出现冲突存储格式")
        for old_key, storage_format in scoped_source_formats.items():
            source = candidate_by_relative.get(old_key)
            if source is None:
                continue
            expected_area = "书籍" if storage_format == "legacy_key" else "body"
            if storage_format not in {"legacy_key", "content_addressed"}:
                raise SafetyError("catalog scoped 旧存储格式无效")
            if source.area != expected_area:
                raise SafetyError("catalog scoped object key 与扫描文件区域不一致")
            migration_sources.append((source, storage_format))
        source_paths = {item.path for item, _format in migration_sources}
        unexpected_candidates = [
            item
            for item in candidates
            if item.area == "body"
            or (item.area == "书籍" and item.path not in source_paths)
        ]
        if not migration_sources and unexpected_candidates:
            raise SafetyError("catalog 映射源文件与 legacy 替换候选不一致")
    else:
        migration_sources = [
            (item, "content_addressed") for item in body_candidates
        ]
    if not migration_sources:
        if not catalog_scoped and legacy_candidates:
            raise SafetyError("存在 legacy TXT 候选但没有可协调的内容寻址 body 对象")
        return {"mode": "apply-update-mysql", "changed_files": 0, "occurrences": 0}
    legacy_by_path = {item.path.resolve(): item for item in legacy_candidates}
    snapshot_by_path: dict[Path, FileSnapshot] = {
        Path(path).resolve(): snapshot
        for path, snapshot in scan_result.get(
            "catalog_file_snapshots", {}
        ).items()
    }
    destination: Path | None = None
    manifest_path: Path | None = None
    manifest: dict[str, Any] = {}
    plans: list[MySQLMigrationPlan] = []
    changed_paths: set[Path] = set()
    lock_acquired = False
    committed = False
    operation_failed = False

    with pool.connection() as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (MYSQL_LOCK_NAME,))
                if _lock_value(cursor.fetchone(), "acquired") != 1:
                    raise SafetyError("无法取得品牌迁移 MySQL GET_LOCK")
                lock_acquired = True
                if hasattr(connection, "begin"):
                    connection.begin()

                unreferenced: list[Candidate] = []
                used_legacy_paths: set[Path] = set()
                for body, storage_format in migration_sources:
                    _candidate_data(body, root)
                    rows = _select_body_mapping(cursor, body.relative_path)
                    if not rows:
                        if catalog_scoped:
                            raise SafetyError(
                                "catalog scoped 映射在 SELECT FOR UPDATE 时消失"
                            )
                        unreferenced.append(body)
                        continue
                    if len(rows) != 1:
                        raise SafetyError("一个旧 body 对象映射到多本书，拒绝歧义迁移")
                    plan = _validate_mapping(
                        rows[0],
                        body,
                        legacy_by_path,
                        snapshot_by_path,
                        root,
                        allow_canonical_alias=catalog_scoped,
                        old_storage_format=storage_format,
                    )
                    if plan.legacy.path in used_legacy_paths:
                        raise SafetyError("多个 body 对象映射到同一个 legacy TXT")
                    used_legacy_paths.add(plan.legacy.path)
                    plans.append(plan)

                unmatched_legacy = [
                    item for item in legacy_candidates if item.path not in used_legacy_paths
                ]
                unreferenced_hashes = {item.before_sha256 for item in unreferenced}
                unreferenced_legacy = [
                    item
                    for item in unmatched_legacy
                    if item.before_sha256 in unreferenced_hashes
                ]
                unsafe_unmatched_legacy = [
                    item for item in unmatched_legacy if item not in unreferenced_legacy
                ]
                if unsafe_unmatched_legacy:
                    raise SafetyError("存在未被 body/MySQL 映射覆盖的 legacy TXT 候选")

                destination = _prepare_backup_dir(root, backup_dir)
                records: list[dict[str, Any]] = []
                for body in unreferenced:
                    records.append(
                        {
                            "path": body.relative_path,
                            "catalog_id": None,
                            "old_object_key": body.relative_path,
                            "old_storage_format": "content_addressed",
                            "new_object_key": None,
                            "before_sha256": body.before_sha256,
                            "after_sha256": body.after_sha256,
                            "count": body.count,
                            "encoding": body.encoding,
                            "backup": None,
                            "status": "unreferenced_skipped",
                        }
                    )
                for legacy in unreferenced_legacy:
                    records.append(
                        {
                            "path": legacy.relative_path,
                            "catalog_id": None,
                            "old_object_key": None,
                            "old_storage_format": None,
                            "new_object_key": None,
                            "before_sha256": legacy.before_sha256,
                            "after_sha256": legacy.after_sha256,
                            "count": legacy.count,
                            "encoding": legacy.encoding,
                            "backup": None,
                            "status": "unreferenced_legacy_skipped",
                        }
                    )
                for plan in plans:
                    backup_path = None
                    if not plan.legacy_already_normalized:
                        original, _, metadata = _candidate_data(
                            plan.legacy, root
                        )
                        backup_path = (
                            destination
                            / "files"
                            / Path(plan.legacy.relative_path)
                        )
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_write(
                            backup_path,
                            original,
                            mode=plan.legacy.mode,
                            atime_ns=metadata.st_atime_ns,
                            mtime_ns=metadata.st_mtime_ns,
                        )
                    records.append(
                        {
                            "path": plan.legacy.relative_path,
                            "catalog_id": plan.catalog_id,
                            "old_object_key": plan.old_object_key,
                            "old_storage_format": plan.old_storage_format,
                            "new_object_key": plan.new_object_key,
                            "before_sha256": plan.body.before_sha256,
                            "after_sha256": plan.body.after_sha256,
                            "count": plan.body.count,
                            "encoding": plan.body.encoding,
                            "backup": (
                                backup_path.relative_to(destination).as_posix()
                                if backup_path is not None
                                else None
                            ),
                            "legacy_already_normalized": (
                                plan.legacy_already_normalized
                            ),
                            "status": (
                                "legacy_already_normalized"
                                if plan.legacy_already_normalized
                                else "backed_up"
                            ),
                        }
                    )
                manifest_path = destination / "manifest.json"
                manifest = {
                    "schema": "oohstory.ixdzs-branding-mysql-migration.v1",
                    "status": "backed_up",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "root": str(root),
                    "old": OLD_NOTICE,
                    "new": NEW_NOTICE,
                    "files": records,
                }
                _atomic_manifest(manifest_path, manifest)

                for plan in plans:
                    _candidate_data(plan.body, root)
                    if plan.legacy_already_normalized:
                        normalized_data, _ = _snapshot_data(
                            plan.legacy, root
                        )
                        if normalized_data != plan.changed_body:
                            raise SafetyError(
                                "写事务前已归一化 legacy TXT 发生变化"
                            )
                    elif plan.legacy.path != plan.body.path:
                        _candidate_data(plan.legacy, root)
                    if not plan.legacy_already_normalized:
                        _atomic_write(
                            plan.legacy.path,
                            plan.changed_body,
                            mode=plan.legacy.mode,
                            atime_ns=plan.legacy.atime_ns,
                            mtime_ns=plan.legacy.mtime_ns,
                        )
                        changed_paths.add(plan.legacy.path)
                    _update_mysql_mapping(cursor, plan)

                connection.commit()
                committed = True
                for record in records:
                    if record["status"] == "backed_up":
                        record["status"] = "migrated"
                manifest["status"] = "complete"
                manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
                _atomic_manifest(manifest_path, manifest)
        except Exception as exc:
            operation_failed = True
            if committed:
                if manifest_path is not None:
                    manifest["status"] = "committed_manifest_update_failed"
                    manifest["error"] = str(exc)
                    try:
                        _atomic_manifest(manifest_path, manifest)
                    except Exception:
                        pass
                raise SafetyError(
                    "MySQL 事务已提交且文件一致，但最终 manifest 更新失败"
                ) from exc
            rollback_error = ""
            try:
                connection.rollback()
            except Exception as rollback_exc:
                # A 2006/2013 disconnect makes an explicit ROLLBACK impossible;
                # MySQL rolls the abandoned transaction back when the session
                # dies.  Do not let that secondary InterfaceError bypass the
                # filesystem restore and manifest handling below.
                rollback_error = (
                    f"；MySQL 连接已断开，服务端将回滚遗留事务："
                    f"{type(rollback_exc).__name__}: {rollback_exc}"
                )
                if hasattr(connection, "close"):
                    try:
                        connection.close()
                    except Exception:
                        pass
            restore_failures = (
                _restore_legacy_files(plans, destination, changed_paths)
                if destination is not None
                else []
            )
            if manifest_path is not None:
                changed_relative = {
                    plan.legacy.relative_path
                    for plan in plans
                    if plan.legacy.path in changed_paths
                }
                failed_relative = {
                    item.split(":", 1)[0] for item in restore_failures
                }
                for record in manifest.get("files", []):
                    if record.get("catalog_id") is None:
                        continue
                    path = str(record.get("path") or "")
                    if record.get("legacy_already_normalized"):
                        record["status"] = "legacy_already_normalized"
                    elif path in failed_relative:
                        record["status"] = "restore_failed"
                    elif path in changed_relative:
                        record["status"] = "restored_after_rollback"
                    else:
                        record["status"] = "rollback_before_change"
                manifest["status"] = (
                    "rollback_restore_failed" if restore_failures else "rolled_back"
                )
                manifest["error"] = str(exc) + rollback_error
                manifest["restore_failures"] = restore_failures
                manifest["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
                _atomic_manifest(manifest_path, manifest)
            if restore_failures:
                raise SafetyError(
                    "MySQL 已回滚，但 legacy 恢复失败: " + "; ".join(restore_failures)
                ) from exc
            raise SafetyError(
                "MySQL 协调迁移失败；事务已回滚，已恢复变更的 legacy TXT: "
                f"{exc}{rollback_error}"
            ) from exc
        finally:
            if lock_acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT RELEASE_LOCK(%s) AS released",
                            (MYSQL_LOCK_NAME,),
                        )
                        released = _lock_value(cursor.fetchone(), "released")
                        if released != 1:
                            raise SafetyError("MySQL GET_LOCK 释放失败")
                except Exception as exc:
                    if hasattr(connection, "close"):
                        try:
                            connection.close()
                        except Exception:
                            pass
                    if not operation_failed:
                        raise SafetyError("MySQL GET_LOCK 释放失败") from exc

    return {
        "mode": "apply-update-mysql",
        "changed_files": len(plans),
        "occurrences": sum(plan.body.count for plan in plans),
        "unreferenced_body_objects": len(unreferenced),
        "migrated_content_addressed": sum(
            plan.old_storage_format == "content_addressed" for plan in plans
        ),
        "migrated_legacy_key": sum(
            plan.old_storage_format == "legacy_key" for plan in plans
        ),
        "legacy_already_normalized": sum(
            plan.legacy_already_normalized for plan in plans
        ),
        "backup_dir": str(destination),
        "manifest": str(manifest_path),
    }


def _production_mysql() -> tuple[Any, str]:
    from project_paths import APP_ROOT

    configured_env = Path(
        os.getenv(
            "OOHSTORY_LIBRARY_INFRASTRUCTURE_ENV",
            os.getenv(
                "WEBNOVEL_LIBRARY_INFRASTRUCTURE_ENV",
                str(PRODUCTION_ENV_FILE),
            ),
        )
    ).expanduser()
    if not configured_env.is_file() or configured_env.is_symlink():
        raise SafetyError(f"生产 MySQL 环境文件无效: {configured_env}")
    for line_number, raw_line in enumerate(
        configured_env.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise SafetyError(f"生产环境文件第 {line_number} 行格式无效")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(
            r"(?:OOHSTORY_LIBRARY|WEBNOVEL)_[A-Z0-9_]+",
            key,
        ):
            raise SafetyError(f"生产环境文件第 {line_number} 行变量名无效")
        try:
            values = shlex.split(raw_value, comments=True, posix=True)
        except ValueError as exc:
            raise SafetyError(f"生产环境文件第 {line_number} 行引号无效") from exc
        if len(values) > 1:
            raise SafetyError(f"生产环境文件第 {line_number} 行值格式无效")
        os.environ[key] = values[0] if values else ""

    backend_root = str(APP_ROOT / "src")
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    from oohstory_library.services.library_database import (
        LibraryInfrastructureSettings,
        MySQLConnectionPool,
    )

    settings = LibraryInfrastructureSettings.from_env()
    if settings.catalog_backend != "mysql":
        raise SafetyError("生产 catalog backend 不是 mysql，拒绝迁移")
    # Catalog migrations validate and lock many rows in one coordinated
    # transaction.  They must not inherit the short request timeout used by
    # normal API traffic, otherwise a temporary row-lock wait can surface as
    # MySQL 2013 and interrupt recovery bookkeeping.
    settings = replace(
        settings,
        mysql_read_timeout=max(settings.mysql_read_timeout, 600),
        mysql_write_timeout=max(settings.mysql_write_timeout, 600),
    )
    return MySQLConnectionPool(settings), settings.catalog_backend


def _archive_reference_counts(cursor: Any, object_key: str) -> tuple[int, int]:
    cursor.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM books WHERE body_object_key=%s) AS book_refs,
          (SELECT COUNT(*) FROM object_assets WHERE object_key=%s) AS asset_refs
        """,
        (object_key, object_key),
    )
    row = cursor.fetchone() or {}
    if isinstance(row, dict):
        return int(row.get("book_refs") or 0), int(row.get("asset_refs") or 0)
    return int(row[0] or 0), int(row[1] or 0)


def _read_archive_manifest(manifest_path: Path) -> tuple[Path, dict[str, Any], bytes]:
    manifest_path = manifest_path.expanduser().absolute()
    _existing_components_have_no_symlink(manifest_path)
    metadata = manifest_path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SafetyError("归档 manifest 必须是真实普通文件")
    original = manifest_path.read_bytes()
    try:
        payload = json.loads(original.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafetyError("归档 manifest 不是有效 UTF-8 JSON") from exc
    if payload.get("schema") != "oohstory.ixdzs-branding-mysql-migration.v1":
        raise SafetyError("只允许归档 v1 MySQL migration manifest")
    if payload.get("status") != "complete":
        raise SafetyError("只有 complete migration manifest 可以归档")
    if not isinstance(payload.get("files"), list):
        raise SafetyError("归档 manifest files 无效")
    return manifest_path.resolve(strict=True), payload, original


def archive_old_bodies(
    manifest_path: Path,
    *,
    pool: Any,
    catalog_backend: str,
    rename_func: Any = os.rename,
) -> dict[str, Any]:
    if str(catalog_backend).strip().lower() != "mysql":
        raise SafetyError("旧 body 归档仅允许生产 MySQL backend")
    manifest_path, manifest, _original_bytes = _read_archive_manifest(
        manifest_path
    )
    if manifest.get("archive_status") == "complete":
        return {
            "mode": "archive-old-bodies",
            "archive_status": "complete",
            "archive_count": int(manifest.get("archive_count") or 0),
            "manifest": str(manifest_path),
        }
    backup_dir = manifest_path.parent.resolve(strict=True)
    root = _validated_root(Path(str(manifest.get("root") or "")))
    body_root = (root / "body").resolve(strict=True)
    archive_root = backup_dir / "old-body"
    selected: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for record in manifest["files"]:
        if not isinstance(record, dict):
            raise SafetyError("归档 manifest 文件记录无效")
        if record.get("old_storage_format") != "content_addressed":
            continue
        key = str(record.get("old_object_key") or "").strip()
        digest = str(record.get("before_sha256") or "").strip().lower()
        previous = seen.setdefault(key, digest)
        if previous != digest:
            raise SafetyError("同一旧 body key 在 manifest 中的 SHA-256 冲突")
        if previous == digest and any(item["key"] == key for item in selected):
            continue
        parts = Path(key).parts
        if (
            len(parts) != 4
            or parts[0] != "body"
            or not BODY_OBJECT_RE.fullmatch(parts[3])
            or Path(parts[3]).stem != digest
            or parts[1] != digest[:2]
            or parts[2] != digest[2:4]
        ):
            raise SafetyError("manifest 旧 body key 不符合 SHA-256 内容寻址")
        source = root / Path(key)
        data, _ = _read_regular_file(source, body_root)
        if _sha256(data) != digest:
            raise SafetyError(f"旧 body 内容 SHA-256 不一致: {key}")
        target = archive_root / Path(key)
        try:
            target.resolve(strict=False).relative_to(
                archive_root.resolve(strict=False)
            )
        except ValueError as exc:
            raise SafetyError("旧 body 归档目标越界") from exc
        if target.is_symlink():
            raise SafetyError(f"旧 body 归档目标是符号链接: {key}")
        if target.exists():
            existing = target.read_bytes() if target.is_file() else b""
            if _sha256(existing) != digest:
                raise SafetyError(f"旧 body 归档目标已存在且内容不一致: {key}")
            raise SafetyError(f"旧 body 归档目标已存在，拒绝覆盖: {key}")
        selected.append(
            {
                "key": key,
                "digest": digest,
                "source": source,
                "target": target,
                "record": record,
                "original_status": record.get("status"),
                "original_archive_path": record.get("archive_path"),
            }
        )

    moved: list[dict[str, Any]] = []
    lock_acquired = False
    operation_failed = False
    with pool.connection(readonly=True) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT GET_LOCK(%s, 0) AS acquired",
                    (MYSQL_ARCHIVE_LOCK_NAME,),
                )
                if _lock_value(cursor.fetchone(), "acquired") != 1:
                    raise SafetyError("无法取得旧 body 归档 MySQL GET_LOCK")
                lock_acquired = True
                for item in selected:
                    book_refs, asset_refs = _archive_reference_counts(
                        cursor, item["key"]
                    )
                    if book_refs or asset_refs:
                        raise SafetyError(
                            f"旧 body 仍被引用: {item['key']} "
                            f"books={book_refs} object_assets={asset_refs}"
                        )

                for item in selected:
                    source_data, _ = _read_regular_file(
                        item["source"], body_root
                    )
                    if _sha256(source_data) != item["digest"]:
                        raise SafetyError(
                            f"归档前旧 body 发生变化: {item['key']}"
                        )
                    target: Path = item["target"]
                    _existing_components_have_no_symlink(target.parent)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _existing_components_have_no_symlink(target.parent)
                    if target.exists() or target.is_symlink():
                        raise SafetyError(
                            f"归档前目标路径被占用: {item['key']}"
                        )
                    if item["source"].stat().st_dev != target.parent.stat().st_dev:
                        raise SafetyError("旧 body 与归档目录不在同一文件系统")
                    rename_func(item["source"], target)
                    moved.append(item)
                    _fsync_directory(item["source"].parent)
                    _fsync_directory(target.parent)
                    item["record"]["archive_path"] = target.relative_to(
                        backup_dir
                    ).as_posix()
                    item["record"]["status"] = "archived_old_body"
                    _atomic_manifest(manifest_path, manifest)

                manifest["archive_status"] = "complete"
                manifest["archive_count"] = len(moved)
                manifest["archived_at"] = datetime.now(timezone.utc).isoformat()
                _atomic_manifest(manifest_path, manifest)
        except Exception as exc:
            operation_failed = True
            rollback_failures: list[str] = []
            for item in reversed(moved):
                try:
                    if item["source"].exists() or item["source"].is_symlink():
                        raise SafetyError("回滚源路径被占用")
                    rename_func(item["target"], item["source"])
                    _fsync_directory(item["target"].parent)
                    _fsync_directory(item["source"].parent)
                except Exception as rollback_exc:
                    rollback_failures.append(
                        f"{item['key']}: {rollback_exc}"
                    )
            for item in selected:
                if item["original_archive_path"] is None:
                    item["record"].pop("archive_path", None)
                else:
                    item["record"]["archive_path"] = item[
                        "original_archive_path"
                    ]
                item["record"]["status"] = item["original_status"]
            manifest["archive_status"] = (
                "rollback_failed" if rollback_failures else "rolled_back"
            )
            manifest["archive_count"] = 0
            manifest["archive_error"] = str(exc)
            manifest["archive_rollback_failures"] = rollback_failures
            _atomic_manifest(manifest_path, manifest)
            if rollback_failures:
                raise SafetyError(
                    "旧 body 归档失败且回滚不完整: "
                    + "; ".join(rollback_failures)
                ) from exc
            raise SafetyError(f"旧 body 归档失败，已完整回滚: {exc}") from exc
        finally:
            if lock_acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT RELEASE_LOCK(%s) AS released",
                            (MYSQL_ARCHIVE_LOCK_NAME,),
                        )
                        if _lock_value(cursor.fetchone(), "released") != 1:
                            raise SafetyError("旧 body 归档 GET_LOCK 释放失败")
                except Exception as exc:
                    if hasattr(connection, "close"):
                        try:
                            connection.close()
                        except Exception:
                            pass
                    if not operation_failed:
                        raise SafetyError("旧 body 归档 GET_LOCK 释放失败") from exc
    return {
        "mode": "archive-old-bodies",
        "archive_status": "complete",
        "archive_count": len(moved),
        "archive_root": str(archive_root),
        "manifest": str(manifest_path),
    }


def apply(scan_result: dict[str, Any], backup_dir: Path | None = None) -> dict[str, Any]:
    root: Path = scan_result["root"]
    candidates: list[Candidate] = scan_result["candidates"]
    rejected = scan_result["rejected"]
    body_candidates = [item for item in candidates if item.area == "body"]
    if rejected:
        raise SafetyError("发现符号链接、越界或无效文件，apply 已拒绝")
    if body_candidates:
        raise SafetyError(
            "匹配内容存在于 body/ 内容寻址对象；需要新对象键与 MySQL 元数据协调，"
            "本脚本不会修改数据库，apply 已拒绝"
        )
    if not candidates:
        return {"mode": "apply", "changed_files": 0, "occurrences": 0}

    destination = _prepare_backup_dir(root, backup_dir)
    records: list[dict[str, Any]] = []
    prepared: list[tuple[Candidate, bytes]] = []
    for candidate in candidates:
        original, changed, metadata = _candidate_data(candidate, root)
        backup_path = destination / "files" / Path(candidate.relative_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            backup_path,
            original,
            mode=candidate.mode,
            atime_ns=metadata.st_atime_ns,
            mtime_ns=metadata.st_mtime_ns,
        )
        records.append(
            {
                "path": candidate.relative_path,
                "count": candidate.count,
                "before_sha256": candidate.before_sha256,
                "after_sha256": candidate.after_sha256,
                "encoding": candidate.encoding,
                "backup": backup_path.relative_to(destination).as_posix(),
                "mode": candidate.mode,
                "atime_ns": candidate.atime_ns,
                "mtime_ns": candidate.mtime_ns,
                "status": "backed_up",
            }
        )
        prepared.append((candidate, changed))

    manifest_path = destination / "manifest.json"
    manifest: dict[str, Any] = {
        "schema": "oohstory.ixdzs-branding-replacement.v1",
        "status": "backed_up",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "old": OLD_NOTICE,
        "new": NEW_NOTICE,
        "replacement_profile": str(
            scan_result.get("replacement_profile") or DEFAULT_PROFILE
        ),
        "files": records,
    }
    _atomic_manifest(manifest_path, manifest)
    try:
        for index, (candidate, changed) in enumerate(prepared):
            _candidate_data(candidate, root)
            _atomic_write(
                candidate.path,
                changed,
                mode=candidate.mode,
                atime_ns=candidate.atime_ns,
                mtime_ns=candidate.mtime_ns,
            )
            records[index]["status"] = "replaced"
        manifest["status"] = "complete"
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_manifest(manifest_path, manifest)
    except Exception:
        manifest["status"] = "interrupted_restore_from_backups"
        manifest["interrupted_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_manifest(manifest_path, manifest)
        raise
    return {
        "mode": "apply",
        "changed_files": len(candidates),
        "occurrences": sum(item.count for item in candidates),
        "backup_dir": str(destination),
        "manifest": str(manifest_path),
    }


def summarize(scan_result: dict[str, Any], *, mode: str) -> dict[str, Any]:
    candidates: list[Candidate] = scan_result["candidates"]
    by_area = {
        area: {
            "matched_files": sum(item.area == area for item in candidates),
            "occurrences": sum(item.count for item in candidates if item.area == area),
        }
        for area in ALLOWED_AREAS
    }
    summary = {
        "mode": mode,
        "root": str(scan_result["root"]),
        "files_seen": scan_result["files_seen"],
        "bytes_scanned": scan_result["bytes_scanned"],
        "matched_files": len(candidates),
        "occurrences": sum(item.count for item in candidates),
        "by_area": by_area,
        "content_addressed_apply_blocked": by_area["body"]["matched_files"],
        "rejected": scan_result["rejected"],
        "replacement_profile": str(
            scan_result.get("replacement_profile") or DEFAULT_PROFILE
        ),
    }
    if scan_result.get("catalog_scoped"):
        summary.update(
            {
                "catalog_scoped": True,
                "catalog_rows": int(scan_result.get("catalog_rows") or 0),
                "catalog_source_prefix": str(
                    scan_result.get("catalog_source_prefix") or ""
                ),
            }
        )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只扫描并报告（默认）")
    mode.add_argument("--apply", action="store_true", help="备份后替换 书籍/ 中的匹配")
    mode.add_argument(
        "--archive-old-bodies",
        type=Path,
        metavar="MANIFEST",
        help="确认旧 body 已零引用后，将其原子移动到 migration 备份目录",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="电子书库根目录")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="本次 apply 的全新备份目录；默认在书库根目录内生成",
    )
    parser.add_argument(
        "--update-mysql",
        action="store_true",
        help=(
            "与 --apply 同用时创建新 body 并单事务更新 MySQL；"
            "与显式 --dry-run 同用时仅按 MySQL 目录定点扫描"
        ),
    )
    parser.add_argument(
        "--profile",
        choices=tuple(PROFILE_SOURCE_PREFIXES),
        default=DEFAULT_PROFILE,
        help="选择来源特定正文清洗规则",
    )
    parser.add_argument(
        "--catalog-source-prefix",
        help=(
            "update-mysql 定点扫描的 source_id 字面前缀；"
            "默认与 --profile 对应"
        ),
    )
    parser.add_argument(
        "--catalog-id",
        type=int,
        default=0,
        help="仅处理一个 MySQL 书目 ID（必须搭配 --update-mysql）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.update_mysql and not (args.apply or args.dry_run):
        parser.error("--update-mysql 必须显式搭配 --apply 或 --dry-run")
    if (args.catalog_source_prefix or args.catalog_id) and not args.update_mysql:
        parser.error("--catalog-source-prefix/--catalog-id 仅可与 --update-mysql 使用")
    if args.catalog_id < 0:
        parser.error("--catalog-id 必须是正整数")
    if args.archive_old_bodies and (
        args.update_mysql or args.backup_dir or args.catalog_source_prefix
        or args.catalog_id
    ):
        parser.error("--archive-old-bodies 与扫描、迁移参数互斥")
    try:
        if args.archive_old_bodies:
            pool, catalog_backend = _production_mysql()
            archive_result = archive_old_bodies(
                args.archive_old_bodies,
                pool=pool,
                catalog_backend=catalog_backend,
            )
            print(json.dumps(archive_result, ensure_ascii=False, indent=2))
            return 0
        pool = None
        catalog_backend = ""
        if args.update_mysql:
            pool, catalog_backend = _production_mysql()
            scan_kwargs = {
                "pool": pool,
                "source_prefix": (
                    args.catalog_source_prefix
                    or PROFILE_SOURCE_PREFIXES[args.profile]
                ),
                "profile": args.profile,
            }
            if args.catalog_id:
                scan_kwargs["catalog_id"] = int(args.catalog_id)
            result = scan_catalog_scope(args.root, **scan_kwargs)
        else:
            result = scan(args.root, profile=args.profile)
        mode = (
            "apply-update-mysql"
            if args.update_mysql and args.apply
            else "dry-run-update-mysql"
            if args.update_mysql
            else "apply"
            if args.apply
            else "dry-run"
        )
        summary = summarize(result, mode=mode)
        if args.update_mysql and args.apply:
            summary.update(
                apply_with_mysql(
                    result,
                    args.backup_dir,
                    pool=pool,
                    catalog_backend=catalog_backend,
                )
            )
        elif args.apply:
            summary.update(apply(result, args.backup_dir))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except SafetyError as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
