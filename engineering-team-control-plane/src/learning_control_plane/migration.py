"""Reference-aware duplicate-ID migration with verified backup and rollback."""
from __future__ import annotations
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .common import (AGENTS, EVENT_HEADER_RE, EVENT_ID_RE, ControlPlaneError, atomic_write, iter_real_files,
    read_json, reject_symlink_ancestors, relative_posix, secure_path, sha256_file, validate_root)

MIGRATION_SCHEMA = 1

def migration_inputs(root: Path) -> list[Path]:
    files: list[Path] = []
    for agent in AGENTS: files.extend(iter_real_files(root / agent / "learnings", ("**/*.md", "**/*.json", "**/*.jsonl")))
    files.extend(iter_real_files(root / "team-learnings", ("**/*.md", "**/*.json", "**/*.jsonl")))
    return sorted(set(files))

def load_resolutions(path: Path | None) -> tuple[dict[str, str], str | None]:
    if path is None: return {}, None
    path = reject_symlink_ancestors(path)
    if path.is_symlink() or not path.is_file(): raise ControlPlaneError("resolution file must be a real file")
    value = read_json(path); references = value.get("references")
    if value.get("schema_version") != 1 or not isinstance(references, dict): raise ControlPlaneError("resolution file requires schema_version=1 and references object")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in references.items()): raise ControlPlaneError("resolution reference keys and targets must be strings")
    return references, sha256_file(path)

def _definition_locator(path: str, line: int) -> str: return f"{path}:{line}"
def _reference_locator(path: str, line: int, column: int, event_id: str) -> str:
    return f"{path}:{line}:{column}:{event_id}"

def plan_migration(root: Path, *, resolutions: dict[str, str] | None = None) -> dict[str, Any]:
    root, resolutions = validate_root(root), resolutions or {}
    inputs = migration_inputs(root); input_hashes = {relative_posix(p, root): sha256_file(p) for p in inputs}
    texts, definitions, references = {}, defaultdict(list), []
    for path in inputs:
        relative = relative_posix(path, root)
        try: text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc: raise ControlPlaneError(f"migration input is not UTF-8: {relative}") from exc
        try:
            if path.suffix == ".json":
                json.loads(text)
            elif path.suffix == ".jsonl":
                for line_number, line_text in enumerate(text.splitlines(), start=1):
                    if line_text.strip(): json.loads(line_text)
        except json.JSONDecodeError as exc:
            raise ControlPlaneError(f"malformed {path.suffix[1:].upper()} migration input: {relative}:{exc.lineno}") from exc
        texts[relative] = text; header_spans = set()
        if path.name == "ID_RESERVATIONS.json":
            continue
        for match in EVENT_HEADER_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            definitions[match.group(1)].append({"path": relative, "line": line, "start": match.start(1), "end": match.end(1)})
            header_spans.add(match.span(1))
        for match in EVENT_ID_RE.finditer(text):
            if match.span(1) not in header_spans:
                line = text.count("\n", 0, match.start()) + 1
                column = match.start() - text.rfind("\n", 0, match.start())
                references.append({"id": match.group(1), "path": relative, "line": line, "column": column,
                    "start": match.start(1), "end": match.end(1)})
    broken = sorted({_reference_locator(i["path"], i["line"], i["column"], i["id"]) for i in references if i["id"] not in definitions})
    if broken: raise ControlPlaneError("broken event references must be repaired before migration: " + ", ".join(broken))
    all_ids, occurrence_targets = set(definitions), {}
    duplicate_ids = {eid: items for eid, items in definitions.items() if len(items) > 1}
    for event_id, items in sorted(duplicate_ids.items()):
        ordered = sorted(items, key=lambda item: (item["path"], item["line"]))
        occurrence_targets[_definition_locator(ordered[0]["path"], ordered[0]["line"])] = event_id
        kind, day, _ = event_id.split("-", 2); prefix = f"{kind}-{day}-"
        sequence = max([int(value[len(prefix):]) for value in all_ids if re.fullmatch(re.escape(prefix) + r"\d{3,}", value)], default=0)
        for item in ordered[1:]:
            sequence += 1; new_id = f"{prefix}{sequence:03d}"
            while new_id in all_ids: sequence += 1; new_id = f"{prefix}{sequence:03d}"
            all_ids.add(new_id); occurrence_targets[_definition_locator(item["path"], item["line"])] = new_id
    replacements = defaultdict(list)
    for event_id, items in duplicate_ids.items():
        for item in items:
            locator = _definition_locator(item["path"], item["line"])
            replacements[item["path"]].append((item["start"], item["end"], occurrence_targets[locator]))
    ambiguous, used_resolutions = [], set()
    for reference in references:
        event_id = reference["id"]
        if event_id not in duplicate_ids: continue
        ref_locator = _reference_locator(reference["path"], reference["line"], reference["column"], event_id)
        candidates = duplicate_ids[event_id]; same_file = [i for i in candidates if i["path"] == reference["path"]]
        target_locator = _definition_locator(same_file[0]["path"], same_file[0]["line"]) if len(same_file) == 1 else None
        if target_locator is None and ref_locator in resolutions: target_locator = resolutions[ref_locator]; used_resolutions.add(ref_locator)
        if target_locator not in occurrence_targets: ambiguous.append(ref_locator); continue
        valid_targets = {_definition_locator(i["path"], i["line"]) for i in candidates}
        if target_locator not in valid_targets: raise ControlPlaneError(f"resolution {ref_locator} targets a definition of the wrong ID: {target_locator}")
        replacements[reference["path"]].append((reference["start"], reference["end"], occurrence_targets[target_locator]))
    if ambiguous: raise ControlPlaneError("ambiguous duplicate references; add exact entries to the resolution file: " + ", ".join(sorted(ambiguous)))
    unused = sorted(set(resolutions) - used_resolutions)
    if unused: raise ControlPlaneError("unused or stale resolution entries: " + ", ".join(unused))
    changes = []
    for relative, operations in sorted(replacements.items()):
        text = texts[relative]
        for start, end, replacement in sorted(operations, key=lambda item: item[0], reverse=True): text = text[:start] + replacement + text[end:]
        if text != texts[relative]:
            encoded = text.encode(); changes.append({"path": relative, "before_sha256": input_hashes[relative],
                "after_sha256": hashlib.sha256(encoded).hexdigest(), "content": encoded})
    public_changes = [{k: v for k, v in item.items() if k != "content"} for item in changes]
    return {"schema_version": MIGRATION_SCHEMA, "input_hashes": input_hashes,
        "duplicate_ids": {k: [_definition_locator(i["path"], i["line"]) for i in v] for k, v in sorted(duplicate_ids.items())},
        "id_mapping": dict(sorted(occurrence_targets.items())), "changes": public_changes,
        "_contents": {item["path"]: item["content"] for item in changes}}

def _prepare_backup_directory(backup_dir: Path, forbidden_root: Path) -> Path:
    backup_dir = reject_symlink_ancestors(backup_dir)
    parent = backup_dir.parent
    if not parent.is_dir(): raise ControlPlaneError("backup parent must be an existing real directory")
    if backup_dir == forbidden_root or forbidden_root in backup_dir.parents:
        raise ControlPlaneError("backup directory must be outside the team root")
    if backup_dir.exists():
        if backup_dir.is_symlink() or not backup_dir.is_dir(): raise ControlPlaneError("backup directory must be a real directory")
        if any(backup_dir.iterdir()): raise ControlPlaneError("backup directory must be new or empty")
    else: backup_dir.mkdir(mode=0o700)
    return backup_dir.resolve(strict=True)

def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()

def apply_migration(root: Path, plan: dict[str, Any], *, backup_dir: Path, max_backup_bytes: int,
                    resolution_sha256: str | None = None) -> dict[str, Any]:
    root = validate_root(root)
    if max_backup_bytes < 1: raise ControlPlaneError("max-backup-bytes must be positive")
    changes, contents = plan.get("changes", []), plan.get("_contents", {})
    if not changes: return {"written": False, "idempotent": True, "changes": [], "manifest": None}
    input_hashes = plan.get("input_hashes")
    if not isinstance(input_hashes, dict): raise ControlPlaneError("migration plan is missing input hashes")
    for relative, expected_hash in input_hashes.items():
        if not isinstance(relative, str) or not isinstance(expected_hash, str): raise ControlPlaneError("migration plan input hash is malformed")
        source_input = secure_path(root, relative)
        if not source_input.is_file() or source_input.is_symlink(): raise ControlPlaneError(f"migration input is missing or unsafe: {relative}")
        if sha256_file(source_input) != expected_hash: raise ControlPlaneError(f"migration input drifted after planning: {relative}")
    total = 0
    for change in changes:
        source = secure_path(root, change["path"])
        if not source.is_file() or source.is_symlink(): raise ControlPlaneError(f"migration source is missing or unsafe: {change['path']}")
        if sha256_file(source) != change["before_sha256"]: raise ControlPlaneError(f"migration input drifted after planning: {change['path']}")
        total += source.stat().st_size
    if total > max_backup_bytes: raise ControlPlaneError(f"backup size {total} exceeds bound {max_backup_bytes}")
    backup_dir = _prepare_backup_directory(backup_dir, root); backup_root = backup_dir / "files"; manifest_changes = []
    for change in changes:
        source = secure_path(root, change["path"]); backup = backup_root.joinpath(*Path(change["path"]).parts)
        atomic_write(backup, source.read_bytes())
        if sha256_file(backup) != change["before_sha256"]: raise ControlPlaneError(f"backup verification failed: {change['path']}")
        manifest_changes.append({**change, "backup": f"files/{change['path']}"})
    for item in manifest_changes: item.pop("content", None)
    manifest = {"schema_version": MIGRATION_SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "team_root": str(root), "input_hashes": plan["input_hashes"], "resolution_sha256": resolution_sha256,
        "backup_bytes": total, "changes": manifest_changes, "id_mapping": plan["id_mapping"]}
    manifest_path = backup_dir / "manifest.json"; payload = _manifest_bytes(manifest)
    atomic_write(manifest_path, payload); atomic_write(backup_dir / "manifest.sha256", (hashlib.sha256(payload).hexdigest() + "\n").encode())
    for change in changes:
        source = secure_path(root, change["path"]); atomic_write(source, contents[change["path"]], mode=source.stat().st_mode & 0o777)
        if sha256_file(source) != change["after_sha256"]: raise ControlPlaneError(f"post-write verification failed: {change['path']}; use rollback")
    return {"written": True, "idempotent": False, "changes": manifest_changes, "manifest": str(manifest_path),
        "manifest_sha256": hashlib.sha256(payload).hexdigest()}

def rollback_migration(manifest_path: Path, *, expected_root: Path | None = None) -> dict[str, Any]:
    manifest_path = reject_symlink_ancestors(manifest_path)
    if manifest_path.is_symlink() or not manifest_path.is_file(): raise ControlPlaneError("manifest must be a real file")
    backup_dir, sidecar = manifest_path.parent, manifest_path.parent / "manifest.sha256"
    if sidecar.is_symlink() or not sidecar.is_file(): raise ControlPlaneError("manifest SHA-256 sidecar is missing or unsafe")
    expected_hash = sidecar.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or sha256_file(manifest_path) != expected_hash: raise ControlPlaneError("manifest hash verification failed")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != MIGRATION_SCHEMA or not isinstance(manifest.get("changes"), list): raise ControlPlaneError("unsupported or malformed migration manifest")
    root = validate_root(Path(manifest.get("team_root", "")))
    if expected_root is not None and root != validate_root(expected_root): raise ControlPlaneError("manifest team_root does not match --team-root")
    verified = []
    for change in manifest["changes"]:
        if not isinstance(change, dict): raise ControlPlaneError("malformed manifest change")
        relative, backup_relative = change.get("path"), change.get("backup")
        if not isinstance(relative, str) or not isinstance(backup_relative, str): raise ControlPlaneError("manifest change path is malformed")
        source, backup = secure_path(root, relative), secure_path(backup_dir, backup_relative)
        if not source.is_file() or source.is_symlink() or not backup.is_file() or backup.is_symlink(): raise ControlPlaneError(f"rollback source/backup is missing or unsafe: {relative}")
        if sha256_file(backup) != change.get("before_sha256"): raise ControlPlaneError(f"backup hash verification failed: {relative}")
        current = sha256_file(source)
        if current not in {change.get("before_sha256"), change.get("after_sha256")}:
            raise ControlPlaneError(f"current source drifted; refusing non-exact rollback: {relative}")
        verified.append((source, backup, change, current == change.get("after_sha256")))
    if not any(needs_restore for _source, _backup, _change, needs_restore in verified):
        return {"rolled_back": False, "idempotent": True, "restored": []}
    restored = []
    for source, backup, change, needs_restore in verified:
        if not needs_restore: continue
        atomic_write(source, backup.read_bytes(), mode=source.stat().st_mode & 0o777)
        if sha256_file(source) != change["before_sha256"]: raise ControlPlaneError(f"rollback verification failed: {change['path']}")
        restored.append(change["path"])
    return {"rolled_back": True, "idempotent": False, "restored": restored}
