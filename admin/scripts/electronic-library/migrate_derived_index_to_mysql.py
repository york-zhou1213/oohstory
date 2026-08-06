#!/usr/bin/env python3
"""Import the legacy derived metadata index into MySQL exactly and idempotently."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
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


def clean_json(value: Any, fallback: Any) -> str:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = fallback
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    index_path = args.index.expanduser().resolve()
    if not index_path.is_file():
        raise RuntimeError(f"metadata index not found: {index_path}")
    batch_size = min(max(int(args.batch_size), 100), 5000)
    settings = LibraryInfrastructureSettings.from_env()
    pool = MySQLConnectionPool(settings)
    source = sqlite3.connect(
        f"{index_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
        timeout=30,
    )
    source.row_factory = sqlite3.Row
    started = time.monotonic()
    processed = 0
    imported = 0
    missing = 0
    try:
        source_total = int(
            source.execute("SELECT COUNT(*) FROM library_index").fetchone()[0]
        )
        rows = source.execute(
            """
            SELECT
                catalog_id,
                source_path,
                source_bytes,
                source_mtime_ns,
                approx_word_count,
                approx_chapter_count,
                summary,
                genre_tags,
                tone_tags,
                keyword_counts,
                primary_tone_tags,
                secondary_tone_tags,
                tone_confidence,
                tone_source,
                tone_evidence,
                tone_review_status,
                tone_review_model,
                tone_reviewed_at,
                word_count,
                chapter_count,
                section_count,
                reader_index_status,
                reader_schema_version,
                reader_indexed_at,
                indexed_at
            FROM library_index
            ORDER BY catalog_id
            """
        )
        for batch in chunks(rows, batch_size):
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
                    missing += len(batch) - len(prepared)
                    if prepared:
                        cursor.executemany(
                            """
                            INSERT INTO book_metadata (
                                catalog_id,
                                source_mtime_ns,
                                reader_source_path,
                                reader_source_bytes,
                                word_count,
                                chapter_count,
                                summary,
                                genre_tags,
                                tone_tags,
                                keyword_counts,
                                primary_tone_tags,
                                secondary_tone_tags,
                                tone_confidence,
                                tone_source,
                                tone_evidence,
                                tone_review_status,
                                tone_review_model,
                                tone_reviewed_at,
                                section_count,
                                reader_index_status,
                                reader_schema_version,
                                reader_indexed_at,
                                indexed_at
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s
                            )
                            ON DUPLICATE KEY UPDATE
                                source_mtime_ns=VALUES(source_mtime_ns),
                                reader_source_path=VALUES(reader_source_path),
                                reader_source_bytes=VALUES(
                                    reader_source_bytes
                                ),
                                word_count=VALUES(word_count),
                                chapter_count=VALUES(chapter_count),
                                summary=VALUES(summary),
                                genre_tags=VALUES(genre_tags),
                                tone_tags=VALUES(tone_tags),
                                keyword_counts=VALUES(keyword_counts),
                                primary_tone_tags=VALUES(primary_tone_tags),
                                secondary_tone_tags=VALUES(secondary_tone_tags),
                                tone_confidence=VALUES(tone_confidence),
                                tone_source=VALUES(tone_source),
                                tone_evidence=VALUES(tone_evidence),
                                tone_review_status=VALUES(tone_review_status),
                                tone_review_model=VALUES(tone_review_model),
                                tone_reviewed_at=VALUES(tone_reviewed_at),
                                section_count=VALUES(section_count),
                                reader_index_status=VALUES(reader_index_status),
                                reader_schema_version=VALUES(
                                    reader_schema_version
                                ),
                                reader_indexed_at=VALUES(reader_indexed_at),
                                indexed_at=VALUES(indexed_at)
                            """,
                            [
                                (
                                    int(row["catalog_id"]),
                                    max(int(row["source_mtime_ns"] or 0), 0),
                                    str(row["source_path"] or "") or None,
                                    max(int(row["source_bytes"] or 0), 0),
                                    max(int(row["word_count"] or 0), 0),
                                    max(int(row["chapter_count"] or 0), 0),
                                    str(row["summary"] or "") or None,
                                    clean_json(row["genre_tags"], []),
                                    clean_json(row["tone_tags"], []),
                                    clean_json(row["keyword_counts"], {}),
                                    clean_json(row["primary_tone_tags"], []),
                                    clean_json(row["secondary_tone_tags"], []),
                                    min(
                                        max(float(row["tone_confidence"] or 0), 0),
                                        1,
                                    ),
                                    str(row["tone_source"] or "local")[:32],
                                    clean_json(row["tone_evidence"], {}),
                                    str(
                                        row["tone_review_status"] or "pending"
                                    )[:32],
                                    str(row["tone_review_model"] or "")[:255],
                                    parse_datetime(row["tone_reviewed_at"]),
                                    max(int(row["section_count"] or 0), 0),
                                    str(row["reader_index_status"] or "")[:32],
                                    max(
                                        int(row["reader_schema_version"] or 0),
                                        0,
                                    ),
                                    parse_datetime(row["reader_indexed_at"]),
                                    parse_datetime(row["indexed_at"]),
                                )
                                for row in prepared
                            ],
                        )
                        cursor.executemany(
                            """
                            UPDATE books
                            SET approx_word_count=%s,
                                approx_chapter_count=%s
                            WHERE id=%s
                            """,
                            [
                                (
                                    max(
                                        int(row["word_count"] or 0),
                                        int(row["approx_word_count"] or 0),
                                        0,
                                    ),
                                    max(
                                        int(row["chapter_count"] or 0),
                                        int(row["approx_chapter_count"] or 0),
                                        0,
                                    ),
                                    int(row["catalog_id"]),
                                )
                                for row in prepared
                            ],
                        )
                    imported += len(prepared)
            processed += len(batch)
            if processed % 5000 == 0:
                print(
                    json.dumps(
                        {
                            "status": "importing",
                            "processed": processed,
                            "total": source_total,
                            "imported": imported,
                            "missing": missing,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    finally:
        source.close()
    report = {
        "status": "imported" if processed == source_total else "incomplete",
        "source": str(index_path),
        "source_total": source_total,
        "processed": processed,
        "imported": imported,
        "missing_catalog_ids": missing,
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
    return 0 if report["status"] == "imported" else 2


if __name__ == "__main__":
    raise SystemExit(main())
