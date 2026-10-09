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
    make_cbz,
    make_epub,
    make_pdf,
    parse_feed,
)

PASSWORD = "opds-pass"
EXTENSIONS = (".epub", ".pdf", ".cbz")

ATOM = "http://www.w3.org/2005/Atom"
NS = {"a": ATOM}
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQUISITION_REL = "http://opds-spec.org/acquisition"
PROGRESSION_REL = "http://opds-spec.org/progression"
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


def _headers() -> dict[str, str]:
    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _get(client, path: str):
    return client.get(path, headers=_headers())


def _section_titles(feed: ElementTree.Element) -> dict[str, str]:
    return {
        link.get("title", ""): link.get("href", "")
        for link in feed_links(feed)
        if link.get("rel") == SUBSECTION_REL
    }


def _entry_sections(feed: ElementTree.Element) -> dict[str, str]:
    """Section title → href taken from the feed's navigation entries."""
    sections: dict[str, str] = {}
    for entry in feed_entries(feed):
        links = links_by_rel(entry_links(entry)).get(SUBSECTION_REL, [])
        if links:
            title = entry.findtext("a:title", namespaces=NS) or ""
            sections[title] = links[0].get("href", "")
    return sections


def _scan(folder_id: int):
    return scan_folder(folder_id, EXTENSIONS)


def _book_id(title: str) -> int:
    with session_scope() as session:
        book = session.scalar(select(Book).where(Book.title == title))
        assert book is not None
        return book.id


def _insert_books(folder_id: int, count: int) -> None:
    insert_books(
        folder_id,
        [
            {"path": f"book{index:03d}.epub", "title": f"Book {index:03d}"}
            for index in range(count)
        ],
    )


def _mixed_library(root: Path, folder_id: int) -> None:
    make_epub(root / "dune.epub", title="Dune", authors=("Frank Herbert",))
    make_pdf(root / "report.pdf", title="Quarterly Report")
    _scan(folder_id)


# --- discovery --------------------------------------------------------------


def test_root_feed_links_device_catalogs(client, folder_id) -> None:
    feed = parse_feed(_get(client, "/opds"))

    hrefs = {
        link.get("href"): link
        for link in feed_links(feed)
        if link.get("rel") == SUBSECTION_REL
    }
    assert "/opdsx3" in hrefs
    assert "/opdsx4" in hrefs
    assert hrefs["/opdsx3"].get("type") == NAV
    assert hrefs["/opdsx3"].get("title") == "X3 Catalog"
    assert hrefs["/opdsx4"].get("title") == "X4 Catalog"


def test_device_catalogs_are_not_nested_under_opds(client) -> None:
    resp = _get(client, "/opds/x3")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == "application/xml"


# --- device feeds -----------------------------------------------------------


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_root_is_folder_view(client, folder_id, profile) -> None:
    original = parse_feed(_get(client, "/opds"))

    resp = _get(client, f"/opds{profile}")

    assert resp.headers["Content-Type"] == NAV
    feed = parse_feed(resp)
    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} Catalog"
    )
    assert _section_titles(feed) == {
        "books": f"/opds{profile}/folders/{folder_id}",
        "All Books": f"/opds{profile}/books",
        "Recent": f"/opds{profile}/recent",
        "Authors": f"/opds{profile}/authors",
    }
    assert _section_titles(original) == {
        "books": f"/opds/folders/{folder_id}",
        "All Books": "/opds/books",
        "Recent": "/opds/recent",
        "Authors": "/opds/authors",
        "X3 Catalog": "/opdsx3",
        "X4 Catalog": "/opdsx4",
    }
    for section in _section_titles(feed):
        assert section in _section_titles(original)
    for source in (original, feed):
        search = [
            link
            for link in feed_links(source)
            if link.get("rel") == "search"
        ]
        assert len(search) == 1
        assert "{searchTerms}" in search[0].get("href", "")
        assert _entry_sections(source) == _section_titles(source)


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_feed_lists_books_with_profile_links(
    client, folder_id, root, profile
) -> None:
    _mixed_library(root, folder_id)
    epub_id = _book_id("Dune")
    pdf_id = _book_id("Quarterly Report")

    resp = _get(client, f"/opds{profile}/books")

    assert resp.headers["Content-Type"] == ACQ
    feed = parse_feed(resp)
    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — All Books"
    )
    assert feed_titles(feed) == ["Dune", "Quarterly Report"]

    entries = {
        entry.findtext("a:title", namespaces=NS): entry
        for entry in feed_entries(feed)
    }
    epub_link = links_by_rel(entry_links(entries["Dune"]))[ACQUISITION_REL][0]
    assert epub_link.get("href") == f"/opds{profile}/download/{epub_id}"
    assert epub_link.get("type") == "application/epub+zip"

    pdf_link = links_by_rel(entry_links(entries["Quarterly Report"]))[
        ACQUISITION_REL
    ][0]
    assert pdf_link.get("href") == f"/opds/download/{pdf_id}"
    assert pdf_link.get("type") == "application/pdf"


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_feed_requires_auth(client, profile) -> None:
    for path in (
        f"/opds{profile}",
        f"/opds{profile}/books",
        f"/opds{profile}/recent",
        f"/opds{profile}/authors",
        f"/opds{profile}/authors/Sol%20Brothers",
        f"/opds{profile}/search?q=x",
        f"/opds{profile}/folders",
        f"/opds{profile}/folders/1",
    ):
        assert client.get(path).status_code == 401, path


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_feed_paginates(client, folder_id, profile) -> None:
    _insert_books(folder_id, 51)

    first = parse_feed(_get(client, f"/opds{profile}/books"))
    assert len(feed_entries(first)) == 50
    next_links = [
        link
        for link in feed_links(first)
        if link.get("rel") == "next"
    ]
    assert next_links[0].get("href") == f"/opds{profile}/books?page=2"

    second = parse_feed(_get(client, f"/opds{profile}/books?page=2"))
    assert feed_titles(second) == ["Book 050"]


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_recent_uses_profile_downloads(
    client, folder_id, root, profile
) -> None:
    _mixed_library(root, folder_id)
    epub_id = _book_id("Dune")

    feed = parse_feed(_get(client, f"/opds{profile}/recent"))

    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — Recent"
    )
    entries = {
        entry.findtext("a:title", namespaces=NS): entry
        for entry in feed_entries(feed)
    }
    assert set(entries) == {"Dune", "Quarterly Report"}
    acquisition = links_by_rel(entry_links(entries["Dune"]))[ACQUISITION_REL][0]
    assert acquisition.get("href") == f"/opds{profile}/download/{epub_id}"


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_authors_index_and_feed_use_profile_downloads(
    client, folder_id, root, profile
) -> None:
    _mixed_library(root, folder_id)
    epub_id = _book_id("Dune")

    index = parse_feed(_get(client, f"/opds{profile}/authors"))

    assert index.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — Authors"
    )
    assert feed_titles(index) == ["Frank Herbert"]
    author_link = links_by_rel(entry_links(feed_entries(index)[0]))[SUBSECTION_REL][0]
    assert author_link.get("href").startswith(f"/opds{profile}/authors/")

    feed = parse_feed(_get(client, author_link.get("href")))

    assert feed_titles(feed) == ["Dune"]
    acquisition = links_by_rel(entry_links(feed_entries(feed)[0]))[ACQUISITION_REL][0]
    assert acquisition.get("href") == f"/opds{profile}/download/{epub_id}"


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_search_uses_profile_downloads(
    client, folder_id, root, profile
) -> None:
    _mixed_library(root, folder_id)
    epub_id = _book_id("Dune")

    feed = parse_feed(_get(client, f"/opds{profile}/search?q=Dune"))

    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — Search: Dune"
    )
    assert feed_titles(feed) == ["Dune"]
    acquisition = links_by_rel(entry_links(feed_entries(feed)[0]))[ACQUISITION_REL][0]
    assert acquisition.get("href") == f"/opds{profile}/download/{epub_id}"


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_feeds_advertise_profile_search(client, folder_id, root, profile):
    _mixed_library(root, folder_id)

    for path in (f"/opds{profile}/books", f"/opds{profile}/search?q=Dune"):
        feed = parse_feed(_get(client, path))
        search = [
            link for link in feed_links(feed) if link.get("rel") == "search"
        ]
        assert len(search) == 1, path
        assert search[0].get("href", "").startswith(f"/opds{profile}/search"), path
        assert "{searchTerms}" in search[0].get("href", ""), path


# --- device book feeds ------------------------------------------------------


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_book_feed_links_to_optimized_download(
    client, folder_id, root, profile
) -> None:
    _mixed_library(root, folder_id)
    book_id = _book_id("Dune")

    feed = parse_feed(_get(client, f"/opds{profile}/books/{book_id}"))

    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — Dune"
    )
    entry = feed_entries(feed)[0]
    links = links_by_rel(entry_links(entry))
    acquisition = links[ACQUISITION_REL][0]
    assert acquisition.get("href") == f"/opds{profile}/download/{book_id}"
    assert acquisition.get("type") == "application/epub+zip"
    assert links["http://opds-spec.org/progression"][0].get("href") == (
        f"/opds/publications/{book_id}/progression"
    )
    assert "http://opds-spec.org/image" in links


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_book_feed_falls_back_to_original_for_pdf(
    client, folder_id, root, profile
) -> None:
    _mixed_library(root, folder_id)
    book_id = _book_id("Quarterly Report")

    feed = parse_feed(_get(client, f"/opds{profile}/books/{book_id}"))

    acquisition = links_by_rel(entry_links(feed_entries(feed)[0]))[ACQUISITION_REL][0]
    assert acquisition.get("href") == f"/opds/download/{book_id}"
    assert acquisition.get("type") == "application/pdf"


def test_device_book_feed_unknown_book_returns_xml_404(
    client, folder_id
) -> None:
    resp = _get(client, "/opdsx3/books/999999")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == "application/xml"


# --- acquisition follow-through ---------------------------------------------


def test_epub_acquisition_link_from_device_feed_resolves(
    client, folder_id, root
) -> None:
    _mixed_library(root, folder_id)
    feed = parse_feed(_get(client, "/opdsx3/books"))
    epub_entry = feed_entries(feed)[0]
    href = links_by_rel(entry_links(epub_entry))[ACQUISITION_REL][0].get("href")

    resp = _get(client, href)

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/epub+zip"


def test_pdf_acquisition_link_from_device_feed_resolves(
    client, folder_id, root
) -> None:
    _mixed_library(root, folder_id)
    feed = parse_feed(_get(client, "/opdsx4/books"))
    pdf_entry = feed_entries(feed)[1]
    href = links_by_rel(entry_links(pdf_entry))[ACQUISITION_REL][0].get("href")

    resp = _get(client, href)

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/pdf"


def test_cbz_acquisition_link_from_device_feed_resolves_and_rewrites(
    client, folder_id, root
) -> None:
    make_cbz(root / "manga.cbz")
    _scan(folder_id)
    with session_scope() as session:
        book = session.scalar(select(Book).where(Book.relative_path == "manga.cbz"))
        assert book is not None
        book_id = book.id

    # In regular catalog:
    regular_feed = parse_feed(_get(client, "/opds/books"))
    reg_entry = [
        e
        for e in feed_entries(regular_feed)
        if "manga" in (e.findtext("a:title", namespaces=NS) or "")
    ][0]
    reg_acq = links_by_rel(entry_links(reg_entry))["http://opds-spec.org/acquisition"][0]
    assert reg_acq.get("href") == f"/opds/download/{book_id}"
    assert reg_acq.get("type") == "application/vnd.comicbook+zip"

    # In x4 device catalog:
    x4_feed = parse_feed(_get(client, "/opdsx4/books"))
    x4_entry = [
        e
        for e in feed_entries(x4_feed)
        if "manga" in (e.findtext("a:title", namespaces=NS) or "")
    ][0]
    x4_acq = links_by_rel(entry_links(x4_entry))["http://opds-spec.org/acquisition"][0]
    assert x4_acq.get("href") == f"/opdsx4/download/{book_id}"
    assert x4_acq.get("type") == "application/x-xtc"

    # Follow the acquisition link
    resp = _get(client, x4_acq.get("href"))
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/x-xtc"
    assert resp.data[:4] == b"XTC\x00"

