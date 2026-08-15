from __future__ import annotations

import json
from pathlib import Path

from starlette.requests import Request

import app.rate_limiter as rate_limiter_module
from app.rate_limiter import RateLimiter


def _request(
    path: str,
    *,
    method: str = "GET",
    user_agent: bytes = b"Mozilla/5.0 OOHStory regression",
    headers: tuple[tuple[bytes, bytes], ...] = (),
    client_ip: str = "203.0.113.10",
) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"user-agent", user_agent), *headers],
            "client": (client_ip, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_audiobook_routes_use_dedicated_quotas_not_generic_page_bucket() -> None:
    limiter = RateLimiter(global_rate=0.001, global_burst=1, chapter_rate=0.001, chapter_burst=1)

    for _ in range(50):
        assert limiter.check(_request("/api/v1/audiobook/sessions/session/chapters/hash/timeline")) is None

    assert limiter.check(_request("/api/v1/books/example/chapters/1/comments")) is None
    blocked = limiter.check(_request("/api/v1/books/example/chapters/1/comments"))
    assert blocked is not None
    assert blocked.status_code == 429


def test_auth_routes_ignore_unrelated_generic_bucket_and_transient_ban() -> None:
    limiter = RateLimiter(
        global_rate=0.001,
        global_burst=1,
        chapter_rate=0.001,
        chapter_burst=1,
    )

    assert limiter.check(_request("/api/v1/books/example/metrics/read", method="POST")) is None
    blocked = limiter.check(
        _request("/api/v1/books/example/metrics/read", method="POST")
    )
    assert blocked is not None
    assert blocked.status_code == 429
    limiter.ban_ip("203.0.113.10", duration=3600, reason="transient test ban")

    assert limiter.check(_request("/api/v1/auth/session")) is None
    assert limiter.check(_request("/api/v1/auth/config")) is None
    assert limiter.check(_request("/api/v1/auth/login", method="POST")) is None

    blocked_bot = limiter.check(
        _request(
            "/api/v1/auth/session",
            user_agent=b"python-requests/2.32 scraper",
        )
    )
    assert blocked_bot is not None
    assert blocked_bot.status_code == 403


def test_public_reader_gets_bypass_dynamic_rate_limits_and_transient_bans() -> None:
    limiter = RateLimiter(global_rate=0.001, global_burst=1, chapter_rate=0.001, chapter_burst=1)
    book_id = "AbCdEfGhIjKlMnOpQrStUv"
    paths = (
        f"/api/v1/books/{book_id}",
        f"/api/v1/books/{book_id}/cover",
        f"/api/v1/books/{book_id}/chapters",
        f"/api/v1/books/{book_id}/chapters/12",
        f"/api/v1/books/{book_id}/illustrations/001/cover.jpg",
    )

    for _ in range(50):
        for path in paths:
            assert limiter.check(_request(path)) is None
            assert limiter.check(_request(path, method="HEAD")) is None

    limiter.ban_ip("203.0.113.10", duration=3600, reason="transient test ban")
    for path in paths:
        assert limiter.check(_request(path)) is None


def test_public_reader_bypass_does_not_cover_comments_writes_or_blocked_bots() -> None:
    limiter = RateLimiter(global_rate=0.001, global_burst=1, chapter_rate=0.001, chapter_burst=1)
    book_id = "AbCdEfGhIjKlMnOpQrStUv"

    assert limiter.check(_request(f"/api/v1/books/{book_id}/chapters/12/comments")) is None
    blocked_comment = limiter.check(_request(f"/api/v1/books/{book_id}/chapters/12/comments"))
    assert blocked_comment is not None
    assert blocked_comment.status_code == 429

    assert limiter.check(_request(f"/api/v1/books/{book_id}", method="POST")) is None
    blocked_write = limiter.check(_request(f"/api/v1/books/{book_id}", method="POST"))
    assert blocked_write is not None
    assert blocked_write.status_code == 429

    blocked_bot = limiter.check(
        _request(
            f"/api/v1/books/{book_id}/chapters/12",
            user_agent=b"python-requests/2.32 scraper",
        )
    )
    assert blocked_bot is not None
    assert blocked_bot.status_code == 403


def test_known_chapter_scraper_is_blocked_before_public_reader_bypass() -> None:
    limiter = RateLimiter()
    book_id = "AbCdEfGhIjKlMnOpQrStUv"
    request = _request(
        f"/api/v1/books/{book_id}/chapters/12",
        headers=((b"cf-connecting-ip", b"198.51.100.23"),),
    )

    blocked = limiter.check(request)

    assert blocked is not None
    assert blocked.status_code == 403

    blocked_html = limiter.check(
        _request(
            f"/books/{book_id}/chapters/12",
            headers=((b"cf-connecting-ip", b"198.51.100.23"),),
        )
    )
    assert blocked_html is not None
    assert blocked_html.status_code == 403


def test_download_quota_is_ten_gets_per_real_ip_and_shared_between_instances(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "download_daily.json"
    lock_path = tmp_path / "download_daily.lock"
    monkeypatch.setattr(rate_limiter_module, "_DOWNLOAD_LOG_PATH", log_path)
    monkeypatch.setattr(rate_limiter_module, "_DOWNLOAD_LOG_LOCK_PATH", lock_path)
    first_worker = RateLimiter()
    second_worker = RateLimiter()
    book_id = "AbCdEfGhIjKlMnOpQrStUv"
    path = f"/api/v1/books/{book_id}/download"
    real_ip_header = ((b"cf-connecting-ip", b"2001:db8::10"),)

    for index in range(10):
        worker = first_worker if index % 2 == 0 else second_worker
        assert worker.check(_request(path, headers=real_ip_header)) is None

    blocked = first_worker.check(_request(path, headers=real_ip_header))
    assert blocked is not None
    assert blocked.status_code == 429
    assert json.loads(blocked.body)["daily_limit"] == 10
    state = json.loads(log_path.read_text(encoding="utf-8"))
    assert state["2001:db8::10"] == 10


def test_download_quota_ignores_head_and_download_metric_beacon(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "download_daily.json"
    monkeypatch.setattr(rate_limiter_module, "_DOWNLOAD_LOG_PATH", log_path)
    monkeypatch.setattr(
        rate_limiter_module,
        "_DOWNLOAD_LOG_LOCK_PATH",
        tmp_path / "download_daily.lock",
    )
    limiter = RateLimiter()
    book_id = "AbCdEfGhIjKlMnOpQrStUv"
    download = f"/api/v1/books/{book_id}/download"
    metric = f"/api/v1/books/{book_id}/metrics/download"

    assert limiter.check(_request(download, method="HEAD")) is None
    assert limiter.check(_request(metric, method="POST")) is None
    assert not log_path.exists()


def test_client_ip_skips_invalid_forwarded_values() -> None:
    request = _request(
        "/api/v1/home",
        headers=(
            (b"cf-connecting-ip", b"not-an-ip"),
            (b"x-forwarded-for", b"2001:0db8:0:0::20, 172.64.0.1"),
        ),
    )

    assert RateLimiter()._get_client_ip(request) == "2001:db8::20"


def test_nginx_public_reader_location_has_no_request_or_connection_limit() -> None:
    nginx = (
        Path(__file__).resolve().parents[1] / "deploy" / "nginx-oohstory.conf"
    ).read_text(encoding="utf-8")
    marker = (
        'location ~ "^/api/v1/books/[A-Za-z0-9_-]{22}'
        '(?:/cover|/chapters(?:/[1-9][0-9]*)?|/illustrations/.+)?$"'
    )
    reader_location = nginx.split(marker, 1)[1].split("\n    location ", 1)[0]

    assert "if ($request_method !~ ^(GET|HEAD)$)" in reader_location
    assert "limit_req " not in reader_location
    assert "limit_conn " not in reader_location
    assert "zone=ohhstory_chapter" not in nginx
