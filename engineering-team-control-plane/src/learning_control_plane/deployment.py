"""Exact source-to-runtime hash and runtime-closure verification."""
from pathlib import Path
from typing import Any
from .common import (ControlPlaneError, SHA256_RE, read_json, reject_symlink_ancestors,
                     secure_path, sha256_file)

IMPORTABLE_SUFFIXES = {".py", ".pyw", ".pyc", ".pyo", ".so", ".pyd", ".dll", ".dylib"}
SCRIPT_SUFFIXES = {".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd"}

def _is_importable_or_executable(path: Path) -> bool:
    if path.suffix.lower() in IMPORTABLE_SUFFIXES | SCRIPT_SUFFIXES:
        return True
    if path.stat().st_mode & 0o111:
        return True
    with path.open("rb") as handle:
        return handle.read(2) == b"#!"

def _runtime_closure(runtime_root: Path, runtime_names: set[str]) -> list[str]:
    failures = []
    for path in runtime_root.glob("**/*"):
        relative = path.relative_to(runtime_root).as_posix()
        if path.is_symlink():
            failures.append(f"runtime closure contains symlink: {relative}")
            continue
        if not path.is_file():
            continue
        try:
            executable = _is_importable_or_executable(path)
        except OSError as exc:
            failures.append(f"runtime closure cannot inspect {relative}: {exc}")
            continue
        if executable and relative not in runtime_names:
            failures.append(f"unmanifested importable/executable runtime file: {relative}")
    return failures

def verify_deployment(manifest_path: Path, source_root: Path, runtime_root: Path) -> dict[str, Any]:
    manifest_path = reject_symlink_ancestors(manifest_path)
    source_root = reject_symlink_ancestors(source_root)
    runtime_root = reject_symlink_ancestors(runtime_root)
    if manifest_path.is_symlink() or not manifest_path.is_file(): raise ControlPlaneError("deployment manifest must be a real file")
    if source_root.is_symlink() or not source_root.is_dir(): raise ControlPlaneError("source root must be a real directory")
    if runtime_root.is_symlink() or not runtime_root.is_dir(): raise ControlPlaneError("runtime root must be a real directory")
    source_root, runtime_root = source_root.resolve(strict=True), runtime_root.resolve(strict=True)
    manifest = read_json(manifest_path); files = manifest.get("files")
    if manifest.get("schema_version") != 1 or not isinstance(files, list) or not files: raise ControlPlaneError("deployment manifest requires schema_version=1 and non-empty files")
    failures, verified, runtime_names, source_names = [], [], set(), set()
    for index, item in enumerate(files):
        if not isinstance(item, dict): failures.append(f"files[{index}] is malformed"); continue
        source_name, runtime_name, expected = item.get("source"), item.get("runtime"), item.get("sha256")
        if not all(isinstance(value, str) for value in (source_name, runtime_name, expected)): failures.append(f"files[{index}] has malformed fields"); continue
        if not SHA256_RE.fullmatch(expected): failures.append(f"files[{index}] has malformed SHA-256"); continue
        if source_name in source_names: failures.append(f"duplicate source manifest entry: {source_name}"); continue
        if runtime_name in runtime_names: failures.append(f"duplicate runtime manifest entry: {runtime_name}"); continue
        source_names.add(source_name); runtime_names.add(runtime_name)
        try:
            source, runtime = secure_path(source_root, source_name), secure_path(runtime_root, runtime_name)
            if not source.is_file() or not runtime.is_file(): raise ControlPlaneError("source/runtime is not a regular file")
            if sha256_file(source) != expected: failures.append(f"source hash mismatch: {source_name}")
            elif sha256_file(runtime) != expected: failures.append(f"runtime hash mismatch: {runtime_name}")
            else: verified.append(runtime_name)
        except (ControlPlaneError, OSError) as exc: failures.append(f"{source_name} -> {runtime_name}: {exc}")
    failures.extend(_runtime_closure(runtime_root, runtime_names))
    return {"schema_version": 1, "verified": verified, "failures": failures, "ok": not failures}
