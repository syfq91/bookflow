from __future__ import annotations

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from bookflow.app import create_app
from bookflow.auth.service import LoginRateLimiter, safe_next_target
from bookflow.config import Settings
from bookflow.database.database import reset_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_PASSWORD = "correct-horse-battery-staple"


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest.fixture
def auth_settings(settings: Settings) -> Settings:
    return replace(settings, admin_username="admin", admin_password=ADMIN_PASSWORD)


@pytest.fixture
def app(auth_settings: Settings):
    reset_engine()
    application = create_app(auth_settings)
    application.config["TESTING"] = True
    yield application
    reset_engine()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def migrated_client(app, auth_settings):
    command.upgrade(alembic_config(auth_settings.database_url), "head")
    return app.test_client()


def _csrf(client) -> str:
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            sess["csrf_token"] = token
        return token


def _login(
    client,
    username: str = "admin",
    password: str = ADMIN_PASSWORD,
    next_target: str = "",
):
    return client.post(
        "/admin/login",
        data={
            "csrf_token": _csrf(client),
            "username": username,
            "password": password,
            "next": next_target,
        },
    )


# --- login -----------------------------------------------------------------


def test_login_page_renders(client) -> None:
    resp = client.get("/admin/login")

    assert resp.status_code == 200
    assert b"csrf_token" in resp.data


def test_valid_login_redirects_to_dashboard(migrated_client) -> None:
    resp = _login(migrated_client)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/")
    assert migrated_client.get("/admin/").status_code == 200


def test_invalid_username_rejected(client) -> None:
    resp = _login(client, username="root")

    assert resp.status_code == 200
    assert b"Invalid username or password." in resp.data
    assert client.get("/admin/").status_code == 302


def test_invalid_password_rejected(client) -> None:
    resp = _login(client, password="wrong")

    assert resp.status_code == 200
    assert b"Invalid username or password." in resp.data
    assert client.get("/admin/").status_code == 302


def test_login_page_redirects_when_authenticated(client) -> None:
    _login(client)

    resp = client.get("/admin/login")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/")


def test_unconfigured_password_returns_503(settings: Settings) -> None:
    reset_engine()
    application = create_app(settings)
    application.config["TESTING"] = True
    try:
        fresh = application.test_client()
        resp = fresh.post(
            "/admin/login",
            data={
                "csrf_token": _csrf(fresh),
                "username": "admin",
                "password": "anything",
            },
        )
        assert resp.status_code == 503
        assert fresh.get("/admin/").status_code == 302
    finally:
        reset_engine()


# --- protected routes ------------------------------------------------------


def test_protected_routes_redirect_anonymous_users(migrated_client) -> None:
    for path in ("/admin/", "/admin/folders", "/admin/health"):
        resp = migrated_client.get(path)

        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["Location"]


def test_authenticated_user_can_open_admin_pages(migrated_client) -> None:
    _login(migrated_client)

    for path in ("/admin/", "/admin/folders", "/admin/health"):
        assert migrated_client.get(path).status_code == 200


def test_dashboard_renders_stats(migrated_client) -> None:
    _login(migrated_client)

    resp = migrated_client.get("/admin/")

    assert b"Dashboard" in resp.data
    assert b"Books" in resp.data
    assert b"Library size" in resp.data


def test_healthz_stays_public(client) -> None:
    assert client.get("/healthz").status_code == 200


def test_dashboard_explains_missing_migrations(client) -> None:
    _login(client)

    resp = client.get("/admin/")

    assert resp.status_code == 200
    assert b"Database not initialized" in resp.data


# --- logout and session invalidation ---------------------------------------


def test_logout_clears_session(client) -> None:
    _login(client)

    resp = client.post("/admin/logout", data={"csrf_token": _csrf(client)})

    assert resp.status_code == 302
    assert client.get("/admin/").status_code == 302


def test_logout_requires_csrf(migrated_client) -> None:
    _login(migrated_client)

    resp = migrated_client.post("/admin/logout", data={})

    assert resp.status_code == 400
    assert migrated_client.get("/admin/").status_code == 200


def test_session_cookie_required(app) -> None:
    client = app.test_client()
    _login(client)

    fresh = app.test_client()

    assert fresh.get("/admin/").status_code == 302


def test_session_cookie_flags(client) -> None:
    resp = _login(client)

    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie


# --- CSRF ------------------------------------------------------------------


def test_login_post_requires_csrf(client) -> None:
    resp = client.post(
        "/admin/login",
        data={"username": "admin", "password": ADMIN_PASSWORD},
    )

    assert resp.status_code == 400


def test_login_post_rejects_wrong_csrf(client) -> None:
    _csrf(client)

    resp = client.post(
        "/admin/login",
        data={
            "csrf_token": "not-the-session-token",
            "username": "admin",
            "password": ADMIN_PASSWORD,
        },
    )

    assert resp.status_code == 400


def test_login_post_accepts_valid_csrf(client) -> None:
    assert _login(client).status_code == 302


# --- redirect safety -------------------------------------------------------


def test_login_honours_internal_next_target(client) -> None:
    resp = _login(client, next_target="/admin/health")

    assert resp.headers["Location"].endswith("/admin/health")


@pytest.mark.parametrize(
    "target",
    ["https://evil.example/steal", "//evil.example/steal", "/\\evil.example"],
)
def test_login_rejects_unsafe_next_target(client, target: str) -> None:
    resp = _login(client, next_target=target)

    assert resp.headers["Location"].endswith("/admin/")


def test_safe_next_target() -> None:
    assert safe_next_target("/admin/health") == "/admin/health"
    assert safe_next_target(None) is None
    assert safe_next_target("") is None
    assert safe_next_target("https://evil.example") is None
    assert safe_next_target("//evil.example") is None


# --- rate limiting ---------------------------------------------------------


def test_rate_limit_blocks_after_max_failures(client, app) -> None:
    for _ in range(5):
        assert _login(client, password="wrong").status_code == 200

    resp = _login(client)

    assert resp.status_code == 429
    limiter = app.extensions["login_rate_limiter"]
    assert limiter.is_blocked(("127.0.0.1", "admin"))


def test_successful_login_resets_rate_limit(client, app) -> None:
    for _ in range(3):
        _login(client, password="wrong")

    assert _login(client).status_code == 302

    limiter = app.extensions["login_rate_limiter"]
    assert not limiter.is_blocked(("127.0.0.1", "admin"))


def test_rate_limiter_window_expires() -> None:
    now = [0.0]
    limiter = LoginRateLimiter(
        max_failures=2, window_seconds=60, clock=lambda: now[0]
    )
    key = ("127.0.0.1", "admin")

    limiter.record_failure(key)
    limiter.record_failure(key)
    assert limiter.is_blocked(key)

    now[0] = 61.0
    assert not limiter.is_blocked(key)


def test_rate_limiter_is_scoped_per_username() -> None:
    limiter = LoginRateLimiter(max_failures=1)
    limiter.record_failure(("127.0.0.1", "admin"))

    assert limiter.is_blocked(("127.0.0.1", "admin"))
    assert not limiter.is_blocked(("127.0.0.1", "root"))
    assert not limiter.is_blocked(("10.0.0.1", "admin"))


def test_rate_limiter_reset_clears_failures() -> None:
    limiter = LoginRateLimiter(max_failures=1)
    key = ("127.0.0.1", "admin")
    limiter.record_failure(key)
    assert limiter.is_blocked(key)

    limiter.reset(key)

    assert not limiter.is_blocked(key)
