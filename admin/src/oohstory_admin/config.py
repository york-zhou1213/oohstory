from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


_BASE_PATH_RE = re.compile(r"^/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*$")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _boolean(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _integer(name: str, value: str | None, default: int, minimum: int, maximum: int) -> int:
    number = default if value is None else int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def _base_path(value: str | None) -> str:
    if value is None:
        return "/admin"
    value = value.strip()
    if value in {"", "/"}:
        return ""
    value = value.rstrip("/")
    if not _BASE_PATH_RE.fullmatch(value):
        raise ValueError("OOH_ADMIN_BASE_PATH must be empty or a simple absolute URL path")
    return value


def _secret(value_name: str, file_name: str) -> str:
    direct = os.environ.get(value_name, "").strip()
    if direct:
        return direct
    filename = os.environ.get(file_name, "").strip()
    if not filename:
        return ""
    path = Path(filename).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{file_name} must be an absolute path")
    if path.stat().st_size > 16 * 1024:
        raise ValueError(f"{file_name} is unexpectedly large")
    return path.read_text(encoding="utf-8").strip()


def _path(name: str, value: str, *, default: Path) -> Path:
    raw = value.strip()
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = (_PROJECT_ROOT / path).resolve()
    return path.resolve(strict=False)


def _redis_prefix(name: str, value: str, *, default: str) -> str:
    prefix = (value or default).strip().rstrip(":")
    if not prefix:
        raise ValueError(f"{name} must not be empty")
    if len(prefix.encode("utf-8")) > 128:
        raise ValueError(f"{name} is too long")
    return prefix + ":"


def _internal_http_url(
    name: str,
    value: str,
    default_port: int,
    *,
    allowed_hosts: set[str] | None = None,
) -> str:
    parsed = urlsplit(value.rstrip("/"))
    hosts = {"127.0.0.1", "localhost", "::1"}
    hosts.update(allowed_hosts or set())
    if parsed.scheme != "http" or parsed.hostname not in hosts:
        raise ValueError(f"{name} must be an http URL on an allowed internal host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError(f"{name} must not contain a path")
    port = parsed.port or default_port
    host = f"[{parsed.hostname}]" if ":" in (parsed.hostname or "") else parsed.hostname
    return f"http://{host}:{port}"


@dataclass(frozen=True, slots=True)
class Settings:
    admin_username: str = ""
    password_hash: str = ""
    session_secret: str = ""
    session_cookie: str = "oohstory_admin_session"
    cookie_secure: bool = False
    cookie_path: str = "/"
    session_ttl_seconds: int = 28_800
    login_attempts: int = 5
    login_window_seconds: int = 900
    reader_url: str = "http://127.0.0.1:8091"
    upstream_timeout_seconds: float = 4.0
    library_root: Path = _PROJECT_ROOT / "electronic-library" / "txt80"
    library_runtime_dir: Path = _PROJECT_ROOT / "electronic-library" / "txt80" / "全局索引"
    library_object_root: Path = _PROJECT_ROOT / "electronic-library" / "txt80"
    library_mysql_host: str = "127.0.0.1"
    library_mysql_port: int = 3306
    library_mysql_database: str = "oohstory_library"
    library_mysql_user: str = "oohstory_library_reader"
    library_mysql_password: str = field(default="", repr=False)
    library_redis_host: str = "127.0.0.1"
    library_redis_port: int = 6379
    library_redis_db: int = 6
    library_redis_password: str = field(default="", repr=False)
    library_redis_prefix: str = "oohstory:"
    library_cache_redis_enabled: bool = False
    library_cache_redis_host: str = "127.0.0.1"
    library_cache_redis_port: int = 6380
    library_cache_redis_db: int = 0
    library_cache_redis_password: str = field(default="", repr=False)
    library_cache_redis_prefix: str = "oohstory-cache:"
    library_cache_redis_connect_timeout: float = 0.2
    library_cache_redis_socket_timeout: float = 0.4
    library_cache_redis_max_payload_bytes: int = 256 * 1024
    database_path: Path = Path(".state/admin.db")
    base_path: str = "/admin"
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    systemctl_path: str = "/usr/bin/systemctl"
    use_sudo_helper: bool = False
    systemctl_helper_path: str = "/usr/local/libexec/oohstory-admin-systemctl"
    script_store_helper_path: str = "/usr/local/libexec/oohstory-admin-script-store"
    library_action_helper_path: str = "/usr/local/libexec/oohstory-admin-library-action"
    library_upload_dir: Path = _PROJECT_ROOT / ".state" / "cover-uploads"
    managed_script_root: Path = _PROJECT_ROOT

    @property
    def auth_configured(self) -> bool:
        return bool(
            self.admin_username
            and self.password_hash
            and len(self.session_secret.encode("utf-8")) >= 32
        )

    @property
    def login_path(self) -> str:
        return f"{self.base_path}/login" if self.base_path else "/login"

    @property
    def logout_path(self) -> str:
        return f"{self.base_path}/logout" if self.base_path else "/logout"

    @property
    def ui_root(self) -> str:
        return self.base_path or "/"

    @property
    def static_path(self) -> str:
        return f"{self.base_path}/static" if self.base_path else "/static"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        cookie_path = env.get("OOHSTORY_ADMIN_COOKIE_PATH", "/").strip() or "/"
        if not cookie_path.startswith("/") or ".." in cookie_path or any(c.isspace() for c in cookie_path):
            raise ValueError("OOHSTORY_ADMIN_COOKIE_PATH must be an absolute URL path")
        allowed_hosts = tuple(
            host.strip()
            for host in env.get("OOHSTORY_ADMIN_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
            if host.strip()
        )
        if not allowed_hosts or any("/" in host or any(c.isspace() for c in host) for host in allowed_hosts):
            raise ValueError("OOHSTORY_ADMIN_ALLOWED_HOSTS is invalid")
        reader_allowed_hosts = {
            host.strip()
            for host in env.get("OOHSTORY_ADMIN_READER_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        }
        if any(
            not re.fullmatch(r"[A-Za-z0-9.-]+", host)
            for host in reader_allowed_hosts
        ):
            raise ValueError("OOHSTORY_ADMIN_READER_ALLOWED_HOSTS is invalid")
        systemctl_path = env.get("OOHSTORY_ADMIN_SYSTEMCTL_PATH", "/usr/bin/systemctl")
        helper_path = env.get(
            "OOHSTORY_ADMIN_SYSTEMCTL_HELPER_PATH",
            "/usr/local/libexec/oohstory-admin-systemctl",
        )
        script_helper_path = env.get(
            "OOHSTORY_ADMIN_SCRIPT_STORE_HELPER_PATH",
            "/usr/local/libexec/oohstory-admin-script-store",
        )
        library_action_helper_path = env.get(
            "OOHSTORY_ADMIN_LIBRARY_ACTION_HELPER_PATH",
            "/usr/local/libexec/oohstory-admin-library-action",
        )
        if (
            not Path(systemctl_path).is_absolute()
            or not Path(helper_path).is_absolute()
            or not Path(script_helper_path).is_absolute()
            or not Path(library_action_helper_path).is_absolute()
        ):
            raise ValueError("helper paths must be absolute")
        return cls(
            admin_username=env.get("OOHSTORY_ADMIN_USERNAME", "").strip(),
            password_hash=env.get("OOHSTORY_ADMIN_PASSWORD_HASH", "").strip(),
            session_secret=env.get("OOHSTORY_ADMIN_SESSION_SECRET", ""),
            session_cookie=env.get("OOHSTORY_ADMIN_SESSION_COOKIE", "oohstory_admin_session"),
            cookie_secure=_boolean(env.get("OOHSTORY_ADMIN_COOKIE_SECURE"), False),
            cookie_path=cookie_path,
            session_ttl_seconds=_integer(
                "OOHSTORY_ADMIN_SESSION_TTL", env.get("OOHSTORY_ADMIN_SESSION_TTL"), 28_800, 300, 86_400
            ),
            login_attempts=_integer(
                "OOHSTORY_ADMIN_LOGIN_ATTEMPTS", env.get("OOHSTORY_ADMIN_LOGIN_ATTEMPTS"), 5, 2, 20
            ),
            login_window_seconds=_integer(
                "OOHSTORY_ADMIN_LOGIN_WINDOW", env.get("OOHSTORY_ADMIN_LOGIN_WINDOW"), 900, 30, 86_400
            ),
            reader_url=_internal_http_url(
                "OOHSTORY_ADMIN_READER_URL",
                env.get("OOHSTORY_ADMIN_READER_URL", "http://127.0.0.1:8091"),
                8091,
                allowed_hosts=reader_allowed_hosts,
            ),
            upstream_timeout_seconds=float(env.get("OOHSTORY_ADMIN_UPSTREAM_TIMEOUT", "4")),
            library_root=_path(
                "OOHSTORY_LIBRARY_ROOT",
                env.get("OOHSTORY_LIBRARY_ROOT", ""),
                default=_PROJECT_ROOT / "electronic-library" / "txt80",
            ),
            library_runtime_dir=_path(
                "OOHSTORY_LIBRARY_RUNTIME_DIR",
                env.get("OOHSTORY_LIBRARY_RUNTIME_DIR", ""),
                default=_PROJECT_ROOT / "electronic-library" / "txt80" / "全局索引",
            ),
            library_object_root=_path(
                "OOHSTORY_LIBRARY_OBJECT_ROOT",
                env.get("OOHSTORY_LIBRARY_OBJECT_ROOT", ""),
                default=_PROJECT_ROOT / "electronic-library" / "txt80",
            ),
            library_mysql_host=env.get("OOHSTORY_LIBRARY_MYSQL_HOST", "127.0.0.1").strip(),
            library_mysql_port=_integer(
                "OOHSTORY_LIBRARY_MYSQL_PORT",
                env.get("OOHSTORY_LIBRARY_MYSQL_PORT"),
                3306,
                1,
                65535,
            ),
            library_mysql_database=env.get(
                "OOHSTORY_LIBRARY_MYSQL_DATABASE", "oohstory_library"
            ).strip(),
            library_mysql_user=env.get(
                "OOHSTORY_LIBRARY_MYSQL_USER", "oohstory_library_reader"
            ).strip(),
            library_mysql_password=_secret(
                "OOHSTORY_LIBRARY_MYSQL_PASSWORD",
                "OOHSTORY_LIBRARY_MYSQL_PASSWORD_FILE",
            ),
            library_redis_host=env.get("OOHSTORY_LIBRARY_REDIS_HOST", "127.0.0.1").strip(),
            library_redis_port=_integer(
                "OOHSTORY_LIBRARY_REDIS_PORT",
                env.get("OOHSTORY_LIBRARY_REDIS_PORT"),
                6379,
                1,
                65535,
            ),
            library_redis_db=_integer(
                "OOHSTORY_LIBRARY_REDIS_DB",
                env.get("OOHSTORY_LIBRARY_REDIS_DB"),
                6,
                0,
                15,
            ),
            library_redis_password=_secret(
                "OOHSTORY_LIBRARY_REDIS_PASSWORD",
                "OOHSTORY_LIBRARY_REDIS_PASSWORD_FILE",
            ),
            library_redis_prefix=(
                env.get("OOHSTORY_LIBRARY_REDIS_PREFIX", "oohstory:").strip().rstrip(":") + ":"
            ),
            library_cache_redis_enabled=_boolean(
                env.get("OOHSTORY_LIBRARY_CACHE_REDIS_ENABLED"), False
            ),
            library_cache_redis_host=env.get(
                "OOHSTORY_LIBRARY_CACHE_REDIS_HOST", "127.0.0.1"
            ).strip(),
            library_cache_redis_port=_integer(
                "OOHSTORY_LIBRARY_CACHE_REDIS_PORT",
                env.get("OOHSTORY_LIBRARY_CACHE_REDIS_PORT"),
                6380,
                1,
                65535,
            ),
            library_cache_redis_db=_integer(
                "OOHSTORY_LIBRARY_CACHE_REDIS_DB",
                env.get("OOHSTORY_LIBRARY_CACHE_REDIS_DB"),
                0,
                0,
                15,
            ),
            library_cache_redis_password=_secret(
                "OOHSTORY_LIBRARY_CACHE_REDIS_PASSWORD",
                "OOHSTORY_LIBRARY_CACHE_REDIS_PASSWORD_FILE",
            ),
            library_cache_redis_prefix=_redis_prefix(
                "OOHSTORY_LIBRARY_CACHE_REDIS_PREFIX",
                env.get(
                    "OOHSTORY_LIBRARY_CACHE_REDIS_PREFIX", "oohstory-cache:"
                ),
                default="oohstory-cache:",
            ),
            library_cache_redis_connect_timeout=max(
                0.05,
                min(
                    float(
                        env.get(
                            "OOHSTORY_LIBRARY_CACHE_REDIS_CONNECT_TIMEOUT", "0.2"
                        )
                    ),
                    2.0,
                ),
            ),
            library_cache_redis_socket_timeout=max(
                0.05,
                min(
                    float(
                        env.get(
                            "OOHSTORY_LIBRARY_CACHE_REDIS_SOCKET_TIMEOUT", "0.4"
                        )
                    ),
                    3.0,
                ),
            ),
            library_cache_redis_max_payload_bytes=_integer(
                "OOHSTORY_LIBRARY_CACHE_REDIS_MAX_PAYLOAD_BYTES",
                env.get("OOHSTORY_LIBRARY_CACHE_REDIS_MAX_PAYLOAD_BYTES"),
                256 * 1024,
                16 * 1024,
                1024 * 1024,
            ),
            database_path=Path(env.get("OOHSTORY_ADMIN_DATABASE", ".state/admin.db")),
            base_path=_base_path(env.get("OOH_ADMIN_BASE_PATH")),
            allowed_hosts=allowed_hosts,
            systemctl_path=systemctl_path,
            use_sudo_helper=_boolean(env.get("OOHSTORY_ADMIN_USE_SUDO_HELPER"), False),
            systemctl_helper_path=helper_path,
            script_store_helper_path=script_helper_path,
            library_action_helper_path=library_action_helper_path,
            library_upload_dir=_path(
                "OOHSTORY_ADMIN_LIBRARY_UPLOAD_DIR",
                env.get("OOHSTORY_ADMIN_LIBRARY_UPLOAD_DIR", ""),
                default=_PROJECT_ROOT / ".state" / "cover-uploads",
            ),
            managed_script_root=_path(
                "OOHSTORY_ADMIN_MANAGED_SCRIPT_ROOT",
                env.get("OOHSTORY_ADMIN_MANAGED_SCRIPT_ROOT", ""),
                default=_PROJECT_ROOT,
            ),
        )
