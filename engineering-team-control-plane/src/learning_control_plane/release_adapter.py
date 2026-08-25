"""Versioned compatibility-adapter activation and rollback controls."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .common import (ControlPlaneError, SHA256_RE, atomic_json, atomic_write, read_json,
                     reject_symlink_ancestors, sha256_file)
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
    if not active.exists() and not active.is_symlink():
        if allow_absent:
            return None
        raise ControlPlaneError("active release link is missing")
    if not active.is_symlink():
        raise ControlPlaneError("active release selector must be a symlink")
    target = os.readlink(active)
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
    if link.exists() and not link.is_symlink():
        raise ControlPlaneError("active release selector must not replace a real path")
    link.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink_ancestors(link.parent)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{link.name}.", dir=link.parent))
    temporary = temporary_dir / "selector"
    try:
        os.symlink(target, temporary)
        os.replace(temporary, link)
        directory_fd = os.open(link.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        temporary_dir.rmdir()


def activate_release(contract_path: Path, manifest_path: Path, source_root: Path,
                     release_root: Path, source_revision: str, receipt_path: Path) -> dict[str, Any]:
    """Verify a versioned release, then atomically switch the active selector."""
    contract = load_runtime_contract(contract_path)
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
    atomic_write(deployed_manifest_path, manifest_path.read_bytes(), mode=0o444)
    atomic_json(metadata_path, metadata)
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
    atomic_json(receipt_path, receipt)
    _atomic_link(contract.runtime_root / contract.active_link, activated)
    resolved = resolve_active_release(contract)
    if resolved["target"] != activated:
        raise ControlPlaneError("post-activation resolution mismatch; use exact rollback receipt")
    return {"ok": True, "active_target": activated, "previous_target": previous,
            "receipt": str(receipt_path), "source_revision": source_revision}


def rollback_release(contract_path: Path, receipt_path: Path) -> dict[str, Any]:
    """Restore the exact predecessor recorded by a successful activation."""
    contract = load_runtime_contract(contract_path)
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
    if previous is None:
        active.unlink()
        directory_fd = os.open(active.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    else:
        _atomic_link(active, previous)
    observed = _read_active_link(contract, allow_absent=True)
    if observed != previous:
        raise ControlPlaneError("post-rollback active release selector mismatch")
    if previous is not None and resolve_active_release(contract)["target"] != previous:
        raise ControlPlaneError("post-rollback release resolution mismatch")
    return {"ok": True, "idempotent": False, "active_target": previous}


def install_adapter(contract_path: Path, adapter_source: Path, receipt_path: Path) -> dict[str, Any]:
    """Atomically replace the canonical consumer after preserving its exact legacy bytes."""
    contract = load_runtime_contract(contract_path)
    active = resolve_active_release(contract)
    adapter_source = reject_symlink_ancestors(adapter_source)
    if (adapter_source.is_symlink() or not adapter_source.is_file()
            or adapter_source.resolve(strict=True) != active["adapter"].resolve(strict=True)):
        raise ControlPlaneError("adapter source must come from the exact active release")
    consumer = _safe_write_path(contract.consumer, "canonical consumer")
    receipt_path = _safe_write_path(receipt_path, "adapter receipt")
    if consumer.is_symlink() or not consumer.is_file():
        raise ControlPlaneError("canonical consumer must be a real file")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ControlPlaneError("adapter receipt path must not already exist")
    legacy = _safe_write_path(
        contract.runtime_root / contract.legacy_target, "legacy compatibility target")
    legacy_metadata = _safe_write_path(
        legacy.with_name("legacy-metadata.json"), "legacy compatibility metadata")
    runtime_contract = _safe_write_path(
        contract.runtime_root / "runtime-contract.json", "runtime contract copy")
    if any(path.exists() or path.is_symlink()
           for path in (legacy, legacy_metadata, runtime_contract)):
        raise ControlPlaneError("legacy compatibility state already exists; use release switching for upgrades")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink_ancestors(legacy.parent)
    original = consumer.read_bytes()
    original_mode = consumer.stat().st_mode & 0o777
    atomic_write(legacy, original, mode=original_mode)
    atomic_json(legacy_metadata, {"schema_version": 1, "sha256": sha256_file(legacy), "mode": original_mode})
    atomic_write(runtime_contract, contract.path.read_bytes(), mode=0o644)
    receipt = {
        "schema_version": 1,
        "operation": "install-adapter",
        "contract_sha256": sha256_file(contract.path),
        "before_sha256": sha256_file(legacy),
        "before_mode": original_mode,
        "after_sha256": sha256_file(adapter_source),
        "active_target": active["target"],
    }
    atomic_json(receipt_path, receipt)
    atomic_write(consumer, adapter_source.read_bytes(), mode=0o755)
    if sha256_file(consumer) != receipt["after_sha256"]:
        raise ControlPlaneError("canonical consumer adapter hash mismatch; use exact rollback receipt")
    if resolve_active_release(contract)["target"] != receipt["active_target"]:
        raise ControlPlaneError("active release changed during adapter installation")
    persisted_metadata = read_json(legacy_metadata)
    if (set(persisted_metadata) != LEGACY_METADATA_FIELDS
            or persisted_metadata.get("schema_version") != 1
            or persisted_metadata.get("sha256") != receipt["before_sha256"]
            or persisted_metadata.get("mode") != receipt["before_mode"]
            or sha256_file(runtime_contract) != receipt["contract_sha256"]):
        raise ControlPlaneError("post-install compatibility state mismatch")
    return {"ok": True, "consumer": str(consumer), "active_target": active["target"],
            "receipt": str(receipt_path), "legacy_sha256": receipt["before_sha256"]}


def rollback_adapter(contract_path: Path, receipt_path: Path) -> dict[str, Any]:
    """Atomically restore the exact pre-adapter canonical consumer."""
    contract = load_runtime_contract(contract_path)
    receipt_path = reject_symlink_ancestors(receipt_path)
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ControlPlaneError("adapter receipt must be a real file")
    receipt = read_json(receipt_path)
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
    current_target = _read_active_link(contract, allow_absent=True)
    if current_target != active_target:
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
    auxiliary = (legacy, legacy_metadata, runtime_contract)
    present = [path.exists() or path.is_symlink() for path in auxiliary]
    current = sha256_file(consumer)
    if not any(present):
        if current != receipt["before_sha256"]:
            raise ControlPlaneError("legacy compatibility state is missing before exact rollback")
        if consumer.stat().st_mode & 0o777 != before_mode:
            raise ControlPlaneError("restored canonical consumer mode drifted")
        return {"ok": True, "idempotent": True, "consumer": str(consumer)}
    if not all(present):
        raise ControlPlaneError("legacy compatibility state is incomplete")
    if legacy.is_symlink() or not legacy.is_file() or sha256_file(legacy) != receipt["before_sha256"]:
        raise ControlPlaneError("legacy compatibility target drifted")
    if legacy_metadata.is_symlink() or not legacy_metadata.is_file():
        raise ControlPlaneError("legacy compatibility metadata is missing or unsafe")
    metadata = read_json(legacy_metadata)
    _require_exact_fields(metadata, LEGACY_METADATA_FIELDS, "legacy compatibility metadata")
    if (type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1
            or metadata.get("sha256") != receipt["before_sha256"]
            or metadata.get("mode") != before_mode):
        raise ControlPlaneError("legacy compatibility metadata drifted")
    if runtime_contract.is_symlink() or not runtime_contract.is_file():
        raise ControlPlaneError("runtime contract copy is missing or unsafe")
    if sha256_file(runtime_contract) != receipt["contract_sha256"]:
        raise ControlPlaneError("runtime contract copy drifted")
    if current != receipt["after_sha256"]:
        raise ControlPlaneError("canonical consumer drifted; refusing non-exact rollback")
    atomic_write(consumer, legacy.read_bytes(), mode=before_mode)
    if sha256_file(consumer) != receipt["before_sha256"] or consumer.stat().st_mode & 0o777 != before_mode:
        raise ControlPlaneError("post-rollback canonical consumer mismatch")
    for path in (legacy_metadata, legacy, runtime_contract):
        path.unlink()
    for directory in {legacy.parent, runtime_contract.parent}:
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    if any(path.exists() or path.is_symlink() for path in auxiliary):
        raise ControlPlaneError("post-rollback compatibility cleanup mismatch")
    if _read_active_link(contract, allow_absent=True) != active_target:
        raise ControlPlaneError("active release changed during adapter rollback")
    return {"ok": True, "idempotent": False, "consumer": str(consumer)}


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
    if contract.consumer.is_symlink() or not contract.consumer.is_file():
        failures.append("canonical consumer is missing or unsafe")
    elif sha256_file(contract.consumer) != metadata["adapter_sha256"]:
        failures.append("canonical consumer does not equal the active reviewed adapter")
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
    inspection: dict[str, Any] = {}
    if not failures:
        completed = subprocess.run(
            [sys.executable, str(contract.consumer), "--adapter-inspect"],
            check=False, capture_output=True, text=True, timeout=10,
        )
        if completed.returncode != 0:
            failures.append(f"canonical consumer inspection failed: {completed.stderr.strip()}")
        else:
            try:
                inspection = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                failures.append(f"canonical consumer inspection returned malformed JSON: {exc}")
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
