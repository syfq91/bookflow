from __future__ import annotations

import os
from pathlib import Path

from bookflow.config import Settings, resolve_session_secret


def _settings(tmp_path: Path, session_secret: str = "") -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        database_url="sqlite://",
        session_secret=session_secret,
        admin_username="admin",
        admin_password="",
    )


def test_defaults(monkeypatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith("OPDS_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPDS_DATA_DIR", str(tmp_path / "data"))

    settings = Settings.from_env()

    assert settings.admin_username == "admin"
    assert settings.data_dir == (tmp_path / "data").resolve()
    assert settings.database_url.endswith("bookflow.db")


def test_env_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPDS_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("OPDS_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'x.db'}")
    monkeypatch.setenv("OPDS_ADMIN_USERNAME", "root")

    settings = Settings.from_env()

    assert settings.admin_username == "root"
    assert settings.database_url == f"sqlite+pysqlite:///{tmp_path / 'x.db'}"


def test_session_cookie_secure_defaults_off(monkeypatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith("OPDS_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPDS_DATA_DIR", str(tmp_path / "data"))

    assert Settings.from_env().session_cookie_secure is False


def test_session_cookie_secure_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPDS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPDS_SESSION_COOKIE_SECURE", "true")

    assert Settings.from_env().session_cookie_secure is True


def test_browse_root_defaults_to_filesystem_root(monkeypatch, tmp_path: Path) -> None:
    for key in list(os.environ):
        if key.startswith("OPDS_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPDS_DATA_DIR", str(tmp_path / "data"))

    assert Settings.from_env().browse_root == Path("/")


def test_browse_root_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPDS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPDS_BROWSE_ROOT", str(tmp_path / "media"))

    assert Settings.from_env().browse_root == (tmp_path / "media").resolve()


def test_derived_cache_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir,
        database_url="sqlite://",
        session_secret="",
        admin_username="admin",
        admin_password="",
    )

    assert settings.x3_cache_dir == data_dir / "cache" / "optimized" / "x3"
    assert settings.x4_cache_dir == data_dir / "cache" / "optimized" / "x4"


def test_ensure_directories(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url="sqlite://",
        session_secret="",
        admin_username="admin",
        admin_password="",
    )

    settings.ensure_directories()

    assert settings.x3_cache_dir.is_dir()
    assert settings.x4_cache_dir.is_dir()


# --- session secret ----------------------------------------------------------


def test_generated_secret_is_persisted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()

    secret = resolve_session_secret(settings)
    stored = settings.data_dir / ".session_secret"

    assert secret
    assert stored.read_text(encoding="ascii").strip() == secret
    assert stored.stat().st_mode & 0o777 == 0o600


def test_generated_secret_is_reused_across_calls(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()

    first = resolve_session_secret(settings)

    # Simulates a restart or another gunicorn worker booting.
    assert resolve_session_secret(settings) == first


def test_configured_secret_is_used_verbatim(tmp_path: Path) -> None:
    settings = _settings(tmp_path, session_secret="explicit-secret")
    settings.ensure_directories()

    assert resolve_session_secret(settings) == "explicit-secret"
    assert not (settings.data_dir / ".session_secret").exists()


def test_app_uses_generated_secret(tmp_path: Path, build_app) -> None:
    settings = _settings(tmp_path)
    application = build_app(settings, migrate=False)

    stored = (settings.data_dir / ".session_secret").read_text(encoding="ascii")

    assert application.secret_key == stored.strip()
