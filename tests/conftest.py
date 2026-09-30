from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Ensure the module-level app in bookflow.app does not write into the repo
# when the package is imported during collection.
os.environ.setdefault("OPDS_DATA_DIR", tempfile.mkdtemp(prefix="bookflow-test-"))

from bookflow.app import create_app  # noqa: E402
from bookflow.config import Settings  # noqa: E402
from bookflow.database.database import reset_engine  # noqa: E402


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
def app(settings: Settings):
    reset_engine()
    application = create_app(settings)
    application.config["TESTING"] = True
    yield application
    reset_engine()


@pytest.fixture
def client(app):
    return app.test_client()
