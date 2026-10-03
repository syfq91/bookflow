from __future__ import annotations

import base64
import zipfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import pytest
from sqlalchemy import select

from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import Book
from bookflow.library.scanner import scan_folder
from bookflow.library.service import add_folder
from bookflow.opds import routes as opds_routes
from factories import (
    CONTAINER_XML,
    PNG_BYTES,
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

PASSWORD = "opds-pass"
EXTENSIONS = (".epub", ".pdf")

ATOM = "http://www.w3.org/2005/Atom"
NS = {"a": ATOM}
NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
ACQUISITION_REL = "http://opds-spec.org/acquisition"
IMAGE_REL = "http://opds-spec.org/image"
THUMB_REL = "http://opds-spec.org/image/thumbnail"
AUTH_DOCUMENT_TYPE = "application/opds-authentication+json"
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


def _get(client, path: str, headers: dict[str, str] | None = None):
    h = _headers()
    if headers:
        h.update(headers)
    return client.get(path, headers=h)


def _scan(folder_id: int):
    return scan_folder(folder_id, EXTENSIONS)


def _book_ids() -> list[int]:
    with session_scope() as session:
        return [book.id for book in session.scalars(select(Book).order_by(Book.id))]


# --- authentication ---------------------------------------------------------


def test_missing_credentials_return_401(client) -> None:
    resp = client.get("/opds")

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == 'Basic realm="BookFlow"'


def test_wrong_password_returns_401(client) -> None:
    resp = client.get("/opds", headers=_headers(password="wrong"))

    assert resp.status_code == 401


def test_wrong_username_returns_401(client) -> None:
    resp = client.get("/opds", headers=_headers(username="root"))

    assert resp.status_code == 401


def test_valid_credentials_return_catalog(client) -> None:
    resp = _get(client, "/opds")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith(NAV)


def test_every_opds_endpoint_requires_auth(client) -> None:
    for path in ("/opds", "/opds/books", "/opds/recent", "/opds/search?q=x"):
        assert client.get(path).status_code == 401, path


def test_unconfigured_password_returns_503(settings: Settings, build_app) -> None:
    application = build_app(settings, migrate=False)

    resp = application.test_client().get("/opds")

    assert resp.status_code == 503
    assert resp.headers["Content-Type"].startswith("text/plain")


# --- discovery --------------------------------------------------------------


def test_root_feed_is_the_folder_view(client, folder_id: int) -> None:
    feed = parse_feed(_get(client, "/opds"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow"
    by_rel = links_by_rel(feed_links(feed))
    hrefs = {link.get("href") for link in feed_links(feed)}
    assert "/opds/books" not in hrefs
    assert "/opds/recent" not in hrefs
    assert "/opds/authors" not in hrefs
    assert "/opds/folders" not in hrefs
    assert "subsection" in by_rel
    search = by_rel["search"]
    assert len(search) == 1
    assert "{searchTerms}" in search[0].get("href", "")
    self_link = by_rel["self"]
    assert self_link[0].get("type") == NAV
    # Clients such as KOReader browse the root from entries alone, so the
    # registered folders must be entries and not just feed-level links.
    sections: dict[str, tuple[str, str]] = {}
    for entry in feed_entries(feed):
        links = links_by_rel(entry_links(entry)).get(SUBSECTION_REL, [])
        title = entry.findtext("a:title", namespaces=NS) or ""
        sections[title] = (links[0].get("href", ""), links[0].get("type", ""))
    assert sections == {
        "books": (f"/opds/folders/{folder_id}", ACQ),
        "X3 Catalog": ("/opdsx3", NAV),
        "X4 Catalog": ("/opdsx4", NAV),
    }


def test_root_feed_bare_path_works(client) -> None:
    resp = client.get("/opds", headers=_headers())

    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith(NAV)


# --- all books --------------------------------------------------------------


def test_books_feed_sorted_case_insensitively(
    client, folder_id: int, root: Path
) -> None:
    for filename, title in (
        ("c.epub", "Neuromancer"),
        ("a.epub", "ancient"),
        ("b.epub", "Dune"),
    ):
        make_epub(root / filename, title=title)
    _scan(folder_id)

    feed = parse_feed(_get(client, "/opds/books"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — All Books"
    assert feed_titles(feed) == ["ancient", "Dune", "Neuromancer"]
    by_rel = links_by_rel(feed_links(feed))
    assert by_rel["self"][0].get("type") == ACQ
    assert "next" not in by_rel


def test_books_feed_empty(client, folder_id: int) -> None:
    feed = parse_feed(_get(client, "/opds/books"))

    assert feed_entries(feed) == []


def test_books_feed_paginates(client, folder_id: int) -> None:
    specs = [
        {"title": f"Book {index:03d}", "path": f"book{index:03d}.epub"}
        for index in range(51)
    ]
    insert_books(folder_id, specs)

    first = parse_feed(_get(client, "/opds/books"))
    assert len(feed_entries(first)) == 50
    by_rel = links_by_rel(feed_links(first))
    assert by_rel["next"][0].get("href") == "/opds/books?page=2"

    second = parse_feed(_get(client, "/opds/books?page=2"))
    assert feed_titles(second) == ["Book 050"]
    assert "next" not in links_by_rel(feed_links(second))


def test_invalid_page_falls_back_to_first_page(
    client, folder_id: int
) -> None:
    insert_books(folder_id, [{"title": "Solo"}])

    feed = parse_feed(_get(client, "/opds/books?page=bogus"))

    assert feed_titles(feed) == ["Solo"]


# --- recent -----------------------------------------------------------------


def test_recent_feed_orders_by_created_desc(client, folder_id: int) -> None:
    insert_books(
        folder_id,
        [
            {"title": "Old", "path": "old.epub", "created_at": datetime(2024, 1, 1)},
            {"title": "New", "path": "new.epub", "created_at": datetime(2024, 6, 1)},
            {
                "title": "Middle",
                "path": "mid.epub",
                "created_at": datetime(2024, 3, 1),
            },
        ],
    )

    feed = parse_feed(_get(client, "/opds/recent"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — Recent"
    assert feed_titles(feed) == ["New", "Middle", "Old"]


# --- authors ----------------------------------------------------------------


def test_authors_feed_groups_and_counts(client, folder_id: int) -> None:
    insert_books(
        folder_id,
        [
            {"title": "Dune", "authors": "Frank Herbert", "path": "a.epub"},
            {"title": "Messiah", "authors": "Frank Herbert", "path": "b.epub"},
            {"title": "Earthsea", "authors": "Ursula Le Guin", "path": "c.epub"},
            {"title": "Untitled", "authors": None, "path": "d.epub"},
        ],
    )

    feed = parse_feed(_get(client, "/opds/authors"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — Authors"
    assert feed_titles(feed) == ["Frank Herbert", "Ursula Le Guin"]
    summaries = [
        entry.findtext("a:summary", namespaces=NS) for entry in feed_entries(feed)
    ]
    assert summaries == ["2 book(s)", "1 book(s)"]
    assert all(
        link.get("type") == ACQ
        for entry in feed_entries(feed)
        for link in links_by_rel(entry_links(entry))["subsection"]
    )


def test_authors_feed_empty(client, folder_id: int) -> None:
    feed = parse_feed(_get(client, "/opds/authors"))

    assert feed_entries(feed) == []


def test_author_feed_lists_books(client, folder_id: int) -> None:
    insert_books(
        folder_id,
        [
            {"title": "Dune", "authors": "Frank Herbert", "path": "a.epub"},
            {"title": "Messiah", "authors": "Frank Herbert", "path": "b.epub"},
            {"title": "Earthsea", "authors": "Ursula Le Guin", "path": "c.epub"},
        ],
    )

    feed = parse_feed(_get(client, f"/opds/authors/{quote('Frank Herbert')}"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — Frank Herbert"
    assert feed_titles(feed) == ["Dune", "Messiah"]


def test_author_feed_followed_from_authors_feed(
    client, folder_id: int
) -> None:
    insert_books(folder_id, [{"title": "Dune", "authors": "Frank Herbert"}])

    authors = parse_feed(_get(client, "/opds/authors"))
    entry = feed_entries(authors)[0]
    href = links_by_rel(entry_links(entry))["subsection"][0].get("href")

    feed = parse_feed(_get(client, href))

    assert feed_titles(feed) == ["Dune"]


def test_author_feed_unknown_author_is_empty(client, folder_id: int) -> None:
    feed = parse_feed(_get(client, "/opds/authors/Nobody%20Here"))

    assert feed_entries(feed) == []


# --- search -----------------------------------------------------------------


def test_search_matches_title_authors_and_description(
    client, folder_id: int
) -> None:
    insert_books(
        folder_id,
        [
            {"title": "Dune", "authors": "Frank Herbert", "path": "a.epub"},
            {
                "title": "Hamlet",
                "authors": "William Shakespeare",
                "path": "b.epub",
                "description": "A tale of revenge in Denmark",
            },
            {"title": "Clean Code", "authors": "Robert Martin", "path": "c.epub"},
        ],
    )

    assert feed_titles(parse_feed(_get(client, "/opds/search?q=Dune"))) == ["Dune"]
    assert feed_titles(parse_feed(_get(client, "/opds/search?q=Shakespeare"))) == [
        "Hamlet"
    ]
    assert feed_titles(parse_feed(_get(client, "/opds/search?q=revenge"))) == [
        "Hamlet"
    ]
    assert feed_titles(parse_feed(_get(client, "/opds/search?q=clean"))) == [
        "Clean Code"
    ]


def test_search_matches_isbn(client, folder_id: int) -> None:
    insert_books(
        folder_id,
        [
            {"title": "Dune", "isbn": "9780441172719", "path": "a.epub"},
            {"title": "Other", "isbn": "9781234567890", "path": "b.epub"},
        ],
    )

    feed = parse_feed(_get(client, "/opds/search?q=0441172719"))

    assert feed_titles(feed) == ["Dune"]


def test_search_is_case_insensitive(client, folder_id: int) -> None:
    insert_books(folder_id, [{"title": "Dune", "path": "a.epub"}])

    feed = parse_feed(_get(client, "/opds/search?q=DUNE"))

    assert feed_titles(feed) == ["Dune"]


def test_search_no_match_returns_empty_feed(client, folder_id: int) -> None:
    insert_books(folder_id, [{"title": "Dune", "path": "a.epub"}])

    feed = parse_feed(_get(client, "/opds/search?q=zzznomatch"))

    assert feed_entries(feed) == []
    assert "next" not in links_by_rel(feed_links(feed))


def test_search_empty_query_returns_empty_feed(client, folder_id: int) -> None:
    insert_books(folder_id, [{"title": "Dune", "path": "a.epub"}])

    feed = parse_feed(_get(client, "/opds/search"))

    assert feed_entries(feed) == []


def test_search_escapes_like_wildcards(client, folder_id: int) -> None:
    insert_books(
        folder_id,
        [
            {"title": "100% Real", "path": "a.epub"},
            {"title": "100 Real", "path": "b.epub"},
        ],
    )

    feed = parse_feed(_get(client, "/opds/search?q=100%25"))

    assert feed_titles(feed) == ["100% Real"]


def test_search_response_is_acquisition_feed(client, folder_id: int) -> None:
    resp = _get(client, "/opds/search?q=x")

    assert resp.headers["Content-Type"].startswith(ACQ)


# --- single book ------------------------------------------------------------


def test_book_feed_includes_acquisition_and_cover_links(
    client, folder_id: int, root: Path
) -> None:
    make_epub(root / "dune.epub", title="Dune", authors=("Frank Herbert",))
    _scan(folder_id)
    book_id = _book_ids()[0]

    feed = parse_feed(_get(client, f"/opds/books/{book_id}"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — Dune"
    entry = feed_entries(feed)[0]
    assert entry.findtext("a:id", namespaces=NS).startswith("tag:bookflow,")
    assert entry.findtext("a:author/a:name", namespaces=NS) == "Frank Herbert"
    by_rel = links_by_rel(entry_links(entry))
    acquisition = by_rel[ACQUISITION_REL][0]
    assert acquisition.get("type") == "application/epub+zip"
    assert acquisition.get("href") == f"/opds/download/{book_id}"
    assert by_rel[THUMB_REL][0].get("href") == f"/opds/cover/{book_id}"
    assert by_rel[IMAGE_REL][0].get("href") == f"/opds/cover/{book_id}"


def test_book_feed_without_title_uses_filename(client, folder_id: int) -> None:
    insert_books(folder_id, [{"title": None, "path": "mystery.epub"}])
    book_id = _book_ids()[0]

    feed = parse_feed(_get(client, f"/opds/books/{book_id}"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — mystery.epub"
    assert feed_titles(feed) == ["mystery.epub"]


def test_unknown_book_returns_xml_404(client, folder_id: int) -> None:
    resp = _get(client, "/opds/books/999999")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == "application/xml"
    root = ElementTree.fromstring(resp.data)
    assert root.findtext("code") == "404"


# --- download ---------------------------------------------------------------


def test_download_returns_file_bytes(client, folder_id: int, root: Path) -> None:
    make_epub(root / "dune.epub", title="Dune")
    _scan(folder_id)
    book_id = _book_ids()[0]

    resp = _get(client, f"/opds/download/{book_id}")

    assert resp.status_code == 200
    assert resp.data == (root / "dune.epub").read_bytes()
    assert resp.headers["Content-Type"] == "application/epub+zip"
    assert resp.headers["Content-Disposition"].startswith("attachment")


def test_download_pdf_mime_type(client, folder_id: int, root: Path) -> None:
    make_pdf(root / "report.pdf", title="Report")
    _scan(folder_id)
    book_id = _book_ids()[0]

    resp = _get(client, f"/opds/download/{book_id}")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/pdf"


def test_download_rejects_path_traversal(
    client, folder_id: int, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.epub"
    make_epub(outside, title="Escape")
    insert_books(folder_id, [{"title": "Escape", "path": "../outside.epub"}])
    book_id = _book_ids()[0]

    resp = _get(client, f"/opds/download/{book_id}")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == "application/xml"


def test_download_missing_file_returns_404(client, folder_id: int) -> None:
    insert_books(folder_id, [{"title": "Gone", "path": "gone.epub"}])
    book_id = _book_ids()[0]

    resp = _get(client, f"/opds/download/{book_id}")

    assert resp.status_code == 404


def test_download_requires_auth(client, folder_id: int, root: Path) -> None:
    make_epub(root / "dune.epub", title="Dune")
    _scan(folder_id)
    book_id = _book_ids()[0]

    assert client.get(f"/opds/download/{book_id}").status_code == 401


# --- covers -----------------------------------------------------------------


def test_cover_serves_extracted_image(client, folder_id: int, root: Path) -> None:
    make_epub(root / "c.epub", title="Covered", cover=("cover.png", PNG_BYTES))
    _scan(folder_id)
    book_id = _book_ids()[0]

    resp = _get(client, f"/opds/cover/{book_id}")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "image/png"
    assert resp.data == PNG_BYTES
    etag = resp.headers.get("ETag")
    assert etag is not None

    reval = _get(client, f"/opds/cover/{book_id}", headers={"If-None-Match": etag})
    assert reval.status_code == 304


def test_pdf_entry_omits_cover_links(client, folder_id: int, root: Path) -> None:
    make_pdf(root / "report.pdf", title="Report")
    _scan(folder_id)
    book_id = _book_ids()[0]

    feed = parse_feed(_get(client, f"/opds/books/{book_id}"))
    entry = feed_entries(feed)[0]
    by_rel = links_by_rel(entry_links(entry))

    assert THUMB_REL not in by_rel
    assert IMAGE_REL not in by_rel


def test_cover_with_percent_encoded_href(client, folder_id: int, root: Path) -> None:
    path = root / "encoded.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Encoded</dc:title>
    <meta name="cover" content="cover-image"/>
  </metadata>
  <manifest>
    <item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover-image" href="cover%20image.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/chapter.xhtml", "<html><body><p>Hi</p></body></html>")
        archive.writestr("OEBPS/cover image.png", PNG_BYTES)

    _scan(folder_id)
    book_id = _book_ids()[-1]
    resp = _get(client, f"/opds/cover/{book_id}")
    assert resp.status_code == 200
    assert resp.data == PNG_BYTES


def test_cover_without_cover_returns_404(client, folder_id: int, root: Path) -> None:
    make_epub(root / "plain.epub", title="Plain")
    _scan(folder_id)
    book_id = _book_ids()[0]

    resp = _get(client, f"/opds/cover/{book_id}")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == "application/xml"


def test_cover_for_pdf_returns_404(client, folder_id: int, root: Path) -> None:
    make_pdf(root / "report.pdf", title="Report")
    _scan(folder_id)
    book_id = _book_ids()[0]

    resp = _get(client, f"/opds/cover/{book_id}")

    assert resp.status_code == 404


def test_cover_requires_auth(client, folder_id: int, root: Path) -> None:
    make_epub(root / "c.epub", title="Covered", cover=("cover.png", PNG_BYTES))
    _scan(folder_id)
    book_id = _book_ids()[0]

    assert client.get(f"/opds/cover/{book_id}").status_code == 401


# --- errors -----------------------------------------------------------------


def test_unknown_opds_path_returns_xml_404(client, folder_id: int) -> None:
    resp = _get(client, "/opds/no/such/feed")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == "application/xml"
    root = ElementTree.fromstring(resp.data)
    assert root.findtext("message")


def test_wrong_method_returns_xml_405(client, folder_id: int) -> None:
    resp = client.post("/opds/books", headers=_headers())

    assert resp.status_code == 405
    assert resp.headers["Content-Type"] == "application/xml"
    root = ElementTree.fromstring(resp.data)
    assert root.findtext("code") == "405"


def test_wrong_method_without_credentials_returns_401(client) -> None:
    """A routing failure authenticates: no blueprint hook runs for it."""
    resp = client.post("/opds/books")

    assert resp.status_code == 401
    assert resp.headers["Content-Type"].startswith(AUTH_DOCUMENT_TYPE)
    assert resp.headers["WWW-Authenticate"] == 'Basic realm="BookFlow"'


def test_authentication_document_stays_public(client) -> None:
    """A wrong method on the Authentication Document is still a 405."""
    resp = client.post("/opds/authentication")

    assert resp.status_code == 405
    assert resp.headers["Content-Type"] == "application/xml"


def test_unhandled_error_returns_xml_500(client, monkeypatch) -> None:
    client.application.config["PROPAGATE_EXCEPTIONS"] = False

    def explode(*args, **kwargs):
        raise RuntimeError("feed exploded")

    monkeypatch.setattr(opds_routes, "acquisition_feed", explode)
    resp = _get(client, "/opds/books")

    assert resp.status_code == 500
    assert resp.headers["Content-Type"] == "application/xml"
    root = ElementTree.fromstring(resp.data)
    assert root.findtext("code") == "500"
