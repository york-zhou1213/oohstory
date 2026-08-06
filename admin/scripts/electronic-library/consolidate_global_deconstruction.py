#!/usr/bin/env python3
"""Consolidate the legacy global deconstruction root into the canonical one.

The canonical root is ``/srv/oohstory/library/txt80/全局拆书库``.  After a
verified overlay the legacy physical directory can be removed.  Production
configuration must point directly at the canonical root.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_LEGACY_ROOT = Path("/srv/oohstory/library/全局拆书库")
DEFAULT_CANONICAL_ROOT = Path(
    "/srv/oohstory/library/txt80/全局拆书库"
)
DEFAULT_PROJECTS_ROOT = Path(
    "/var/lib/oohstory-admin/library-project"
)
LEGACY_PROJECT_PREFIX = (
    "/opt/oohstory-admin/"
    "electronic-library/全局拆书库"
)
TEXT_SUFFIXES = {".json", ".md", ".log", ".txt", ".sh"}
RSYNC_EXCLUDES = (
    ".project-deconstruction-links.json",
    ".project-deconstruction-links.json.lock",
    "*/原文/原文.txt",
)


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(data)
    os.chmod(temporary, path.stat().st_mode)
    os.replace(temporary, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    data += b"\n"
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def rsync_arguments(source: Path, target: Path, *, dry_run: bool) -> list[str]:
    command = ["rsync", "-a"]
    if dry_run:
        command.extend(["-n", "--itemize-changes"])
    for pattern in RSYNC_EXCLUDES:
        command.extend(["--exclude", pattern])
    command.extend([f"{source}/", f"{target}/"])
    return command


def run_rsync(source: Path, target: Path, *, dry_run: bool) -> str:
    result = subprocess.run(
        rsync_arguments(source, target, dry_run=dry_run),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def verify_overlay(source: Path, target: Path) -> None:
    command = ["rsync", "-ainc", "--itemize-changes"]
    for pattern in RSYNC_EXCLUDES:
        command.extend(["--exclude", pattern])
    command.extend([f"{source}/", f"{target}/"])
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    changes = [
        line
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith(".")
    ]
    if changes:
        raise RuntimeError(
            "legacy overlay verification failed: " + "; ".join(changes[:8])
        )


def rewrite_legacy_paths(root: Path, legacy: Path, canonical: Path) -> int:
    replacements = {
        str(legacy).encode("utf-8"): str(canonical).encode("utf-8"),
        str(DEFAULT_LEGACY_ROOT).encode("utf-8"): str(canonical).encode("utf-8"),
        LEGACY_PROJECT_PREFIX.encode("utf-8"): str(canonical).encode("utf-8"),
    }
    rewritten = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if (
            path.suffix not in TEXT_SUFFIXES
            and not path.name.startswith("_progress.md")
        ):
            continue
        data = path.read_bytes()
        updated = data
        for old, new in replacements.items():
            updated = updated.replace(old, new)
        if updated != data:
            atomic_write(path, updated)
            rewritten += 1
    return rewritten


def task_source_map(canonical: Path) -> dict[str, Path]:
    selected: dict[str, tuple[str, Path]] = {}
    for task_path in (canonical / ".tasks").glob("*.json"):
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        output = Path(str(task.get("output_dir") or "")).expanduser()
        source = Path(str(task.get("source_path") or "")).expanduser()
        try:
            output_key = output.resolve().relative_to(canonical.resolve()).parts[0]
        except (OSError, ValueError, IndexError):
            continue
        if not source.is_file() or source.stat().st_size <= 0:
            continue
        updated_at = str(task.get("updated_at") or task.get("created_at") or "")
        previous = selected.get(output_key)
        if previous is None or updated_at >= previous[0]:
            selected[output_key] = (updated_at, source.resolve())
    return {key: value[1] for key, value in selected.items()}


def replace_original_copies(canonical: Path) -> tuple[int, int]:
    sources = task_source_map(canonical)
    linked = 0
    reclaimed = 0
    unresolved: list[str] = []
    for original in canonical.glob("*/原文/原文.txt"):
        source = sources.get(original.parents[1].name)
        if source is None:
            unresolved.append(str(original))
            continue
        if original.is_symlink():
            try:
                if original.resolve(strict=True) == source:
                    continue
            except OSError:
                pass
        elif original.exists():
            reclaimed += original.stat().st_size

        relative_target = os.path.relpath(source, start=original.parent)
        temporary = original.with_name(f".{original.name}.link-{os.getpid()}")
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(relative_target)
        os.replace(temporary, original)
        linked += 1
    if unresolved:
        raise RuntimeError(
            "no canonical source_path for original references: "
            + "; ".join(unresolved)
        )
    return linked, reclaimed


def rewrite_project_links(projects_root: Path, canonical: Path) -> int:
    rewritten = 0
    if not projects_root.is_dir():
        return rewritten
    for project_root in projects_root.iterdir():
        reference_root = project_root / "拆文库"
        if not reference_root.is_dir() or reference_root.is_symlink():
            continue
        for link in reference_root.iterdir():
            if not link.is_symlink():
                continue
            target_text = os.readlink(link)
            if "全局拆书库" not in target_text:
                continue
            target = canonical / link.name
            temporary = link.with_name(f".{link.name}.link-{os.getpid()}")
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()
            temporary.symlink_to(target, target_is_directory=True)
            os.replace(temporary, link)
            rewritten += 1
    return rewritten


def rebuild_registry(canonical: Path) -> dict[str, Any]:
    backend_root = Path(__file__).resolve().parents[2] / "src"
    sys.path.insert(0, str(backend_root))
    from oohstory_library.services.electronic_library import ElectronicLibraryService

    registry_path = canonical / ".project-deconstruction-links.json"
    atomic_json(
        registry_path,
        {"schema_version": 1, "updated_at": "", "links": []},
    )
    service = ElectronicLibraryService(
        library_root=canonical.parent,
        runtime_dir=canonical.parent / "全局索引",
    )
    if service.global_deconstruction_root.resolve() != canonical.resolve():
        raise RuntimeError(
            "runtime global deconstruction root does not match canonical root"
        )
    return service.repair_project_deconstruction_links()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", default=str(DEFAULT_LEGACY_ROOT))
    parser.add_argument("--canonical-root", default=str(DEFAULT_CANONICAL_ROOT))
    parser.add_argument("--projects-root", default=str(DEFAULT_PROJECTS_ROOT))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--remove-legacy-after-verify", action="store_true")
    args = parser.parse_args()

    legacy = Path(args.legacy_root).expanduser().absolute()
    canonical = Path(args.canonical_root).expanduser().absolute()
    projects_root = Path(args.projects_root).expanduser().absolute()
    if legacy == canonical:
        raise ValueError("legacy and canonical roots must differ")
    if not canonical.is_dir():
        raise FileNotFoundError(f"canonical root missing: {canonical}")

    legacy_is_alias = bool(
        legacy.is_symlink()
        and legacy.resolve(strict=True) == canonical.resolve(strict=True)
    )
    if not legacy.exists() and not legacy.is_symlink():
        raise FileNotFoundError(f"legacy root missing: {legacy}")

    if not args.apply:
        preview = "" if legacy_is_alias else run_rsync(
            legacy, canonical, dry_run=True
        )
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "legacy_root": str(legacy),
                    "canonical_root": str(canonical),
                    "legacy_is_alias": legacy_is_alias,
                    "rsync_changes": len(
                        [
                            line
                            for line in preview.splitlines()
                            if line.strip() and not line.startswith(".")
                        ]
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not legacy_is_alias:
        run_rsync(legacy, canonical, dry_run=False)
        verify_overlay(legacy, canonical)

    rewritten_files = rewrite_legacy_paths(canonical, legacy, canonical)
    rewritten_links = rewrite_project_links(projects_root, canonical)
    original_links, reclaimed_bytes = replace_original_copies(canonical)
    registry = rebuild_registry(canonical)

    if args.remove_legacy_after_verify and not legacy_is_alias:
        shutil.rmtree(legacy)

    report = {
        "migrated_at": datetime.now().isoformat(timespec="seconds"),
        "legacy_root": str(legacy),
        "canonical_root": str(canonical),
        "legacy_removed": not legacy.exists() and not legacy.is_symlink(),
        "rewritten_text_files": rewritten_files,
        "rewritten_project_links": rewritten_links,
        "original_links_created": original_links,
        "original_copy_bytes_reclaimed": reclaimed_bytes,
        "registry_total": int(registry.get("registry", {}).get("total") or 0),
        "registry_errors": registry.get("errors") or [],
    }
    report_path = canonical / ".consolidation-20260802.json"
    atomic_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
