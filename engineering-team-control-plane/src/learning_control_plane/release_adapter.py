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
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ControlPlaneError(f"{field} must be a safe relative POSIX path")
    return path.as_posix()


def _absolute_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ControlPlaneError(f"{field} must be an absolute path")
    return Path(os.path.abspath(value))


def load_runtime_contract(path: Path) -> RuntimeContract:
    """Load the frozen v1 routing contract and reject ambiguous path layouts."""
    path = reject_symlink_ancestors(path)
    if path.is_symlink() or not path.is_file():
        raise ControlPlaneError("runtime contract must be a real file")
    payload = read_json(path)
    if payload.get("schema_version") != 1 or payload.get("adapter_version") != 1:
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
    active = contract.runtime_root / contract.active_link
    if not active.exists() and not active.is_symlink():
        if allow_absent:
            return None
        raise ControlPlaneError("active release link is missing")
    if not active.is_symlink():
        raise ControlPlaneError("active release selector must be a symlink")
    target = os.readlink(active)
    parts = PurePosixPath(target).parts
    if len(parts) != 2 or parts[0] != contract.releases_dir or not RELEASE_ID_RE.fullmatch(parts[1]):
        raise ControlPlaneError("active release selector has an unsafe target")
    release = contract.runtime_root / parts[0] / parts[1]
    if release.is_symlink() or not release.is_dir():
        raise ControlPlaneError("active release target must be a real directory")
    return PurePosixPath(*parts).as_posix()


def resolve_active_release(contract: RuntimeContract) -> dict[str, Any]:
    target = _read_active_link(contract)
    assert target is not None
    release_root = contract.runtime_root / target
    metadata_path = release_root / "release-metadata.json"
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise ControlPlaneError("active release metadata is missing or unsafe")
    metadata = read_json(metadata_path)
    if metadata.get("schema_version") != 1 or metadata.get("adapter_version") != contract.adapter_version:
        raise ControlPlaneError("active release metadata version is unsupported")
    if metadata.get("release_id") != release_root.name:
        raise ControlPlaneError("active release metadata identity mismatch")
    if not REVISION_RE.fullmatch(str(metadata.get("source_revision", ""))):
        raise ControlPlaneError("active release source_revision is malformed")
    for field in ("manifest_sha256", "contract_sha256", "entrypoint_sha256", "adapter_sha256"):
        if not SHA256_RE.fullmatch(str(metadata.get(field, ""))):
            raise ControlPlaneError(f"active release {field} is malformed")
    entrypoint = release_root / contract.release_entrypoint
    adapter = release_root / contract.adapter_source
    if entrypoint.is_symlink() or not entrypoint.is_file() or adapter.is_symlink() or not adapter.is_file():
        raise ControlPlaneError("active release entrypoint or adapter is missing or unsafe")
    if sha256_file(entrypoint) != metadata["entrypoint_sha256"]:
        raise ControlPlaneError("active release entrypoint hash mismatch")
    if sha256_file(adapter) != metadata["adapter_sha256"]:
        raise ControlPlaneError("active release adapter hash mismatch")
    return {"target": target, "root": release_root, "metadata": metadata,
            "entrypoint": entrypoint, "adapter": adapter}


def _atomic_link(link: Path, target: str) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
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
    if not REVISION_RE.fullmatch(source_revision):
        raise ControlPlaneError("source_revision must be a lowercase 40-character Git SHA")
    expected_parent = contract.runtime_root / contract.releases_dir
    if release_root.is_symlink() or not release_root.is_dir() or release_root.parent != expected_parent:
        raise ControlPlaneError("release_root must be a real direct child of the contracted releases directory")
    if not RELEASE_ID_RE.fullmatch(release_root.name):
        raise ControlPlaneError("release directory name is not a safe release ID")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ControlPlaneError("activation receipt path must not already exist")
    verification = verify_deployment(manifest_path, source_root, release_root)
    if not verification["ok"]:
        raise ControlPlaneError(f"release verification failed: {verification['failures']}")
    metadata_path = release_root / "release-metadata.json"
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
    }
    atomic_write(release_root / "deployment-manifest.json", manifest_path.read_bytes(), mode=0o444)
    atomic_json(metadata_path, metadata)
    previous = _read_active_link(contract, allow_absent=True)
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
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ControlPlaneError("activation receipt must be a real file")
    receipt = read_json(receipt_path)
    if receipt.get("schema_version") != 1 or receipt.get("operation") != "activate-release":
        raise ControlPlaneError("unsupported activation receipt")
    if receipt.get("contract_sha256") != sha256_file(contract.path):
        raise ControlPlaneError("activation receipt contract hash mismatch")
    current = _read_active_link(contract, allow_absent=True)
    activated = receipt.get("activated_target")
    previous = receipt.get("previous_target")
    if current == previous:
        return {"ok": True, "idempotent": True, "active_target": previous}
    if current != activated:
        raise ControlPlaneError("active release drifted; refusing non-exact rollback")
    active = contract.runtime_root / contract.active_link
    if previous is None:
        active.unlink()
        directory_fd = os.open(active.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    else:
        _atomic_link(active, str(previous))
    return {"ok": True, "idempotent": False, "active_target": previous}


def install_adapter(contract_path: Path, adapter_source: Path, receipt_path: Path) -> dict[str, Any]:
    """Atomically replace the canonical consumer after preserving its exact legacy bytes."""
    contract = load_runtime_contract(contract_path)
    active = resolve_active_release(contract)
    if adapter_source.resolve(strict=True) != active["adapter"].resolve(strict=True):
        raise ControlPlaneError("adapter source must come from the exact active release")
    if contract.consumer.is_symlink() or not contract.consumer.is_file():
        raise ControlPlaneError("canonical consumer must be a real file")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ControlPlaneError("adapter receipt path must not already exist")
    legacy = contract.runtime_root / contract.legacy_target
    if legacy.exists() or legacy.is_symlink():
        raise ControlPlaneError("legacy compatibility target already exists; use release switching for upgrades")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    original = contract.consumer.read_bytes()
    original_mode = contract.consumer.stat().st_mode & 0o777
    atomic_write(legacy, original, mode=original_mode)
    legacy_metadata = legacy.with_name("legacy-metadata.json")
    atomic_json(legacy_metadata, {"schema_version": 1, "sha256": sha256_file(legacy), "mode": original_mode})
    runtime_contract = contract.runtime_root / "runtime-contract.json"
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
    atomic_write(contract.consumer, adapter_source.read_bytes(), mode=0o755)
    if sha256_file(contract.consumer) != receipt["after_sha256"]:
        raise ControlPlaneError("canonical consumer adapter hash mismatch; use exact rollback receipt")
    return {"ok": True, "consumer": str(contract.consumer), "active_target": active["target"],
            "receipt": str(receipt_path), "legacy_sha256": receipt["before_sha256"]}


def rollback_adapter(contract_path: Path, receipt_path: Path) -> dict[str, Any]:
    """Atomically restore the exact pre-adapter canonical consumer."""
    contract = load_runtime_contract(contract_path)
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ControlPlaneError("adapter receipt must be a real file")
    receipt = read_json(receipt_path)
    if receipt.get("schema_version") != 1 or receipt.get("operation") != "install-adapter":
        raise ControlPlaneError("unsupported adapter receipt")
    if receipt.get("contract_sha256") != sha256_file(contract.path):
        raise ControlPlaneError("adapter receipt contract hash mismatch")
    legacy = contract.runtime_root / contract.legacy_target
    if legacy.is_symlink() or not legacy.is_file() or sha256_file(legacy) != receipt.get("before_sha256"):
        raise ControlPlaneError("legacy compatibility target drifted")
    current = sha256_file(contract.consumer)
    if current == receipt.get("before_sha256"):
        return {"ok": True, "idempotent": True, "consumer": str(contract.consumer)}
    if current != receipt.get("after_sha256"):
        raise ControlPlaneError("canonical consumer drifted; refusing non-exact rollback")
    atomic_write(contract.consumer, legacy.read_bytes(), mode=int(receipt["before_mode"]))
    return {"ok": True, "idempotent": False, "consumer": str(contract.consumer)}


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
    legacy = contract.runtime_root / contract.legacy_target
    legacy_metadata_path = legacy.with_name("legacy-metadata.json")
    if legacy.is_symlink() or not legacy.is_file() or legacy_metadata_path.is_symlink() or not legacy_metadata_path.is_file():
        failures.append("legacy compatibility target or metadata is missing or unsafe")
    else:
        legacy_metadata = read_json(legacy_metadata_path)
        if legacy_metadata.get("sha256") != sha256_file(legacy):
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
