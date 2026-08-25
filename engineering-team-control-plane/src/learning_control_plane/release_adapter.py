"""Versioned compatibility-adapter activation and rollback controls."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .common import (ControlPlaneError, SHA256_RE, read_json, reject_symlink_ancestors,
                     sha256_file)
from .deployment import verify_deployment


REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LEGACY_COMMANDS = ("bootstrap", "preflight", "close", "metrics", "stale")
RELEASE_COMMANDS = ("audit-task", "audit-system")
ACTIVATION_RECEIPT_FIELDS = {
    "schema_version", "operation", "contract_sha256", "manifest_sha256",
    "source_revision", "previous_target", "activated_target",
}
ADAPTER_RECEIPT_FIELDS = {
    "schema_version", "operation", "contract_sha256", "before_sha256",
    "before_mode", "after_sha256", "active_target",
}
RELEASE_METADATA_FIELDS = {
    "schema_version", "adapter_version", "release_id", "source_revision",
    "manifest_sha256", "contract_sha256", "entrypoint_sha256", "adapter_sha256",
    "previous_target",
}
LEGACY_METADATA_FIELDS = {"schema_version", "sha256", "mode"}


@dataclass(frozen=True)
class RuntimeContract:
    path: Path
    adapter_version: int
    team_root: Path
    runtime_root: Path
    consumer: Path
    releases_dir: str
    active_link: str
    legacy_target: str
    release_entrypoint: str
    adapter_source: str


def _relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ControlPlaneError(f"{field} must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value
            or any(part in {"", ".", ".."} for part in path.parts)):
        raise ControlPlaneError(f"{field} must be a safe relative POSIX path")
    return path.as_posix()


def _absolute_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ControlPlaneError(f"{field} must be an absolute path")
    return Path(os.path.abspath(value))


def _require_exact_fields(payload: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - payload.keys())
    extra = sorted(payload.keys() - expected)
    if missing or extra:
        raise ControlPlaneError(f"{label} fields mismatch: missing={missing}, extra={extra}")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _is_revision(value: Any) -> bool:
    return isinstance(value, str) and REVISION_RE.fullmatch(value) is not None


def _safe_write_path(path: Path, field: str, *, allow_leaf_symlink: bool = False) -> Path:
    """Reject symlink ancestors before any runtime or receipt mutation."""
    path = Path(os.path.abspath(path))
    reject_symlink_ancestors(path.parent)
    if path.is_symlink() and not allow_leaf_symlink:
        raise ControlPlaneError(f"{field} must not be a symlink")
    return path


def _require_distinct_managed_paths(paths: tuple[tuple[Path, str], ...]) -> None:
    """Reject overlapping namespaces and hard-link aliases before mutation."""
    names: dict[str, str] = {}
    inodes: dict[tuple[int, int], str] = {}
    for path, field in paths:
        normalized = os.path.normcase(os.path.abspath(path))
        for existing, existing_field in names.items():
            if (normalized == existing
                    or os.path.commonpath((normalized, existing)) in {normalized, existing}):
                raise ControlPlaneError(
                    "managed adapter paths must be distinct: "
                    f"{existing_field} and {field}")
        names[normalized] = field
        try:
            status = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            continue
        identity = (status.st_dev, status.st_ino)
        if identity in inodes:
            raise ControlPlaneError(
                f"managed adapter paths must be distinct: {inodes[identity]} and {field}")
        inodes[identity] = field


def _open_directory_fd(path: Path, field: str, *, create: bool = False) -> int:
    """Open an absolute directory component-by-component without following links."""
    path = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    traversed = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            traversed /= part
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise ControlPlaneError(f"{field} directory is missing: {traversed}") from None
                try:
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except OSError as exc:
                    raise ControlPlaneError(
                        f"{field} has an unsafe directory component: {traversed}") from exc
            except OSError as exc:
                raise ControlPlaneError(
                    f"{field} has an unsafe directory component: {traversed}") from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def _directory_fd(path: Path, field: str, *, create: bool = False) -> Iterator[int]:
    descriptor = _open_directory_fd(path, field, create=create)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _runtime_transition_lock(contract: RuntimeContract) -> Iterator[None]:
    """Serialize every selector and adapter transition on the stable runtime parent."""
    descriptor = _open_directory_fd(contract.runtime_root.parent, "runtime transition lock")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _leaf_stat(directory_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _require_regular_leaf(directory_fd: int, name: str, field: str) -> os.stat_result:
    status = _leaf_stat(directory_fd, name)
    if status is None or not stat.S_ISREG(status.st_mode):
        raise ControlPlaneError(f"{field} must be a real file")
    return status


def _open_regular_leaf_fd(
        directory_fd: int, name: str, field: str) -> tuple[int, os.stat_result]:
    try:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory_fd)
    except OSError as exc:
        raise ControlPlaneError(f"{field} must be a stable real file") from exc
    status = os.fstat(descriptor)
    if not stat.S_ISREG(status.st_mode):
        os.close(descriptor)
        raise ControlPlaneError(f"{field} must be a real file")
    return descriptor, status


def _read_bytes_at(directory_fd: int, name: str, field: str) -> bytes:
    descriptor, _status = _open_regular_leaf_fd(directory_fd, name, field)
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def _read_bytes_path(path: Path, field: str) -> bytes:
    with _directory_fd(path.parent, field) as directory_fd:
        return _read_bytes_at(directory_fd, path.name, field)


def _read_json_at(directory_fd: int, name: str, field: str) -> dict[str, Any]:
    try:
        value = json.loads(_read_bytes_at(directory_fd, name, field).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlPlaneError(f"malformed JSON: {field}: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlPlaneError(f"JSON object required: {field}")
    return value


def _read_json_path(path: Path, field: str) -> dict[str, Any]:
    with _directory_fd(path.parent, field) as directory_fd:
        return _read_json_at(directory_fd, path.name, field)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_bytes_fd(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            return handle.read()
    finally:
        os.lseek(descriptor, 0, os.SEEK_SET)


def _sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while block := os.read(descriptor, 1024 * 1024):
        digest.update(block)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _file_identity(status: os.stat_result) -> tuple[int, int, int, int]:
    return (status.st_dev, status.st_ino, stat.S_IFMT(status.st_mode),
            stat.S_IMODE(status.st_mode))


def _atomic_write_at(directory_fd: int, name: str, content: bytes, *, mode: int,
                     field: str, replace: bool) -> None:
    """Write and publish a leaf relative to an already validated directory FD."""
    current = _leaf_stat(directory_fd, name)
    if current is not None:
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            raise ControlPlaneError(f"{field} must not be a symlink or special file")
        if not replace:
            raise ControlPlaneError(f"{field} path must not already exist")
    temporary = f".{name}.{os.getpid()}.{secrets.token_hex(8)}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        mode,
        dir_fd=directory_fd,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        if replace:
            os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        else:
            try:
                os.link(temporary, name, src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd, follow_symlinks=False)
            except FileExistsError as exc:
                raise ControlPlaneError(f"{field} path must not already exist") from exc
            os.unlink(temporary, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _atomic_json_at(directory_fd: int, name: str, value: Any, *, field: str,
                    replace: bool = False) -> None:
    content = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _atomic_write_at(directory_fd, name, content, mode=0o600, field=field, replace=replace)


def _unlink_at(directory_fd: int, name: str, field: str, *, allow_symlink: bool = False) -> None:
    current = _leaf_stat(directory_fd, name)
    if current is None:
        raise ControlPlaneError(f"{field} is missing")
    if stat.S_ISLNK(current.st_mode):
        if not allow_symlink:
            raise ControlPlaneError(f"{field} must not be a symlink")
    elif not stat.S_ISREG(current.st_mode):
        raise ControlPlaneError(f"{field} must be a real file")
    os.unlink(name, dir_fd=directory_fd)
    os.fsync(directory_fd)


def _readlink_at(directory_fd: int, name: str, field: str, *, allow_absent: bool = False) -> str | None:
    current = _leaf_stat(directory_fd, name)
    if current is None:
        if allow_absent:
            return None
        raise ControlPlaneError(f"{field} is missing")
    if not stat.S_ISLNK(current.st_mode):
        raise ControlPlaneError(f"{field} must be a symlink")
    return os.readlink(name, dir_fd=directory_fd)


def _atomic_link_at(directory_fd: int, name: str, target: str) -> None:
    current = _leaf_stat(directory_fd, name)
    if current is not None and not stat.S_ISLNK(current.st_mode):
        raise ControlPlaneError("active release selector must not replace a real path")
    temporary = f".{name}.{os.getpid()}.{secrets.token_hex(8)}"
    try:
        os.symlink(target, temporary, dir_fd=directory_fd)
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _release_target(contract: RuntimeContract, value: Any, field: str,
                    *, allow_none: bool = False) -> tuple[str | None, Path | None]:
    if value is None:
        if allow_none:
            return None, None
        raise ControlPlaneError(f"{field} must name a release target")
    target = _relative_path(value, field)
    parts = PurePosixPath(target).parts
    if len(parts) != 2 or parts[0] != contract.releases_dir or not RELEASE_ID_RE.fullmatch(parts[1]):
        raise ControlPlaneError(f"{field} must be {contract.releases_dir}/RELEASE_ID")
    release = reject_symlink_ancestors(contract.runtime_root.joinpath(*parts))
    if release.is_symlink() or not release.is_dir():
        raise ControlPlaneError(f"{field} must resolve to a real release directory")
    return target, release


def _resolve_release_target(contract: RuntimeContract, target: Any, field: str) -> dict[str, Any]:
    target, release_root = _release_target(contract, target, field)
    assert target is not None and release_root is not None
    metadata_path = reject_symlink_ancestors(release_root / "release-metadata.json")
    manifest_path = reject_symlink_ancestors(release_root / "deployment-manifest.json")
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise ControlPlaneError(f"{field} metadata is missing or unsafe")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ControlPlaneError(f"{field} deployment manifest is missing or unsafe")
    metadata = read_json(metadata_path)
    _require_exact_fields(metadata, RELEASE_METADATA_FIELDS, f"{field} metadata")
    if (type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1
            or type(metadata.get("adapter_version")) is not int
            or metadata["adapter_version"] != contract.adapter_version):
        raise ControlPlaneError(f"{field} metadata version is unsupported")
    if metadata.get("release_id") != release_root.name:
        raise ControlPlaneError(f"{field} metadata identity mismatch")
    if not _is_revision(metadata.get("source_revision")):
        raise ControlPlaneError(f"{field} source_revision is malformed")
    for name in ("manifest_sha256", "contract_sha256", "entrypoint_sha256", "adapter_sha256"):
        if not _is_sha256(metadata.get(name)):
            raise ControlPlaneError(f"{field} {name} is malformed")
    if metadata["manifest_sha256"] != sha256_file(manifest_path):
        raise ControlPlaneError(f"{field} deployment manifest hash mismatch")
    if metadata["contract_sha256"] != sha256_file(contract.path):
        raise ControlPlaneError(f"{field} runtime contract hash mismatch")
    previous_target, _ = _release_target(
        contract, metadata.get("previous_target"), f"{field} metadata previous_target",
        allow_none=True)
    if previous_target == target:
        raise ControlPlaneError(f"{field} metadata predecessor must differ from the release")
    entrypoint = reject_symlink_ancestors(release_root / contract.release_entrypoint)
    adapter = reject_symlink_ancestors(release_root / contract.adapter_source)
    if entrypoint.is_symlink() or not entrypoint.is_file() or adapter.is_symlink() or not adapter.is_file():
        raise ControlPlaneError(f"{field} entrypoint or adapter is missing or unsafe")
    if sha256_file(entrypoint) != metadata["entrypoint_sha256"]:
        raise ControlPlaneError(f"{field} entrypoint hash mismatch")
    if sha256_file(adapter) != metadata["adapter_sha256"]:
        raise ControlPlaneError(f"{field} adapter hash mismatch")
    return {"target": target, "root": release_root, "metadata": metadata,
            "manifest": manifest_path, "entrypoint": entrypoint, "adapter": adapter}


def load_runtime_contract(path: Path) -> RuntimeContract:
    """Load the frozen v1 routing contract and reject ambiguous path layouts."""
    path = reject_symlink_ancestors(path)
    if path.is_symlink() or not path.is_file():
        raise ControlPlaneError("runtime contract must be a real file")
    payload = read_json(path)
    if (type(payload.get("schema_version")) is not int or payload["schema_version"] != 1
            or type(payload.get("adapter_version")) is not int or payload["adapter_version"] != 1):
        raise ControlPlaneError("runtime contract requires schema_version=1 and adapter_version=1")
    team_root = reject_symlink_ancestors(
        _absolute_path(payload.get("canonical_team_root"), "canonical_team_root"))
    runtime_root = reject_symlink_ancestors(
        _absolute_path(payload.get("canonical_runtime_root"), "canonical_runtime_root"))
    consumer = reject_symlink_ancestors(
        _absolute_path(payload.get("canonical_consumer"), "canonical_consumer"))
    if runtime_root.parent != team_root / "runtime" or runtime_root.name != "learning-control-plane":
        raise ControlPlaneError("canonical_runtime_root must be TEAM_ROOT/runtime/learning-control-plane")
    if consumer != team_root / "scripts" / "learning_loop.py":
        raise ControlPlaneError("canonical_consumer must be TEAM_ROOT/scripts/learning_loop.py")
    legacy_commands = payload.get("legacy_commands")
    release_commands = payload.get("release_commands")
    if legacy_commands != list(LEGACY_COMMANDS) or release_commands != list(RELEASE_COMMANDS):
        raise ControlPlaneError("runtime command routing is not the frozen compatibility surface")
    return RuntimeContract(
        path=path.resolve(strict=True),
        adapter_version=1,
        team_root=team_root,
        runtime_root=runtime_root,
        consumer=consumer,
        releases_dir=_relative_path(payload.get("releases_dir"), "releases_dir"),
        active_link=_relative_path(payload.get("active_link"), "active_link"),
        legacy_target=_relative_path(payload.get("legacy_target"), "legacy_target"),
        release_entrypoint=_relative_path(payload.get("release_entrypoint"), "release_entrypoint"),
        adapter_source=_relative_path(payload.get("adapter_source"), "adapter_source"),
    )


def _read_active_link(contract: RuntimeContract, *, allow_absent: bool = False) -> str | None:
    active = _safe_write_path(
        contract.runtime_root / contract.active_link, "active release selector",
        allow_leaf_symlink=True)
    with _directory_fd(active.parent, "active release selector") as directory_fd:
        target = _readlink_at(
            directory_fd, active.name, "active release selector", allow_absent=allow_absent)
    if target is None:
        return None
    try:
        target, _ = _release_target(contract, target, "active release selector target")
    except ControlPlaneError as exc:
        raise ControlPlaneError("active release selector has an unsafe target") from exc
    assert target is not None
    return target


def resolve_active_release(contract: RuntimeContract) -> dict[str, Any]:
    target = _read_active_link(contract)
    assert target is not None
    return _resolve_release_target(contract, target, "active release")


def _atomic_link(link: Path, target: str) -> None:
    link = _safe_write_path(link, "active release selector", allow_leaf_symlink=True)
    with _directory_fd(link.parent, "active release selector", create=True) as directory_fd:
        _atomic_link_at(directory_fd, link.name, target)


def activate_release(contract_path: Path, manifest_path: Path, source_root: Path,
                     release_root: Path, source_revision: str, receipt_path: Path) -> dict[str, Any]:
    """Verify a versioned release, then atomically switch the active selector."""
    contract = load_runtime_contract(contract_path)
    with _runtime_transition_lock(contract):
        return _activate_release_locked(
            contract, manifest_path, source_root, release_root, source_revision, receipt_path)


def _activate_release_locked(contract: RuntimeContract, manifest_path: Path, source_root: Path,
                             release_root: Path, source_revision: str,
                             receipt_path: Path) -> dict[str, Any]:
    if not _is_revision(source_revision):
        raise ControlPlaneError("source_revision must be a lowercase 40-character Git SHA")
    expected_parent = reject_symlink_ancestors(contract.runtime_root / contract.releases_dir)
    release_root = reject_symlink_ancestors(release_root)
    receipt_path = _safe_write_path(receipt_path, "activation receipt")
    if release_root.is_symlink() or not release_root.is_dir() or release_root.parent != expected_parent:
        raise ControlPlaneError("release_root must be a real direct child of the contracted releases directory")
    if not RELEASE_ID_RE.fullmatch(release_root.name):
        raise ControlPlaneError("release directory name is not a safe release ID")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ControlPlaneError("activation receipt path must not already exist")
    previous = _read_active_link(contract, allow_absent=True)
    if previous is not None:
        _resolve_release_target(contract, previous, "current active release")
    verification = verify_deployment(manifest_path, source_root, release_root)
    if not verification["ok"]:
        raise ControlPlaneError(f"release verification failed: {verification['failures']}")
    metadata_path = _safe_write_path(release_root / "release-metadata.json", "release metadata")
    deployed_manifest_path = _safe_write_path(
        release_root / "deployment-manifest.json", "deployed manifest")
    if metadata_path.exists() or metadata_path.is_symlink():
        raise ControlPlaneError("release metadata already exists; release directories are immutable")
    metadata = {
        "schema_version": 1,
        "adapter_version": contract.adapter_version,
        "release_id": release_root.name,
        "source_revision": source_revision,
        "manifest_sha256": sha256_file(manifest_path),
        "contract_sha256": sha256_file(contract.path),
        "entrypoint_sha256": sha256_file(release_root / contract.release_entrypoint),
        "adapter_sha256": sha256_file(release_root / contract.adapter_source),
        "previous_target": previous,
    }
    with _directory_fd(release_root, "release metadata") as release_fd:
        _atomic_write_at(
            release_fd, deployed_manifest_path.name, manifest_path.read_bytes(), mode=0o444,
            field="deployed manifest", replace=False)
        _atomic_json_at(
            release_fd, metadata_path.name, metadata, field="release metadata")
    if (sha256_file(deployed_manifest_path) != metadata["manifest_sha256"]
            or _read_json_path(metadata_path, "release metadata") != metadata):
        raise ControlPlaneError("release metadata path changed during activation")
    activated = f"{contract.releases_dir}/{release_root.name}"
    receipt = {
        "schema_version": 1,
        "operation": "activate-release",
        "contract_sha256": sha256_file(contract.path),
        "manifest_sha256": metadata["manifest_sha256"],
        "source_revision": source_revision,
        "previous_target": previous,
        "activated_target": activated,
    }
    with _directory_fd(receipt_path.parent, "activation receipt", create=True) as receipt_fd:
        _atomic_json_at(receipt_fd, receipt_path.name, receipt, field="activation receipt")
    if _read_json_path(receipt_path, "activation receipt") != receipt:
        raise ControlPlaneError("activation receipt path changed during activation")
    _atomic_link(contract.runtime_root / contract.active_link, activated)
    resolved = resolve_active_release(contract)
    if resolved["target"] != activated:
        raise ControlPlaneError("post-activation resolution mismatch; use exact rollback receipt")
    return {"ok": True, "active_target": activated, "previous_target": previous,
            "receipt": str(receipt_path), "source_revision": source_revision}


def rollback_release(contract_path: Path, receipt_path: Path) -> dict[str, Any]:
    """Restore the exact predecessor recorded by a successful activation."""
    contract = load_runtime_contract(contract_path)
    with _runtime_transition_lock(contract):
        return _rollback_release_locked(contract, receipt_path)


def _rollback_release_locked(contract: RuntimeContract, receipt_path: Path) -> dict[str, Any]:
    receipt_path = reject_symlink_ancestors(receipt_path)
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ControlPlaneError("activation receipt must be a real file")
    receipt = read_json(receipt_path)
    _require_exact_fields(receipt, ACTIVATION_RECEIPT_FIELDS, "activation receipt")
    if (type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
            or receipt.get("operation") != "activate-release"):
        raise ControlPlaneError("unsupported activation receipt")
    if receipt.get("contract_sha256") != sha256_file(contract.path):
        raise ControlPlaneError("activation receipt contract hash mismatch")
    if not _is_sha256(receipt.get("manifest_sha256")):
        raise ControlPlaneError("activation receipt manifest_sha256 is malformed")
    if not _is_revision(receipt.get("source_revision")):
        raise ControlPlaneError("activation receipt source_revision is malformed")
    activated, _ = _release_target(
        contract, receipt.get("activated_target"), "activation receipt activated_target")
    previous, _ = _release_target(
        contract, receipt.get("previous_target"), "activation receipt previous_target",
        allow_none=True)
    if previous == activated:
        raise ControlPlaneError("activation receipt targets must be distinct")
    activated_release = _resolve_release_target(
        contract, activated, "activation receipt activated_target")
    if (activated_release["metadata"]["manifest_sha256"] != receipt["manifest_sha256"]
            or activated_release["metadata"]["source_revision"] != receipt["source_revision"]
            or activated_release["metadata"]["previous_target"] != previous):
        raise ControlPlaneError("activation receipt does not match activated release metadata")
    if previous is not None:
        _resolve_release_target(contract, previous, "activation receipt previous_target")
    current = _read_active_link(contract, allow_absent=True)
    if current == previous:
        return {"ok": True, "idempotent": True, "active_target": previous}
    if current != activated:
        raise ControlPlaneError("active release drifted; refusing non-exact rollback")
    active = _safe_write_path(
        contract.runtime_root / contract.active_link, "active release selector",
        allow_leaf_symlink=True)
    with _directory_fd(active.parent, "active release selector") as active_fd:
        observed_current = _readlink_at(active_fd, active.name, "active release selector")
        if observed_current != current:
            raise ControlPlaneError("active release changed during rollback")
        if previous is None:
            _unlink_at(
                active_fd, active.name, "active release selector", allow_symlink=True)
        else:
            _atomic_link_at(active_fd, active.name, previous)
    observed = _read_active_link(contract, allow_absent=True)
    if observed != previous:
        raise ControlPlaneError("post-rollback active release selector mismatch")
    if previous is not None and resolve_active_release(contract)["target"] != previous:
        raise ControlPlaneError("post-rollback release resolution mismatch")
    return {"ok": True, "idempotent": False, "active_target": previous}


def _validate_adapter_receipt(
        contract: RuntimeContract, receipt: dict[str, Any]) -> tuple[int, str]:
    _require_exact_fields(receipt, ADAPTER_RECEIPT_FIELDS, "adapter receipt")
    if (type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
            or receipt.get("operation") != "install-adapter"):
        raise ControlPlaneError("unsupported adapter receipt")
    if receipt.get("contract_sha256") != sha256_file(contract.path):
        raise ControlPlaneError("adapter receipt contract hash mismatch")
    for name in ("before_sha256", "after_sha256"):
        if not _is_sha256(receipt.get(name)):
            raise ControlPlaneError(f"adapter receipt {name} is malformed")
    before_mode = receipt.get("before_mode")
    if isinstance(before_mode, bool) or not isinstance(before_mode, int) or not 0 <= before_mode <= 0o777:
        raise ControlPlaneError("adapter receipt before_mode is malformed")
    active_target, _ = _release_target(
        contract, receipt.get("active_target"), "adapter receipt active_target")
    active = _resolve_release_target(contract, active_target, "adapter receipt active_target")
    if active["metadata"]["adapter_sha256"] != receipt["after_sha256"]:
        raise ControlPlaneError("adapter receipt does not match active release metadata")
    assert active_target is not None
    return before_mode, active_target


def _validate_legacy_metadata(metadata: dict[str, Any], receipt: dict[str, Any]) -> None:
    _require_exact_fields(metadata, LEGACY_METADATA_FIELDS, "legacy compatibility metadata")
    if (type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1
            or metadata.get("sha256") != receipt["before_sha256"]
            or metadata.get("mode") != receipt["before_mode"]):
        raise ControlPlaneError("legacy compatibility metadata drifted")


def install_adapter(contract_path: Path, adapter_source: Path, receipt_path: Path) -> dict[str, Any]:
    """Install or resume the adapter from its immutable recovery receipt."""
    contract = load_runtime_contract(contract_path)
    with _runtime_transition_lock(contract):
        return _install_adapter_locked(contract, adapter_source, receipt_path)


def _install_adapter_locked(
        contract: RuntimeContract, adapter_source: Path, receipt_path: Path) -> dict[str, Any]:
    active = resolve_active_release(contract)
    adapter_source = reject_symlink_ancestors(adapter_source)
    if (adapter_source.is_symlink() or not adapter_source.is_file()
            or adapter_source.resolve(strict=True) != active["adapter"].resolve(strict=True)):
        raise ControlPlaneError("adapter source must come from the exact active release")
    consumer = _safe_write_path(contract.consumer, "canonical consumer")
    receipt_path = _safe_write_path(receipt_path, "adapter receipt")
    if consumer.is_symlink() or not consumer.is_file():
        raise ControlPlaneError("canonical consumer must be a real file")
    legacy = _safe_write_path(
        contract.runtime_root / contract.legacy_target, "legacy compatibility target")
    legacy_metadata = _safe_write_path(
        legacy.with_name("legacy-metadata.json"), "legacy compatibility metadata")
    runtime_contract = _safe_write_path(
        contract.runtime_root / "runtime-contract.json", "runtime contract copy")
    _require_distinct_managed_paths((
        (receipt_path, "adapter receipt"),
        (consumer, "canonical consumer"),
        (legacy, "legacy compatibility target"),
        (legacy_metadata, "legacy compatibility metadata"),
        (runtime_contract, "runtime contract copy"),
    ))
    adapter_bytes = _read_bytes_path(adapter_source, "adapter source")
    contract_bytes = _read_bytes_path(contract.path, "runtime contract")
    adapter_sha256 = _sha256_bytes(adapter_bytes)
    contract_sha256 = _sha256_bytes(contract_bytes)
    if adapter_sha256 != active["metadata"]["adapter_sha256"]:
        raise ControlPlaneError("adapter source hash does not match the active release")
    if receipt_path.exists() or receipt_path.is_symlink():
        try:
            with _directory_fd(receipt_path.parent, "adapter receipt") as receipt_fd:
                existing_receipt = _read_json_at(
                    receipt_fd, receipt_path.name, "adapter receipt")
                _before_mode, receipt_target = _validate_adapter_receipt(
                    contract, existing_receipt)
            if (receipt_target != active["target"]
                    or existing_receipt["after_sha256"] != adapter_sha256
                    or existing_receipt["contract_sha256"] != contract_sha256):
                raise ControlPlaneError("adapter receipt does not match this installation")
        except ControlPlaneError as exc:
            raise ControlPlaneError(
                "adapter receipt path must not already exist unless it matches "
                "a recoverable installation") from exc
    with (_directory_fd(consumer.parent, "canonical consumer") as consumer_fd,
          _directory_fd(legacy.parent, "legacy compatibility target", create=True) as legacy_fd,
          _directory_fd(contract.runtime_root, "runtime contract copy") as runtime_fd,
          _directory_fd(receipt_path.parent, "adapter receipt", create=True) as receipt_fd):
        consumer_status = _require_regular_leaf(
            consumer_fd, consumer.name, "canonical consumer")
        consumer_bytes = _read_bytes_at(consumer_fd, consumer.name, "canonical consumer")
        consumer_sha256 = _sha256_bytes(consumer_bytes)
        receipt_status = _leaf_stat(receipt_fd, receipt_path.name)
        if receipt_status is None:
            if any(_leaf_stat(directory_fd, name) is not None for directory_fd, name in (
                (legacy_fd, legacy.name),
                (legacy_fd, legacy_metadata.name),
                (runtime_fd, runtime_contract.name),
            )):
                raise ControlPlaneError(
                    "legacy compatibility state already exists without this recovery receipt")
            receipt = {
                "schema_version": 1,
                "operation": "install-adapter",
                "contract_sha256": contract_sha256,
                "before_sha256": consumer_sha256,
                "before_mode": consumer_status.st_mode & 0o777,
                "after_sha256": adapter_sha256,
                "active_target": active["target"],
            }
            _atomic_json_at(receipt_fd, receipt_path.name, receipt, field="adapter receipt")
        else:
            try:
                receipt = _read_json_at(receipt_fd, receipt_path.name, "adapter receipt")
                before_mode, receipt_target = _validate_adapter_receipt(contract, receipt)
            except ControlPlaneError as exc:
                raise ControlPlaneError(
                    "adapter receipt path must not already exist unless it matches "
                    "a recoverable installation") from exc
            if (receipt_target != active["target"]
                    or receipt["after_sha256"] != adapter_sha256
                    or receipt["contract_sha256"] != contract_sha256):
                raise ControlPlaneError("adapter receipt does not match this installation")
            if consumer_sha256 not in {receipt["before_sha256"], receipt["after_sha256"]}:
                raise ControlPlaneError("canonical consumer drifted during adapter recovery")
            if (consumer_sha256 == receipt["before_sha256"]
                    and consumer_status.st_mode & 0o777 != before_mode):
                raise ControlPlaneError("canonical consumer mode drifted during adapter recovery")
            if (consumer_sha256 == receipt["after_sha256"]
                    and stat.S_IMODE(consumer_status.st_mode) != 0o755):
                raise ControlPlaneError("canonical consumer mode drifted during adapter recovery")

        legacy_status = _leaf_stat(legacy_fd, legacy.name)
        if legacy_status is None:
            if consumer_sha256 != receipt["before_sha256"]:
                raise ControlPlaneError(
                    "legacy compatibility target is missing after consumer publication")
            _atomic_write_at(
                legacy_fd, legacy.name, consumer_bytes, mode=receipt["before_mode"],
                field="legacy compatibility target", replace=False)
        else:
            legacy_bytes = _read_bytes_at(
                legacy_fd, legacy.name, "legacy compatibility target")
            if (_sha256_bytes(legacy_bytes) != receipt["before_sha256"]
                    or legacy_status.st_mode & 0o777 != receipt["before_mode"]):
                raise ControlPlaneError("legacy compatibility target drifted")

        if _leaf_stat(legacy_fd, legacy_metadata.name) is None:
            _atomic_json_at(
                legacy_fd, legacy_metadata.name,
                {"schema_version": 1, "sha256": receipt["before_sha256"],
                 "mode": receipt["before_mode"]},
                field="legacy compatibility metadata")
        else:
            _validate_legacy_metadata(
                _read_json_at(
                    legacy_fd, legacy_metadata.name, "legacy compatibility metadata"),
                receipt)

        if _leaf_stat(runtime_fd, runtime_contract.name) is None:
            _atomic_write_at(
                runtime_fd, runtime_contract.name, contract_bytes, mode=0o644,
                field="runtime contract copy", replace=False)
        elif (_sha256_bytes(_read_bytes_at(
                runtime_fd, runtime_contract.name, "runtime contract copy"))
                != receipt["contract_sha256"]):
            raise ControlPlaneError("runtime contract copy drifted")

        if (_sha256_bytes(_read_bytes_path(legacy, "legacy compatibility target"))
                != receipt["before_sha256"]
                or _sha256_bytes(_read_bytes_path(runtime_contract, "runtime contract copy"))
                != receipt["contract_sha256"]
                or _read_json_path(receipt_path, "adapter receipt") != receipt):
            raise ControlPlaneError("compatibility path changed during adapter installation")
        _validate_legacy_metadata(
            _read_json_path(legacy_metadata, "legacy compatibility metadata"), receipt)

        current = _sha256_bytes(_read_bytes_at(
            consumer_fd, consumer.name, "canonical consumer"))
        if current == receipt["before_sha256"]:
            _atomic_write_at(
                consumer_fd, consumer.name, adapter_bytes, mode=0o755,
                field="canonical consumer", replace=True)
        elif current != receipt["after_sha256"]:
            raise ControlPlaneError("canonical consumer drifted during adapter installation")

        installed_status = _require_regular_leaf(
            consumer_fd, consumer.name, "canonical consumer")
        if (_sha256_bytes(_read_bytes_at(
                consumer_fd, consumer.name, "canonical consumer")) != receipt["after_sha256"]
                or stat.S_IMODE(installed_status.st_mode) != 0o755
                or _sha256_bytes(_read_bytes_at(
                    legacy_fd, legacy.name, "legacy compatibility target"))
                != receipt["before_sha256"]
                or _sha256_bytes(_read_bytes_at(
                    runtime_fd, runtime_contract.name, "runtime contract copy"))
                != receipt["contract_sha256"]
                or _read_json_at(receipt_fd, receipt_path.name, "adapter receipt") != receipt):
            raise ControlPlaneError("post-install path binding mismatch")
        _validate_legacy_metadata(
            _read_json_at(
                legacy_fd, legacy_metadata.name, "legacy compatibility metadata"),
            receipt)
    consumer_status = os.stat(consumer, follow_symlinks=False)
    if (_sha256_bytes(_read_bytes_path(legacy, "legacy compatibility target"))
            != receipt["before_sha256"]
            or _sha256_bytes(_read_bytes_path(runtime_contract, "runtime contract copy"))
            != receipt["contract_sha256"]
            or _sha256_bytes(_read_bytes_path(consumer, "canonical consumer"))
            != receipt["after_sha256"]
            or stat.S_IMODE(consumer_status.st_mode) != 0o755
            or _read_json_path(receipt_path, "adapter receipt") != receipt):
        raise ControlPlaneError("post-install path binding mismatch")
    if resolve_active_release(contract)["target"] != receipt["active_target"]:
        raise ControlPlaneError("active release changed during adapter installation")
    return {"ok": True, "consumer": str(consumer), "active_target": active["target"],
            "receipt": str(receipt_path), "legacy_sha256": receipt["before_sha256"]}


def rollback_adapter(contract_path: Path, receipt_path: Path) -> dict[str, Any]:
    """Restore or resume restoration of the exact pre-adapter consumer."""
    contract = load_runtime_contract(contract_path)
    with _runtime_transition_lock(contract):
        return _rollback_adapter_locked(contract, receipt_path)


def _rollback_adapter_locked(contract: RuntimeContract, receipt_path: Path) -> dict[str, Any]:
    receipt_path = reject_symlink_ancestors(receipt_path)
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ControlPlaneError("adapter receipt must be a real file")
    with _directory_fd(receipt_path.parent, "adapter receipt") as receipt_fd:
        receipt = _read_json_at(receipt_fd, receipt_path.name, "adapter receipt")
    before_mode, active_target = _validate_adapter_receipt(contract, receipt)
    if _read_active_link(contract, allow_absent=True) != active_target:
        raise ControlPlaneError("active release selector does not match adapter receipt")
    consumer = _safe_write_path(contract.consumer, "canonical consumer")
    if consumer.is_symlink() or not consumer.is_file():
        raise ControlPlaneError("canonical consumer must be a real file")
    legacy = _safe_write_path(
        contract.runtime_root / contract.legacy_target, "legacy compatibility target")
    legacy_metadata = _safe_write_path(
        legacy.with_name("legacy-metadata.json"), "legacy compatibility metadata")
    runtime_contract = _safe_write_path(
        contract.runtime_root / "runtime-contract.json", "runtime contract copy")
    _require_distinct_managed_paths((
        (receipt_path, "adapter receipt"),
        (consumer, "canonical consumer"),
        (legacy, "legacy compatibility target"),
        (legacy_metadata, "legacy compatibility metadata"),
        (runtime_contract, "runtime contract copy"),
    ))
    with (_directory_fd(consumer.parent, "canonical consumer") as consumer_fd,
          _directory_fd(legacy.parent, "legacy compatibility target", create=True) as legacy_fd,
          _directory_fd(contract.runtime_root, "runtime contract copy") as runtime_fd):
        consumer_status = _require_regular_leaf(
            consumer_fd, consumer.name, "canonical consumer")
        current = _sha256_bytes(_read_bytes_at(
            consumer_fd, consumer.name, "canonical consumer"))
        if current not in {receipt["before_sha256"], receipt["after_sha256"]}:
            raise ControlPlaneError("canonical consumer drifted; refusing non-exact rollback")
        if (current == receipt["before_sha256"]
                and consumer_status.st_mode & 0o777 != before_mode):
            raise ControlPlaneError("restored canonical consumer mode drifted")
        if (current == receipt["after_sha256"]
                and stat.S_IMODE(consumer_status.st_mode) != 0o755):
            raise ControlPlaneError("installed canonical consumer mode drifted")

        legacy_present = _leaf_stat(legacy_fd, legacy.name) is not None
        metadata_present = _leaf_stat(legacy_fd, legacy_metadata.name) is not None
        contract_present = _leaf_stat(runtime_fd, runtime_contract.name) is not None
        initially_complete = not any((legacy_present, metadata_present, contract_present))
        legacy_bytes: bytes | None = None
        if legacy_present:
            legacy_status = _require_regular_leaf(
                legacy_fd, legacy.name, "legacy compatibility target")
            legacy_bytes = _read_bytes_at(
                legacy_fd, legacy.name, "legacy compatibility target")
            if (_sha256_bytes(legacy_bytes) != receipt["before_sha256"]
                    or legacy_status.st_mode & 0o777 != before_mode):
                raise ControlPlaneError("legacy compatibility target drifted")
        elif current == receipt["after_sha256"]:
            raise ControlPlaneError(
                "legacy compatibility target is missing before exact rollback")
        if metadata_present:
            if not legacy_present:
                raise ControlPlaneError("legacy compatibility recovery state is invalid")
            _validate_legacy_metadata(
                _read_json_at(
                    legacy_fd, legacy_metadata.name, "legacy compatibility metadata"),
                receipt)
        if contract_present and (_sha256_bytes(_read_bytes_at(
                runtime_fd, runtime_contract.name, "runtime contract copy"))
                != receipt["contract_sha256"]):
            raise ControlPlaneError("runtime contract copy drifted")

        if current == receipt["after_sha256"]:
            assert legacy_bytes is not None
            _atomic_write_at(
                consumer_fd, consumer.name, legacy_bytes, mode=before_mode,
                field="canonical consumer", replace=True)
        restored_status = _require_regular_leaf(
            consumer_fd, consumer.name, "canonical consumer")
        if (_sha256_bytes(_read_bytes_at(
                consumer_fd, consumer.name, "canonical consumer")) != receipt["before_sha256"]
                or restored_status.st_mode & 0o777 != before_mode):
            raise ControlPlaneError("post-rollback canonical consumer mismatch")

        if contract_present:
            _unlink_at(runtime_fd, runtime_contract.name, "runtime contract copy")
        if metadata_present:
            _unlink_at(
                legacy_fd, legacy_metadata.name, "legacy compatibility metadata")
        if legacy_present:
            _unlink_at(legacy_fd, legacy.name, "legacy compatibility target")
        if any((
            _leaf_stat(legacy_fd, legacy.name),
            _leaf_stat(legacy_fd, legacy_metadata.name),
            _leaf_stat(runtime_fd, runtime_contract.name),
        )):
            raise ControlPlaneError("post-rollback compatibility cleanup mismatch")
    for path, field in (
        (legacy, "legacy compatibility target"),
        (legacy_metadata, "legacy compatibility metadata"),
        (runtime_contract, "runtime contract copy"),
    ):
        _safe_write_path(path, field)
        if path.exists() or path.is_symlink():
            raise ControlPlaneError("post-rollback compatibility cleanup mismatch")
    if _read_active_link(contract, allow_absent=True) != active_target:
        raise ControlPlaneError("active release changed during adapter rollback")
    return {"ok": True, "idempotent": initially_complete, "consumer": str(consumer)}


def verify_live_consumer(contract_path: Path, manifest_path: Path, source_root: Path) -> dict[str, Any]:
    """Prove that the canonical consumer resolves both compatibility routes."""
    contract = load_runtime_contract(contract_path)
    active = resolve_active_release(contract)
    failures: list[str] = []
    verification = verify_deployment(manifest_path, source_root, active["root"])
    failures.extend(verification["failures"])
    metadata = active["metadata"]
    if metadata["manifest_sha256"] != sha256_file(manifest_path):
        failures.append("active release manifest hash mismatch")
    if metadata["contract_sha256"] != sha256_file(contract.path):
        failures.append("active release contract hash mismatch")
    inspection: dict[str, Any] = {}
    legacy = reject_symlink_ancestors(contract.runtime_root / contract.legacy_target)
    legacy_metadata_path = reject_symlink_ancestors(legacy.with_name("legacy-metadata.json"))
    if legacy.is_symlink() or not legacy.is_file() or legacy_metadata_path.is_symlink() or not legacy_metadata_path.is_file():
        failures.append("legacy compatibility target or metadata is missing or unsafe")
    else:
        legacy_metadata = read_json(legacy_metadata_path)
        if set(legacy_metadata) != LEGACY_METADATA_FIELDS:
            failures.append("legacy compatibility metadata fields mismatch")
        elif (type(legacy_metadata.get("schema_version")) is not int
              or legacy_metadata["schema_version"] != 1
              or legacy_metadata.get("sha256") != sha256_file(legacy)
              or isinstance(legacy_metadata.get("mode"), bool)
              or not isinstance(legacy_metadata.get("mode"), int)):
            failures.append("legacy compatibility target hash mismatch")
    try:
        with _directory_fd(
                contract.consumer.parent, "canonical consumer") as consumer_directory_fd:
            consumer_fd, consumer_status = _open_regular_leaf_fd(
                consumer_directory_fd, contract.consumer.name, "canonical consumer")
            try:
                consumer_identity = _file_identity(consumer_status)
                consumer_snapshot = _read_bytes_fd(consumer_fd)
                if _sha256_bytes(consumer_snapshot) != metadata["adapter_sha256"]:
                    failures.append(
                        "canonical consumer does not equal the active reviewed adapter")
                if stat.S_IMODE(consumer_status.st_mode) != 0o755:
                    failures.append("canonical consumer mode must be 0755")
                if not failures:
                    inspection_bootstrap = (
                        "import sys;"
                        "path=sys.argv[1];source=sys.stdin.buffer.read();"
                        "sys.argv=[path,*sys.argv[2:]];"
                        "exec(compile(source,path,'exec'),"
                        "{'__name__':'__main__','__file__':path})"
                    )
                    completed = subprocess.run(
                        [sys.executable, "-c", inspection_bootstrap,
                         str(contract.consumer), "--adapter-inspect"],
                        input=consumer_snapshot, check=False, capture_output=True,
                        timeout=10,
                    )
                    try:
                        descriptor_sha256 = _sha256_fd(consumer_fd)
                        descriptor_status = os.fstat(consumer_fd)
                        with _directory_fd(
                                contract.consumer.parent,
                                "post-inspection canonical consumer") as current_directory_fd:
                            path_fd, path_status = _open_regular_leaf_fd(
                                current_directory_fd, contract.consumer.name,
                                "post-inspection canonical consumer")
                            try:
                                path_sha256 = _sha256_fd(path_fd)
                                path_status = os.fstat(path_fd)
                            finally:
                                os.close(path_fd)
                    except (ControlPlaneError, OSError):
                        failures.append("canonical consumer changed during inspection")
                    else:
                        if (consumer_identity != _file_identity(path_status)
                                or consumer_identity != _file_identity(descriptor_status)
                                or descriptor_sha256 != metadata["adapter_sha256"]
                                or path_sha256 != metadata["adapter_sha256"]):
                            failures.append("canonical consumer changed during inspection")
                    if completed.returncode != 0:
                        failures.append(
                            "canonical consumer inspection failed: "
                            f"{completed.stderr.decode('utf-8', errors='replace').strip()}")
                    else:
                        try:
                            inspection = json.loads(completed.stdout)
                        except json.JSONDecodeError as exc:
                            failures.append(
                                "canonical consumer inspection returned malformed JSON: "
                                f"{exc}")
            finally:
                os.close(consumer_fd)
    except ControlPlaneError:
        failures.append("canonical consumer is missing or unsafe")
    expected = {
        "active_target": active["target"],
        "release_entrypoint": str(active["entrypoint"]),
        "legacy_target": str(legacy),
        "source_revision": metadata["source_revision"],
        "legacy_commands": list(LEGACY_COMMANDS),
        "release_commands": list(RELEASE_COMMANDS),
    }
    if inspection and any(inspection.get(key) != value for key, value in expected.items()):
        failures.append("canonical consumer inspection does not match the frozen routing contract")
    return {"schema_version": 1, "ok": not failures, "failures": failures,
            "source_revision": metadata["source_revision"], "active_target": active["target"],
            "verified": verification["verified"], "inspection": inspection}
