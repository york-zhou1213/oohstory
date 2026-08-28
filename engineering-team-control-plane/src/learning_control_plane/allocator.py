"""Lock-safe, full-store learning-event ID allocation."""
from __future__ import annotations
import fcntl
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .common import (AGENTS, EVENT_HEADER_RE, EVENT_ID_RE, EVENT_KINDS, ControlPlaneError,
                     atomic_json, iter_event_store_files, markdown_visible_text, read_json, validate_root)

RESERVATION_SCHEMA = 1
LOCK_NAME = ".learning-id-allocation.lock"
RESERVATION_NAME = "ID_RESERVATIONS.json"

def _store_ids(root: Path) -> set[str]:
    found: set[str] = set()
    for path in iter_event_store_files(root):
        if path.name == RESERVATION_NAME:
            continue
        try: text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc: raise ControlPlaneError(f"non-UTF-8 learning file: {path}") from exc
        if path.suffix == ".md": text = markdown_visible_text(text)
        found.update(match.group(1) for match in EVENT_HEADER_RE.finditer(text))
    return found

def _load_reservations(path: Path) -> dict[str, Any]:
    if not path.exists(): return {"schema_version": RESERVATION_SCHEMA, "reservations": []}
    if path.is_symlink() or not path.is_file(): raise ControlPlaneError("ID reservation store must be a real file")
    value = read_json(path); reservations = value.get("reservations")
    if value.get("schema_version") != RESERVATION_SCHEMA or not isinstance(reservations, list): raise ControlPlaneError("malformed ID_RESERVATIONS.json")
    for entry in reservations:
        if (not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
                or not EVENT_ID_RE.fullmatch(entry["id"]) or entry.get("owner") not in AGENTS):
            raise ControlPlaneError("malformed ID reservation entry")
    return value

def allocate_id(root: Path, *, kind: str, owner: str, day: str | None = None) -> dict[str, Any]:
    root = validate_root(root)
    if kind not in EVENT_KINDS: raise ControlPlaneError(f"kind must be one of {', '.join(EVENT_KINDS)}")
    if owner not in AGENTS: raise ControlPlaneError(f"owner must be one of {', '.join(AGENTS)}")
    if day is None: day = datetime.now(timezone.utc).strftime("%Y%m%d")
    try: parsed_day = datetime.strptime(day, "%Y%m%d").date()
    except ValueError as exc: raise ControlPlaneError("date must be a valid YYYYMMDD value") from exc
    if parsed_day.year < 2000: raise ControlPlaneError("date is outside the supported range")
    lock_path = root / "team-learnings" / LOCK_NAME
    if lock_path.exists() and lock_path.is_symlink(): raise ControlPlaneError("allocation lock must not be a symlink")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        reservation_path = root / "team-learnings" / RESERVATION_NAME
        reservations = _load_reservations(reservation_path)
        all_ids = _store_ids(root) | {entry["id"] for entry in reservations["reservations"]}
        prefix = f"{kind}-{day}-"
        used = [int(value[len(prefix):]) for value in all_ids if re.fullmatch(re.escape(prefix) + r"\d{3,}", value)]
        event_id = f"{prefix}{max(used, default=0) + 1:03d}"
        reservation = {"id": event_id, "owner": owner, "reserved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pid": os.getpid()}
        reservations["reservations"].append(reservation); atomic_json(reservation_path, reservations)
        return reservation
    finally:
        try: fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally: os.close(descriptor)
