#!/usr/bin/env python3
"""Compatibility dispatcher for the canonical engineering learning-loop consumer."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath


ADAPTER_VERSION = 1
LEGACY_COMMANDS = ("bootstrap", "preflight", "close", "metrics", "stale")
RELEASE_COMMANDS = ("audit-task", "audit-system")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def _fail(message: str) -> "NoReturn":
    print(f"ERROR: compatibility adapter: {message}", file=sys.stderr)
    raise SystemExit(2)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _state() -> dict[str, object]:
    consumer = Path(__file__).absolute()
    if consumer.is_symlink() or not consumer.is_file() or consumer.name != "learning_loop.py":
        _fail("canonical consumer must be a real scripts/learning_loop.py file")
    team_root = consumer.parent.parent
    runtime_root = team_root / "runtime" / "learning-control-plane"
    contract_path = runtime_root / "runtime-contract.json"
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read runtime contract: {exc}")
    expected_paths = {
        "canonical_team_root": str(team_root),
        "canonical_runtime_root": str(runtime_root),
        "canonical_consumer": str(consumer),
    }
    if contract.get("schema_version") != 1 or contract.get("adapter_version") != ADAPTER_VERSION:
        _fail("unsupported runtime contract version")
    if any(contract.get(key) != value for key, value in expected_paths.items()):
        _fail("runtime contract does not name this canonical consumer")
    if contract.get("legacy_commands") != list(LEGACY_COMMANDS) or contract.get("release_commands") != list(RELEASE_COMMANDS):
        _fail("runtime contract command surface mismatch")
    active = runtime_root / "active"
    if not active.is_symlink():
        _fail("active release selector is missing or is not a symlink")
    target = os.readlink(active)
    parts = PurePosixPath(target).parts
    if len(parts) != 2 or parts[0] != "releases" or not RELEASE_ID_RE.fullmatch(parts[1]):
        _fail("active release selector target is unsafe")
    release = runtime_root / parts[0] / parts[1]
    if release.is_symlink() or not release.is_dir():
        _fail("active release is missing or unsafe")
    metadata_path = release / "release-metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read active release metadata: {exc}")
    if (metadata.get("schema_version") != 1 or metadata.get("adapter_version") != ADAPTER_VERSION
            or metadata.get("release_id") != release.name
            or not REVISION_RE.fullmatch(str(metadata.get("source_revision", "")))):
        _fail("active release metadata identity is malformed")
    if (not SHA256_RE.fullmatch(str(metadata.get("contract_sha256", "")))
            or _sha256(contract_path) != metadata["contract_sha256"]):
        _fail("active release runtime contract hash mismatch")
    manifest_path = release / "deployment-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read active deployment manifest: {exc}")
    if (not SHA256_RE.fullmatch(str(metadata.get("manifest_sha256", "")))
            or _sha256(manifest_path) != metadata["manifest_sha256"]
            or manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list)):
        _fail("active deployment manifest identity is malformed")
    runtime_names: set[str] = set()
    for item in manifest["files"]:
        if not isinstance(item, dict):
            _fail("active deployment manifest contains a malformed entry")
        runtime_name, expected = item.get("runtime"), item.get("sha256")
        if not isinstance(runtime_name, str) or not SHA256_RE.fullmatch(str(expected)):
            _fail("active deployment manifest contains malformed fields")
        relative = PurePosixPath(runtime_name)
        if (relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts)
                or runtime_name in runtime_names):
            _fail("active deployment manifest contains an unsafe or duplicate runtime path")
        runtime_names.add(runtime_name)
        deployed = release.joinpath(*relative.parts)
        if deployed.is_symlink() or not deployed.is_file() or _sha256(deployed) != expected:
            _fail(f"active reviewed release file hash mismatch: {runtime_name}")
    executable_suffixes = {".py", ".pyw", ".pyc", ".pyo", ".so", ".pyd", ".dll", ".dylib",
                           ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd"}
    for deployed in release.glob("**/*"):
        relative = deployed.relative_to(release).as_posix()
        if deployed.is_symlink():
            _fail(f"active release closure contains symlink: {relative}")
        if not deployed.is_file() or relative in {"release-metadata.json", "deployment-manifest.json"}:
            continue
        try:
            executable = (deployed.suffix.lower() in executable_suffixes
                          or bool(deployed.stat().st_mode & 0o111)
                          or deployed.read_bytes()[:2] == b"#!")
        except OSError as exc:
            _fail(f"cannot inspect active release closure {relative}: {exc}")
        if executable and relative not in runtime_names:
            _fail(f"unmanifested executable in active release: {relative}")
    entrypoint = release / "scripts" / "learning_control_plane.py"
    release_adapter = release / "scripts" / "learning_loop_adapter.py"
    legacy = runtime_root / "compat" / "v1" / "learning_loop.py"
    for path, field in ((entrypoint, "entrypoint_sha256"), (release_adapter, "adapter_sha256")):
        if path.is_symlink() or not path.is_file() or not SHA256_RE.fullmatch(str(metadata.get(field, ""))):
            _fail(f"active release {field} target is missing or malformed")
        if _sha256(path) != metadata[field]:
            _fail(f"active release {field} hash mismatch")
    if _sha256(consumer) != metadata["adapter_sha256"]:
        _fail("canonical consumer does not equal the active reviewed adapter")
    legacy_metadata_path = legacy.with_name("legacy-metadata.json")
    try:
        legacy_metadata = json.loads(legacy_metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read legacy compatibility metadata: {exc}")
    if (legacy.is_symlink() or not legacy.is_file()
            or not SHA256_RE.fullmatch(str(legacy_metadata.get("sha256", "")))
            or _sha256(legacy) != legacy_metadata["sha256"]):
        _fail("legacy compatibility target hash mismatch")
    return {
        "active_target": target,
        "release_entrypoint": str(entrypoint),
        "legacy_target": str(legacy),
        "source_revision": metadata["source_revision"],
        "legacy_commands": list(LEGACY_COMMANDS),
        "release_commands": list(RELEASE_COMMANDS),
    }


def main() -> int:
    state = _state()
    if sys.argv[1:] == ["--adapter-inspect"]:
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if len(sys.argv) < 2:
        _fail("a lifecycle command is required")
    command = sys.argv[1]
    if command in LEGACY_COMMANDS:
        target = str(state["legacy_target"])
    elif command in RELEASE_COMMANDS:
        target = str(state["release_entrypoint"])
    else:
        _fail(f"unsupported command: {command}")
    os.execv(sys.executable, [sys.executable, target, *sys.argv[1:]])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
