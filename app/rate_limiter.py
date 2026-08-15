"""Per-IP rate limiting and bot protection middleware for OOH Story."""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from .reader_abuse import ReaderAbuseGuard, ReaderProbeSigner

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STATE_ROOT = Path(
    os.getenv("OOHSTORY_STATE_ROOT", str(_PROJECT_ROOT / "var"))
).expanduser().resolve()
_DOWNLOAD_LOG_PATH = _STATE_ROOT / "download_daily.json"
_DOWNLOAD_LOG_LOCK_PATH = _STATE_ROOT / "download_daily.lock"
_DOWNLOAD_LOG_LOCK = threading.Lock()
_READER_ABUSE_DB_PATH = _STATE_ROOT / "reader_abuse.sqlite3"
_READER_PROBE_SECRET_PATH = _STATE_ROOT / "reader_probe.key"


KNOWN_GOOD_BOTS = frozenset({
    "googlebot",
    "bingbot",
    "yandexbot",
    "baiduspider",
    "duckduckbot",
    "slurp",
    "sogou",
    "360spider",
})

BLOCKED_BOT_KEYWORDS = frozenset({
    # Scraping frameworks / HTTP libraries (not real browsers or crawlers)
    "scrapy",
    "python-requests",
    "go-http-client",
    "java/",
    "wget",
    "libwww",
    "lwp-trivial",
    "httpunit",
    "nutch",
    "phpcrawl",
    "fast-webcrawler",
    "fast enterprise crawler",
    "biglotron",
    "heritrix",
    # AI training bots
    "gptbot",
    "claudebot",
    "ccbot",
    "bytespider",
    # Security scanners
    "censys",
    "zgrab",
    "masscan",
    "httpx",
})

PERMANENTLY_BLOCKED_IPS = frozenset({
    "198.51.100.24",
    "198.51.100.23",
})

_SEARCH_ENGINE_CIDRS = [
    # Google
    "66.249.64.0/19",
    "64.233.160.0/19",
    "72.14.192.0/18",
    "209.85.128.0/17",
    "216.239.32.0/19",
    "66.102.0.0/20",
    "74.125.0.0/16",
    "108.177.8.0/21",
    "172.217.0.0/16",
    "142.250.0.0/15",
    "35.191.0.0/16",
    "34.64.0.0/10",
    # Bing / MSN
    "40.77.167.0/24",
    "207.46.0.0/16",
    "157.55.0.0/16",
    "199.30.16.0/20",
    "13.66.0.0/16",
    "13.64.0.0/11",
    "40.74.0.0/15",
    # Yandex
    "5.255.250.0/24",
    "77.88.5.0/24",
    "87.250.224.0/19",
    "100.43.80.0/21",
    # Baidu
    "180.76.0.0/16",
    "119.63.192.0/21",
    "116.179.32.0/20",
    # DuckDuckGo
    "20.191.45.212/32",
    "40.88.21.235/32",
    "52.142.26.175/32",
]

SEARCH_ENGINE_NETS = [ipaddress.ip_network(c) for c in _SEARCH_ENGINE_CIDRS]

_PUBLIC_READER_PATH_RE = re.compile(
    r"^/api/v1/books/[A-Za-z0-9_-]{22}(?:"
    r"/cover"
    r"|/chapters(?:/[1-9][0-9]*)?"
    r"|/illustrations/.+"
    r")?$"
)

_COUNTED_DOWNLOAD_PATH_RE = re.compile(
    r"^/api/v1/(?:"
    r"books/[A-Za-z0-9_-]{22}/download"
    r"|me/deconstructions/[^/]+/download"
    r")$"
)

_READER_CHAPTER_API_RE = re.compile(
    r"^/api/v1/books/([A-Za-z0-9_-]{22})/chapters/([1-9][0-9]*)$"
)
_READER_CHAPTER_HTML_RE = re.compile(
    r"^/books/([A-Za-z0-9_-]{22})/chapters/([1-9][0-9]*)$"
)


@dataclass
class _Bucket:
    tokens: float
    last_refill: float
    violations: int = 0


@dataclass
class _BanRecord:
    until: float
    reason: str


@dataclass
class _DailyCounter:
    count: int
    day: int


class RateLimiter:
    """Token-bucket rate limiter with auto-ban for repeat offenders."""

    DOWNLOAD_DAILY_LIMIT = 10

    def __init__(
        self,
        global_rate: float = 2.0,
        global_burst: int = 30,
        sitemap_rate: float = 0.1,
        sitemap_burst: int = 5,
        chapter_rate: float = 1.5,
        chapter_burst: int = 20,
        ban_threshold: int = 50,
        ban_duration: int = 3600,
        cleanup_interval: int = 300,
        reader_guard_enabled: bool = False,
        reader_guard_path: Path = _READER_ABUSE_DB_PATH,
        reader_probe_secret_path: Path = _READER_PROBE_SECRET_PATH,
        reader_recent_limit: int = 60,
        reader_daily_limit: int = 500,
    ):
        self._global_rate = global_rate
        self._global_burst = global_burst
        self._sitemap_rate = sitemap_rate
        self._sitemap_burst = sitemap_burst
        self._chapter_rate = chapter_rate
        self._chapter_burst = chapter_burst
        self._ban_threshold = ban_threshold
        self._ban_duration = ban_duration
        self._cleanup_interval = cleanup_interval

        self._buckets: dict[str, _Bucket] = {}
        self._bans: dict[str, _BanRecord] = {}
        self._download_counters: dict[str, _DailyCounter] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()
        self._reader_guard = (
            ReaderAbuseGuard(
                reader_guard_path,
                recent_limit=reader_recent_limit,
                daily_limit=reader_daily_limit,
            )
            if reader_guard_enabled
            else None
        )
        self._reader_probe_signer = (
            ReaderProbeSigner(reader_probe_secret_path)
            if reader_guard_enabled
            else None
        )

    def _get_client_ip(self, request: Request) -> str:
        candidates = [
            request.headers.get("cf-connecting-ip", ""),
            request.headers.get("x-forwarded-for", "").split(",", 1)[0],
            request.client.host if request.client else "",
        ]
        for candidate in candidates:
            candidate = candidate.strip()
            try:
                return ipaddress.ip_address(candidate).compressed
            except ValueError:
                continue
        return "unknown"

    def _classify_path(self, path: str) -> tuple[str, float, int]:
        if path.startswith("/sitemap") or path.startswith("/sitemaps/"):
            return "sitemap", self._sitemap_rate, self._sitemap_burst
        if "/chapters/" in path:
            return "chapter", self._chapter_rate, self._chapter_burst
        return "global", self._global_rate, self._global_burst

    @staticmethod
    def _reader_chapter_resource(path: str) -> tuple[str, int] | None:
        for pattern in (_READER_CHAPTER_API_RE, _READER_CHAPTER_HTML_RE):
            match = pattern.fullmatch(path)
            if match:
                return match.group(1), int(match.group(2))
        return None

    @staticmethod
    def _is_public_reader_request(request: Request) -> bool:
        return (
            request.method in {"GET", "HEAD"}
            and _PUBLIC_READER_PATH_RE.fullmatch(request.url.path) is not None
        )

    def _try_consume(self, key: str, rate: float, burst: int) -> bool:
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=burst - 1, last_refill=now)
            self._buckets[key] = bucket
            return True
        elapsed = now - bucket.last_refill
        bucket.tokens = min(burst, bucket.tokens + elapsed * rate)
        bucket.last_refill = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        bucket.violations += 1
        return False

    def _maybe_ban(self, ip: str, key: str) -> None:
        bucket = self._buckets.get(key)
        if bucket and bucket.violations >= self._ban_threshold:
            self._bans[ip] = _BanRecord(
                until=time.monotonic() + self._ban_duration,
                reason=f"exceeded {self._ban_threshold} rate limit violations",
            )
            bucket.violations = 0

    def _is_banned(self, ip: str) -> _BanRecord | None:
        record = self._bans.get(ip)
        if record is None:
            return None
        if time.monotonic() > record.until:
            del self._bans[ip]
            return None
        return record

    def _cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        stale_keys = [
            k for k, v in self._buckets.items()
            if now - v.last_refill > 600
        ]
        for k in stale_keys:
            del self._buckets[k]
        expired_bans = [
            k for k, v in self._bans.items()
            if now > v.until
        ]
        for k in expired_bans:
            del self._bans[k]
        today = int(time.time()) // 86400
        stale_dl = [k for k, v in self._download_counters.items() if v.day != today]
        for k in stale_dl:
            del self._download_counters[k]

    @staticmethod
    def _is_official_app(ua: str) -> bool:
        return "oohstoryapp/" in ua.lower() and "official" in ua.lower()

    def _is_blocked_bot(self, ua: str) -> str | None:
        ua_lower = ua.lower()
        if not ua or len(ua) < 10:
            return "empty or suspicious user agent"
        if self._is_official_app(ua):
            return None
        for keyword in BLOCKED_BOT_KEYWORDS:
            if keyword in ua_lower:
                return f"blocked bot: {keyword}"
        return None

    def _is_search_engine(self, ua: str) -> bool:
        ua_lower = ua.lower()
        return any(bot in ua_lower for bot in KNOWN_GOOD_BOTS)

    @staticmethod
    def is_search_engine_ip(ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in SEARCH_ENGINE_NETS)

    @staticmethod
    def _check_download_limit(ip: str) -> JSONResponse | None:
        limit = RateLimiter.DOWNLOAD_DAILY_LIMIT
        today = time.strftime("%Y-%m-%d")
        with _DOWNLOAD_LOG_LOCK:
            try:
                _DOWNLOAD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                with _DOWNLOAD_LOG_LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
                    # Uvicorn runs multiple workers. A threading.Lock only
                    # protects one worker, so reserve the quota while holding
                    # a process-wide advisory lock shared by every worker.
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    data: dict[str, Any] = {}
                    if _DOWNLOAD_LOG_PATH.exists():
                        try:
                            loaded = json.loads(
                                _DOWNLOAD_LOG_PATH.read_text(encoding="utf-8")
                            )
                            if isinstance(loaded, dict):
                                data = loaded
                        except (json.JSONDecodeError, OSError):
                            return JSONResponse(
                                {"detail": "下载额度暂时无法验证，请稍后再试"},
                                status_code=503,
                                headers={"Retry-After": "60"},
                            )
                    if data.get("_date") != today:
                        data = {"_date": today}
                    count = int(data.get(ip, 0) or 0)
                    if count >= limit:
                        now = time.time()
                        local_now = time.localtime(now)
                        next_midnight = time.mktime(
                            (
                                local_now.tm_year,
                                local_now.tm_mon,
                                local_now.tm_mday + 1,
                                0,
                                0,
                                0,
                                0,
                                0,
                                -1,
                            )
                        )
                        retry_after = max(1, int(next_midnight - now))
                        return JSONResponse(
                            {
                                "detail": (
                                    f"今日下载次数已达上限（{limit}次/天），"
                                    "请明天再试"
                                ),
                                "daily_limit": limit,
                            },
                            status_code=429,
                            headers={"Retry-After": str(retry_after)},
                        )
                    data[ip] = count + 1

                    temp_path = _DOWNLOAD_LOG_PATH.with_name(
                        f".{_DOWNLOAD_LOG_PATH.name}.{os.getpid()}."
                        f"{threading.get_ident()}.tmp"
                    )
                    try:
                        with temp_path.open("w", encoding="utf-8") as output:
                            json.dump(data, output, ensure_ascii=False)
                            output.flush()
                            os.fsync(output.fileno())
                        temp_path.chmod(0o600)
                        os.replace(temp_path, _DOWNLOAD_LOG_PATH)
                    finally:
                        temp_path.unlink(missing_ok=True)
            except OSError:
                # A broken quota store must not silently grant unlimited
                # downloads. Reading stays available; only downloads pause.
                return JSONResponse(
                    {"detail": "下载额度暂时无法验证，请稍后再试"},
                    status_code=503,
                    headers={"Retry-After": "60"},
                )
        return None

    def ban_ip(self, ip: str, duration: int, reason: str) -> None:
        with self._lock:
            self._bans[ip] = _BanRecord(
                until=time.monotonic() + duration,
                reason=reason,
            )

    def reader_probe_path(
        self,
        request: Request,
        book_id: str,
        chapter_id: int,
    ) -> str | None:
        if self._reader_probe_signer is None:
            return None
        ip = self._get_client_ip(request)
        try:
            token = self._reader_probe_signer.mint(ip, book_id, chapter_id)
        except OSError:
            return None
        return f"/api/v1/reader-probe/{book_id}/{chapter_id}/{token}"

    def record_reader_probe(
        self,
        request: Request,
        book_id: str,
        chapter_id: int,
        token: str,
    ) -> bool:
        if self._reader_probe_signer is None or self._reader_guard is None:
            return False
        ip = self._get_client_ip(request)
        try:
            valid = self._reader_probe_signer.validate(
                ip,
                book_id,
                chapter_id,
                token,
            )
        except OSError:
            return False
        if not valid:
            return False
        self._reader_guard.record_trap(ip)
        return True

    def record_honeypot(self, request: Request) -> None:
        if self._reader_guard is None:
            return
        self._reader_guard.record_trap(self._get_client_ip(request))

    def check(self, request: Request) -> JSONResponse | None:
        path = request.url.path
        if path in {"/healthz", "/robots.txt", "/favicon.ico"}:
            return None
        chapter_resource = self._reader_chapter_resource(path)
        is_api = path.startswith("/api/")
        if not is_api and chapter_resource is None:
            return None

        ip = self._get_client_ip(request)
        ua = request.headers.get("user-agent", "")

        if ip in PERMANENTLY_BLOCKED_IPS:
            return JSONResponse(
                {"detail": "Access denied"},
                status_code=403,
            )

        is_bot = self._is_search_engine(ua)
        is_verified_engine = is_bot and self.is_search_engine_ip(ip)

        if is_bot and is_api:
            return JSONResponse(
                {"detail": "Not Found"},
                status_code=404,
            )

        if is_verified_engine:
            return None

        bot_reason = self._is_blocked_bot(ua)
        if bot_reason and not is_bot:
            return JSONResponse(
                {"detail": "Access denied"},
                status_code=403,
            )

        if chapter_resource is not None and self._reader_guard is not None:
            book_id, chapter_id = chapter_resource
            decision = self._reader_guard.check_chapter(
                ip,
                f"{book_id}:{chapter_id}",
            )
            if decision is not None:
                return JSONResponse(
                    {
                        "detail": "章节访问过于频繁，请稍后再试",
                        "retry_after": decision.retry_after,
                    },
                    status_code=429,
                    headers={"Retry-After": str(decision.retry_after)},
                )

        if not is_api:
            return None

        # Authentication endpoints already use durable, operation-specific
        # limits in AccountStore. Unrelated reading or comment traffic must
        # not consume the generic IP bucket and turn a valid session probe or
        # login into a misleading 429 response. Permanent IP and bot blocks
        # above still apply.
        if path.startswith("/api/v1/auth/"):
            return None

        with self._lock:
            self._cleanup()

            # Reading is a public, read-only workload. A chapter page loads
            # the catalog, current body and adjacent chapter prefetches in a
            # short burst; treating those requests as abuse made ordinary
            # readers accumulate violations and eventually hit a two-hour
            # transient ban. Keep permanent IP and bot protections above,
            # but never apply dynamic buckets or transient bans to immutable
            # public book, chapter, cover and illustration reads.
            if self._is_public_reader_request(request):
                return None

            ban = self._is_banned(ip)
            if ban:
                return JSONResponse(
                    {"detail": "请求过于频繁，请稍后再试", "retry_after": self._ban_duration},
                    status_code=429,
                    headers={"Retry-After": str(self._ban_duration)},
                )

            if (
                request.method == "GET"
                and _COUNTED_DOWNLOAD_PATH_RE.fullmatch(path) is not None
            ):
                blocked = self._check_download_limit(ip)
                if blocked is not None:
                    return blocked

            # Audiobook playback deliberately performs several coordinated
            # requests (manifest, timeline, stream, progress and cache probes)
            # at once.  Applying the generic page token bucket here classifies
            # those URLs as chapter traffic and can rate-limit a legitimate
            # player before the dedicated, shared audiobook quotas run.
            if path.startswith("/api/v1/audiobook/"):
                return None

            category, rate, burst = self._classify_path(path)

            if is_bot:
                rate = min(rate, 0.02)
                burst = min(burst, 1)

            key = f"{ip}:{category}"

            if not self._try_consume(key, rate, burst):
                self._maybe_ban(ip, key)
                retry_after = 60 if category == "sitemap" else 10
                return JSONResponse(
                    {"detail": "请求过于频繁，请稍后再试", "retry_after": retry_after},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )

        return None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_buckets": len(self._buckets),
                "active_bans": len(self._bans),
                "banned_ips": {
                    ip: {"until": r.until - time.monotonic(), "reason": r.reason}
                    for ip, r in self._bans.items()
                },
                "download_counters": {
                    ip: {"count": c.count, "limit": self.DOWNLOAD_DAILY_LIMIT}
                    for ip, c in self._download_counters.items()
                },
            }
