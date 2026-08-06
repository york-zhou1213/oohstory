from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIBRARY_ROOT = PROJECT_ROOT / "data" / "library"
OOHSTORY_DEFAULT_COVER_SHA256 = (
    "d421cee15a266d258979455101443085bbc686504ee802c55686cbfc92d0b09e"
)


@dataclass(frozen=True)
class Settings:
    library_root: Path
    state_root: Path
    allowed_hosts: tuple[str, ...]
    catalog_backend: str = "sqlite"
    object_root: Path | None = None
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "oohstory_library"
    mysql_user: str = "oohstory_reader"
    mysql_password_file: Path | None = None
    mysql_pool_size: int = 8
    public_deconstruction_root: Path | None = None
    catalog_source_root: Path | None = None
    max_source_bytes: int = 128 * 1024 * 1024
    max_index_source_bytes: int = 32 * 1024 * 1024
    max_chapter_bytes: int = 2 * 1024 * 1024
    max_chapter_count: int = 10_000
    max_report_chars: int = 200_000
    max_cover_bytes: int = 20 * 1024 * 1024
    account_database: Path | None = None
    session_cookie: str = "oohstory_session"
    session_ttl_seconds: int = 30 * 24 * 60 * 60
    google_web_client_id: str = ""
    google_android_client_id: str = ""
    google_ios_client_id: str = ""
    google_redirect_uri: str = "http://localhost:8091/api/v1/auth/google/callback"
    upload_root: Path | None = None
    avatar_root: Path | None = None
    max_avatar_bytes: int = 5 * 1024 * 1024
    max_avatar_pixels: int = 2048 * 2048
    max_upload_bytes: int = 64 * 1024 * 1024
    upload_daily_files: int = 5
    upload_daily_bytes: int = 256 * 1024 * 1024
    submission_review_command: tuple[str, ...] = ()
    submission_review_timeout: int = 300
    submission_handoff_root: Path | None = None
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password_file: Path | None = None
    smtp_from: str = ""
    smtp_starttls: bool = True
    public_origin: str = "http://localhost:8091"

    @property
    def catalog_path(self) -> Path:
        return self.library_root / "catalog.sqlite3"

    @property
    def books_root(self) -> Path:
        return self.library_root / "书籍"

    @property
    def index_path(self) -> Path:
        return self.library_root / "全局索引" / "electronic_library_index.sqlite3"

    @property
    def shared_reader_index_root(self) -> Path:
        return (
            (self.object_root or self.library_root)
            / "全局索引"
            / "阅读目录"
        )

    @property
    def reader_index_root(self) -> Path:
        return self.state_root / "reader-index"

    @property
    def cover_root(self) -> Path:
        return (self.object_root or self.library_root) / "封面"

    @property
    def cover_index_path(self) -> Path:
        return self.cover_root / "index.sqlite3"

    @property
    def default_cover_path(self) -> Path:
        configured = os.getenv("OOHSTORY_DEFAULT_COVER_PATH", "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return (
            self.library_root
            / ".oohstory-default-assets"
            / f"oohstory-default-cover-{OOHSTORY_DEFAULT_COVER_SHA256}.jpg"
        ).resolve()

    @property
    def deconstruction_root(self) -> Path:
        return (
            self.public_deconstruction_root
            if self.public_deconstruction_root is not None
            else self.library_root / "全局拆书库"
        )

    @property
    def user_database_path(self) -> Path:
        return self.account_database or self.state_root / "accounts.sqlite3"

    @property
    def user_upload_root(self) -> Path:
        return self.upload_root or self.state_root / "user-uploads"

    @property
    def user_avatar_root(self) -> Path:
        return self.avatar_root or self.state_root / "user-avatars"

    @property
    def user_submission_handoff_root(self) -> Path:
        return (
            self.submission_handoff_root
            or self.library_root / "全局索引" / "用户投稿队列"
        )


def load_settings() -> Settings:
    library_root = Path(
        os.getenv("OOHSTORY_LIBRARY_ROOT", str(DEFAULT_LIBRARY_ROOT))
    ).expanduser().resolve()
    state_root = Path(
        os.getenv("OOHSTORY_STATE_ROOT", str(PROJECT_ROOT / "var"))
    ).expanduser().resolve()
    raw_hosts = os.getenv(
        "OOHSTORY_ALLOWED_HOSTS",
        "localhost,127.0.0.1,testserver",
    )
    allowed_hosts = tuple(
        host.strip().lower() for host in raw_hosts.split(",") if host.strip()
    )
    raw_deconstruction_root = os.getenv(
        "OOHSTORY_DECONSTRUCTION_ROOT", ""
    ).strip()
    raw_catalog_source_root = os.getenv(
        "OOHSTORY_CATALOG_SOURCE_ROOT", ""
    ).strip()
    catalog_backend = os.getenv(
        "OOHSTORY_CATALOG_BACKEND", "sqlite"
    ).strip().casefold()
    if catalog_backend not in {"mysql", "sqlite"}:
        raise RuntimeError("OOHSTORY_CATALOG_BACKEND 必须是 mysql 或 sqlite")
    raw_object_root = os.getenv("OOHSTORY_OBJECT_ROOT", "").strip()
    raw_password_file = os.getenv(
        "OOHSTORY_MYSQL_PASSWORD_FILE", ""
    ).strip()
    raw_account_database = os.getenv(
        "OOHSTORY_ACCOUNT_DATABASE", ""
    ).strip()
    raw_upload_root = os.getenv("OOHSTORY_USER_UPLOAD_ROOT", "").strip()
    raw_avatar_root = os.getenv("OOHSTORY_USER_AVATAR_ROOT", "").strip()
    raw_handoff_root = os.getenv("OOHSTORY_SUBMISSION_HANDOFF_ROOT", "").strip()
    raw_review_command = os.getenv("OOHSTORY_SUBMISSION_REVIEW_COMMAND", "").strip()
    raw_smtp_password_file = os.getenv("OOHSTORY_SMTP_PASSWORD_FILE", "").strip()
    return Settings(
        library_root=library_root,
        state_root=state_root,
        allowed_hosts=allowed_hosts,
        catalog_backend=catalog_backend,
        object_root=(
            Path(raw_object_root).expanduser().resolve()
            if raw_object_root
            else library_root
        ),
        mysql_host=os.getenv("OOHSTORY_MYSQL_HOST", "127.0.0.1").strip(),
        mysql_port=int(os.getenv("OOHSTORY_MYSQL_PORT", "3306")),
        mysql_database=os.getenv(
            "OOHSTORY_MYSQL_DATABASE", "oohstory_library"
        ).strip(),
        mysql_user=os.getenv(
            "OOHSTORY_MYSQL_USER", "oohstory_reader"
        ).strip(),
        mysql_password_file=(
            Path(raw_password_file).expanduser().resolve()
            if raw_password_file
            else None
        ),
        mysql_pool_size=min(
            max(int(os.getenv("OOHSTORY_MYSQL_POOL_SIZE", "8")), 1),
            24,
        ),
        account_database=(
            Path(raw_account_database).expanduser().resolve()
            if raw_account_database
            else None
        ),
        session_cookie=os.getenv(
            "OOHSTORY_SESSION_COOKIE", "oohstory_session"
        ).strip(),
        session_ttl_seconds=min(
            max(int(os.getenv("OOHSTORY_SESSION_TTL", str(30 * 24 * 60 * 60))), 3600),
            90 * 24 * 60 * 60,
        ),
        google_web_client_id=os.getenv("OOHSTORY_GOOGLE_WEB_CLIENT_ID", "").strip(),
        google_android_client_id=os.getenv(
            "OOHSTORY_GOOGLE_ANDROID_CLIENT_ID", ""
        ).strip(),
        google_ios_client_id=os.getenv("OOHSTORY_GOOGLE_IOS_CLIENT_ID", "").strip(),
        google_redirect_uri=os.getenv(
            "OOHSTORY_GOOGLE_REDIRECT_URI",
            "http://localhost:8091/api/v1/auth/google/callback",
        ).strip(),
        upload_root=(
            Path(raw_upload_root).expanduser().resolve()
            if raw_upload_root
            else None
        ),
        avatar_root=(
            Path(raw_avatar_root).expanduser().resolve()
            if raw_avatar_root
            else None
        ),
        max_avatar_bytes=min(
            max(int(os.getenv("OOHSTORY_MAX_AVATAR_BYTES", str(5 * 1024 * 1024))), 64 * 1024),
            10 * 1024 * 1024,
        ),
        max_avatar_pixels=min(
            max(int(os.getenv("OOHSTORY_MAX_AVATAR_PIXELS", str(2048 * 2048))), 256 * 256),
            4096 * 4096,
        ),
        max_upload_bytes=min(
            max(int(os.getenv("OOHSTORY_MAX_UPLOAD_BYTES", str(64 * 1024 * 1024))), 1024),
            150 * 1024 * 1024,
        ),
        upload_daily_files=min(max(int(os.getenv("OOHSTORY_UPLOAD_DAILY_FILES", "5")), 1), 50),
        upload_daily_bytes=min(
            max(int(os.getenv("OOHSTORY_UPLOAD_DAILY_BYTES", str(256 * 1024 * 1024))), 1024),
            2 * 1024 * 1024 * 1024,
        ),
        submission_review_command=tuple(shlex.split(raw_review_command)) if raw_review_command else (),
        submission_review_timeout=min(
            max(int(os.getenv("OOHSTORY_SUBMISSION_REVIEW_TIMEOUT", "300")), 10), 900
        ),
        submission_handoff_root=(
            Path(raw_handoff_root).expanduser().resolve() if raw_handoff_root else None
        ),
        smtp_host=os.getenv("OOHSTORY_SMTP_HOST", "").strip(),
        smtp_port=int(os.getenv("OOHSTORY_SMTP_PORT", "587")),
        smtp_username=os.getenv("OOHSTORY_SMTP_USERNAME", "").strip(),
        smtp_password_file=(
            Path(raw_smtp_password_file).expanduser().resolve()
            if raw_smtp_password_file else None
        ),
        smtp_from=os.getenv("OOHSTORY_SMTP_FROM", "").strip(),
        smtp_starttls=os.getenv("OOHSTORY_SMTP_STARTTLS", "1").strip().casefold()
        not in {"0", "false", "no"},
        public_origin=os.getenv("OOHSTORY_PUBLIC_ORIGIN", "http://localhost:8091").strip().rstrip("/"),
        public_deconstruction_root=(
            Path(raw_deconstruction_root).expanduser().resolve()
            if raw_deconstruction_root
            else None
        ),
        catalog_source_root=(
            Path(raw_catalog_source_root).expanduser().resolve()
            if raw_catalog_source_root
            else None
        ),
    )
