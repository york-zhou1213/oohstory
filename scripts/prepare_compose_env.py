#!/usr/bin/env python3
"""Prepare ignored local secrets and data directories for Docker Compose."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADMIN_SRC = PROJECT_ROOT / "admin" / "src"
sys.path.insert(0, str(ADMIN_SRC))

from oohstory_admin.security import hash_password  # noqa: E402
from init_local_library import initialize  # noqa: E402


def _token() -> str:
    return secrets.token_urlsafe(36)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--reader-port", type=int, default=8091)
    parser.add_argument("--admin-port", type=int, default=8092)
    parser.add_argument("--mysql-port", type=int, default=13306)
    parser.add_argument("--no-show-password", action="store_true")
    parser.add_argument(
        "--prompt-admin-password",
        action="store_true",
        help="prompt instead of generating a one-time admin password",
    )
    args = parser.parse_args()
    for name, port in (
        ("reader", args.reader_port),
        ("admin", args.admin_port),
        ("mysql", args.mysql_port),
    ):
        if not 1024 <= port <= 65535:
            raise SystemExit(f"{name} port must be between 1024 and 65535")

    env_path = PROJECT_ROOT / ".env.compose"
    if env_path.exists():
        raise SystemExit(
            "Compose environment already exists; remove .env.compose only "
            "when intentionally rotating all local secrets."
        )

    if args.prompt_admin_password:
        password = getpass.getpass("Admin password (12+ characters): ")
        confirmation = getpass.getpass("Confirm admin password: ")
        if password != confirmation:
            raise SystemExit("passwords do not match")
    else:
        password = _token()

    password_hash = hash_password(password)
    database_secrets = tuple(_token() for _ in range(4))
    try:
        env_path.write_text(
            "\n".join(
                (
                    "COMPOSE_PROJECT_NAME=oohstory",
                    f"OOHSTORY_ADMIN_USERNAME={args.admin_username}",
                    f"OOHSTORY_ADMIN_PASSWORD_HASH='{password_hash}'",
                    f"OOHSTORY_ADMIN_SESSION_SECRET={_token()}",
                    f"OOHSTORY_MYSQL_ROOT_PASSWORD={database_secrets[0]}",
                    f"OOHSTORY_MYSQL_WRITER_PASSWORD={database_secrets[1]}",
                    f"OOHSTORY_MYSQL_ADMIN_READER_PASSWORD={database_secrets[2]}",
                    f"OOHSTORY_MYSQL_PUBLIC_READER_PASSWORD={database_secrets[3]}",
                    f"OOHSTORY_PUBLIC_ORIGIN=http://localhost:{args.reader_port}",
                    f"OOHSTORY_READER_PUBLISH_PORT={args.reader_port}",
                    f"OOHSTORY_ADMIN_PUBLISH_PORT={args.admin_port}",
                    f"OOHSTORY_MYSQL_PUBLISH_PORT={args.mysql_port}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        env_path.chmod(0o600)
        initialize(PROJECT_ROOT / "data" / "library")
    except Exception:
        if env_path.exists():
            env_path.unlink()
        raise

    print("Prepared mode-0600 .env.compose and data/library.")
    if not args.prompt_admin_password and not args.no_show_password:
        print(f"Generated admin password (shown once): {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
