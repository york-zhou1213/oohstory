"""Exact source-to-runtime hash verification for controlled deployment."""
from pathlib import Path
from typing import Any
from .common import (ControlPlaneError, SHA256_RE, read_json, reject_symlink_ancestors,
                     secure_path, sha256_file)

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
    failures, verified = [], []
    for index, item in enumerate(files):
        if not isinstance(item, dict): failures.append(f"files[{index}] is malformed"); continue
        source_name, runtime_name, expected = item.get("source"), item.get("runtime"), item.get("sha256")
        if not all(isinstance(value, str) for value in (source_name, runtime_name, expected)): failures.append(f"files[{index}] has malformed fields"); continue
        if not SHA256_RE.fullmatch(expected): failures.append(f"files[{index}] has malformed SHA-256"); continue
        try:
            source, runtime = secure_path(source_root, source_name), secure_path(runtime_root, runtime_name)
            if not source.is_file() or not runtime.is_file(): raise ControlPlaneError("source/runtime is not a regular file")
            if sha256_file(source) != expected: failures.append(f"source hash mismatch: {source_name}")
            elif sha256_file(runtime) != expected: failures.append(f"runtime hash mismatch: {runtime_name}")
            else: verified.append(runtime_name)
        except (ControlPlaneError, OSError) as exc: failures.append(f"{source_name} -> {runtime_name}: {exc}")
    return {"schema_version": 1, "verified": verified, "failures": failures, "ok": not failures}
