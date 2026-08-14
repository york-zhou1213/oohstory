from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import sqlite3

import pymysql


def main() -> None:
    account_path = Path(
        os.getenv("OOHSTORY_ACCOUNT_DATABASE", "/var/lib/oohstory-reader/accounts.sqlite3")
    )
    password_path = Path(os.environ["OOHSTORY_MYSQL_PASSWORD_FILE"])
    with sqlite3.connect(account_path) as accounts:
        sessions = accounts.execute(
            "SELECT token_hash,user_id FROM user_sessions "
            "WHERE revoked_at IS NULL AND expires_at>datetime('now')"
        ).fetchall()
    connection = pymysql.connect(
        host=os.getenv("OOHSTORY_MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("OOHSTORY_MYSQL_PORT", "3306")),
        user=os.environ["OOHSTORY_MYSQL_USER"],
        password=password_path.read_text(encoding="utf-8").strip(),
        database=os.environ["OOHSTORY_MYSQL_DATABASE"],
        autocommit=False,
    )
    changed = 0
    try:
        with connection.cursor() as cursor:
            for token_hash, user_id in sessions:
                stable = sha256(f"user:{user_id}".encode("utf-8")).digest()
                cursor.execute(
                    "UPDATE audiobook_sessions SET owner_hash=%s "
                    "WHERE owner_hash=%s AND cancelled=0 AND expires_at>UTC_TIMESTAMP(6)",
                    (stable, bytes(token_hash)),
                )
                changed += int(cursor.rowcount)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(f"migrated_active_audiobook_sessions={changed}")


if __name__ == "__main__":
    main()
