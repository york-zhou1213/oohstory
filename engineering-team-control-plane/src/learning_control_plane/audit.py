"""Task and whole-store fail-closed audits."""
from __future__ import annotations
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from .common import (AGENTS, EVENT_HEADER_RE, EVENT_ID_RE, INDEX_NAMES, SHA256_RE, STAGE_RE, TASK_RE,
    ControlPlaneError, iter_event_store_files, iter_real_files, parse_timestamp, read_json,
    markdown_visible_text, relative_posix, require_real_directory, require_real_file, sha256_file,
    timestamp_calendar_date, validate_root)

TERMINAL_STATES = {"CLOSED", "CANCELLED", "FAILED", "SUPERSEDED", "REJECTED"}

def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ControlPlaneError(f"{field} must be a non-empty string")
    return value.strip()

def _validate_exact_item(root: Path, item: Any, index: int) -> str:
    field = f"retrieval_evidence.exact_retrieval[{index}]"
    if not isinstance(item, dict): raise ControlPlaneError(f"{field} must be an object")
    relative = _nonempty_string(item.get("path"), f"{field}.path")
    path = require_real_file(root, relative)
    expected_hash = _nonempty_string(item.get("sha256"), f"{field}.sha256")
    if not SHA256_RE.fullmatch(expected_hash): raise ControlPlaneError(f"{field}.sha256 must be lowercase SHA-256")
    if sha256_file(path) != expected_hash: raise ControlPlaneError(f"{field} hash does not match {relative}")
    start, end = item.get("line_start"), item.get("line_end")
    if not isinstance(start, int) or isinstance(start, bool) or start < 1: raise ControlPlaneError(f"{field}.line_start must be a positive integer")
    if not isinstance(end, int) or isinstance(end, bool) or end < start: raise ControlPlaneError(f"{field}.line_end must be >= line_start")
    try: line_count = len(path.read_text(encoding="utf-8").splitlines())
    except UnicodeDecodeError as exc: raise ControlPlaneError(f"{field}.path is not UTF-8 text") from exc
    if end > line_count: raise ControlPlaneError(f"{field} line range exceeds {relative}")
    return relative

def validate_closure_evidence(root: Path, receipt: dict[str, Any]) -> None:
    if receipt.get("schema_version") != 2: raise ControlPlaneError("schema_version 2 is required by the memory/RAG hard gate")
    agent = receipt.get("agent")
    if agent not in AGENTS: raise ControlPlaneError("receipt agent is invalid")
    expected_day = timestamp_calendar_date(receipt.get("closed_at"), "closed_at").isoformat()
    evidence = receipt.get("closure_evidence")
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1: raise ControlPlaneError("closure_evidence schema_version 1 is required")
    memory = evidence.get("owner_day_memory")
    if not isinstance(memory, dict): raise ControlPlaneError("closure_evidence.owner_day_memory must be an object")
    day = _nonempty_string(memory.get("date"), "owner_day_memory.date")
    if day != expected_day: raise ControlPlaneError(f"owner-day memory date {day} does not match closure day {expected_day}")
    expected_relative = f"{agent}/memory/{day}.md"
    relative = _nonempty_string(memory.get("path"), "owner_day_memory.path")
    if relative != expected_relative: raise ControlPlaneError(f"owner-day memory must be agent-owned path {expected_relative}")
    path = require_real_file(root, relative)
    size = path.stat().st_size
    try:
        memory_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ControlPlaneError("owner-day memory must be UTF-8 text") from exc
    if size <= 0 or not memory_text.strip(): raise ControlPlaneError("owner-day memory must be non-empty UTF-8 text")
    recorded_bytes = memory.get("bytes")
    if not isinstance(recorded_bytes, int) or isinstance(recorded_bytes, bool) or recorded_bytes != size: raise ControlPlaneError("owner_day_memory.bytes does not match the file")
    memory_hash = _nonempty_string(memory.get("sha256"), "owner_day_memory.sha256")
    if not SHA256_RE.fullmatch(memory_hash) or sha256_file(path) != memory_hash: raise ControlPlaneError("owner-day memory SHA-256 does not match the file")
    retrieval = evidence.get("retrieval_evidence")
    if not isinstance(retrieval, dict): raise ControlPlaneError("closure_evidence.retrieval_evidence must be an object")
    mode = retrieval.get("mode")
    if mode not in {"semantic_recall", "exact_file_fallback"}: raise ControlPlaneError("retrieval_evidence.mode must be semantic_recall or exact_file_fallback")
    recall = retrieval.get("semantic_recall")
    if not isinstance(recall, dict): raise ControlPlaneError("retrieval_evidence.semantic_recall must be an object")
    _nonempty_string(recall.get("query"), "semantic_recall.query")
    if mode == "semantic_recall":
        hits = recall.get("hits")
        if recall.get("status") != "ok": raise ControlPlaneError("semantic_recall mode requires status=ok")
        if not isinstance(hits, list) or not hits or not all(isinstance(hit, str) and hit.strip() for hit in hits): raise ControlPlaneError("successful semantic recall requires non-empty hits")
    else:
        if recall.get("status") != "unavailable": raise ControlPlaneError("exact-file fallback requires semantic status=unavailable")
        _nonempty_string(recall.get("reason"), "semantic_recall.reason")
    exact = retrieval.get("exact_retrieval")
    if not isinstance(exact, list) or not exact: raise ControlPlaneError("retrieval_evidence.exact_retrieval must be non-empty")
    exact_paths = [_validate_exact_item(root, item, index) for index, item in enumerate(exact)]
    if relative not in exact_paths: raise ControlPlaneError("exact retrieval must include the bound owner-day memory file")
    if mode == "exact_file_fallback":
        fallback_files = retrieval.get("fallback_files")
        if not isinstance(fallback_files, list) or not fallback_files: raise ControlPlaneError("exact-file fallback requires fallback_files")
        if not all(isinstance(item, str) and item in exact_paths for item in fallback_files): raise ControlPlaneError("fallback_files must identify validated exact retrieval paths")

def validate_receipt_identity(receipt: dict[str, Any], *, task: str, agent: str, stage: str, status: str | None = None) -> None:
    if receipt.get("task_id") != task or receipt.get("agent") != agent or receipt.get("stage") != stage: raise ControlPlaneError("receipt payload identity does not match its path/requirement")
    if status is not None and receipt.get("status") != status: raise ControlPlaneError(f"receipt status must be {status}")

def _task_buckets(root: Path) -> list[tuple[str, Path]]:
    require_real_directory(root, PurePosixPath("tasks"))
    return [(bucket, require_real_directory(root, PurePosixPath("tasks", bucket)))
        for bucket in ("active", "archive")]

def _task_frontmatter(path: Path, expected_task: str) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ControlPlaneError(f"task record is not UTF-8: {expected_task}") from exc
    if not lines or lines[0].strip() != "---":
        raise ControlPlaneError(f"task record lacks front matter: {expected_task}")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ControlPlaneError(f"task record has unterminated front matter: {expected_task}") from exc
    frontmatter = lines[1:end]
    task_ids = [match.group(1).strip() for line in frontmatter
        if (match := re.fullmatch(r"task-id:\s*(.*?)\s*", line))]
    if len(task_ids) != 1:
        raise ControlPlaneError(f"task record requires exactly one frontmatter task-id: {expected_task}")
    if task_ids[0] != expected_task:
        raise ControlPlaneError(f"task record frontmatter task-id does not match filename: {expected_task}")
    return frontmatter

def _authoritative_requirements(root: Path, task: str) -> list[str]:
    records = []
    for bucket, directory in _task_buckets(root):
        candidate = directory / f"{task}.md"
        if candidate.exists() or candidate.is_symlink():
            records.append(require_real_file(root, PurePosixPath("tasks", bucket, f"{task}.md")))
    if len(records) != 1:
        raise ControlPlaneError(f"task requires exactly one authoritative active/archive record: {task}")
    lines = _task_frontmatter(records[0], task)
    requirements, found = [], False
    index = 0
    while index < len(lines):
        if lines[index] != "learning-requirements:":
            index += 1
            continue
        if found:
            raise ControlPlaneError("task record repeats learning-requirements")
        found = True
        index += 1
        while index < len(lines) and (not lines[index].strip() or lines[index].startswith((" ", "\t"))):
            item = lines[index].strip()
            if item:
                if not item.startswith("- "):
                    raise ControlPlaneError("learning-requirements entries must use an indented list")
                requirements.append(item[2:].strip().strip("'\"`"))
            index += 1
    if not found or not requirements:
        raise ControlPlaneError("task record requires non-empty learning-requirements")
    if len(requirements) != len(set(requirements)):
        raise ControlPlaneError("task record contains duplicate learning-requirements")
    for requirement in requirements:
        agent, separator, stage = requirement.partition(":")
        if not separator or agent not in AGENTS or not STAGE_RE.fullmatch(stage):
            raise ControlPlaneError(f"invalid authoritative learning requirement: {requirement}")
    return requirements

def audit_task(root: Path, task: str, requirements: list[str]) -> dict[str, Any]:
    root = validate_root(root)
    if not TASK_RE.fullmatch(task): raise ControlPlaneError("invalid TASK-ID")
    if not requirements: raise ControlPlaneError("at least one AGENT:STAGE requirement is required")
    failures: dict[str, list[str]] = {}
    parsed_requirements = []
    for requirement in requirements:
        agent, separator, stage = requirement.partition(":")
        if not separator or agent not in AGENTS or not STAGE_RE.fullmatch(stage): failures[requirement] = ["invalid AGENT:STAGE requirement"]; continue
        parsed_requirements.append(requirement)
    if len(parsed_requirements) != len(set(parsed_requirements)):
        failures["requested_requirements"] = ["duplicate AGENT:STAGE requirement"]
    try:
        authoritative = _authoritative_requirements(root, task)
    except (ControlPlaneError, OSError) as exc:
        failures["authoritative_participation"] = [str(exc)]
        authoritative = []
    missing = sorted(set(authoritative) - set(parsed_requirements))
    extra = sorted(set(parsed_requirements) - set(authoritative))
    if missing or extra:
        failures["requested_requirements"] = [
            f"requirements do not match authoritative participation; omitted={missing}; extra={extra}"
        ]
    for requirement in authoritative:
        agent, _, stage = requirement.partition(":")
        try:
            path = require_real_file(root, PurePosixPath("team-learnings", "receipts", task, f"{agent}-{stage}.json"))
            receipt = read_json(path)
            validate_receipt_identity(receipt, task=task, agent=agent, stage=stage, status="closed")
            validate_closure_evidence(root, receipt)
        except (ControlPlaneError, OSError) as exc: failures[requirement] = [str(exc)]
    receipt_directory = root / "team-learnings" / "receipts" / task
    if receipt_directory.exists() or receipt_directory.is_symlink():
        try:
            if receipt_directory.is_symlink() or not receipt_directory.is_dir():
                raise ControlPlaneError("task receipt path must be a real directory")
            if any(path.is_dir() for path in receipt_directory.iterdir() if not path.is_symlink()):
                raise ControlPlaneError("nested task receipt directories are forbidden")
            for path in iter_real_files(receipt_directory, ("*.json",)):
                filename = path.stem
                agent, separator, stage = filename.partition("-")
                if not separator or agent not in AGENTS or not STAGE_RE.fullmatch(stage):
                    raise ControlPlaneError(f"invalid receipt path identity: {filename}")
                receipt = read_json(path)
                validate_receipt_identity(receipt, task=task, agent=agent, stage=stage)
                status = receipt.get("status")
                if status not in {"open", "closed"}:
                    raise ControlPlaneError(f"receipt status must be open or closed: {filename}")
                if status == "open":
                    failures[f"open_receipt:{agent}:{stage}"] = ["every task receipt must be closed"]
        except (ControlPlaneError, OSError) as exc:
            failures["task_receipt_inventory"] = [str(exc)]
    return {"schema_version": 1, "task_id": task, "failures": failures, "ok": not failures}

def _receipt_files(root: Path) -> list[Path]:
    receipts = root / "team-learnings" / "receipts"
    if not receipts.exists(): return []
    if receipts.is_symlink() or not receipts.is_dir(): raise ControlPlaneError("receipts path must be a real directory")
    return iter_real_files(receipts, ("**/*",))

def _task_records(root: Path) -> tuple[dict[str, str], list[str]]:
    records, errors = {}, []
    try:
        buckets = _task_buckets(root)
    except ControlPlaneError as exc:
        return records, [str(exc)]
    for bucket, directory in buckets:
        for path in iter_real_files(directory, ("TASK-*.md",)):
            task = path.stem
            if not TASK_RE.fullmatch(task): errors.append(f"invalid task record name: {relative_posix(path, root)}"); continue
            if task in records:
                errors.append(f"duplicate task record: {task}")
            try:
                frontmatter = _task_frontmatter(path, task)
            except ControlPlaneError as exc:
                errors.append(f"{relative_posix(path, root)}: {exc}")
                continue
            matches = [match.group(1) for line in frontmatter
                if (match := re.fullmatch(r"state:\s*([A-Z_]+)\s*", line))]
            records[task] = "ARCHIVED" if bucket == "archive" else (matches[-1] if matches else "UNKNOWN")
    return records, errors

def _scan_event_store(root: Path):
    definitions, references, errors = defaultdict(list), [], []
    try:
        files = iter_event_store_files(root)
    except ControlPlaneError as exc:
        return definitions, [], [str(exc)]
    for path in files:
        relative = relative_posix(path, root)
        try: text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError: errors.append(f"non-UTF-8 store file: {relative}"); continue
        try:
            if path.suffix == ".json":
                json.loads(text)
            elif path.suffix == ".jsonl":
                for number, line_text in enumerate(text.splitlines(), start=1):
                    if line_text.strip(): json.loads(line_text)
        except json.JSONDecodeError as exc:
            errors.append(f"malformed {path.suffix[1:].upper()}: {relative}:{exc.lineno}")
            continue
        if path.name == "ID_RESERVATIONS.json":
            continue
        scan_text = markdown_visible_text(text) if path.suffix == ".md" else text
        header_spans = set()
        for match in EVENT_HEADER_RE.finditer(scan_text):
            definitions[match.group(1)].append((relative, scan_text.count("\n", 0, match.start()) + 1)); header_spans.add(match.span(1))
        for match in EVENT_ID_RE.finditer(scan_text):
            if match.span(1) not in header_spans: references.append((relative, scan_text.count("\n", 0, match.start()) + 1, match.group(1)))
    return definitions, [f"{p}:{line} -> {eid}" for p, line, eid in references if eid not in definitions], errors

def _lifecycle_debt(root: Path):
    missing, empty_errors = [], []
    for agent in AGENTS:
        for name in INDEX_NAMES:
            relative = f"{agent}/learnings/{name}"
            try: path = require_real_file(root, relative)
            except ControlPlaneError: missing.append(relative); continue
            if name == "ERRORS.md":
                text = markdown_visible_text(path.read_text(encoding="utf-8"))
                if not any(match.group(1).startswith("ERR-") for match in EVENT_HEADER_RE.finditer(text)):
                    empty_errors.append(agent)
    flow_debt, team_index = [], root / "team-learnings" / "TEAM_LEARNINGS.md"
    if not team_index.is_file() or team_index.is_symlink(): missing.append("team-learnings/TEAM_LEARNINGS.md")
    else:
        section = "unknown"
        for line in team_index.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "): section = line[3:].strip()
            elif "_(No entries yet)_" in line: flow_debt.append(section)
    return missing, empty_errors, flow_debt

def _state_debt(root: Path):
    path = root / "team-learnings" / "LEARNING_STATE.json"
    if not path.exists(): return [], [], []
    state = read_json(path); lessons = state.get("lessons")
    if state.get("schema_version") != 1 or not isinstance(lessons, dict): return [], [], ["malformed LEARNING_STATE.json"]
    guard, promotion = [], []
    for lesson_id, entry in lessons.items():
        if not isinstance(entry, dict): return [], [], [f"malformed state entry: {lesson_id}"]
        try: recurrence = int(entry.get("recurrence_count", 1))
        except (TypeError, ValueError): return [], [], [f"invalid recurrence_count: {lesson_id}"]
        if recurrence >= 2 and not entry.get("guards"): guard.append(lesson_id)
        if recurrence >= 3 and len(set(entry.get("source_tasks", []))) >= 2 and not entry.get("promoted_to"): promotion.append(lesson_id)
    return guard, promotion, []

def audit_system(root: Path, *, stale_hours: float = 24.0, now: datetime | None = None) -> dict[str, Any]:
    root = validate_root(root)
    if (not isinstance(stale_hours, (int, float)) or isinstance(stale_hours, bool)
            or not math.isfinite(stale_hours) or stale_hours < 0):
        raise ControlPlaneError("stale-hours must be a finite non-negative number")
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        raise ControlPlaneError("now must include a timezone")
    now = now.astimezone(timezone.utc)
    task_records, errors = _task_records(root); orphan, stale, malformed = [], [], []; receipts = _receipt_files(root)
    for path in receipts:
        relative = relative_posix(path, root)
        try:
            receipt_relative = path.relative_to(root / "team-learnings" / "receipts")
            if len(receipt_relative.parts) != 2 or path.suffix != ".json":
                raise ControlPlaneError("receipt must use canonical TASK-ID/AGENT-STAGE.json depth and filename")
            task, filename = receipt_relative.parts[0], path.stem
            agent, separator, stage = filename.partition("-")
            if not separator or agent not in AGENTS or not STAGE_RE.fullmatch(stage) or not TASK_RE.fullmatch(task): raise ControlPlaneError("invalid receipt path identity")
            receipt = read_json(path); validate_receipt_identity(receipt, task=task, agent=agent, stage=stage)
            if receipt.get("schema_version") not in {1, 2}: raise ControlPlaneError("unsupported receipt schema_version")
            status = receipt.get("status")
            if status not in {"open", "closed"}: raise ControlPlaneError("receipt status must be open or closed")
            if status == "closed": parse_timestamp(receipt.get("closed_at"), "closed_at"); continue
            opened = parse_timestamp(receipt.get("opened_at"), "opened_at")
            if task not in task_records:
                orphan.append(relative)
            else:
                state = task_records[task]
                if opened > now:
                    raise ControlPlaneError("opened_at is in the future")
                if state in TERMINAL_STATES or state == "ARCHIVED" or now - opened >= timedelta(hours=stale_hours): stale.append(relative)
        except (ControlPlaneError, OSError) as exc: malformed.append(f"{relative}: {exc}")
    definitions, broken_refs, scan_errors = _scan_event_store(root); errors.extend(scan_errors)
    duplicates = {eid: [f"{p}:{line}" for p, line in locs] for eid, locs in sorted(definitions.items()) if len(locs) > 1}
    missing, empty_agents, empty_flows = _lifecycle_debt(root); guard, promotion, state_errors = _state_debt(root); errors.extend(state_errors)
    categories = {"orphan_open_receipts": sorted(orphan), "stale_open_receipts": sorted(set(stale)), "malformed_receipts": sorted(malformed),
        "duplicate_event_ids": duplicates, "broken_event_references": sorted(broken_refs), "guard_debt": sorted(guard), "promotion_debt": sorted(promotion),
        "missing_lifecycle_files": sorted(set(missing)), "empty_error_agents": sorted(empty_agents),
        "empty_flow_sections": sorted(empty_flows), "errors": sorted(set(errors))}
    return {"schema_version": 1, "generated_at": now.isoformat(timespec="seconds"), "receipt_count": len(receipts), **categories,
        "ok": not any(bool(value) for value in categories.values())}
