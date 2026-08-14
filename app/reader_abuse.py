"""Persistent reader-abuse controls shared by every Reader worker."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReaderGuardDecision:
    reason: str
    retry_after: int


class ReaderAbuseGuard:
    """Track unique chapter traversal and honeypot hits in SQLite."""

    def __init__(
        self,
        database_path: Path,
        *,
        recent_limit: int = 60,
        recent_window_seconds: int = 600,
        daily_limit: int = 500,
        trap_limit: int = 2,
        trap_window_seconds: int = 86400,
        trap_ban_seconds: int = 86400,
    ) -> None:
        self.database_path = database_path
        self.recent_limit = recent_limit
        self.recent_window_seconds = recent_window_seconds
        self.daily_limit = daily_limit
        self.trap_limit = trap_limit
        self.trap_window_seconds = trap_window_seconds
        self.trap_ban_seconds = trap_ban_seconds
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=2.0)
        connection.execute("PRAGMA busy_timeout=2000")
        if not self._schema_ready:
            with self._schema_lock:
                if not self._schema_ready:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute("PRAGMA synchronous=NORMAL")
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS chapter_reads (
                            day TEXT NOT NULL,
                            ip TEXT NOT NULL,
                            resource TEXT NOT NULL,
                            last_seen INTEGER NOT NULL,
                            PRIMARY KEY(day, ip, resource)
                        );
                        CREATE INDEX IF NOT EXISTS idx_chapter_reads_recent
                            ON chapter_reads(ip, last_seen);
                        CREATE TABLE IF NOT EXISTS reader_risk (
                            ip TEXT PRIMARY KEY,
                            trap_hits INTEGER NOT NULL DEFAULT 0,
                            first_trap INTEGER NOT NULL DEFAULT 0,
                            last_trap INTEGER NOT NULL DEFAULT 0,
                            ban_until INTEGER NOT NULL DEFAULT 0,
                            reason TEXT NOT NULL DEFAULT ''
                        );
                        """
                    )
                    self._schema_ready = True
        return connection

    @staticmethod
    def _day(now: int) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(now))

    @staticmethod
    def _seconds_until_midnight(now: int) -> int:
        current = datetime.fromtimestamp(now)
        midnight = datetime.combine(
            current.date() + timedelta(days=1),
            datetime.min.time(),
        )
        return max(1, int(midnight.timestamp() - now))

    def check_chapter(
        self,
        ip: str,
        resource: str,
        *,
        now: int | None = None,
    ) -> ReaderGuardDecision | None:
        timestamp = int(time.time() if now is None else now)
        day = self._day(timestamp)
        recent_cutoff = timestamp - self.recent_window_seconds
        try:
            connection = self._connect()
            with connection:
                risk = connection.execute(
                    "SELECT ban_until,reason FROM reader_risk WHERE ip=?",
                    (ip,),
                ).fetchone()
                if risk and int(risk[0]) > timestamp:
                    return ReaderGuardDecision(
                        str(risk[1] or "reader abuse"),
                        max(1, int(risk[0]) - timestamp),
                    )

                connection.execute(
                    "DELETE FROM chapter_reads WHERE day<?",
                    ((datetime.fromtimestamp(timestamp).date() - timedelta(days=1)).isoformat(),),
                )
                connection.execute(
                    """
                    INSERT INTO chapter_reads(day,ip,resource,last_seen)
                    VALUES(?,?,?,?)
                    ON CONFLICT(day,ip,resource)
                    DO UPDATE SET last_seen=excluded.last_seen
                    """,
                    (day, ip, resource, timestamp),
                )
                recent_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chapter_reads WHERE ip=? AND last_seen>=?",
                        (ip, recent_cutoff),
                    ).fetchone()[0]
                )
                daily_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chapter_reads WHERE day=? AND ip=?",
                        (day, ip),
                    ).fetchone()[0]
                )

                if recent_count > self.recent_limit:
                    retry_after = self.recent_window_seconds
                    connection.execute(
                        """
                        INSERT INTO reader_risk(ip,ban_until,reason)
                        VALUES(?,?,?)
                        ON CONFLICT(ip) DO UPDATE SET
                            ban_until=MAX(reader_risk.ban_until,excluded.ban_until),
                            reason=excluded.reason
                        """,
                        (ip, timestamp + retry_after, "chapter traversal velocity"),
                    )
                    return ReaderGuardDecision(
                        "chapter traversal velocity",
                        retry_after,
                    )

                if daily_count > self.daily_limit:
                    retry_after = self._seconds_until_midnight(timestamp)
                    connection.execute(
                        """
                        INSERT INTO reader_risk(ip,ban_until,reason)
                        VALUES(?,?,?)
                        ON CONFLICT(ip) DO UPDATE SET
                            ban_until=MAX(reader_risk.ban_until,excluded.ban_until),
                            reason=excluded.reason
                        """,
                        (ip, timestamp + retry_after, "daily chapter limit"),
                    )
                    return ReaderGuardDecision("daily chapter limit", retry_after)
            return None
        except sqlite3.Error:
            # Reading must remain available if the abuse database has a local
            # operational fault. Nginx limits and explicit IP blocks remain.
            LOGGER.exception("reader abuse database check failed")
            return None
        finally:
            if "connection" in locals():
                connection.close()

    def record_trap(
        self,
        ip: str,
        *,
        now: int | None = None,
    ) -> ReaderGuardDecision | None:
        timestamp = int(time.time() if now is None else now)
        try:
            connection = self._connect()
            with connection:
                row = connection.execute(
                    "SELECT trap_hits,first_trap,ban_until FROM reader_risk WHERE ip=?",
                    (ip,),
                ).fetchone()
                if row and int(row[2]) > timestamp:
                    return ReaderGuardDecision(
                        "honeypot",
                        max(1, int(row[2]) - timestamp),
                    )
                if row and timestamp - int(row[1]) <= self.trap_window_seconds:
                    hits = int(row[0]) + 1
                    first_trap = int(row[1])
                else:
                    hits = 1
                    first_trap = timestamp
                ban_until = timestamp + self.trap_ban_seconds if hits >= self.trap_limit else 0
                connection.execute(
                    """
                    INSERT INTO reader_risk(
                        ip,trap_hits,first_trap,last_trap,ban_until,reason
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(ip) DO UPDATE SET
                        trap_hits=excluded.trap_hits,
                        first_trap=excluded.first_trap,
                        last_trap=excluded.last_trap,
                        ban_until=excluded.ban_until,
                        reason=excluded.reason
                    """,
                    (ip, hits, first_trap, timestamp, ban_until, "honeypot"),
                )
                if ban_until:
                    return ReaderGuardDecision("honeypot", self.trap_ban_seconds)
            return None
        except sqlite3.Error:
            LOGGER.exception("reader honeypot database update failed")
            return None
        finally:
            if "connection" in locals():
                connection.close()


class ReaderProbeSigner:
    """Mint short-lived, IP-bound reader honeypot URLs."""

    def __init__(self, secret_path: Path) -> None:
        self.secret_path = secret_path
        self._secret: bytes | None = None
        self._lock = threading.Lock()

    def _load_secret(self) -> bytes:
        if self._secret is not None:
            return self._secret
        with self._lock:
            if self._secret is not None:
                return self._secret
            self.secret_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(
                    self.secret_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(secrets.token_bytes(32))
                    output.flush()
                    os.fsync(output.fileno())
            secret = self.secret_path.read_bytes()
            if len(secret) < 32:
                raise OSError("reader probe secret is too short")
            self._secret = secret
            return secret

    def _signature(
        self,
        ip: str,
        book_id: str,
        chapter_id: int,
        hour: int,
    ) -> str:
        message = f"{ip}:{book_id}:{chapter_id}:{hour}".encode("utf-8")
        return hmac.new(self._load_secret(), message, hashlib.sha256).hexdigest()[:32]

    def mint(
        self,
        ip: str,
        book_id: str,
        chapter_id: int,
        *,
        now: int | None = None,
    ) -> str:
        timestamp = int(time.time() if now is None else now)
        return self._signature(ip, book_id, chapter_id, timestamp // 3600)

    def validate(
        self,
        ip: str,
        book_id: str,
        chapter_id: int,
        token: str,
        *,
        now: int | None = None,
    ) -> bool:
        timestamp = int(time.time() if now is None else now)
        current_hour = timestamp // 3600
        return any(
            hmac.compare_digest(
                token,
                self._signature(ip, book_id, chapter_id, hour),
            )
            for hour in (current_hour, current_hour - 1)
        )
