#!/usr/bin/env python3
"""Bulk-load a verified plot TSV into MySQL over the local admin socket."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def mysql_admin(database: str, sql: str, *, local_infile: bool = False) -> str:
    command = [
        "mysql",
        "--protocol=SOCKET",
        "--user=root",
        "--batch",
        "--skip-column-names",
        "--default-character-set=utf8mb4",
    ]
    if local_infile:
        command.append("--local-infile=1")
    if database:
        command.append(database)
    result = subprocess.run(
        command,
        input=sql.encode("utf-8"),
        capture_output=True,
        timeout=3600,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    return result.stdout.decode("utf-8", errors="replace").strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--database", default="oohstory_library")
    parser.add_argument("--expected-rows", type=int, required=True)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        raise RuntimeError(f"plot TSV not found: {input_path}")
    database = str(args.database).strip()
    if not database.replace("_", "").isalnum():
        raise ValueError("invalid MySQL database name")
    existing = int(
        mysql_admin(
            database,
            "SELECT COUNT(*) FROM plot_segments;\n",
        )
    )
    if existing and not args.replace:
        raise RuntimeError(
            f"plot_segments is not empty ({existing} rows); use --replace"
        )
    escaped_path = (
        input_path.as_posix().replace("\\", "\\\\").replace("'", "\\'")
    )
    started = time.monotonic()
    mysql_admin("", "SET GLOBAL local_infile=ON;\n")
    try:
        load_sql = ""
        if args.replace:
            load_sql += "TRUNCATE TABLE plot_segments;\n"
        load_sql += f"""
SET SESSION sql_log_bin=0;
SET SESSION unique_checks=0;
SET SESSION foreign_key_checks=0;
LOAD DATA LOCAL INFILE '{escaped_path}'
INTO TABLE plot_segments
CHARACTER SET utf8mb4
FIELDS TERMINATED BY '\\t' ESCAPED BY '\\\\'
LINES TERMINATED BY '\\n'
(id, catalog_id, source_id, location, motif_tags, motif_text, content);
SELECT COUNT(*) FROM plot_segments;
"""
        output = mysql_admin(
            database,
            load_sql,
            local_infile=True,
        )
    finally:
        mysql_admin("", "SET GLOBAL local_infile=OFF;\n")
    loaded = int(output.splitlines()[-1])
    report = {
        "status": (
            "verified" if loaded == int(args.expected_rows) else "incomplete"
        ),
        "input": str(input_path),
        "input_bytes": input_path.stat().st_size,
        "expected_rows": int(args.expected_rows),
        "loaded_rows": loaded,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "local_infile_restored": (
            mysql_admin("", "SELECT @@GLOBAL.local_infile;\n") == "0"
        ),
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
    return 0 if report["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
