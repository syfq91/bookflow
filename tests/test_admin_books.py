from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from bookflow.app import create_app
from bookflow.config import Settings
from bookflow.database.database import reset_engine, session_scope
from bookflow.database.models import Book
from bookflow.library.scanner import scan_folder
from bookflow.library.service import add_folder
from factories import csrf_token, make_epub, make_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_PASSWORD = "library-pass"
EXTENSIONS = (".epub", ".pdf", ".cbz", ".cbr", ".mobi", ".azw3")


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
    command.upgrade(alembic_config(auth_settings.database_url), "head")
    yield application
    reset_engine()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "books"
    path.mkdir()
    return path


def _login(client) -> None:
    resp = client.post(
        "/admin/login",
        data={
            "csrf_token": csrf_token(client),
            "username": "admin",
            "password": ADMIN_PASSWORD,
        },
    )
    assert resp.status_code == 302


def _seed_tree(root: Path) -> int:
    """Create files on disk, register the folder, and scan it."""
    (root / "sub").mkdir(parents=True, exist_ok=True)
    (root / "sub" / "deep").mkdir(exist_ok=True)
    make_epub(root / "top.epub", title="Top")
    make_epub(root / "sub" / "nested.epub", title="Nested")
    make_epub(root / "sub" / "deep" / "hidden.epub", title="Hidden")
    make_pdf(root / "manual.pdf", title="Manual")
    folder_id = add_folder(str(root)).folder_id
    assert folder_id is not None
    scan_folder(folder_id, EXTENSIONS)
    return folder_id


def _book_id(relative_path: str) -> int:
    with session_scope() as session:
        book = session.scalars(
            select(Book).where(Book.relative_path == relative_path)
        ).one()
        return book.id


# --- library tree routes ----------------------------------------------------


def test_library_routes_require_login(client) -> None:
    assert client.get("/admin/library").status_code == 302
    assert client.get("/admin/library/1").status_code == 302
    assert client.get("/admin/books/1/download").status_code == 302


def test_library_index_redirects_to_folders(client) -> None:
    _login(client)

    resp = client.get("/admin/library")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/folders")


def test_folders_lists_registered_folders_without_books(client, root: Path) -> None:
    _login(client)
    folder_id = _seed_tree(root)

    resp = client.get("/admin/folders")

    assert resp.status_code == 200
    assert b"<h1>Library</h1>" in resp.data
    assert b"top.epub" not in resp.data
    assert f"/admin/library/{folder_id}".encode() in resp.data


def test_dashboard_links_to_library(client) -> None:
    _login(client)

    resp = client.get("/admin/")

    assert b'href="/admin/folders">Library</a>' in resp.data


def test_tree_lists_subfolders_and_files(client, root: Path) -> None:
    _login(client)
    folder_id = _seed_tree(root)

    resp = client.get(f"/admin/library/{folder_id}")

    assert resp.status_code == 200
    assert b"<h2>Folders</h2>" in resp.data
    assert b"sub</a" in resp.data
    assert b"<h2>Files</h2>" in resp.data
    assert b"top.epub" in resp.data
    assert b"manual.pdf" in resp.data
    assert b"nested.epub" not in resp.data
    expected = f'<span aria-current="page">{root.name}</span>'.encode()
    assert expected in resp.data
    assert resp.data.count(b'aria-current="page"') == 1


def test_tree_drills_into_subfolder(client, root: Path) -> None:
    _login(client)
    folder_id = _seed_tree(root)

    resp = client.get(f"/admin/library/{folder_id}?path=sub")

    assert resp.status_code == 200
    assert b"nested.epub" in resp.data
    assert b"deep</a" in resp.data
    assert b"top.epub" not in resp.data
    assert b'aria-current="page">sub' in resp.data
    assert b"&uarr; Up" in resp.data


def test_tree_two_levels_deep(client, root: Path) -> None:
    _login(client)
    folder_id = _seed_tree(root)

    resp = client.get(f"/admin/library/{folder_id}?path=sub/deep")

    assert resp.status_code == 200
    assert b"hidden.epub" in resp.data
    assert b"nested.epub" not in resp.data


def test_tree_unknown_folder_returns_404(client) -> None:
    _login(client)

    assert client.get("/admin/library/4242").status_code == 404


@pytest.mark.parametrize(
    "bad",
    ["../x", "/etc", "a//b", "a/./b", "..", "a\\b"],
)
def test_tree_rejects_bad_paths(client, root: Path, bad: str) -> None:
    _login(client)
    folder_id = _seed_tree(root)

    resp = client.get(f"/admin/library/{folder_id}?path={bad}")

    assert resp.status_code == 400


def test_tree_flat_folder_shows_files_only(client, root: Path) -> None:
    _login(client)
    make_epub(root / "solo.epub", title="Solo")
    folder_id = add_folder(str(root)).folder_id
    assert folder_id is not None
    scan_folder(folder_id, EXTENSIONS)

    resp = client.get(f"/admin/library/{folder_id}")

    assert resp.status_code == 200
    assert b"<h2>Folders</h2>" not in resp.data
    assert b"solo.epub" in resp.data


# --- downloads --------------------------------------------------------------


def test_download_returns_file_bytes(client, root: Path) -> None:
    _login(client)
    _seed_tree(root)
    book_id = _book_id("top.epub")

    resp = client.get(f"/admin/books/{book_id}/download")

    assert resp.status_code == 200
    assert resp.data == (root / "top.epub").read_bytes()
    assert resp.headers["Content-Type"] == "application/epub+zip"
    assert resp.headers["Content-Disposition"].startswith("attachment")


def test_download_pdf_mime_type(client, root: Path) -> None:
    _login(client)
    _seed_tree(root)
    book_id = _book_id("manual.pdf")

    resp = client.get(f"/admin/books/{book_id}/download")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/pdf"


def test_download_rejects_path_traversal(client, root: Path) -> None:
    _login(client)
    outside = root.parent / "outside.epub"
    make_epub(outside, title="Outside")
    folder_id = add_folder(str(root)).folder_id
    assert folder_id is not None
    with session_scope() as session:
        session.add(
            Book(
                folder_id=folder_id,
                relative_path="../outside.epub",
                title="Outside",
                file_format="epub",
            )
        )
        session.flush()
        book_id = session.query(Book).one().id

    resp = client.get(f"/admin/books/{book_id}/download")

    assert resp.status_code == 404


def test_download_missing_file_returns_404(client, root: Path) -> None:
    _login(client)
    folder_id = add_folder(str(root)).folder_id
    assert folder_id is not None
    with session_scope() as session:
        session.add(
            Book(
                folder_id=folder_id,
                relative_path="ghost.epub",
                title="Ghost",
                file_format="epub",
            )
        )
        session.flush()
        book_id = session.query(Book).one().id

    resp = client.get(f"/admin/books/{book_id}/download")

    assert resp.status_code == 404


def test_download_unknown_book_returns_404(client) -> None:
    _login(client)

    assert client.get("/admin/books/4242/download").status_code == 404
