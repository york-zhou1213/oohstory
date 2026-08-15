from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
import time
from uuid import uuid4

from .main import repository, settings


def _cleanup_database() -> dict[str, int]:
    mysql = getattr(repository(), "_mysql", None)
    if mysql is None:
        return {}
    counts: dict[str, int] = {}
    statements = (
        (
            "session_links",
            "DELETE FROM audiobook_session_manifests WHERE session_id IN ("
            "SELECT session_id FROM (SELECT session_id FROM audiobook_sessions "
            "WHERE cancelled=1 OR expires_at<UTC_TIMESTAMP(6) LIMIT 500) stale_sessions)",
        ),
        (
            "sessions",
            "DELETE FROM audiobook_sessions WHERE cancelled=1 "
            "OR expires_at<UTC_TIMESTAMP(6) LIMIT 500",
        ),
        (
            "tts_leases",
            "DELETE FROM audiobook_tts_leases WHERE expires_at<UTC_TIMESTAMP(6) LIMIT 500",
        ),
        (
            "progress",
            "DELETE FROM audiobook_progress WHERE updated_at<DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 365 DAY) LIMIT 500",
        ),
        (
            "device_progress",
            "DELETE FROM audiobook_device_progress WHERE updated_at<DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 365 DAY) LIMIT 500",
        ),
        (
            "manifests",
            "DELETE FROM audiobook_chapter_manifests WHERE manifest_hash IN ("
            "SELECT manifest_hash FROM (SELECT m.manifest_hash "
            "FROM audiobook_chapter_manifests m "
            "LEFT JOIN audiobook_session_manifests sm ON sm.manifest_hash=m.manifest_hash "
            "WHERE sm.manifest_hash IS NULL "
            "AND m.last_accessed_at<DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 30 DAY) "
            "LIMIT 500) candidates)",
        ),
    )
    with mysql.pool.transaction() as connection:
        with connection.cursor() as cursor:
            for label, sql in statements:
                cursor.execute(sql)
                counts[label] = int(cursor.rowcount)
    return counts


def _cleanup_audio_jobs(limit: int = 500) -> int:
    mysql = getattr(repository(), "_mysql", None)
    if mysql is None:
        return 0
    root = (settings.audiobook_storage_root / "tts-audio-cache").resolve()
    with mysql.pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT LOWER(HEX(segment_hash)) AS segment_hash,cache_object_key "
                "FROM audiobook_audio_jobs WHERE "
                "expires_at<UTC_TIMESTAMP(6) ORDER BY expires_at LIMIT %s",
                (int(limit),),
            )
            rows = list(cursor.fetchall())
    removed = 0
    for row in rows:
        key = str(row.get("segment_hash") or "")
        if len(key) != 64:
            continue
        relative = str(row.get("cache_object_key") or f"{key[:2]}/{key}.mp3")
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            continue
        lock_path = root / key[:2] / f"{key}.lock"
        descriptor = None
        tombstone: Path | None = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if target.is_file():
                tombstone = target.with_name(f".{target.name}.{uuid4().hex}.gc")
                os.replace(target, tombstone)
            with mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM audiobook_audio_jobs WHERE segment_hash=UNHEX(%s) "
                        "AND expires_at<UTC_TIMESTAMP(6)",
                        (key,),
                    )
                    deleted = cursor.rowcount == 1
            if deleted:
                if tombstone is not None:
                    tombstone.unlink(missing_ok=True)
                removed += 1
            elif tombstone is not None:
                os.replace(tombstone, target)
        except (FileNotFoundError, BlockingIOError, OSError):
            if tombstone is not None and tombstone.exists() and not target.exists():
                try:
                    os.replace(tombstone, target)
                except OSError:
                    pass
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)
    return removed


def _bounded_files(root: Path, suffix: str, *, older_than: float, limit: int = 1000):
    if not root.is_dir():
        return
    seen = 0
    for directory, _children, names in os.walk(root):
        for name in names:
            if not name.endswith(suffix):
                continue
            path = Path(directory) / name
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mtime >= older_than:
                continue
            yield path
            seen += 1
            if seen >= limit:
                return


def _cleanup_receipts(now: float) -> int:
    removed = 0
    root = settings.audiobook_storage_root / "audiobook-stream-receipts"
    for suffix in (".complete", ".intent", ".cursor", ".cursor.lock"):
        for path in _bounded_files(root, suffix, older_than=now - 3600):
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
            try:
                path.parent.rmdir()
            except OSError:
                pass
    return removed


def _cleanup_stale_locks(now: float) -> int:
    removed = 0
    root = settings.audiobook_storage_root / "tts-audio-cache"
    for path in _bounded_files(root, ".lock", older_than=now - 86400):
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            opened = os.fstat(descriptor)
            current = path.lstat()
            if opened.st_ino == current.st_ino and opened.st_dev == current.st_dev:
                path.unlink()
                removed += 1
        except (FileNotFoundError, BlockingIOError, OSError):
            pass
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)
    return removed


def _cleanup_chapter_cache(now: float) -> int:
    removed = 0
    root = settings.audiobook_storage_root / "audiobook-chapter-stream-cache"
    for path in _bounded_files(root, ".mp3", older_than=now - 6 * 3600):
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    return removed


def main() -> None:
    now = time.time()
    counts = _cleanup_database()
    counts["audio_jobs"] = _cleanup_audio_jobs()
    counts["receipts"] = _cleanup_receipts(now)
    counts["locks"] = _cleanup_stale_locks(now)
    counts["chapter_cache"] = _cleanup_chapter_cache(now)
    print(" ".join(f"{key}={value}" for key, value in sorted(counts.items())))


if __name__ == "__main__":
    main()
