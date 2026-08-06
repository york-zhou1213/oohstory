from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _safe_text(value: object, limit: int) -> str:
    text = "".join(char for char in str(value) if char >= " " and char != "\x7f")
    return text[:limit]


class AuditLog:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
        os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def record(self, actor: object, action: object, target: object, result: object) -> int:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO audit_log(actor, action, target, result, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    _safe_text(actor, 128),
                    _safe_text(action, 64),
                    _safe_text(target, 255),
                    _safe_text(result, 1000),
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def list(self, limit: int = 100, offset: int = 0) -> list[dict[str, object]]:
        limit = min(max(int(limit), 1), 200)
        offset = max(int(offset), 0)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, actor, action, target, result, created_at FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]
