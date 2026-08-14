"""Fail-open Redis hot cache for disposable library read models.

MySQL and the NAS remain authoritative.  This module deliberately exposes no
Redis Streams, locks, or leases so it cannot accidentally be used as the
operational queue client.
"""

from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import redis


CACHE_SCOPES = ("catalog", "book", "cover", "tone", "plot", "deconstruction")
_FORBIDDEN_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "secret",
        "credentials",
        "image_bytes",
        "cover_bytes",
        "book_body",
        "body_text",
        "chapter_text",
        "chapters",
        "full_report",
        "deconstruction_file",
    }
)


@dataclass(frozen=True, slots=True)
class LibraryCacheSettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 6380
    db: int = 0
    password: str = ""
    prefix: str = "oohstory-cache:"
    connect_timeout_seconds: float = 0.2
    socket_timeout_seconds: float = 0.4
    max_key_bytes: int = 512
    max_payload_bytes: int = 256 * 1024
    warm_workers: int = 2
    warm_queue_size: int = 32

    @classmethod
    def from_infrastructure(cls, settings: Any) -> "LibraryCacheSettings":
        return cls(
            enabled=bool(settings.cache_redis_enabled),
            host=str(settings.cache_redis_host),
            port=int(settings.cache_redis_port),
            db=int(settings.cache_redis_db),
            password=str(settings.cache_redis_password or ""),
            prefix=str(settings.cache_redis_prefix),
            connect_timeout_seconds=float(settings.cache_redis_connect_timeout),
            socket_timeout_seconds=float(settings.cache_redis_socket_timeout),
            max_payload_bytes=int(settings.cache_redis_max_payload_bytes),
        )


def _normalized(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _normalized(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        return sorted((_normalized(item) for item in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.replace("\x00", "").split())
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


class RedisHotCache:
    """Small, bounded JSON cache whose every operation fails open."""

    def __init__(
        self,
        settings: LibraryCacheSettings,
        *,
        client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        if settings.enabled and client is None:
            self.client = redis.Redis(
                host=settings.host,
                port=settings.port,
                db=settings.db,
                password=settings.password or None,
                decode_responses=False,
                socket_connect_timeout=settings.connect_timeout_seconds,
                socket_timeout=settings.socket_timeout_seconds,
                health_check_interval=30,
            )
        self._metrics = {"hits": 0, "misses": 0, "errors": 0}
        self._metrics_lock = threading.Lock()
        self._warm_lock = threading.Lock()
        self._warming: set[str] = set()
        self._warm_pending: dict[
            str,
            tuple[
                str,
                str,
                Mapping[str, Any],
                Callable[[], Any],
                int,
                int,
            ],
        ] = {}
        self._warm_slots = threading.BoundedSemaphore(settings.warm_queue_size)
        self._executor = (
            ThreadPoolExecutor(
                max_workers=settings.warm_workers,
                thread_name_prefix="oohstory-cache-warm",
            )
            if settings.enabled and settings.warm_workers > 0
            else None
        )

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled and self.client is not None)

    @staticmethod
    def query_hash(payload: Mapping[str, Any]) -> str:
        raw = json.dumps(
            _normalized(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _metric(self, name: str) -> None:
        with self._metrics_lock:
            self._metrics[name] += 1

    def _key(self, suffix: str) -> str:
        key = f"{self.settings.prefix}{suffix.lstrip(':')}"
        if len(key.encode("utf-8")) > self.settings.max_key_bytes:
            raise ValueError("cache key exceeds configured bound")
        return key

    def generation(self, scope: str) -> int:
        if scope not in CACHE_SCOPES or not self.enabled:
            return 0
        try:
            return int(self.client.get(self._key(f"generation:{scope}")) or 0)
        except RECOVERABLE_OPERATION_ERRORS:
            self._metric("errors")
            return 0

    def key_for(
        self,
        scope: str,
        kind: str,
        query: Mapping[str, Any],
        *,
        generation: int | None = None,
    ) -> str:
        if scope not in CACHE_SCOPES:
            raise ValueError(f"unknown cache scope: {scope}")
        safe_kind = "".join(
            char for char in str(kind).lower() if char.isalnum() or char in "-_"
        )[:48]
        if not safe_kind:
            raise ValueError("cache kind is empty")
        selected_generation = (
            self.generation(scope) if generation is None else max(int(generation), 0)
        )
        return self._key(
            f"value:{scope}:{selected_generation}:{safe_kind}:{self.query_hash(query)}"
        )

    @classmethod
    def _safe_payload(cls, value: Any, *, depth: int = 0) -> bool:
        if depth > 12:
            return False
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).casefold() in _FORBIDDEN_KEYS:
                    return False
                if not cls._safe_payload(item, depth=depth + 1):
                    return False
            return True
        if isinstance(value, (list, tuple)):
            return len(value) <= 2_000 and all(
                cls._safe_payload(item, depth=depth + 1) for item in value
            )
        if isinstance(value, str):
            return len(value.encode("utf-8")) <= 32 * 1024
        return value is None or isinstance(value, (bool, int, float)) or hasattr(
            value, "isoformat"
        )

    def get_json(
        self,
        scope: str,
        kind: str,
        query: Mapping[str, Any],
        *,
        expected_type: type = dict,
    ) -> Any | None:
        if not self.enabled:
            return None
        try:
            raw = self.client.get(self.key_for(scope, kind, query))
            if raw is None:
                self._metric("misses")
                return None
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            if not isinstance(raw, bytes) or len(raw) > self.settings.max_payload_bytes:
                raise ValueError("invalid cached payload size")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, expected_type) or not self._safe_payload(value):
                raise ValueError("invalid cached JSON shape")
            self._metric("hits")
            return value
        except RECOVERABLE_OPERATION_ERRORS:
            self._metric("errors")
            self._metric("misses")
            return None

    def set_json(
        self,
        scope: str,
        kind: str,
        query: Mapping[str, Any],
        value: Any,
        *,
        ttl_seconds: int,
        generation: int | None = None,
    ) -> bool:
        if not self.enabled or not self._safe_payload(value):
            return False
        try:
            raw = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                default=lambda item: item.isoformat()
                if hasattr(item, "isoformat")
                else str(item),
            ).encode("utf-8")
            if len(raw) > self.settings.max_payload_bytes:
                return False
            self.client.setex(
                self.key_for(scope, kind, query, generation=generation),
                max(1, min(int(ttl_seconds), 86_400)),
                raw,
            )
            return True
        except RECOVERABLE_OPERATION_ERRORS:
            self._metric("errors")
            return False

    def invalidate(self, *scopes: str) -> dict[str, int]:
        generations: dict[str, int] = {}
        if not self.enabled:
            return generations
        for scope in dict.fromkeys(scopes or CACHE_SCOPES):
            if scope not in CACHE_SCOPES:
                continue
            try:
                generations[scope] = int(
                    self.client.incr(self._key(f"generation:{scope}"))
                )
            except RECOVERABLE_OPERATION_ERRORS:
                self._metric("errors")
        return generations

    def schedule_warm(
        self,
        scope: str,
        kind: str,
        query: Mapping[str, Any],
        loader: Callable[[], Any],
        *,
        ttl_seconds: int,
    ) -> bool:
        """Coalesce and enqueue a bounded best-effort durable read."""

        if not self.enabled or self._executor is None:
            return False
        token = f"{scope}:{kind}:{self.query_hash(query)}"
        generation = self.generation(scope)
        work = (scope, kind, query, loader, ttl_seconds, generation)
        with self._warm_lock:
            if token in self._warming:
                # A second durable update arrived while this key was warming.
                # Keep only the newest loader/generation, then rerun after the
                # in-flight load finishes.  This both coalesces bursts and
                # guarantees the latest committed value is warmed.
                self._warm_pending[token] = work
                return True
            if not self._warm_slots.acquire(blocking=False):
                return False
            self._warming.add(token)

        def run() -> None:
            current = work
            while True:
                (
                    current_scope,
                    current_kind,
                    current_query,
                    current_loader,
                    current_ttl,
                    current_generation,
                ) = current
                try:
                    value = current_loader()
                    if value is not None:
                        self.set_json(
                            current_scope,
                            current_kind,
                            current_query,
                            value,
                            ttl_seconds=current_ttl,
                            generation=current_generation,
                        )
                except RECOVERABLE_OPERATION_ERRORS:
                    self._metric("errors")
                with self._warm_lock:
                    pending = self._warm_pending.pop(token, None)
                    if pending is not None:
                        current = pending
                        continue
                    self._warming.discard(token)
                    self._warm_slots.release()
                    return

        try:
            self._executor.submit(run)
            return True
        except RECOVERABLE_OPERATION_ERRORS:
            with self._warm_lock:
                self._warming.discard(token)
            self._warm_slots.release()
            self._metric("errors")
            return False

    def stats(self) -> dict[str, Any]:
        with self._metrics_lock:
            metrics = dict(self._metrics)
        with self._warm_lock:
            warm_depth = len(self._warming) + len(self._warm_pending)
        total = metrics["hits"] + metrics["misses"]
        result: dict[str, Any] = {
            "enabled": self.settings.enabled,
            "endpoint": f"{self.settings.host}:{self.settings.port}/{self.settings.db}",
            "ping": False,
            **metrics,
            "hit_rate": round(metrics["hits"] / total, 4) if total else 0.0,
            "generations": {scope: 0 for scope in CACHE_SCOPES},
            "warm_queue_depth": warm_depth,
        }
        if not self.enabled:
            return result
        try:
            result["ping"] = bool(self.client.ping())
            # Only fetch per-scope generations after one successful ping.  A
            # dead cache therefore adds one short timeout to the health page,
            # not one timeout per scope.
            result["generations"] = {
                scope: self.generation(scope) for scope in CACHE_SCOPES
            }
            memory = self.client.info("memory") or {}
            result["used_memory"] = int(memory.get("used_memory") or 0)
            result["maxmemory"] = int(memory.get("maxmemory") or 0)
            policy = memory.get("maxmemory_policy")
            if not policy:
                policy = (self.client.config_get("maxmemory-policy") or {}).get(
                    "maxmemory-policy", ""
                )
            result["policy"] = str(policy or "")
        except RECOVERABLE_OPERATION_ERRORS:
            self._metric("errors")
            result["errors"] += 1
        return result
