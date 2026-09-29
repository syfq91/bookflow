"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
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


DEFAULT_SCAN_EXTENSIONS = (".epub", ".pdf", ".cbz", ".cbr", ".mobi", ".azw3")


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

    host: str
    port: int
    data_dir: Path
    database_url: str
    session_secret: str
    admin_username: str
    admin_password: str
    session_cookie_secure: bool = False
    scan_extensions: tuple[str, ...] = DEFAULT_SCAN_EXTENSIONS

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
            host=_env("OPDS_HOST", "0.0.0.0"),
            port=int(_env("OPDS_PORT", "8000")),
            data_dir=data_dir,
            database_url=database_url,
            session_secret=_env("OPDS_SESSION_SECRET", ""),
            admin_username=_env("OPDS_ADMIN_USERNAME", "admin"),
            admin_password=_env("OPDS_ADMIN_PASSWORD", ""),
            session_cookie_secure=_env_bool("OPDS_SESSION_COOKIE_SECURE", False),
            scan_extensions=_parse_extensions(
                _env("OPDS_SCAN_EXTENSIONS", "")
            ),
        )

    def ensure_directories(self) -> None:
        """Create the data and cache directories if they do not exist."""
        for path in (self.data_dir, self.x3_cache_dir, self.x4_cache_dir):
            path.mkdir(parents=True, exist_ok=True)
