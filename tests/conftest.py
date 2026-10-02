from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from flask import Flask

# Ensure the module-level app in bookflow.app does not write into the repo
# when the package is imported during collection.
os.environ.setdefault("OPDS_DATA_DIR", tempfile.mkdtemp(prefix="bookflow-test-"))

from bookflow.app import create_app
from bookflow.config import Settings
from bookflow.database.database import reset_engine
from factories import alembic_config

AppFactory = Callable[..., Flask]


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
