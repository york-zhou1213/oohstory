#!/usr/bin/env python3
"""Copy legacy SQLite paragraph comments into MySQL + mounted objects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.comments import CommentStore  # noqa: E402
from app.library import LibraryRepository  # noqa: E402
from app.settings import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    settings = load_settings()
    database = (args.database or settings.user_database_path).resolve()
    comments = CommentStore(LibraryRepository(settings), settings.comment_object_root)
    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    created = skipped = reactions = 0
    try:
        rows = source.execute(
            "SELECT id,user_id,book_id,chapter_id,paragraph_index,paragraph_key,"
            "paragraph_excerpt,content,status,created_at FROM paragraph_comments "
            "ORDER BY created_at,id"
        ).fetchall()
        for row in rows:
            _identifier, inserted = comments.import_legacy(
                comment_id=row["id"], user_id=row["user_id"],
                book_id=row["book_id"], scope="paragraph",
                chapter_id=int(row["chapter_id"]),
                paragraph_index=int(row["paragraph_index"]),
                paragraph_key=row["paragraph_key"],
                paragraph_excerpt=row["paragraph_excerpt"],
                content=row["content"], status=row["status"],
                created_at=row["created_at"],
            )
            created += int(inserted)
            skipped += int(not inserted)
        for row in source.execute(
            "SELECT comment_id,user_id,like_count,created_at "
            "FROM paragraph_comment_thanks ORDER BY created_at,comment_id,user_id"
        ):
            comments.import_reaction(
                row["comment_id"], row["user_id"], int(row["like_count"]), row["created_at"]
            )
            reactions += 1
    finally:
        source.close()
    report = {
        "schema": "oohstory-comment-migration-v1",
        "source_database": str(database),
        "comment_root": str(settings.comment_object_root),
        "source_comments": created + skipped,
        "created_comments": created,
        "skipped_comments": skipped,
        "source_reactions": reactions,
    }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
