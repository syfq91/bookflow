from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from flask import Flask

from bookflow.app import create_app
from bookflow.config import Settings
from bookflow.database.database import reset_engine
from factories import alembic_config

AppFactory = Callable[..., Flask]


@pytest.fixture(scope="session", autouse=True)
def _default_data_dir() -> Iterator[Path]:
    """Point default Settings at a scratch dir; never at the repo.

    Nothing builds an app at import time any more, but any code that
    falls back to ``Settings.from_env()`` must still stay out of the
    working tree.
    """
    data_dir = Path(tempfile.mkdtemp(prefix="bookflow-test-"))
    os.environ.setdefault("OPDS_DATA_DIR", str(data_dir))
    yield data_dir
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret",
        admin_username="admin",
        admin_password="",
    )


@pytest.fixture
def build_app() -> Iterator[AppFactory]:
    """Factory for configured test apps; the engine is reset around each test.

    Suites that need a migrated app define a two-line ``app`` fixture on top
    of this; ``migrate=False`` builds an app against an empty database.
    """

    def _build(settings: Settings, *, migrate: bool = True) -> Flask:
        reset_engine()
        application = create_app(settings)
        application.config["TESTING"] = True
        if migrate:
            command.upgrade(alembic_config(settings.database_url), "head")
        return application

    yield _build
    reset_engine()


@pytest.fixture
def app(settings: Settings, build_app: AppFactory):
    return build_app(settings, migrate=False)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "books"
    path.mkdir()
    return path
