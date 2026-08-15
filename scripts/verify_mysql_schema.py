#!/usr/bin/env python3
"""Verify an initialized OOH Story MySQL schema and migration ledger."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pymysql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = PROJECT_ROOT / "admin" / "deploy" / "mysql"
EXPECTED_TABLES = 29
EXPECTED_TRIGGERS = 7


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--database", default="oohstory_library")
    parser.add_argument("--user", required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    args = parser.parse_args()

    password = args.password_file.read_text(encoding="utf-8").strip()
    migrations = sorted(MIGRATION_ROOT.glob("[0-9][0-9][0-9]_*.sql"))
    expected = {
        path.name.split("_", 1)[0]: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in migrations
    }
    connection = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        database=args.database,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version, checksum FROM schema_migrations")
            applied = {str(version): str(checksum) for version, checksum in cursor.fetchall()}
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=%s AND table_type='BASE TABLE'",
                (args.database,),
            )
            table_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.triggers "
                "WHERE trigger_schema=%s",
                (args.database,),
            )
            trigger_count = int(cursor.fetchone()[0])
    finally:
        connection.close()

    if applied != expected:
        raise SystemExit(
            f"migration ledger mismatch: expected {len(expected)}, got {len(applied)}"
        )
    if table_count != EXPECTED_TABLES:
        raise SystemExit(
            f"table count mismatch: expected {EXPECTED_TABLES}, got {table_count}"
        )
    if trigger_count != EXPECTED_TRIGGERS:
        raise SystemExit(
            f"trigger count mismatch: expected {EXPECTED_TRIGGERS}, got {trigger_count}"
        )
    print(
        f"MySQL schema verified: {len(applied)} migrations, "
        f"{table_count} tables, {trigger_count} triggers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
