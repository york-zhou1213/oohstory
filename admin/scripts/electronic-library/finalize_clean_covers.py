#!/usr/bin/env python3
"""Retire superseded covers after each verified AI replacement commits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
from project_paths import APP_ROOT  # noqa: E402
LIBRARY_ROOT = APP_ROOT / "electronic-library" / "txt80"
COVER_ROOT = (LIBRARY_ROOT / "封面").resolve()
INDEX_PATH = Path(
    os.getenv(
        "WEBNOVEL_COVER_INDEX_PATH",
        str(LIBRARY_ROOT / "全局索引" / "cover_index.sqlite3"),
    )
).expanduser().resolve()
sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.library_database import LibraryInfrastructureSettings  # noqa: E402
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime  # noqa: E402


def stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_cover_path(filename: str) -> Path:
    if not filename or Path(filename).name != filename:
        raise ValueError("封面文件名不安全")
    path = (COVER_ROOT / filename).resolve()
    if path.parent != COVER_ROOT:
        raise ValueError("封面文件超出限定目录")
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cover_content_type(filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix)
    if content_type is None:
        raise ValueError("AI 替换封面格式不受支持")
    return content_type


def retire_mysql_cover(
    runtime: MySQLLibraryRuntime,
    row: dict[str, object],
) -> dict[str, object]:
    """Promote the AI asset, then unlink its zero-reference predecessor."""

    catalog_id = int(row["catalog_id"])
    original = str(row.get("original_filename") or "")
    replacement = str(row.get("replacement_filename") or "")
    replacement_path = safe_cover_path(replacement)
    if not replacement_path.is_file():
        raise FileNotFoundError("AI 替换封面不存在")
    replacement_bytes = replacement_path.stat().st_size
    if replacement_bytes < 8 * 1024:
        raise ValueError("AI 替换封面文件大小异常")
    replacement_sha256 = file_sha256(replacement_path)
    expected_sha256 = str(row.get("replacement_sha256") or "")
    if expected_sha256 and replacement_sha256 != expected_sha256:
        raise ValueError("AI 替换封面哈希不一致")
    if not runtime.promote_clean_cover_asset(
        catalog_id=catalog_id,
        original_filename=original,
        replacement_filename=replacement,
        replacement_sha256=replacement_sha256,
        bytes_count=replacement_bytes,
        content_type=cover_content_type(replacement),
    ):
        raise RuntimeError("AI 替换封面指针状态已变化，拒绝清理原图")

    if not original or original == replacement:
        marked = runtime.mark_clean_cover_deleted(
            catalog_id=catalog_id,
            original_filename=original,
            replacement_filename=replacement,
        )
        return {
            "catalog_id": catalog_id,
            "status": "no_superseded_original",
            "marked": marked,
        }

    original_path = safe_cover_path(original)
    references = runtime.clean_cover_original_references(
        filename=original,
        catalog_id=catalog_id,
    )
    if any(references.values()):
        return {
            "catalog_id": catalog_id,
            "status": "retained_referenced",
            "references": references,
        }

    retiring_name = f".retiring-{catalog_id}-{original}"
    retiring_path = safe_cover_path(retiring_name)
    if original_path.is_file():
        os.replace(original_path, retiring_path)
        removed_status = "deleted"
    elif retiring_path.is_file():
        removed_status = "deleted_recovered_staging"
    else:
        removed_status = "already_missing"

    # Recheck after the atomic rename closes the small check/unlink race. If
    # a writer reintroduced the old key, restore the file instead of deleting.
    references = runtime.clean_cover_original_references(
        filename=original,
        catalog_id=catalog_id,
    )
    if any(references.values()):
        if retiring_path.is_file() and not original_path.exists():
            os.replace(retiring_path, original_path)
        return {
            "catalog_id": catalog_id,
            "status": "restored_referenced",
            "references": references,
        }
    retiring_path.unlink(missing_ok=True)
    if not runtime.mark_clean_cover_deleted(
        catalog_id=catalog_id,
        original_filename=original,
        replacement_filename=replacement,
    ):
        raise RuntimeError("原图已清理，但删除状态写入失败；等待探针补记")
    return {
        "catalog_id": catalog_id,
        "status": removed_status,
        "bytes": replacement_bytes,
    }


def status(conn: sqlite3.Connection) -> dict[str, int | bool]:
    txt80_total = conn.execute(
        """
        SELECT count(*) FROM covers
        WHERE detail_url LIKE 'https://www.txt80.cc/%'
        """
    ).fetchone()[0]
    txt80_not_ready = conn.execute(
        """
        SELECT count(*) FROM covers
        WHERE detail_url LIKE 'https://www.txt80.cc/%' AND status!='done'
        """
    ).fetchone()[0]
    job_total = conn.execute(
        "SELECT count(*) FROM clean_cover_jobs"
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT count(*) FROM clean_cover_jobs WHERE status!='done'"
    ).fetchone()[0]
    invalid_current = conn.execute(
        """
        SELECT count(*) FROM clean_cover_jobs j
        LEFT JOIN covers c ON c.catalog_id=j.catalog_id
        WHERE j.status='done' AND (
          j.replacement_filename IS NULL
          OR c.filename!=j.replacement_filename
        )
        """
    ).fetchone()[0]
    ready = bool(
        txt80_total
        and txt80_not_ready == 0
        and job_total == txt80_total
        and pending == 0
        and invalid_current == 0
    )
    return {
        "txt80_total": txt80_total,
        "txt80_not_ready": txt80_not_ready,
        "job_total": job_total,
        "pending_or_failed": pending,
        "invalid_current": invalid_current,
        "ready": ready,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="全量无水印封面完成后清理被替换的旧封面"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="逐本执行已验证 AI 替换的旧封面回收；默认只检查",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="单次最多检查的已完成任务数（1..5000）",
    )
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 5000:
        parser.error("--limit 必须在 1..5000")
    if LibraryInfrastructureSettings.from_env().catalog_backend == "mysql":
        runtime = MySQLLibraryRuntime()
        report = runtime.clean_cover_status()
        rows = runtime.clean_cover_deletion_rows(limit=int(args.limit))
        report["eligible"] = len(rows)
        if not args.apply:
            print(json.dumps(report, ensure_ascii=False))
            return 0
        outcomes: dict[str, int] = {}
        errors: list[dict[str, object]] = []
        for row in rows:
            try:
                outcome = retire_mysql_cover(runtime, row)
                key = str(outcome["status"])
                outcomes[key] = outcomes.get(key, 0) + 1
            except Exception as exc:
                errors.append(
                    {
                        "catalog_id": int(row["catalog_id"]),
                        "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    }
                )
        report.update(
            {
                "outcomes": outcomes,
                "failed": len(errors),
                "errors": errors[:20],
            }
        )
        print(json.dumps(report, ensure_ascii=False))
        return 1 if errors else 0
    if not INDEX_PATH.exists():
        print(json.dumps({"ready": False, "reason": "index_missing"}))
        return 0

    with sqlite3.connect(INDEX_PATH, timeout=30) as conn:
        columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(clean_cover_jobs)"
            )
        }
        if "original_deleted_at" not in columns:
            conn.execute(
                "ALTER TABLE clean_cover_jobs ADD COLUMN original_deleted_at TEXT"
            )
            conn.commit()
        report = status(conn)
        if not args.apply or not report["ready"]:
            print(json.dumps(report, ensure_ascii=False))
            return 0

        rows = conn.execute(
            """
            SELECT catalog_id,original_filename,replacement_filename
            FROM clean_cover_jobs
            WHERE original_deleted_at IS NULL
            """
        ).fetchall()
        deleted = missing = retained = 0
        for catalog_id, original, replacement in rows:
            if not original or original == replacement:
                retained += 1
            else:
                path = safe_cover_path(str(original))
                if path.exists():
                    path.unlink()
                    deleted += 1
                else:
                    missing += 1
            conn.execute(
                """
                UPDATE clean_cover_jobs SET original_deleted_at=?,updated_at=?
                WHERE catalog_id=?
                """,
                (stamp(), stamp(), catalog_id),
            )
        conn.commit()
        report.update(
            {
                "deleted": deleted,
                "already_missing": missing,
                "retained_current": retained,
            }
        )
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
