"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    """Read a non-negative integer setting; unusable values fall back."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return max(int(value.strip()), 0)
    except ValueError:
        return default


DEFAULT_SCAN_EXTENSIONS = (".epub", ".pdf", ".cbz", ".cbr", ".mobi", ".azw3")

LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")
DEFAULT_LOG_LEVEL = "INFO"


def _env_log_level(name: str, default: str) -> str:
    """Read a logging level name; unusable values fall back."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    level = value.strip().upper()
    return level if level in LOG_LEVELS else default


def _parse_extensions(value: str) -> tuple[str, ...]:
    """Parse a comma-separated extension list, normalized to `.ext`."""
    if not value.strip():
        return DEFAULT_SCAN_EXTENSIONS
    extensions: list[str] = []
    for part in value.split(","):
        item = part.strip().lower()
        if not item:
            continue
        if not item.startswith("."):
            item = "." + item
        extensions.append(item)
    unique = tuple(dict.fromkeys(extensions))
    return unique or DEFAULT_SCAN_EXTENSIONS


@dataclass(frozen=True)
class Settings:
    """Resolved application settings."""

    data_dir: Path
    database_url: str
    session_secret: str
    admin_username: str
    admin_password: str
    session_cookie_secure: bool = False
    scan_extensions: tuple[str, ...] = DEFAULT_SCAN_EXTENSIONS
    browse_root: Path = Path("/")
    trusted_proxy_hops: int = 0
    log_level: str = DEFAULT_LOG_LEVEL

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache" / "optimized"

    @property
    def x3_cache_dir(self) -> Path:
        return self.cache_dir / "x3"

    @property
    def x4_cache_dir(self) -> Path:
        return self.cache_dir / "x4"

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(_env("OPDS_DATA_DIR", "./data")).expanduser().resolve()

        database_url = os.environ.get("OPDS_DATABASE_URL")
        if not database_url:
            database_url = f"sqlite+pysqlite:///{data_dir / 'bookflow.db'}"

        return cls(
            data_dir=data_dir,
            database_url=database_url,
            session_secret=_env("OPDS_SESSION_SECRET", ""),
            admin_username=_env("OPDS_ADMIN_USERNAME", "admin"),
            admin_password=_env("OPDS_ADMIN_PASSWORD", ""),
            session_cookie_secure=_env_bool("OPDS_SESSION_COOKIE_SECURE", False),
            scan_extensions=_parse_extensions(
                _env("OPDS_SCAN_EXTENSIONS", "")
            ),
            browse_root=(
                Path(_env("OPDS_BROWSE_ROOT", "/")).expanduser().resolve()
            ),
            trusted_proxy_hops=_env_int("OPDS_TRUSTED_PROXY_HOPS", 0),
            log_level=_env_log_level("OPDS_LOG_LEVEL", DEFAULT_LOG_LEVEL),
        )

    def ensure_directories(self) -> None:
        """Create the data and cache directories if they do not exist."""
        for path in (self.data_dir, self.x3_cache_dir, self.x4_cache_dir):
            path.mkdir(parents=True, exist_ok=True)


def resolve_session_secret(settings: Settings) -> str:
    """Return the configured session secret, generating one when absent.

    Without ``OPDS_SESSION_SECRET`` a random secret is created once and
    persisted to ``data/.session_secret`` (mode 0600), so sessions survive
    restarts and every gunicorn worker signs cookies with the same key.
    If the data directory is not writable, an in-memory secret is used and
    sessions reset on the next restart.
    """
    if settings.session_secret:
        return settings.session_secret

    path = settings.data_dir / ".session_secret"
    for _attempt in range(3):
        try:
            stored = path.read_text(encoding="ascii").strip()
            if stored:
                return stored
        except FileNotFoundError:
            pass
        except OSError:
            break
        secret = secrets.token_urlsafe(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue  # another worker won the race; re-read its value
        except OSError:
            return secret  # data dir not writable: keep it in memory
        try:
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(secret)
        except OSError:
            path.unlink(missing_ok=True)
            return secret
        return secret
    return secrets.token_urlsafe(32)
