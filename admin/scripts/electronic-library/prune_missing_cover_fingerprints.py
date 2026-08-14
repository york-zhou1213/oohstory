#!/usr/bin/env python3
"""Delete exact known-placeholder files after every durable pointer moved."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
from project_paths import APP_ROOT  # noqa: E402

LIBRARY_ROOT = APP_ROOT / "electronic-library"
COVER_ROOT = (LIBRARY_ROOT / "封面").resolve()
AUDIT_ROOT = LIBRARY_ROOT / "全局索引" / "cover-prune-manifests"
sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.cover_failure_policy import (  # noqa: E402
    is_missing_cover_placeholder_sha256,
)
from oohstory_library.services.library_runtime_mysql import (  # noqa: E402
    MySQLLibraryRuntime,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("清理清单缺少 rows")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        filename = str(row.get("filename") or "")
        digest = str(row.get("sha256") or "").casefold()
        if (
            filename
            and filename not in seen
            and Path(filename).name == filename
            and is_missing_cover_placeholder_sha256(digest)
        ):
            seen.add(filename)
            result.append(
                {
                    "catalog_id": int(row["catalog_id"]),
                    "filename": filename,
                    "sha256": digest,
                }
            )
    return result


def write_audit(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    os.chmod(path, 0o600)


def referenced_filenames(
    runtime: MySQLLibraryRuntime,
    rows: list[dict[str, Any]],
) -> set[str]:
    """Read the four authoritative reference sets in bounded batches."""

    referenced: set[str] = set()
    with runtime.pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            for offset in range(0, len(rows), 250):
                group = rows[offset : offset + 250]
                names = [str(row["filename"]) for row in group]
                keys = [value for name in names for value in (name, f"封面/{name}")]
                name_marks = ",".join(["%s"] * len(names))
                key_marks = ",".join(["%s"] * len(keys))

                cursor.execute(
                    f"SELECT cover_object_key FROM books "
                    f"WHERE cover_object_key IN ({key_marks})",
                    keys,
                )
                referenced.update(
                    Path(str(row["cover_object_key"])).name
                    for row in cursor.fetchall()
                )
                cursor.execute(
                    f"SELECT filename FROM library_covers "
                    f"WHERE filename IN ({name_marks})",
                    names,
                )
                referenced.update(
                    str(row["filename"]) for row in cursor.fetchall()
                )
                cursor.execute(
                    f"SELECT object_key FROM object_assets "
                    f"WHERE asset_type='cover' AND object_key IN ({key_marks})",
                    keys,
                )
                referenced.update(
                    Path(str(row["object_key"])).name
                    for row in cursor.fetchall()
                )
                cursor.execute(
                    f"""
                    SELECT original_filename,replacement_filename
                    FROM library_clean_cover_jobs
                    WHERE status IN ('pending','manual_pending','processing')
                      AND (
                        original_filename IN ({name_marks})
                        OR replacement_filename IN ({name_marks})
                      )
                    """,
                    [*names, *names],
                )
                for row in cursor.fetchall():
                    for column in ("original_filename", "replacement_filename"):
                        value = str(row.get(column) or "")
                        if value:
                            referenced.add(value)
    return referenced


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--writers-stopped",
        action="store_true",
        help="确认所有封面写入 worker 已停止；--apply 时必须提供",
    )
    args = parser.parse_args()
    if args.apply and not args.writers_stopped:
        parser.error("--apply 必须同时提供 --writers-stopped")

    rows = load_manifest(args.manifest.resolve())
    runtime = MySQLLibraryRuntime()
    referenced = referenced_filenames(runtime, rows)
    outcomes: dict[str, int] = {}
    deleted_bytes = 0
    audit_rows: list[dict[str, Any]] = []
    for row in rows:
        path = (COVER_ROOT / row["filename"]).resolve()
        status = "eligible"
        size = 0
        references: dict[str, int] = {}
        actual_digest = ""
        if path.parent != COVER_ROOT:
            status = "unsafe_path"
        elif not path.is_file():
            status = "already_missing"
        else:
            size = path.stat().st_size
            actual_digest = file_sha256(path)
            if actual_digest != row["sha256"]:
                status = "hash_changed"
            elif not is_missing_cover_placeholder_sha256(actual_digest):
                status = "not_placeholder"
            else:
                if row["filename"] in referenced:
                    references = {"snapshot": 1}
                    status = "referenced"
                elif args.apply:
                    retiring = (
                        COVER_ROOT
                        / (
                            f".retiring-missing-batch-{row['catalog_id']}-"
                            f"{actual_digest[:16]}{path.suffix}"
                        )
                    ).resolve()
                    if retiring.parent != COVER_ROOT:
                        status = "unsafe_retiring_path"
                    else:
                        os.replace(path, retiring)
                        retiring.unlink()
                        status = "deleted"
                        deleted_bytes += size
        outcomes[status] = outcomes.get(status, 0) + 1
        audit_rows.append(
            {
                **row,
                "status": status,
                "bytes": size,
                "actual_sha256": actual_digest,
                "references": references,
            }
        )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    audit_path = AUDIT_ROOT / f"missing-placeholder-prune-{timestamp}.json.gz"
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_manifest": str(args.manifest.resolve()),
        "apply": bool(args.apply),
        "candidates": len(rows),
        "outcomes": outcomes,
        "deleted_bytes": deleted_bytes,
        "rows": audit_rows,
    }
    write_audit(audit_path, report)
    print(
        json.dumps(
            {
                "apply": bool(args.apply),
                "candidates": len(rows),
                "outcomes": outcomes,
                "deleted_bytes": deleted_bytes,
                "audit": str(audit_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if outcomes.get("hash_changed") or outcomes.get("referenced") else 0


if __name__ == "__main__":
    raise SystemExit(main())
