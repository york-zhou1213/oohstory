#!/usr/bin/env python3
"""Replace known placeholder covers with one shared OOHStory default asset.

Targets may come from an operation manifest or an explicitly supplied known
placeholder SHA-256.  Missing artwork is represented by catalog/job state;
``books.cover_object_key`` and ``object_assets`` are reserved for real covers.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
from project_paths import APP_ROOT  # noqa: E402

LIBRARY_ROOT = APP_ROOT / "electronic-library" / "txt80"
COVER_ROOT = (LIBRARY_ROOT / "封面").resolve()
sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.library_runtime_mysql import (  # noqa: E402
    MySQLLibraryRuntime,
)
from oohstory_library.services.cover_failure_policy import (  # noqa: E402
    is_missing_cover_placeholder_image,
    is_missing_cover_placeholder_sha256,
)
from oohstory_library.services.default_cover import (  # noqa: E402
    OOHSTORY_DEFAULT_COVER_SHA256,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(
    path: Path,
    inode_cache: dict[tuple[int, int], str] | None = None,
) -> str:
    """Hash one physical inode once and tolerate a transient mount EINVAL."""

    for attempt in range(3):
        try:
            stat = path.stat()
            key = (int(stat.st_dev), int(stat.st_ino))
            if inode_cache is not None and key in inode_cache:
                return inode_cache[key]
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            value = digest.hexdigest()
            if inode_cache is not None:
                inode_cache[key] = value
            return value
        except OSError:
            if attempt == 2:
                raise
            time.sleep(0.1 * (attempt + 1))
    raise AssertionError("unreachable")


def load_json(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("操作清单必须是 JSON 对象")
    return value


def write_backup(path: Path, payload: dict[str, Any]) -> None:
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


def load_visual_manifest_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the bounded visual audit and reject ambiguous catalog rows."""

    rows: list[dict[str, Any]] = []
    if isinstance(manifest.get("rows"), list):
        rows = [dict(row) for row in manifest["rows"]]
    elif isinstance(manifest.get("families"), dict):
        for family, values in manifest["families"].items():
            if not isinstance(values, list):
                raise ValueError("视觉清单 families 必须包含数组")
            for value in values:
                row = dict(value)
                row.setdefault("family", str(family))
                if "catalog_id" not in row and "id" in row:
                    row["catalog_id"] = row["id"]
                rows.append(row)
    seen: set[int] = set()
    for row in rows:
        catalog_id = int(row.get("catalog_id") or 0)
        digest = str(row.get("sha256") or "").strip().casefold()
        if catalog_id <= 0 or len(digest) != 64:
            raise ValueError("视觉清单包含无效目录 ID 或 SHA-256")
        if catalog_id in seen:
            raise ValueError(f"视觉清单重复目录 ID：{catalog_id}")
        seen.add(catalog_id)
        row["catalog_id"] = catalog_id
        row["sha256"] = digest
    return rows


def validate_visual_rows(
    current: list[dict[str, Any]],
    expected: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Recheck current bytes before a visual audit is allowed to mutate state."""

    eligible: list[dict[str, Any]] = []
    counts = {
        "sha_changed": 0,
        "missing_file": 0,
        "unsafe_filename": 0,
        "visual_mismatch": 0,
    }
    for row in current:
        catalog_id = int(row["catalog_id"])
        audited = expected[catalog_id]
        if str(row.get("sha256") or "").casefold() != audited["sha256"]:
            counts["sha_changed"] += 1
            continue
        filename = str(row.get("filename") or "")
        if not filename or Path(filename).name != filename:
            counts["unsafe_filename"] += 1
            continue
        path = (COVER_ROOT / filename).resolve()
        if path.parent != COVER_ROOT:
            counts["unsafe_filename"] += 1
            continue
        if not path.is_file():
            counts["missing_file"] += 1
            continue
        data = path.read_bytes()
        if not is_missing_cover_placeholder_image(
            data,
            sha256=audited["sha256"],
        ):
            counts["visual_mismatch"] += 1
            continue
        copied = dict(row)
        copied["placeholder_family"] = str(audited.get("family") or "")
        copied["visual_score"] = audited.get("score")
        eligible.append(copied)
    return eligible, counts


def validate_template(path: Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    if len(data) < 8 * 1024 or not data.startswith(b"\xff\xd8"):
        raise ValueError("默认封面必须是至少 8 KiB 的 JPEG")
    return data, sha256_bytes(data)


def batches(values: list[int], size: int = 400):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def select_ids_by_hashes(
    runtime: MySQLLibraryRuntime,
    hashes: set[str],
) -> list[int]:
    values = sorted(hashes)
    marks = ",".join(["%s"] * len(values))
    with runtime.pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT catalog_id FROM library_covers "
                f"WHERE sha256 IN ({marks}) ORDER BY catalog_id",
                values,
            )
            return [int(row["catalog_id"]) for row in cursor.fetchall()]


def select_missing_public_ids(
    runtime: MySQLLibraryRuntime,
) -> tuple[list[int], dict[str, int]]:
    """Return public books whose reader cover is blank or physically absent."""

    with runtime.pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT b.id AS catalog_id,b.cover_object_key,c.cover_url,
                       c.sha256,j.status AS job_status
                FROM books b
                LEFT JOIN library_covers c ON c.catalog_id=b.id
                LEFT JOIN library_clean_cover_jobs j ON j.catalog_id=b.id
                WHERE b.is_active=1 AND b.body_available=1
                ORDER BY b.id
                """
            )
            rows = [dict(row) for row in cursor.fetchall()]
    root = runtime.settings.object_root.resolve()
    ids: list[int] = []
    counts = {"blank_pointer": 0, "missing_file": 0, "unsafe_path": 0}
    for row in rows:
        object_key = str(row.get("cover_object_key") or "").strip()
        if not object_key:
            if (
                str(row.get("cover_url") or "") == "oohstory-default://shared"
                and str(row.get("sha256") or "").casefold()
                == OOHSTORY_DEFAULT_COVER_SHA256
                and str(row.get("job_status") or "")
                in {"generate_pending", "processing"}
            ):
                continue
            counts["blank_pointer"] += 1
            ids.append(int(row["catalog_id"]))
            continue
        candidate = root / object_key
        try:
            resolved = candidate.resolve()
            safe = resolved.is_relative_to(root) and not candidate.is_symlink()
        except (OSError, RuntimeError):
            safe = False
            resolved = candidate
        if not safe:
            counts["unsafe_path"] += 1
            ids.append(int(row["catalog_id"]))
        elif not resolved.is_file():
            counts["missing_file"] += 1
            ids.append(int(row["catalog_id"]))
    counts["scanned_public_books"] = len(rows)
    counts["missing_total"] = len(ids)
    return ids, counts


def select_rows(runtime: MySQLLibraryRuntime, ids: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with runtime.pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            for group in batches(ids):
                marks = ",".join(["%s"] * len(group))
                cursor.execute(
                    f"""
                    SELECT b.id AS catalog_id,b.cover_object_key,b.row_version,
                           b.source_id,b.title,b.author,b.detail_url,c.cover_url,
                           c.filename,c.sha256,c.status AS cover_status,
                           o.object_key AS asset_object_key,
                           o.sha256 AS asset_sha256,o.bytes AS asset_bytes,
                           j.status AS job_status,j.attempts AS job_attempts,
                           j.original_filename,j.replacement_filename
                    FROM books b
                    LEFT JOIN library_covers c ON c.catalog_id=b.id
                    LEFT JOIN object_assets o
                      ON o.catalog_id=b.id AND o.asset_type='cover'
                    LEFT JOIN library_clean_cover_jobs j ON j.catalog_id=b.id
                    WHERE b.id IN ({marks})
                    """,
                    group,
                )
                rows.extend(dict(row) for row in cursor.fetchall())
    return rows


def install_files(rows: list[dict[str, Any]], data: bytes, digest: str) -> int:
    """Install exactly one shared file, irrespective of catalog count."""

    canonical_root = (COVER_ROOT.parent / ".oohstory-default-assets").resolve()
    canonical_root.mkdir(parents=True, exist_ok=True)
    canonical = canonical_root / f"oohstory-default-cover-{digest}.jpg"
    if canonical.exists():
        if not canonical.is_file() or sha256_path(canonical) != digest:
            raise ValueError("OOHStory 默认封面共享资源冲突")
        canonical.chmod(0o644)
        return 0
    part = canonical.with_suffix(canonical.suffix + ".part")
    try:
        part.write_bytes(data)
        os.replace(part, canonical)
    finally:
        part.unlink(missing_ok=True)
    canonical.chmod(0o644)
    return 1


def archive_placeholder_samples(
    rows: list[dict[str, Any]],
    hashes: set[str],
    target_root: Path,
) -> int:
    """Keep one recoverable byte sample for every retired fingerprint."""

    target_root.mkdir(parents=True, exist_ok=True)
    archived: set[str] = set()
    inode_cache: dict[tuple[int, int], str] = {}
    for row in rows:
        name = str(row.get("filename") or "")
        if not name or Path(name).name != name:
            continue
        source = (COVER_ROOT / name).resolve()
        if source.parent != COVER_ROOT or not source.is_file():
            continue
        digest = sha256_path(source, inode_cache)
        if digest not in hashes or digest in archived:
            continue
        shutil.copy2(source, target_root / f"{digest}{source.suffix.casefold()}")
        archived.add(digest)
    return len(archived)


def retire_catalog_placeholder_files(
    rows: list[dict[str, Any]], hashes: set[str]
) -> int:
    """Unlink only catalog files whose exact bytes are now shared fallback."""

    retired = 0
    inode_cache: dict[tuple[int, int], str] = {}
    for row in rows:
        name = str(row.get("filename") or "")
        if not name or Path(name).name != name:
            continue
        path = (COVER_ROOT / name).resolve()
        if path.parent != COVER_ROOT or not path.is_file():
            continue
        try:
            actual = sha256_path(path, inode_cache)
        except OSError:
            # The mounted library can briefly return EINVAL while a heavily
            # linked inode is being unlinked.  Cleanup is resumable and must
            # not turn an already committed state migration into a failure.
            continue
        if actual not in hashes:
            raise ValueError(f"待清理封面哈希已变化：{name}")
        path.unlink(missing_ok=True)
        retired += 1
    return retired


def retire_legacy_default_shards(digest: str) -> int:
    root = (COVER_ROOT.parent / ".oohstory-default-assets").resolve()
    shared = root / f"oohstory-default-cover-{digest}.jpg"
    retired = 0
    for path in root.glob(f"oohstory-default-{digest}-*.jpg"):
        if path == shared or not path.is_file():
            continue
        try:
            actual = sha256_path(path)
        except OSError:
            continue
        if actual != digest:
            raise ValueError(f"旧默认封面分片哈希冲突：{path.name}")
        path.unlink(missing_ok=True)
        retired += 1
    return retired


def retire_orphaned_catalog_defaults(digest: str) -> int:
    """Remove old per-catalog defaults left by earlier AI replacements."""

    retired = 0
    inode_cache: dict[tuple[int, int], str] = {}
    pattern = f"*-oohstory-default-{digest[:16]}.jpg"
    for path in COVER_ROOT.glob(pattern):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            actual = sha256_path(path, inode_cache)
        except OSError:
            continue
        if actual != digest:
            raise ValueError(f"孤立默认封面哈希冲突：{path.name}")
        path.unlink(missing_ok=True)
        retired += 1
    return retired


def apply_batch(
    runtime: MySQLLibraryRuntime,
    rows: list[dict[str, Any]],
    allowed_hashes: set[str],
    digest: str,
    size: int,
) -> tuple[int, int]:
    ids = [int(row["catalog_id"]) for row in rows]
    if not ids:
        return 0, 0
    marks = ",".join(["%s"] * len(ids))
    allowed_values = sorted(allowed_hashes)
    hash_marks = ",".join(["%s"] * len(allowed_values))
    updated = 0
    with runtime.pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT b.id AS catalog_id,c.source_id,c.title,c.author,c.sha256
                FROM books b JOIN library_covers c ON c.catalog_id=b.id
                WHERE b.id IN ({marks}) FOR UPDATE
                """,
                ids,
            )
            locked = [
                dict(row)
                for row in cursor.fetchall()
                if str(row.get("sha256") or "").casefold() in allowed_hashes
            ]
            for row in locked:
                catalog_id = int(row["catalog_id"])
                cursor.execute(
                    f"""
                    UPDATE library_covers
                    SET filename=NULL,sha256=%s,status='ai_fallback',
                        cover_url='oohstory-default://shared',
                        last_error=%s,attempts=0,
                        lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL
                    WHERE catalog_id=%s AND sha256 IN ({hash_marks})
                    """,
                    (
                        digest,
                        "已切换为共享默认封面，等待 AI 生成真实封面",
                        catalog_id,
                        *allowed_values,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                cursor.execute(
                    """
                    UPDATE books SET cover_object_key=NULL,row_version=row_version+1
                    WHERE id=%s
                    """,
                    (catalog_id,),
                )
                cursor.execute(
                    """
                    DELETE FROM object_assets
                    WHERE catalog_id=%s AND asset_type='cover'
                    """,
                    (catalog_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO library_clean_cover_jobs (
                      catalog_id,source_id,title,author,status,
                      original_filename,attempts,last_error,updated_at
                    ) VALUES (%s,%s,%s,%s,'generate_pending',NULL,0,
                              'OOHStory 默认封面已就位，等待优先 AI 重绘',
                              '1970-01-02 00:00:00')
                    ON DUPLICATE KEY UPDATE
                      source_id=VALUES(source_id),title=VALUES(title),
                      author=VALUES(author),status='generate_pending',
                      original_filename=NULL,replacement_url=NULL,
                      replacement_filename=NULL,verification_source=NULL,
                      original_deleted_at=NULL,attempts=0,ai_session_id=NULL,
                      source_width=NULL,source_height=NULL,
                      generated_width=NULL,generated_height=NULL,
                      last_error=VALUES(last_error),lease_owner=NULL,
                      lease_token=NULL,lease_expires_at=NULL,
                      updated_at='1970-01-02 00:00:00'
                    """,
                    (
                        catalog_id,
                        str(row["source_id"]),
                        str(row["title"]),
                        str(row["author"]),
                    ),
                )
                updated += 1
    return updated, len(ids) - updated


def apply_missing_batch(
    runtime: MySQLLibraryRuntime,
    rows: list[dict[str, Any]],
    digest: str,
    size: int,
) -> tuple[int, int]:
    ids = [int(row["catalog_id"]) for row in rows]
    if not ids:
        return 0, 0
    marks = ",".join(["%s"] * len(ids))
    root = runtime.settings.object_root.resolve()
    updated = 0
    with runtime.pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id AS catalog_id,source_id,title,author,detail_url,
                       cover_object_key
                FROM books
                WHERE id IN ({marks}) AND is_active=1 AND body_available=1
                FOR UPDATE
                """,
                ids,
            )
            locked = [dict(row) for row in cursor.fetchall()]
            for row in locked:
                object_key = str(row.get("cover_object_key") or "").strip()
                if object_key:
                    candidate = root / object_key
                    try:
                        resolved = candidate.resolve()
                        safe = (
                            resolved.is_relative_to(root)
                            and not candidate.is_symlink()
                        )
                    except (OSError, RuntimeError):
                        safe = False
                        resolved = candidate
                    if safe and resolved.is_file():
                        continue
                catalog_id = int(row["catalog_id"])
                cursor.execute(
                    """
                    INSERT INTO library_covers (
                      catalog_id,source_id,title,author,detail_url,cover_url,
                      filename,sha256,status,attempts,last_error
                    ) VALUES (%s,%s,%s,%s,%s,
                              'oohstory-default://shared',NULL,%s,
                              'ai_fallback',0,%s)
                    ON DUPLICATE KEY UPDATE
                      source_id=VALUES(source_id),title=VALUES(title),
                      author=VALUES(author),detail_url=VALUES(detail_url),
                      cover_url=VALUES(cover_url),filename=NULL,
                      sha256=VALUES(sha256),status='ai_fallback',attempts=0,
                      last_error=VALUES(last_error),lease_owner=NULL,
                      lease_token=NULL,lease_expires_at=NULL
                    """,
                    (
                        catalog_id,
                        str(row.get("source_id") or ""),
                        str(row.get("title") or ""),
                        str(row.get("author") or ""),
                        str(row.get("detail_url") or ""),
                        digest,
                        "原封面为空或实体缺失，已改用共享默认封面并排队重绘",
                    ),
                )
                cursor.execute(
                    """
                    UPDATE books SET cover_object_key=NULL,row_version=row_version+1
                    WHERE id=%s
                    """,
                    (catalog_id,),
                )
                cursor.execute(
                    """
                    DELETE FROM object_assets
                    WHERE catalog_id=%s AND asset_type='cover'
                    """,
                    (catalog_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO library_clean_cover_jobs (
                      catalog_id,source_id,title,author,status,
                      original_filename,attempts,last_error,updated_at
                    ) VALUES (%s,%s,%s,%s,'generate_pending',NULL,0,%s,
                              '1970-01-02 00:00:00')
                    ON DUPLICATE KEY UPDATE
                      source_id=VALUES(source_id),title=VALUES(title),
                      author=VALUES(author),
                      status=IF(library_clean_cover_jobs.status='processing',
                                'processing','generate_pending'),
                      original_filename=IF(
                        library_clean_cover_jobs.status='processing',
                        library_clean_cover_jobs.original_filename,NULL),
                      replacement_url=IF(
                        library_clean_cover_jobs.status='processing',
                        library_clean_cover_jobs.replacement_url,NULL),
                      replacement_filename=IF(
                        library_clean_cover_jobs.status='processing',
                        library_clean_cover_jobs.replacement_filename,NULL),
                      verification_source=IF(
                        library_clean_cover_jobs.status='processing',
                        library_clean_cover_jobs.verification_source,NULL),
                      original_deleted_at=IF(
                        library_clean_cover_jobs.status='processing',
                        library_clean_cover_jobs.original_deleted_at,NULL),
                      attempts=IF(library_clean_cover_jobs.status='processing',
                                  library_clean_cover_jobs.attempts,0),
                      ai_session_id=IF(
                        library_clean_cover_jobs.status='processing',
                        library_clean_cover_jobs.ai_session_id,NULL),
                      last_error=IF(library_clean_cover_jobs.status='processing',
                                    library_clean_cover_jobs.last_error,
                                    VALUES(last_error)),
                      lease_owner=IF(
                        library_clean_cover_jobs.status='processing',
                        library_clean_cover_jobs.lease_owner,NULL),
                      lease_token=IF(
                        library_clean_cover_jobs.status='processing',
                        library_clean_cover_jobs.lease_token,NULL),
                      lease_expires_at=IF(
                        library_clean_cover_jobs.status='processing',
                        library_clean_cover_jobs.lease_expires_at,NULL),
                      updated_at=IF(
                        library_clean_cover_jobs.status='processing',
                        library_clean_cover_jobs.updated_at,
                        '1970-01-02 00:00:00')
                    """,
                    (
                        catalog_id,
                        str(row.get("source_id") or ""),
                        str(row.get("title") or ""),
                        str(row.get("author") or ""),
                        "OOHStory 默认封面已就位，等待正常 AI 重绘",
                    ),
                )
                updated += 1
    return updated, len(ids) - updated


def main() -> int:
    parser = argparse.ArgumentParser()
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--manifest", type=Path)
    targets.add_argument(
        "--visual-manifest",
        type=Path,
        help="经视觉模板审计的目录行；执行前会重新校验当前 SHA 和图片",
    )
    targets.add_argument(
        "--sha256",
        dest="sha256s",
        action="append",
        help="已登记的缺失封面 SHA-256；可重复提供",
    )
    targets.add_argument(
        "--missing-public",
        action="store_true",
        help="扫描所有可阅读书籍，将空指针或缺失实体改为默认封面",
    )
    targets.add_argument(
        "--cleanup-shared",
        action="store_true",
        help="仅清理已迁移的逐书默认文件和旧分片，可重复执行",
    )
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, default=Path("/var/backups/oohstory-admin"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    data, digest = validate_template(args.template.resolve())
    if args.cleanup_shared:
        written = install_files([], data, digest)
        orphaned = shards = 0
        for _attempt in range(3):
            orphaned += retire_orphaned_catalog_defaults(digest)
            shards += retire_legacy_default_shards(digest)
            if not any(COVER_ROOT.glob(f"*-oohstory-default-{digest[:16]}.jpg")):
                break
            time.sleep(0.5)
        report = {
            "mode": "cleanup_shared",
            "shared_written": written,
            "orphan_defaults_retired": orphaned,
            "legacy_shards_retired": shards,
            "remaining_catalog_defaults": sum(
                1 for _ in COVER_ROOT.glob(
                    f"*-oohstory-default-{digest[:16]}.jpg"
                )
            ),
            "remaining_legacy_shards": sum(
                1 for _ in (
                    COVER_ROOT.parent / ".oohstory-default-assets"
                ).glob(f"oohstory-default-{digest}-*.jpg")
            ),
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if not report["remaining_catalog_defaults"] else 1
    runtime = MySQLLibraryRuntime()
    missing_scan: dict[str, int] = {}
    missing_mode = bool(args.missing_public)
    visual_mode = bool(args.visual_manifest)
    visual_rows: list[dict[str, Any]] = []
    if args.visual_manifest:
        manifest = load_json(args.visual_manifest.resolve())
        visual_rows = load_visual_manifest_rows(manifest)
        allowed_hashes = {str(row["sha256"]) for row in visual_rows}
        manifest_ids = sorted(int(row["catalog_id"]) for row in visual_rows)
        source_manifest = str(args.visual_manifest.resolve())
    elif args.manifest:
        manifest = load_json(args.manifest.resolve())
        allowed_hashes = {
            str(value).strip().casefold()
            for value in manifest.get("placeholder_sha256s", [])
            if len(str(value).strip()) == 64
        }
        manifest_ids = sorted(
            {int(row["catalog_id"]) for row in manifest.get("rows", [])}
        )
        source_manifest = str(args.manifest.resolve())
    elif args.sha256s:
        allowed_hashes = {
            str(value).strip().casefold() for value in (args.sha256s or [])
        }
        manifest_ids = select_ids_by_hashes(runtime, allowed_hashes)
        source_manifest = None
    else:
        allowed_hashes = set()
        manifest_ids, missing_scan = select_missing_public_ids(runtime)
        source_manifest = "missing-public-cover-scan"
    if not missing_mode and not visual_mode and (
        not allowed_hashes
        or any(
            len(value) != 64
            or not is_missing_cover_placeholder_sha256(value)
            for value in allowed_hashes
        )
    ):
        raise SystemExit("目标必须全部是已登记的缺失封面 SHA-256")
    if not manifest_ids:
        raise SystemExit("没有命中需要切换的目录行")

    current = select_rows(runtime, manifest_ids)
    visual_validation: dict[str, int] = {}
    if visual_mode:
        expected = {int(row["catalog_id"]): row for row in visual_rows}
        eligible, visual_validation = validate_visual_rows(current, expected)
    elif missing_mode:
        eligible = current
    else:
        eligible = [
            row for row in current
            if str(row.get("sha256") or "").casefold() in allowed_hashes
        ]
    report: dict[str, Any] = {
        "manifest_rows": len(manifest_ids),
        "placeholder_sha256s": (
            [] if visual_mode else sorted(allowed_hashes)
        ),
        "placeholder_sha256_count": len(allowed_hashes),
        "mode": (
            "visual_placeholder"
            if visual_mode
            else "missing_public" if missing_mode else "placeholder_sha256"
        ),
        "missing_scan": missing_scan,
        "visual_validation": visual_validation,
        "present_rows": len(current),
        "eligible": len(eligible),
        "already_changed_or_missing": len(manifest_ids) - len(eligible),
        "default_sha256": digest,
        "default_bytes": len(data),
        "apply": bool(args.apply),
    }
    if not args.apply:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = args.backup_dir / f"missing-cover-default-switch-{stamp}.json.gz"
    write_backup(
        backup_path,
        {
            "created_at": datetime.now(UTC).isoformat(),
            "source_manifest": source_manifest,
            "placeholder_sha256s": sorted(allowed_hashes),
            "default_sha256": digest,
            "rows": eligible,
        },
    )
    written = install_files(eligible, data, digest)
    retired_hashes = set(allowed_hashes) | {digest}
    sample_root = backup_path.with_suffix("").with_suffix("")
    sample_root = sample_root.parent / f"{sample_root.name}-assets"
    samples_archived = archive_placeholder_samples(
        eligible, retired_hashes, sample_root
    )
    updated = skipped = 0
    for group in batches([int(row["catalog_id"]) for row in eligible], 250):
        by_id = {int(row["catalog_id"]): row for row in eligible}
        group_rows = [by_id[catalog_id] for catalog_id in group]
        if missing_mode:
            changed, unchanged = apply_missing_batch(
                runtime, group_rows, digest, len(data)
            )
        else:
            changed, unchanged = apply_batch(
                runtime, group_rows, allowed_hashes, digest, len(data)
            )
        updated += changed
        skipped += unchanged
    runtime.invalidate_catalog_cache()
    files_retired = retire_catalog_placeholder_files(eligible, retired_hashes)
    orphan_defaults_retired = retire_orphaned_catalog_defaults(digest)
    shards_retired = retire_legacy_default_shards(digest)
    report.update(
        {
            "files_written": written,
            "files_retired": files_retired,
            "orphan_defaults_retired": orphan_defaults_retired,
            "legacy_shards_retired": shards_retired,
            "fingerprint_samples_archived": samples_archived,
            "sample_archive": str(sample_root),
            "updated": updated,
            "skipped_after_lock": skipped,
            "backup": str(backup_path),
        }
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if updated + skipped == len(eligible) else 1


if __name__ == "__main__":
    raise SystemExit(main())
