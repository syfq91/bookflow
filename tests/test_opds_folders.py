from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest
from sqlalchemy import select

from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import Book
from bookflow.library.scanner import scan_folder
from bookflow.library.service import add_folder
from factories import (
    entry_links,
    feed_entries,
    feed_links,
    feed_titles,
    insert_books,
    links_by_rel,
    make_epub,
    make_pdf,
    parse_feed,
)

PASSWORD = "folders-pass"
EXTENSIONS = (".epub", ".pdf")

ATOM = "http://www.w3.org/2005/Atom"
NS = {"a": ATOM}
NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
ACQUISITION_REL = "http://opds-spec.org/acquisition"
SUBSECTION_REL = "subsection"


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def opds_settings(settings: Settings) -> Settings:
    return replace(settings, admin_password=PASSWORD)


@pytest.fixture
def app(build_app, opds_settings: Settings):
    return build_app(opds_settings)


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


def _subsection_titles(feed: ElementTree.Element) -> list[str]:
    links = links_by_rel(feed_links(feed)).get(SUBSECTION_REL, [])
    return [link.get("title", "") for link in links]


def _book_titles(feed: ElementTree.Element) -> list[str]:
    """Titles of publication entries (subfolder entries excluded)."""
    return [
        entry.findtext("a:title", namespaces=NS)
        for entry in feed_entries(feed)
        if ACQUISITION_REL in links_by_rel(entry_links(entry))
    ]


def _nav_items(feed: ElementTree.Element) -> dict[str, str]:
    """Section title → href for the navigation entries of a feed."""
    items: dict[str, str] = {}
    for entry in feed_entries(feed):
        links = links_by_rel(entry_links(entry)).get(SUBSECTION_REL, [])
        if links:
            title = entry.findtext("a:title", namespaces=NS) or ""
            items[title] = links[0].get("href", "")
    return items


def _scan(folder_id: int):
    return scan_folder(folder_id, EXTENSIONS)


def _register(tmp_path: Path, name: str) -> int:
    path = tmp_path / name
    path.mkdir()
    result = add_folder(str(path))
    assert result.ok, result.error
    assert result.folder_id is not None
    return result.folder_id


# --- discovery --------------------------------------------------------------


def test_root_feed_lists_registered_folders(
    client, folder_id: int
) -> None:
    feed = parse_feed(_get(client, "/opds"))

    by_rel = links_by_rel(feed_links(feed))
    folders = [
        link
        for link in by_rel[SUBSECTION_REL]
        if link.get("href") == f"/opds/folders/{folder_id}"
    ]
    assert len(folders) == 1
    assert folders[0].get("title") == "books"
    assert folders[0].get("type") == ACQ
    assert _nav_items(feed)["books"] == f"/opds/folders/{folder_id}"


def test_folders_index_requires_auth(client) -> None:
    assert client.get("/opds/folders").status_code == 401
    assert client.get("/opds/folders/1").status_code == 401
    assert client.get("/opds/x3/folders").status_code == 401
    assert client.get("/opds/x4/folders/1").status_code == 401


def test_folders_index_lists_registered_folders(client, tmp_path: Path) -> None:
    first = _register(tmp_path, "alpha")
    second = _register(tmp_path, "Beta")

    feed = parse_feed(_get(client, "/opds/folders"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — Folders"
    by_rel = links_by_rel(feed_links(feed))
    links = {
        link.get("title"): link.get("href")
        for link in by_rel[SUBSECTION_REL]
    }
    assert links == {
        "alpha": f"/opds/folders/{first}",
        "Beta": f"/opds/folders/{second}",
    }
    assert by_rel["self"][0].get("type") == NAV
    assert _nav_items(feed) == {
        "alpha": f"/opds/folders/{first}",
        "Beta": f"/opds/folders/{second}",
    }


def test_folders_index_lists_folder_without_books(
    client, folder_id: int
) -> None:
    feed = parse_feed(_get(client, "/opds/folders"))

    assert _nav_items(feed) == {"books": f"/opds/folders/{folder_id}"}


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

    feed = parse_feed(_get(client, f"/opds/folders/{folder_id}"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — books"
    assert _book_titles(feed) == ["Top Level"]
    assert _subsection_titles(feed) == ["series"]
    assert _nav_items(feed) == {
        "series": f"/opds/folders/{folder_id}?path=series"
    }


def test_level_feed_drills_down(client, folder_id: int, root: Path) -> None:
    _nested_library(root, folder_id)

    feed = parse_feed(_get(client, f"/opds/folders/{folder_id}?path=series"))

    assert _book_titles(feed) == ["First", "Second"]
    links = links_by_rel(feed_links(feed))
    assert _subsection_titles(feed) == ["deeper"]
    assert links[SUBSECTION_REL][0].get("href") == (
        f"/opds/folders/{folder_id}?path=series/deeper"
    )
    assert _nav_items(feed) == {
        "deeper": f"/opds/folders/{folder_id}?path=series/deeper"
    }


def test_level_feed_leaf_has_no_subfolders(
    client, folder_id: int, root: Path
) -> None:
    _nested_library(root, folder_id)

    feed = parse_feed(_get(client, f"/opds/folders/{folder_id}?path=series/deeper"))

    assert feed_titles(feed) == ["Hidden"]
    assert SUBSECTION_REL not in links_by_rel(feed_links(feed))
    assert "next" not in links_by_rel(feed_links(feed))
    assert _nav_items(feed) == {}


def test_level_feed_does_not_leak_sibling_directories(
    client, folder_id: int, root: Path
) -> None:
    _nested_library(root, folder_id)
    insert_books(folder_id, [{"title": "Elsewhere", "path": "other/file.epub"}])

    feed = parse_feed(_get(client, f"/opds/folders/{folder_id}?path=series"))

    assert _book_titles(feed) == ["First", "Second"]
    assert _subsection_titles(feed) == ["deeper"]


def test_subfolders_sort_case_insensitively(
    client, folder_id: int, root: Path
) -> None:
    insert_books(
        folder_id,
        [
            {"title": "B1", "path": "Beta/a.epub"},
            {"title": "A1", "path": "alpha/b.epub"},
            {"title": "C1", "path": "gamma/c.epub"},
        ],
    )

    feed = parse_feed(_get(client, f"/opds/folders/{folder_id}"))

    assert _subsection_titles(feed) == ["alpha", "Beta", "gamma"]
    assert list(_nav_items(feed)) == ["alpha", "Beta", "gamma"]


def test_level_feed_paginates_books(client, folder_id: int) -> None:
    specs = [
        {"title": f"Book {index:03d}", "path": f"book{index:03d}.epub"}
        for index in range(51)
    ]
    insert_books(folder_id, specs)

    first = parse_feed(_get(client, f"/opds/folders/{folder_id}"))
    assert len(feed_entries(first)) == 50
    by_rel = links_by_rel(feed_links(first))
    assert by_rel["next"][0].get("href") == f"/opds/folders/{folder_id}?page=2"

    second = parse_feed(_get(client, f"/opds/folders/{folder_id}?page=2"))
    assert feed_titles(second) == ["Book 050"]
    assert "next" not in links_by_rel(feed_links(second))


def test_level_feed_paginates_while_path_is_set(
    client, folder_id: int, root: Path
) -> None:
    specs = [
        {"title": f"Book {index:03d}", "path": f"series/book{index:03d}.epub"}
        for index in range(51)
    ]
    insert_books(folder_id, specs)

    first = parse_feed(_get(client, f"/opds/folders/{folder_id}?path=series"))
    assert len(feed_entries(first)) == 50
    by_rel = links_by_rel(feed_links(first))
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

    feed = parse_feed(_get(client, f"/opds/folders/{folder_id}?path=../../../etc"))

    assert feed_titles(feed) == []
    assert _subsection_titles(feed) == []


def test_path_argument_ignores_empty_segments(
    client, folder_id: int, root: Path
) -> None:
    _nested_library(root, folder_id)

    feed = parse_feed(_get(client, f"/opds/folders/{folder_id}?path=/series//"))

    assert _book_titles(feed) == ["First", "Second"]


def test_folders_are_isolated_between_registered_folders(
    client, tmp_path: Path
) -> None:
    first = _register(tmp_path, "one")
    second = _register(tmp_path, "two")
    insert_books(first, [{"title": "Alpha", "path": "shared/a.epub"}])
    insert_books(second, [{"title": "Beta", "path": "shared/b.epub"}])

    feed = parse_feed(_get(client, f"/opds/folders/{first}?path=shared"))

    assert feed_titles(feed) == ["Alpha"]
    assert SUBSECTION_REL not in links_by_rel(feed_links(feed))


# --- device profiles --------------------------------------------------------


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_catalog_lists_registered_folders(
    client, folder_id: int, profile
) -> None:
    feed = parse_feed(_get(client, f"/opds/{profile}"))

    by_rel = links_by_rel(feed_links(feed))
    folders = [
        link
        for link in by_rel[SUBSECTION_REL]
        if link.get("href") == f"/opds/{profile}/folders/{folder_id}"
    ]
    assert len(folders) == 1
    assert folders[0].get("title") == "books"
    assert _nav_items(feed)["books"] == f"/opds/{profile}/folders/{folder_id}"


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_folders_index_lists_folders(
    client, tmp_path: Path, profile
) -> None:
    folder = _register(tmp_path, "alpha")

    feed = parse_feed(_get(client, f"/opds/{profile}/folders"))

    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — Folders"
    )
    by_rel = links_by_rel(feed_links(feed))
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

    feed = parse_feed(
        _get(client, f"/opds/{profile}/folders/{folder_id}?path=series")
    )

    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — books / series"
    )
    assert feed_titles(feed) == ["Dune", "Report"]
    entries = {
        entry.findtext("a:title", namespaces=NS): entry
        for entry in feed_entries(feed)
    }
    epub_link = links_by_rel(entry_links(entries["Dune"]))[ACQUISITION_REL][0]
    assert epub_link.get("href") == (
        f"/opds/{profile}/download/{_book_id('Dune')}"
    )
    pdf_link = links_by_rel(entry_links(entries["Report"]))[ACQUISITION_REL][0]
    assert pdf_link.get("href") == f"/opds/download/{_book_id('Report')}"


def _book_id(title: str) -> int:
    with session_scope() as session:
        book = session.scalar(select(Book).where(Book.title == title))
        assert book is not None
        return book.id
