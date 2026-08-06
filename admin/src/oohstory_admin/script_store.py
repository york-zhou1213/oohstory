from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


MAX_SCRIPT_BYTES = 64 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ScriptDefinition:
    script_id: str
    label: str
    relative_path: str
    units: tuple[str, ...]


SCRIPT_DEFINITIONS: tuple[ScriptDefinition, ...] = (
    ScriptDefinition(
        "authorized_catalog",
        "授权书源目录同步脚本",
        "scripts/electronic-library/authorized_site_catalog_sync.py",
        (
            "oohstory-library-authorized-catalog-sync.service",
            "oohstory-library-authorized-catalog-sync.timer",
        ),
    ),
    ScriptDefinition(
        "authorized_download",
        "授权正文同步脚本",
        "scripts/electronic-library/authorized_site_download_worker.py",
        (
            "oohstory-library-authorized-download.service",
            "oohstory-library-authorized-download.timer",
        ),
    ),
    ScriptDefinition(
        "cover_sync",
        "封面同步脚本",
        "scripts/electronic-library/txt80_cover_sync.py",
        (
            "oohstory-library-cover-sync.service",
            "oohstory-library-xbiquge-cover-sync.service",
            "oohstory-library-ixdzs-cover-sync.service",
            "oohstory-library-shubaow-cover-sync.service",
            "oohstory-library-linovelib-cover-sync.service",
        ),
    ),
    ScriptDefinition(
        "local_source_upgrade",
        "本地书源升级脚本",
        "scripts/electronic-library/local_source_upgrade_worker.py",
        ("oohstory-library-local-source-upgrade.service",),
    ),
    ScriptDefinition(
        "index_refresh",
        "轻量索引刷新脚本",
        "scripts/electronic-library/refresh_library_indexes.py",
        ("oohstory-library-index-refresh.service",),
    ),
    ScriptDefinition(
        "ingestion_index",
        "新书可见索引脚本",
        "scripts/electronic-library/ingestion_index_worker.py",
        ("oohstory-library-ingestion-index.service",),
    ),
    ScriptDefinition(
        "derived_index",
        "派生索引脚本",
        "scripts/electronic-library/derived_index_worker.py",
        (
            "oohstory-library-derived-index.service",
            "oohstory-library-derived-index-probe.service",
        ),
    ),
)

SCRIPT_ALLOWLIST = {item.script_id: item for item in SCRIPT_DEFINITIONS}
SCRIPT_BY_UNIT = {
    unit: item
    for item in SCRIPT_DEFINITIONS
    for unit in item.units
}


class ScriptStoreError(RuntimeError):
    pass


class ScriptNotFoundError(ScriptStoreError):
    pass


class ScriptConflictError(ScriptStoreError):
    pass


class ScriptValidationError(ScriptStoreError):
    pass


@dataclass(frozen=True, slots=True)
class ScriptSaveResult:
    script_id: str
    old_sha256: str
    new_sha256: str
    backup: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_content(content: str) -> bytes:
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ScriptValidationError("脚本必须是 UTF-8 文本") from exc
    if not data:
        raise ScriptValidationError("脚本内容不能为空")
    if len(data) > MAX_SCRIPT_BYTES:
        raise ScriptValidationError("脚本超过 64 KiB 上限")
    if b"\x00" in data:
        raise ScriptValidationError("脚本不能包含 NUL 字节")
    if not content.startswith("#!/usr/bin/env python3"):
        raise ScriptValidationError("Python 脚本必须保留标准 python3 shebang")
    try:
        compile(content, "<managed-script>", "exec", dont_inherit=True)
    except SyntaxError as exc:
        line = exc.lineno or 0
        raise ScriptValidationError(f"Python 语法校验失败（第 {line} 行）") from exc
    return data


def _safe_target(project_root: Path, definition: ScriptDefinition) -> Path:
    root = project_root.resolve(strict=True)
    target = root / definition.relative_path
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ScriptValidationError("脚本路径越界") from exc
    try:
        info = target.lstat()
    except FileNotFoundError as exc:
        raise ScriptNotFoundError("OOHStory 托管脚本尚未部署") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ScriptValidationError("托管脚本必须是普通文件，禁止软链接")
    if target.resolve(strict=True) != target:
        raise ScriptValidationError("托管脚本路径中禁止软链接")
    return target


def _read_regular(target: Path) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise ScriptValidationError("托管脚本读取失败或已变成软链接") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ScriptValidationError("托管脚本必须是普通文件")
        data = b""
        while len(data) <= MAX_SCRIPT_BYTES:
            chunk = os.read(descriptor, min(65_536, MAX_SCRIPT_BYTES + 1 - len(data)))
            if not chunk:
                break
            data += chunk
    finally:
        os.close(descriptor)
    if len(data) > MAX_SCRIPT_BYTES:
        raise ScriptValidationError("脚本超过 64 KiB 上限")
    return data, info


def _atomic_replace(target: Path, data: bytes, expected_sha256: str) -> ScriptSaveResult:
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ScriptValidationError("原始 SHA-256 无效")
    old_data, info = _read_regular(target)
    old_sha = _sha256(old_data)
    if old_sha != expected_sha256:
        raise ScriptConflictError("脚本已被其他操作修改，请刷新后重试")
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, stat.S_IMODE(info.st_mode))
        try:
            os.chown(temp_name, info.st_uid, info.st_gid)
        except PermissionError:
            pass
        os.replace(temp_name, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    new_data, _ = _read_regular(target)
    new_sha = _sha256(new_data)
    if new_sha != _sha256(data):
        raise ScriptStoreError("保存后哈希校验失败")
    return ScriptSaveResult("", old_sha, new_sha, "")


class ScriptStore:
    def __init__(
        self,
        project_root: Path,
        *,
        use_sudo_helper: bool = False,
        helper_path: str = "/usr/local/libexec/oohstory-admin-script-store",
        runner: Runner = subprocess.run,
    ):
        self.project_root = project_root.resolve(strict=False)
        self.use_sudo_helper = use_sudo_helper
        self.helper_path = helper_path
        self.runner = runner

    def definition(self, script_id: str) -> ScriptDefinition:
        try:
            return SCRIPT_ALLOWLIST[script_id]
        except KeyError as exc:
            raise ScriptNotFoundError("脚本不在允许列表中") from exc

    def read(self, script_id: str) -> dict[str, Any]:
        definition = self.definition(script_id)
        target = _safe_target(self.project_root, definition)
        data, info = _read_regular(target)
        if b"\x00" in data:
            raise ScriptValidationError("脚本包含 NUL 字节")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ScriptValidationError("脚本不是 UTF-8 文本") from exc
        return {
            "id": definition.script_id,
            "label": definition.label,
            "relative_path": definition.relative_path,
            "absolute_path": str(target),
            "units": definition.units,
            "content": content,
            "sha256": _sha256(data),
            "bytes": len(data),
            "modified_ns": info.st_mtime_ns,
            "lines": content.count("\n") + (0 if content.endswith("\n") else 1),
        }

    def describe_unit(self, unit: str) -> dict[str, Any] | None:
        definition = SCRIPT_BY_UNIT.get(unit)
        if not definition:
            return None
        target = self.project_root / definition.relative_path
        description: dict[str, Any] = {
            "id": definition.script_id,
            "label": definition.label,
            "relative_path": definition.relative_path,
            "absolute_path": str(target),
            "units": definition.units,
            "editable": False,
        }
        try:
            record = self.read(definition.script_id)
        except ScriptStoreError as exc:
            description["error"] = str(exc)
        else:
            description.update(
                editable=True,
                sha256=record["sha256"],
                bytes=record["bytes"],
                modified_ns=record["modified_ns"],
            )
        return description

    def save(self, script_id: str, content: str, expected_sha256: str) -> ScriptSaveResult:
        definition = self.definition(script_id)
        data = _validate_content(content)
        if not SHA256_RE.fullmatch(expected_sha256):
            raise ScriptValidationError("原始 SHA-256 无效")
        if self.use_sudo_helper:
            argv = [
                "/usr/bin/sudo",
                "-n",
                self.helper_path,
                "write",
                definition.script_id,
                expected_sha256,
            ]
            try:
                completed = self.runner(
                    argv,
                    input=data,
                    capture_output=True,
                    timeout=15,
                    check=False,
                    shell=False,
                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                raise ScriptStoreError("脚本保存 helper 不可用或超时") from exc
            message = (completed.stderr or "").strip()
            if completed.returncode == 75:
                raise ScriptConflictError("脚本已被其他操作修改，请刷新后重试")
            if completed.returncode != 0:
                raise ScriptStoreError(message[:300] or "脚本保存失败")
            try:
                payload = json.loads(completed.stdout)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ScriptStoreError("脚本保存 helper 返回无效结果") from exc
            return ScriptSaveResult(
                script_id=definition.script_id,
                old_sha256=str(payload["old_sha256"]),
                new_sha256=str(payload["new_sha256"]),
                backup=str(payload["backup"]),
            )

        target = _safe_target(self.project_root, definition)
        result = _atomic_replace(target, data, expected_sha256)
        return ScriptSaveResult(
            script_id=definition.script_id,
            old_sha256=result.old_sha256,
            new_sha256=result.new_sha256,
            backup=result.backup,
        )
