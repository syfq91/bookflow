from __future__ import annotations

from dataclasses import replace

import pytest

from bookflow.auth.service import LoginRateLimiter, safe_next_target
from bookflow.config import Settings
from factories import csrf_token, login_admin

ADMIN_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def auth_settings(settings: Settings) -> Settings:
    return replace(settings, admin_username="admin", admin_password=ADMIN_PASSWORD)


@pytest.fixture
def app(build_app, auth_settings: Settings):
    return build_app(auth_settings, migrate=False)


@pytest.fixture
def migrated_client(build_app, auth_settings: Settings):
    return build_app(auth_settings).test_client()


# --- login -----------------------------------------------------------------


def test_login_page_renders(client) -> None:
    resp = client.get("/admin/login")

    assert resp.status_code == 200
    assert b"csrf_token" in resp.data


def test_valid_login_redirects_to_dashboard(migrated_client) -> None:
    resp = login_admin(migrated_client, password=ADMIN_PASSWORD)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/")
    assert migrated_client.get("/admin/").status_code == 200


def test_invalid_username_rejected(client) -> None:
    resp = login_admin(client, username="root", password=ADMIN_PASSWORD)

    assert resp.status_code == 401
    assert b"Invalid username or password." in resp.data
    assert client.get("/admin/").status_code == 302


def test_invalid_password_rejected(client) -> None:
    resp = login_admin(client, password="wrong")

    assert resp.status_code == 401
    assert b"Invalid username or password." in resp.data
    assert client.get("/admin/").status_code == 302


def test_login_page_redirects_when_authenticated(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.get("/admin/login")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/")


def test_unconfigured_password_returns_503(settings: Settings, build_app) -> None:
    fresh = build_app(settings, migrate=False).test_client()
    resp = fresh.post(
        "/admin/login",
        data={
            "csrf_token": csrf_token(fresh),
            "username": "admin",
            "password": "anything",
        },
    )
    assert resp.status_code == 503
    assert fresh.get("/admin/").status_code == 302


# --- protected routes ------------------------------------------------------


def test_protected_routes_redirect_anonymous_users(migrated_client) -> None:
    for path in ("/admin/", "/admin/folders", "/admin/health"):
        resp = migrated_client.get(path)

        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["Location"]


def test_authenticated_user_can_open_admin_pages(migrated_client) -> None:
    login_admin(migrated_client, password=ADMIN_PASSWORD)

    for path in ("/admin/", "/admin/folders", "/admin/health"):
        assert migrated_client.get(path).status_code == 200


def test_dashboard_renders_stats(migrated_client) -> None:
    login_admin(migrated_client, password=ADMIN_PASSWORD)

    resp = migrated_client.get("/admin/")

    assert b"Dashboard" in resp.data
    assert b"Books" in resp.data
    assert b"Library size" in resp.data


def test_healthz_stays_public(client) -> None:
    assert client.get("/healthz").status_code == 200


def test_dashboard_explains_missing_migrations(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.get("/admin/")

    assert resp.status_code == 200
    assert b"Database not initialized" in resp.data


# --- logout and session invalidation ---------------------------------------


def test_logout_clears_session(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.post("/admin/logout", data={"csrf_token": csrf_token(client)})

    assert resp.status_code == 302
    assert client.get("/admin/").status_code == 302


def test_logout_requires_csrf(migrated_client) -> None:
    login_admin(migrated_client, password=ADMIN_PASSWORD)

    resp = migrated_client.post("/admin/logout", data={})

    assert resp.status_code == 400
    assert migrated_client.get("/admin/").status_code == 200


def test_session_cookie_required(app) -> None:
    client = app.test_client()
    login_admin(client, password=ADMIN_PASSWORD)

    fresh = app.test_client()

    assert fresh.get("/admin/").status_code == 302


def test_session_cookie_flags(client) -> None:
    resp = login_admin(client, password=ADMIN_PASSWORD)

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
    csrf_token(client)

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
    assert login_admin(client, password=ADMIN_PASSWORD).status_code == 302


# --- redirect safety -------------------------------------------------------


def test_login_honours_internal_next_target(client) -> None:
    resp = login_admin(client, password=ADMIN_PASSWORD, next_target="/admin/health")

    assert resp.headers["Location"].endswith("/admin/health")


@pytest.mark.parametrize(
    "target",
    ["https://evil.example/steal", "//evil.example/steal", "/\\evil.example"],
)
def test_login_rejects_unsafe_next_target(client, target: str) -> None:
    resp = login_admin(client, password=ADMIN_PASSWORD, next_target=target)

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
        assert login_admin(client, password="wrong").status_code == 401

    resp = login_admin(client, password=ADMIN_PASSWORD)

    assert resp.status_code == 429
    limiter = app.extensions["login_rate_limiter"]
    assert limiter.is_blocked(("127.0.0.1", "admin"))


def test_successful_login_resets_rate_limit(client, app) -> None:
    for _ in range(3):
        login_admin(client, password="wrong")

    assert login_admin(client, password=ADMIN_PASSWORD).status_code == 302

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


# --- rate limiting behind a reverse proxy -----------------------------------


def _failed_login(client, headers: dict[str, str]) -> int:
    resp = client.post(
        "/admin/login",
        data={
            "csrf_token": csrf_token(client),
            "username": "admin",
            "password": "wrong",
        },
        headers=headers,
    )
    return resp.status_code


def test_rate_limit_ignores_forwarded_header_by_default(client, app) -> None:
    forwarded = {"X-Forwarded-For": "203.0.113.7"}

    for _ in range(5):
        assert _failed_login(client, forwarded) == 401
    assert _failed_login(client, forwarded) == 429

    limiter = app.extensions["login_rate_limiter"]
    assert limiter.is_blocked(("127.0.0.1", "admin"))
    assert not limiter.is_blocked(("203.0.113.7", "admin"))


def test_rate_limit_keys_on_forwarded_client_when_proxy_trusted(
    build_app, auth_settings: Settings
) -> None:
    application = build_app(
        replace(auth_settings, trusted_proxy_hops=1), migrate=False
    )
    client = application.test_client()
    first = {"X-Forwarded-For": "203.0.113.7"}
    second = {"X-Forwarded-For": "203.0.113.8"}

    for _ in range(5):
        assert _failed_login(client, first) == 401

    assert _failed_login(client, first) == 429
    assert _failed_login(client, second) == 401

    limiter = application.extensions["login_rate_limiter"]
    assert limiter.is_blocked(("203.0.113.7", "admin"))
    assert not limiter.is_blocked(("203.0.113.8", "admin"))
    assert not limiter.is_blocked(("127.0.0.1", "admin"))


def test_rate_limiter_prunes_empty_keys() -> None:
    now = 1000.0
    limiter = LoginRateLimiter(window_seconds=60.0, clock=lambda: now)
    key = ("127.0.0.1", "admin")

    assert not limiter.is_blocked(key)
    assert key not in limiter._failures

    limiter.record_failure(key)
    assert key in limiter._failures

    now += 61.0
    assert not limiter.is_blocked(key)
    assert key not in limiter._failures
