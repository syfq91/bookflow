"""Flask application factory."""

from __future__ import annotations

from flask import Flask, jsonify

from bookflow.config import Settings
from bookflow.database.database import init_engine


def create_app(settings: Settings | None = None) -> Flask:
    """Create and configure the Flask application."""
    if settings is None:
        settings = Settings.from_env()

    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.secret_key = settings.session_secret or "dev-only-insecure-secret"

    settings.ensure_directories()
    init_engine(settings)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    return app


app = create_app()
