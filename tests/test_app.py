from __future__ import annotations

from bookflow.app import create_app
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


def test_factory_creates_data_dirs(settings: Settings) -> None:
    create_app(settings)

    assert settings.data_dir.is_dir()
    assert settings.x3_cache_dir.is_dir()
    assert settings.x4_cache_dir.is_dir()


def test_settings_stored_on_config(app) -> None:
    assert isinstance(app.config["SETTINGS"], Settings)


def test_error_outside_opds_keeps_flask_html(client) -> None:
    response = client.post("/admin/")

    assert response.status_code == 405
    assert response.content_type.startswith("text/html")
