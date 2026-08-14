#!/usr/bin/env python3
"""One-time migration to the English electronic-library root and global deconstructions."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def migrate_catalog_paths(
    catalog_path: Path, old_root: Path, new_root: Path
) -> int:
    old_prefix = str(old_root.resolve())
    new_prefix = str(new_root.resolve())
    with sqlite3.connect(catalog_path, timeout=30) as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) FROM books
            WHERE output_path IS NOT NULL AND output_path LIKE ?
            """,
            (old_prefix + "%",),
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE books
            SET output_path = ? || substr(output_path, ?),
                updated_at = datetime('now')
            WHERE output_path IS NOT NULL AND output_path LIKE ?
            """,
            (new_prefix, len(old_prefix) + 1, old_prefix + "%"),
        )
        conn.commit()
    return int(count)


def import_legacy_deconstructions(
    projects_root: Path, global_root: Path
) -> list[dict[str, str]]:
    imported: list[dict[str, str]] = []
    global_root.mkdir(parents=True, exist_ok=True)
    if not projects_root.exists():
        return imported
    for legacy_root in sorted(projects_root.glob("*/拆文库")):
        if not legacy_root.is_dir():
            continue
        project_name = legacy_root.parent.name
        for source in sorted(legacy_root.iterdir()):
            if not source.is_dir() or source.name.startswith("."):
                continue
            target = global_root / source.name
            if target.exists():
                imported.append(
                    {
                        "project": project_name,
                        "source": str(source),
                        "target": str(target),
                        "status": "skipped_existing",
                    }
                )
                continue
            shutil.copytree(source, target, copy_function=shutil.copy2)
            imported.append(
                {
                    "project": project_name,
                    "source": str(source),
                    "target": str(target),
                    "status": "copied",
                }
            )
    return imported


def migrate_global_indexes(runtime_dir: Path, index_root: Path) -> list[str]:
    moved: list[str] = []
    index_root.mkdir(parents=True, exist_ok=True)
    names = (
        "electronic_library_index.sqlite3",
        "electronic_library_index.sqlite3-shm",
        "electronic_library_index.sqlite3-wal",
        "electronic_library_index_status.json",
        "electronic_library_plot_index_status.json",
    )
    for name in names:
        source = runtime_dir / name
        target = index_root / name
        if not source.exists() or target.exists():
            continue
        shutil.move(str(source), str(target))
        moved.append(str(target))
    return moved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", required=True)
    parser.add_argument("--new-root", required=True)
    parser.add_argument(
        "--projects-root",
        default="/var/lib/oohstory-admin/library-project",
    )
    parser.add_argument(
        "--runtime-dir",
        default="/opt/oohstory-admin/runtime",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    old_root = Path(args.old_root).expanduser()
    new_root = Path(args.new_root).expanduser().resolve()
    projects_root = Path(args.projects_root).expanduser().resolve()
    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    catalog_path = new_root / "catalog.sqlite3"
    global_root = new_root / "全局拆书库"
    index_root = new_root / "全局索引"

    if not args.apply:
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "catalog": str(catalog_path),
                    "old_root": str(old_root),
                    "new_root": str(new_root),
                    "global_deconstruction_root": str(global_root),
                    "global_index_root": str(index_root),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not catalog_path.is_file():
        raise FileNotFoundError(f"catalog not found: {catalog_path}")

    updated_rows = migrate_catalog_paths(catalog_path, old_root, new_root)
    imported = import_legacy_deconstructions(projects_root, global_root)
    moved_indexes = migrate_global_indexes(runtime_dir, index_root)
    payload = {
        "migrated_at": datetime.now().isoformat(timespec="seconds"),
        "old_root": str(old_root),
        "new_root": str(new_root),
        "catalog_paths_updated": updated_rows,
        "legacy_deconstructions": imported,
        "global_indexes_moved": moved_indexes,
    }
    manifest = global_root / ".migration-20260728.json"
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
