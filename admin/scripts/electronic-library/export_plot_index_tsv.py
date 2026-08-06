#!/usr/bin/env python3
"""Export the final legacy plot index for MySQL LOAD DATA.

This is an explicit one-time migration tool. Production never reads the
legacy SQLite database.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


def mysql_escape(value: Any) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\b", "\\b")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("\x1a", "\\Z")
    )


def normalized_tags(value: Any) -> str:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    tags = [str(item).strip()[:120] for item in parsed if str(item).strip()]
    return json.dumps(tags, ensure_ascii=False, separators=(",", ":"))


def metadata_fingerprints(
    connection: sqlite3.Connection,
) -> dict[int, tuple[str, int, int, int]]:
    return {
        int(row["catalog_id"]): (
            str(row["source_id"]),
            int(row["source_bytes"] or 0),
            int(row["source_mtime_ns"] or 0),
            int(row["segment_count"] or 0),
        )
        for row in connection.execute(
            """
            SELECT catalog_id, source_id, source_bytes,
                   source_mtime_ns, segment_count
            FROM plot_index_meta
            """
        )
    }


def selected_rows(
    final: sqlite3.Connection,
    base: sqlite3.Connection | None,
    changed_catalog_ids: set[int],
    final_catalog_ids: set[int],
) -> Iterable[sqlite3.Row]:
    if base is None:
        yield from final.execute(
            """
            SELECT catalog_id, source_id, location, motif_tags, content
            FROM plot_segments
            ORDER BY id
            """
        )
        return
    for row in base.execute(
        """
        SELECT catalog_id, source_id, location, motif_tags, content
        FROM plot_segments
        ORDER BY id
        """
    ):
        catalog_id = int(row["catalog_id"])
        if (
            catalog_id in final_catalog_ids
            and catalog_id not in changed_catalog_ids
        ):
            yield row
    changed = sorted(changed_catalog_ids)
    for offset in range(0, len(changed), 500):
        batch = changed[offset : offset + 500]
        placeholders = ", ".join("?" for _ in batch)
        yield from final.execute(
            f"""
            SELECT catalog_id, source_id, location, motif_tags, content
            FROM plot_segments
            WHERE catalog_id IN ({placeholders})
            ORDER BY catalog_id, id
            """,
            batch,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument(
        "--base-index",
        type=Path,
        help=(
            "Faster older index used for books whose final metadata "
            "fingerprint is identical"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    source_path = args.index.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    if not source_path.is_file():
        raise RuntimeError(f"plot index not found: {source_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".partial")
    started = time.monotonic()
    connection = sqlite3.connect(
        f"{source_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    base_connection: sqlite3.Connection | None = None
    final_meta = metadata_fingerprints(connection)
    changed_catalog_ids = set(final_meta)
    if args.base_index:
        base_path = args.base_index.expanduser().resolve()
        if not base_path.is_file():
            raise RuntimeError(f"base plot index not found: {base_path}")
        base_connection = sqlite3.connect(
            f"{base_path.as_uri()}?mode=ro&immutable=1",
            uri=True,
            timeout=30,
        )
        base_connection.row_factory = sqlite3.Row
        base_meta = metadata_fingerprints(base_connection)
        changed_catalog_ids = {
            catalog_id
            for catalog_id, fingerprint in final_meta.items()
            if base_meta.get(catalog_id) != fingerprint
        }
    total = int(
        sum(fingerprint[3] for fingerprint in final_meta.values())
    )
    exported = 0
    base_rows = 0
    final_rows = 0
    try:
        rows = selected_rows(
            connection,
            base_connection,
            changed_catalog_ids,
            set(final_meta),
        )
        with temporary_path.open(
            "w",
            encoding="utf-8",
            newline="",
            buffering=16 * 1024 * 1024,
        ) as output:
            for row in rows:
                tags = normalized_tags(row["motif_tags"])
                exported += 1
                if int(row["catalog_id"]) in changed_catalog_ids:
                    final_rows += 1
                else:
                    base_rows += 1
                fields = (
                    exported,
                    int(row["catalog_id"]),
                    mysql_escape(row["source_id"]),
                    mysql_escape(row["location"]),
                    mysql_escape(tags),
                    mysql_escape(" ".join(json.loads(tags))[:1024]),
                    mysql_escape(row["content"]),
                )
                output.write(
                    f"{fields[0]}\t{fields[1]}\t"
                    + "\t".join(str(field) for field in fields[2:])
                    + "\n"
                )
                if exported % 25000 == 0:
                    print(
                        json.dumps(
                            {
                                "status": "exporting",
                                "exported": exported,
                                "total": total,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            output.flush()
            os.fsync(output.fileno())
    finally:
        if base_connection is not None:
            base_connection.close()
        connection.close()
    temporary_path.replace(output_path)
    report = {
        "status": "verified" if exported == total else "incomplete",
        "source": str(source_path),
        "base_source": (
            str(args.base_index.expanduser().resolve())
            if args.base_index
            else ""
        ),
        "output": str(output_path),
        "source_rows": total,
        "exported_rows": exported,
        "base_rows": base_rows,
        "final_rows": final_rows,
        "changed_catalog_ids": len(changed_catalog_ids),
        "output_bytes": output_path.stat().st_size,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report_temp = args.report.with_suffix(args.report.suffix + ".tmp")
        report_temp.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report_temp.replace(args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if exported == total else 2


if __name__ == "__main__":
    raise SystemExit(main())
