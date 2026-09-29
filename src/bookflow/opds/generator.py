"""Atom/OPDS catalog generation.

Feeds are built with the standard library XML toolkit. Acquisition links
always point at the BookFlow download endpoint; cover links point at the
on-demand cover endpoint.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from flask import url_for

from bookflow.database.models import Book

ATOM_NS = "http://www.w3.org/2005/Atom"

NAVIGATION_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQUISITION_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"

ACQUISITION_REL = "http://opds-spec.org/acquisition"
IMAGE_REL = "http://opds-spec.org/image"
THUMBNAIL_REL = "http://opds-spec.org/image/thumbnail"
SUBSECTION_REL = "subsection"
SEARCH_REL = "search"

BOOK_MIME_TYPES = {
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
    ".cbz": "application/vnd.comicbook+zip",
    ".cbr": "application/vnd.comicbook-rar",
    ".mobi": "application/x-mobipocket-ebook",
    ".azw3": "application/vnd.amazon.ebook",
}
DEFAULT_MIME_TYPE = "application/octet-stream"

EPOCH = datetime(1970, 1, 1)

ElementTree.register_namespace("", ATOM_NS)


@dataclass(frozen=True)
class Link:
    """A single Atom link element."""

    rel: str
    href: str
    link_type: str | None = None
    title: str | None = None


def book_mime_type(filename: str) -> str:
    """Return the acquisition MIME type for a book filename."""
    return BOOK_MIME_TYPES.get(Path(filename).suffix.lower(), DEFAULT_MIME_TYPE)


def navigation_feed(
    *,
    title: str,
    updated: datetime | None,
    self_href: str,
    links: Sequence[Link] = (),
    entries: Sequence[ElementTree.Element] = (),
) -> bytes:
    """Serialize a navigation catalog feed."""
    feed = _new_feed(title, updated, self_href, NAVIGATION_TYPE)
    for link in links:
        _add_link(feed, link)
    for entry in entries:
        feed.append(entry)
    return _serialize(feed)


def acquisition_feed(
    *,
    title: str,
    updated: datetime | None,
    self_href: str,
    books: Sequence[Book],
    links: Sequence[Link] = (),
    next_href: str | None = None,
) -> bytes:
    """Serialize an acquisition catalog feed."""
    feed = _new_feed(title, updated, self_href, ACQUISITION_TYPE)
    for book in books:
        feed.append(book_entry(book))
    for link in links:
        _add_link(feed, link)
    if next_href:
        _add_link(feed, Link("next", next_href, ACQUISITION_TYPE))
    return _serialize(feed)


def book_entry(book: Book) -> ElementTree.Element:
    """Build the Atom entry for one book."""
    entry = ElementTree.Element(f"{{{ATOM_NS}}}entry")
    created = book.created_at or EPOCH
    _add_text(entry, "id", f"tag:bookflow,{created:%Y-%m-%d},book/{book.id}")
    _add_text(entry, "title", book.title or book.relative_path)
    _add_text(entry, "updated", _timestamp(book.updated_at or created))
    if book.authors:
        author = ElementTree.SubElement(entry, f"{{{ATOM_NS}}}author")
        _add_text(author, "name", book.authors)
    if book.description:
        _add_text(entry, "summary", book.description)

    _add_link(
        entry,
        Link(
            ACQUISITION_REL,
            url_for("opds.download", book_id=book.id),
            book_mime_type(book.relative_path),
            title="Download",
        ),
    )
    cover = url_for("opds.cover", book_id=book.id)
    _add_link(entry, Link(THUMBNAIL_REL, cover))
    _add_link(entry, Link(IMAGE_REL, cover))
    return entry


def author_entry(name: str, count: int, href: str, updated: datetime | None):
    """Build the Atom entry listing one author."""
    entry = ElementTree.Element(f"{{{ATOM_NS}}}entry")
    _add_text(entry, "id", f"tag:bookflow,author,{name}")
    _add_text(entry, "title", name)
    _add_text(entry, "updated", _timestamp(updated))
    _add_text(entry, "summary", f"{count} book(s)")
    _add_link(entry, Link(SUBSECTION_REL, href, ACQUISITION_TYPE))
    return entry


def _new_feed(
    title: str, updated: datetime | None, self_href: str, content_type: str
) -> ElementTree.Element:
    feed = ElementTree.Element(f"{{{ATOM_NS}}}feed")
    _add_text(feed, "title", title)
    _add_text(feed, "id", f"tag:bookflow,{self_href}")
    _add_text(feed, "updated", _timestamp(updated))
    _add_link(feed, Link("self", self_href, content_type))
    return feed


def _add_text(parent: ElementTree.Element, tag: str, text: str) -> None:
    element = ElementTree.SubElement(parent, f"{{{ATOM_NS}}}{tag}")
    element.text = text


def _add_link(parent: ElementTree.Element, link: Link) -> None:
    attributes = {"rel": link.rel, "href": link.href}
    if link.link_type:
        attributes["type"] = link.link_type
    if link.title:
        attributes["title"] = link.title
    ElementTree.SubElement(parent, f"{{{ATOM_NS}}}link", attributes)


def _timestamp(value: datetime | None) -> str:
    return (value or EPOCH).strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialize(feed: ElementTree.Element) -> bytes:
    return ElementTree.tostring(feed, encoding="utf-8", xml_declaration=True)
