"""Shared test helpers: sample ebook files, index seeds, login, feed parsing."""

from __future__ import annotations

import base64
import mimetypes
import secrets
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from alembic.config import Config
from flask import Response
from pypdf import PdfWriter

from bookflow.database.database import session_scope
from bookflow.database.models import Book

REPO_ROOT = Path(__file__).resolve().parent.parent

ATOM = "http://www.w3.org/2005/Atom"
NS = {"a": ATOM}

# 1x1 transparent PNG
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def make_epub(
    path: Path,
    *,
    title: str,
    authors: tuple[str, ...] = (),
    publisher: str | None = None,
    language: str | None = None,
    isbn: str | None = None,
    description: str | None = None,
    series: str | None = None,
    series_index: float | None = None,
    cover: tuple[str, bytes] | None = None,
) -> Path:
    """Write a minimal EPUB carrying the given metadata."""
    entries = [
        '<dc:identifier id="uid">urn:uuid:bookflow-test</dc:identifier>',
        f"<dc:title>{title}</dc:title>",
    ]
    entries += [f"<dc:creator>{author}</dc:creator>" for author in authors]
    if publisher:
        entries.append(f"<dc:publisher>{publisher}</dc:publisher>")
    if language:
        entries.append(f"<dc:language>{language}</dc:language>")
    if isbn:
        entries.append(f'<dc:identifier opf:scheme="ISBN">{isbn}</dc:identifier>')
    if description:
        entries.append(f"<dc:description>{description}</dc:description>")
    if series:
        entries.append(f'<meta name="calibre:series" content="{series}"/>')
    if series_index is not None:
        entries.append(
            f'<meta name="calibre:series_index" content="{series_index}"/>'
        )

    cover_item = ""
    if cover:
        cover_name, _cover_bytes = cover
        entries.append('<meta name="cover" content="cover-image"/>')
        cover_media_type = mimetypes.guess_type(cover_name)[0] or "image/jpeg"
        cover_item = (
            f'<item id="cover-image" href="{cover_name}" '
            f'media-type="{cover_media_type}"/>'
        )

    metadata = "\n    ".join(entries)
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf"
         xmlns:opf="http://www.idpf.org/2007/opf"
         version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    {metadata}
  </metadata>
  <manifest>
    <item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    {cover_item}
  </manifest>
  <spine>
    <itemref idref="c1"/>
  </spine>
</package>
"""
    chapter = (
        "<html xmlns='http://www.w3.org/1999/xhtml'>"
        "<body><p>text</p></body></html>"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/chapter.xhtml", chapter)
        if cover:
            archive.writestr(f"OEBPS/{cover[0]}", cover[1])
    return path


def make_pdf(path: Path, *, title: str | None = None, author: str | None = None):
    """Write a one-page PDF carrying the given document information."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    info: dict[str, str] = {}
    if title:
        info["/Title"] = title
    if author:
        info["/Author"] = author
    if info:
        writer.add_metadata(info)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def csrf_token(client) -> str:
    """Return the test client's session CSRF token, minting one if needed."""
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            sess["csrf_token"] = token
        return token


def alembic_config(database_url: str) -> Config:
    """Alembic config pointed at the test database."""
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def login_admin(
    client,
    *,
    password: str,
    username: str = "admin",
    next_target: str = "",
) -> Response:
    """POST /admin/login with a valid CSRF token; return the response."""
    return client.post(
        "/admin/login",
        data={
            "csrf_token": csrf_token(client),
            "username": username,
            "password": password,
            "next": next_target,
        },
    )


def create_user(
    *,
    username: str,
    password: str,
    is_admin: bool = False,
) -> int:
    """Insert a user row and return their id."""
    from argon2 import PasswordHasher

    from bookflow.database.models import User

    hasher = PasswordHasher()
    with session_scope() as session:
        user = User(
            username=username,
            password_hash=hasher.hash(password),
            is_admin=is_admin,
        )
        session.add(user)
        session.flush()
        return user.id


def basic_auth_headers(username: str, password: str) -> dict[str, str]:
    """Build HTTP Basic Authorization header dictionary."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def insert_books(folder_id: int, specs: list[dict]) -> list[int]:
    """Insert book rows directly (no files needed) and return their ids."""
    ids: list[int] = []
    with session_scope() as session:
        for index, spec in enumerate(specs):
            fields = {
                "folder_id": folder_id,
                "relative_path": spec.get("path", f"book{index}.epub"),
                "title": spec.get("title"),
                "authors": spec.get("authors"),
                "publisher": spec.get("publisher"),
                "isbn": spec.get("isbn"),
                "description": spec.get("description"),
                "series": spec.get("series"),
                "file_format": spec.get("file_format"),
                "file_size": spec.get("file_size"),
            }
            if "created_at" in spec:
                fields["created_at"] = spec["created_at"]
            book = Book(**fields)
            session.add(book)
            session.flush()
            ids.append(book.id)
    return ids


# --- feed parsing -----------------------------------------------------------


def parse_feed(resp) -> ElementTree.Element:
    """Parse an Atom response body, asserting the request succeeded."""
    assert resp.status_code == 200
    return ElementTree.fromstring(resp.data)


def feed_entries(feed: ElementTree.Element) -> list[ElementTree.Element]:
    return feed.findall("a:entry", NS)


def feed_titles(feed: ElementTree.Element) -> list[str]:
    return [
        entry.findtext("a:title", namespaces=NS) for entry in feed_entries(feed)
    ]


def feed_links(feed: ElementTree.Element) -> list[ElementTree.Element]:
    return feed.findall("a:link", NS)


def entry_links(entry: ElementTree.Element) -> list[ElementTree.Element]:
    return entry.findall("a:link", NS)


def links_by_rel(links) -> dict[str, list[ElementTree.Element]]:
    grouped: dict[str, list[ElementTree.Element]] = {}
    for link in links:
        grouped.setdefault(link.get("rel", ""), []).append(link)
    return grouped
