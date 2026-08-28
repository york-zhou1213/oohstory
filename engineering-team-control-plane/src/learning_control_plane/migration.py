"""Reference-aware duplicate-ID migration with verified backup and rollback."""
from __future__ import annotations
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from .audit import _task_frontmatter, validate_closure_evidence, validate_receipt_identity
from .common import (AGENTS, EVENT_HEADER_RE, EVENT_ID_RE, STAGE_RE, TASK_RE,
    ControlPlaneError, atomic_write, iter_event_store_files, markdown_visible_text,
    read_json, reject_symlink_ancestors, relative_posix, require_real_directory,
    require_real_file, secure_path, sha256_file, timestamp_calendar_date, validate_root)

MIGRATION_SCHEMA = 1
RECEIPT_DISPOSITION_OPERATION = "receipt-schema-v2-disposition"
ATX_SECTION_END_RE = re.compile(r"(?m)^[ ]{0,3}#{1,2}(?:[ \t]+|$)")
SETEXT_SECTION_END_RE = re.compile(r"(?m)^[ \t]{0,3}\S[^\n]*\n[ \t]{0,3}(?:=+|-+)[ \t]*(?:\n|$)")

def migration_inputs(root: Path) -> list[Path]:
    return iter_event_store_files(root)

def load_resolutions(path: Path | None) -> tuple[dict[str, str], str | None]:
    if path is None: return {}, None
    path = reject_symlink_ancestors(path)
    if path.is_symlink() or not path.is_file(): raise ControlPlaneError("resolution file must be a real file")
    value = read_json(path); references = value.get("references")
    if value.get("schema_version") != 1 or not isinstance(references, dict): raise ControlPlaneError("resolution file requires schema_version=1 and references object")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in references.items()): raise ControlPlaneError("resolution reference keys and targets must be strings")
    return references, sha256_file(path)

def _receipt_relative(task: str, agent: str, stage: str) -> str:
    if not TASK_RE.fullmatch(task) or agent not in AGENTS or not STAGE_RE.fullmatch(stage):
        raise ControlPlaneError("invalid receipt identity")
    return f"team-learnings/receipts/{task}/{agent}-{stage}.json"

def _exact_retrieval_item(root: Path, relative: str) -> dict[str, Any]:
    path = require_real_file(root, relative)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ControlPlaneError(f"exact retrieval is not UTF-8 text: {relative}") from exc
    if not lines:
        raise ControlPlaneError(f"exact retrieval must be non-empty: {relative}")
    return {"path": relative, "sha256": sha256_file(path),
            "line_start": 1, "line_end": len(lines)}

def _current_closure_evidence(root: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    query = receipt.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ControlPlaneError("schema-v1 receipt query must be non-empty")
    agent = receipt.get("agent")
    if agent not in AGENTS:
        raise ControlPlaneError("schema-v1 receipt agent is invalid")
    day = timestamp_calendar_date(receipt.get("closed_at"), "closed_at").isoformat()
    memory_relative = f"{agent}/memory/{day}.md"
    memory_path = require_real_file(root, memory_relative)
    try:
        memory_text = memory_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ControlPlaneError("owner-day memory must be UTF-8 text") from exc
    if not memory_text.strip():
        raise ControlPlaneError("owner-day memory must be non-empty UTF-8 text")
    consulted = receipt.get("consulted_lessons", [])
    if not isinstance(consulted, list) or not all(
            isinstance(item, str) and item.strip() for item in consulted):
        raise ControlPlaneError("schema-v1 consulted_lessons must be a string list")
    exact_paths = list(dict.fromkeys([memory_relative, *consulted]))
    exact = [_exact_retrieval_item(root, relative) for relative in exact_paths]
    return {
        "schema_version": 1,
        "owner_day_memory": {
            "date": day,
            "path": memory_relative,
            "bytes": memory_path.stat().st_size,
            "sha256": sha256_file(memory_path),
        },
        "retrieval_evidence": {
            "mode": "exact_file_fallback",
            "semantic_recall": {
                "status": "unavailable",
                "query": query.strip(),
                "reason": "legacy writer records deterministic exact-file retrieval only",
            },
            "exact_retrieval": exact,
            "fallback_files": [item["path"] for item in exact],
        },
    }

def upgrade_current_receipt(root: Path, *, task: str, agent: str,
                            stage: str) -> dict[str, Any]:
    """Upgrade the receipt just closed by the preserved legacy writer."""
    root = validate_root(root)
    relative = _receipt_relative(task, agent, stage)
    path = require_real_file(root, relative)
    receipt = read_json(path)
    validate_receipt_identity(
        receipt, task=task, agent=agent, stage=stage, status="closed")
    if receipt.get("schema_version") == 2:
        validate_closure_evidence(root, receipt)
        return {"written": False, "idempotent": True, "receipt": receipt}
    if receipt.get("schema_version") != 1:
        raise ControlPlaneError("legacy close receipt must use schema_version 1")
    upgraded = dict(receipt)
    upgraded["schema_version"] = 2
    upgraded["closure_evidence"] = _current_closure_evidence(root, receipt)
    validate_closure_evidence(root, upgraded)
    mode = path.stat().st_mode & 0o777
    atomic_write(path, _manifest_bytes(upgraded), mode=mode)
    written = read_json(path)
    validate_receipt_identity(
        written, task=task, agent=agent, stage=stage, status="closed")
    validate_closure_evidence(root, written)
    return {"written": True, "idempotent": False, "receipt": written}

def load_receipt_dispositions(path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    path = reject_symlink_ancestors(path)
    if path.is_symlink() or not path.is_file():
        raise ControlPlaneError("receipt disposition file must be a real file")
    value = read_json(path)
    receipts = value.get("receipts")
    tasks = value.get("tasks", {})
    if (value.get("schema_version") != 1 or not isinstance(receipts, dict)
            or not isinstance(tasks, dict)
            or set(value) - {"schema_version", "receipts", "tasks"}):
        raise ControlPlaneError(
            "receipt disposition file requires schema_version=1, receipts object, and optional tasks object")
    return receipts, tasks, sha256_file(path)

def _schema_v1_receipts(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    receipts_root = require_real_directory(
        root, PurePosixPath("team-learnings", "receipts"))
    found: dict[str, tuple[Path, dict[str, Any]]] = {}
    for task_directory in sorted(receipts_root.iterdir()):
        if (task_directory.is_symlink() or not task_directory.is_dir()
                or not TASK_RE.fullmatch(task_directory.name)):
            raise ControlPlaneError("receipt task entries must be real TASK-ID directories")
        for path in sorted(task_directory.iterdir()):
            if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                raise ControlPlaneError("receipt entries must be direct regular JSON files")
            agent, separator, stage = path.stem.partition("-")
            if not separator or agent not in AGENTS or not STAGE_RE.fullmatch(stage):
                raise ControlPlaneError(f"invalid receipt path identity: {path.stem}")
            receipt = read_json(path)
            validate_receipt_identity(
                receipt, task=task_directory.name, agent=agent, stage=stage)
            if receipt.get("schema_version") == 1:
                found[relative_posix(path, root)] = (path, receipt)
            elif receipt.get("schema_version") == 2:
                if receipt.get("status") == "closed":
                    validate_closure_evidence(root, receipt)
            else:
                raise ControlPlaneError(f"unsupported receipt schema: {relative_posix(path, root)}")
    return found

def _task_requirement_change(root: Path, task: str,
                             requirements: Any) -> tuple[Path, bytes | None]:
    if not TASK_RE.fullmatch(task):
        raise ControlPlaneError(f"invalid task requirement identity: {task}")
    if (not isinstance(requirements, list) or not requirements
            or not all(isinstance(item, str) and item for item in requirements)
            or len(requirements) != len(set(requirements))):
        raise ControlPlaneError(f"task requirements must be a non-empty unique list: {task}")
    for requirement in requirements:
        agent, separator, stage = requirement.partition(":")
        if not separator or agent not in AGENTS or not STAGE_RE.fullmatch(stage):
            raise ControlPlaneError(f"invalid task learning requirement: {task}:{requirement}")
    records = []
    for bucket in ("active", "archive"):
        directory = require_real_directory(root, PurePosixPath("tasks", bucket))
        candidate = directory / f"{task}.md"
        if candidate.exists() or candidate.is_symlink():
            records.append(require_real_file(root, relative_posix(candidate, root)))
    if len(records) != 1:
        raise ControlPlaneError(
            f"task requirements need exactly one authoritative task record: {task}")
    path = records[0]
    frontmatter, _state = _task_frontmatter(path, task)
    found = [index for index, line in enumerate(frontmatter)
             if line == "learning-requirements:"]
    if len(found) > 1:
        raise ControlPlaneError(f"task repeats learning-requirements: {task}")
    if found:
        index = found[0] + 1
        current = []
        while index < len(frontmatter) and (
                not frontmatter[index].strip()
                or frontmatter[index].startswith((" ", "\t"))):
            item = frontmatter[index].strip()
            if item:
                if not item.startswith("- "):
                    raise ControlPlaneError(
                        f"task learning-requirements entries are malformed: {task}")
                current.append(item[2:].strip().strip("'\"`"))
            index += 1
        if current != requirements:
            raise ControlPlaneError(
                f"task learning-requirements conflict with explicit disposition: {task}")
        return path, None
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    closing = next(index for index, line in enumerate(lines[1:], start=1)
                   if line.strip() == "---")
    inserted = ["learning-requirements:",
                *(f"  - {requirement}" for requirement in requirements)]
    content = "\n".join([*lines[:closing], *inserted, *lines[closing:]])
    if text.endswith("\n"):
        content += "\n"
    return path, content.encode()

def plan_receipt_disposition(root: Path, *, dispositions: dict[str, Any],
                             task_requirements: dict[str, Any] | None = None) -> dict[str, Any]:
    root = validate_root(root)
    task_requirements = task_requirements or {}
    legacy = _schema_v1_receipts(root)
    supplied = set(dispositions)
    missing, extra = sorted(set(legacy) - supplied), sorted(supplied - set(legacy))
    if missing or extra:
        raise ControlPlaneError(
            f"receipt dispositions must exactly cover schema-v1 receipts; missing={missing}, extra={extra}")
    changes, public_dispositions, public_tasks = [], [], []
    input_hashes = {relative: sha256_file(path)
                    for relative, (path, _receipt) in sorted(legacy.items())}
    for relative, (path, receipt) in sorted(legacy.items()):
        entry = dispositions[relative]
        if not isinstance(entry, dict):
            raise ControlPlaneError(f"receipt disposition must be an object: {relative}")
        action = entry.get("action")
        if action == "retain":
            reason = entry.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ControlPlaneError(f"retained receipt requires a non-empty reason: {relative}")
            if set(entry) != {"action", "reason"}:
                raise ControlPlaneError(f"retained receipt fields are invalid: {relative}")
            public_dispositions.append(
                {"path": relative, "action": action, "reason": reason.strip()})
            continue
        if action != "upgrade" or set(entry) != {"action", "closure_evidence"}:
            raise ControlPlaneError(f"receipt disposition action is invalid: {relative}")
        validate_receipt_identity(
            receipt, task=receipt["task_id"], agent=receipt["agent"],
            stage=receipt["stage"], status="closed")
        upgraded = dict(receipt)
        upgraded["schema_version"] = 2
        upgraded["closure_evidence"] = entry["closure_evidence"]
        validate_closure_evidence(root, upgraded)
        content = _manifest_bytes(upgraded)
        changes.append({"path": relative, "before_sha256": input_hashes[relative],
                        "after_sha256": hashlib.sha256(content).hexdigest(),
                        "content": content})
        public_dispositions.append({"path": relative, "action": action})
    for task, requirements in sorted(task_requirements.items()):
        path, content = _task_requirement_change(root, task, requirements)
        relative = relative_posix(path, root)
        before = sha256_file(path)
        input_hashes[relative] = before
        public_tasks.append({"task_id": task, "learning_requirements": requirements})
        if content is None:
            continue
        changes.append({"path": relative, "before_sha256": before,
                        "after_sha256": hashlib.sha256(content).hexdigest(),
                        "content": content})
    return {
        "schema_version": MIGRATION_SCHEMA,
        "operation": RECEIPT_DISPOSITION_OPERATION,
        "input_hashes": input_hashes,
        "dispositions": public_dispositions,
        "tasks": public_tasks,
        "changes": [{key: value for key, value in item.items() if key != "content"}
                    for item in changes],
        "_contents": {item["path"]: item["content"] for item in changes},
    }

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
        scan_text = markdown_visible_text(text) if path.suffix == ".md" else text
        header_matches = list(EVENT_HEADER_RE.finditer(scan_text))
        for match in header_matches:
            line = scan_text.count("\n", 0, match.start()) + 1
            headings = [candidate for candidate in (
                ATX_SECTION_END_RE.search(scan_text, match.end(1)),
                SETEXT_SECTION_END_RE.search(scan_text, match.end(1)),
            ) if candidate]
            section_end = min(candidate.start() for candidate in headings) if headings else len(scan_text)
            definitions[match.group(1)].append({"path": relative, "line": line,
                "start": match.start(1), "end": match.end(1),
                "section_start": match.end(1), "section_end": section_end})
            header_spans.add(match.span(1))
        for match in EVENT_ID_RE.finditer(scan_text):
            if match.span(1) not in header_spans:
                line = scan_text.count("\n", 0, match.start()) + 1
                column = match.start() - scan_text.rfind("\n", 0, match.start())
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
        candidates = duplicate_ids[event_id]
        if ref_locator in resolutions:
            target_locator = resolutions[ref_locator]
            used_resolutions.add(ref_locator)
        else:
            structural_self = [item for item in candidates
                if item["path"] == reference["path"]
                and item["section_start"] <= reference["start"] < item["section_end"]]
            target_locator = (_definition_locator(structural_self[0]["path"], structural_self[0]["line"])
                if len(structural_self) == 1 else None)
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

def apply_receipt_disposition(root: Path, plan: dict[str, Any], *, backup_dir: Path,
                              max_backup_bytes: int,
                              disposition_sha256: str) -> dict[str, Any]:
    """Apply an explicit, hash-bound schema-v1 receipt disposition plan."""
    root = validate_root(root)
    if plan.get("operation") != RECEIPT_DISPOSITION_OPERATION:
        raise ControlPlaneError("receipt disposition plan operation is invalid")
    if max_backup_bytes < 1:
        raise ControlPlaneError("max-backup-bytes must be positive")
    if not re.fullmatch(r"[0-9a-f]{64}", disposition_sha256):
        raise ControlPlaneError("disposition SHA-256 is malformed")
    input_hashes = plan.get("input_hashes")
    changes, contents = plan.get("changes"), plan.get("_contents", {})
    dispositions, tasks = plan.get("dispositions"), plan.get("tasks")
    if (not isinstance(input_hashes, dict) or not isinstance(changes, list)
            or not isinstance(contents, dict) or not isinstance(dispositions, list)
            or not isinstance(tasks, list)):
        raise ControlPlaneError("receipt disposition plan is malformed")
    for relative, expected_hash in input_hashes.items():
        path = secure_path(root, relative)
        if not path.is_file() or path.is_symlink() or sha256_file(path) != expected_hash:
            raise ControlPlaneError(f"receipt input drifted after planning: {relative}")
    total = sum(secure_path(root, item["path"]).stat().st_size for item in changes)
    if total > max_backup_bytes:
        raise ControlPlaneError(f"backup size {total} exceeds bound {max_backup_bytes}")
    backup_dir = _prepare_backup_directory(backup_dir, root)
    backup_root = backup_dir / "files"
    manifest_changes = []
    for change in changes:
        relative = change.get("path")
        if not isinstance(relative, str) or relative not in contents:
            raise ControlPlaneError("receipt disposition change is malformed")
        source = secure_path(root, relative)
        if sha256_file(source) != change.get("before_sha256"):
            raise ControlPlaneError(f"receipt input drifted after planning: {relative}")
        backup = backup_root.joinpath(*PurePosixPath(relative).parts)
        atomic_write(backup, source.read_bytes())
        if sha256_file(backup) != change["before_sha256"]:
            raise ControlPlaneError(f"backup verification failed: {relative}")
        manifest_changes.append({**change, "backup": f"files/{relative}"})
    manifest = {
        "schema_version": MIGRATION_SCHEMA,
        "operation": RECEIPT_DISPOSITION_OPERATION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "team_root": str(root),
        "input_hashes": input_hashes,
        "disposition_sha256": disposition_sha256,
        "backup_bytes": total,
        "dispositions": dispositions,
        "tasks": tasks,
        "changes": manifest_changes,
    }
    manifest_path = backup_dir / "manifest.json"
    payload = _manifest_bytes(manifest)
    atomic_write(manifest_path, payload)
    atomic_write(backup_dir / "manifest.sha256",
                 (hashlib.sha256(payload).hexdigest() + "\n").encode())
    for change in changes:
        source = secure_path(root, change["path"])
        atomic_write(source, contents[change["path"]], mode=source.stat().st_mode & 0o777)
        if sha256_file(source) != change["after_sha256"]:
            raise ControlPlaneError(
                f"post-write verification failed: {change['path']}; use rollback")
    return {"written": bool(changes), "idempotent": False,
            "changes": manifest_changes, "dispositions": dispositions, "tasks": tasks,
            "manifest": str(manifest_path),
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

def rollback_receipt_disposition(manifest_path: Path, *,
                                 expected_root: Path | None = None) -> dict[str, Any]:
    manifest_path = reject_symlink_ancestors(manifest_path)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ControlPlaneError("manifest must be a real file")
    sidecar = manifest_path.parent / "manifest.sha256"
    if sidecar.is_symlink() or not sidecar.is_file():
        raise ControlPlaneError("manifest SHA-256 sidecar is missing or unsafe")
    expected_hash = sidecar.read_text(encoding="ascii").strip()
    if (not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            or sha256_file(manifest_path) != expected_hash):
        raise ControlPlaneError("manifest hash verification failed")
    manifest = read_json(manifest_path)
    if (manifest.get("schema_version") != MIGRATION_SCHEMA
            or manifest.get("operation") != RECEIPT_DISPOSITION_OPERATION
            or not isinstance(manifest.get("changes"), list)
            or not isinstance(manifest.get("dispositions"), list)
            or not isinstance(manifest.get("tasks"), list)):
        raise ControlPlaneError("unsupported or malformed receipt disposition manifest")
    root = validate_root(Path(manifest.get("team_root", "")))
    if expected_root is not None and root != validate_root(expected_root):
        raise ControlPlaneError("manifest team_root does not match --team-root")
    verified = []
    for change in manifest["changes"]:
        if not isinstance(change, dict):
            raise ControlPlaneError("malformed manifest change")
        relative, backup_relative = change.get("path"), change.get("backup")
        if not isinstance(relative, str) or not isinstance(backup_relative, str):
            raise ControlPlaneError("manifest change path is malformed")
        source = secure_path(root, relative)
        backup = secure_path(manifest_path.parent, backup_relative)
        if (not source.is_file() or source.is_symlink()
                or not backup.is_file() or backup.is_symlink()):
            raise ControlPlaneError(f"rollback source/backup is missing or unsafe: {relative}")
        if sha256_file(backup) != change.get("before_sha256"):
            raise ControlPlaneError(f"backup hash verification failed: {relative}")
        current = sha256_file(source)
        if current not in {change.get("before_sha256"), change.get("after_sha256")}:
            raise ControlPlaneError(
                f"current source drifted; refusing non-exact rollback: {relative}")
        verified.append((source, backup, change, current == change.get("after_sha256")))
    if not any(needs_restore for _source, _backup, _change, needs_restore in verified):
        return {"rolled_back": False, "idempotent": True, "restored": []}
    restored = []
    for source, backup, change, needs_restore in verified:
        if not needs_restore:
            continue
        atomic_write(source, backup.read_bytes(), mode=source.stat().st_mode & 0o777)
        if sha256_file(source) != change["before_sha256"]:
            raise ControlPlaneError(f"rollback verification failed: {change['path']}")
        restored.append(change["path"])
    return {"rolled_back": True, "idempotent": False, "restored": restored}
