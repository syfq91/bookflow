from __future__ import annotations

import logging
from dataclasses import replace

from bookflow.config import Settings


def test_healthz(client) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert response.content_type.startswith("application/json")


def test_root_redirects_to_admin(client) -> None:
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/")


def test_factory_creates_data_dirs(settings: Settings, build_app) -> None:
    build_app(settings, migrate=False)

    assert settings.data_dir.is_dir()
    assert settings.x3_cache_dir.is_dir()
    assert settings.x4_cache_dir.is_dir()


def test_settings_stored_on_config(app) -> None:
    assert isinstance(app.config["SETTINGS"], Settings)


def test_error_outside_opds_keeps_flask_html(client) -> None:
    response = client.post("/admin/")

    assert response.status_code == 405
    assert response.content_type.startswith("text/html")



def test_create_app_applies_log_level(settings: Settings, build_app) -> None:
    build_app(replace(settings, log_level="DEBUG"), migrate=False)
    assert logging.getLogger().level == logging.DEBUG

    build_app(settings, migrate=False)
    assert logging.getLogger().level == logging.INFO


def test_create_app_makes_info_records_reach_handlers(
    settings: Settings, build_app, caplog
) -> None:
    build_app(settings, migrate=False)

    logging.getLogger("bookflow.reachable").info("INFO reaches the handlers")

    assert any(
        "INFO reaches the handlers" in record.getMessage()
        for record in caplog.records
    )


def test_permanent_session_lifetime(app) -> None:
    from datetime import timedelta

    assert app.permanent_session_lifetime == timedelta(days=7)


def test_sqlite_pragmas_enabled(app) -> None:
    from sqlalchemy import text

    from bookflow.database.database import session_scope

    with session_scope() as session:
        journal_mode = session.execute(text("PRAGMA journal_mode")).scalar()
        busy_timeout = session.execute(text("PRAGMA busy_timeout")).scalar()
        foreign_keys = session.execute(text("PRAGMA foreign_keys")).scalar()

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == 5000
    assert foreign_keys == 1
