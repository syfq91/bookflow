"""Flask application factory."""

from __future__ import annotations

from flask import Flask, Response, jsonify, redirect, request, url_for
from werkzeug.exceptions import HTTPException

from bookflow.admin.routes import bp as admin_bp
from bookflow.auth.routes import bp as auth_bp
from bookflow.auth.service import LoginRateLimiter, PasswordVerifier, ensure_csrf_token
from bookflow.config import Settings, resolve_session_secret
from bookflow.database.database import init_engine
from bookflow.opds.progression import bp as progression_bp
from bookflow.opds.progression import problem_document
from bookflow.opds.routes import bp as opds_bp
from bookflow.opds.routes import error_document


def create_app(settings: Settings | None = None) -> Flask:
    """Create and configure the Flask application."""
    if settings is None:
        settings = Settings.from_env()

    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.session_cookie_secure,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )

    settings.ensure_directories()
    app.secret_key = resolve_session_secret(settings)
    init_engine(settings)

    app.extensions["password_verifier"] = PasswordVerifier(
        settings.admin_username, settings.admin_password
    )
    app.extensions["login_rate_limiter"] = LoginRateLimiter()
    app.jinja_env.globals["csrf_token"] = ensure_csrf_token

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(opds_bp)
    app.register_blueprint(progression_bp)

    @app.get("/")
    def index():
        return redirect(url_for("admin.dashboard"))

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    @app.errorhandler(HTTPException)
    def error_without_a_blueprint(
        error: HTTPException,
    ) -> Response | HTTPException:
        """Handle the failures that blueprints never see.

        A failed URL match leaves ``request.url_rule`` unset, so
        ``request.blueprints`` is empty and the OPDS handlers registered
        with ``@bp.errorhandler`` are skipped. Requests below an OPDS
        prefix still get their error document; every other request is
        returned unchanged, which is Flask's default HTML error page.
        """
        path = request.path
        for prefix, document in (
            (progression_bp.url_prefix, problem_document),
            (opds_bp.url_prefix, error_document),
        ):
            if prefix and path.startswith(prefix):
                return document(error)
        return error

    return app


app = create_app()
