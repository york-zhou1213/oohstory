#!/usr/bin/env python3
"""Move MySQL body mappings from duplicate body/ objects to 书籍/ files.

Audit is the default.  It hashes every categorized TXT against the digest in
its content-addressed key and writes a compressed manifest.  Apply consumes
that manifest, updates MySQL in resumable batches, then removes only body/
files which have zero remaining database references.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from project_paths import APP_ROOT

sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.library_cache import (  # noqa: E402
    LibraryCacheSettings,
    RedisHotCache,
)
from oohstory_library.services.library_catalog_mysql import (  # noqa: E402
    MySQLCatalogStore,
)
from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
)


SCHEMA = "oohstory.body-to-categorized-books.v1"
BODY_KEY = re.compile(
    r"^body/([0-9a-f]{2})/([0-9a-f]{2})/([0-9a-f]{64})\.txt$"
)
MYSQL_LOCK = "oohstory:body-to-categorized-books:v1"
DEFAULT_ROOT = Path("/srv/oohstory/library")


class MigrationError(RuntimeError):
    pass


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _atomic_gzip_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6) as stream:
                stream.write(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _read_gzip_json(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve(strict=True)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise MigrationError("manifest 必须是真实普通文件")
    with gzip.open(path, "rt", encoding="utf-8") as reader:
        payload = json.load(reader)
    if payload.get("schema") != SCHEMA:
        raise MigrationError("manifest schema 不匹配")
    if payload.get("status") != "audit_complete":
        raise MigrationError("只允许使用 audit_complete manifest")
    if not isinstance(payload.get("records"), list):
        raise MigrationError("manifest records 无效")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chunks(values: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _fetch_rows(pool: MySQLConnectionPool) -> tuple[list[dict[str, Any]], dict[str, int]]:
    with pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    b.id AS catalog_id,
                    b.body_object_key,
                    b.legacy_output_path,
                    b.bytes AS book_bytes,
                    b.sha256 AS book_sha256,
                    a.id AS asset_id,
                    a.object_key AS asset_object_key,
                    a.bytes AS asset_bytes,
                    a.sha256 AS asset_sha256
                FROM books AS b
                LEFT JOIN object_assets AS a
                  ON a.catalog_id=b.id AND a.asset_type='body'
                WHERE b.body_object_key LIKE 'body/%'
                ORDER BY b.id
                """
            )
            rows = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT object_key, catalog_id
                FROM object_assets
                WHERE asset_type='body' AND object_key LIKE '书籍/%'
                """
            )
            canonical_assets = {
                str(row["object_key"]): int(row["catalog_id"])
                for row in cursor.fetchall()
            }
    return rows, canonical_assets


def _canonical_file(root: Path, raw_path: Any) -> Path:
    value = Path(str(raw_path or "").strip()).expanduser()
    if not str(value):
        raise MigrationError("legacy_output_path 为空")
    candidate = value if value.is_absolute() else root / value
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to((root / "书籍").resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise MigrationError("分类正文不存在或不在电子书库根目录的书籍/内") from exc
    metadata = resolved.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise MigrationError("分类正文必须是真实普通文件")
    return resolved


def _body_file(root: Path, object_key: str) -> tuple[Path, str]:
    match = BODY_KEY.fullmatch(object_key)
    if (
        not match
        or match.group(1) != match.group(3)[:2]
        or match.group(2) != match.group(3)[2:4]
    ):
        raise MigrationError("body_object_key 不是标准内容寻址路径")
    raw = root / Path(object_key)
    if raw.is_symlink():
        raise MigrationError("body 对象不能是符号链接")
    try:
        resolved = raw.resolve(strict=True)
        resolved.relative_to((root / "body").resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise MigrationError("body 对象不存在或越界") from exc
    metadata = resolved.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise MigrationError("body 对象不是普通文件")
    return resolved, match.group(3)


def _audit_one(
    row: dict[str, Any],
    *,
    root: Path,
    canonical_assets: dict[str, int],
) -> dict[str, Any]:
    catalog_id = int(row["catalog_id"])
    old_key = str(row.get("body_object_key") or "")
    body_path, expected_sha = _body_file(root, old_key)
    canonical_path = _canonical_file(root, row.get("legacy_output_path"))
    new_key = canonical_path.relative_to(root).as_posix()
    if not new_key.startswith("书籍/"):
        raise MigrationError("规范正文键未落在 书籍/ 下")
    existing_owner = canonical_assets.get(new_key)
    if existing_owner is not None and existing_owner != catalog_id:
        raise MigrationError(
            f"分类正文对象键已属于其他书目 {existing_owner}: {new_key}"
        )
    body_stat = body_path.stat()
    canonical_stat = canonical_path.stat()
    if body_stat.st_size != canonical_stat.st_size:
        raise MigrationError("body 副本与分类正文大小不一致")
    for field in ("book_bytes", "asset_bytes"):
        value = int(row.get(field) or 0)
        if value and value != canonical_stat.st_size:
            raise MigrationError(f"{field} 与分类正文大小不一致")
    for field in ("book_sha256", "asset_sha256"):
        value = str(row.get(field) or "").strip().lower()
        if value and value != expected_sha:
            raise MigrationError(f"{field} 与 body 内容寻址摘要不一致")
    asset_key = str(row.get("asset_object_key") or "")
    if asset_key and asset_key != old_key:
        raise MigrationError("object_assets 与 books 的 body 键不一致")
    canonical_sha = _sha256(canonical_path)
    if canonical_sha != expected_sha:
        raise MigrationError("分类正文 SHA-256 与 body 内容寻址摘要不一致")
    return {
        "catalog_id": catalog_id,
        "old_object_key": old_key,
        "new_object_key": new_key,
        "old_legacy_output_path": str(row.get("legacy_output_path") or ""),
        "canonical_path": str(canonical_path),
        "bytes": canonical_stat.st_size,
        "sha256": canonical_sha,
        "canonical_dev": canonical_stat.st_dev,
        "canonical_ino": canonical_stat.st_ino,
        "canonical_mtime_ns": canonical_stat.st_mtime_ns,
        "body_dev": body_stat.st_dev,
        "body_ino": body_stat.st_ino,
        "asset_id": int(row.get("asset_id") or 0),
    }


def audit(
    pool: MySQLConnectionPool,
    *,
    root: Path,
    workers: int,
    manifest_path: Path,
    reuse_manifest_path: Path | None = None,
) -> dict[str, Any]:
    rows, canonical_assets = _fetch_rows(pool)
    reused_by_id: dict[int, dict[str, Any]] = {}
    if reuse_manifest_path is not None:
        reused_manifest = _read_gzip_json(reuse_manifest_path)
        if Path(str(reused_manifest.get("library_root") or "")).resolve() != root:
            raise MigrationError("复用 manifest 的 library_root 不一致")
        reused_by_id = {
            int(record["catalog_id"]): dict(record)
            for record in reused_manifest["records"]
        }
    seen_new_keys: dict[str, int] = {}
    started = time.monotonic()

    def verify(row: dict[str, Any]) -> dict[str, Any]:
        reused = reused_by_id.get(int(row["catalog_id"]))
        if reused is not None:
            old_key = str(row.get("body_object_key") or "")
            canonical = _canonical_file(root, row.get("legacy_output_path"))
            if (
                old_key == reused.get("old_object_key")
                and str(canonical) == reused.get("canonical_path")
            ):
                _verify_unchanged(reused, root)
                body_path, expected_sha = _body_file(root, old_key)
                body_metadata = body_path.stat()
                if (
                    expected_sha == reused.get("sha256")
                    and body_metadata.st_size == int(reused["bytes"])
                    and str(row.get("asset_object_key") or "")
                    in {"", old_key}
                    and all(
                        not str(row.get(field) or "").strip()
                        or str(row.get(field) or "").strip().lower()
                        == expected_sha
                        for field in ("book_sha256", "asset_sha256")
                    )
                    and all(
                        not int(row.get(field) or 0)
                        or int(row.get(field) or 0) == int(reused["bytes"])
                        for field in ("book_bytes", "asset_bytes")
                    )
                    and canonical_assets.get(
                        str(reused["new_object_key"]),
                        int(row["catalog_id"]),
                    )
                    == int(row["catalog_id"])
                ):
                    return {**reused, "audit_reused": True}
        return _audit_one(row, root=root, canonical_assets=canonical_assets)

    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, record in enumerate(executor.map(verify, rows), start=1):
            previous = seen_new_keys.setdefault(
                record["new_object_key"], record["catalog_id"]
            )
            if previous != record["catalog_id"]:
                raise MigrationError(
                    f"多个书目共享分类正文键: {record['new_object_key']}"
                )
            records.append(record)
            if index % 2000 == 0 or index == len(rows):
                elapsed = max(time.monotonic() - started, 0.001)
                print(
                    f"已校验 {index}/{len(rows)}，"
                    f"{index / elapsed:.1f} 本/秒",
                    flush=True,
                )
    payload = {
        "schema": SCHEMA,
        "status": "audit_complete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "library_root": str(root),
        "record_count": len(records),
        "distinct_old_objects": len(
            {record["old_object_key"] for record in records}
        ),
        "bytes": sum(int(record["bytes"]) for record in records),
        "records": records,
    }
    _atomic_gzip_json(manifest_path, payload)
    return {
        "mode": "audit",
        "records": len(records),
        "reused": sum(bool(record.get("audit_reused")) for record in records),
        "distinct_old_objects": payload["distinct_old_objects"],
        "bytes": payload["bytes"],
        "manifest": str(manifest_path),
    }


def _lock_value(row: Any, key: str) -> int:
    if isinstance(row, dict):
        return int(row.get(key) or 0)
    return int(row[0] or 0)


def _verify_unchanged(record: dict[str, Any], root: Path) -> None:
    canonical = _canonical_file(root, record["canonical_path"])
    metadata = canonical.stat()
    expected = (
        int(record["canonical_dev"]),
        int(record["canonical_ino"]),
        int(record["bytes"]),
        int(record["canonical_mtime_ns"]),
    )
    actual = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    if actual != expected:
        raise MigrationError(
            f"审计后分类正文发生变化: {record['new_object_key']}"
        )


def _backup_manifest(
    manifest: dict[str, Any],
    *,
    backup_root: Path,
) -> Path:
    destination = (
        backup_root.expanduser().resolve()
        / f"body-to-categorized-books-{_utc_stamp()}"
    )
    destination.mkdir(parents=True, exist_ok=False)
    path = destination / "database-mapping-before.json.gz"
    _atomic_gzip_json(
        path,
        {
            **manifest,
            "status": "pre_apply_database_backup",
            "backed_up_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return path


def _update_batch(
    pool: MySQLConnectionPool,
    records: list[dict[str, Any]],
    *,
    root: Path,
) -> tuple[int, int]:
    updated = 0
    already = 0
    for record in records:
        _verify_unchanged(record, root)
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            for record in records:
                cursor.execute(
                    """
                    UPDATE books
                    SET body_object_key=%s,
                        legacy_output_path=%s,
                        bytes=%s,
                        sha256=%s,
                        row_version=row_version+1,
                        updated_at=UTC_TIMESTAMP(6)
                    WHERE id=%s AND body_object_key=%s
                    """,
                    (
                        record["new_object_key"],
                        record["canonical_path"],
                        int(record["bytes"]),
                        record["sha256"],
                        int(record["catalog_id"]),
                        record["old_object_key"],
                    ),
                )
                if int(cursor.rowcount) == 0:
                    cursor.execute(
                        "SELECT body_object_key FROM books WHERE id=%s FOR UPDATE",
                        (int(record["catalog_id"]),),
                    )
                    current = cursor.fetchone()
                    current_key = (
                        str(current.get("body_object_key") or "")
                        if isinstance(current, dict)
                        else str(current[0] or "")
                        if current
                        else ""
                    )
                    if current_key != record["new_object_key"]:
                        raise MigrationError(
                            f"书目 {record['catalog_id']} 映射已发生冲突变化"
                        )
                    already += 1
                else:
                    updated += 1
                cursor.execute(
                    """
                    INSERT INTO object_assets (
                        catalog_id, asset_type, object_key, storage_backend,
                        content_type, bytes, sha256, state
                    ) VALUES (
                        %s, 'body', %s, 'nas',
                        'text/plain; charset=utf-8', %s, %s, 'available'
                    )
                    ON DUPLICATE KEY UPDATE
                        object_key=VALUES(object_key),
                        storage_backend='nas',
                        content_type=VALUES(content_type),
                        bytes=VALUES(bytes),
                        sha256=VALUES(sha256),
                        state='available'
                    """,
                    (
                        int(record["catalog_id"]),
                        record["new_object_key"],
                        int(record["bytes"]),
                        record["sha256"],
                    ),
                )
        connection.commit()
    return updated, already


def _remaining_body_references(pool: MySQLConnectionPool) -> tuple[int, int]:
    with pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM books
                   WHERE body_object_key LIKE 'body/%') AS books_count,
                  (SELECT COUNT(*) FROM object_assets
                   WHERE asset_type='body' AND object_key LIKE 'body/%')
                    AS assets_count
                """
            )
            row = cursor.fetchone() or {}
    if isinstance(row, dict):
        return int(row["books_count"]), int(row["assets_count"])
    return int(row[0]), int(row[1])


def _remove_verified_duplicates(
    pool: MySQLConnectionPool,
    *,
    root: Path,
    records: list[dict[str, Any]],
) -> tuple[int, int]:
    books_count, assets_count = _remaining_body_references(pool)
    if books_count or assets_count:
        raise MigrationError(
            "仍有 body/ 数据库引用，拒绝删除任何副本: "
            f"books={books_count}, assets={assets_count}"
        )
    expected: dict[str, int] = {}
    for record in records:
        expected.setdefault(record["old_object_key"], int(record["bytes"]))
    removed = 0
    removed_bytes = 0
    if not (root / "body").exists():
        return removed, removed_bytes
    body_root = (root / "body").resolve(strict=True)
    for object_key, expected_size in expected.items():
        if not (root / Path(object_key)).exists():
            continue
        body_path, expected_sha = _body_file(root, object_key)
        if body_path.name != f"{expected_sha}.txt":
            raise MigrationError("body 文件名与摘要不一致")
        metadata = body_path.stat()
        if metadata.st_size != expected_size:
            raise MigrationError(f"待删除 body 文件大小变化: {object_key}")
        body_path.unlink()
        removed += 1
        removed_bytes += metadata.st_size
        parent = body_path.parent
        while parent != body_root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    try:
        body_root.rmdir()
    except OSError:
        pass
    return removed, removed_bytes


def apply(
    pool: MySQLConnectionPool,
    settings: LibraryInfrastructureSettings,
    *,
    root: Path,
    manifest_path: Path,
    backup_root: Path,
    batch_size: int,
) -> dict[str, Any]:
    manifest = _read_gzip_json(manifest_path)
    if Path(str(manifest.get("library_root") or "")).resolve() != root:
        raise MigrationError("manifest library_root 与当前根目录不一致")
    records = [dict(record) for record in manifest["records"]]
    backup = _backup_manifest(manifest, backup_root=backup_root)
    updated = 0
    already = 0
    processed = 0
    with pool.connection() as lock_connection:
        with lock_connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (MYSQL_LOCK,))
            if _lock_value(cursor.fetchone(), "acquired") != 1:
                raise MigrationError("无法取得正文迁移 MySQL 锁")
        try:
            for batch in _chunks(records, batch_size):
                batch_updated, batch_already = _update_batch(
                    pool, batch, root=root
                )
                updated += batch_updated
                already += batch_already
                processed += len(batch)
                if processed % 5000 == 0 or processed == len(records):
                    print(
                        f"已迁移 {processed}/{len(records)}，"
                        f"updated={updated}, already={already}",
                        flush=True,
                    )
            removed, removed_bytes = _remove_verified_duplicates(
                pool, root=root, records=records
            )
        finally:
            with lock_connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s) AS released", (MYSQL_LOCK,))
    cache = RedisHotCache(LibraryCacheSettings.from_infrastructure(settings))
    MySQLCatalogStore(settings, pool, cache)._invalidate_cache("catalog")
    report_path = manifest_path.with_name(
        manifest_path.name.removesuffix(".json.gz") + "-applied.json.gz"
    )
    result = {
        "mode": "apply",
        "records": len(records),
        "updated": updated,
        "already_migrated": already,
        "removed_body_files": removed,
        "removed_bytes": removed_bytes,
        "backup": str(backup),
        "manifest": str(manifest_path),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_gzip_json(
        report_path,
        {
            "schema": SCHEMA,
            "status": "apply_complete",
            **result,
        },
    )
    result["report"] = str(report_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--reuse-manifest", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=Path("/var/backups/oohstory-admin"),
    )
    args = parser.parse_args()
    root = args.library_root.expanduser().resolve(strict=True)
    if root != DEFAULT_ROOT.resolve(strict=True):
        raise MigrationError(f"生产正文根目录必须固定为 {DEFAULT_ROOT}")
    workers = min(max(int(args.workers), 1), 8)
    batch_size = min(max(int(args.batch_size), 50), 2000)
    settings = LibraryInfrastructureSettings.from_env()
    if settings.catalog_backend != "mysql":
        raise MigrationError("只允许在 MySQL catalog backend 执行")
    if settings.object_root.expanduser().resolve() != root:
        raise MigrationError("MySQL object_root 与规范电子书库根目录不一致")
    pool = MySQLConnectionPool(settings)
    manifest_path = (
        args.manifest.expanduser()
        if args.manifest
        else root
        / "全局索引"
        / "body-migration-manifests"
        / f"body-to-books-{_utc_stamp()}.json.gz"
    )
    result = (
        apply(
            pool,
            settings,
            root=root,
            manifest_path=manifest_path,
            backup_root=args.backup_root,
            batch_size=batch_size,
        )
        if args.apply
        else audit(
            pool,
            root=root,
            workers=workers,
            manifest_path=manifest_path,
            reuse_manifest_path=(
                args.reuse_manifest.expanduser()
                if args.reuse_manifest
                else None
            ),
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
