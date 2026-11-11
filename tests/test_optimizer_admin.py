"""Tests for the admin optimizer device profiles configuration panel."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import Book, DeviceProfile, LibraryFolder, OptimizedBook
from factories import csrf_token, login_admin

PASSWORD = "admin-secret-test"


def _make_book() -> int:
    with session_scope() as session:
        folder = LibraryFolder(path="/tmp/test-folder", name="test")
        session.add(folder)
        session.flush()
        book = Book(folder_id=folder.id, relative_path="b.epub", title="Test")
        session.add(book)
        session.flush()
        return book.id


@pytest.fixture
def admin_settings(settings: Settings) -> Settings:
    return Settings(
        data_dir=settings.data_dir,
        database_url=settings.database_url,
        session_secret="test-secret",
        admin_username="admin",
        admin_password=PASSWORD,
        auto_scan_interval=0,
    )


@pytest.fixture
def app(admin_settings: Settings, build_app):
    return build_app(admin_settings)


@pytest.fixture
def client(app):
    return app.test_client()


def test_optimizer_settings_requires_login(client) -> None:
    resp = client.get("/admin/optimizer")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_optimizer_settings_renders_profiles(client) -> None:
    login_admin(client, password=PASSWORD)
    resp = client.get("/admin/optimizer")
    assert resp.status_code == 200
    assert b"Optimizer Profiles" in resp.data
    assert b"Xteink X3" in resp.data
    assert b"Xteink X4" in resp.data


def test_update_profile_settings_and_clears_cache(client, app) -> None:
    login_admin(client, password=PASSWORD)
    book_id = _make_book()

    # Seed an optimized book row and cache file
    with session_scope() as session:
        session.add(
            OptimizedBook(
                book_id=book_id,
                profile="x4",
                source_mtime=12345,
                source_size=1000,
                optimized_size=500,
            )
        )
    cache_dir = app.config["SETTINGS"].x4_cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{book_id}.epub"
    cache_file.write_bytes(b"dummy")
    assert cache_file.is_file()

    resp = client.post(
        "/admin/optimizer/x4",
        data={
            "csrf_token": csrf_token(client),
            "enabled": "on",
            "target_width": "600",
            "target_height": "900",
            "cbz_dither": "on",
            "cbz_split_spreads": "on",
            "cbz_rotate_spreads": "on",
            "epub_quality": "85",
            "epub_grayscale": "on",
            "epub_contrast_boost": "on",
            "epub_remove_fonts": "on",
            "epub_remove_unused_css": "on",
            "epub_text_cleanup": "on",
        },
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/optimizer")

    # Cache should be cleared
    assert not cache_file.exists()

    with session_scope() as session:
        p = session.scalar(
            select(DeviceProfile).where(DeviceProfile.name == "x4")
        )
        assert p is not None
        assert p.target_width == 600
        assert p.target_height == 900
        assert p.epub_quality == 85
        assert p.enabled is True

        # OptimizedBook row for x4 should be removed
        rows = session.scalars(
            select(OptimizedBook).where(OptimizedBook.profile == "x4")
        ).all()
        assert rows == []


def test_update_profile_validation_bounds(client) -> None:
    login_admin(client, password=PASSWORD)

    resp = client.post(
        "/admin/optimizer/x4",
        data={
            "csrf_token": csrf_token(client),
            "target_width": "-50",
            "target_height": "20",
            "epub_quality": "150",
        },
    )
    assert resp.status_code == 302

    with session_scope() as session:
        p = session.scalar(
            select(DeviceProfile).where(DeviceProfile.name == "x4")
        )
        assert p is not None
        assert p.target_width == 100  # clamped to min 100
        assert p.target_height == 100  # clamped to min 100
        assert p.epub_quality == 100  # clamped to max 100
        assert p.enabled is False  # absent checkbox means False


def test_update_unknown_profile_returns_404(client) -> None:
    login_admin(client, password=PASSWORD)
    resp = client.post(
        "/admin/optimizer/unknown_profile",
        data={
            "csrf_token": csrf_token(client),
            "target_width": "500",
            "target_height": "700",
        },
    )
    assert resp.status_code == 404


def test_clear_profile_cache_route(client, app) -> None:
    login_admin(client, password=PASSWORD)
    book_id = _make_book()

    with session_scope() as session:
        session.add(
            OptimizedBook(
                book_id=book_id,
                profile="x3",
                source_mtime=123,
                source_size=10,
                optimized_size=5,
            )
        )

    resp = client.post(
        "/admin/optimizer/x3/clear-cache",
        data={"csrf_token": csrf_token(client)},
    )
    assert resp.status_code == 302

    with session_scope() as session:
        rows = session.scalars(
            select(OptimizedBook).where(OptimizedBook.profile == "x3")
        ).all()
        assert rows == []
