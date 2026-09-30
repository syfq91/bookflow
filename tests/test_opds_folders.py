from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

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
from factories import make_epub, make_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "folders-pass"
EXTENSIONS = (".epub", ".pdf")

ATOM = "http://www.w3.org/2005/Atom"
NS = {"a": ATOM}
NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
ACQUISITION_REL = "http://opds-spec.org/acquisition"
SUBSECTION_REL = "subsection"


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def opds_settings(settings: Settings) -> Settings:
    return replace(settings, admin_password=PASSWORD)


@pytest.fixture
def app(opds_settings: Settings):
    reset_engine()
    application = create_app(opds_settings)
    application.config["TESTING"] = True
    command.upgrade(alembic_config(opds_settings.database_url), "head")
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


@pytest.fixture
def folder_id(root: Path) -> int:
    result = add_folder(str(root))
    assert result.ok, result.error
    assert result.folder_id is not None
    return result.folder_id


# --- helpers ----------------------------------------------------------------


def _headers(password: str = PASSWORD, username: str = "admin") -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _get(client, path: str):
    return client.get(path, headers=_headers())


def _parse(resp) -> ElementTree.Element:
    assert resp.status_code == 200
    return ElementTree.fromstring(resp.data)


def _entries(feed: ElementTree.Element) -> list[ElementTree.Element]:
    return feed.findall("a:entry", NS)


def _titles(feed: ElementTree.Element) -> list[str]:
    return [entry.findtext("a:title", namespaces=NS) for entry in _entries(feed)]


def _feed_links(feed: ElementTree.Element) -> list[ElementTree.Element]:
    return feed.findall("a:link", NS)


def _entry_links(entry: ElementTree.Element) -> list[ElementTree.Element]:
    return entry.findall("a:link", NS)


def _links_by_rel(links) -> dict[str, list[ElementTree.Element]]:
    grouped: dict[str, list[ElementTree.Element]] = {}
    for link in links:
        grouped.setdefault(link.get("rel", ""), []).append(link)
    return grouped


def _subsection_titles(feed: ElementTree.Element) -> list[str]:
    links = _links_by_rel(_feed_links(feed)).get(SUBSECTION_REL, [])
    return [link.get("title", "") for link in links]


def _scan(folder_id: int):
    return scan_folder(folder_id, EXTENSIONS)


def _insert(folder_id: int, specs: list[dict]) -> list[int]:
    """Insert book rows directly (no files needed for feed tests)."""
    ids: list[int] = []
    with session_scope() as session:
        for index, spec in enumerate(specs):
            book = Book(
                folder_id=folder_id,
                relative_path=spec.get("path", f"book{index}.epub"),
                title=spec.get("title"),
            )
            session.add(book)
            session.flush()
            ids.append(book.id)
    return ids


def _register(tmp_path: Path, name: str) -> int:
    path = tmp_path / name
    path.mkdir()
    result = add_folder(str(path))
    assert result.ok, result.error
    assert result.folder_id is not None
    return result.folder_id


# --- discovery --------------------------------------------------------------


def test_root_feed_links_folders(client) -> None:
    feed = _parse(_get(client, "/opds"))

    by_rel = _links_by_rel(_feed_links(feed))
    folders = [
        link
        for link in by_rel[SUBSECTION_REL]
        if link.get("href") == "/opds/folders"
    ]
    assert len(folders) == 1
    assert folders[0].get("title") == "Folders"
    assert folders[0].get("type") == NAV


def test_folders_index_requires_auth(client) -> None:
    assert client.get("/opds/folders").status_code == 401
    assert client.get("/opds/folders/1").status_code == 401
    assert client.get("/opds/x3/folders").status_code == 401
    assert client.get("/opds/x4/folders/1").status_code == 401


def test_folders_index_lists_registered_folders(client, tmp_path: Path) -> None:
    first = _register(tmp_path, "alpha")
    second = _register(tmp_path, "Beta")

    feed = _parse(_get(client, "/opds/folders"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — Folders"
    by_rel = _links_by_rel(_feed_links(feed))
    links = {
        link.get("title"): link.get("href")
        for link in by_rel[SUBSECTION_REL]
    }
    assert links == {
        "alpha": f"/opds/folders/{first}",
        "Beta": f"/opds/folders/{second}",
    }
    assert by_rel["self"][0].get("type") == NAV
    assert _entries(feed) == []


def test_folders_index_lists_folder_without_books(
    client, folder_id: int
) -> None:
    feed = _parse(_get(client, "/opds/folders"))

    assert _titles(feed) == []
    hrefs = [
        link.get("href")
        for link in _links_by_rel(_feed_links(feed))[SUBSECTION_REL]
    ]
    assert hrefs == [f"/opds/folders/{folder_id}"]


# --- folder levels ----------------------------------------------------------


def _nested_library(root: Path, folder_id: int) -> None:
    make_epub(root / "top.epub", title="Top Level")
    (root / "series").mkdir()
    make_epub(root / "series" / "first.epub", title="First")
    make_epub(root / "series" / "second.epub", title="Second")
    (root / "series" / "deeper").mkdir()
    make_epub(root / "series" / "deeper" / "hidden.epub", title="Hidden")
    _scan(folder_id)


def test_level_feed_lists_direct_books_and_subfolders(
    client, folder_id: int, root: Path
) -> None:
    _nested_library(root, folder_id)

    feed = _parse(_get(client, f"/opds/folders/{folder_id}"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — books"
    assert _titles(feed) == ["Top Level"]
    assert _subsection_titles(feed) == ["series"]


def test_level_feed_drills_down(client, folder_id: int, root: Path) -> None:
    _nested_library(root, folder_id)

    feed = _parse(_get(client, f"/opds/folders/{folder_id}?path=series"))

    assert _titles(feed) == ["First", "Second"]
    links = _links_by_rel(_feed_links(feed))
    assert _subsection_titles(feed) == ["deeper"]
    assert links[SUBSECTION_REL][0].get("href") == (
        f"/opds/folders/{folder_id}?path=series/deeper"
    )


def test_level_feed_leaf_has_no_subfolders(
    client, folder_id: int, root: Path
) -> None:
    _nested_library(root, folder_id)

    feed = _parse(_get(client, f"/opds/folders/{folder_id}?path=series/deeper"))

    assert _titles(feed) == ["Hidden"]
    assert SUBSECTION_REL not in _links_by_rel(_feed_links(feed))
    assert "next" not in _links_by_rel(_feed_links(feed))


def test_level_feed_does_not_leak_sibling_directories(
    client, folder_id: int, root: Path
) -> None:
    _nested_library(root, folder_id)
    _insert(folder_id, [{"title": "Elsewhere", "path": "other/file.epub"}])

    feed = _parse(_get(client, f"/opds/folders/{folder_id}?path=series"))

    assert _titles(feed) == ["First", "Second"]
    assert _subsection_titles(feed) == ["deeper"]


def test_subfolders_sort_case_insensitively(
    client, folder_id: int, root: Path
) -> None:
    _insert(
        folder_id,
        [
            {"title": "B1", "path": "Beta/a.epub"},
            {"title": "A1", "path": "alpha/b.epub"},
            {"title": "C1", "path": "gamma/c.epub"},
        ],
    )

    feed = _parse(_get(client, f"/opds/folders/{folder_id}"))

    assert _subsection_titles(feed) == ["alpha", "Beta", "gamma"]


def test_level_feed_paginates_books(client, folder_id: int) -> None:
    specs = [
        {"title": f"Book {index:03d}", "path": f"book{index:03d}.epub"}
        for index in range(51)
    ]
    _insert(folder_id, specs)

    first = _parse(_get(client, f"/opds/folders/{folder_id}"))
    assert len(_entries(first)) == 50
    by_rel = _links_by_rel(_feed_links(first))
    assert by_rel["next"][0].get("href") == f"/opds/folders/{folder_id}?page=2"

    second = _parse(_get(client, f"/opds/folders/{folder_id}?page=2"))
    assert _titles(second) == ["Book 050"]
    assert "next" not in _links_by_rel(_feed_links(second))


def test_level_feed_paginates_while_path_is_set(
    client, folder_id: int, root: Path
) -> None:
    specs = [
        {"title": f"Book {index:03d}", "path": f"series/book{index:03d}.epub"}
        for index in range(51)
    ]
    _insert(folder_id, specs)

    first = _parse(_get(client, f"/opds/folders/{folder_id}?path=series"))
    assert len(_entries(first)) == 50
    by_rel = _links_by_rel(_feed_links(first))
    assert by_rel["next"][0].get("href") == (
        f"/opds/folders/{folder_id}?path=series&page=2"
    )


def test_unknown_folder_returns_xml_404(client, folder_id: int) -> None:
    resp = _get(client, "/opds/folders/999999")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == "application/xml"
    assert b"<code>404</code>" in resp.data


def test_path_argument_never_reaches_the_filesystem(
    client, folder_id: int, root: Path
) -> None:
    _nested_library(root, folder_id)

    feed = _parse(_get(client, f"/opds/folders/{folder_id}?path=../../../etc"))

    assert _titles(feed) == []
    assert _subsection_titles(feed) == []


def test_path_argument_ignores_empty_segments(
    client, folder_id: int, root: Path
) -> None:
    _nested_library(root, folder_id)

    feed = _parse(_get(client, f"/opds/folders/{folder_id}?path=/series//"))

    assert _titles(feed) == ["First", "Second"]


def test_folders_are_isolated_between_registered_folders(
    client, tmp_path: Path
) -> None:
    first = _register(tmp_path, "one")
    second = _register(tmp_path, "two")
    _insert(first, [{"title": "Alpha", "path": "shared/a.epub"}])
    _insert(second, [{"title": "Beta", "path": "shared/b.epub"}])

    feed = _parse(_get(client, f"/opds/folders/{first}?path=shared"))

    assert _titles(feed) == ["Alpha"]
    assert SUBSECTION_REL not in _links_by_rel(_feed_links(feed))


# --- device profiles --------------------------------------------------------


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_catalog_links_to_folders(client, folder_id: int, profile) -> None:
    feed = _parse(_get(client, f"/opds/{profile}"))

    by_rel = _links_by_rel(_feed_links(feed))
    folders = [
        link
        for link in by_rel[SUBSECTION_REL]
        if link.get("href") == f"/opds/{profile}/folders"
    ]
    assert len(folders) == 1
    assert folders[0].get("title") == "Folders"


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_folders_index_lists_folders(
    client, tmp_path: Path, profile
) -> None:
    folder = _register(tmp_path, "alpha")

    feed = _parse(_get(client, f"/opds/{profile}/folders"))

    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — Folders"
    )
    by_rel = _links_by_rel(_feed_links(feed))
    assert by_rel[SUBSECTION_REL][0].get("href") == (
        f"/opds/{profile}/folders/{folder}"
    )


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_folder_level_uses_profile_downloads(
    client, folder_id: int, root: Path, profile
) -> None:
    make_epub(root / "series" / "dune.epub", title="Dune")
    make_pdf(root / "series" / "report.pdf", title="Report")
    _scan(folder_id)

    feed = _parse(
        _get(client, f"/opds/{profile}/folders/{folder_id}?path=series")
    )

    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — books / series"
    )
    assert _titles(feed) == ["Dune", "Report"]
    entries = {
        entry.findtext("a:title", namespaces=NS): entry
        for entry in _entries(feed)
    }
    epub_link = _links_by_rel(_entry_links(entries["Dune"]))[ACQUISITION_REL][0]
    assert epub_link.get("href") == (
        f"/opds/{profile}/download/{_book_id('Dune')}"
    )
    pdf_link = _links_by_rel(_entry_links(entries["Report"]))[ACQUISITION_REL][0]
    assert pdf_link.get("href") == f"/opds/download/{_book_id('Report')}"


def _book_id(title: str) -> int:
    with session_scope() as session:
        book = session.scalar(select(Book).where(Book.title == title))
        assert book is not None
        return book.id
