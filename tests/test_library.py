from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import func, select

from bookflow.config import Settings
from bookflow.database.database import init_engine, reset_engine, session_scope
from bookflow.database.models import Book, LibraryFolder
from bookflow.library import browse as browse_module
from bookflow.library.browse import browse_directory
from bookflow.library.scanner import folder_lock, scan_folder
from bookflow.library.service import (
    add_folder,
    get_folder,
    list_folders,
    remove_folder,
)
from factories import alembic_config, csrf_token, login_admin, make_epub

ADMIN_PASSWORD = "library-pass"
EXTENSIONS = (".epub", ".pdf", ".cbz", ".cbr", ".mobi", ".azw3")


@pytest.fixture(autouse=True)
def db(settings: Settings):
    reset_engine()
    command.upgrade(alembic_config(settings.database_url), "head")
    init_engine(settings)
    yield
    reset_engine()


@pytest.fixture
def auth_settings(settings: Settings, tmp_path: Path) -> Settings:
    return replace(
        settings,
        admin_username="admin",
        admin_password=ADMIN_PASSWORD,
        browse_root=tmp_path,
    )


@pytest.fixture
def app(build_app, auth_settings: Settings):
    return build_app(auth_settings)


# --- folder service --------------------------------------------------------


def test_add_folder_registers(tmp_path: Path) -> None:
    target = tmp_path / "library"
    target.mkdir()

    result = add_folder(str(target))

    assert result.ok
    assert result.path == str(target.resolve())
    assert result.name == "library"
    with session_scope() as session:
        folder = session.scalars(select(LibraryFolder)).one()
        assert folder.last_scan_at is None


def test_add_folder_rejects_empty() -> None:
    result = add_folder("   ")

    assert not result.ok
    assert "Enter a folder path" in (result.error or "")


def test_add_folder_rejects_relative_path() -> None:
    result = add_folder("books")

    assert not result.ok
    assert "absolute" in (result.error or "")


def test_add_folder_rejects_missing_path(tmp_path: Path) -> None:
    result = add_folder(str(tmp_path / "nope"))

    assert not result.ok
    assert "does not exist" in (result.error or "")


def test_add_folder_rejects_file(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("not a folder")

    result = add_folder(str(target))

    assert not result.ok
    assert "not a directory" in (result.error or "")


def test_add_folder_rejects_duplicate(root: Path) -> None:
    assert add_folder(str(root)).ok

    again = add_folder(str(root) + os.sep)

    assert not again.ok
    assert "already registered" in (again.error or "")


def test_add_folder_rejects_nested_child(tmp_path: Path) -> None:
    parent = tmp_path / "books"
    (parent / "child").mkdir(parents=True)
    assert add_folder(str(parent)).ok

    nested = add_folder(str(parent / "child"))

    assert not nested.ok
    assert "inside registered folder" in (nested.error or "")


def test_add_folder_rejects_nested_parent(tmp_path: Path) -> None:
    child = tmp_path / "books" / "child"
    child.mkdir(parents=True)
    assert add_folder(str(child)).ok

    parent = add_folder(str(tmp_path / "books"))

    assert not parent.ok
    assert "is inside this path" in (parent.error or "")


def test_remove_folder_deletes_index_but_not_files(root: Path) -> None:
    make_epub(root / "dune.epub", title="Dune")
    result = add_folder(str(root))
    assert result.folder_id is not None
    scan_folder(result.folder_id, EXTENSIONS)

    assert remove_folder(result.folder_id) is True

    with session_scope() as session:
        assert session.scalar(select(func.count(LibraryFolder.id))) == 0
        assert session.scalar(select(func.count(Book.id))) == 0
    assert (root / "dune.epub").exists()


def test_remove_unknown_folder() -> None:
    assert remove_folder(4242) is False


def test_list_folders_reports_stats(root: Path) -> None:
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


def test_get_folder_matches_the_list_entry(root: Path) -> None:
    make_epub(root / "dune.epub", title="Dune")
    result = add_folder(str(root))
    assert result.folder_id is not None
    scan_folder(result.folder_id, EXTENSIONS)

    folder = get_folder(result.folder_id)

    assert folder == list_folders()[0]
    assert get_folder(4242) is None


# --- browse service ---------------------------------------------------------


def test_browse_lists_directories_only(tmp_path: Path) -> None:
    (tmp_path / "b-books").mkdir()
    (tmp_path / "A-comics").mkdir()
    (tmp_path / "notes.txt").write_text("not a folder")
    root = tmp_path.resolve()

    result = browse_directory(None, root)

    assert result.ok
    assert [name for name, _ in result.entries] == ["A-comics", "b-books"]
    assert result.path == str(root)
    assert result.parent is None
    assert not result.truncated
    assert result.crumbs == [(root.name or str(root), str(root))]


def test_browse_nested_shows_parent_and_crumbs(tmp_path: Path) -> None:
    child = tmp_path / "media" / "books"
    child.mkdir(parents=True)
    root = tmp_path.resolve()

    result = browse_directory(str(child), root)

    assert result.ok
    assert result.path == str(child.resolve())
    assert result.parent == str(root / "media")
    assert [label for label, _ in result.crumbs] == [
        root.name or str(root),
        "media",
        "books",
    ]
    assert result.crumbs[-1] == ("books", str(child.resolve()))


def test_browse_rejects_outside_root(tmp_path: Path) -> None:
    result = browse_directory("/etc", tmp_path)

    assert not result.ok
    assert "outside the browse root" in (result.error or "")


def test_browse_rejects_relative_path(tmp_path: Path) -> None:
    result = browse_directory("books", tmp_path)

    assert not result.ok
    assert "absolute" in (result.error or "")


def test_browse_rejects_missing_path(tmp_path: Path) -> None:
    result = browse_directory(str(tmp_path / "nope"), tmp_path)

    assert not result.ok
    assert "does not exist" in (result.error or "")


def test_browse_rejects_file(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("not a folder")

    result = browse_directory(str(target), tmp_path)

    assert not result.ok
    assert "not a directory" in (result.error or "")


def test_browse_unusable_root(tmp_path: Path) -> None:
    result = browse_directory(None, tmp_path / "missing-root")

    assert not result.ok
    assert "not usable" in (result.error or "")


def test_browse_caps_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(5):
        (tmp_path / f"dir{index}").mkdir()
    monkeypatch.setattr(browse_module, "MAX_ENTRIES", 2)

    result = browse_directory(None, tmp_path)

    assert result.ok
    assert len(result.entries) == 2
    assert result.truncated


def test_browse_follows_symlinked_directory(tmp_path: Path) -> None:
    target = tmp_path / "real" / "nested"
    target.mkdir(parents=True)
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    root = tmp_path.resolve()

    listing = browse_directory(None, root)
    assert ("alias", str(root / "alias")) in listing.entries

    drilled = browse_directory(str(root / "alias"), root)
    assert drilled.ok
    assert drilled.path == str((tmp_path / "real").resolve())


# --- admin routes ----------------------------------------------------------


def test_folders_page_requires_login(client) -> None:
    assert client.get("/admin/folders").status_code == 302
    assert client.get("/admin/folders/new").status_code == 302


def test_folders_page_renders_empty_state(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.get("/admin/folders")

    assert resp.status_code == 200
    assert b"<h1>Library</h1>" in resp.data
    assert b"No library folders yet" in resp.data


def test_add_folder_form_renders(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.get("/admin/folders/new")

    assert resp.status_code == 200
    assert b"Add library folder" in resp.data


def test_add_folder_requires_csrf(client, tmp_path: Path) -> None:
    login_admin(client, password=ADMIN_PASSWORD)
    target = tmp_path / "books"
    target.mkdir()

    resp = client.post("/admin/folders", data={"path": str(target)})

    assert resp.status_code == 400


def test_add_folder_via_ui_registers_and_scans(client, tmp_path: Path) -> None:
    login_admin(client, password=ADMIN_PASSWORD)
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
        assert session.scalar(select(func.count(Book.id))) == 1


def test_add_invalid_folder_shows_error(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.post(
        "/admin/folders",
        data={"path": "/nonexistent/bookflow", "csrf_token": csrf_token(client)},
    )

    assert resp.status_code == 400
    assert b"does not exist" in resp.data


def test_add_relative_folder_shows_error(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.post(
        "/admin/folders",
        data={"path": "relative/path", "csrf_token": csrf_token(client)},
    )

    assert resp.status_code == 400
    assert b"absolute" in resp.data


def test_scan_route_rescans_folder(client, tmp_path: Path) -> None:
    login_admin(client, password=ADMIN_PASSWORD)
    target = tmp_path / "scanme"
    target.mkdir()
    make_epub(target / "a.epub", title="A")
    client.post(
        "/admin/folders",
        data={"path": str(target), "csrf_token": csrf_token(client)},
    )
    with session_scope() as session:
        folder_id = session.scalars(select(LibraryFolder)).one().id

    make_epub(target / "b.epub", title="B")
    resp = client.post(
        f"/admin/folders/{folder_id}/scan",
        data={"csrf_token": csrf_token(client)},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"1 added" in resp.data
    with session_scope() as session:
        assert session.scalar(select(func.count(Book.id))) == 2


def test_scan_unknown_folder_returns_404(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.post(
        "/admin/folders/4242/scan", data={"csrf_token": csrf_token(client)}
    )

    assert resp.status_code == 404


def test_scan_route_conflicts_when_already_running(
    client, tmp_path: Path
) -> None:
    login_admin(client, password=ADMIN_PASSWORD)
    target = tmp_path / "busy"
    target.mkdir()
    client.post(
        "/admin/folders",
        data={"path": str(target), "csrf_token": csrf_token(client)},
    )
    with session_scope() as session:
        folder_id = session.scalars(select(LibraryFolder)).one().id

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
    login_admin(client, password=ADMIN_PASSWORD)
    target = tmp_path / "deleteme"
    target.mkdir()
    make_epub(target / "a.epub", title="A")
    client.post(
        "/admin/folders",
        data={"path": str(target), "csrf_token": csrf_token(client)},
    )
    with session_scope() as session:
        folder_id = session.scalars(select(LibraryFolder)).one().id

    resp = client.post(
        f"/admin/folders/{folder_id}/delete",
        data={"csrf_token": csrf_token(client)},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"were not changed" in resp.data
    with session_scope() as session:
        assert session.scalar(select(func.count(LibraryFolder.id))) == 0
        assert session.scalar(select(func.count(Book.id))) == 0
    assert (target / "a.epub").exists()


def test_delete_unknown_folder_returns_404(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.post(
        "/admin/folders/4242/delete", data={"csrf_token": csrf_token(client)}
    )

    assert resp.status_code == 404


# --- browse routes ----------------------------------------------------------


def _hrefs_to(html: bytes, endpoint: str) -> list[str]:
    """Decoded hrefs pointing at ``endpoint`` (query included)."""
    text = html.decode()
    pattern = rf'href="([^"]*{re.escape(endpoint)}[^"]*)"'
    return [urllib.parse.unquote(match) for match in re.findall(pattern, text)]


def test_browse_requires_login(client) -> None:
    assert client.get("/admin/folders/browse").status_code == 302


def test_browse_page_renders_directories(client, tmp_path: Path) -> None:
    login_admin(client, password=ADMIN_PASSWORD)
    (tmp_path / "media").mkdir()

    resp = client.get("/admin/folders/browse")

    assert resp.status_code == 200
    assert b"Browse server folders" in resp.data
    assert b">media<" in resp.data
    assert b"Select this folder" in resp.data


def test_browse_outside_root_returns_400(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.get("/admin/folders/browse?path=/etc")

    assert resp.status_code == 400
    assert b"outside the browse root" in resp.data


def test_browse_relative_path_returns_400(client) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.get("/admin/folders/browse?path=books")

    assert resp.status_code == 400
    assert b"absolute" in resp.data


def test_browse_drill_down_offers_up_link(client, tmp_path: Path) -> None:
    login_admin(client, password=ADMIN_PASSWORD)
    child = tmp_path / "media" / "books"
    child.mkdir(parents=True)
    root = tmp_path.resolve()

    resp = client.get(f"/admin/folders/browse?path={child}")

    assert resp.status_code == 200
    assert b"&uarr; Up" in resp.data
    up_links = [
        href
        for href in _hrefs_to(resp.data, "/admin/folders/browse")
        if href.endswith(f"path={root}")
    ]
    assert up_links
    assert b"Select this folder" in resp.data


def test_add_form_prefills_path_from_query(client, tmp_path: Path) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.get(f"/admin/folders/new?path={tmp_path}")

    assert resp.status_code == 200
    assert f'value="{tmp_path}"'.encode() in resp.data


def test_add_form_links_to_browser(client, tmp_path: Path) -> None:
    login_admin(client, password=ADMIN_PASSWORD)

    resp = client.get(f"/admin/folders/new?path={tmp_path}")

    browse_links = _hrefs_to(resp.data, "/admin/folders/browse")
    assert browse_links
    assert browse_links[0].endswith(f"path={tmp_path}")


def test_browse_select_links_target_the_form(client, tmp_path: Path) -> None:
    login_admin(client, password=ADMIN_PASSWORD)
    target = tmp_path / "picked"
    target.mkdir()

    resp = client.get("/admin/folders/browse")

    select_links = _hrefs_to(resp.data, "/admin/folders/new")
    assert any(link.endswith(f"path={target}") for link in select_links)


def test_browse_marks_registered_and_conflicting_folders(
    client, tmp_path: Path
) -> None:
    login_admin(client, password=ADMIN_PASSWORD)
    registered = tmp_path / "lib"
    registered.mkdir()
    holder = tmp_path / "holder" / "nested"
    holder.mkdir(parents=True)
    assert add_folder(str(registered)).ok
    assert add_folder(str(holder)).ok

    resp = client.get("/admin/folders/browse")

    assert resp.status_code == 200
    html = resp.data.decode()
    assert re.search(
        r"lib</a\s*>\s*<span class=\"badge badge-ok\">registered", html
    )
    assert re.search(
        r"holder</a\s*>\s*<span class=\"badge badge-warn\">conflicts", html
    )


def test_browse_to_register_flow(client, tmp_path: Path) -> None:
    login_admin(client, password=ADMIN_PASSWORD)
    target = tmp_path / "picked"
    target.mkdir()
    make_epub(target / "dune.epub", title="Dune")

    browse = client.get("/admin/folders/browse")
    assert browse.status_code == 200
    assert b"picked" in browse.data

    form = client.get(f"/admin/folders/new?path={target}")
    assert f'value="{target}"'.encode() in form.data

    resp = client.post(
        "/admin/folders",
        data={"path": str(target), "csrf_token": csrf_token(client)},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"1 added" in resp.data
    with session_scope() as session:
        assert session.scalar(select(func.count(Book.id))) == 1
