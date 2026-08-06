#!/usr/bin/env python3
"""Rollback rejected non-AI cover transformations and retain every file."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path


from project_paths import APP_ROOT  # noqa: E402
ROOT = (APP_ROOT / "electronic-library" / "txt80" / "封面").resolve()
DB = ROOT / "index.sqlite3"
METHODS = {
    "local-bottom-band-crop-v1",
    "local-watermark-inpaint-v1",
}


def main() -> None:
    now = datetime.now().isoformat(timespec="seconds")
    restored = skipped = 0
    with sqlite3.connect(DB, timeout=60) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT j.catalog_id,j.original_filename,j.replacement_filename,
                   c.filename
            FROM clean_cover_jobs j
            JOIN covers c ON c.catalog_id=j.catalog_id
            WHERE j.verification_source IN (?,?)
            """,
            tuple(sorted(METHODS)),
        ).fetchall()
        for catalog_id, original_name, replacement_name, current_name in rows:
            if (
                not original_name
                or Path(original_name).name != original_name
                or not replacement_name
                or Path(replacement_name).name != replacement_name
            ):
                skipped += 1
                continue
            original = (ROOT / original_name).resolve()
            replacement = (ROOT / replacement_name).resolve()
            if (
                original.parent != ROOT
                or replacement.parent != ROOT
                or not original.is_file()
                or current_name != replacement_name
            ):
                skipped += 1
                continue
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            conn.execute(
                """
                UPDATE covers SET filename=?,sha256=?,updated_at=?
                WHERE catalog_id=? AND filename=?
                """,
                (original_name, digest, now, catalog_id, replacement_name),
            )
            conn.execute(
                """
                UPDATE clean_cover_jobs
                SET status='pending',original_filename=?,
                    replacement_url=NULL,replacement_filename=NULL,
                    verification_source=NULL,
                    last_error='已撤销非 AI 修补方案，等待 AI 等比例重绘',
                    updated_at=?
                WHERE catalog_id=? AND verification_source IN (?,?)
                """,
                (
                    original_name,
                    now,
                    catalog_id,
                    *tuple(sorted(METHODS)),
                ),
            )
            restored += 1
        conn.commit()
    print(
        f"restored={restored} skipped={skipped} "
        "rejected_files_deleted=0"
    )


if __name__ == "__main__":
    main()
