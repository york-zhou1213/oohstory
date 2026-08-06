#!/usr/bin/env python3
"""Audit MySQL 8 catalog scale readiness and explicitly apply migrations.

The default mode is strictly read-only.  Schema changes are delegated to the
ordered MySQL migration runner only when ``--apply`` is supplied.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


from project_paths import APP_ROOT  # noqa: E402
BACKEND_ROOT = APP_ROOT / "src"
MIGRATION_ROOT = APP_ROOT / "deploy" / "mysql"
MIGRATION_RUNNER = Path(__file__).resolve().with_name(
    "apply_mysql_migrations.py"
)
sys.path.insert(0, str(BACKEND_ROOT))

from oohstory_library.services.library_catalog_mysql import MySQLCatalogStore  # noqa: E402
from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
)


REQUIRED_TABLES = {
    "books",
    "book_metadata",
    "book_public_metrics",
    "book_public_metric_visitors",
    "book_public_ids",
    "catalog_facets",
    "catalog_status_counts",
    "crawl_pages",
    "crawl_state",
    "download_jobs",
    "library_clean_cover_jobs",
    "library_covers",
    "library_fanqie_cover_jobs",
    "library_postprocess_jobs",
    "library_sync_runs",
    "object_assets",
    "public_catalog_facets",
    "schema_migrations",
}
REQUIRED_BOOK_INDEXES = {
    "PRIMARY",
    "ftx_books_title_author",
    "idx_books_active_id",
    "idx_books_active_library_id",
    "idx_books_library_body_category_id",
    "idx_books_public_category_recent",
    "idx_books_public_recent",
    "idx_books_public_serialization",
    "idx_books_public_words",
    "uq_books_detail_url_hash",
    "uq_books_source_id",
}
REQUIRED_TRIGGERS = {
    "trg_books_catalog_counts_ad",
    "trg_books_catalog_counts_ai",
    "trg_books_catalog_counts_au",
    "trg_books_public_counts_ad",
    "trg_books_public_counts_ai",
    "trg_books_public_counts_au",
}


def require_mysql(
    settings: LibraryInfrastructureSettings,
) -> None:
    if settings.catalog_backend != "mysql":
        raise RuntimeError(
            "scale_catalog.py only supports MySQL; set "
            "WEBNOVEL_CATALOG_BACKEND=mysql"
        )


def local_migrations() -> dict[str, str]:
    return {
        path.name.split("_", 1)[0]: path.name
        for path in sorted(MIGRATION_ROOT.glob("[0-9][0-9][0-9]_*.sql"))
    }


def mysql_major(version: Any) -> int:
    match = re.match(r"\s*(\d+)", str(version or ""))
    return int(match.group(1)) if match else 0


def _single_value(cursor: Any, sql: str, key: str) -> int:
    cursor.execute(sql)
    row = cursor.fetchone() or {}
    return int(row.get(key) or 0)


def inspect(
    settings: LibraryInfrastructureSettings,
    pool: MySQLConnectionPool | None = None,
) -> dict[str, Any]:
    """Return a read-only readiness report for the configured MySQL schema."""
    require_mysql(settings)
    pool = pool or MySQLConnectionPool(settings)
    health = pool.health()
    migrations = local_migrations()
    with pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name AS object_name
                FROM information_schema.tables
                WHERE table_schema=%s AND table_type='BASE TABLE'
                """,
                (settings.mysql_database,),
            )
            tables = {str(row["object_name"]) for row in cursor.fetchall()}

            applied: dict[str, str] = {}
            if "schema_migrations" in tables:
                cursor.execute(
                    "SELECT version, checksum FROM schema_migrations"
                )
                applied = {
                    str(row["version"]): str(row["checksum"])
                    for row in cursor.fetchall()
                }

            cursor.execute(
                """
                SELECT DISTINCT index_name AS object_name
                FROM information_schema.statistics
                WHERE table_schema=%s AND table_name='books'
                """,
                (settings.mysql_database,),
            )
            indexes = {str(row["object_name"]) for row in cursor.fetchall()}

            cursor.execute(
                """
                SELECT trigger_name AS object_name
                FROM information_schema.triggers
                WHERE trigger_schema=%s
                """,
                (settings.mysql_database,),
            )
            triggers = {str(row["object_name"]) for row in cursor.fetchall()}

            books = (
                _single_value(
                    cursor,
                    "SELECT COUNT(*) AS count FROM books",
                    "count",
                )
                if "books" in tables
                else 0
            )
            readable = (
                _single_value(
                    cursor,
                    """
                    SELECT COALESCE(SUM(book_count), 0) AS count
                    FROM catalog_facets WHERE body_available=1
                    """,
                    "count",
                )
                if "catalog_facets" in tables
                else 0
            )

    missing_migrations = [
        name for version, name in migrations.items() if version not in applied
    ]
    missing_tables = sorted(REQUIRED_TABLES - tables)
    missing_indexes = sorted(REQUIRED_BOOK_INDEXES - indexes)
    missing_triggers = sorted(REQUIRED_TRIGGERS - triggers)
    version = str(health.get("version") or "")
    mysql8 = mysql_major(version) == 8
    ready = not (
        missing_migrations
        or missing_tables
        or missing_indexes
        or missing_triggers
        or not mysql8
    )
    return {
        "mode": "read-only",
        "ready": ready,
        "database": settings.mysql_database,
        "mysql": {
            "version": version,
            "mysql8": mysql8,
            "transaction_isolation": health.get("transaction_isolation"),
        },
        "catalog": {
            "books": books,
            "readable": readable,
        },
        "migrations": {
            "local": len(migrations),
            "applied": len(applied),
            "missing": missing_migrations,
        },
        "missing": {
            "tables": missing_tables,
            "book_indexes": missing_indexes,
            "triggers": missing_triggers,
        },
    }


def apply_migrations(*, admin_socket: bool = False) -> None:
    command = [sys.executable, str(MIGRATION_RUNNER)]
    if admin_socket:
        command.append("--admin-socket")
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "只读审计 MySQL 8 电子书库规模就绪状态；"
            "只有显式 --apply 才执行迁移。"
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="显式执行 deploy/mysql 中尚未应用的迁移",
    )
    parser.add_argument(
        "--admin-socket",
        action="store_true",
        help="把 --admin-socket 传给迁移执行器（仅与 --apply 一起使用）",
    )
    args = parser.parse_args()
    if args.admin_socket and not args.apply:
        parser.error("--admin-socket requires --apply")

    settings = LibraryInfrastructureSettings.from_env()
    require_mysql(settings)
    started = time.perf_counter()
    if args.apply:
        apply_migrations(admin_socket=bool(args.admin_socket))
    # Constructing the store here verifies that this tool remains wired to the
    # production MySQL catalog implementation.  Redis is deliberately omitted,
    # so the audit cannot populate or invalidate cache keys.
    store = MySQLCatalogStore(settings, cache_client=None)
    payload = inspect(settings, pool=store.pool)
    payload["mode"] = "applied-and-audited" if args.apply else "read-only"
    payload["elapsed_seconds"] = round(time.perf_counter() - started, 4)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
