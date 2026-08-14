from __future__ import annotations

from pathlib import Path

from app.reader_abuse import ReaderAbuseGuard, ReaderProbeSigner


def test_recent_limit_counts_unique_chapters_only(tmp_path: Path) -> None:
    guard = ReaderAbuseGuard(
        tmp_path / "guard.sqlite3",
        recent_limit=3,
        daily_limit=20,
    )
    now = 1_786_709_000

    for chapter in ("book:1", "book:2", "book:3"):
        assert guard.check_chapter("203.0.113.10", chapter, now=now) is None
    assert guard.check_chapter("203.0.113.10", "book:1", now=now) is None

    blocked = guard.check_chapter("203.0.113.10", "book:4", now=now)
    assert blocked is not None
    assert blocked.reason == "chapter traversal velocity"
    assert blocked.retry_after == 600


def test_daily_limit_is_shared_by_new_guard_instances(tmp_path: Path) -> None:
    database = tmp_path / "guard.sqlite3"
    first = ReaderAbuseGuard(database, recent_limit=100, daily_limit=3)
    second = ReaderAbuseGuard(database, recent_limit=100, daily_limit=3)
    now = 1_786_709_000

    for chapter in ("book:1", "book:2", "book:3"):
        assert first.check_chapter("203.0.113.20", chapter, now=now) is None

    blocked = second.check_chapter("203.0.113.20", "book:4", now=now)
    assert blocked is not None
    assert blocked.reason == "daily chapter limit"
    assert blocked.retry_after > 0


def test_two_honeypot_hits_persist_and_ban_future_chapters(tmp_path: Path) -> None:
    database = tmp_path / "guard.sqlite3"
    first = ReaderAbuseGuard(database)
    second = ReaderAbuseGuard(database)
    now = 1_786_709_000

    assert first.record_trap("203.0.113.30", now=now) is None
    trapped = second.record_trap("203.0.113.30", now=now + 1)
    assert trapped is not None
    assert trapped.reason == "honeypot"

    blocked = ReaderAbuseGuard(database).check_chapter(
        "203.0.113.30",
        "book:1",
        now=now + 2,
    )
    assert blocked is not None
    assert blocked.reason == "honeypot"


def test_probe_token_is_short_lived_and_bound_to_ip(tmp_path: Path) -> None:
    signer = ReaderProbeSigner(tmp_path / "probe.key")
    now = 1_786_709_000
    token = signer.mint("203.0.113.40", "A" * 22, 7, now=now)

    assert signer.validate("203.0.113.40", "A" * 22, 7, token, now=now)
    assert signer.validate("203.0.113.40", "A" * 22, 7, token, now=now + 3600)
    assert not signer.validate("203.0.113.41", "A" * 22, 7, token, now=now)
    assert not signer.validate("203.0.113.40", "A" * 22, 7, token, now=now + 7200)
