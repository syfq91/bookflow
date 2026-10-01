from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import pytest
from alembic import command
from alembic.config import Config

from bookflow.app import create_app
from bookflow.config import Settings
from bookflow.database.database import reset_engine, session_scope
from bookflow.database.models import Book
from bookflow.library.scanner import scan_folder
from bookflow.library.service import add_folder
from factories import PNG_BYTES, make_epub, make_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "opds-pass"
EXTENSIONS = (".epub", ".pdf")

ATOM = "http://www.w3.org/2005/Atom"
NS = {"a": ATOM}
NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
ACQUISITION_REL = "http://opds-spec.org/acquisition"
IMAGE_REL = "http://opds-spec.org/image"
THUMB_REL = "http://opds-spec.org/image/thumbnail"
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


def _scan(folder_id: int):
    return scan_folder(folder_id, EXTENSIONS)


def _book_ids() -> list[int]:
    with session_scope() as session:
        return [book.id for book in session.query(Book).order_by(Book.id)]


def _insert(folder_id: int, specs: list[dict]) -> list[int]:
    """Insert book rows directly (no files needed for feed tests)."""
    ids: list[int] = []
    with session_scope() as session:
        for index, spec in enumerate(specs):
            fields = {
                "folder_id": folder_id,
                "relative_path": spec.get("path", f"book{index}.epub"),
                "title": spec.get("title"),
                "authors": spec.get("authors"),
                "publisher": spec.get("publisher"),
                "description": spec.get("description"),
                "series": spec.get("series"),
                "isbn": spec.get("isbn"),
            }
            if "created_at" in spec:
                fields["created_at"] = spec["created_at"]
            book = Book(**fields)
            session.add(book)
            session.flush()
            ids.append(book.id)
    return ids


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


def test_unconfigured_password_returns_503(settings: Settings) -> None:
    application = create_app(settings)
    application.config["TESTING"] = True

    resp = application.test_client().get("/opds")

    assert resp.status_code == 503
    assert resp.headers["Content-Type"].startswith("text/plain")


# --- discovery --------------------------------------------------------------


def test_root_feed_is_the_folder_view(client, folder_id: int) -> None:
    feed = _parse(_get(client, "/opds"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow"
    by_rel = _links_by_rel(_feed_links(feed))
    hrefs = {link.get("href") for link in _feed_links(feed)}
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
    for entry in _entries(feed):
        links = _links_by_rel(_entry_links(entry)).get(SUBSECTION_REL, [])
        title = entry.findtext("a:title", namespaces=NS) or ""
        sections[title] = (links[0].get("href", ""), links[0].get("type", ""))
    assert sections == {
        "books": (f"/opds/folders/{folder_id}", ACQ),
        "X3 Catalog": ("/opds/x3", NAV),
        "X4 Catalog": ("/opds/x4", NAV),
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

    feed = _parse(_get(client, "/opds/books"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — All Books"
    assert _titles(feed) == ["ancient", "Dune", "Neuromancer"]
    by_rel = _links_by_rel(_feed_links(feed))
    assert by_rel["self"][0].get("type") == ACQ
    assert "next" not in by_rel


def test_books_feed_empty(client, folder_id: int) -> None:
    feed = _parse(_get(client, "/opds/books"))

    assert _entries(feed) == []


def test_books_feed_paginates(client, folder_id: int) -> None:
    specs = [
        {"title": f"Book {index:03d}", "path": f"book{index:03d}.epub"}
        for index in range(51)
    ]
    _insert(folder_id, specs)

    first = _parse(_get(client, "/opds/books"))
    assert len(_entries(first)) == 50
    by_rel = _links_by_rel(_feed_links(first))
    assert by_rel["next"][0].get("href") == "/opds/books?page=2"

    second = _parse(_get(client, "/opds/books?page=2"))
    assert _titles(second) == ["Book 050"]
    assert "next" not in _links_by_rel(_feed_links(second))


def test_invalid_page_falls_back_to_first_page(
    client, folder_id: int
) -> None:
    _insert(folder_id, [{"title": "Solo"}])

    feed = _parse(_get(client, "/opds/books?page=bogus"))

    assert _titles(feed) == ["Solo"]


# --- recent -----------------------------------------------------------------


def test_recent_feed_orders_by_created_desc(client, folder_id: int) -> None:
    _insert(
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

    feed = _parse(_get(client, "/opds/recent"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — Recent"
    assert _titles(feed) == ["New", "Middle", "Old"]


# --- authors ----------------------------------------------------------------


def test_authors_feed_groups_and_counts(client, folder_id: int) -> None:
    _insert(
        folder_id,
        [
            {"title": "Dune", "authors": "Frank Herbert", "path": "a.epub"},
            {"title": "Messiah", "authors": "Frank Herbert", "path": "b.epub"},
            {"title": "Earthsea", "authors": "Ursula Le Guin", "path": "c.epub"},
            {"title": "Untitled", "authors": None, "path": "d.epub"},
        ],
    )

    feed = _parse(_get(client, "/opds/authors"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — Authors"
    assert _titles(feed) == ["Frank Herbert", "Ursula Le Guin"]
    summaries = [
        entry.findtext("a:summary", namespaces=NS) for entry in _entries(feed)
    ]
    assert summaries == ["2 book(s)", "1 book(s)"]
    assert all(
        link.get("type") == ACQ
        for entry in _entries(feed)
        for link in _links_by_rel(_entry_links(entry))["subsection"]
    )


def test_authors_feed_empty(client, folder_id: int) -> None:
    feed = _parse(_get(client, "/opds/authors"))

    assert _entries(feed) == []


def test_author_feed_lists_books(client, folder_id: int) -> None:
    _insert(
        folder_id,
        [
            {"title": "Dune", "authors": "Frank Herbert", "path": "a.epub"},
            {"title": "Messiah", "authors": "Frank Herbert", "path": "b.epub"},
            {"title": "Earthsea", "authors": "Ursula Le Guin", "path": "c.epub"},
        ],
    )

    feed = _parse(_get(client, f"/opds/authors/{quote('Frank Herbert')}"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — Frank Herbert"
    assert _titles(feed) == ["Dune", "Messiah"]


def test_author_feed_followed_from_authors_feed(
    client, folder_id: int
) -> None:
    _insert(folder_id, [{"title": "Dune", "authors": "Frank Herbert"}])

    authors = _parse(_get(client, "/opds/authors"))
    entry = _entries(authors)[0]
    href = _links_by_rel(_entry_links(entry))["subsection"][0].get("href")

    feed = _parse(_get(client, href))

    assert _titles(feed) == ["Dune"]


def test_author_feed_unknown_author_is_empty(client, folder_id: int) -> None:
    feed = _parse(_get(client, "/opds/authors/Nobody%20Here"))

    assert _entries(feed) == []


# --- search -----------------------------------------------------------------


def test_search_matches_title_authors_and_description(
    client, folder_id: int
) -> None:
    _insert(
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

    assert _titles(_parse(_get(client, "/opds/search?q=Dune"))) == ["Dune"]
    assert _titles(_parse(_get(client, "/opds/search?q=Shakespeare"))) == ["Hamlet"]
    assert _titles(_parse(_get(client, "/opds/search?q=revenge"))) == ["Hamlet"]
    assert _titles(_parse(_get(client, "/opds/search?q=clean"))) == ["Clean Code"]


def test_search_matches_isbn(client, folder_id: int) -> None:
    _insert(
        folder_id,
        [
            {"title": "Dune", "isbn": "9780441172719", "path": "a.epub"},
            {"title": "Other", "isbn": "9781234567890", "path": "b.epub"},
        ],
    )

    feed = _parse(_get(client, "/opds/search?q=0441172719"))

    assert _titles(feed) == ["Dune"]


def test_search_is_case_insensitive(client, folder_id: int) -> None:
    _insert(folder_id, [{"title": "Dune", "path": "a.epub"}])

    feed = _parse(_get(client, "/opds/search?q=DUNE"))

    assert _titles(feed) == ["Dune"]


def test_search_no_match_returns_empty_feed(client, folder_id: int) -> None:
    _insert(folder_id, [{"title": "Dune", "path": "a.epub"}])

    feed = _parse(_get(client, "/opds/search?q=zzznomatch"))

    assert _entries(feed) == []
    assert "next" not in _links_by_rel(_feed_links(feed))


def test_search_empty_query_returns_empty_feed(client, folder_id: int) -> None:
    _insert(folder_id, [{"title": "Dune", "path": "a.epub"}])

    feed = _parse(_get(client, "/opds/search"))

    assert _entries(feed) == []


def test_search_escapes_like_wildcards(client, folder_id: int) -> None:
    _insert(
        folder_id,
        [
            {"title": "100% Real", "path": "a.epub"},
            {"title": "100 Real", "path": "b.epub"},
        ],
    )

    feed = _parse(_get(client, "/opds/search?q=100%25"))

    assert _titles(feed) == ["100% Real"]


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

    feed = _parse(_get(client, f"/opds/books/{book_id}"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — Dune"
    entry = _entries(feed)[0]
    assert entry.findtext("a:id", namespaces=NS).startswith("tag:bookflow,")
    assert entry.findtext("a:author/a:name", namespaces=NS) == "Frank Herbert"
    by_rel = _links_by_rel(_entry_links(entry))
    acquisition = by_rel[ACQUISITION_REL][0]
    assert acquisition.get("type") == "application/epub+zip"
    assert acquisition.get("href") == f"/opds/download/{book_id}"
    assert by_rel[THUMB_REL][0].get("href") == f"/opds/cover/{book_id}"
    assert by_rel[IMAGE_REL][0].get("href") == f"/opds/cover/{book_id}"


def test_book_feed_without_title_uses_filename(client, folder_id: int) -> None:
    _insert(folder_id, [{"title": None, "path": "mystery.epub"}])
    book_id = _book_ids()[0]

    feed = _parse(_get(client, f"/opds/books/{book_id}"))

    assert feed.findtext("a:title", namespaces=NS) == "BookFlow — mystery.epub"
    assert _titles(feed) == ["mystery.epub"]


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
    _insert(folder_id, [{"title": "Escape", "path": "../outside.epub"}])
    book_id = _book_ids()[0]

    resp = _get(client, f"/opds/download/{book_id}")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == "application/xml"


def test_download_missing_file_returns_404(client, folder_id: int) -> None:
    _insert(folder_id, [{"title": "Gone", "path": "gone.epub"}])
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
