#!/usr/bin/env python3
"""Verify NAS copies and register stable object keys in MySQL."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable


from project_paths import APP_ROOT  # noqa: E402
sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
)


DEFAULT_LIBRARY_ROOT = APP_ROOT / "electronic-library"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(2 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def body_rows(pool: MySQLConnectionPool) -> list[dict[str, Any]]:
    with pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id AS catalog_id,
                    legacy_output_path,
                    bytes,
                    sha256
                FROM books
                WHERE status='done'
                  AND NULLIF(TRIM(COALESCE(legacy_output_path, '')), '')
                      IS NOT NULL
                ORDER BY id
                """
            )
            return [dict(row) for row in cursor.fetchall()]


def cover_rows(index_path: Path) -> list[dict[str, Any]]:
    if not index_path.is_file():
        return []
    connection = sqlite3.connect(
        f"{index_path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT catalog_id, filename, sha256
                FROM covers
                WHERE status='done'
                  AND NULLIF(TRIM(COALESCE(filename, '')), '') IS NOT NULL
                ORDER BY catalog_id
                """
            )
        ]
    finally:
        connection.close()


def verify_body(
    row: dict[str, Any],
    *,
    local_root: Path,
    object_root: Path,
    full_hash: bool,
) -> dict[str, Any]:
    catalog_id = int(row["catalog_id"])
    source = Path(str(row["legacy_output_path"])).expanduser().resolve()
    try:
        relative = source.relative_to(local_root)
    except ValueError:
        return {
            "catalog_id": catalog_id,
            "status": "invalid_source_path",
            "source": str(source),
        }
    if not source.is_file():
        return {
            "catalog_id": catalog_id,
            "status": "source_missing",
            "source": str(source),
        }
    object_key = relative.as_posix()
    target = (object_root / relative).resolve()
    try:
        target.relative_to(object_root)
    except ValueError:
        return {"catalog_id": catalog_id, "status": "invalid_object_key"}
    if not target.is_file():
        return {
            "catalog_id": catalog_id,
            "status": "missing",
            "object_key": object_key,
        }
    source_size = source.stat().st_size
    actual_size = target.stat().st_size
    if actual_size != source_size:
        return {
            "catalog_id": catalog_id,
            "status": "size_mismatch",
            "object_key": object_key,
            "source_bytes": source_size,
            "actual_bytes": actual_size,
        }
    expected_size = int(row.get("bytes") or 0)
    expected_hash = str(row.get("sha256") or "").strip().lower()
    if full_hash:
        source_hash = file_sha256(source)
        actual_hash = file_sha256(target)
    else:
        source_hash = expected_hash
        actual_hash = expected_hash or file_sha256(target)
    if full_hash and actual_hash != source_hash:
        return {
            "catalog_id": catalog_id,
            "status": "hash_mismatch",
            "object_key": object_key,
            "source_sha256": source_hash,
            "actual_sha256": actual_hash,
        }
    return {
        "catalog_id": catalog_id,
        "status": "verified",
        "asset_type": "body",
        "object_key": object_key,
        "bytes": actual_size,
        "sha256": actual_hash,
        "content_type": "text/plain; charset=utf-8",
        "metadata_mismatch": bool(
            (expected_size and expected_size != actual_size)
            or (expected_hash and expected_hash != actual_hash)
        ),
    }


def verify_cover(
    row: dict[str, Any],
    *,
    local_root: Path,
    object_root: Path,
    full_hash: bool,
) -> dict[str, Any]:
    catalog_id = int(row["catalog_id"])
    filename = Path(str(row["filename"])).name
    object_key = f"封面/{filename}"
    source = (local_root / object_key).resolve()
    try:
        source.relative_to(local_root)
    except ValueError:
        return {"catalog_id": catalog_id, "status": "invalid_source_path"}
    if not source.is_file():
        return {
            "catalog_id": catalog_id,
            "status": "source_missing",
            "source": str(source),
        }
    target = (object_root / object_key).resolve()
    if not target.is_file():
        return {
            "catalog_id": catalog_id,
            "status": "missing",
            "object_key": object_key,
        }
    source_size = source.stat().st_size
    actual_size = target.stat().st_size
    if actual_size != source_size:
        return {
            "catalog_id": catalog_id,
            "status": "size_mismatch",
            "object_key": object_key,
            "source_bytes": source_size,
            "actual_bytes": actual_size,
        }
    expected_hash = str(row.get("sha256") or "").strip().lower()
    if full_hash:
        source_hash = file_sha256(source)
        actual_hash = file_sha256(target)
    else:
        source_hash = expected_hash
        actual_hash = expected_hash or file_sha256(target)
    if full_hash and actual_hash != source_hash:
        return {
            "catalog_id": catalog_id,
            "status": "hash_mismatch",
            "object_key": object_key,
            "source_sha256": source_hash,
            "actual_sha256": actual_hash,
        }
    suffix = target.suffix.casefold()
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    return {
        "catalog_id": catalog_id,
        "status": "verified",
        "asset_type": "cover",
        "object_key": object_key,
        "bytes": actual_size,
        "sha256": actual_hash,
        "content_type": content_type,
        "metadata_mismatch": bool(
            expected_hash and expected_hash != actual_hash
        ),
    }


def chunks(values: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def register_verified(
    pool: MySQLConnectionPool,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    body = [row for row in rows if row["asset_type"] == "body"]
    covers = [row for row in rows if row["asset_type"] == "cover"]
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            if body:
                cursor.executemany(
                    """
                    UPDATE books
                    SET body_object_key=%s,
                        bytes=%s,
                        sha256=%s,
                        row_version=row_version+1
                    WHERE id=%s
                    """,
                    [
                        (
                            row["object_key"],
                            int(row["bytes"]),
                            row["sha256"] or None,
                            int(row["catalog_id"]),
                        )
                        for row in body
                    ],
                )
            if covers:
                cursor.executemany(
                    """
                    UPDATE books
                    SET cover_object_key=%s, row_version=row_version+1
                    WHERE id=%s
                    """,
                    [
                        (row["object_key"], int(row["catalog_id"]))
                        for row in covers
                    ],
                )
            cursor.executemany(
                """
                INSERT INTO object_assets (
                    catalog_id, asset_type, object_key, storage_backend,
                    content_type, bytes, sha256, state
                ) VALUES (
                    %s, %s, %s, 'nas', %s, %s, %s, 'available'
                )
                ON DUPLICATE KEY UPDATE
                    object_key=VALUES(object_key),
                    storage_backend='nas',
                    content_type=VALUES(content_type),
                    bytes=VALUES(bytes),
                    sha256=VALUES(sha256),
                    state='available'
                """,
                [
                    (
                        int(row["catalog_id"]),
                        row["asset_type"],
                        row["object_key"],
                        row["content_type"],
                        int(row["bytes"]),
                        row["sha256"] or None,
                    )
                    for row in rows
                ],
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--asset",
        choices=("body", "cover", "all"),
        default="all",
    )
    parser.add_argument("--local-root", type=Path, default=DEFAULT_LIBRARY_ROOT)
    parser.add_argument(
        "--cover-index",
        type=Path,
        default=DEFAULT_LIBRARY_ROOT / "全局索引" / "cover_index.sqlite3",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Only verify paths/sizes when an expected SHA-256 already exists",
    )
    parser.add_argument(
        "--no-register",
        action="store_true",
        help="Audit without updating MySQL object keys",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    workers = min(max(args.workers, 1), 16)
    settings = LibraryInfrastructureSettings.from_env()
    pool = MySQLConnectionPool(settings)
    local_root = args.local_root.expanduser().resolve()
    object_root = settings.object_root.expanduser().resolve()
    object_root.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[str, dict[str, Any]]] = []
    if args.asset in {"body", "all"}:
        jobs.extend(("body", row) for row in body_rows(pool))
    if args.asset in {"cover", "all"}:
        jobs.extend(("cover", row) for row in cover_rows(args.cover_index))
    started = time.monotonic()

    def verify(job: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        kind, row = job
        if kind == "body":
            return verify_body(
                row,
                local_root=local_root,
                object_root=object_root,
                full_hash=not args.quick,
            )
        return verify_cover(
            row,
            local_root=local_root,
            object_root=object_root,
            full_hash=not args.quick,
        )

    counts: dict[str, int] = {}
    metadata_mismatches = 0
    failures: list[dict[str, Any]] = []
    verified_batch: list[dict[str, Any]] = []
    processed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for result in executor.map(verify, jobs):
            processed += 1
            status = str(result["status"])
            counts[status] = counts.get(status, 0) + 1
            if status == "verified":
                if result.get("metadata_mismatch"):
                    metadata_mismatches += 1
                verified_batch.append(result)
                if len(verified_batch) >= 500 and not args.no_register:
                    register_verified(pool, verified_batch)
                    verified_batch.clear()
            elif len(failures) < 200:
                failures.append(result)
            if processed % 1000 == 0:
                print(
                    json.dumps(
                        {
                            "status": "verifying",
                            "processed": processed,
                            "total": len(jobs),
                            "counts": counts,
                            "elapsed_seconds": round(
                                time.monotonic() - started, 2
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    if verified_batch and not args.no_register:
        register_verified(pool, verified_batch)
    report = {
        "status": (
            "verified"
            if counts.get("verified", 0) == len(jobs)
            else "incomplete"
        ),
        "asset": args.asset,
        "quick": bool(args.quick),
        "registered": not args.no_register,
        "processed": processed,
        "counts": counts,
        "metadata_mismatches": metadata_mismatches,
        "failures": failures,
        "object_root": str(object_root),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(args.report.suffix + ".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
