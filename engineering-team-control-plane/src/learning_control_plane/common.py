"""Shared constants, parsing, hashing, and path-boundary helpers."""
from __future__ import annotations
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

AGENTS = ("ken", "john", "jucy", "bob", "mus")
EVENT_KINDS = ("ERR", "FR", "LRN", "FEAT")
EVENT_ID_RE = re.compile(r"(?<![A-Z0-9-])((?:ERR|FR|LRN|FEAT)-\d{8}-[A-Za-z0-9]{3,})(?![A-Z0-9-])")
EVENT_HEADER_RE = re.compile(r"(?m)^## \[((?:ERR|FR|LRN|FEAT)-\d{8}-[A-Za-z0-9]{3,})\](?:\s|$)")
TASK_RE = re.compile(r"^TASK-[A-Za-z0-9._-]+$")
STAGE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INDEX_NAMES = ("LEARNINGS.md", "ERRORS.md", "FEATURE_REQUESTS.md")
DEFAULT_ROOT = "/root/.openclaw/workspaces/engineering-team"

class ControlPlaneError(ValueError):
    """Expected fail-closed validation error."""

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ControlPlaneError(f"{field} must be a non-empty RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ControlPlaneError(f"{field} is not RFC3339: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ControlPlaneError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)

def timestamp_calendar_date(value: Any, field: str):
    """Return the calendar date in the timestamp's own declared offset."""
    if not isinstance(value, str) or not value:
        raise ControlPlaneError(f"{field} must be a non-empty RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ControlPlaneError(f"{field} is not RFC3339: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ControlPlaneError(f"{field} must include a timezone")
    return parsed.date()

def reject_symlink_ancestors(path: Path) -> Path:
    """Reject a path if any existing component is a symlink."""
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise ControlPlaneError(f"symlink path component is forbidden: {current}")
    return Path(os.path.abspath(absolute))

def validate_root(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise ControlPlaneError(f"team root must be a real directory: {root}")
    root = root.resolve(strict=True)
    for agent in AGENTS:
        require_real_directory(root, PurePosixPath(agent))
    require_real_directory(root, PurePosixPath("team-learnings"))
    return root

def _validated_parts(relative: str | PurePosixPath) -> tuple[str, ...]:
    raw = str(relative)
    if not raw or "\\" in raw or "\x00" in raw:
        raise ControlPlaneError(f"unsafe relative path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ControlPlaneError(f"unsafe relative path: {raw!r}")
    return path.parts

def secure_path(root: Path, relative: str | PurePosixPath, *, must_exist: bool = True) -> Path:
    parts = _validated_parts(relative)
    candidate = root.joinpath(*parts)
    current = root
    for index, part in enumerate(parts):
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            if must_exist or index != len(parts) - 1:
                raise ControlPlaneError(f"path does not exist: {'/'.join(parts)}")
            break
        if current.is_symlink():
            raise ControlPlaneError(f"symlink is forbidden: {'/'.join(parts[:index + 1])}")
    resolved_parent = candidate.parent.resolve(strict=True)
    if os.path.commonpath((str(root), str(resolved_parent))) != str(root):
        raise ControlPlaneError(f"path escapes team root: {relative}")
    return candidate

def require_real_directory(root: Path, relative: PurePosixPath) -> Path:
    path = secure_path(root, relative)
    if not path.is_dir():
        raise ControlPlaneError(f"required directory is missing: {relative}")
    return path

def require_real_file(root: Path, relative: str | PurePosixPath) -> Path:
    path = secure_path(root, relative)
    if not path.is_file():
        raise ControlPlaneError(f"required regular file is missing: {relative}")
    return path

def iter_real_files(directory: Path, patterns: Iterable[str]) -> list[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        for path in directory.glob(pattern):
            relative = path.relative_to(directory)
            current = directory
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise ControlPlaneError(f"symlink is forbidden: {current}")
            if path.is_file():
                found.add(path)
    return sorted(found)

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlPlaneError(f"malformed JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlPlaneError(f"JSON object required: {path}")
    return value

def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()
