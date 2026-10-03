from __future__ import annotations

import base64
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from bookflow.admin.routes import _dashboard_stats, _health_stats
from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import Book, LibraryFolder, OptimizedBook
from bookflow.library.scanner import scan_folder
from bookflow.library.service import add_folder
from factories import login_admin, make_epub, make_pdf

PASSWORD = "health-pass"
EXTENSIONS = (".epub", ".pdf")
PROGRESSION_TYPE = "application/opds-progression+json"


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def health_settings(settings: Settings) -> Settings:
    return replace(settings, admin_username="admin", admin_password=PASSWORD)


@pytest.fixture
def app(build_app, health_settings: Settings):
    return build_app(health_settings)


@pytest.fixture
def unmigrated_client(build_app, health_settings: Settings):
    return build_app(health_settings, migrate=False).test_client()


@pytest.fixture
def folder_id(client, root: Path) -> int:
    result = add_folder(str(root))
    assert result.ok, result.error
    assert result.folder_id is not None
    return result.folder_id


def _health(client):
    login_admin(client, password=PASSWORD)
    return client.get("/admin/health")


def _book_id(folder_id: int, title: str = "Dune") -> int:
    with session_scope() as session:
        book = session.scalar(select(Book).where(Book.title == title))
        assert book is not None
        return book.id


def _scan(folder_id: int):
    return scan_folder(folder_id, EXTENSIONS)


# --- auth and component checks ----------------------------------------------


def test_health_requires_login(client) -> None:
    resp = client.get("/admin/health")

    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_health_lists_every_component(client) -> None:
    resp = _health(client)

    assert resp.status_code == 200
    for component in (b"Database", b"Library", b"Cache", b"epubkit", b"OPDS"):
        assert component in resp.data
    assert b"badge-ok" in resp.data
    assert b"routes registered" in resp.data
    assert b"importable" in resp.data


def test_database_check_flags_missing_migrations(unmigrated_client) -> None:
    resp = _health(unmigrated_client)

    assert resp.status_code == 200
    assert b"badge-error" in resp.data
    assert b"Database not initialized" in resp.data


def test_library_check_reports_missing_folder(client, folder_id, root) -> None:
    shutil.rmtree(root)

    resp = _health(client)

    assert b"badge-error" in resp.data
    assert str(root).encode() in resp.data


def test_library_check_warns_without_folders(client) -> None:
    resp = _health(client)

    assert b"badge-warn" in resp.data
    assert b"No library folders registered." in resp.data


def test_cache_check_flags_removed_cache_dir(
    client, health_settings
) -> None:
    shutil.rmtree(health_settings.cache_dir, ignore_errors=True)

    resp = _health(client)

    assert b"badge-error" in resp.data
    assert b"does not exist" in resp.data


# --- scanner status ---------------------------------------------------------


def test_scanner_reports_last_successful_scan(
    client, folder_id, root
) -> None:
    make_epub(root / "dune.epub", title="Dune")
    _scan(folder_id)
    with session_scope() as session:
        folder = session.get(LibraryFolder, folder_id)
        last_scan = folder.last_scan_at

    resp = _health(client)

    assert b"Scanner" in resp.data
    assert last_scan.strftime("%Y-%m-%d %H:%M").encode() in resp.data


def test_scanner_reports_folder_errors(client, folder_id) -> None:
    with session_scope() as session:
        folder = session.get(LibraryFolder, folder_id)
        folder.last_scan_status = "error"
        folder.last_scan_error = "disk went away"

    resp = _health(client)

    assert b"disk went away" in resp.data
    assert b"1 folder(s)" in resp.data


# --- statistics -------------------------------------------------------------


def test_statistics_break_down_format_and_progression(
    client, folder_id, root
) -> None:
    make_epub(root / "dune.epub", title="Dune", authors=("Frank Herbert",))
    make_pdf(root / "report.pdf", title="Quarterly Report")
    _scan(folder_id)

    book_id = _book_id(folder_id)
    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    put = client.put(
        f"/opds/publications/{book_id}/progression",
        data=json.dumps(
            {
                "modified": "2026-01-27T11:00:00Z",
                "device": {"id": "urn:uuid:test", "name": "Reader"},
                "progression": 0.5,
                "title": "Chapter 1",
                "references": ["c1.html"],
            }
        ),
        content_type=PROGRESSION_TYPE,
        headers={"Authorization": f"Basic {token}"},
    )
    assert put.status_code in (200, 201)

    resp = _health(client)

    assert b"Library statistics" in resp.data
    assert b"<dt>epub</dt>" in resp.data
    assert b"<dt>pdf</dt>" in resp.data
    assert b"Progression" in resp.data
    assert b"Reader" in resp.data
    assert b"Optimization cache" in resp.data


# --- dashboard --------------------------------------------------------------


def test_dashboard_shows_cache_sizes_and_health(
    client, folder_id
) -> None:
    with session_scope() as session:
        book = Book(folder_id=folder_id, relative_path="dune.epub", title="Dune")
        session.add(book)
        session.flush()
        session.add(
            OptimizedBook(
                book_id=book.id,
                profile="x3",
                source_mtime=1,
                source_size=100,
                optimized_size=1234,
            )
        )

    login_admin(client, password=PASSWORD)
    resp = client.get("/admin/")

    assert resp.status_code == 200
    assert b"Optimization cache" in resp.data
    assert b"1.2 kB" in resp.data
    assert b"Health" in resp.data
    assert b"badge-ok" in resp.data
    assert b"Duration" in resp.data


def test_dashboard_still_renders_without_migrations(
    unmigrated_client,
) -> None:
    login_admin(unmigrated_client, password=PASSWORD)

    resp = unmigrated_client.get("/admin/")

    assert resp.status_code == 200
    assert b"Database not initialized" in resp.data
    assert b"badge-error" in resp.data


def test_stats_fallbacks_keep_the_complete_shape(unmigrated_client) -> None:
    health = _health_stats()
    dashboard = _dashboard_stats()

    assert "Database not initialized" in str(health["db_error"])
    assert health["scanner"] == {"last_success": None, "errors": []}
    assert health["cache"] == {
        "x3": {"books": 0, "size": 0},
        "x4": {"books": 0, "size": 0},
    }
    assert health["progression"] == {"books": 0, "devices": []}
    assert "Database not initialized" in str(dashboard["db_error"])
    assert dashboard["books"] == 0
    assert dashboard["cache"] == {"x3": 0, "x4": 0}
