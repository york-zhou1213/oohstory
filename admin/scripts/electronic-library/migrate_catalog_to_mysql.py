#!/usr/bin/env python3
"""Idempotently migrate the live SQLite catalog into MySQL 8.0.

The source database is opened read-only. IDs are preserved so reader indexes,
deconstruction tasks, covers, and frontend URLs continue to reference the same
catalog records. The command can be rerun after a failed batch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


from project_paths import APP_ROOT  # noqa: E402
BACKEND_ROOT = APP_ROOT / "src"
DEFAULT_SOURCE = APP_ROOT / "electronic-library" / "catalog.sqlite3"
DEFAULT_MEMBERSHIP = (
    APP_ROOT
    / "electronic-library"
    / "全局索引"
    / "library_memberships.sqlite3"
)
DEFAULT_SCHEMA = APP_ROOT / "deploy" / "mysql" / "001_library_schema.sql"
sys.path.insert(0, str(BACKEND_ROOT))

from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
)


DIGEST_FIELDS = (
    "id",
    "source_id",
    "detail_url",
    "title",
    "author",
    "category",
    "status",
    "attempts",
    "bytes",
    "sha256",
    "book_status",
    "identity_key",
    "title_key",
    "library_id",
)


def normalize_identity(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if character.isalnum())


def normalize_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
    return None


def stable_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


class CatalogDigest:
    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self.rows = 0

    def update(self, row: dict[str, Any]) -> None:
        payload = "\x1f".join(stable_value(row.get(key)) for key in DIGEST_FIELDS)
        self._digest.update(payload.encode("utf-8", errors="surrogatepass"))
        self._digest.update(b"\n")
        self.rows += 1

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def sqlite_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_xinfo({table})")
    }


def membership_overrides(path: Path) -> dict[int, str]:
    if not path.is_file():
        return {}
    with sqlite_connection(path) as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "catalog_library_overrides" not in tables:
            return {}
        return {
            int(row["catalog_id"]): str(row["target_library"])
            for row in connection.execute(
                "SELECT catalog_id, target_library "
                "FROM catalog_library_overrides"
            )
        }


def source_books(
    connection: sqlite3.Connection,
    overrides: dict[int, str],
    batch_size: int,
) -> Iterator[list[dict[str, Any]]]:
    columns = table_columns(connection, "books")
    last_id = 0
    while True:
        rows = connection.execute(
            "SELECT * FROM books WHERE id>? ORDER BY id LIMIT ?",
            (last_id, batch_size),
        ).fetchall()
        if not rows:
            return
        batch: list[dict[str, Any]] = []
        for sqlite_row in rows:
            raw = dict(sqlite_row)
            catalog_id = int(raw["id"])
            source_id = str(raw.get("source_id") or "").strip()
            inferred_library = (
                "fanqie"
                if source_id.casefold().startswith(
                    ("fanqie-", "xbiquge-", "ixdzs-", "shubaow-")
                )
                else "local"
            )
            library_id = str(
                raw.get("library_id")
                if "library_id" in columns
                else ""
            ).strip()
            library_id = overrides.get(
                catalog_id,
                library_id if library_id in {"local", "fanqie"} else inferred_library,
            )
            title = str(raw.get("title") or "").strip()
            author = str(raw.get("author") or "").strip()
            identity_key = str(raw.get("identity_key") or "").strip()
            if not identity_key:
                identity_key = (
                    f"{normalize_identity(title)}\x1f{normalize_identity(author)}"
                )
            title_key = str(raw.get("title_key") or "").strip()
            if not title_key:
                title_key = normalize_identity(title)
            batch.append(
                {
                    "id": catalog_id,
                    "source_id": source_id or None,
                    "detail_url": str(raw.get("detail_url") or "").strip(),
                    "title": title,
                    "author": author,
                    "category": str(raw.get("category") or "未分类").strip()
                    or "未分类",
                    "expected_size": str(raw.get("expected_size") or "").strip()
                    or None,
                    "download_page_url": str(
                        raw.get("download_page_url") or ""
                    ).strip()
                    or None,
                    "file_url": str(raw.get("file_url") or "").strip() or None,
                    "legacy_output_path": str(
                        raw.get("output_path") or ""
                    ).strip()
                    or None,
                    "body_object_key": None,
                    "cover_object_key": None,
                    "status": str(raw.get("status") or "discovered"),
                    "attempts": max(int(raw.get("attempts") or 0), 0),
                    "bytes": max(int(raw.get("bytes") or 0), 0),
                    "sha256": str(raw.get("sha256") or "").strip() or None,
                    "last_error": str(raw.get("last_error") or "")[:2000] or None,
                    "discovered_at": normalize_datetime(raw.get("discovered_at")),
                    "updated_at": normalize_datetime(raw.get("updated_at")),
                    "book_status": str(raw.get("book_status") or "已完结"),
                    "identity_key": identity_key or None,
                    "title_key": title_key or None,
                    "library_id": library_id,
                }
            )
        yield batch
        last_id = int(rows[-1]["id"])


def apply_schema(settings: LibraryInfrastructureSettings, schema: Path) -> None:
    environment = dict(os.environ)
    environment["MYSQL_PWD"] = settings.mysql_password
    result = subprocess.run(
        [
            "mysql",
            "--protocol=TCP",
            "--host",
            settings.mysql_host,
            "--port",
            str(settings.mysql_port),
            "--user",
            settings.mysql_user,
            "--database",
            settings.mysql_database,
            "--default-character-set=utf8mb4",
            "--binary-mode",
        ],
        input=schema.read_bytes(),
        env=environment,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "MySQL schema failed: "
            + result.stderr.decode("utf-8", errors="replace")[-2000:]
        )


BOOK_COLUMNS = (
    "id",
    "source_id",
    "detail_url",
    "title",
    "author",
    "category",
    "expected_size",
    "download_page_url",
    "file_url",
    "legacy_output_path",
    "body_object_key",
    "cover_object_key",
    "status",
    "attempts",
    "bytes",
    "sha256",
    "last_error",
    "discovered_at",
    "updated_at",
    "book_status",
    "identity_key",
    "title_key",
    "library_id",
)


def mysql_book_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(column) for column in BOOK_COLUMNS)


def upsert_books(
    pool: MySQLConnectionPool,
    rows: Sequence[dict[str, Any]],
) -> None:
    columns = ", ".join(BOOK_COLUMNS)
    placeholders = ", ".join(["%s"] * len(BOOK_COLUMNS))
    updates = ", ".join(
        (
            f"{column}=COALESCE(VALUES({column}), {column})"
            if column in {"body_object_key", "cover_object_key"}
            else f"{column}=VALUES({column})"
        )
        for column in BOOK_COLUMNS
        if column != "id"
    )
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                f"""
                INSERT INTO books ({columns})
                VALUES ({placeholders})
                ON DUPLICATE KEY UPDATE {updates}, row_version=row_version+1
                """,
                [mysql_book_values(row) for row in rows],
            )


def migrate_auxiliary_tables(
    source_connection: sqlite3.Connection,
    pool: MySQLConnectionPool,
) -> dict[str, int]:
    tables = {
        str(row["name"])
        for row in source_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    counts: dict[str, int] = {}
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            if "catalog_sections" in tables:
                rows = [
                    dict(row)
                    for row in source_connection.execute(
                        "SELECT * FROM catalog_sections"
                    )
                ]
                cursor.executemany(
                    """
                    INSERT INTO catalog_sections (
                        source_name, section_key, path, label, total_pages,
                        status, attempts, last_error, updated_at
                    ) VALUES (
                        'txt80', %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    ON DUPLICATE KEY UPDATE
                        path=VALUES(path),
                        label=VALUES(label),
                        total_pages=VALUES(total_pages),
                        status=VALUES(status),
                        attempts=VALUES(attempts),
                        last_error=VALUES(last_error),
                        updated_at=VALUES(updated_at)
                    """,
                    [
                        (
                            row["section_key"],
                            row["path"],
                            row.get("label"),
                            max(int(row.get("total_pages") or 0), 0),
                            row.get("status") or "pending",
                            max(int(row.get("attempts") or 0), 0),
                            row.get("last_error"),
                            normalize_datetime(row.get("updated_at")),
                        )
                        for row in rows
                    ],
                )
                counts["catalog_sections"] = len(rows)
            page_rows: list[tuple[Any, ...]] = []
            if "listing_pages" in tables:
                page_rows.extend(
                    (
                        "txt80",
                        str(row["section_key"]),
                        max(int(row["page"]), 0),
                        row["url"],
                        row["status"],
                        max(int(row["attempts"] or 0), 0),
                        row["book_count"],
                        row["last_error"],
                        normalize_datetime(row["updated_at"]),
                    )
                    for row in source_connection.execute(
                        "SELECT * FROM listing_pages"
                    )
                )
            # ``listing_pages`` is the authoritative crawler state exposed by
            # the production API.  The legacy ``pages`` table often contains
            # the same URLs with stale statuses; importing both lets the URL
            # uniqueness key overwrite the authoritative rows nondeterministically.
            if "pages" in tables and "listing_pages" not in tables:
                page_rows.extend(
                    (
                        "txt80-legacy",
                        "",
                        max(int(row["page"]), 0),
                        row["url"],
                        row["status"],
                        max(int(row["attempts"] or 0), 0),
                        row["book_count"],
                        row["last_error"],
                        normalize_datetime(row["updated_at"]),
                    )
                    for row in source_connection.execute("SELECT * FROM pages")
                )
            if page_rows:
                cursor.executemany(
                    """
                    INSERT INTO crawl_pages (
                        source_name, section_key, page_number, url,
                        status, attempts, book_count, last_error, updated_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON DUPLICATE KEY UPDATE
                        url=VALUES(url),
                        status=VALUES(status),
                        attempts=VALUES(attempts),
                        book_count=VALUES(book_count),
                        last_error=VALUES(last_error),
                        updated_at=VALUES(updated_at)
                    """,
                    page_rows,
                )
            counts["crawl_pages"] = len(page_rows)
            if "crawl_state" in tables:
                rows = [
                    dict(row)
                    for row in source_connection.execute(
                        "SELECT * FROM crawl_state"
                    )
                ]
                cursor.executemany(
                    """
                    INSERT INTO crawl_state (
                        source_name, state_key, state_value, updated_at
                    ) VALUES ('txt80', %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        state_value=VALUES(state_value),
                        updated_at=VALUES(updated_at)
                    """,
                    [
                        (
                            row["key"],
                            json.dumps(row["value"], ensure_ascii=False),
                            normalize_datetime(row.get("updated_at")),
                        )
                        for row in rows
                    ],
                )
                counts["crawl_state"] = len(rows)
            if "duplicate_books" in tables:
                rows = [
                    dict(row)
                    for row in source_connection.execute(
                        "SELECT * FROM duplicate_books"
                    )
                ]
                cursor.executemany(
                    """
                    INSERT INTO duplicate_books (
                        duplicate_book_id, duplicate_source_id,
                        kept_book_id, kept_source_id, title, author,
                        removed_path, removed_bytes, removed_sha256,
                        duplicate_metrics, kept_metrics, reason, removed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    ON DUPLICATE KEY UPDATE
                        kept_book_id=VALUES(kept_book_id),
                        reason=VALUES(reason),
                        removed_at=VALUES(removed_at)
                    """,
                    [
                        (
                            int(row["duplicate_book_id"]),
                            row.get("duplicate_source_id"),
                            int(row["kept_book_id"]),
                            row.get("kept_source_id"),
                            row.get("title"),
                            row.get("author"),
                            row.get("removed_path"),
                            max(int(row.get("removed_bytes") or 0), 0),
                            row.get("removed_sha256"),
                            (
                                row.get("duplicate_metrics")
                                if _valid_json(row.get("duplicate_metrics"))
                                else None
                            ),
                            (
                                row.get("kept_metrics")
                                if _valid_json(row.get("kept_metrics"))
                                else None
                            ),
                            row["reason"],
                            normalize_datetime(row["removed_at"]),
                        )
                        for row in rows
                    ],
                )
                counts["duplicate_books"] = len(rows)
            if "library_sync_runs" in tables:
                rows = [
                    dict(row)
                    for row in source_connection.execute(
                        "SELECT * FROM library_sync_runs"
                    )
                ]
                cursor.executemany(
                    """
                    INSERT INTO library_sync_runs (
                        id, started_at, finished_at, status, summary
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        finished_at=VALUES(finished_at),
                        status=VALUES(status),
                        summary=VALUES(summary)
                    """,
                    [
                        (
                            int(row["id"]),
                            normalize_datetime(row["started_at"]),
                            normalize_datetime(row.get("finished_at")),
                            row["status"],
                            json.dumps(
                                row.get("summary") or "",
                                ensure_ascii=False,
                            ),
                        )
                        for row in rows
                    ],
                )
                counts["library_sync_runs"] = len(rows)
    return counts


def _valid_json(value: Any) -> bool:
    if value is None:
        return False
    try:
        json.loads(str(value))
        return True
    except (TypeError, ValueError):
        return False


def rebuild_facets_and_jobs(pool: MySQLConnectionPool) -> dict[str, int]:
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM catalog_facets")
            cursor.execute(
                """
                INSERT INTO catalog_facets (
                    library_id, body_available, category, book_count
                )
                SELECT
                    library_id,
                    body_available,
                    COALESCE(NULLIF(category, ''), '未分类'),
                    COUNT(*)
                FROM books
                WHERE status <> 'duplicate'
                GROUP BY
                    library_id,
                    body_available,
                    COALESCE(NULLIF(category, ''), '未分类')
                """
            )
            facet_rows = int(cursor.rowcount)
            cursor.execute("DELETE FROM catalog_status_counts")
            cursor.execute(
                """
                INSERT INTO catalog_status_counts (
                    library_id, status, book_count
                )
                SELECT library_id, status, COUNT(*)
                FROM books
                GROUP BY library_id, status
                """
            )
            status_rows = int(cursor.rowcount)
            cursor.execute(
                """
                INSERT INTO download_jobs (
                    catalog_id, source_id, source_name, status,
                    priority, attempts, max_attempts, available_at, payload
                )
                SELECT
                    id,
                    source_id,
                    CASE
                        WHEN source_id LIKE 'xbiquge-%%' THEN 'xbiquge'
                        WHEN source_id LIKE 'ixdzs-%%' THEN 'ixdzs'
                        ELSE 'other'
                    END,
                    'pending',
                    CASE
                        WHEN status='discovered' THEN 50
                        ELSE 100
                    END,
                    attempts,
                    8,
                    UTC_TIMESTAMP(6),
                    JSON_OBJECT(
                        'detail_url', detail_url,
                        'title', title,
                        'author', author,
                        'category', category,
                        'book_status', book_status
                    )
                FROM books
                WHERE status IN ('discovered', 'failed')
                  AND attempts < 8
                  AND (
                    source_id LIKE 'xbiquge-%%'
                    OR source_id LIKE 'ixdzs-%%'
                  )
                ON DUPLICATE KEY UPDATE
                    source_id=VALUES(source_id),
                    source_name=VALUES(source_name),
                    attempts=VALUES(attempts),
                    payload=VALUES(payload),
                    status=IF(
                        download_jobs.status IN ('done', 'downloading'),
                        download_jobs.status,
                        'pending'
                    )
                """
            )
            job_rows = int(cursor.rowcount)
    return {
        "facet_rows": facet_rows,
        "status_rows": status_rows,
        "download_job_changes": job_rows,
    }


def mysql_digest(pool: MySQLConnectionPool, batch_size: int) -> CatalogDigest:
    digest = CatalogDigest()
    last_id = 0
    fields = ", ".join(DIGEST_FIELDS)
    while True:
        with pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT {fields}
                    FROM books
                    WHERE id>%s
                    ORDER BY id
                    LIMIT %s
                    """,
                    (last_id, batch_size),
                )
                rows = list(cursor.fetchall())
        if not rows:
            return digest
        for row in rows:
            digest.update(row)
        last_id = int(rows[-1]["id"])


def source_digest(
    source: Path,
    membership: Path,
    batch_size: int,
) -> CatalogDigest:
    overrides = membership_overrides(membership)
    digest = CatalogDigest()
    with sqlite_connection(source) as connection:
        for batch in source_books(connection, overrides, batch_size):
            for row in batch:
                digest.update(row)
    return digest


def migrate(
    *,
    source: Path,
    membership: Path,
    schema: Path,
    batch_size: int,
    apply_schema_first: bool,
) -> dict[str, Any]:
    settings = LibraryInfrastructureSettings.from_env()
    if not settings.mysql_password:
        raise RuntimeError("MySQL application password is not configured")
    if apply_schema_first:
        apply_schema(settings, schema)
    pool = MySQLConnectionPool(settings)
    started = time.monotonic()
    migrated = 0
    source_hash = CatalogDigest()
    overrides = membership_overrides(membership)
    with sqlite_connection(source) as connection:
        for batch in source_books(connection, overrides, batch_size):
            upsert_books(pool, batch)
            for row in batch:
                source_hash.update(row)
            migrated += len(batch)
            print(
                json.dumps(
                    {
                        "status": "migrating",
                        "rows": migrated,
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        auxiliary = migrate_auxiliary_tables(connection, pool)
    derived = rebuild_facets_and_jobs(pool)
    target_hash = mysql_digest(pool, batch_size)
    report = {
        "status": (
            "verified"
            if source_hash.rows == target_hash.rows
            and source_hash.hexdigest == target_hash.hexdigest
            else "mismatch"
        ),
        "source": str(source.resolve()),
        "membership": str(membership.resolve()),
        "source_rows": source_hash.rows,
        "target_rows": target_hash.rows,
        "source_sha256": source_hash.hexdigest,
        "target_sha256": target_hash.hexdigest,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "auxiliary": auxiliary,
        **derived,
    }
    if report["status"] != "verified":
        raise RuntimeError(json.dumps(report, ensure_ascii=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--membership", type=Path, default=DEFAULT_MEMBERSHIP)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--apply-schema", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.batch_size < 100 or args.batch_size > 10000:
        parser.error("--batch-size must be between 100 and 10000")
    if args.verify_only:
        settings = LibraryInfrastructureSettings.from_env()
        pool = MySQLConnectionPool(settings)
        source = source_digest(args.source, args.membership, args.batch_size)
        target = mysql_digest(pool, args.batch_size)
        report = {
            "status": (
                "verified"
                if source.rows == target.rows
                and source.hexdigest == target.hexdigest
                else "mismatch"
            ),
            "source_rows": source.rows,
            "target_rows": target.rows,
            "source_sha256": source.hexdigest,
            "target_sha256": target.hexdigest,
        }
    else:
        report = migrate(
            source=args.source,
            membership=args.membership,
            schema=args.schema,
            batch_size=args.batch_size,
            apply_schema_first=args.apply_schema,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
