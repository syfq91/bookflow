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
PASSWORD = "opds-pass"
EXTENSIONS = (".epub", ".pdf")

ATOM = "http://www.w3.org/2005/Atom"
NS = {"a": ATOM}
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQUISITION_REL = "http://opds-spec.org/acquisition"
PROGRESSION_REL = "http://opds-spec.org/progression"
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


def _headers() -> dict[str, str]:
    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
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


def _entry_links(entry: ElementTree.Element) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for link in entry.findall("a:link", NS):
        grouped.setdefault(link.get("rel", ""), []).append(link)
    return grouped


def _feed_links(feed: ElementTree.Element) -> list[ElementTree.Element]:
    return feed.findall("a:link", NS)


def _section_titles(feed: ElementTree.Element) -> dict[str, str]:
    return {
        link.get("title", ""): link.get("href", "")
        for link in _feed_links(feed)
        if link.get("rel") == SUBSECTION_REL
    }


def _entry_sections(feed: ElementTree.Element) -> dict[str, str]:
    """Section title → href taken from the feed's navigation entries."""
    sections: dict[str, str] = {}
    for entry in _entries(feed):
        links = _entry_links(entry).get(SUBSECTION_REL, [])
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
    with session_scope() as session:
        for index in range(count):
            session.add(
                Book(
                    folder_id=folder_id,
                    relative_path=f"book{index:03d}.epub",
                    title=f"Book {index:03d}",
                )
            )


def _mixed_library(root: Path, folder_id: int) -> None:
    make_epub(root / "dune.epub", title="Dune", authors=("Frank Herbert",))
    make_pdf(root / "report.pdf", title="Quarterly Report")
    _scan(folder_id)


# --- discovery --------------------------------------------------------------


def test_root_feed_links_device_catalogs(client, folder_id) -> None:
    feed = _parse(_get(client, "/opds"))

    hrefs = {
        link.get("href"): link
        for link in _feed_links(feed)
        if link.get("rel") == SUBSECTION_REL
    }
    assert "/opds/x3" in hrefs
    assert "/opds/x4" in hrefs
    assert hrefs["/opds/x3"].get("type") == NAV
    assert hrefs["/opds/x3"].get("title") == "X3 Catalog"
    assert hrefs["/opds/x4"].get("title") == "X4 Catalog"


# --- device feeds -----------------------------------------------------------


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_root_has_same_sections_as_original(client, profile) -> None:
    original = _parse(_get(client, "/opds"))

    resp = _get(client, f"/opds/{profile}")

    assert resp.headers["Content-Type"] == NAV
    feed = _parse(resp)
    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} Catalog"
    )
    assert _section_titles(feed) == {
        "All Books": f"/opds/{profile}/books",
        "Recent": f"/opds/{profile}/recent",
        "Authors": f"/opds/{profile}/authors",
        "Folders": f"/opds/{profile}/folders",
    }
    for section in _section_titles(feed):
        assert section in _section_titles(original)
    for source in (original, feed):
        search = [
            link
            for link in _feed_links(source)
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

    resp = _get(client, f"/opds/{profile}/books")

    assert resp.headers["Content-Type"] == ACQ
    feed = _parse(resp)
    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — All Books"
    )
    assert _titles(feed) == ["Dune", "Quarterly Report"]

    entries = {
        entry.findtext("a:title", namespaces=NS): entry
        for entry in _entries(feed)
    }
    epub_link = _entry_links(entries["Dune"])[ACQUISITION_REL][0]
    assert epub_link.get("href") == f"/opds/{profile}/download/{epub_id}"
    assert epub_link.get("type") == "application/epub+zip"

    pdf_link = _entry_links(entries["Quarterly Report"])[ACQUISITION_REL][0]
    assert pdf_link.get("href") == f"/opds/download/{pdf_id}"
    assert pdf_link.get("type") == "application/pdf"


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_feed_requires_auth(client, profile) -> None:
    for path in (
        f"/opds/{profile}",
        f"/opds/{profile}/books",
        f"/opds/{profile}/recent",
        f"/opds/{profile}/authors",
        f"/opds/{profile}/authors/Sol%20Brothers",
        f"/opds/{profile}/search?q=x",
        f"/opds/{profile}/folders",
        f"/opds/{profile}/folders/1",
    ):
        assert client.get(path).status_code == 401, path


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_feed_paginates(client, folder_id, profile) -> None:
    _insert_books(folder_id, 51)

    first = _parse(_get(client, f"/opds/{profile}/books"))
    assert len(_entries(first)) == 50
    next_links = [
        link
        for link in _feed_links(first)
        if link.get("rel") == "next"
    ]
    assert next_links[0].get("href") == f"/opds/{profile}/books?page=2"

    second = _parse(_get(client, f"/opds/{profile}/books?page=2"))
    assert _titles(second) == ["Book 050"]


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_recent_uses_profile_downloads(
    client, folder_id, root, profile
) -> None:
    _mixed_library(root, folder_id)
    epub_id = _book_id("Dune")

    feed = _parse(_get(client, f"/opds/{profile}/recent"))

    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — Recent"
    )
    entries = {
        entry.findtext("a:title", namespaces=NS): entry
        for entry in _entries(feed)
    }
    assert set(entries) == {"Dune", "Quarterly Report"}
    acquisition = _entry_links(entries["Dune"])[ACQUISITION_REL][0]
    assert acquisition.get("href") == f"/opds/{profile}/download/{epub_id}"


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_authors_index_and_feed_use_profile_downloads(
    client, folder_id, root, profile
) -> None:
    _mixed_library(root, folder_id)
    epub_id = _book_id("Dune")

    index = _parse(_get(client, f"/opds/{profile}/authors"))

    assert index.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — Authors"
    )
    assert _titles(index) == ["Frank Herbert"]
    author_link = _entry_links(_entries(index)[0])[SUBSECTION_REL][0]
    assert author_link.get("href").startswith(f"/opds/{profile}/authors/")

    feed = _parse(_get(client, author_link.get("href")))

    assert _titles(feed) == ["Dune"]
    acquisition = _entry_links(_entries(feed)[0])[ACQUISITION_REL][0]
    assert acquisition.get("href") == f"/opds/{profile}/download/{epub_id}"


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_search_uses_profile_downloads(
    client, folder_id, root, profile
) -> None:
    _mixed_library(root, folder_id)
    epub_id = _book_id("Dune")

    feed = _parse(_get(client, f"/opds/{profile}/search?q=Dune"))

    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — Search: Dune"
    )
    assert _titles(feed) == ["Dune"]
    acquisition = _entry_links(_entries(feed)[0])[ACQUISITION_REL][0]
    assert acquisition.get("href") == f"/opds/{profile}/download/{epub_id}"


# --- device book feeds ------------------------------------------------------


@pytest.mark.parametrize("profile", ["x3", "x4"])
def test_device_book_feed_links_to_optimized_download(
    client, folder_id, root, profile
) -> None:
    _mixed_library(root, folder_id)
    book_id = _book_id("Dune")

    feed = _parse(_get(client, f"/opds/{profile}/books/{book_id}"))

    assert feed.findtext("a:title", namespaces=NS) == (
        f"BookFlow — {profile.upper()} — Dune"
    )
    entry = _entries(feed)[0]
    links = _entry_links(entry)
    acquisition = links[ACQUISITION_REL][0]
    assert acquisition.get("href") == f"/opds/{profile}/download/{book_id}"
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

    feed = _parse(_get(client, f"/opds/{profile}/books/{book_id}"))

    acquisition = _entry_links(_entries(feed)[0])[ACQUISITION_REL][0]
    assert acquisition.get("href") == f"/opds/download/{book_id}"
    assert acquisition.get("type") == "application/pdf"


def test_device_book_feed_unknown_book_returns_xml_404(
    client, folder_id
) -> None:
    resp = _get(client, "/opds/x3/books/999999")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == "application/xml"


# --- acquisition follow-through ---------------------------------------------


def test_epub_acquisition_link_from_device_feed_resolves(
    client, folder_id, root
) -> None:
    _mixed_library(root, folder_id)
    feed = _parse(_get(client, "/opds/x3/books"))
    epub_entry = _entries(feed)[0]
    href = _entry_links(epub_entry)[ACQUISITION_REL][0].get("href")

    resp = _get(client, href)

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/epub+zip"


def test_pdf_acquisition_link_from_device_feed_resolves(
    client, folder_id, root
) -> None:
    _mixed_library(root, folder_id)
    feed = _parse(_get(client, "/opds/x4/books"))
    pdf_entry = _entries(feed)[1]
    href = _entry_links(pdf_entry)[ACQUISITION_REL][0].get("href")

    resp = _get(client, href)

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/pdf"
