"""Flask application factory."""

from __future__ import annotations

import logging
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import Flask, Response, jsonify, redirect, request, session, url_for
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from bookflow.admin.routes import bp as admin_bp
from bookflow.auth.routes import bp as auth_bp
from bookflow.auth.service import LoginRateLimiter, PasswordVerifier, ensure_csrf_token
from bookflow.config import Settings, resolve_session_secret
from bookflow.database.database import init_engine, session_scope
from bookflow.database.models import User
from bookflow.opds.auth import require_basic_auth
from bookflow.opds.progression import bp as progression_bp
from bookflow.opds.progression import problem_document
from bookflow.opds.routes import OPDS_URL_PREFIXES, error_document
from bookflow.opds.routes import bp as opds_bp


def ensure_admin_user(settings: Settings) -> None:
    """Ensure the configured OPDS_ADMIN_USERNAME exists with OPDS_ADMIN_PASSWORD."""
    if not settings.admin_password:
        return
    try:
        with session_scope() as session:
            admin = session.scalar(
                select(User).where(User.username == settings.admin_username)
            )
            hasher = PasswordHasher()
            if admin is None:
                session.add(
                    User(
                        username=settings.admin_username,
                        password_hash=hasher.hash(settings.admin_password),
                        is_admin=True,
                    )
                )
            else:
                try:
                    hasher.verify(admin.password_hash, settings.admin_password)
                except (InvalidHashError, VerificationError):
                    admin.password_hash = hasher.hash(settings.admin_password)
                    admin.is_admin = True
    except SQLAlchemyError:
        pass


def _is_admin() -> bool:
    """Jinja global: is the current session signed in as the admin?"""
    return bool(session.get("admin"))


def _configure_logging(level_name: str) -> None:
    """Apply the configured level to the root logger.

    A bare WSGI process installs no handlers, so Python's last-resort
    handler would drop everything below WARNING and INFO events (scan
    results, cache clears) never reach output. The level is always
    applied; a stderr handler is added only when the process has none,
    leaving pytest's capture handlers and gunicorn's log setup alone.
    """
    level = logging.getLevelNamesMapping().get(
        level_name.upper(), logging.INFO
    )
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)


def create_app(settings: Settings | None = None) -> Flask:
    """Create and configure the Flask application."""
    if settings is None:
        settings = Settings.from_env()
    _configure_logging(settings.log_level)

    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.session_cookie_secure,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )

    settings.ensure_directories()
    app.secret_key = resolve_session_secret(settings)
    init_engine(settings)
    ensure_admin_user(settings)

    if settings.trusted_proxy_hops:
        # Opt-in: with a reverse proxy in front, every client shares the
        # proxy's address. Only X-Forwarded-For is honoured, which is what
        # the login rate limiter keys on. Never enable this when BookFlow
        # is reachable directly — clients can forge the header.
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=settings.trusted_proxy_hops
        )

    app.extensions["password_verifier"] = PasswordVerifier(
        settings.admin_username, settings.admin_password
    )
    app.extensions["login_rate_limiter"] = LoginRateLimiter()
    app.jinja_env.globals["csrf_token"] = ensure_csrf_token
    app.jinja_env.globals["is_admin"] = _is_admin

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(opds_bp)
    app.register_blueprint(progression_bp)

    @app.get("/")
    def index() -> Response:
        return redirect(url_for("admin.dashboard"))

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify(status="ok")

    @app.errorhandler(HTTPException)
    def error_without_a_blueprint(
        error: HTTPException,
    ) -> Response | HTTPException:
        """Handle the failures that blueprints never see.

        A failed URL match leaves ``request.url_rule`` unset, so
        ``request.blueprints`` is empty and neither the blueprints'
        ``before_request`` hooks nor their ``@bp.errorhandler`` run.
        Requests below an OPDS prefix therefore authenticate here — the
        same Basic-auth check a routed request gets — before their error
        document is built; the Authentication Document itself stays
        public. Every other request is returned unchanged, which is
        Flask's default HTML error page.
        """
        path = request.path
        if progression_bp.url_prefix and path.startswith(
            progression_bp.url_prefix
        ):
            document = problem_document
        elif any(path.startswith(prefix) for prefix in OPDS_URL_PREFIXES):
            document = error_document
        else:
            return error
        if path != url_for("opds.authentication"):
            denied = require_basic_auth(skip="opds.authentication")
            if denied is not None:
                return denied
        return document(error)

    return app
