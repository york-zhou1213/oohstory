#!/usr/bin/env python3
"""Normalize malformed trailing ``[type`` labels in the MySQL catalog."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_paths import APP_ROOT


sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library import install_legacy_environment_aliases  # noqa: E402
from oohstory_library.services.library_catalog import (  # noqa: E402
    MALFORMED_TRAILING_TYPE_LABEL,
    normalize_catalog_title,
)
from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
)
from oohstory_library.services.library_cache import (  # noqa: E402
    LibraryCacheSettings,
    RedisHotCache,
)
from oohstory_library.services.library_identity_claims import (  # noqa: E402
    book_identity_hash,
    book_identity_key,
    normalize_book_identity,
)


ENV_FILE = Path("/etc/oohstory-admin/library-infrastructure.env")
LOCK_NAME = "oohstory:normalize-catalog-titles:v1"
RELATED_TITLE_TABLES = (
    "authorized_source_updates",
    "library_covers",
    "library_clean_cover_jobs",
    "library_fanqie_cover_jobs",
)


class MigrationError(RuntimeError):
    pass


def load_settings() -> LibraryInfrastructureSettings:
    if not ENV_FILE.is_file() or ENV_FILE.is_symlink():
        raise MigrationError(f"生产环境文件无效: {ENV_FILE}")
    for line_number, raw_line in enumerate(
        ENV_FILE.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise MigrationError(f"环境文件第 {line_number} 行格式无效")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key.startswith(("OOHSTORY_LIBRARY_", "WEBNOVEL_")):
            raise MigrationError(f"环境文件第 {line_number} 行变量名无效")
        values = shlex.split(raw_value, comments=True, posix=True)
        if len(values) > 1:
            raise MigrationError(f"环境文件第 {line_number} 行值格式无效")
        os.environ[key] = values[0] if values else ""
    install_legacy_environment_aliases()
    settings = LibraryInfrastructureSettings.from_env()
    if settings.catalog_backend != "mysql":
        raise MigrationError("生产 catalog backend 不是 mysql")
    return replace(
        settings,
        mysql_read_timeout=max(settings.mysql_read_timeout, 600),
        mysql_write_timeout=max(settings.mysql_write_timeout, 600),
    )


def candidate_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    old_title = str(row.get("title") or "")
    new_title = normalize_catalog_title(old_title)
    if not new_title or new_title == old_title:
        return None
    return {
        **row,
        "old_title": old_title,
        "new_title": new_title,
        "new_identity_key": book_identity_key(new_title, row.get("author")),
        "new_title_key": normalize_book_identity(new_title),
    }


def rejected_empty_title(row: dict[str, Any]) -> bool:
    """Return true only when the malformed suffix is the entire title."""

    raw_title = str(row.get("title") or "").strip()
    return bool(MALFORMED_TRAILING_TYPE_LABEL.fullmatch(raw_title)) and (
        normalize_catalog_title(raw_title) == raw_title
    )


def related_candidate_from_row(
    table: str,
    row: dict[str, Any],
) -> dict[str, Any] | None:
    old_title = str(row.get("title") or "")
    new_title = normalize_catalog_title(old_title)
    if not new_title or new_title == old_title:
        return None
    return {
        "table": table,
        "catalog_id": int(row["catalog_id"]),
        "old_title": old_title,
        "new_title": new_title,
        "row": row,
    }


def scan(pool: MySQLConnectionPool) -> dict[str, Any]:
    with pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id,source_id,status,title,author,category,library_id,
                       identity_key,title_key,row_version,updated_at
                FROM books
                WHERE INSTR(title, '[') > 0
                ORDER BY id
                """
            )
            raw_rows = [dict(row) for row in cursor.fetchall()]
            candidates = [
                candidate
                for row in raw_rows
                if (candidate := candidate_from_row(row)) is not None
            ]
            rejected = [
                {
                    "catalog_id": int(row["id"]),
                    "title": str(row.get("title") or ""),
                    "reason": "删除标签后书名为空，保留原值",
                }
                for row in raw_rows
                if rejected_empty_title(row)
            ]
            collision_count = 0
            for item in candidates:
                cursor.execute(
                    """
                    SELECT catalog_id
                    FROM global_book_identity_claims
                    WHERE identity_hash=%s
                    LIMIT 1
                    """,
                    (book_identity_hash(item["new_identity_key"]),),
                )
                claim = cursor.fetchone()
                if claim and int(claim.get("catalog_id") or 0) != int(item["id"]):
                    collision_count += 1
            related_candidates: list[dict[str, Any]] = []
            for table in RELATED_TITLE_TABLES:
                cursor.execute(
                    f"SELECT * FROM {table} WHERE INSTR(title, '[') > 0 "
                    "ORDER BY catalog_id"
                )
                related_candidates.extend(
                    candidate
                    for row in cursor.fetchall()
                    if (
                        candidate := related_candidate_from_row(
                            table, dict(row)
                        )
                    )
                    is not None
                )
    return {
        "scanned_titles_with_bracket": len(raw_rows),
        "candidates": candidates,
        "rejected": rejected,
        "identity_collisions": collision_count,
        "related_candidates": related_candidates,
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
            json.dump(json_safe(payload), writer, ensure_ascii=False, indent=2)
            writer.write("\n")
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def backup_rows(
    pool: MySQLConnectionPool,
    scan_result: dict[str, Any],
    backup_dir: Path,
) -> Path:
    if backup_dir.exists() or backup_dir.is_symlink():
        raise MigrationError(f"备份目录已存在，拒绝覆盖: {backup_dir}")
    backup_dir.mkdir(parents=True, mode=0o700)
    ids = [int(item["id"]) for item in scan_result["candidates"]]
    related: dict[str, list[dict[str, Any]]] = {}
    claims: list[dict[str, Any]] = []
    with pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            for start in range(0, len(ids), 500):
                batch = ids[start : start + 500]
                placeholders = ",".join(["%s"] * len(batch))
                cursor.execute(
                    "SELECT * FROM global_book_identity_claims "
                    f"WHERE catalog_id IN ({placeholders}) ORDER BY catalog_id",
                    batch,
                )
                claims.extend(dict(row) for row in cursor.fetchall())
                for table in RELATED_TITLE_TABLES:
                    cursor.execute(
                        f"SELECT * FROM {table} "
                        f"WHERE catalog_id IN ({placeholders}) ORDER BY catalog_id",
                        batch,
                    )
                    related.setdefault(table, []).extend(
                        dict(row) for row in cursor.fetchall()
                    )
    manifest = {
        "schema": "webnovel.catalog-title-normalization.v1",
        "status": "backed_up",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scan": {
            "scanned_titles_with_bracket": scan_result[
                "scanned_titles_with_bracket"
            ],
            "candidate_count": len(scan_result["candidates"]),
            "identity_collisions": scan_result["identity_collisions"],
            "rejected": scan_result["rejected"],
        },
        "books": scan_result["candidates"],
        "global_book_identity_claims": claims,
        "related_title_rows": related,
        "standalone_related_title_rows": scan_result[
            "related_candidates"
        ],
    }
    manifest_path = backup_dir / "manifest.json"
    atomic_json(manifest_path, manifest)
    return manifest_path


def lock_value(row: Any, key: str) -> int:
    if isinstance(row, dict):
        return int(row.get(key) or 0)
    return int(row[0] or 0) if row else 0


def apply(
    pool: MySQLConnectionPool,
    scan_result: dict[str, Any],
    manifest_path: Path,
) -> dict[str, Any]:
    candidates = scan_result["candidates"]
    changed = 0
    collisions = 0
    related_changed = 0
    with pool.connection() as connection:
        lock_acquired = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (LOCK_NAME,))
                if lock_value(cursor.fetchone(), "acquired") != 1:
                    raise MigrationError("无法取得书名迁移锁")
                lock_acquired = True
                connection.begin()
                for item in candidates:
                    catalog_id = int(item["id"])
                    cursor.execute(
                        "SELECT title,author,row_version FROM books "
                        "WHERE id=%s FOR UPDATE",
                        (catalog_id,),
                    )
                    current = cursor.fetchone()
                    if not current:
                        raise MigrationError(f"书目在迁移中消失: {catalog_id}")
                    current_candidate = candidate_from_row(
                        {"id": catalog_id, **dict(current)}
                    )
                    if (
                        current_candidate is None
                        or current_candidate["old_title"] != item["old_title"]
                        or current_candidate["new_title"] != item["new_title"]
                    ):
                        raise MigrationError(f"书目在扫描后发生变化: {catalog_id}")
                    cursor.execute(
                        "DELETE FROM global_book_identity_claims "
                        "WHERE catalog_id=%s",
                        (catalog_id,),
                    )
                    cursor.execute(
                        """
                        INSERT IGNORE INTO global_book_identity_claims (
                            identity_hash,identity_key,catalog_id
                        ) VALUES (%s,%s,%s)
                        """,
                        (
                            book_identity_hash(item["new_identity_key"]),
                            item["new_identity_key"],
                            catalog_id,
                        ),
                    )
                    if int(cursor.rowcount or 0) == 0:
                        collisions += 1
                    cursor.execute(
                        """
                        UPDATE books
                        SET title=%s,identity_key=%s,title_key=%s,
                            row_version=row_version+1,
                            updated_at=UTC_TIMESTAMP(6)
                        WHERE id=%s AND title=%s
                        """,
                        (
                            item["new_title"],
                            item["new_identity_key"],
                            item["new_title_key"],
                            catalog_id,
                            item["old_title"],
                        ),
                    )
                    if int(cursor.rowcount or 0) != 1:
                        raise MigrationError(f"书名条件更新失败: {catalog_id}")
                    for table in RELATED_TITLE_TABLES:
                        cursor.execute(
                            f"UPDATE {table} SET title=%s WHERE catalog_id=%s",
                            (item["new_title"], catalog_id),
                        )
                    changed += 1
                for item in scan_result["related_candidates"]:
                    cursor.execute(
                        f"UPDATE {item['table']} SET title=%s "
                        "WHERE catalog_id=%s AND title=%s",
                        (
                            item["new_title"],
                            item["catalog_id"],
                            item["old_title"],
                        ),
                    )
                    related_changed += int(cursor.rowcount or 0)
                connection.commit()
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        finally:
            if lock_acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT RELEASE_LOCK(%s) AS released", (LOCK_NAME,)
                        )
                except Exception:
                    pass

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "complete"
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["changed_books"] = changed
    manifest["identity_collisions"] = collisions
    manifest["standalone_related_titles_changed"] = related_changed
    atomic_json(manifest_path, manifest)
    RedisHotCache(
        LibraryCacheSettings.from_infrastructure(pool.settings)
    ).invalidate("catalog", "book", "cover")
    return {
        "mode": "apply",
        "changed_books": changed,
        "identity_collisions": collisions,
        "standalone_related_titles_changed": related_changed,
        "manifest": str(manifest_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        pool = MySQLConnectionPool(settings)
        result = scan(pool)
        summary = {
            "mode": "dry-run" if not args.apply else "apply",
            "scanned_titles_with_bracket": result[
                "scanned_titles_with_bracket"
            ],
            "candidate_count": len(result["candidates"]),
            "identity_collisions": result["identity_collisions"],
            "related_candidate_count": len(result["related_candidates"]),
            "rejected": result["rejected"],
        }
        if args.apply:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_dir = (
                args.backup_dir
                or Path("/var/backups/oohstory-admin")
                / f"catalog-title-normalization-{stamp}"
            ).expanduser().absolute()
            manifest_path = backup_rows(pool, result, backup_dir)
            summary.update(apply(pool, result, manifest_path))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "refused", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
