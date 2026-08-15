"""Reader identities, revocable sessions, and private cross-device state.

The public catalog remains read-only.  Reader-owned data lives in a separate
SQLite database under the systemd state directory, so a compromised account
write path cannot mutate catalog or book tables.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from email_validator import EmailNotValidError, validate_email

from .comment_moderation import moderate_comment, moderate_display_name
from .point_pricing import (
    MAX_DOWNLOAD_POINT_UNITS,
    POINT_SCALE,
    deconstruction_reward_units,
    point_label,
    point_units,
    point_value,
)


PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)
ALLOWED_CLIENTS = {"web", "android", "ios"}
ALLOWED_UPLOAD_SUFFIXES = {".zip"}
ALLOWED_NOVEL_SUFFIXES = {".txt", ".epub"}
INVITE_CODE_PREFIX = "OOH-"
READING_LEVELS: tuple[tuple[str, str, int], ...] = (
    ("Ⅰ", "只如初见", 0),
    ("Ⅱ", "此去经年", 30),
    ("Ⅲ", "素心相赠", 100),
    ("Ⅳ", "犹故人归", 250),
    ("Ⅴ", "踏歌寻醉", 500),
    ("Ⅵ", "冷暖自知", 1_000),
    ("Ⅶ", "青青子衿", 1_800),
    ("Ⅷ", "似水流年", 3_000),
    ("Ⅸ", "不诉离殇", 5_000),
    ("Ⅹ", "近月侵衣", 8_000),
    ("Ⅺ", "对酒当歌", 12_000),
    ("Ⅻ", "长风万里", 18_000),
    ("ⅩⅢ", "知与谁同", 26_000),
    ("ⅩⅣ", "扶摇九霄", 36_000),
    ("ⅩⅤ", "凌云绝顶", 48_000),
    ("ⅩⅥ", "摘星揽月", 62_000),
    ("ⅩⅦ", "天人合一", 80_000),
    ("ⅩⅧ", "水月镜花", 100_000),
)
RECOMMENDATION_COST_SECONDS = 3_600
POINT_READING_SECONDS = 3_600
DECONSTRUCTION_TASK_TTL_DAYS = 7
MAX_ACTIVE_DECONSTRUCTION_TASKS = 10
RECOMMENDATION_EVENT_NAMESPACE = uuid.UUID("09abdf73-f981-45bb-91ef-7317c610281f")


class AccountError(ValueError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat(timespec="seconds")


def token_hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def normalize_email(value: str) -> str:
    candidate = unicodedata.normalize("NFKC", str(value or "")).strip()
    try:
        result = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError as exc:
        raise AccountError("请输入有效的电子邮箱") from exc
    return result.normalized.casefold()


def clean_display_name(value: str, *, fallback: str = "读者") -> str:
    cleaned = " ".join(
        unicodedata.normalize("NFKC", str(value or "")).replace("\x00", " ").split()
    )
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > 40:
        raise AccountError("昵称不能超过 40 个字符")
    moderation = moderate_display_name(cleaned)
    if not moderation.allowed:
        raise AccountError(moderation.detail, 422)
    return cleaned


def validate_password(value: str, *, email: str = "") -> str:
    password = str(value or "")
    if len(password) < 12 or len(password) > 128:
        raise AccountError("密码长度必须为 12–128 个字符")
    if password.casefold() in {"123456789012", "password1234", email.casefold()}:
        raise AccountError("密码过于简单，请使用不重复的长密码")
    categories = sum(
        (
            any(char.islower() for char in password),
            any(char.isupper() for char in password),
            any(char.isdigit() for char in password),
            any(not char.isalnum() for char in password),
        )
    )
    if categories < 3:
        raise AccountError("密码需包含大小写字母、数字或符号中的至少三类")
    return password


def clean_profile_text(value: str, *, field: str, max_length: int) -> str:
    cleaned = (
        unicodedata.normalize("NFKC", str(value or "")).replace("\x00", "").strip()
    )
    if len(cleaned) > max_length:
        raise AccountError(f"{field}不能超过 {max_length} 个字符")
    return cleaned


def reading_level_summary(active_seconds: int) -> dict[str, Any]:
    total = max(0, int(active_seconds))
    hours = total / 3600
    index = 0
    for candidate, (_roman, _name, threshold) in enumerate(READING_LEVELS):
        if total >= threshold * 3600:
            index = candidate
        else:
            break
    roman, name, threshold = READING_LEVELS[index]
    is_max = index == len(READING_LEVELS) - 1
    next_threshold = None if is_max else READING_LEVELS[index + 1][2]
    if next_threshold is None:
        seconds_to_next = 0
        progress = 1.0
    else:
        seconds_to_next = max(0, next_threshold * 3600 - total)
        span = max(1, (next_threshold - threshold) * 3600)
        progress = min(1.0, max(0.0, (total - threshold * 3600) / span))
    return {
        "level": index + 1,
        "roman": roman,
        "name": name,
        "threshold_hours": threshold,
        "active_seconds": total,
        "active_minutes": total // 60,
        "active_hours": round(hours, 2),
        "next_threshold_hours": next_threshold,
        "seconds_to_next": seconds_to_next,
        "minutes_to_next": None if is_max else (seconds_to_next + 59) // 60,
        "hours_to_next": None if is_max else round(seconds_to_next / 3600, 2),
        "progress": round(progress, 6),
        "is_max": is_max,
    }


@dataclass(frozen=True)
class SessionContext:
    session_id: str
    user_id: str
    email: str
    display_name: str
    email_verified: bool
    google_linked: bool
    role: str
    client: str
    csrf_hash: bytes
    expires_at: str
    token: str = ""
    csrf_token: str = ""

    def public_user(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "email_verified": self.email_verified,
            "google_linked": self.google_linked,
            "role": self.role,
        }


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  password_hash TEXT,
  email_verified_at TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS managed_categories (
  id TEXT PRIMARY KEY,
  source_name TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  sort_order INTEGER NOT NULL DEFAULT 100,
  is_custom INTEGER NOT NULL DEFAULT 0 CHECK(is_custom IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_managed_categories_visible
  ON managed_categories(enabled,sort_order,display_name);

CREATE TABLE IF NOT EXISTS admin_audit_events (
  id TEXT PRIMARY KEY,
  actor_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created
  ON admin_audit_events(created_at DESC);

CREATE TABLE IF NOT EXISTS user_identities (
  provider TEXT NOT NULL,
  subject TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider_email TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_login_at TEXT NOT NULL,
  PRIMARY KEY(provider, subject)
);

CREATE TABLE IF NOT EXISTS user_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash BLOB NOT NULL UNIQUE,
  csrf_hash BLOB NOT NULL,
  client TEXT NOT NULL,
  user_agent_hash BLOB,
  ip_hash BLOB,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_sessions_lookup
  ON user_sessions(token_hash, expires_at, revoked_at);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
  token_hash BLOB PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_states (
  state_hash BLOB PRIMARY KEY,
  code_verifier TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS google_link_tokens (
  token_hash BLOB PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_google_link_tokens_user
  ON google_link_tokens(user_id, expires_at, used_at);

CREATE TABLE IF NOT EXISTS auth_rate_limits (
  key_hash BLOB PRIMARY KEY,
  window_started_at TEXT NOT NULL,
  attempts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS registration_invites (
  id TEXT PRIMARY KEY,
  code_hash BLOB NOT NULL UNIQUE,
  label TEXT NOT NULL DEFAULT '',
  max_uses INTEGER NOT NULL CHECK(max_uses >= 1 AND max_uses <= 100000),
  used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0),
  created_at TEXT NOT NULL,
  expires_at TEXT,
  disabled_at TEXT,
  last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS registration_invite_redemptions (
  invite_id TEXT NOT NULL REFERENCES registration_invites(id) ON DELETE RESTRICT,
  user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  redeemed_at TEXT NOT NULL,
  PRIMARY KEY(invite_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_registration_invites_status
  ON registration_invites(disabled_at, expires_at, used_count, max_uses);

CREATE TABLE IF NOT EXISTS user_reading_history (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id TEXT NOT NULL,
  chapter_id INTEGER NOT NULL DEFAULT 1,
  progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 1),
  title TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  cover_url TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, book_id)
);

CREATE TABLE IF NOT EXISTS user_favorites (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  cover_url TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, book_id)
);
CREATE INDEX IF NOT EXISTS idx_user_favorites_book
  ON user_favorites(book_id,user_id);

CREATE TABLE IF NOT EXISTS user_bookshelf (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  cover_url TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, book_id)
);

CREATE TABLE IF NOT EXISTS user_profiles (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  bio TEXT NOT NULL DEFAULT '',
  gender TEXT NOT NULL DEFAULT '' CHECK(gender IN ('','female','male','nonbinary','prefer_not_say')),
  birthday TEXT,
  location TEXT NOT NULL DEFAULT '',
  avatar_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_reading_totals (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  active_seconds INTEGER NOT NULL DEFAULT 0 CHECK(active_seconds >= 0),
  last_heartbeat_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_reading_heartbeats (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  event_id TEXT NOT NULL,
  book_id TEXT NOT NULL,
  claimed_seconds INTEGER NOT NULL,
  accepted_seconds INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id,event_id)
);
CREATE INDEX IF NOT EXISTS idx_user_reading_heartbeats_created
  ON user_reading_heartbeats(user_id,created_at DESC);

CREATE TABLE IF NOT EXISTS user_book_recommendations (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  spent_seconds INTEGER NOT NULL DEFAULT 3600 CHECK(spent_seconds = 3600),
  metric_applied INTEGER NOT NULL DEFAULT 0 CHECK(metric_applied IN (0,1)),
  created_at TEXT NOT NULL,
  applied_at TEXT,
  UNIQUE(user_id,request_id)
);
CREATE INDEX IF NOT EXISTS idx_user_book_recommendations_pending
  ON user_book_recommendations(metric_applied,created_at);
CREATE INDEX IF NOT EXISTS idx_user_book_recommendations_user_book
  ON user_book_recommendations(user_id,book_id,created_at DESC);

CREATE TABLE IF NOT EXISTS security_audit_events (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  event TEXT NOT NULL,
  outcome TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_security_audit_user_created
  ON security_audit_events(user_id,created_at DESC);

CREATE TABLE IF NOT EXISTS deconstruction_uploads (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  original_filename TEXT NOT NULL,
  stored_filename TEXT,
  bytes INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT,
  media_type TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  scanner_engine TEXT,
  scanner_result TEXT,
  rejection_reason TEXT,
  created_at TEXT NOT NULL,
  scanned_at TEXT,
  queued_at TEXT,
  completed_at TEXT,
  output_slug TEXT,
  task_id TEXT,
  download_points INTEGER NOT NULL DEFAULT 0 CHECK(download_points BETWEEN 0 AND 999),
  download_point_units INTEGER NOT NULL DEFAULT 0
    CHECK(download_point_units BETWEEN 0 AND 99900),
  reward_point_units INTEGER NOT NULL DEFAULT 0
    CHECK(reward_point_units BETWEEN 0 AND 99900),
  cover_override_path TEXT,
  cover_override_version INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_upload_user_history
  ON deconstruction_uploads(user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_upload_clean_digest
  ON deconstruction_uploads(sha256)
  WHERE sha256 IS NOT NULL AND status IN ('ai_pending','reviewing','approved','completed');

CREATE TABLE IF NOT EXISTS deconstruction_download_metrics (
  slug TEXT PRIMARY KEY,
  download_count INTEGER NOT NULL DEFAULT 0 CHECK(download_count >= 0),
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deconstruction_tasks (
  id TEXT PRIMARY KEY,
  creator_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_title TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  request_note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','claimed','submitted','completed','cancelled','expired')),
  claimed_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  submission_id TEXT,
  output_slug TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  claimed_at TEXT,
  submitted_at TEXT,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_deconstruction_tasks_status_expiry
  ON deconstruction_tasks(status,expires_at,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deconstruction_tasks_creator
  ON deconstruction_tasks(creator_user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deconstruction_tasks_claimer
  ON deconstruction_tasks(claimed_by_user_id,created_at DESC);

CREATE TABLE IF NOT EXISTS user_point_wallets (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
  balance_units INTEGER NOT NULL DEFAULT 0 CHECK(balance_units >= 0),
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_point_ledger (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  delta INTEGER NOT NULL,
  balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
  delta_units INTEGER NOT NULL DEFAULT 0,
  balance_after_units INTEGER NOT NULL DEFAULT 0 CHECK(balance_after_units >= 0),
  kind TEXT NOT NULL,
  reference_type TEXT NOT NULL DEFAULT '',
  reference_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(user_id,kind,reference_type,reference_id)
);
CREATE INDEX IF NOT EXISTS idx_point_ledger_user_created
  ON user_point_ledger(user_id,created_at DESC);

CREATE TABLE IF NOT EXISTS deconstruction_products (
  slug TEXT PRIMARY KEY,
  contributor_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  task_id TEXT,
  download_points INTEGER NOT NULL DEFAULT 0 CHECK(download_points BETWEEN 0 AND 999),
  download_point_units INTEGER NOT NULL DEFAULT 0
    CHECK(download_point_units BETWEEN 0 AND 99900),
  published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deconstruction_purchases (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slug TEXT NOT NULL,
  points_spent INTEGER NOT NULL CHECK(points_spent > 0),
  point_units_spent INTEGER NOT NULL DEFAULT 0 CHECK(point_units_spent >= 0),
  purchased_at TEXT NOT NULL,
  PRIMARY KEY(user_id,slug)
);

CREATE TABLE IF NOT EXISTS deconstruction_likes (
  slug TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY(slug,user_id)
);
CREATE INDEX IF NOT EXISTS idx_deconstruction_likes_slug
  ON deconstruction_likes(slug,created_at DESC);

CREATE TABLE IF NOT EXISTS novel_submissions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  author TEXT NOT NULL,
  category TEXT NOT NULL,
  serialization_status TEXT NOT NULL CHECK(serialization_status IN ('ongoing','finished')),
  summary TEXT NOT NULL,
  source TEXT NOT NULL,
  authorization TEXT NOT NULL,
  manuscript_filename TEXT NOT NULL,
  manuscript_path TEXT,
  cover_path TEXT,
  bytes INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT,
  status TEXT NOT NULL,
  scanner_result TEXT,
  review_result TEXT,
  rejection_reason TEXT,
  handoff_manifest TEXT,
  catalog_id TEXT,
  public_id TEXT,
  cover_override_path TEXT,
  cover_override_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  reviewed_at TEXT,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_novel_submission_user_created
  ON novel_submissions(user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_novel_submission_queue
  ON novel_submissions(status,created_at);

CREATE TABLE IF NOT EXISTS user_notifications (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  action_url TEXT NOT NULL DEFAULT '',
  resource_type TEXT NOT NULL DEFAULT '',
  resource_id TEXT NOT NULL DEFAULT '',
  dedupe_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  read_at TEXT,
  UNIQUE(user_id,dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
  ON user_notifications(user_id,read_at,created_at DESC);

CREATE TABLE IF NOT EXISTS paragraph_comments (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id TEXT NOT NULL,
  chapter_id INTEGER NOT NULL CHECK(chapter_id > 0),
  paragraph_index INTEGER NOT NULL CHECK(paragraph_index >= 0),
  paragraph_key TEXT NOT NULL,
  paragraph_excerpt TEXT NOT NULL,
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'visible' CHECK(status IN ('visible','hidden')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paragraph_comments_chapter
  ON paragraph_comments(book_id,chapter_id,paragraph_key,status,created_at,id);
CREATE INDEX IF NOT EXISTS idx_paragraph_comments_user
  ON paragraph_comments(user_id,created_at DESC);

CREATE TABLE IF NOT EXISTS paragraph_comment_thanks (
  comment_id TEXT NOT NULL REFERENCES paragraph_comments(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  like_count INTEGER NOT NULL DEFAULT 1 CHECK(like_count BETWEEN 1 AND 3),
  created_at TEXT NOT NULL,
  PRIMARY KEY(comment_id,user_id)
);
CREATE INDEX IF NOT EXISTS idx_paragraph_comment_thanks_user
  ON paragraph_comment_thanks(user_id,created_at DESC);
"""


class AccountStore:
    def __init__(self, path: Path, *, session_ttl_seconds: int) -> None:
        self.path = Path(path).expanduser().resolve()
        self.session_ttl_seconds = int(session_ttl_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
        os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Additive migrations for account databases created by older releases."""
        connection.execute("BEGIN IMMEDIATE")
        try:
            recommendation_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(user_book_recommendations)"
                )
            }
            if recommendation_columns and "id" not in recommendation_columns:
                legacy_rows = connection.execute(
                    "SELECT user_id,book_id,spent_seconds,metric_applied,"
                    "created_at,applied_at FROM user_book_recommendations"
                ).fetchall()
                connection.execute(
                    "DROP INDEX IF EXISTS idx_user_book_recommendations_pending"
                )
                connection.execute(
                    "DROP INDEX IF EXISTS idx_user_book_recommendations_user_book"
                )
                connection.execute(
                    "ALTER TABLE user_book_recommendations "
                    "RENAME TO user_book_recommendations_legacy"
                )
                connection.execute(
                    "CREATE TABLE user_book_recommendations ("
                    "id TEXT PRIMARY KEY,"
                    "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
                    "book_id TEXT NOT NULL,"
                    "request_id TEXT NOT NULL,"
                    "spent_seconds INTEGER NOT NULL DEFAULT 3600 "
                    "CHECK(spent_seconds = 3600),"
                    "metric_applied INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(metric_applied IN (0,1)),"
                    "created_at TEXT NOT NULL,"
                    "applied_at TEXT,"
                    "UNIQUE(user_id,request_id))"
                )
                for row in legacy_rows:
                    request_id = str(
                        uuid.uuid5(
                            RECOMMENDATION_EVENT_NAMESPACE,
                            f"legacy:{row['user_id']}:{row['book_id']}",
                        )
                    )
                    event_id = str(
                        uuid.uuid5(
                            RECOMMENDATION_EVENT_NAMESPACE,
                            f"{row['user_id']}:{request_id}",
                        )
                    )
                    connection.execute(
                        "INSERT INTO user_book_recommendations"
                        "(id,user_id,book_id,request_id,spent_seconds,metric_applied,"
                        "created_at,applied_at) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            event_id,
                            str(row["user_id"]),
                            str(row["book_id"]),
                            request_id,
                            int(row["spent_seconds"]),
                            int(row["metric_applied"]),
                            str(row["created_at"]),
                            row["applied_at"],
                        ),
                    )
                connection.execute("DROP TABLE user_book_recommendations_legacy")
                connection.execute(
                    "CREATE INDEX idx_user_book_recommendations_pending "
                    "ON user_book_recommendations(metric_applied,created_at)"
                )
                connection.execute(
                    "CREATE INDEX idx_user_book_recommendations_user_book "
                    "ON user_book_recommendations(user_id,book_id,created_at DESC)"
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        existing = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(deconstruction_uploads)")
        }
        columns = {
            "structure_profile": "TEXT",
            "structure_report": "TEXT",
            "review_result": "TEXT",
            "reviewed_at": "TEXT",
            "handoff_manifest": "TEXT",
            "review_attempts": "INTEGER NOT NULL DEFAULT 0",
            "task_id": "TEXT",
            "download_points": "INTEGER NOT NULL DEFAULT 0",
            "download_point_units": "INTEGER NOT NULL DEFAULT 0",
            "reward_point_units": "INTEGER NOT NULL DEFAULT 0",
            "cover_override_path": "TEXT",
            "cover_override_version": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, ddl in columns.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE deconstruction_uploads ADD COLUMN {name} {ddl}"
                )
        novel_existing = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(novel_submissions)")
        }
        novel_columns = {
            "catalog_id": "TEXT",
            "public_id": "TEXT",
            "cover_override_path": "TEXT",
            "cover_override_version": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, ddl in novel_columns.items():
            if name not in novel_existing:
                connection.execute(
                    f"ALTER TABLE novel_submissions ADD COLUMN {name} {ddl}"
                )
        reaction_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(paragraph_comment_thanks)")
        }
        if "like_count" not in reaction_columns:
            connection.execute(
                "ALTER TABLE paragraph_comment_thanks ADD COLUMN like_count "
                "INTEGER NOT NULL DEFAULT 1 CHECK(like_count BETWEEN 1 AND 3)"
            )
        connection.execute("DROP INDEX IF EXISTS idx_upload_clean_digest")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_upload_clean_digest "
            "ON deconstruction_uploads(sha256) WHERE sha256 IS NOT NULL "
            "AND status IN ('ai_pending','reviewing','approved','completed')"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_upload_active_task "
            "ON deconstruction_uploads(task_id) WHERE task_id IS NOT NULL "
            "AND status!='rejected'"
        )
        user_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(users)")
        }
        if "role" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
            )
        connection.execute(
            "UPDATE users SET role='user' WHERE role NOT IN ('user','admin','owner')"
        )
        # Public registrations must always remain ordinary users unless an
        # operator role was provisioned through a separate, explicit process.
        # Never infer administrative access from user count or verification.
        unit_columns = {
            "user_point_wallets": {
                "balance_units": "INTEGER NOT NULL DEFAULT 0",
            },
            "user_point_ledger": {
                "delta_units": "INTEGER NOT NULL DEFAULT 0",
                "balance_after_units": "INTEGER NOT NULL DEFAULT 0",
            },
            "deconstruction_products": {
                "download_point_units": "INTEGER NOT NULL DEFAULT 0",
            },
            "deconstruction_purchases": {
                "point_units_spent": "INTEGER NOT NULL DEFAULT 0",
            },
        }
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table, additions in unit_columns.items():
                current = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                for name, ddl in additions.items():
                    if name not in current:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                        )
            connection.execute(
                "UPDATE deconstruction_uploads SET download_point_units="
                "CAST(ROUND(download_points*100) AS INTEGER) "
                "WHERE download_point_units=0 AND COALESCE(download_points,0)!=0"
            )
            connection.execute(
                "UPDATE deconstruction_products SET download_point_units="
                "CAST(ROUND(download_points*100) AS INTEGER) "
                "WHERE download_point_units=0 AND COALESCE(download_points,0)!=0"
            )
            connection.execute(
                "UPDATE deconstruction_purchases SET point_units_spent="
                "CAST(ROUND(points_spent*100) AS INTEGER) "
                "WHERE point_units_spent=0 AND COALESCE(points_spent,0)!=0"
            )
            connection.execute(
                "UPDATE user_point_wallets SET balance_units="
                "CAST(ROUND(balance*100) AS INTEGER) "
                "WHERE balance_units=0 AND COALESCE(balance,0)!=0"
            )
            connection.execute(
                "UPDATE user_point_ledger SET delta_units="
                "CAST(ROUND(delta*100) AS INTEGER) "
                "WHERE delta_units=0 AND COALESCE(delta,0)!=0"
            )
            connection.execute(
                "UPDATE user_point_ledger SET balance_after_units="
                "CAST(ROUND(balance_after*100) AS INTEGER) "
                "WHERE balance_after_units=0 AND COALESCE(balance_after,0)!=0"
            )
            for row in connection.execute(
                "SELECT id,structure_report FROM deconstruction_uploads "
                "WHERE reward_point_units=0 AND structure_report IS NOT NULL "
                "AND structure_report!=''"
            ):
                try:
                    structure = json.loads(str(row["structure_report"]))
                    reward_units = int(
                        structure.get("reward_point_units")
                        or structure.get("download_point_units")
                        or deconstruction_reward_units(
                            int(structure.get("original_text_char_count") or 0)
                        )
                    )
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if 0 < reward_units <= MAX_DOWNLOAD_POINT_UNITS:
                    connection.execute(
                        "UPDATE deconstruction_uploads SET reward_point_units=? "
                        "WHERE id=? AND reward_point_units=0",
                        (reward_units, row["id"]),
                    )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> dict[str, Any]:
        password_login_enabled = False
        if "password_login_enabled" in row.keys():
            password_login_enabled = bool(row["password_login_enabled"])
        elif "password_hash" in row.keys():
            password_login_enabled = bool(row["password_hash"])
        return {
            "id": row["id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "email_verified": bool(row["email_verified_at"]),
            "google_linked": bool(row["google_linked"])
            if "google_linked" in row.keys()
            else False,
            "password_login_enabled": password_login_enabled,
            "role": str(row["role"] if "role" in row.keys() else "user"),
        }

    def google_linked(self, user_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM user_identities "
                "WHERE provider='google' AND user_id=? LIMIT 1",
                (str(user_id),),
            ).fetchone()
        return row is not None

    def login_methods(self, user_id: str) -> dict[str, bool]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_hash,"
                "EXISTS(SELECT 1 FROM user_identities i WHERE i.provider='google' "
                "AND i.user_id=u.id) AS google_linked "
                "FROM users u WHERE u.id=? AND u.status='active'",
                (str(user_id),),
            ).fetchone()
        if not row:
            raise AccountError("账户不存在", 404)
        return {
            "google": bool(row["google_linked"]),
            "password": bool(row["password_hash"]),
        }

    def enforce_rate_limit(
        self,
        key: str,
        *,
        limit: int = 8,
        window: int = 900,
        cost: int = 1,
    ) -> None:
        if limit <= 0 or window <= 0 or cost <= 0:
            raise ValueError("rate limit values must be positive")
        digest = token_hash(key)
        now = utcnow()
        cutoff = now - timedelta(seconds=window)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT window_started_at,attempts FROM auth_rate_limits WHERE key_hash=?",
                (digest,),
            ).fetchone()
            if row and datetime.fromisoformat(row["window_started_at"]) > cutoff:
                attempts = int(row["attempts"]) + cost
                if attempts > limit:
                    connection.rollback()
                    raise AccountError("尝试次数过多，请稍后再试", 429)
                connection.execute(
                    "UPDATE auth_rate_limits SET attempts=? WHERE key_hash=?",
                    (attempts, digest),
                )
            else:
                connection.execute(
                    "INSERT INTO auth_rate_limits(key_hash,window_started_at,attempts) "
                    "VALUES(?,?,?) ON CONFLICT(key_hash) DO UPDATE SET "
                    "window_started_at=excluded.window_started_at,attempts=excluded.attempts",
                    (digest, iso(now), cost),
                )
            connection.commit()

    @staticmethod
    def _clean_invite_code(value: str) -> str:
        code = unicodedata.normalize("NFKC", str(value or "")).strip()
        if len(code) < 20 or len(code) > 128:
            raise AccountError("邀请码无效或已失效", 403)
        return code

    def create_invite(
        self,
        *,
        label: str = "",
        max_uses: int = 1,
        expires_in_days: int = 30,
    ) -> tuple[str, dict[str, Any]]:
        label = " ".join(unicodedata.normalize("NFKC", str(label or "")).split())
        if len(label) > 80:
            raise AccountError("邀请码备注不能超过 80 个字符")
        if not 1 <= int(max_uses) <= 100_000:
            raise AccountError("邀请码使用次数必须在 1–100000 之间")
        if not 1 <= int(expires_in_days) <= 365:
            raise AccountError("邀请码有效期必须在 1–365 天之间")
        invite_id = str(uuid.uuid4())
        code = f"{INVITE_CODE_PREFIX}{secrets.token_urlsafe(24)}"
        now = utcnow()
        expires_at = iso(now + timedelta(days=int(expires_in_days)))
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO registration_invites"
                "(id,code_hash,label,max_uses,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                (
                    invite_id,
                    token_hash(code),
                    label,
                    int(max_uses),
                    iso(now),
                    expires_at,
                ),
            )
        return code, {
            "id": invite_id,
            "label": label,
            "max_uses": int(max_uses),
            "used_count": 0,
            "expires_at": expires_at,
            "disabled": False,
        }

    def list_invites(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,label,max_uses,used_count,created_at,expires_at,disabled_at,last_used_at "
                "FROM registration_invites ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) | {"disabled": bool(row["disabled_at"])} for row in rows]

    def revoke_invite(self, invite_id: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE registration_invites SET disabled_at=COALESCE(disabled_at,?) WHERE id=?",
                (iso(), str(invite_id)),
            )
        if result.rowcount != 1:
            raise AccountError("邀请码不存在", 404)

    def _consume_invite(
        self,
        connection: sqlite3.Connection,
        invite_code: str,
        *,
        user_id: str,
        now: str,
    ) -> None:
        code = self._clean_invite_code(invite_code)
        row = connection.execute(
            "SELECT id FROM registration_invites WHERE code_hash=? AND disabled_at IS NULL "
            "AND (expires_at IS NULL OR expires_at>?) AND used_count<max_uses",
            (token_hash(code), now),
        ).fetchone()
        if not row:
            raise AccountError("邀请码无效或已失效", 403)
        updated = connection.execute(
            "UPDATE registration_invites SET used_count=used_count+1,last_used_at=? "
            "WHERE id=? AND disabled_at IS NULL AND (expires_at IS NULL OR expires_at>?) "
            "AND used_count<max_uses",
            (now, row["id"], now),
        )
        if updated.rowcount != 1:
            raise AccountError("邀请码无效或已失效", 403)
        connection.execute(
            "INSERT INTO registration_invite_redemptions(invite_id,user_id,redeemed_at) "
            "VALUES(?,?,?)",
            (row["id"], user_id, now),
        )

    def register(
        self,
        email: str,
        password: str,
        display_name: str,
        invite_code: str,
    ) -> tuple[dict[str, Any], str]:
        email = normalize_email(email)
        password = validate_password(password, email=email)
        display_name = clean_display_name(display_name, fallback=email.split("@", 1)[0])
        now = iso()
        user_id = str(uuid.uuid4())
        verification = secrets.token_urlsafe(32)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO users(id,email,display_name,password_hash,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        user_id,
                        email,
                        display_name,
                        PASSWORD_HASHER.hash(password),
                        now,
                        now,
                    ),
                )
                if str(invite_code or "").strip():
                    self._consume_invite(
                        connection, invite_code, user_id=user_id, now=now
                    )
                connection.execute(
                    "INSERT INTO email_verification_tokens(token_hash,user_id,created_at,expires_at) "
                    "VALUES(?,?,?,?)",
                    (
                        token_hash(verification),
                        user_id,
                        now,
                        iso(utcnow() + timedelta(hours=24)),
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise AccountError("该邮箱已经注册", 409) from exc
        return {
            "id": user_id,
            "email": email,
            "display_name": display_name,
            "email_verified": False,
            "google_linked": False,
            "password_login_enabled": True,
            "role": "user",
        }, verification

    def verify_email(self, verification: str) -> dict[str, Any]:
        now = iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT user_id FROM email_verification_tokens "
                "WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
                (token_hash(verification), now),
            ).fetchone()
            if not row:
                connection.rollback()
                raise AccountError("验证链接无效或已过期", 400)
            connection.execute(
                "UPDATE email_verification_tokens SET used_at=? WHERE token_hash=?",
                (now, token_hash(verification)),
            )
            connection.execute(
                "UPDATE users SET email_verified_at=COALESCE(email_verified_at,?),updated_at=? "
                "WHERE id=?",
                (now, now, row["user_id"]),
            )
            user = connection.execute(
                "SELECT * FROM users WHERE id=?", (row["user_id"],)
            ).fetchone()
            connection.commit()
        return self._user_from_row(user)

    def create_verification_token(self, user_id: str) -> str:
        verification = secrets.token_urlsafe(32)
        now = iso()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT email_verified_at FROM users WHERE id=? AND status='active'",
                (user_id,),
            ).fetchone()
            if not row:
                raise AccountError("账户不存在", 404)
            if row["email_verified_at"]:
                raise AccountError("邮箱已经验证", 409)
            connection.execute(
                "UPDATE email_verification_tokens SET used_at=? "
                "WHERE user_id=? AND used_at IS NULL",
                (now, user_id),
            )
            connection.execute(
                "INSERT INTO email_verification_tokens(token_hash,user_id,created_at,expires_at) "
                "VALUES(?,?,?,?)",
                (
                    token_hash(verification),
                    user_id,
                    now,
                    iso(utcnow() + timedelta(hours=24)),
                ),
            )
        return verification

    def password_login(self, email: str, password: str) -> dict[str, Any]:
        email = normalize_email(email)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE email=? AND status='active'",
                (email,),
            ).fetchone()
        encoded = row["password_hash"] if row else None
        try:
            valid = bool(encoded) and PASSWORD_HASHER.verify(
                encoded, str(password or "")
            )
        except (InvalidHashError, VerifyMismatchError):
            valid = False
        if not valid or row is None:
            raise AccountError("邮箱或密码错误", 401)
        now = iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET last_login_at=?,updated_at=? WHERE id=?",
                (now, now, row["id"]),
            )
        return self._user_from_row(row)

    @staticmethod
    def _google_identity(claims: dict[str, Any]) -> tuple[str, str]:
        subject = str(claims.get("sub") or "").strip()
        email = normalize_email(str(claims.get("email") or ""))
        if (
            not subject
            or len(subject) > 255
            or "\x00" in subject
            or not claims.get("email_verified")
        ):
            raise AccountError("Google 账户邮箱尚未验证", 401)
        return subject, email

    @staticmethod
    def _google_display_name(claims: dict[str, Any], email: str) -> str:
        candidates = (
            str(claims.get("name") or ""),
            email.split("@", 1)[0],
            "读者",
        )
        for candidate in candidates:
            try:
                return clean_display_name(candidate)
            except AccountError:
                continue
        return "读者"

    @classmethod
    def _link_google_in_transaction(
        cls,
        connection: sqlite3.Connection,
        user_id: str,
        claims: dict[str, Any],
        *,
        now: str,
    ) -> dict[str, Any]:
        subject, email = cls._google_identity(claims)
        user = connection.execute(
            "SELECT * FROM users WHERE id=? AND status='active'",
            (str(user_id),),
        ).fetchone()
        if not user:
            raise AccountError("账户不存在或已停用", 403)
        if str(user["email"]).casefold() != email.casefold():
            raise AccountError("Google 邮箱必须与注册账户邮箱一致", 409)

        subject_owner = connection.execute(
            "SELECT user_id FROM user_identities WHERE provider='google' AND subject=?",
            (subject,),
        ).fetchone()
        if subject_owner and subject_owner["user_id"] != user_id:
            raise AccountError("该 Google 账户已绑定其他用户", 409)
        existing = connection.execute(
            "SELECT subject FROM user_identities "
            "WHERE provider='google' AND user_id=? LIMIT 1",
            (str(user_id),),
        ).fetchone()
        if existing and existing["subject"] != subject:
            raise AccountError("当前账户已绑定其他 Google 账户", 409)
        if not subject_owner:
            connection.execute(
                "INSERT INTO user_identities"
                "(provider,subject,user_id,provider_email,created_at,last_login_at) "
                "VALUES('google',?,?,?,?,?)",
                (subject, user_id, email, now, now),
            )
        else:
            connection.execute(
                "UPDATE user_identities SET provider_email=?,last_login_at=? "
                "WHERE provider='google' AND subject=?",
                (email, now, subject),
            )
        connection.execute(
            "UPDATE users SET email_verified_at=COALESCE(email_verified_at,?),"
            "updated_at=? WHERE id=?",
            (now, now, user_id),
        )
        row = connection.execute(
            "SELECT u.*,1 AS google_linked FROM users u WHERE u.id=?",
            (user_id,),
        ).fetchone()
        return cls._user_from_row(row)

    def google_login(self, claims: dict[str, Any]) -> dict[str, Any]:
        subject, email = self._google_identity(claims)
        now = iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            identity = connection.execute(
                "SELECT user_id FROM user_identities "
                "WHERE provider='google' AND subject=?",
                (subject,),
            ).fetchone()
            if identity:
                user_id = str(identity["user_id"])
                row = connection.execute(
                    "SELECT u.*,1 AS google_linked FROM users u "
                    "WHERE u.id=? AND u.status='active'",
                    (user_id,),
                ).fetchone()
                if not row:
                    connection.rollback()
                    raise AccountError("账户已被停用", 403)
                if str(row["email"]).casefold() != email.casefold():
                    if row["password_hash"]:
                        connection.rollback()
                        raise AccountError("Google 账户邮箱与绑定记录不一致", 409)
                    conflict = connection.execute(
                        "SELECT id FROM users WHERE email=? AND id<>? LIMIT 1",
                        (email, user_id),
                    ).fetchone()
                    if conflict:
                        connection.rollback()
                        raise AccountError("新的 Google 邮箱已被其他账户使用", 409)
                    connection.execute(
                        "UPDATE users SET email=?,updated_at=? WHERE id=?",
                        (email, now, user_id),
                    )
            else:
                email_owner = connection.execute(
                    "SELECT u.id,u.password_hash,"
                    "EXISTS(SELECT 1 FROM user_identities i "
                    "WHERE i.provider='google' AND i.user_id=u.id) AS google_linked "
                    "FROM users u WHERE u.email=? LIMIT 1",
                    (email,),
                ).fetchone()
                if email_owner:
                    connection.rollback()
                    if email_owner["google_linked"]:
                        raise AccountError("该邮箱已关联其他 Google 账户", 409)
                    raise AccountError(
                        "该邮箱已创建密码账户，请先用邮箱密码登录后在个人中心绑定 Google",
                        409,
                    )
                user_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO users"
                    "(id,email,display_name,password_hash,email_verified_at,status,"
                    "created_at,updated_at,last_login_at) "
                    "VALUES(?,?,?,NULL,?,'active',?,?,?)",
                    (
                        user_id,
                        email,
                        self._google_display_name(claims, email),
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO user_identities"
                    "(provider,subject,user_id,provider_email,created_at,last_login_at) "
                    "VALUES('google',?,?,?,?,?)",
                    (subject, user_id, email, now, now),
                )
                self._audit(connection, user_id, "google_account_create", "success")
            connection.execute(
                "UPDATE user_identities SET last_login_at=?,provider_email=? "
                "WHERE provider='google' AND subject=?",
                (now, email, subject),
            )
            connection.execute(
                "UPDATE users SET last_login_at=?,updated_at=? WHERE id=?",
                (now, now, user_id),
            )
            row = connection.execute(
                "SELECT u.*,1 AS google_linked FROM users u "
                "WHERE u.id=? AND u.status='active'",
                (user_id,),
            ).fetchone()
            connection.commit()
        return self._user_from_row(row)

    def link_google(self, user_id: str, claims: dict[str, Any]) -> dict[str, Any]:
        now = iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            user = self._link_google_in_transaction(
                connection,
                str(user_id),
                claims,
                now=now,
            )
            connection.commit()
        return user

    def create_google_link_token(self, user_id: str) -> str:
        raw_token = secrets.token_urlsafe(32)
        now = utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM users WHERE id=? AND status='active'",
                (str(user_id),),
            ).fetchone()
            if not active:
                connection.rollback()
                raise AccountError("账户不存在或已停用", 403)
            connection.execute(
                "UPDATE google_link_tokens SET used_at=? "
                "WHERE user_id=? AND used_at IS NULL",
                (iso(now), str(user_id)),
            )
            connection.execute(
                "INSERT INTO google_link_tokens"
                "(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                (
                    token_hash(raw_token),
                    str(user_id),
                    iso(now),
                    iso(now + timedelta(minutes=10)),
                ),
            )
            connection.commit()
        return raw_token

    def link_google_with_token(
        self,
        raw_token: str,
        claims: dict[str, Any],
    ) -> dict[str, Any]:
        now = iso()
        if not raw_token or len(raw_token) > 128:
            raise AccountError("Google 绑定请求无效或已过期", 403)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            link = connection.execute(
                "SELECT user_id FROM google_link_tokens "
                "WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
                (token_hash(raw_token), now),
            ).fetchone()
            if not link:
                connection.rollback()
                raise AccountError("Google 绑定请求无效或已过期", 403)
            user = self._link_google_in_transaction(
                connection,
                link["user_id"],
                claims,
                now=now,
            )
            updated = connection.execute(
                "UPDATE google_link_tokens SET used_at=? "
                "WHERE token_hash=? AND used_at IS NULL",
                (now, token_hash(raw_token)),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise AccountError("Google 绑定请求无效或已过期", 403)
            connection.commit()
        return user

    def create_session(
        self,
        user: dict[str, Any],
        *,
        client: str,
        user_agent: str = "",
        ip: str = "",
    ) -> SessionContext:
        if client not in ALLOWED_CLIENTS:
            raise AccountError("客户端类型无效")
        raw_token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        session_id = str(uuid.uuid4())
        now = utcnow()
        expires = now + timedelta(seconds=self.session_ttl_seconds)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO user_sessions(id,user_id,token_hash,csrf_hash,client,user_agent_hash,ip_hash,created_at,last_seen_at,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    user["id"],
                    token_hash(raw_token),
                    token_hash(csrf),
                    client,
                    token_hash(user_agent[:500]) if user_agent else None,
                    token_hash(ip[:100]) if ip else None,
                    iso(now),
                    iso(now),
                    iso(expires),
                ),
            )
        return SessionContext(
            session_id=session_id,
            user_id=user["id"],
            email=user["email"],
            display_name=user["display_name"],
            email_verified=bool(user["email_verified"]),
            client=client,
            google_linked=self.google_linked(user["id"]),
            role=str(user.get("role") or "user"),
            csrf_hash=token_hash(csrf),
            expires_at=iso(expires),
            token=raw_token,
            csrf_token=csrf,
        )

    def session(self, raw_token: str) -> SessionContext | None:
        if not raw_token or len(raw_token) > 128:
            return None
        now = iso()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT s.*,u.email,u.display_name,u.email_verified_at,u.status,u.role,"
                "EXISTS(SELECT 1 FROM user_identities i WHERE i.provider='google' "
                "AND i.user_id=u.id) AS google_linked "
                "FROM user_sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?",
                (token_hash(raw_token), now),
            ).fetchone()
            if not row or row["status"] != "active":
                return None
            connection.execute(
                "UPDATE user_sessions SET last_seen_at=? WHERE id=? AND last_seen_at<?",
                (now, row["id"], iso(utcnow() - timedelta(minutes=5))),
            )
        return SessionContext(
            session_id=row["id"],
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"],
            email_verified=bool(row["email_verified_at"]),
            client=row["client"],
            google_linked=bool(row["google_linked"]),
            role=str(row["role"] or "user"),
            csrf_hash=bytes(row["csrf_hash"]),
            expires_at=row["expires_at"],
        )

    def revoke_session(self, raw_token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (iso(), token_hash(raw_token)),
            )

    def rotate_csrf(self, raw_token: str) -> str:
        csrf = secrets.token_urlsafe(24)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE user_sessions SET csrf_hash=? WHERE token_hash=? "
                "AND revoked_at IS NULL AND expires_at>?",
                (token_hash(csrf), token_hash(raw_token), iso()),
            )
            if cursor.rowcount != 1:
                raise AccountError("登录状态已失效", 401)
        return csrf

    def require_csrf(self, session: SessionContext, supplied: str) -> None:
        if not supplied or not hmac.compare_digest(
            session.csrf_hash, token_hash(supplied)
        ):
            raise AccountError("安全令牌无效，请刷新页面后重试", 403)

    def profile(self, user_id: str) -> dict[str, Any]:
        now = iso()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO user_profiles(user_id,created_at,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO NOTHING",
                (user_id, now, now),
            )
            row = connection.execute(
                "SELECT u.display_name,p.bio,p.gender,p.birthday,p.location,p.avatar_version "
                "FROM users u JOIN user_profiles p ON p.user_id=u.id WHERE u.id=?",
                (user_id,),
            ).fetchone()
        if not row:
            raise AccountError("账户不存在", 404)
        return dict(row)

    def update_profile(
        self,
        user_id: str,
        *,
        display_name: str,
        bio: str,
        gender: str,
        birthday: str | None,
        location: str,
    ) -> dict[str, Any]:
        display_name = clean_display_name(display_name)
        bio = clean_profile_text(bio, field="个人简介", max_length=500)
        location = clean_profile_text(location, field="所在地", max_length=80)
        gender = str(gender or "")
        if gender not in {"", "female", "male", "nonbinary", "prefer_not_say"}:
            raise AccountError("性别选项无效")
        birthday = str(birthday or "").strip() or None
        if birthday:
            try:
                parsed = date.fromisoformat(birthday)
            except ValueError as exc:
                raise AccountError("生日格式无效") from exc
            if parsed.year < 1900 or parsed > utcnow().date():
                raise AccountError("生日范围无效")
        now = iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE users SET display_name=?,updated_at=? WHERE id=? AND status='active'",
                (display_name, now, user_id),
            )
            if changed.rowcount != 1:
                connection.rollback()
                raise AccountError("账户不存在", 404)
            connection.execute(
                "INSERT INTO user_profiles"
                "(user_id,bio,gender,birthday,location,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "bio=excluded.bio,gender=excluded.gender,birthday=excluded.birthday,"
                "location=excluded.location,updated_at=excluded.updated_at",
                (user_id, bio, gender, birthday, location, now, now),
            )
            connection.commit()
        return self.profile(user_id)

    def bump_avatar_version(self, user_id: str) -> int:
        now = iso()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO user_profiles(user_id,avatar_version,created_at,updated_at) "
                "VALUES(?,1,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "avatar_version=avatar_version+1,updated_at=excluded.updated_at",
                (user_id, now, now),
            )
            row = connection.execute(
                "SELECT avatar_version FROM user_profiles WHERE user_id=?", (user_id,)
            ).fetchone()
        return int(row["avatar_version"])

    def active_avatar_version(self, user_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(p.avatar_version,0) AS avatar_version "
                "FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id "
                "WHERE u.id=? AND u.status='active'",
                (str(user_id),),
            ).fetchone()
        if row is None or int(row["avatar_version"]) <= 0:
            raise AccountError("用户尚未上传头像", 404)
        return int(row["avatar_version"])

    def comment_authors(self, user_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(str(item) for item in user_ids if item))[:1000]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT u.id,u.display_name,COALESCE(p.avatar_version,0) AS avatar_version,"
                "COALESCE(rt.active_seconds,0) AS active_seconds "
                "FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id "
                "LEFT JOIN user_reading_totals rt ON rt.user_id=u.id "
                f"WHERE u.status='active' AND u.id IN ({placeholders})",
                ids,
            ).fetchall()
        return {
            str(row["id"]): {
                "display_name": str(row["display_name"]),
                "avatar_version": int(row["avatar_version"]),
                "reading": reading_level_summary(int(row["active_seconds"])),
            }
            for row in rows
        }

    def _audit(
        self, connection: sqlite3.Connection, user_id: str, event: str, outcome: str
    ) -> None:
        connection.execute(
            "INSERT INTO security_audit_events(id,user_id,event,outcome,created_at) "
            "VALUES(?,?,?,?,?)",
            (str(uuid.uuid4()), user_id, event[:80], outcome[:40], iso()),
        )

    def change_password(
        self,
        user_id: str,
        session_id: str,
        current_password: str,
        new_password: str,
    ) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT email,password_hash FROM users WHERE id=? AND status='active'",
                (user_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise AccountError("账户不存在", 404)
            encoded = row["password_hash"]
            try:
                valid = bool(encoded) and PASSWORD_HASHER.verify(
                    str(encoded), str(current_password or "")
                )
            except (InvalidHashError, VerifyMismatchError):
                valid = False
            if not valid:
                self._audit(
                    connection,
                    user_id,
                    "password_change",
                    "invalid_current_password",
                )
                connection.commit()
                raise AccountError("当前密码不正确", 401)
            password = validate_password(new_password, email=row["email"])
            if hmac.compare_digest(str(current_password), password):
                self._audit(connection, user_id, "password_change", "reused_password")
                connection.commit()
                raise AccountError("新密码不能与当前密码相同")
            now = iso()
            connection.execute(
                "UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
                (PASSWORD_HASHER.hash(password), now, user_id),
            )
            revoked = connection.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND id<>? "
                "AND revoked_at IS NULL",
                (now, user_id, session_id),
            ).rowcount
            self._audit(connection, user_id, "password_change", "success")
            connection.commit()
        return int(revoked)

    def setup_password(
        self,
        user_id: str,
        session_id: str,
        claims: dict[str, Any],
        new_password: str,
    ) -> int:
        subject, email = self._google_identity(claims)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT email,password_hash FROM users "
                "WHERE id=? AND status='active'",
                (user_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise AccountError("账户不存在", 404)
            if row["password_hash"]:
                connection.rollback()
                raise AccountError("邮箱密码登录已经启用", 409)
            identity = connection.execute(
                "SELECT subject FROM user_identities WHERE provider='google' "
                "AND user_id=? LIMIT 1",
                (user_id,),
            ).fetchone()
            if not identity or not hmac.compare_digest(
                str(identity["subject"]), subject
            ):
                self._audit(
                    connection, user_id, "password_setup", "google_subject_mismatch"
                )
                connection.commit()
                raise AccountError("Google 身份确认失败", 403)
            if str(row["email"]).casefold() != email.casefold():
                self._audit(
                    connection, user_id, "password_setup", "google_email_mismatch"
                )
                connection.commit()
                raise AccountError("Google 邮箱与当前账户不一致", 409)
            password = validate_password(new_password, email=row["email"])
            now = iso()
            connection.execute(
                "UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
                (PASSWORD_HASHER.hash(password), now, user_id),
            )
            revoked = connection.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND id<>? "
                "AND revoked_at IS NULL",
                (now, user_id, session_id),
            ).rowcount
            self._audit(connection, user_id, "password_setup", "success")
            connection.commit()
        return int(revoked)

    def reading_summary(self, user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT active_seconds FROM user_reading_totals WHERE user_id=?",
                (user_id,),
            ).fetchone()
        return reading_level_summary(int(row["active_seconds"]) if row else 0)

    def wallet_summary(self, user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(w.balance_units,0) AS balance_units,"
                "COALESCE(r.active_seconds,0) AS active_seconds "
                "FROM users u LEFT JOIN user_point_wallets w ON w.user_id=u.id "
                "LEFT JOIN user_reading_totals r ON r.user_id=u.id WHERE u.id=?",
                (user_id,),
            ).fetchone()
            ledger = [
                dict(item)
                for item in connection.execute(
                    "SELECT delta_units,balance_after_units,kind,reference_type,reference_id,created_at "
                    "FROM user_point_ledger WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
                    (user_id,),
                )
            ]
        if row is None:
            raise AccountError("账户不存在", 404)
        active_seconds = int(row["active_seconds"] or 0)
        for item in ledger:
            item["delta"] = point_value(item.pop("delta_units", 0))
            item["balance_after"] = point_value(
                item.pop("balance_after_units", 0)
            )
        return {
            "balance": point_value(row["balance_units"] or 0),
            "reading_seconds": active_seconds,
            "exchangeable_points": active_seconds // POINT_READING_SECONDS,
            "seconds_per_point": POINT_READING_SECONDS,
            "cash_recharge_enabled": False,
            "ledger": ledger,
        }

    def convert_reading_to_points(
        self, user_id: str, points: int, request_id: str
    ) -> dict[str, Any]:
        amount = int(points)
        if amount < 1 or amount > 1_000:
            raise AccountError("单次兑换积分必须在 1 至 1000 之间")
        try:
            canonical_request_id = str(uuid.UUID(str(request_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AccountError("兑换事件标识无效") from exc
        required_seconds = amount * POINT_READING_SECONDS
        now = iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT balance_after FROM user_point_ledger WHERE user_id=? "
                "AND kind='reading_exchange' AND reference_type='request' AND reference_id=?",
                (user_id, canonical_request_id),
            ).fetchone()
            if existing:
                connection.commit()
                return self.wallet_summary(user_id) | {"converted": 0, "idempotent": True}
            reading = connection.execute(
                "SELECT active_seconds FROM user_reading_totals WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if reading is None or int(reading["active_seconds"] or 0) < required_seconds:
                connection.rollback()
                raise AccountError("可兑换阅读时长不足", 409)
            wallet = connection.execute(
                "SELECT balance_units FROM user_point_wallets WHERE user_id=?",
                (user_id,),
            ).fetchone()
            balance_units = int(wallet["balance_units"] if wallet else 0) + (
                amount * POINT_SCALE
            )
            balance = point_value(balance_units)
            connection.execute(
                "UPDATE user_reading_totals SET active_seconds=active_seconds-?,updated_at=? WHERE user_id=?",
                (required_seconds, now, user_id),
            )
            connection.execute(
                "INSERT INTO user_point_wallets(user_id,balance,balance_units,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "balance=excluded.balance,balance_units=excluded.balance_units,"
                "updated_at=excluded.updated_at",
                (user_id, balance, balance_units, now),
            )
            connection.execute(
                "INSERT INTO user_point_ledger(id,user_id,delta,balance_after,delta_units,balance_after_units,"
                "kind,reference_type,reference_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    user_id,
                    amount,
                    balance,
                    amount * POINT_SCALE,
                    balance_units,
                    "reading_exchange",
                    "request",
                    canonical_request_id,
                    now,
                ),
            )
            connection.commit()
        return self.wallet_summary(user_id) | {"converted": amount, "idempotent": False}

    def recommendation_status(self, user_id: str, book_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS boost_count,"
                "COALESCE(SUM(spent_seconds),0) AS spent_seconds,"
                "COALESCE(SUM(CASE WHEN metric_applied=0 THEN 1 ELSE 0 END),0) "
                "AS pending_count,MAX(created_at) AS recommended_at FROM "
                "user_book_recommendations WHERE user_id=? AND book_id=?",
                (str(user_id), str(book_id)),
            ).fetchone()
        boost_count = int(row["boost_count"] or 0)
        pending_count = int(row["pending_count"] or 0)
        return self.reading_summary(user_id) | {
            "recommended": boost_count > 0,
            "boost_count": boost_count,
            "metric_applied": pending_count == 0,
            "pending_count": pending_count,
            "donated_seconds": int(row["spent_seconds"] or 0),
            "recommended_at": str(row["recommended_at"])
            if row["recommended_at"]
            else None,
            "recommendation_cost_seconds": RECOMMENDATION_COST_SECONDS,
            "recommendation_cost_hours": 1,
        }

    def pending_recommendation_events(
        self, user_id: str, book_id: str, *, limit: int = 100
    ) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM user_book_recommendations "
                "WHERE user_id=? AND book_id=? AND metric_applied=0 "
                "ORDER BY created_at,id LIMIT ?",
                (str(user_id), str(book_id), max(1, min(int(limit), 500))),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def donate_recommendation(
        self, user_id: str, book_id: str, request_id: str
    ) -> dict[str, Any]:
        try:
            parsed_request_id = uuid.UUID(str(request_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AccountError("助力事件标识无效") from exc
        if parsed_request_id.version != 4 or str(parsed_request_id) != str(request_id):
            raise AccountError("助力事件标识无效")
        canonical_request_id = str(parsed_request_id)
        event_id = str(
            uuid.uuid5(
                RECOMMENDATION_EVENT_NAMESPACE,
                f"{str(user_id)}:{canonical_request_id}",
            )
        )
        now = iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id,book_id,metric_applied,spent_seconds,created_at FROM "
                "user_book_recommendations WHERE user_id=? AND request_id=?",
                (str(user_id), canonical_request_id),
            ).fetchone()
            total = connection.execute(
                "SELECT active_seconds FROM user_reading_totals WHERE user_id=?",
                (str(user_id),),
            ).fetchone()
            active_seconds = int(total["active_seconds"]) if total else 0
            if existing:
                if str(existing["book_id"]) != str(book_id):
                    connection.rollback()
                    raise AccountError("助力事件已用于其他作品，请重新操作", 409)
                boost_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM user_book_recommendations "
                        "WHERE user_id=? AND book_id=?",
                        (str(user_id), str(book_id)),
                    ).fetchone()[0]
                )
                connection.commit()
                return reading_level_summary(active_seconds) | {
                    "recommended": True,
                    "new_donation": False,
                    "metric_applied": bool(existing["metric_applied"]),
                    "donated_seconds": 0,
                    "event_id": str(existing["id"]),
                    "request_id": canonical_request_id,
                    "boost_count": boost_count,
                    "recommendation_cost_seconds": RECOMMENDATION_COST_SECONDS,
                    "recommendation_cost_hours": 1,
                }
            if active_seconds < RECOMMENDATION_COST_SECONDS:
                self._audit(
                    connection,
                    str(user_id),
                    "book_recommendation",
                    "insufficient_reading_time",
                )
                connection.commit()
                raise AccountError(
                    "当前可用阅读经验时长不足 1 小时。继续阅读，累计满 1 小时后再来为好书助力吧。",
                    409,
                )
            updated = connection.execute(
                "UPDATE user_reading_totals SET active_seconds=active_seconds-?,updated_at=? "
                "WHERE user_id=? AND active_seconds>=?",
                (
                    RECOMMENDATION_COST_SECONDS,
                    now,
                    str(user_id),
                    RECOMMENDATION_COST_SECONDS,
                ),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise AccountError("阅读时长正在更新，请稍后再试", 409)
            connection.execute(
                "INSERT INTO user_book_recommendations"
                "(id,user_id,book_id,request_id,spent_seconds,metric_applied,"
                "created_at,applied_at) VALUES(?,?,?,?,?,0,?,NULL)",
                (
                    event_id,
                    str(user_id),
                    str(book_id),
                    canonical_request_id,
                    RECOMMENDATION_COST_SECONDS,
                    now,
                ),
            )
            self._audit(connection, str(user_id), "book_recommendation", "donated")
            boost_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM user_book_recommendations "
                    "WHERE user_id=? AND book_id=?",
                    (str(user_id), str(book_id)),
                ).fetchone()[0]
            )
            connection.commit()
        return reading_level_summary(active_seconds - RECOMMENDATION_COST_SECONDS) | {
            "recommended": True,
            "new_donation": True,
            "metric_applied": False,
            "donated_seconds": RECOMMENDATION_COST_SECONDS,
            "event_id": event_id,
            "request_id": canonical_request_id,
            "boost_count": boost_count,
            "recommendation_cost_seconds": RECOMMENDATION_COST_SECONDS,
            "recommendation_cost_hours": 1,
        }

    def mark_recommendation_applied(self, user_id: str, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE user_book_recommendations SET metric_applied=1,applied_at=? "
                "WHERE user_id=? AND id=?",
                (iso(), str(user_id), str(event_id)),
            )
            connection.commit()

    def accept_reading_heartbeat(
        self,
        user_id: str,
        *,
        event_id: str,
        book_id: str,
        claimed_seconds: int,
    ) -> dict[str, Any]:
        claimed = min(60, max(1, int(claimed_seconds)))
        now_dt = utcnow()
        now = iso(now_dt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT accepted_seconds FROM user_reading_heartbeats "
                "WHERE user_id=? AND event_id=?",
                (user_id, event_id),
            ).fetchone()
            if duplicate:
                total = connection.execute(
                    "SELECT active_seconds FROM user_reading_totals WHERE user_id=?",
                    (user_id,),
                ).fetchone()
                connection.commit()
                return reading_level_summary(
                    int(total["active_seconds"]) if total else 0
                ) | {
                    "accepted_seconds": int(duplicate["accepted_seconds"]),
                    "duplicate": True,
                }
            total = connection.execute(
                "SELECT active_seconds,last_heartbeat_at FROM user_reading_totals WHERE user_id=?",
                (user_id,),
            ).fetchone()
            active_seconds = int(total["active_seconds"]) if total else 0
            accepted = claimed
            if total and total["last_heartbeat_at"]:
                elapsed = max(
                    0.0,
                    (
                        now_dt - datetime.fromisoformat(total["last_heartbeat_at"])
                    ).total_seconds(),
                )
                accepted = 0 if elapsed < 5 else min(claimed, max(0, int(elapsed) + 2))
            connection.execute(
                "INSERT INTO user_reading_totals"
                "(user_id,active_seconds,last_heartbeat_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "active_seconds=active_seconds+excluded.active_seconds,"
                "last_heartbeat_at=excluded.last_heartbeat_at,updated_at=excluded.updated_at",
                (user_id, accepted, now, now),
            )
            connection.execute(
                "INSERT INTO user_reading_heartbeats"
                "(user_id,event_id,book_id,claimed_seconds,accepted_seconds,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (user_id, event_id, book_id, claimed, accepted, now),
            )
            connection.execute(
                "DELETE FROM user_reading_heartbeats WHERE user_id=? AND created_at<?",
                (user_id, iso(now_dt - timedelta(days=30))),
            )
            connection.commit()
        return reading_level_summary(active_seconds + accepted) | {
            "accepted_seconds": accepted,
            "duplicate": False,
        }

    def create_paragraph_comment(
        self,
        user_id: str,
        *,
        book_id: str,
        chapter_id: int,
        paragraph_index: int,
        paragraph_key: str,
        paragraph_excerpt: str,
        content: str,
    ) -> str:
        comment_id = str(uuid.uuid4())
        now_dt = utcnow()
        now = iso(now_dt)
        cleaned = (
            unicodedata.normalize("NFKC", str(content or ""))
            .replace("\x00", "")
            .strip()
        )
        if not cleaned:
            raise AccountError("评论不能为空")
        if len(cleaned) > 500:
            raise AccountError("评论不能超过 500 个字符")
        moderation = moderate_comment(cleaned)
        if not moderation.allowed:
            raise AccountError(moderation.detail, 422)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recent = int(
                connection.execute(
                    "SELECT COUNT(*) FROM paragraph_comments WHERE user_id=? AND created_at>=?",
                    (user_id, iso(now_dt - timedelta(hours=24))),
                ).fetchone()[0]
            )
            if recent >= 100:
                connection.rollback()
                raise AccountError("今日评论次数已达上限，请明天再试", 429)
            connection.execute(
                "INSERT INTO paragraph_comments"
                "(id,user_id,book_id,chapter_id,paragraph_index,paragraph_key,"
                "paragraph_excerpt,content,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    comment_id,
                    user_id,
                    str(book_id),
                    int(chapter_id),
                    int(paragraph_index),
                    str(paragraph_key)[:80],
                    str(paragraph_excerpt)[:160],
                    cleaned,
                    "visible",
                    now,
                ),
            )
            connection.commit()
        return comment_id

    def chapter_paragraph_comments(
        self,
        *,
        book_id: str,
        chapter_id: int,
        paragraph_keys: list[str],
        viewer_user_id: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        keys = [str(key)[:80] for key in paragraph_keys if key]
        if not keys:
            return {"paragraphs": {}, "comment_count": 0}
        bounded = min(max(int(limit), 1), 1000)
        placeholders = ",".join("?" for _ in keys)
        viewer = str(viewer_user_id or "")
        query = (
            "SELECT c.id,c.user_id,c.paragraph_index,c.paragraph_key,c.paragraph_excerpt,"
            "c.content,c.created_at,u.display_name,COALESCE(p.avatar_version,0) AS avatar_version,"
            "COALESCE(rt.active_seconds,0) AS active_seconds,"
            "COALESCE(SUM(t.like_count),0) AS like_count,"
            "COALESCE(MAX(CASE WHEN t.user_id=? THEN t.like_count ELSE 0 END),0) "
            "AS viewer_like_count "
            "FROM paragraph_comments c JOIN users u ON u.id=c.user_id AND u.status='active' "
            "LEFT JOIN user_profiles p ON p.user_id=c.user_id "
            "LEFT JOIN user_reading_totals rt ON rt.user_id=c.user_id "
            "LEFT JOIN paragraph_comment_thanks t ON t.comment_id=c.id "
            f"WHERE c.book_id=? AND c.chapter_id=? AND c.status='visible' AND c.paragraph_key IN ({placeholders}) "
            "GROUP BY c.id ORDER BY c.created_at ASC,c.id ASC LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(
                query, (viewer, str(book_id), int(chapter_id), *keys, bounded)
            ).fetchall()
        paragraphs: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row["paragraph_key"])
            thread = paragraphs.setdefault(
                key,
                {
                    "paragraph_index": int(row["paragraph_index"]),
                    "paragraph_key": key,
                    "excerpt": row["paragraph_excerpt"],
                    "count": 0,
                    "total_thanks": 0,
                    "comments": [],
                },
            )
            rank = reading_level_summary(int(row["active_seconds"]))
            like_count = int(row["like_count"])
            viewer_like_count = int(row["viewer_like_count"])
            thread["comments"].append(
                {
                    "id": row["id"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                    "like_count": like_count,
                    "viewer_like_count": viewer_like_count,
                    # Compatibility aliases for installed clients older than v1.9.2.
                    "thanks_count": like_count,
                    "thanked_by_me": viewer_like_count > 0,
                    "is_own": bool(viewer and row["user_id"] == viewer),
                    "author": {
                        "display_name": row["display_name"],
                        "avatar_url": (
                            f"/api/v1/users/{row['user_id']}/avatar?v={int(row['avatar_version'])}"
                            if int(row["avatar_version"]) > 0
                            else None
                        ),
                        "reading": {
                            "level": rank["level"],
                            "roman": rank["roman"],
                            "name": rank["name"],
                        },
                    },
                }
            )
            thread["count"] += 1
            thread["total_thanks"] += like_count
        return {
            "paragraphs": paragraphs,
            "comment_count": sum(
                int(thread["count"]) for thread in paragraphs.values()
            ),
        }

    def adjust_paragraph_comment_like(
        self, user_id: str, comment_id: str, *, delta: int
    ) -> dict[str, Any]:
        if int(delta) not in {-1, 1}:
            raise ValueError("点赞增量必须为 -1 或 1")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT user_id FROM paragraph_comments WHERE id=? AND status='visible'",
                (str(comment_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise AccountError("评论不存在", 404)
            if row["user_id"] == user_id:
                connection.rollback()
                raise AccountError("不能给自己的评论点赞")
            reaction = connection.execute(
                "SELECT like_count FROM paragraph_comment_thanks "
                "WHERE comment_id=? AND user_id=?",
                (str(comment_id), user_id),
            ).fetchone()
            viewer_like_count = int(reaction["like_count"]) if reaction else 0
            if delta > 0:
                if viewer_like_count >= 3:
                    connection.rollback()
                    raise AccountError("每位用户对同一条评论最多点赞 3 次", 409)
                if reaction is None:
                    connection.execute(
                        "INSERT INTO paragraph_comment_thanks"
                        "(comment_id,user_id,like_count,created_at) VALUES(?,?,1,?)",
                        (str(comment_id), user_id, iso()),
                    )
                else:
                    connection.execute(
                        "UPDATE paragraph_comment_thanks SET like_count=like_count+1 "
                        "WHERE comment_id=? AND user_id=?",
                        (str(comment_id), user_id),
                    )
                viewer_like_count += 1
            else:
                if viewer_like_count <= 1:
                    connection.execute(
                        "DELETE FROM paragraph_comment_thanks WHERE comment_id=? AND user_id=?",
                        (str(comment_id), user_id),
                    )
                    viewer_like_count = 0
                else:
                    connection.execute(
                        "UPDATE paragraph_comment_thanks SET like_count=like_count-1 "
                        "WHERE comment_id=? AND user_id=?",
                        (str(comment_id), user_id),
                    )
                    viewer_like_count -= 1
            count = int(
                connection.execute(
                    "SELECT COALESCE(SUM(like_count),0) FROM paragraph_comment_thanks "
                    "WHERE comment_id=?",
                    (str(comment_id),),
                ).fetchone()[0]
            )
            connection.commit()
        return {
            "liked": viewer_like_count > 0,
            "like_count": count,
            "viewer_like_count": viewer_like_count,
            "thanked": viewer_like_count > 0,
            "thanks_count": count,
        }

    def state(self, user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            history = [
                dict(row)
                for row in connection.execute(
                    "SELECT book_id,chapter_id,progress,title,author,cover_url,updated_at "
                    "FROM user_reading_history WHERE user_id=? ORDER BY updated_at DESC LIMIT 500",
                    (user_id,),
                )
            ]
            favorites = [
                dict(row)
                for row in connection.execute(
                    "SELECT book_id,title,author,cover_url,created_at,updated_at "
                    "FROM user_favorites WHERE user_id=? ORDER BY updated_at DESC LIMIT 1000",
                    (user_id,),
                )
            ]
            bookshelf = [
                dict(row)
                for row in connection.execute(
                    "SELECT book_id,title,author,cover_url,note,created_at,updated_at "
                    "FROM user_bookshelf WHERE user_id=? ORDER BY updated_at DESC LIMIT 1000",
                    (user_id,),
                )
            ]
        return {"history": history, "favorites": favorites, "bookshelf": bookshelf}

    def sync_state(
        self, user_id: str, payload: dict[str, list[dict[str, Any]]]
    ) -> dict[str, Any]:
        now = iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in payload.get("history", [])[:500]:
                connection.execute(
                    "INSERT INTO user_reading_history(user_id,book_id,chapter_id,progress,title,author,cover_url,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,book_id) DO UPDATE SET "
                    "chapter_id=CASE WHEN excluded.updated_at>=updated_at THEN excluded.chapter_id ELSE chapter_id END,"
                    "progress=CASE WHEN excluded.updated_at>=updated_at THEN excluded.progress ELSE progress END,"
                    "title=excluded.title,author=excluded.author,cover_url=excluded.cover_url,"
                    "updated_at=MAX(updated_at,excluded.updated_at)",
                    (
                        user_id,
                        item["book_id"],
                        int(item.get("chapter_id") or 1),
                        float(item.get("progress") or 0),
                        str(item.get("title") or "")[:200],
                        str(item.get("author") or "")[:100],
                        str(item.get("cover_url") or "")[:500],
                        str(item.get("updated_at") or now),
                    ),
                )
            for table, key in (
                ("user_favorites", "favorites"),
                ("user_bookshelf", "bookshelf"),
            ):
                for item in payload.get(key, [])[:1000]:
                    note = (
                        str(item.get("note") or "")[:500]
                        if table == "user_bookshelf"
                        else None
                    )
                    columns = (
                        "user_id,book_id,title,author,cover_url,created_at,updated_at"
                    )
                    values: tuple[Any, ...] = (
                        user_id,
                        item["book_id"],
                        str(item.get("title") or "")[:200],
                        str(item.get("author") or "")[:100],
                        str(item.get("cover_url") or "")[:500],
                        str(item.get("created_at") or now),
                        str(item.get("updated_at") or now),
                    )
                    if table == "user_bookshelf":
                        columns += ",note"
                        values += (note,)
                    connection.execute(
                        f"INSERT INTO {table}({columns}) VALUES({','.join('?' for _ in values)}) "
                        "ON CONFLICT(user_id,book_id) DO UPDATE SET title=excluded.title,"
                        "author=excluded.author,cover_url=excluded.cover_url,updated_at=MAX(updated_at,excluded.updated_at)"
                        + (",note=excluded.note" if table == "user_bookshelf" else ""),
                        values,
                    )
            connection.commit()
        return self.state(user_id)

    def remove_state_item(self, user_id: str, kind: str, book_id: str) -> int | None:
        tables = {
            "history": "user_reading_history",
            "favorites": "user_favorites",
            "bookshelf": "user_bookshelf",
        }
        table = tables.get(kind)
        if table is None:
            raise AccountError("记录类型无效")
        favorite_count: int | None = None
        with self._connect() as connection:
            connection.execute(
                f"DELETE FROM {table} WHERE user_id=? AND book_id=?",
                (user_id, book_id),
            )
            if kind == "favorites":
                row = connection.execute(
                    "SELECT COUNT(*) FROM user_favorites f "
                    "INNER JOIN users u ON u.id=f.user_id "
                    "WHERE f.book_id=? AND u.status='active'",
                    (book_id,),
                ).fetchone()
                favorite_count = int(row[0] if row else 0)
        return favorite_count

    def favorite_count(self, book_id: str) -> int:
        """Return the authoritative active-account favorite count for a book."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM user_favorites f "
                "INNER JOIN users u ON u.id=f.user_id "
                "WHERE f.book_id=? AND u.status='active'",
                (book_id,),
            ).fetchone()
        return int(row[0] if row else 0)

    def favorite_counts(self) -> dict[str, int]:
        """Return all authoritative counts for bounded reconciliation jobs."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT f.book_id,COUNT(*) AS favorite_count "
                "FROM user_favorites f INNER JOIN users u ON u.id=f.user_id "
                "WHERE u.status='active' GROUP BY f.book_id"
            ).fetchall()
        return {str(row["book_id"]): int(row["favorite_count"]) for row in rows}

    @staticmethod
    def _expire_deconstruction_tasks(connection: sqlite3.Connection) -> int:
        return int(
            connection.execute(
                "UPDATE deconstruction_tasks SET status='expired' "
                "WHERE status IN ('open','claimed') AND expires_at<=?",
                (iso(),),
            ).rowcount
        )

    def create_deconstruction_task(
        self, user_id: str, *, book_title: str, author: str, request_note: str
    ) -> dict[str, Any]:
        title = clean_profile_text(book_title, field="书名", max_length=160)
        creator = clean_profile_text(author, field="作者", max_length=100)
        note = clean_profile_text(request_note, field="任务说明", max_length=2000)
        if not title:
            raise AccountError("请填写需要拆解的书名")
        for value in (title, creator, note):
            if value:
                moderation = moderate_comment(value)
                if not moderation.allowed:
                    raise AccountError(moderation.detail, 422)
        task_id = str(uuid.uuid4())
        now = utcnow()
        created_at = iso(now)
        expires_at = iso(now + timedelta(days=DECONSTRUCTION_TASK_TTL_DAYS))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_deconstruction_tasks(connection)
            count = connection.execute(
                "SELECT COUNT(*) FROM deconstruction_tasks WHERE creator_user_id=? "
                "AND status IN ('open','claimed','submitted')",
                (user_id,),
            ).fetchone()[0]
            if int(count) >= MAX_ACTIVE_DECONSTRUCTION_TASKS:
                connection.rollback()
                raise AccountError("同时进行的拆书任务不能超过 10 个", 409)
            connection.execute(
                "INSERT INTO deconstruction_tasks(id,creator_user_id,book_title,author,request_note,status,created_at,expires_at) "
                "VALUES(?,?,?,?,?,'open',?,?)",
                (task_id, user_id, title, creator, note, created_at, expires_at),
            )
            connection.commit()
        return self.deconstruction_task(task_id, user_id)

    def deconstruction_task(
        self, task_id: str, viewer_user_id: str
    ) -> dict[str, Any]:
        items = self.list_deconstruction_tasks(viewer_user_id, task_id=task_id)
        if not items:
            raise AccountError("拆书任务不存在", 404)
        return items[0]

    def list_deconstruction_tasks(
        self, viewer_user_id: str, *, task_id: str = ""
    ) -> list[dict[str, Any]]:
        where = "WHERE t.id=?" if task_id else ""
        params: tuple[Any, ...] = (task_id,) if task_id else ()
        now = utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_deconstruction_tasks(connection)
            rows = connection.execute(
                "SELECT t.*,creator.display_name AS creator_display_name,"
                "claimer.display_name AS claimer_display_name FROM deconstruction_tasks t "
                "JOIN users creator ON creator.id=t.creator_user_id "
                "LEFT JOIN users claimer ON claimer.id=t.claimed_by_user_id "
                f"{where} ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'claimed' THEN 1 "
                "WHEN 'submitted' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END,t.created_at DESC LIMIT 500",
                params,
            ).fetchall()
            connection.commit()
        result: list[dict[str, Any]] = []
        for source in rows:
            item = dict(source)
            try:
                remaining = max(
                    0,
                    int((datetime.fromisoformat(str(item["expires_at"])) - now).total_seconds()),
                )
            except (ValueError, TypeError):
                remaining = 0
            item.update(
                {
                    "remaining_seconds": remaining,
                    "viewer_is_creator": item["creator_user_id"] == viewer_user_id,
                    "viewer_is_claimer": item.get("claimed_by_user_id") == viewer_user_id,
                    "can_claim": item["status"] == "open" and item["creator_user_id"] != viewer_user_id,
                }
            )
            result.append(item)
        return result

    def claim_deconstruction_task(self, task_id: str, user_id: str) -> dict[str, Any]:
        now = iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_deconstruction_tasks(connection)
            row = connection.execute(
                "SELECT creator_user_id,status FROM deconstruction_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise AccountError("拆书任务不存在", 404)
            if str(row["creator_user_id"]) == user_id:
                connection.rollback()
                raise AccountError("不能接取自己发布的任务", 409)
            if str(row["status"]) != "open":
                connection.rollback()
                raise AccountError("任务已被接取或已经过期", 409)
            connection.execute(
                "UPDATE deconstruction_tasks SET status='claimed',claimed_by_user_id=?,claimed_at=? WHERE id=? AND status='open'",
                (user_id, now, task_id),
            )
            connection.commit()
        return self.deconstruction_task(task_id, user_id)

    def release_deconstruction_task(self, task_id: str, user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_deconstruction_tasks(connection)
            cursor = connection.execute(
                "UPDATE deconstruction_tasks SET status='open',claimed_by_user_id=NULL,claimed_at=NULL "
                "WHERE id=? AND claimed_by_user_id=? AND status='claimed'",
                (task_id, user_id),
            )
            if int(cursor.rowcount) != 1:
                connection.rollback()
                raise AccountError("当前任务不能取消接取", 409)
            connection.commit()
        return self.deconstruction_task(task_id, user_id)

    def task_creator_user_id(self, task_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT creator_user_id FROM deconstruction_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        return str(row["creator_user_id"]) if row else None

    def create_upload(
        self,
        user_id: str,
        filename: str,
        media_type: str,
        *,
        task_id: str = "",
        download_points: Any = 0,
    ) -> str:
        upload_id = str(uuid.uuid4())
        # Kept as an ignored keyword for old web/app clients. The authoritative
        # price is derived from 原文/原文.txt after safe extraction.
        _ = download_points
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_deconstruction_tasks(connection)
            linked_task_id = str(task_id or "").strip()
            if linked_task_id:
                task = connection.execute(
                    "SELECT status,claimed_by_user_id FROM deconstruction_tasks WHERE id=?",
                    (linked_task_id,),
                ).fetchone()
                if task is None:
                    connection.rollback()
                    raise AccountError("拆书任务不存在", 404)
                if task["status"] != "claimed" or task["claimed_by_user_id"] != user_id:
                    connection.rollback()
                    raise AccountError("请先接取该任务再上传档案", 409)
            connection.execute(
                "INSERT INTO deconstruction_uploads(id,user_id,original_filename,media_type,status,created_at,"
                "task_id,download_points,download_point_units) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    upload_id,
                    user_id,
                    filename,
                    media_type[:100],
                    "quarantined",
                    iso(),
                    linked_task_id or None,
                    0,
                    0,
                ),
            )
            if linked_task_id:
                connection.execute(
                    "UPDATE deconstruction_tasks SET status='submitted',submission_id=?,submitted_at=? WHERE id=?",
                    (upload_id, iso(), linked_task_id),
                )
            connection.commit()
        return upload_id

    def receive_upload(
        self,
        upload_id: str,
        user_id: str,
        *,
        stored_filename: str,
        size: int,
    ) -> None:
        """Persist the completed transfer while leaving inspection queued."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE deconstruction_uploads SET stored_filename=?,bytes=?,rejection_reason='' "
                "WHERE id=? AND user_id=? AND status='quarantined'",
                (stored_filename, int(size), upload_id, user_id),
            )
        if int(cursor.rowcount) != 1:
            raise AccountError("上传记录状态已变化，请重新上传", 409)

    def claim_upload_scan(
        self, *, upload_id: str | None = None
    ) -> dict[str, Any] | None:
        """Atomically claim one transferred archive for background inspection."""
        stale_before = iso(utcnow() - timedelta(minutes=15))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE deconstruction_uploads SET status='quarantined',scanned_at=NULL "
                "WHERE status='scanning' AND scanned_at<?",
                (stale_before,),
            )
            where = "AND id=?" if upload_id else ""
            params: tuple[Any, ...] = (upload_id,) if upload_id else ()
            row = connection.execute(
                "SELECT * FROM deconstruction_uploads WHERE status='quarantined' "
                "AND stored_filename IS NOT NULL AND stored_filename!='' "
                f"{where} ORDER BY created_at LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                "UPDATE deconstruction_uploads SET status='scanning',scanned_at=? "
                "WHERE id=? AND status='quarantined'",
                (iso(), row["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM deconstruction_uploads WHERE id=?",
                (row["id"],),
            ).fetchone()
            connection.commit()
        return dict(claimed)

    def release_upload_scan(self, upload_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE deconstruction_uploads SET status='quarantined',scanned_at=NULL "
                "WHERE id=? AND status='scanning'",
                (upload_id,),
            )

    def enforce_upload_quota(
        self,
        user_id: str,
        *,
        max_files: int,
        max_bytes: int,
    ) -> None:
        cutoff = iso(utcnow() - timedelta(hours=24))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS files,COALESCE(SUM(bytes),0) AS bytes "
                "FROM deconstruction_uploads WHERE user_id=? AND created_at>=? "
                "AND status!='rejected'",
                (user_id, cutoff),
            ).fetchone()
        if int(row["files"]) >= max_files or int(row["bytes"]) >= max_bytes:
            raise AccountError("已达到 24 小时上传限额，请稍后再试", 429)

    def enforce_submission_quota(
        self, user_id: str, *, max_files: int, max_bytes: int
    ) -> None:
        cutoff = iso(utcnow() - timedelta(hours=24))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(files),0) AS files,COALESCE(SUM(bytes),0) AS bytes FROM ("
                "SELECT COUNT(*) AS files,COALESCE(SUM(bytes),0) AS bytes FROM deconstruction_uploads "
                "WHERE user_id=? AND created_at>=? AND status!='rejected' UNION ALL "
                "SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM novel_submissions "
                "WHERE user_id=? AND created_at>=? AND status!='rejected')",
                (user_id, cutoff, user_id, cutoff),
            ).fetchone()
        if int(row["files"]) >= max_files or int(row["bytes"]) >= max_bytes:
            raise AccountError("已达到 24 小时投稿限额，请稍后再试", 429)

    def finish_upload(
        self,
        upload_id: str,
        user_id: str,
        *,
        stored_filename: str,
        size: int,
        digest: str,
        scanner: dict[str, Any],
        structure: dict[str, Any],
    ) -> None:
        reward_units = int(
            structure.get("reward_point_units")
            or structure.get("download_point_units")
            or 0
        )
        if reward_units < 0 or reward_units > MAX_DOWNLOAD_POINT_UNITS:
            raise AccountError("拆书档案审核奖励超出允许范围")
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    "UPDATE deconstruction_uploads SET stored_filename=?,bytes=?,sha256=?,status='ai_pending',"
                    "scanner_engine=?,scanner_result=?,structure_profile=?,structure_report=?,"
                    "reward_point_units=?,scanned_at=?,queued_at=?,rejection_reason='' "
                    "WHERE id=? AND user_id=? AND status='scanning'",
                    (
                        stored_filename,
                        size,
                        digest,
                        str(scanner.get("engine") or "")[:100],
                        json.dumps(scanner, ensure_ascii=False, separators=(",", ":"))[
                            :4000
                        ],
                        str(structure.get("profile") or "")[:20],
                        json.dumps(
                            structure, ensure_ascii=False, separators=(",", ":")
                        )[:20_000],
                        reward_units,
                        iso(),
                        iso(),
                        upload_id,
                        user_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AccountError("相同文件已经进入归纳队列", 409) from exc
        if int(cursor.rowcount) != 1:
            raise AccountError("上传检查任务状态已变化，请稍后刷新", 409)

    def reject_upload(self, upload_id: str, user_id: str, reason: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT task_id FROM deconstruction_uploads WHERE id=? AND user_id=?",
                (upload_id, user_id),
            ).fetchone()
            connection.execute(
                "UPDATE deconstruction_uploads SET status='rejected',rejection_reason=?,scanned_at=? "
                "WHERE id=? AND user_id=?",
                (reason[:500], iso(), upload_id, user_id),
            )
            if row and row["task_id"]:
                connection.execute(
                    "UPDATE deconstruction_tasks SET status=CASE WHEN expires_at<=? THEN 'expired' ELSE 'claimed' END,"
                    "submission_id=NULL,submitted_at=NULL WHERE id=? AND submission_id=? AND status='submitted'",
                    (iso(), row["task_id"], upload_id),
                )
            connection.commit()

    def uploads(
        self, user_id: str, *, published_only: bool = False
    ) -> list[dict[str, Any]]:
        publication_filter = (
            " AND u.status='completed' AND p.slug IS NOT NULL"
            if published_only
            else ""
        )
        with self._connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT u.id,u.original_filename,u.bytes,u.sha256,u.media_type,u.status,u.scanner_engine,"
                    "u.rejection_reason,u.created_at,u.scanned_at,u.queued_at,u.completed_at,u.output_slug,"
                    "u.structure_profile,u.structure_report,u.review_result,u.reviewed_at,u.handoff_manifest,"
                    "u.task_id,u.reward_point_units,u.cover_override_path,u.cover_override_version,"
                    "COALESCE(p.download_point_units,u.download_point_units,0) AS download_point_units,"
                    "CASE WHEN p.slug IS NULL THEN 0 ELSE 1 END AS product_available,"
                    "COALESCE((SELECT COUNT(*) FROM deconstruction_purchases purchase WHERE purchase.slug=p.slug),0) AS purchase_count,"
                    "COALESCE((SELECT SUM(point_units_spent) FROM deconstruction_purchases purchase WHERE purchase.slug=p.slug),0) AS points_earned_units "
                    "FROM deconstruction_uploads u LEFT JOIN deconstruction_products p ON p.slug=u.output_slug "
                    f"WHERE u.user_id=?{publication_filter} ORDER BY u.created_at DESC LIMIT 200",
                    (user_id,),
                )
            ]
        for row in rows:
            for key in ("structure_report", "review_result"):
                try:
                    row[key] = json.loads(row[key]) if row.get(key) else None
                except (TypeError, ValueError, json.JSONDecodeError):
                    row[key] = None
            row["handoff_ready"] = bool(row.pop("handoff_manifest", None))
            row["has_cover_override"] = bool(row.pop("cover_override_path", None))
            row["cover_override_version"] = int(
                row.get("cover_override_version") or 0
            )
            row["product_available"] = bool(row.get("product_available"))
            row["download_points"] = point_value(
                row.pop("download_point_units", 0)
            )
            row["reward_points"] = point_value(
                row.pop("reward_point_units", 0)
            )
            row["points_earned"] = point_value(row.pop("points_earned_units", 0))
        return rows

    def create_notification(
        self,
        user_id: str,
        *,
        kind: str,
        title: str,
        message: str,
        dedupe_key: str,
        action_url: str = "",
        resource_type: str = "",
        resource_id: str = "",
    ) -> str:
        notification_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO user_notifications"
                "(id,user_id,kind,title,message,action_url,resource_type,resource_id,dedupe_key,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    notification_id,
                    user_id,
                    kind[:40],
                    title[:120],
                    message[:2000],
                    action_url[:500],
                    resource_type[:40],
                    resource_id[:100],
                    dedupe_key[:200],
                    iso(),
                ),
            )
            row = connection.execute(
                "SELECT id FROM user_notifications WHERE user_id=? AND dedupe_key=?",
                (user_id, dedupe_key[:200]),
            ).fetchone()
        return str(row["id"])

    def notifications(
        self, user_id: str, *, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        bounded = min(max(int(limit), 1), 200)
        bounded_offset = min(max(int(offset), 0), 100_000)
        with self._connect() as connection:
            unread = int(
                connection.execute(
                    "SELECT COUNT(*) FROM user_notifications WHERE user_id=? AND read_at IS NULL",
                    (user_id,),
                ).fetchone()[0]
            )
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM user_notifications WHERE user_id=?",
                    (user_id,),
                ).fetchone()[0]
            )
            action_required = int(
                connection.execute(
                    "SELECT COUNT(*) FROM ("
                    "SELECT n.resource_type,n.resource_id FROM user_notifications n "
                    "WHERE n.user_id=? AND n.read_at IS NULL AND n.kind='submission_review' "
                    "AND n.resource_id IS NOT NULL AND ("
                    "EXISTS(SELECT 1 FROM novel_submissions s WHERE s.id=n.resource_id AND s.user_id=n.user_id AND s.status='rejected') "
                    "OR EXISTS(SELECT 1 FROM deconstruction_uploads d WHERE d.id=n.resource_id AND d.user_id=n.user_id AND d.status='rejected')"
                    ") GROUP BY n.resource_type,n.resource_id)",
                    (user_id,),
                ).fetchone()[0]
            )
            items = [
                dict(row)
                for row in connection.execute(
                    "SELECT id,kind,title,message,action_url,resource_type,resource_id,created_at,read_at "
                    "FROM user_notifications WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                    (user_id, bounded, bounded_offset),
                )
            ]
            for resource_type, table in (
                ("deconstruction", "deconstruction_uploads"),
                ("novel", "novel_submissions"),
            ):
                resource_ids = [
                    str(item["resource_id"])
                    for item in items
                    if item.get("resource_type") == resource_type
                    and item.get("resource_id")
                ]
                if not resource_ids:
                    continue
                placeholders = ",".join("?" for _ in resource_ids)
                title_column = (
                    "original_filename" if resource_type == "deconstruction" else "title"
                )
                resources = {
                    str(row["id"]): {
                        "status": str(row["status"]),
                        "title": str(row["resource_title"] or "").strip(),
                    }
                    for row in connection.execute(
                        f"SELECT id,status,{title_column} AS resource_title FROM {table} "
                        f"WHERE user_id=? AND id IN ({placeholders})",
                        (user_id, *resource_ids),
                    )
                }
                for item in items:
                    if item.get("resource_type") == resource_type:
                        resource = resources.get(str(item.get("resource_id") or ""), {})
                        item["resource_status"] = resource.get("status", "")
                        item["resource_title"] = resource.get("title", "")
        return {
            "items": items,
            "unread_count": unread,
            "action_required_count": action_required,
            "total_count": total,
        }

    def mark_notification_read(
        self, user_id: str, notification_id: str | None = None
    ) -> int:
        now = iso()
        with self._connect() as connection:
            if notification_id:
                cursor = connection.execute(
                    "UPDATE user_notifications SET read_at=COALESCE(read_at,?) WHERE id=? AND user_id=?",
                    (now, notification_id, user_id),
                )
            else:
                cursor = connection.execute(
                    "UPDATE user_notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",
                    (now, user_id),
                )
        return max(int(cursor.rowcount), 0)

    def create_novel_submission(
        self, user_id: str, metadata: dict[str, Any], filename: str
    ) -> str:
        submission_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO novel_submissions"
                "(id,user_id,title,author,category,serialization_status,summary,source,authorization,"
                "manuscript_filename,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    submission_id,
                    user_id,
                    metadata["title"],
                    metadata["author"],
                    metadata["category"],
                    metadata["serialization_status"],
                    metadata["summary"],
                    metadata["source"],
                    metadata["authorization"],
                    filename,
                    "quarantined",
                    iso(),
                ),
            )
        return submission_id

    def finish_novel_submission(
        self,
        submission_id: str,
        user_id: str,
        *,
        manuscript_path: str,
        cover_path: str,
        size: int,
        digest: str,
        scanner: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE novel_submissions SET manuscript_path=?,cover_path=?,bytes=?,sha256=?,"
                "scanner_result=?,status='ai_pending' WHERE id=? AND user_id=? AND status='quarantined'",
                (
                    manuscript_path,
                    cover_path,
                    int(size),
                    digest,
                    json.dumps(scanner, ensure_ascii=False, separators=(",", ":"))[
                        :20_000
                    ],
                    submission_id,
                    user_id,
                ),
            )

    def reject_novel_submission(
        self, submission_id: str, user_id: str, reason: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE novel_submissions SET status='rejected',rejection_reason=?,reviewed_at=? "
                "WHERE id=? AND user_id=?",
                (reason[:2000], iso(), submission_id, user_id),
            )

    def novel_submissions(
        self, user_id: str, *, published_only: bool = False
    ) -> list[dict[str, Any]]:
        publication_filter = " AND status='completed'" if published_only else ""
        with self._connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT id,title,author,category,serialization_status,summary,source,manuscript_filename,status,bytes,"
                    "rejection_reason,review_result,created_at,reviewed_at,completed_at,handoff_manifest,"
                    "cover_path,catalog_id,public_id,cover_override_path,cover_override_version "
                    f"FROM novel_submissions WHERE user_id=?{publication_filter} ORDER BY created_at DESC LIMIT 200",
                    (user_id,),
                )
            ]
        for row in rows:
            try:
                row["review_result"] = (
                    json.loads(row["review_result"])
                    if row.get("review_result")
                    else None
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                row["review_result"] = None
            row["handoff_ready"] = bool(row.pop("handoff_manifest", None))
            row["has_uploaded_cover"] = bool(row.pop("cover_path", None))
            row["has_cover_override"] = bool(row.pop("cover_override_path", None))
            row["cover_override_version"] = int(
                row.get("cover_override_version") or 0
            )
        return rows

    @staticmethod
    def _submission_cover_table(submission_type: str) -> str:
        kind = str(submission_type or "").casefold()
        if kind == "novel":
            return "novel_submissions"
        if kind == "deconstruction":
            return "deconstruction_uploads"
        raise AccountError("投稿类型无效", 404)

    def submission_cover_record(
        self,
        user_id: str,
        submission_type: str,
        submission_id: str,
        *,
        published_only: bool = False,
    ) -> dict[str, Any]:
        table = self._submission_cover_table(submission_type)
        with self._connect() as connection:
            if table == "novel_submissions":
                row = connection.execute(
                    "SELECT id,status,cover_path,cover_override_path,"
                    "cover_override_version FROM novel_submissions "
                    "WHERE id=? AND user_id=?",
                    (str(submission_id), str(user_id)),
                ).fetchone()
                published = bool(row and row["status"] == "completed")
                original_path = str(row["cover_path"] or "") if row else ""
            else:
                row = connection.execute(
                    "SELECT u.id,u.status,u.cover_override_path,"
                    "u.cover_override_version,p.slug AS product_slug "
                    "FROM deconstruction_uploads u "
                    "LEFT JOIN deconstruction_products p ON p.slug=u.output_slug "
                    "WHERE u.id=? AND u.user_id=?",
                    (str(submission_id), str(user_id)),
                ).fetchone()
                published = bool(
                    row and row["status"] == "completed" and row["product_slug"]
                )
                original_path = ""
            if row is None:
                raise AccountError("投稿不存在", 404)
            if published_only and not published:
                raise AccountError("投稿尚未完成正式入库", 409)
            override_path = str(row["cover_override_path"] or "")
            return {
                "id": str(row["id"]),
                "published": published,
                "path": override_path or original_path,
                "has_override": bool(override_path),
                "version": int(row["cover_override_version"] or 0),
            }

    def update_published_submission_cover(
        self,
        user_id: str,
        submission_type: str,
        submission_id: str,
        relative_path: str,
    ) -> int:
        table = self._submission_cover_table(submission_type)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if table == "novel_submissions":
                row = connection.execute(
                    "SELECT status,cover_override_version FROM novel_submissions "
                    "WHERE id=? AND user_id=?",
                    (str(submission_id), str(user_id)),
                ).fetchone()
                published = bool(row and row["status"] == "completed")
            else:
                row = connection.execute(
                    "SELECT u.status,u.cover_override_version,p.slug AS product_slug "
                    "FROM deconstruction_uploads u "
                    "LEFT JOIN deconstruction_products p ON p.slug=u.output_slug "
                    "WHERE u.id=? AND u.user_id=?",
                    (str(submission_id), str(user_id)),
                ).fetchone()
                published = bool(
                    row and row["status"] == "completed" and row["product_slug"]
                )
            if row is None:
                connection.rollback()
                raise AccountError("投稿不存在", 404)
            if not published:
                connection.rollback()
                raise AccountError("只有正式入库的投稿可以更新封面", 409)
            version = int(row["cover_override_version"] or 0) + 1
            connection.execute(
                f"UPDATE {table} SET cover_override_path=?,cover_override_version=? "
                "WHERE id=? AND user_id=?",
                (str(relative_path), version, str(submission_id), str(user_id)),
            )
            connection.commit()
        return version

    def claim_review(self) -> dict[str, Any] | None:
        """Atomically claim one queued review across both submission types."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT 'deconstruction' AS submission_type,id,user_id,created_at "
                "FROM deconstruction_uploads WHERE status='ai_pending' "
                "UNION ALL SELECT 'novel',id,user_id,created_at FROM novel_submissions "
                "WHERE status='ai_pending' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                connection.commit()
                return None
            table = (
                "deconstruction_uploads"
                if row["submission_type"] == "deconstruction"
                else "novel_submissions"
            )
            connection.execute(
                f"UPDATE {table} SET status='reviewing' WHERE id=? AND status='ai_pending'",
                (row["id"],),
            )
            payload = dict(
                connection.execute(
                    f"SELECT * FROM {table} WHERE id=?", (row["id"],)
                ).fetchone()
            )
            connection.commit()
        payload["submission_type"] = row["submission_type"]
        return payload

    def release_review(
        self, submission_type: str, submission_id: str, reason: str
    ) -> None:
        table = (
            "deconstruction_uploads"
            if submission_type == "deconstruction"
            else "novel_submissions"
        )
        with self._connect() as connection:
            if table == "deconstruction_uploads":
                connection.execute(
                    "UPDATE deconstruction_uploads SET status='ai_pending',review_attempts=review_attempts+1,"
                    "rejection_reason=? WHERE id=? AND status='reviewing'",
                    (reason[:500], submission_id),
                )
            else:
                connection.execute(
                    "UPDATE novel_submissions SET status='ai_pending',rejection_reason=? "
                    "WHERE id=? AND status='reviewing'",
                    (reason[:500], submission_id),
                )

    def complete_review(
        self,
        submission_type: str,
        submission_id: str,
        result: dict[str, Any],
        *,
        handoff_manifest: str = "",
    ) -> dict[str, Any] | None:
        table = (
            "deconstruction_uploads"
            if submission_type == "deconstruction"
            else "novel_submissions"
        )
        approved = result.get("decision") == "approve"
        status = "approved" if approved else "rejected"
        reason = "" if approved else str(result.get("reason") or "审核未通过")[:2000]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT user_id{',task_id' if table == 'deconstruction_uploads' else ''} FROM {table} WHERE id=? AND status='reviewing'",
                (submission_id,),
            ).fetchone()
            if not row:
                connection.rollback()
                return None
            connection.execute(
                f"UPDATE {table} SET status=?,review_result=?,rejection_reason=?,reviewed_at=?,handoff_manifest=? "
                "WHERE id=? AND status='reviewing'",
                (
                    status,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":"))[
                        :20_000
                    ],
                    reason,
                    iso(),
                    handoff_manifest[:2000],
                    submission_id,
                ),
            )
            if not approved and table == "deconstruction_uploads" and row["task_id"]:
                connection.execute(
                    "UPDATE deconstruction_tasks SET status=CASE WHEN expires_at<=? THEN 'expired' ELSE 'claimed' END,"
                    "submission_id=NULL,submitted_at=NULL WHERE id=? AND submission_id=? AND status='submitted'",
                    (iso(), row["task_id"], submission_id),
                )
            connection.commit()
        return {"user_id": str(row["user_id"]), "status": status}

    def handoff_records(self, user_id: str | None = None) -> list[dict[str, Any]]:
        where = " AND user_id=?" if user_id else ""
        params: tuple[Any, ...] = (user_id,) if user_id else ()
        records: list[dict[str, Any]] = []
        with self._connect() as connection:
            for submission_type, table in (
                ("deconstruction", "deconstruction_uploads"),
                ("novel", "novel_submissions"),
            ):
                title_column = (
                    "original_filename" if submission_type == "deconstruction" else "title"
                )
                rows = connection.execute(
                    f"SELECT id,user_id,status,handoff_manifest,{title_column} AS resource_title FROM {table} "
                    f"WHERE handoff_manifest IS NOT NULL AND handoff_manifest!='' "
                    f"AND status='approved'{where} LIMIT 200",
                    params,
                )
                records.extend(
                    {**dict(row), "submission_type": submission_type} for row in rows
                )
        return records

    @staticmethod
    def _credit_deconstruction_reward(
        connection: sqlite3.Connection,
        *,
        user_id: str,
        submission_id: str,
        reward_units: int,
        created_at: str,
    ) -> bool:
        """Credit one published submission exactly once inside its transaction."""
        units = int(reward_units or 0)
        if units <= 0:
            return False
        existing = connection.execute(
            "SELECT 1 FROM user_point_ledger WHERE user_id=? "
            "AND kind='deconstruction_upload_reward' "
            "AND reference_type='submission' AND reference_id=?",
            (user_id, submission_id),
        ).fetchone()
        if existing:
            return False
        wallet = connection.execute(
            "SELECT balance_units FROM user_point_wallets WHERE user_id=?",
            (user_id,),
        ).fetchone()
        balance_units = int(wallet["balance_units"] if wallet else 0) + units
        balance = point_value(balance_units)
        connection.execute(
            "INSERT INTO user_point_wallets(user_id,balance,balance_units,updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "balance=excluded.balance,balance_units=excluded.balance_units,"
            "updated_at=excluded.updated_at",
            (user_id, balance, balance_units, created_at),
        )
        connection.execute(
            "INSERT INTO user_point_ledger(id,user_id,delta,balance_after,delta_units,"
            "balance_after_units,kind,reference_type,reference_id,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                user_id,
                point_value(units),
                balance,
                units,
                balance_units,
                "deconstruction_upload_reward",
                "submission",
                submission_id,
                created_at,
            ),
        )
        return True

    def grant_completed_deconstruction_reward(
        self, submission_id: str
    ) -> dict[str, Any]:
        """Idempotently backfill/reconcile one formally published contribution."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT u.user_id,u.status,u.output_slug,u.reward_point_units,"
                "u.structure_report,p.slug AS product_slug "
                "FROM deconstruction_uploads u "
                "LEFT JOIN deconstruction_products p ON p.slug=u.output_slug "
                "WHERE u.id=?",
                (str(submission_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise AccountError("拆文投稿不存在", 404)
            if row["status"] != "completed" or not row["product_slug"]:
                connection.rollback()
                raise AccountError("拆文尚未完成审核入库，不能发放奖励", 409)
            reward_units = int(row["reward_point_units"] or 0)
            if reward_units <= 0:
                try:
                    structure = json.loads(str(row["structure_report"] or "{}"))
                    reward_units = int(
                        structure.get("reward_point_units")
                        or structure.get("download_point_units")
                        or deconstruction_reward_units(
                            int(structure.get("original_text_char_count") or 0)
                        )
                    )
                except (
                    AttributeError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ) as exc:
                    connection.rollback()
                    raise AccountError("拆文原文字数不可用，不能发放奖励", 409) from exc
                connection.execute(
                    "UPDATE deconstruction_uploads SET reward_point_units=? WHERE id=?",
                    (reward_units, str(submission_id)),
                )
            now = iso()
            granted = self._credit_deconstruction_reward(
                connection,
                user_id=str(row["user_id"]),
                submission_id=str(submission_id),
                reward_units=reward_units,
                created_at=now,
            )
            connection.commit()
        return self.wallet_summary(str(row["user_id"])) | {
            "submission_id": str(submission_id),
            "reward_points": point_value(reward_units),
            "granted": granted,
        }

    def complete_handoff(
        self, submission_type: str, submission_id: str, result: dict[str, Any]
    ) -> dict[str, Any] | None:
        table = (
            "deconstruction_uploads"
            if submission_type == "deconstruction"
            else "novel_submissions"
        )
        succeeded = str(result.get("status") or "").casefold() == "completed"
        status = "completed" if succeeded else "rejected"
        message = str(
            result.get("message") or ("投稿已入库" if succeeded else "入库阶段未通过")
        )[:2000]
        task_creator_user_id = ""
        completed_task_id = ""
        reward_units = 0
        reward_granted = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT user_id{',task_id,download_points,download_point_units,reward_point_units' if table == 'deconstruction_uploads' else ''} FROM {table} WHERE id=? AND status='approved'",
                (submission_id,),
            ).fetchone()
            if not row:
                connection.rollback()
                return None
            extra = ""
            values: list[Any] = [
                status,
                iso() if succeeded else None,
                "" if succeeded else message,
            ]
            if table == "deconstruction_uploads":
                extra = ",output_slug=?"
                values.append(str(result.get("output_slug") or "")[:160])
            elif succeeded:
                extra = ",catalog_id=?,public_id=?"
                values.extend(
                    [
                        str(result.get("catalog_id") or "")[:80],
                        str(result.get("public_id") or "")[:80],
                    ]
                )
            connection.execute(
                f"UPDATE {table} SET status=?,completed_at=?,rejection_reason=?{extra} WHERE id=? AND status='approved'",
                (*values, submission_id),
            )
            if table == "deconstruction_uploads":
                task_id = str(row["task_id"] or "")
                completed_task_id = task_id
                output_slug = str(result.get("output_slug") or "")[:160]
                if succeeded and output_slug:
                    connection.execute(
                        "INSERT INTO deconstruction_products(slug,contributor_user_id,task_id,download_points,"
                        "download_point_units,published_at) VALUES(?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET "
                        "contributor_user_id=excluded.contributor_user_id,task_id=excluded.task_id,"
                        "download_points=excluded.download_points,download_point_units=excluded.download_point_units,"
                        "published_at=excluded.published_at",
                        (
                            output_slug,
                            row["user_id"],
                            task_id or None,
                            point_value(row["download_point_units"] or 0),
                            int(row["download_point_units"] or 0),
                            iso(),
                        ),
                    )
                    reward_units = int(row["reward_point_units"] or 0)
                    reward_granted = self._credit_deconstruction_reward(
                        connection,
                        user_id=str(row["user_id"]),
                        submission_id=str(submission_id),
                        reward_units=reward_units,
                        created_at=iso(),
                    )
                    if task_id:
                        creator = connection.execute(
                            "SELECT creator_user_id FROM deconstruction_tasks WHERE id=?",
                            (task_id,),
                        ).fetchone()
                        task_creator_user_id = str(creator["creator_user_id"]) if creator else ""
                        connection.execute(
                            "UPDATE deconstruction_tasks SET status='completed',output_slug=?,completed_at=? "
                            "WHERE id=? AND submission_id=?",
                            (output_slug, iso(), task_id, submission_id),
                        )
                elif task_id:
                    connection.execute(
                        "UPDATE deconstruction_tasks SET status=CASE WHEN expires_at<=? THEN 'expired' ELSE 'claimed' END,"
                        "submission_id=NULL,submitted_at=NULL WHERE id=? AND submission_id=?",
                        (iso(), task_id, submission_id),
                    )
            connection.commit()
        return {
            "user_id": str(row["user_id"]),
            "status": status,
            "message": message,
            "task_creator_user_id": task_creator_user_id,
            "task_id": completed_task_id,
            "reward_points": point_value(reward_units),
            "reward_granted": reward_granted,
        }

    def deconstruction_access(self, user_id: str, slug: str) -> dict[str, Any]:
        normalized = str(slug or "").strip()[:160]
        with self._connect() as connection:
            product = connection.execute(
                "SELECT p.download_point_units,p.contributor_user_id,p.task_id,t.creator_user_id "
                "FROM deconstruction_products p LEFT JOIN deconstruction_tasks t ON t.id=p.task_id "
                "WHERE p.slug=?",
                (normalized,),
            ).fetchone()
            purchased = connection.execute(
                "SELECT point_units_spent,purchased_at FROM deconstruction_purchases WHERE user_id=? AND slug=?",
                (user_id, normalized),
            ).fetchone()
        if product is None:
            return {
                "slug": normalized,
                "download_points": 0,
                "can_download": True,
                "purchased": False,
                "is_contributor": False,
                "is_requester": False,
            }
        is_contributor = str(product["contributor_user_id"]) == user_id
        is_requester = str(product["creator_user_id"] or "") == user_id
        price_units = int(product["download_point_units"] or 0)
        return {
            "slug": normalized,
            "download_points": point_value(price_units),
            "can_download": price_units == 0
            or is_contributor
            or is_requester
            or purchased is not None,
            "purchased": purchased is not None,
            "is_contributor": is_contributor,
            "is_requester": is_requester,
            "purchased_at": str(purchased["purchased_at"]) if purchased else None,
        }

    def update_deconstruction_price(
        self, user_id: str, slug: str, download_points: Any
    ) -> dict[str, Any]:
        """Update a contributor-owned product and its completed submission mirror."""
        normalized = str(slug or "").strip()[:160]
        if not normalized:
            raise AccountError("拆书档案不存在", 404)
        try:
            price_units = point_units(download_points)
        except ValueError as exc:
            raise AccountError(str(exc)) from exc
        price = point_value(price_units)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            product = connection.execute(
                "SELECT contributor_user_id FROM deconstruction_products WHERE slug=?",
                (normalized,),
            ).fetchone()
            if product is None:
                connection.rollback()
                raise AccountError("拆书档案尚未完成入库", 409)
            if str(product["contributor_user_id"]) != user_id:
                connection.rollback()
                raise AccountError("只有档案贡献者可以修改下载积分", 403)
            connection.execute(
                "UPDATE deconstruction_products SET download_points=?,download_point_units=? "
                "WHERE slug=?",
                (price, price_units, normalized),
            )
            connection.execute(
                "UPDATE deconstruction_uploads SET download_points=?,download_point_units=? "
                "WHERE user_id=? AND output_slug=? AND status='completed'",
                (price, price_units, user_id, normalized),
            )
            sales = connection.execute(
                "SELECT COUNT(*) AS purchase_count,"
                "COALESCE(SUM(point_units_spent),0) AS points_earned_units "
                "FROM deconstruction_purchases WHERE slug=?",
                (normalized,),
            ).fetchone()
            connection.commit()
        return {
            "slug": normalized,
            "download_points": price,
            "purchase_count": int(sales["purchase_count"] or 0),
            "points_earned": point_value(sales["points_earned_units"] or 0),
            "updated_at": iso(),
        }

    def purchase_deconstruction(
        self,
        user_id: str,
        slug: str,
        *,
        expected_points: Any | None = None,
    ) -> dict[str, Any]:
        normalized = str(slug or "").strip()[:160]
        now = iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            product = connection.execute(
                "SELECT p.download_point_units,p.contributor_user_id,t.creator_user_id "
                "FROM deconstruction_products p LEFT JOIN deconstruction_tasks t ON t.id=p.task_id WHERE p.slug=?",
                (normalized,),
            ).fetchone()
            if product is None:
                connection.rollback()
                raise AccountError("该档案无需积分购买", 409)
            price_units = int(product["download_point_units"] or 0)
            price = point_value(price_units)
            if price_units == 0 or user_id in {
                str(product["contributor_user_id"]),
                str(product["creator_user_id"] or ""),
            }:
                connection.commit()
                return self.deconstruction_access(user_id, normalized) | {"charged": 0}
            existing = connection.execute(
                "SELECT 1 FROM deconstruction_purchases WHERE user_id=? AND slug=?",
                (user_id, normalized),
            ).fetchone()
            if existing:
                connection.commit()
                return self.deconstruction_access(user_id, normalized) | {"charged": 0}
            if expected_points is not None:
                try:
                    expected_units = point_units(expected_points)
                except ValueError as exc:
                    connection.rollback()
                    raise AccountError(str(exc)) from exc
                if price_units != expected_units:
                    connection.rollback()
                    raise AccountError(
                        f"档案下载积分已更新为 {point_label(price_units)}，请确认最新价格后再购买",
                        409,
                    )
            wallet = connection.execute(
                "SELECT balance_units FROM user_point_wallets WHERE user_id=?",
                (user_id,),
            ).fetchone()
            balance_units = int(wallet["balance_units"] if wallet else 0)
            if balance_units < price_units:
                connection.rollback()
                raise AccountError("积分不足，可用阅读时长兑换积分", 409)
            new_balance_units = balance_units - price_units
            new_balance = point_value(new_balance_units)
            contributor_user_id = str(product["contributor_user_id"])
            contributor_wallet = connection.execute(
                "SELECT balance_units FROM user_point_wallets WHERE user_id=?",
                (contributor_user_id,),
            ).fetchone()
            contributor_balance_units = int(
                contributor_wallet["balance_units"] if contributor_wallet else 0
            ) + price_units
            contributor_balance = point_value(contributor_balance_units)
            connection.execute(
                "UPDATE user_point_wallets SET balance=?,balance_units=?,updated_at=? WHERE user_id=?",
                (new_balance, new_balance_units, now, user_id),
            )
            connection.execute(
                "INSERT INTO user_point_wallets(user_id,balance,balance_units,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET balance=excluded.balance,"
                "balance_units=excluded.balance_units,updated_at=excluded.updated_at",
                (
                    contributor_user_id,
                    contributor_balance,
                    contributor_balance_units,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO deconstruction_purchases(user_id,slug,points_spent,point_units_spent,purchased_at) "
                "VALUES(?,?,?,?,?)",
                (user_id, normalized, price, price_units, now),
            )
            connection.execute(
                "INSERT INTO user_point_ledger(id,user_id,delta,balance_after,delta_units,balance_after_units,"
                "kind,reference_type,reference_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    user_id,
                    -price,
                    new_balance,
                    -price_units,
                    new_balance_units,
                    "deconstruction_purchase",
                    "slug",
                    normalized,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO user_point_ledger(id,user_id,delta,balance_after,delta_units,balance_after_units,"
                "kind,reference_type,reference_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    contributor_user_id,
                    price,
                    contributor_balance,
                    price_units,
                    contributor_balance_units,
                    "deconstruction_sale",
                    "purchase",
                    f"{normalized}:{user_id}",
                    now,
                ),
            )
            connection.commit()
        return self.deconstruction_access(user_id, normalized) | {"charged": price, "balance": new_balance}

    def deconstruction_engagement(
        self, slugs: list[str], *, viewer_user_id: str | None = None
    ) -> dict[str, dict[str, Any]]:
        normalized = list(
            dict.fromkeys(str(value or "").strip()[:160] for value in slugs)
        )[:500]
        normalized = [value for value in normalized if value]
        if not normalized:
            return {}
        placeholders = ",".join("?" for _ in normalized)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT slug,COUNT(*) AS like_count FROM deconstruction_likes "
                f"WHERE slug IN ({placeholders}) GROUP BY slug",
                normalized,
            ).fetchall()
            liked: set[str] = set()
            if viewer_user_id:
                liked = {
                    str(row["slug"])
                    for row in connection.execute(
                        f"SELECT slug FROM deconstruction_likes WHERE user_id=? "
                        f"AND slug IN ({placeholders})",
                        (str(viewer_user_id), *normalized),
                    ).fetchall()
                }
            download_rows = connection.execute(
                f"SELECT slug,download_count FROM deconstruction_download_metrics "
                f"WHERE slug IN ({placeholders})",
                normalized,
            ).fetchall()
        counts = {str(row["slug"]): int(row["like_count"]) for row in rows}
        downloads = {
            str(row["slug"]): int(row["download_count"])
            for row in download_rows
        }
        return {
            slug: {
                "like_count": counts.get(slug, 0),
                "viewer_liked": slug in liked,
                "download_count": downloads.get(slug, 0),
            }
            for slug in normalized
        }

    def deconstruction_download_counts(
        self, slugs: list[str] | tuple[str, ...]
    ) -> dict[str, int]:
        normalized = tuple(dict.fromkeys(str(slug) for slug in slugs if str(slug)))
        if not normalized:
            return {}
        placeholders = ",".join("?" for _ in normalized)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT slug,download_count FROM deconstruction_download_metrics "
                f"WHERE slug IN ({placeholders})",
                normalized,
            ).fetchall()
        result = {slug: 0 for slug in normalized}
        result.update(
            {str(row["slug"]): int(row["download_count"]) for row in rows}
        )
        return result

    def increment_deconstruction_download(self, slug: str) -> int:
        normalized = str(slug or "").strip()
        if not normalized or len(normalized) > 160 or normalized.startswith("."):
            raise AccountError("拆书档案标识无效", 422)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "INSERT INTO deconstruction_download_metrics"
                "(slug,download_count,updated_at) VALUES(?,1,?) "
                "ON CONFLICT(slug) DO UPDATE SET "
                "download_count=download_count+1,updated_at=excluded.updated_at "
                "RETURNING download_count",
                (normalized, iso()),
            ).fetchone()
            connection.commit()
        return int(row["download_count"])

    def toggle_deconstruction_like(self, user_id: str, slug: str) -> dict[str, Any]:
        normalized = str(slug or "").strip()[:160]
        if not normalized:
            raise AccountError("拆书档案不存在", 404)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM deconstruction_likes WHERE slug=? AND user_id=?",
                (normalized, str(user_id)),
            ).fetchone()
            if existing:
                connection.execute(
                    "DELETE FROM deconstruction_likes WHERE slug=? AND user_id=?",
                    (normalized, str(user_id)),
                )
                liked = False
            else:
                connection.execute(
                    "INSERT INTO deconstruction_likes(slug,user_id,created_at) "
                    "VALUES(?,?,?)",
                    (normalized, str(user_id), iso()),
                )
                liked = True
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM deconstruction_likes WHERE slug=?",
                    (normalized,),
                ).fetchone()[0]
            )
            connection.commit()
        return {"slug": normalized, "liked": liked, "like_count": count}

    def record_admin_audit(
        self,
        actor_user_id: str,
        action: str,
        resource_type: str,
        resource_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO admin_audit_events"
                "(id,actor_user_id,action,resource_type,resource_id,detail,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    str(actor_user_id),
                    str(action)[:80],
                    str(resource_type)[:40],
                    str(resource_id)[:160],
                    json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":"))[
                        :4000
                    ],
                    iso(),
                ),
            )

    def admin_summary(self) -> dict[str, int]:
        with self._connect() as connection:
            user = connection.execute(
                "SELECT COUNT(*) total,SUM(status='active') active FROM users"
            ).fetchone()
            invites = connection.execute(
                "SELECT COUNT(*) total,SUM(disabled_at IS NULL AND (expires_at IS NULL OR expires_at>?) "
                "AND used_count<max_uses) active FROM registration_invites",
                (iso(),),
            ).fetchone()
            uploads = connection.execute(
                "SELECT COUNT(*) total,SUM(status='completed') completed,"
                "SUM(status IN ('approved','ai_pending','reviewing')) pending FROM novel_submissions"
            ).fetchone()
        return {
            "users": int(user["total"] or 0),
            "active_users": int(user["active"] or 0),
            "invites": int(invites["total"] or 0),
            "active_invites": int(invites["active"] or 0),
            "novel_uploads": int(uploads["total"] or 0),
            "published_uploads": int(uploads["completed"] or 0),
            "pending_uploads": int(uploads["pending"] or 0),
        }

    def admin_users(
        self,
        *,
        query: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        conditions: list[str] = []
        params: list[Any] = []
        cleaned = " ".join(str(query or "").split())[:100]
        if cleaned:
            conditions.append(
                "(u.email LIKE ? ESCAPE '\\' OR u.display_name LIKE ? ESCAPE '\\')"
            )
            needle = (
                "%"
                + cleaned.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            params.extend((needle, needle))
        if status in {"active", "disabled"}:
            conditions.append("u.status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        page_size = min(max(int(page_size), 1), 100)
        page = max(int(page), 1)
        with self._connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM users u{where}", tuple(params)
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT u.id,u.email,u.display_name,u.status,u.role,u.email_verified_at,"
                "u.created_at,u.last_login_at,COALESCE(t.active_seconds,0) active_seconds,"
                "COUNT(DISTINCT s.id) active_sessions FROM users u "
                "LEFT JOIN user_reading_totals t ON t.user_id=u.id "
                "LEFT JOIN user_sessions s ON s.user_id=u.id AND s.revoked_at IS NULL AND s.expires_at>?"
                f"{where} GROUP BY u.id ORDER BY u.created_at DESC,u.id DESC LIMIT ? OFFSET ?",
                (iso(), *params, page_size, (page - 1) * page_size),
            ).fetchall()
        return {
            "items": [
                dict(row) | {"email_verified": bool(row["email_verified_at"])}
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    def admin_update_user(
        self,
        actor_user_id: str,
        user_id: str,
        *,
        status: str,
    ) -> dict[str, Any]:
        if status not in {"active", "disabled"}:
            raise AccountError("用户状态无效", 422)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            actor = connection.execute(
                "SELECT role FROM users WHERE id=? AND status='active'",
                (str(actor_user_id),),
            ).fetchone()
            target = connection.execute(
                "SELECT id,email,display_name,status,role FROM users WHERE id=?",
                (str(user_id),),
            ).fetchone()
            if not actor or actor["role"] not in {"admin", "owner"}:
                connection.rollback()
                raise AccountError("需要管理员权限", 403)
            if not target:
                connection.rollback()
                raise AccountError("用户不存在", 404)
            if str(target["id"]) == str(actor_user_id) and status != "active":
                connection.rollback()
                raise AccountError("不能在当前会话中停用自己", 409)
            connection.execute(
                "UPDATE users SET status=?,updated_at=? WHERE id=?",
                (status, iso(), str(user_id)),
            )
            if status == "disabled":
                connection.execute(
                    "UPDATE user_sessions SET revoked_at=COALESCE(revoked_at,?) WHERE user_id=?",
                    (iso(), str(user_id)),
                )
            connection.commit()
        return {
            "id": str(target["id"]),
            "email": str(target["email"]),
            "display_name": str(target["display_name"]),
            "status": status,
            "role": str(target["role"]),
        }

    def sync_managed_categories(self, source_names: list[str]) -> None:
        now = iso()
        with self._connect() as connection:
            for index, raw in enumerate(source_names):
                source = " ".join(str(raw or "").split())[:40]
                if not source:
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO managed_categories"
                    "(id,source_name,display_name,sort_order,is_custom,created_at,updated_at) "
                    "VALUES(?,?,?,?,0,?,?)",
                    (str(uuid.uuid4()), source, source, (index + 1) * 10, now, now),
                )

    def managed_categories(
        self, *, include_disabled: bool = False
    ) -> list[dict[str, Any]]:
        where = "" if include_disabled else " WHERE enabled=1"
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,source_name,display_name,description,enabled,sort_order,is_custom,"
                f"created_at,updated_at FROM managed_categories{where} "
                "ORDER BY sort_order,display_name,id"
            ).fetchall()
        return [
            dict(row)
            | {"enabled": bool(row["enabled"]), "is_custom": bool(row["is_custom"])}
            for row in rows
        ]

    def create_managed_category(
        self, name: str, *, description: str = "", sort_order: int = 100
    ) -> dict[str, Any]:
        display = " ".join(unicodedata.normalize("NFKC", str(name or "")).split())
        description = " ".join(
            unicodedata.normalize("NFKC", str(description or "")).split()
        )
        if not 1 <= len(display) <= 40 or len(description) > 240:
            raise AccountError("分类名称或简介长度无效", 422)
        category_id = str(uuid.uuid4())
        now = iso()
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO managed_categories"
                    "(id,source_name,display_name,description,sort_order,is_custom,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,1,?,?)",
                    (
                        category_id,
                        display,
                        display,
                        description,
                        min(max(int(sort_order), 0), 10000),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise AccountError("该分类已经存在", 409) from exc
        return self.managed_category(category_id)

    def managed_category(self, category_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,source_name,display_name,description,enabled,sort_order,is_custom,"
                "created_at,updated_at FROM managed_categories WHERE id=?",
                (str(category_id),),
            ).fetchone()
        if not row:
            raise AccountError("分类不存在", 404)
        return dict(row) | {
            "enabled": bool(row["enabled"]),
            "is_custom": bool(row["is_custom"]),
        }

    def update_managed_category(
        self,
        category_id: str,
        *,
        display_name: str,
        description: str,
        enabled: bool,
        sort_order: int,
    ) -> dict[str, Any]:
        display = " ".join(
            unicodedata.normalize("NFKC", str(display_name or "")).split()
        )
        description = " ".join(
            unicodedata.normalize("NFKC", str(description or "")).split()
        )
        if not 1 <= len(display) <= 40 or len(description) > 240:
            raise AccountError("分类名称或简介长度无效", 422)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE managed_categories SET display_name=?,description=?,enabled=?,sort_order=?,updated_at=? "
                "WHERE id=?",
                (
                    display,
                    description,
                    int(bool(enabled)),
                    min(max(int(sort_order), 0), 10000),
                    iso(),
                    str(category_id),
                ),
            )
        if cursor.rowcount != 1:
            raise AccountError("分类不存在", 404)
        return self.managed_category(category_id)

    def delete_managed_category(self, category_id: str) -> dict[str, Any]:
        category = self.managed_category(category_id)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM managed_categories WHERE id=?", (str(category_id),)
            )
        return category

    def mark_admin_novel_submission(self, submission_id: str, user_id: str) -> None:
        result = {
            "admin_approved": True,
            "decision": "approve",
            "reason": "管理员已确认资料与发布授权，等待隔离内容复核",
        }
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE novel_submissions SET review_result=?,rejection_reason='' "
                "WHERE id=? AND user_id=? AND status='ai_pending'",
                (
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    str(submission_id),
                    str(user_id),
                ),
            )
        if cursor.rowcount != 1:
            raise AccountError("上传记录状态已变化，请刷新后重试", 409)

    def admin_novel_submissions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT n.id,n.user_id,n.title,n.author,n.category,n.serialization_status,n.status,"
                "n.bytes,n.rejection_reason,n.created_at,n.reviewed_at,n.completed_at,u.display_name "
                "FROM novel_submissions n JOIN users u ON u.id=n.user_id "
                "ORDER BY n.created_at DESC,n.id DESC LIMIT ?",
                (min(max(int(limit), 1), 200),),
            ).fetchall()
        return [dict(row) for row in rows]
