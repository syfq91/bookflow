from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from bookflow.app import create_app
from bookflow.config import Settings
from bookflow.database.database import init_engine, reset_engine, session_scope
from bookflow.database.models import Book, LibraryFolder
from bookflow.library.scanner import folder_lock, scan_folder
from bookflow.library.service import add_folder, list_folders, remove_folder
from factories import csrf_token, make_epub

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_PASSWORD = "library-pass"
EXTENSIONS = (".epub", ".pdf", ".cbz", ".cbr", ".mobi", ".azw3")


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest.fixture
def db(settings: Settings):
    reset_engine()
    command.upgrade(alembic_config(settings.database_url), "head")
    init_engine(settings)
    yield
    reset_engine()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "books"
    path.mkdir()
    return path


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


# --- folder service --------------------------------------------------------


def test_add_folder_registers(db, tmp_path: Path) -> None:
    target = tmp_path / "library"
    target.mkdir()

    result = add_folder(str(target))

    assert result.ok
    assert result.path == str(target.resolve())
    assert result.name == "library"
    with session_scope() as session:
        folder = session.query(LibraryFolder).one()
        assert folder.enabled is True
        assert folder.last_scan_at is None


def test_add_folder_rejects_empty(db) -> None:
    result = add_folder("   ")

    assert not result.ok
    assert "Enter a folder path" in (result.error or "")


def test_add_folder_rejects_relative_path(db) -> None:
    result = add_folder("books")

    assert not result.ok
    assert "absolute" in (result.error or "")


def test_add_folder_rejects_missing_path(db, tmp_path: Path) -> None:
    result = add_folder(str(tmp_path / "nope"))

    assert not result.ok
    assert "does not exist" in (result.error or "")


def test_add_folder_rejects_file(db, tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("not a folder")

    result = add_folder(str(target))

    assert not result.ok
    assert "not a directory" in (result.error or "")


def test_add_folder_rejects_duplicate(db, root: Path) -> None:
    assert add_folder(str(root)).ok

    again = add_folder(str(root) + os.sep)

    assert not again.ok
    assert "already registered" in (again.error or "")


def test_add_folder_rejects_nested_child(db, tmp_path: Path) -> None:
    parent = tmp_path / "books"
    (parent / "child").mkdir(parents=True)
    assert add_folder(str(parent)).ok

    nested = add_folder(str(parent / "child"))

    assert not nested.ok
    assert "inside registered folder" in (nested.error or "")


def test_add_folder_rejects_nested_parent(db, tmp_path: Path) -> None:
    child = tmp_path / "books" / "child"
    child.mkdir(parents=True)
    assert add_folder(str(child)).ok

    parent = add_folder(str(tmp_path / "books"))

    assert not parent.ok
    assert "is inside this path" in (parent.error or "")


def test_remove_folder_deletes_index_but_not_files(db, root: Path) -> None:
    make_epub(root / "dune.epub", title="Dune")
    result = add_folder(str(root))
    assert result.folder_id is not None
    scan_folder(result.folder_id, EXTENSIONS)

    assert remove_folder(result.folder_id) is True

    with session_scope() as session:
        assert session.query(LibraryFolder).count() == 0
        assert session.query(Book).count() == 0
    assert (root / "dune.epub").exists()


def test_remove_unknown_folder(db) -> None:
    assert remove_folder(4242) is False


def test_list_folders_reports_stats(db, root: Path) -> None:
    make_epub(root / "dune.epub", title="Dune")
    result = add_folder(str(root))
    assert result.folder_id is not None
    scan_folder(result.folder_id, EXTENSIONS)

    folders = list_folders()

    assert len(folders) == 1
    assert folders[0]["books"] == 1
    assert folders[0]["size"] == (root / "dune.epub").stat().st_size
    assert folders[0]["last_scan_status"] == "ok"
    assert folders[0]["last_scan_error"] is None


# --- admin routes ----------------------------------------------------------


def test_folders_page_requires_login(client) -> None:
    assert client.get("/admin/folders").status_code == 302
    assert client.get("/admin/folders/new").status_code == 302


def test_folders_page_renders_empty_state(client) -> None:
    _login(client)

    resp = client.get("/admin/folders")

    assert resp.status_code == 200
    assert b"Library Folders" in resp.data
    assert b"No library folders yet" in resp.data


def test_add_folder_form_renders(client) -> None:
    _login(client)

    resp = client.get("/admin/folders/new")

    assert resp.status_code == 200
    assert b"Add library folder" in resp.data


def test_add_folder_requires_csrf(client, tmp_path: Path) -> None:
    _login(client)
    target = tmp_path / "books"
    target.mkdir()

    resp = client.post("/admin/folders", data={"path": str(target)})

    assert resp.status_code == 400


def test_add_folder_via_ui_registers_and_scans(client, tmp_path: Path) -> None:
    _login(client)
    target = tmp_path / "ui"
    target.mkdir()
    make_epub(target / "dune.epub", title="Dune")

    resp = client.post(
        "/admin/folders",
        data={"path": str(target), "csrf_token": csrf_token(client)},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"1 added" in resp.data
    assert target.name.encode() in resp.data
    with session_scope() as session:
        assert session.query(Book).count() == 1


def test_add_invalid_folder_shows_error(client) -> None:
    _login(client)

    resp = client.post(
        "/admin/folders",
        data={"path": "/nonexistent/bookflow", "csrf_token": csrf_token(client)},
    )

    assert resp.status_code == 400
    assert b"does not exist" in resp.data


def test_add_relative_folder_shows_error(client) -> None:
    _login(client)

    resp = client.post(
        "/admin/folders",
        data={"path": "relative/path", "csrf_token": csrf_token(client)},
    )

    assert resp.status_code == 400
    assert b"absolute" in resp.data


def test_scan_route_rescans_folder(client, tmp_path: Path) -> None:
    _login(client)
    target = tmp_path / "scanme"
    target.mkdir()
    make_epub(target / "a.epub", title="A")
    client.post(
        "/admin/folders",
        data={"path": str(target), "csrf_token": csrf_token(client)},
    )
    with session_scope() as session:
        folder_id = session.query(LibraryFolder).one().id

    make_epub(target / "b.epub", title="B")
    resp = client.post(
        f"/admin/folders/{folder_id}/scan",
        data={"csrf_token": csrf_token(client)},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"1 added" in resp.data
    with session_scope() as session:
        assert session.query(Book).count() == 2


def test_scan_unknown_folder_returns_404(client) -> None:
    _login(client)

    resp = client.post(
        "/admin/folders/4242/scan", data={"csrf_token": csrf_token(client)}
    )

    assert resp.status_code == 404


def test_scan_route_conflicts_when_already_running(
    client, tmp_path: Path
) -> None:
    _login(client)
    target = tmp_path / "busy"
    target.mkdir()
    client.post(
        "/admin/folders",
        data={"path": str(target), "csrf_token": csrf_token(client)},
    )
    with session_scope() as session:
        folder_id = session.query(LibraryFolder).one().id

    lock = folder_lock(folder_id)
    assert lock.acquire(blocking=False)
    try:
        resp = client.post(
            f"/admin/folders/{folder_id}/scan",
            data={"csrf_token": csrf_token(client)},
        )
    finally:
        lock.release()

    assert resp.status_code == 409
    assert b"already in progress" in resp.data


def test_delete_route_removes_folder_but_keeps_files(
    client, tmp_path: Path
) -> None:
    _login(client)
    target = tmp_path / "deleteme"
    target.mkdir()
    make_epub(target / "a.epub", title="A")
    client.post(
        "/admin/folders",
        data={"path": str(target), "csrf_token": csrf_token(client)},
    )
    with session_scope() as session:
        folder_id = session.query(LibraryFolder).one().id

    resp = client.post(
        f"/admin/folders/{folder_id}/delete",
        data={"csrf_token": csrf_token(client)},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"were not changed" in resp.data
    with session_scope() as session:
        assert session.query(LibraryFolder).count() == 0
        assert session.query(Book).count() == 0
    assert (target / "a.epub").exists()


def test_delete_unknown_folder_returns_404(client) -> None:
    _login(client)

    resp = client.post(
        "/admin/folders/4242/delete", data={"csrf_token": csrf_token(client)}
    )

    assert resp.status_code == 404
