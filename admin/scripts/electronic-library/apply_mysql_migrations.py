#!/usr/bin/env python3
"""Apply ordered MySQL schema migrations exactly once."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path


from project_paths import APP_ROOT  # noqa: E402
sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
)


MIGRATION_ROOT = APP_ROOT / "deploy" / "mysql"


def apply_sql(
    settings: LibraryInfrastructureSettings,
    path: Path,
    *,
    admin_socket: bool = False,
    timeout_seconds: int = 3600,
) -> None:
    environment = dict(os.environ)
    if admin_socket:
        command = [
            "mysql",
            "--protocol=SOCKET",
            "--user=root",
            "--database",
            settings.mysql_database,
        ]
        environment.pop("MYSQL_PWD", None)
    else:
        command = [
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
        ]
        environment["MYSQL_PWD"] = settings.mysql_password
    result = subprocess.run(
        [*command, "--default-character-set=utf8mb4", "--binary-mode"],
        input=path.read_bytes(),
        env=environment,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"{path.name}: "
            + result.stderr.decode("utf-8", errors="replace")[-2000:]
        )


def admin_sql(
    settings: LibraryInfrastructureSettings,
    sql: str,
    *,
    timeout_seconds: int = 60,
) -> str:
    """Execute ledger SQL through the same local root socket as migration DDL."""

    command = [
        "mysql",
        "--protocol=SOCKET",
        "--user=root",
        "--database",
        settings.mysql_database,
        "--default-character-set=utf8mb4",
        "--batch",
        "--raw",
        "--skip-column-names",
        "--binary-mode",
    ]
    result = subprocess.run(
        command,
        input=sql.encode("utf-8"),
        env={key: value for key, value in os.environ.items() if key != "MYSQL_PWD"},
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "MySQL admin-socket operation failed: "
            + result.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    return result.stdout.decode("utf-8", errors="replace")


def load_applied_migrations(
    settings: LibraryInfrastructureSettings,
    *,
    admin_socket: bool,
) -> tuple[dict[str, str], MySQLConnectionPool | None]:
    ledger_sql = """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(64) NOT NULL PRIMARY KEY,
            applied_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            checksum CHAR(64) NOT NULL,
            description VARCHAR(255) NOT NULL
        ) ENGINE=InnoDB;
    """
    if admin_socket:
        output = admin_sql(
            settings,
            ledger_sql
            + "SELECT CONCAT(version, CHAR(9), checksum) "
            "FROM schema_migrations ORDER BY version;",
        )
        applied: dict[str, str] = {}
        for line in output.splitlines():
            version, separator, checksum = line.partition("\t")
            if separator:
                applied[version] = checksum
        return applied, None

    pool = MySQLConnectionPool(settings)
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(ledger_sql)
            cursor.execute("SELECT version, checksum FROM schema_migrations")
            applied = {
                str(row["version"]): str(row["checksum"])
                for row in cursor.fetchall()
            }
    return applied, pool


def record_migration(
    settings: LibraryInfrastructureSettings,
    pool: MySQLConnectionPool | None,
    *,
    admin_socket: bool,
    version: str,
    checksum: str,
    description: str,
) -> None:
    if admin_socket:
        safe_description = description.replace("'", "''")
        admin_sql(
            settings,
            "INSERT INTO schema_migrations (version, checksum, description) "
            f"VALUES ('{version}', '{checksum}', '{safe_description}');",
        )
        return
    if pool is None:
        raise RuntimeError("application MySQL pool is unavailable")
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO schema_migrations (
                    version, checksum, description
                ) VALUES (%s, %s, %s)
                """,
                (version, checksum, description),
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--adopt-through",
        default="",
        help=(
            "Record already-applied migrations through this numeric prefix; "
            "used only when introducing the migration ledger"
        ),
    )
    parser.add_argument(
        "--admin-socket",
        action="store_true",
        help=(
            "Apply migration SQL as local root over the Unix socket; required "
            "for trigger DDL while binary logging is enabled"
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=3600,
        help=(
            "Maximum runtime for one migration (default: 3600 seconds); "
            "large FULLTEXT indexes can exceed ten minutes"
        ),
    )
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    settings = LibraryInfrastructureSettings.from_env()
    migrations = sorted(MIGRATION_ROOT.glob("[0-9][0-9][0-9]_*.sql"))
    if not migrations:
        raise RuntimeError("no MySQL migrations found")
    applied, pool = load_applied_migrations(
        settings,
        admin_socket=bool(args.admin_socket),
    )
    for path in migrations:
        version = path.name.split("_", 1)[0]
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if version in applied:
            if applied[version] != checksum:
                raise RuntimeError(
                    f"migration {version} checksum changed after application"
                )
            print(f"skip {path.name}")
            continue
        if args.adopt_through and version <= args.adopt_through:
            action = "adopt"
        else:
            apply_sql(
                settings,
                path,
                admin_socket=bool(args.admin_socket),
                timeout_seconds=int(args.timeout_seconds),
            )
            action = "apply"
        record_migration(
            settings,
            pool,
            admin_socket=bool(args.admin_socket),
            version=version,
            checksum=checksum,
            description=path.stem,
        )
        print(f"{action} {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
