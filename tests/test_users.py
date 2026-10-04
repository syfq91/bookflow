from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import select

from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import User
from factories import csrf_token, login_admin

ADMIN_PASSWORD = "users-admin-pass"


@pytest.fixture
def auth_settings(settings: Settings) -> Settings:
    return replace(settings, admin_username="admin", admin_password=ADMIN_PASSWORD)


@pytest.fixture
def app(build_app, auth_settings: Settings):
    return build_app(auth_settings)


@pytest.fixture
def logged_in_client(app):
    client = app.test_client()
    login_admin(client, password=ADMIN_PASSWORD)
    return client


def test_users_page_requires_auth(client) -> None:
    resp = client.get("/admin/users")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_users_page_lists_users(logged_in_client) -> None:
    resp = logged_in_client.get("/admin/users")
    assert resp.status_code == 200
    assert b"admin" in resp.data
    assert b"Admin" in resp.data


def test_user_new_form_renders(logged_in_client) -> None:
    resp = logged_in_client.get("/admin/users/new")
    assert resp.status_code == 200
    assert b"Add user account" in resp.data
    assert b"csrf_token" in resp.data


def test_user_create_success(logged_in_client) -> None:
    token = csrf_token(logged_in_client)
    resp = logged_in_client.post(
        "/admin/users",
        data={
            "csrf_token": token,
            "username": "reader_one",
            "password": "reader-password-123",
            "is_admin": "",
        },
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/users")

    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == "reader_one"))
        assert user is not None
        assert user.is_admin is False

    users_resp = logged_in_client.get("/admin/users")
    assert b"reader_one" in users_resp.data
    assert b"Reader" in users_resp.data


def test_user_create_validation_errors(logged_in_client) -> None:
    token = csrf_token(logged_in_client)

    r1 = logged_in_client.post(
        "/admin/users",
        data={"csrf_token": token, "username": "", "password": "password123"},
    )
    assert r1.status_code == 400
    assert b"Username is required" in r1.data

    r2 = logged_in_client.post(
        "/admin/users",
        data={"csrf_token": token, "username": "bad user!", "password": "password123"},
    )
    assert r2.status_code == 400
    assert b"Username must be 3-64 characters" in r2.data

    r3 = logged_in_client.post(
        "/admin/users",
        data={"csrf_token": token, "username": "valid_user", "password": "short"},
    )
    assert r3.status_code == 400
    assert b"Password must be at least 8 characters long" in r3.data

    r4 = logged_in_client.post(
        "/admin/users",
        data={"csrf_token": token, "username": "admin", "password": "password123"},
    )
    assert r4.status_code == 400
    assert b"already taken" in r4.data


def test_user_change_password(logged_in_client, app) -> None:
    token = csrf_token(logged_in_client)
    logged_in_client.post(
        "/admin/users",
        data={
            "csrf_token": token,
            "username": "charlie",
            "password": "initial-password",
        },
    )
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == "charlie"))
        assert user is not None
        user_id = user.id

    r = logged_in_client.post(
        f"/admin/users/{user_id}/password",
        data={"csrf_token": token, "password": "new-secret-password"},
    )
    assert r.status_code == 302

    verifier = app.extensions["password_verifier"]
    assert verifier.verify("charlie", "new-secret-password")
    assert not verifier.verify("charlie", "initial-password")


def test_user_delete(logged_in_client) -> None:
    token = csrf_token(logged_in_client)
    logged_in_client.post(
        "/admin/users",
        data={
            "csrf_token": token,
            "username": "todelete",
            "password": "some-password",
        },
    )
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == "todelete"))
        assert user is not None
        user_id = user.id

    del_resp = logged_in_client.post(
        f"/admin/users/{user_id}/delete",
        data={"csrf_token": token},
    )
    assert del_resp.status_code == 302

    with session_scope() as session:
        assert session.get(User, user_id) is None


def test_user_cannot_delete_self(logged_in_client) -> None:
    token = csrf_token(logged_in_client)
    with session_scope() as session:
        admin_user = session.scalar(select(User).where(User.username == "admin"))
        assert admin_user is not None
        admin_id = admin_user.id

    resp = logged_in_client.post(
        f"/admin/users/{admin_id}/delete",
        data={"csrf_token": token},
    )
    assert resp.status_code == 302
    with session_scope() as session:
        assert session.get(User, admin_id) is not None


def test_user_can_delete_secondary_admin(logged_in_client) -> None:
    token = csrf_token(logged_in_client)
    logged_in_client.post(
        "/admin/users",
        data={
            "csrf_token": token,
            "username": "admin2",
            "password": "password123",
            "is_admin": "true",
        },
    )
    with session_scope() as session:
        admin2 = session.scalar(select(User).where(User.username == "admin2"))
        assert admin2 is not None
        admin2_id = admin2.id

    del_resp = logged_in_client.post(
        f"/admin/users/{admin2_id}/delete",
        data={"csrf_token": token},
    )
    assert del_resp.status_code == 302
    with session_scope() as session:
        assert session.get(User, admin2_id) is None


def test_user_cannot_delete_last_admin(logged_in_client) -> None:
    token = csrf_token(logged_in_client)
    with session_scope() as session:
        admin = session.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        admin_id = admin.id

    # Simulate an admin session without user_id matching admin_id
    with logged_in_client.session_transaction() as sess:
        sess["user_id"] = 9999

    del_resp = logged_in_client.post(
        f"/admin/users/{admin_id}/delete",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert del_resp.status_code == 200
    assert b"Cannot delete the last remaining administrator" in del_resp.data
    with session_scope() as session:
        assert session.get(User, admin_id) is not None
