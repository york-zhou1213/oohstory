#!/usr/bin/env python3
"""Import the legacy SQLite plot index into MySQL in bounded batches."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


from project_paths import APP_ROOT  # noqa: E402
sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
)


DEFAULT_INDEX = (
    APP_ROOT
    / "electronic-library"
    / "txt80"
    / "全局索引"
    / "electronic_library_index.sqlite3"
)


def chunks(rows: Iterable[sqlite3.Row], size: int) -> Iterable[list[sqlite3.Row]]:
    batch: list[sqlite3.Row] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def clean_tags(value: Any) -> tuple[str, str]:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    tags = [str(item).strip()[:120] for item in parsed if str(item).strip()]
    return (
        json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
        " ".join(tags)[:1024],
    )


def import_segment_batch(
    pool: MySQLConnectionPool,
    batch: list[sqlite3.Row],
) -> tuple[int, int]:
    ids = [int(row["id"]) for row in batch]
    placeholders = ", ".join(["%s"] * len(ids))
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id
                FROM plot_segments
                WHERE id IN ({placeholders})
                """,
                ids,
            )
            existing = {int(row["id"]) for row in cursor.fetchall()}
            prepared = []
            for row in batch:
                if int(row["id"]) in existing:
                    continue
                motif_tags, motif_text = clean_tags(row["motif_tags"])
                prepared.append(
                    (
                        int(row["id"]),
                        int(row["catalog_id"]),
                        str(row["source_id"])[:255],
                        str(row["location"] or "")[:512],
                        motif_tags,
                        motif_text,
                        str(row["content"] or ""),
                    )
                )
            if prepared:
                cursor.executemany(
                    """
                    INSERT INTO plot_segments (
                        id, catalog_id, source_id, location,
                        motif_tags, motif_text, content
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    prepared,
                )
    return len(batch), len(prepared)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--after-id",
        type=int,
        default=0,
        help=(
            "Resume after a previously verified contiguous source ID. "
            "Final source/target counts are still verified."
        ),
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    index_path = args.index.expanduser().resolve()
    if not index_path.is_file():
        raise RuntimeError(f"plot index not found: {index_path}")
    batch_size = min(max(int(args.batch_size), 100), 5000)
    workers = min(max(int(args.workers), 1), 8)
    settings = LibraryInfrastructureSettings.from_env()
    pool = MySQLConnectionPool(settings)
    source = sqlite3.connect(
        f"{index_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
        timeout=30,
    )
    source.row_factory = sqlite3.Row
    started = time.monotonic()
    imported_meta = 0
    imported_segments = 0
    after_id = max(int(args.after_id), 0)
    processed_segments = int(
        source.execute(
            "SELECT COUNT(*) FROM plot_segments WHERE id<=?",
            (after_id,),
        ).fetchone()[0]
    )
    missing_meta = 0
    try:
        source_meta = int(
            source.execute("SELECT COUNT(*) FROM plot_index_meta").fetchone()[0]
        )
        source_segments = int(
            source.execute("SELECT COUNT(*) FROM plot_segments").fetchone()[0]
        )
        meta_rows = source.execute(
            """
            SELECT source_id, catalog_id, source_bytes, source_mtime_ns,
                   segment_count, indexed_at
            FROM plot_index_meta
            ORDER BY catalog_id
            """
        )
        for batch in chunks(meta_rows, batch_size):
            ids = sorted({int(row["catalog_id"]) for row in batch})
            placeholders = ", ".join(["%s"] * len(ids))
            with pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT id FROM books WHERE id IN ({placeholders})",
                        ids,
                    )
                    existing = {int(row["id"]) for row in cursor.fetchall()}
                    prepared = [
                        row for row in batch if int(row["catalog_id"]) in existing
                    ]
                    missing_meta += len(batch) - len(prepared)
                    if prepared:
                        cursor.executemany(
                            """
                            INSERT INTO plot_index_meta (
                                catalog_id, source_id, source_bytes,
                                source_mtime_ns, segment_count, indexed_at
                            ) VALUES (%s, %s, %s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE
                                source_id=VALUES(source_id),
                                source_bytes=VALUES(source_bytes),
                                source_mtime_ns=VALUES(source_mtime_ns),
                                segment_count=VALUES(segment_count),
                                indexed_at=VALUES(indexed_at)
                            """,
                            [
                                (
                                    int(row["catalog_id"]),
                                    str(row["source_id"])[:255],
                                    max(int(row["source_bytes"] or 0), 0),
                                    max(int(row["source_mtime_ns"] or 0), 0),
                                    max(int(row["segment_count"] or 0), 0),
                                    parse_datetime(row["indexed_at"]),
                                )
                                for row in prepared
                            ],
                        )
                    imported_meta += len(prepared)

        segment_rows = source.execute(
            """
            SELECT id, source_id, catalog_id, location, motif_tags, content
            FROM plot_segments
            WHERE id>?
            ORDER BY id
            """,
            (after_id,),
        )
        pending: set[Future[tuple[int, int]]] = set()

        def collect_completed(*, drain: bool = False) -> None:
            nonlocal processed_segments, imported_segments, pending
            if not pending:
                return
            completed, remaining = wait(
                pending,
                return_when=(
                    FIRST_COMPLETED if not drain else "ALL_COMPLETED"
                ),
            )
            pending = set(remaining)
            for future in completed:
                processed, inserted = future.result()
                processed_segments += processed
                imported_segments += inserted
            if processed_segments % 25000 < batch_size * workers:
                print(
                    json.dumps(
                        {
                            "status": "importing",
                            "processed": processed_segments,
                            "inserted": imported_segments,
                            "total": source_segments,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for batch in chunks(segment_rows, batch_size):
                pending.add(
                    executor.submit(import_segment_batch, pool, batch)
                )
                if len(pending) >= workers * 2:
                    collect_completed()
            collect_completed(drain=True)
    finally:
        source.close()

    with pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS count FROM plot_index_meta")
            target_meta = int(cursor.fetchone()["count"])
            cursor.execute("SELECT COUNT(*) AS count FROM plot_segments")
            target_segments = int(cursor.fetchone()["count"])
            cursor.execute(
                "SELECT COALESCE(SUM(segment_count), 0) AS count "
                "FROM plot_index_meta"
            )
            declared_segments = int(cursor.fetchone()["count"])
    complete = (
        missing_meta == 0
        and target_meta == source_meta
        and target_segments == source_segments
        and declared_segments == source_segments
    )
    report = {
        "status": "verified" if complete else "incomplete",
        "source": str(index_path),
        "source_meta": source_meta,
        "target_meta": target_meta,
        "source_segments": source_segments,
        "target_segments": target_segments,
        "declared_segments": declared_segments,
        "inserted_segments": imported_segments,
        "processed_segments": processed_segments,
        "missing_catalog_ids": missing_meta,
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
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
