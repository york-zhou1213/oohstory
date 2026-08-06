from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any


SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_BYTES,
        maxmem=64 * 1024 * 1024,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64encode(salt)}${_b64encode(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n_text, r_text, p_text, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_text), int(r_text), int(p_text)
        if n < 2**14 or n > 2**18 or r < 8 or r > 16 or p < 1 or p > 4:
            return False
        salt, expected = _b64decode(salt_text), _b64decode(digest_text)
        if not 16 <= len(salt) <= 64 or not 32 <= len(expected) <= 64:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=128 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, MemoryError):
        return False


class SessionSigner:
    def __init__(self, secret: str, ttl_seconds: int):
        self._key = secret.encode("utf-8")
        self._ttl = ttl_seconds

    def create(self, username: str, now: int | None = None) -> tuple[str, str]:
        issued = int(time.time()) if now is None else now
        csrf = secrets.token_urlsafe(32)
        payload = {
            "sub": username,
            "iat": issued,
            "exp": issued + self._ttl,
            "csrf": csrf,
            "nonce": secrets.token_urlsafe(16),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        encoded = _b64encode(raw)
        signature = _b64encode(hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}", csrf

    def verify(self, token: str, now: int | None = None) -> dict[str, Any] | None:
        if not self._key or len(token) > 4096:
            return None
        try:
            encoded, supplied = token.split(".", 1)
            expected = _b64encode(hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied, expected):
                return None
            payload = json.loads(_b64decode(encoded))
            current = int(time.time()) if now is None else now
            if not isinstance(payload, dict):
                return None
            if not isinstance(payload.get("sub"), str) or not isinstance(payload.get("csrf"), str):
                return None
            issued, expires = int(payload["iat"]), int(payload["exp"])
            if issued > current + 60 or expires <= current or expires - issued > self._ttl:
                return None
            return payload
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            return None


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    limited: bool
    retry_after: int = 0


class LoginRateLimiter:
    def __init__(self, attempts: int, window_seconds: int, clock=time.monotonic):
        self.attempts = attempts
        self.window = window_seconds
        self.clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, events: deque[float], now: float) -> None:
        cutoff = now - self.window
        while events and events[0] <= cutoff:
            events.popleft()

    def check(self, *keys: str) -> RateLimitResult:
        now = self.clock()
        retry = 0
        with self._lock:
            for key in keys:
                events = self._failures[key]
                self._prune(events, now)
                if len(events) >= self.attempts:
                    retry = max(retry, int(self.window - (now - events[0])) + 1)
            return RateLimitResult(retry > 0, retry)

    def fail(self, *keys: str) -> RateLimitResult:
        now = self.clock()
        with self._lock:
            for key in keys:
                events = self._failures[key]
                self._prune(events, now)
                events.append(now)
        return self.check(*keys)

    def clear(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)


def password_cli() -> None:
    parser = argparse.ArgumentParser(description="Generate an OOHStory admin scrypt password hash")
    parser.parse_args()
    first = getpass.getpass("Admin password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("passwords do not match")
    print(hash_password(first))


if __name__ == "__main__":
    password_cli()
