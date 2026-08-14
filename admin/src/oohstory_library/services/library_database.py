"""MySQL and operational-Redis primitives for the electronic library.

MySQL is durable. The Redis client in this module is reserved for reconstructible
Streams/locks/leases; the independently configured eviction cache lives in
``library_cache``.
"""

from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

import contextlib
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pymysql
import redis
from pymysql.cursors import DictCursor

from oohstory_library import library_env, library_env_name


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OBJECT_ROOT = APP_ROOT / "electronic-library"


def _read_secret(value: str, file_value: str) -> str:
    direct = os.getenv(library_env_name(value), "").strip()
    if direct:
        return direct
    path = os.getenv(library_env_name(file_value), "").strip()
    if path:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    direct = os.getenv(value, "").strip()
    if direct:
        return direct
    path = os.getenv(file_value, "").strip()
    if not path:
        return ""
    return Path(path).expanduser().read_text(encoding="utf-8").strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(library_env(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{library_env_name(name)} must be a boolean")


@dataclass(frozen=True)
class LibraryInfrastructureSettings:
    catalog_backend: str
    mysql_host: str
    mysql_port: int
    mysql_database: str
    mysql_user: str
    mysql_password: str
    mysql_pool_size: int
    redis_host: str
    redis_port: int
    redis_db: int
    redis_password: str
    redis_prefix: str
    object_root: Path
    cache_redis_enabled: bool = False
    cache_redis_host: str = "127.0.0.1"
    cache_redis_port: int = 6380
    cache_redis_db: int = 0
    cache_redis_password: str = ""
    cache_redis_prefix: str = "oohstory-cache:"
    cache_redis_connect_timeout: float = 0.2
    cache_redis_socket_timeout: float = 0.4
    cache_redis_max_payload_bytes: int = 256 * 1024
    mysql_read_timeout: int = 30
    mysql_write_timeout: int = 30

    @classmethod
    def from_env(cls) -> "LibraryInfrastructureSettings":
        backend = str(
            library_env("WEBNOVEL_CATALOG_BACKEND", "sqlite") or "sqlite"
        ).strip().lower()
        if backend not in {"sqlite", "shadow", "mysql"}:
            raise ValueError(
                "OOHSTORY_LIBRARY_CATALOG_BACKEND must be sqlite, "
                "shadow, or mysql"
            )
        prefix = str(
            library_env("WEBNOVEL_REDIS_PREFIX", "oohstory:")
            or "oohstory:"
        ).strip()
        if not prefix:
            raise ValueError("OOHSTORY_LIBRARY_REDIS_PREFIX cannot be empty")
        if not prefix.endswith(":"):
            prefix += ":"
        cache_prefix = str(
            library_env("WEBNOVEL_CACHE_REDIS_PREFIX", "oohstory-cache:")
            or "oohstory-cache:"
        ).strip()
        if not cache_prefix:
            raise ValueError(
                "OOHSTORY_LIBRARY_CACHE_REDIS_PREFIX cannot be empty"
            )
        if not cache_prefix.endswith(":"):
            cache_prefix += ":"
        return cls(
            catalog_backend=backend,
            mysql_host=str(
                library_env("WEBNOVEL_MYSQL_HOST", "127.0.0.1")
                or "127.0.0.1"
            ).strip(),
            mysql_port=int(
                library_env("WEBNOVEL_MYSQL_PORT", "3306") or "3306"
            ),
            mysql_database=str(
                library_env("WEBNOVEL_MYSQL_DATABASE", "oohstory_library")
                or "oohstory_library"
            ).strip(),
            mysql_user=str(
                library_env("WEBNOVEL_MYSQL_USER", "oohstory_library")
                or "oohstory_library"
            ).strip(),
            mysql_password=_read_secret(
                "WEBNOVEL_MYSQL_PASSWORD",
                "WEBNOVEL_MYSQL_PASSWORD_FILE",
            ),
            mysql_pool_size=max(
                2,
                min(
                    int(
                        library_env("WEBNOVEL_MYSQL_POOL_SIZE", "12")
                        or "12"
                    ),
                    64,
                ),
            ),
            redis_host=str(
                library_env("WEBNOVEL_REDIS_HOST", "127.0.0.1")
                or "127.0.0.1"
            ).strip(),
            redis_port=int(
                library_env("WEBNOVEL_REDIS_PORT", "6379") or "6379"
            ),
            redis_db=int(
                library_env("WEBNOVEL_REDIS_DB", "6") or "6"
            ),
            redis_password=_read_secret(
                "WEBNOVEL_REDIS_PASSWORD",
                "WEBNOVEL_REDIS_PASSWORD_FILE",
            ),
            redis_prefix=prefix,
            cache_redis_enabled=_env_bool(
                "WEBNOVEL_CACHE_REDIS_ENABLED", False
            ),
            cache_redis_host=str(
                library_env("WEBNOVEL_CACHE_REDIS_HOST", "127.0.0.1")
                or "127.0.0.1"
            ).strip(),
            cache_redis_port=int(
                library_env("WEBNOVEL_CACHE_REDIS_PORT", "6380") or "6380"
            ),
            cache_redis_db=int(
                library_env("WEBNOVEL_CACHE_REDIS_DB", "0") or "0"
            ),
            cache_redis_password=_read_secret(
                "WEBNOVEL_CACHE_REDIS_PASSWORD",
                "WEBNOVEL_CACHE_REDIS_PASSWORD_FILE",
            ),
            cache_redis_prefix=cache_prefix,
            cache_redis_connect_timeout=max(
                0.05,
                min(
                    float(
                        library_env(
                            "WEBNOVEL_CACHE_REDIS_CONNECT_TIMEOUT", "0.2"
                        )
                        or "0.2"
                    ),
                    2.0,
                ),
            ),
            cache_redis_socket_timeout=max(
                0.05,
                min(
                    float(
                        library_env(
                            "WEBNOVEL_CACHE_REDIS_SOCKET_TIMEOUT", "0.4"
                        )
                        or "0.4"
                    ),
                    3.0,
                ),
            ),
            cache_redis_max_payload_bytes=max(
                16 * 1024,
                min(
                    int(
                        library_env(
                            "WEBNOVEL_CACHE_REDIS_MAX_PAYLOAD_BYTES",
                            str(256 * 1024),
                        )
                        or str(256 * 1024)
                    ),
                    1024 * 1024,
                ),
            ),
            object_root=Path(
                library_env(
                    "WEBNOVEL_OBJECT_ROOT",
                    str(DEFAULT_OBJECT_ROOT),
                )
            ).expanduser(),
            mysql_read_timeout=max(
                5,
                min(
                    int(
                        library_env("WEBNOVEL_MYSQL_READ_TIMEOUT", "30")
                        or "30"
                    ),
                    1800,
                ),
            ),
            mysql_write_timeout=max(
                5,
                min(
                    int(
                        library_env("WEBNOVEL_MYSQL_WRITE_TIMEOUT", "30")
                        or "30"
                    ),
                    1800,
                ),
            ),
        )


class MySQLConnectionPool:
    """Small process-local pool with connection validation on every checkout."""

    def __init__(self, settings: LibraryInfrastructureSettings):
        self.settings = settings
        self._available: queue.LifoQueue[pymysql.Connection] = queue.LifoQueue(
            maxsize=settings.mysql_pool_size
        )
        self._created = 0
        self._lock = threading.Lock()

    def _new_connection(self) -> pymysql.Connection:
        return pymysql.connect(
            host=self.settings.mysql_host,
            port=self.settings.mysql_port,
            user=self.settings.mysql_user,
            password=self.settings.mysql_password,
            database=self.settings.mysql_database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
            connect_timeout=5,
            read_timeout=self.settings.mysql_read_timeout,
            write_timeout=self.settings.mysql_write_timeout,
            program_name="oohstory-library",
            init_command=(
                "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED"
            ),
        )

    def _acquire(self) -> pymysql.Connection:
        try:
            connection = self._available.get_nowait()
        except queue.Empty:
            with self._lock:
                if self._created < self.settings.mysql_pool_size:
                    self._created += 1
                    create = True
                else:
                    create = False
            if create:
                try:
                    connection = self._new_connection()
                except RECOVERABLE_OPERATION_ERRORS:
                    with self._lock:
                        self._created -= 1
                    raise
            else:
                connection = self._available.get(timeout=10)
        try:
            connection.ping(reconnect=True)
        except RECOVERABLE_OPERATION_ERRORS:
            self._discard(connection)
            with self._lock:
                self._created += 1
            try:
                connection = self._new_connection()
            except RECOVERABLE_OPERATION_ERRORS:
                with self._lock:
                    self._created -= 1
                raise
        return connection

    def _discard(self, connection: pymysql.Connection) -> None:
        with contextlib.suppress(Exception):
            connection.close()
        with self._lock:
            self._created = max(0, self._created - 1)

    def _release(self, connection: pymysql.Connection) -> None:
        try:
            self._available.put_nowait(connection)
        except queue.Full:
            self._discard(connection)

    @contextlib.contextmanager
    def connection(
        self,
        *,
        readonly: bool = False,
    ) -> Iterator[pymysql.Connection]:
        connection = self._acquire()
        healthy = True
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET SESSION TRANSACTION READ ONLY"
                    if readonly
                    else "SET SESSION TRANSACTION READ WRITE"
                )
            yield connection
            if readonly:
                connection.rollback()
            else:
                connection.commit()
        except RECOVERABLE_OPERATION_ERRORS:
            healthy = False
            with contextlib.suppress(Exception):
                connection.rollback()
            raise
        finally:
            if healthy and getattr(connection, "open", False):
                self._release(connection)
            else:
                self._discard(connection)

    def health(self) -> dict[str, Any]:
        with self.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT VERSION() AS version, "
                    "@@hostname AS hostname, "
                    "@@transaction_isolation AS transaction_isolation"
                )
                row = cursor.fetchone()
        return {
            "ok": True,
            "version": row["version"],
            "hostname": row["hostname"],
            "transaction_isolation": row["transaction_isolation"],
            "pool_size": self.settings.mysql_pool_size,
        }


class RedisQueueClient:
    """Namespaced Redis Streams client backed by durable MySQL jobs."""

    DOWNLOAD_STREAM = "downloads"
    DOWNLOAD_GROUP = "download-workers"

    def __init__(self, settings: LibraryInfrastructureSettings):
        self.settings = settings
        self.client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=5,
            health_check_interval=30,
        )

    def key(self, suffix: str) -> str:
        return f"{self.settings.redis_prefix}{suffix.lstrip(':')}"

    def ensure_groups(self) -> None:
        stream = self.key(self.DOWNLOAD_STREAM)
        try:
            self.client.xgroup_create(
                stream,
                self.DOWNLOAD_GROUP,
                id="0",
                mkstream=True,
            )
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def enqueue_download(
        self,
        *,
        job_id: int,
        catalog_id: int,
        source_name: str,
        priority: int,
    ) -> str:
        self.ensure_groups()
        return str(
            self.client.xadd(
                self.key(self.DOWNLOAD_STREAM),
                {
                    "job_id": int(job_id),
                    "catalog_id": int(catalog_id),
                    "source_name": source_name,
                    "priority": int(priority),
                },
                maxlen=2_000_000,
                approximate=True,
            )
        )

    def health(self) -> dict[str, Any]:
        return {
            "ok": bool(self.client.ping()),
            "version": self.client.info("server").get("redis_version"),
            "db": self.settings.redis_db,
            "prefix": self.settings.redis_prefix,
        }
