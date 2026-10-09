"""OPDS catalog routes: navigation and acquisition feeds."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Never
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape

from flask import (
    Blueprint,
    Response,
    abort,
    request,
    url_for,
)
from sqlalchemy import ColumnElement, and_, false, func, or_, select
from werkzeug.exceptions import HTTPException

from bookflow.database.database import session_scope
from bookflow.database.models import Book, LibraryFolder
from bookflow.library.metadata import extract_cover
from bookflow.library.paths import book_file, send_book_response
from bookflow.opds.auth import (
    AUTH_DOCUMENT_TYPE,
    authentication_document,
    require_basic_auth,
)
from bookflow.opds.generator import (
    ACQUISITION_TYPE,
    NAVIGATION_TYPE,
    SEARCH_REL,
    SUBSECTION_REL,
    Link,
    acquisition_feed,
    author_entry,
    nav_entry,
    navigation_feed,
)
from bookflow.optimizer.service import OptimizationError, optimize_book

bp = Blueprint("opds", __name__)

# One blueprint serves the original catalog and both device catalogs, so
# every rule carries its full path instead of a shared url_prefix.
OPDS_URL_PREFIXES = ("/opds", "/opdsx3", "/opdsx4")

bp.before_request(partial(require_basic_auth, skip="opds.authentication"))

PAGE_SIZE = 50
FOLDER_SEGMENTS_LIMIT = 500
PATH_MAX_DEPTH = 16
PATH_MAX_LENGTH = 1024


# --- discovery --------------------------------------------------------------


@bp.get("/opds/authentication")
def authentication() -> Response:
    """Serve the OPDS Authentication Document (public, per specification)."""
    return Response(
        authentication_document(), content_type=AUTH_DOCUMENT_TYPE
    )


@bp.get("/opds/")
@bp.get("/opds")
def root_feed() -> Response:
    """Root navigation feed of the original catalog (the folder view)."""
    return _catalog_root(None)


@bp.get("/opdsx3")
def x3_feed() -> Response:
    """Root navigation feed of the X3 catalog (the folder view)."""
    return _catalog_root("x3")


@bp.get("/opdsx4")
def x4_feed() -> Response:
    """Root navigation feed of the X4 catalog (the folder view)."""
    return _catalog_root("x4")


def _catalog_root(profile: str | None) -> Response:
    """Build the root feed of a catalog: registered folders, listed directly.

    The root feed *is* the folders view, so clients land in the folder
    hierarchy without an extra hop. The flat feeds (All Books, Recent,
    Authors) are linked alongside so entry-only clients can reach them,
    and the original catalog keeps its X3/X4 catalog links so the device
    profiles stay reachable.
    """
    ep = _endpoints(profile)
    folders = _registered_folders()
    with session_scope() as session:
        updated = session.scalar(select(func.max(Book.updated_at)))
    links, entries = _folder_items(ep, folders, updated)
    for title, href, link_type in (
        ("All Books", url_for(ep.books), ACQUISITION_TYPE),
        ("Recent", url_for(ep.recent), ACQUISITION_TYPE),
        ("Authors", url_for(ep.authors), NAVIGATION_TYPE),
    ):
        links.append(Link(SUBSECTION_REL, href, link_type, title=title))
        entries.append(
            nav_entry(
                entry_id=f"tag:bookflow,section,{href}",
                title=title,
                href=href,
                link_type=link_type,
                updated=updated,
            )
        )
    if profile is None:
        for name, href in (
            ("X3 Catalog", url_for("opds.x3_feed")),
            ("X4 Catalog", url_for("opds.x4_feed")),
        ):
            links.append(
                Link(SUBSECTION_REL, href, NAVIGATION_TYPE, title=name)
            )
            entries.append(
                nav_entry(
                    entry_id=f"tag:bookflow,section,{href}",
                    title=name,
                    href=href,
                    link_type=NAVIGATION_TYPE,
                    updated=updated,
                )
            )
    links.append(_search_link(ep))
    title = (
        "BookFlow"
        if profile is None
        else f"BookFlow — {profile.upper()} Catalog"
    )
    data = navigation_feed(
        title=title,
        updated=updated,
        self_href=url_for(ep.root),
        links=links,
        entries=entries,
    )
    return Response(data, content_type=NAVIGATION_TYPE)


@bp.get("/opds/books")
def books_feed() -> Response:
    """All books of the original catalog, A→Z."""
    return _books_feed(None)


@bp.get("/opdsx3/books")
def x3_books_feed() -> Response:
    """All books with X3-optimized EPUB acquisitions."""
    return _books_feed("x3")


@bp.get("/opdsx4/books")
def x4_books_feed() -> Response:
    """All books with X4-optimized EPUB acquisitions."""
    return _books_feed("x4")


def _books_feed(profile: str | None) -> Response:
    ep = _endpoints(profile)
    return _acquisition_response(
        _book_page(),
        endpoint=ep.books,
        title=f"BookFlow — {_prefix(profile)}All Books",
        profile=profile,
    )


@bp.get("/opds/recent")
def recent_feed() -> Response:
    """Books of the original catalog, newest first."""
    return _recent_feed(None)


@bp.get("/opdsx3/recent")
def x3_recent_feed() -> Response:
    """Newest books with X3-optimized EPUB acquisitions."""
    return _recent_feed("x3")


@bp.get("/opdsx4/recent")
def x4_recent_feed() -> Response:
    """Newest books with X4-optimized EPUB acquisitions."""
    return _recent_feed("x4")


def _recent_feed(profile: str | None) -> Response:
    ep = _endpoints(profile)
    return _acquisition_response(
        _book_page(order=(Book.created_at.desc(), Book.id.desc())),
        endpoint=ep.recent,
        title=f"BookFlow — {_prefix(profile)}Recent",
        profile=profile,
    )


# --- folders ----------------------------------------------------------------


@bp.get("/opds/folders")
def folders_feed() -> Response:
    """Navigation feed listing every registered library folder."""
    return _folders_index(None)


@bp.get("/opds/folders/<int:folder_id>")
def folder_feed(folder_id: int) -> Response:
    """One directory level of a registered folder: subfolders and books."""
    return _folder_level(folder_id, None)


@bp.get("/opdsx3/folders")
def x3_folders_feed() -> Response:
    """Navigation feed listing folders for the X3 catalog."""
    return _folders_index("x3")


@bp.get("/opdsx3/folders/<int:folder_id>")
def x3_folder_feed(folder_id: int) -> Response:
    """One directory level, with X3-optimized EPUB acquisitions."""
    return _folder_level(folder_id, "x3")


@bp.get("/opdsx4/folders")
def x4_folders_feed() -> Response:
    """Navigation feed listing folders for the X4 catalog."""
    return _folders_index("x4")


@bp.get("/opdsx4/folders/<int:folder_id>")
def x4_folder_feed(folder_id: int) -> Response:
    """One directory level, with X4-optimized EPUB acquisitions."""
    return _folder_level(folder_id, "x4")


def _registered_folders() -> list[tuple[int, str]]:
    """Every registered folder as ``(id, name)``, case-insensitive order."""
    with session_scope() as session:
        return list(
            session.execute(
                select(LibraryFolder.id, LibraryFolder.name).order_by(
                    func.lower(LibraryFolder.name)
                )
            )
        )


def _folder_items(
    ep: _Endpoints,
    folders: Sequence[tuple[int, str]],
    updated: datetime | None,
) -> tuple[list[Link], list[ElementTree.Element]]:
    """Feed-level links and navigation entries for the registered folders."""
    links = [
        Link(
            SUBSECTION_REL,
            url_for(ep.folder_level, folder_id=folder_id),
            ACQUISITION_TYPE,
            title=name,
        )
        for folder_id, name in folders
    ]
    entries = [
        nav_entry(
            entry_id=f"tag:bookflow,folder,{folder_id}",
            title=name,
            href=url_for(ep.folder_level, folder_id=folder_id),
            link_type=ACQUISITION_TYPE,
            updated=updated,
        )
        for folder_id, name in folders
    ]
    return links, entries


def _folders_index(profile: str | None) -> Response:
    ep = _endpoints(profile)
    folders = _registered_folders()
    with session_scope() as session:
        updated = session.scalar(select(func.max(Book.updated_at)))
    links, entries = _folder_items(ep, folders, updated)
    links.append(_search_link(ep))
    data = navigation_feed(
        title=f"BookFlow — {_prefix(profile)}Folders",
        updated=updated,
        self_href=url_for(ep.folders),
        links=links,
        entries=entries,
    )
    return Response(data, content_type=NAVIGATION_TYPE)


def _folder_level(folder_id: int, profile: str | None) -> Response:
    ep = _endpoints(profile)
    path = _clean_path(request.args.get("path", ""))
    prefix = f"{path}/" if path else ""

    scope: list[ColumnElement[bool]] = [Book.folder_id == folder_id]
    if prefix:
        scope.append(Book.relative_path.startswith(prefix, autoescape=True))
    rest = func.substr(Book.relative_path, len(prefix) + 1)

    with session_scope() as session:
        folder = session.get(LibraryFolder, folder_id)
        if folder is None:
            abort(404)
        segments = list(
            session.scalars(
                select(func.substr(rest, 1, func.instr(rest, "/") - 1))
                .where(*scope, func.instr(rest, "/") > 0)
                .distinct()
                .limit(FOLDER_SEGMENTS_LIMIT)
            )
        )
        segments.sort(key=str.casefold)

    segment_hrefs = [
        (
            segment,
            url_for(
                ep.folder_level,
                folder_id=folder_id,
                path=f"{path}/{segment}" if path else segment,
            ),
        )
        for segment in segments
    ]
    links = [
        Link(SUBSECTION_REL, href, ACQUISITION_TYPE, title=segment)
        for segment, href in segment_hrefs
    ]
    book_page = _book_page(
        condition=and_(*scope, func.instr(rest, "/") == 0),
        updated_condition=and_(*scope),
    )
    entries = [
        nav_entry(
            entry_id=f"tag:bookflow,folder,{folder_id},{path}/{segment}",
            title=segment,
            href=href,
            link_type=ACQUISITION_TYPE,
            updated=book_page.updated,
        )
        for segment, href in segment_hrefs
    ]
    label = folder.name if not path else f"{folder.name} / {path}"
    return _acquisition_response(
        book_page,
        endpoint=ep.folder_level,
        title=f"BookFlow — {_prefix(profile)}{label}",
        profile=profile,
        url_kwargs={"folder_id": folder_id, "path": path or None},
        links=links,
        entries=entries,
    )


# --- authors ----------------------------------------------------------------


@bp.get("/opds/authors")
def authors_feed() -> Response:
    """Author index of the original catalog."""
    return _authors_feed(None)


@bp.get("/opdsx3/authors")
def x3_authors_feed() -> Response:
    """Author index for the X3 catalog."""
    return _authors_feed("x3")


@bp.get("/opdsx4/authors")
def x4_authors_feed() -> Response:
    """Author index for the X4 catalog."""
    return _authors_feed("x4")


def _authors_feed(profile: str | None) -> Response:
    ep = _endpoints(profile)
    with session_scope() as session:
        rows = list(
            session.execute(
                select(Book.authors, func.count(Book.id))
                .where(Book.authors.is_not(None), Book.authors != "")
                .group_by(Book.authors)
                .order_by(func.lower(Book.authors))
            )
        )
        updated = session.scalar(select(func.max(Book.updated_at)))
    entries = [
        author_entry(
            name,
            int(count),
            url_for(ep.author, author=name),
            updated,
        )
        for name, count in rows
    ]
    data = navigation_feed(
        title=f"BookFlow — {_prefix(profile)}Authors",
        updated=updated,
        self_href=url_for(ep.authors),
        links=[_search_link(ep)],
        entries=entries,
    )
    return Response(data, content_type=NAVIGATION_TYPE)


@bp.get("/opds/authors/<path:author>")
def author_feed(author: str) -> Response:
    """Books by one author, original catalog."""
    return _author_feed(None, author)


@bp.get("/opdsx3/authors/<path:author>")
def x3_author_feed(author: str) -> Response:
    """Books by one author with X3-optimized EPUB acquisitions."""
    return _author_feed("x3", author)


@bp.get("/opdsx4/authors/<path:author>")
def x4_author_feed(author: str) -> Response:
    """Books by one author with X4-optimized EPUB acquisitions."""
    return _author_feed("x4", author)


def _author_feed(profile: str | None, author: str) -> Response:
    ep = _endpoints(profile)
    return _acquisition_response(
        _book_page(condition=Book.authors == author),
        endpoint=ep.author,
        title=f"BookFlow — {_prefix(profile)}{author}",
        profile=profile,
        url_kwargs={"author": author},
    )


# --- search -----------------------------------------------------------------


@bp.get("/opds/search")
def search_feed() -> Response:
    """Search the original catalog."""
    return _search_feed(None)


@bp.get("/opdsx3/search")
def x3_search_feed() -> Response:
    """Search with X3-optimized EPUB acquisitions."""
    return _search_feed("x3")


@bp.get("/opdsx4/search")
def x4_search_feed() -> Response:
    """Search with X4-optimized EPUB acquisitions."""
    return _search_feed("x4")


def _search_feed(profile: str | None) -> Response:
    ep = _endpoints(profile)
    query = request.args.get("q", "").strip()
    title = (
        f"BookFlow — {_prefix(profile)}Search: {query}"
        if query
        else f"BookFlow — {_prefix(profile)}Search"
    )
    return _acquisition_response(
        _book_page(condition=_search_condition(query) if query else false()),
        endpoint=ep.search,
        title=title,
        profile=profile,
        url_kwargs={"q": query or None},
    )


# --- single book ------------------------------------------------------------


@bp.get("/opds/books/<int:book_id>")
def book_feed(book_id: int) -> Response:
    """Return an OPDS feed containing a single book entry."""
    return _book_feed(book_id, None)


@bp.get("/opdsx3/books/<int:book_id>")
def x3_book_feed(book_id: int) -> Response:
    """Return an OPDS feed for a single book with X3 optimized acquisition."""
    return _book_feed(book_id, "x3")


@bp.get("/opdsx4/books/<int:book_id>")
def x4_book_feed(book_id: int) -> Response:
    """Return an OPDS feed for a single book with X4 optimized acquisition."""
    return _book_feed(book_id, "x4")


def _book_feed(book_id: int, profile: str | None) -> Response:
    ep = _endpoints(profile)
    with session_scope() as session:
        book = session.get(Book, book_id)
        if book is None:
            abort(404)
        title = book.title or book.relative_path
        updated = book.updated_at or book.created_at
        data = acquisition_feed(
            title=f"BookFlow — {_prefix(profile)}{title}",
            updated=updated,
            self_href=url_for(ep.book, book_id=book_id),
            books=[book],
            links=[_search_link(ep)],
            profile=profile,
        )
    return Response(data, content_type=ACQUISITION_TYPE)


@bp.get("/opds/download/<int:book_id>")
def download(book_id: int) -> Response:
    """Download the original ebook file."""
    return send_book_response(book_file(book_id))


_COVER_CACHE_MAX_ENTRIES = 256
_COVER_MAX_BYTES = 2 * 1024 * 1024
_cover_cache: dict[tuple[int, int, int], tuple[bytes, str, str] | None] = {}
_cover_cache_lock = threading.Lock()


@bp.get("/opds/cover/<int:book_id>")
def cover(book_id: int) -> Response:
    """Serve the extracted cover image for an EPUB or CBZ."""
    target = book_file(book_id)
    if target.suffix.lower() not in (".epub", ".cbz"):
        abort(404)

    stat = target.stat()
    cache_key = (book_id, stat.st_mtime_ns, stat.st_size)

    with _cover_cache_lock:
        cached = _cover_cache.get(cache_key)

    if cache_key in _cover_cache:
        if cached is None:
            abort(404)
        data, media_type, etag = cached
        if request.if_none_match and (
            request.if_none_match.contains(etag.strip('"'))
            or request.if_none_match.contains_raw(etag)
        ):
            return Response(
                status=304,
                headers={"ETag": etag, "Cache-Control": "public, max-age=3600"},
            )
        return Response(
            data,
            content_type=media_type,
            headers={"Cache-Control": "public, max-age=3600", "ETag": etag},
        )

    extracted = extract_cover(target)
    if extracted is None:
        with _cover_cache_lock:
            if len(_cover_cache) >= _COVER_CACHE_MAX_ENTRIES:
                _cover_cache.clear()
            _cover_cache[cache_key] = None
        abort(404)

    data, media_type = extracted
    digest = hashlib.sha256(data).hexdigest()
    etag = f'"{digest}"'

    with _cover_cache_lock:
        if len(_cover_cache) >= _COVER_CACHE_MAX_ENTRIES:
            _cover_cache.clear()
        if len(data) <= _COVER_MAX_BYTES:
            _cover_cache[cache_key] = (data, media_type, etag)

    if request.if_none_match and (
        request.if_none_match.contains(digest)
        or request.if_none_match.contains_raw(etag)
    ):
        return Response(
            status=304,
            headers={"ETag": etag, "Cache-Control": "public, max-age=3600"},
        )
    return Response(
        data,
        content_type=media_type,
        headers={"Cache-Control": "public, max-age=3600", "ETag": etag},
    )


# --- optimized acquisition --------------------------------------------------


@bp.get("/opdsx3/download/<int:book_id>")
def x3_download(book_id: int) -> Response:
    """Download the book optimized for the X3 profile."""
    return _optimized_download(book_id, "x3")


@bp.get("/opdsx4/download/<int:book_id>")
def x4_download(book_id: int) -> Response:
    """Download the book optimized for the X4 profile."""
    return _optimized_download(book_id, "x4")


# --- errors -----------------------------------------------------------------


@bp.get("/opds/<path:unknown>")
@bp.get("/opdsx3/<path:unknown>")
@bp.get("/opdsx4/<path:unknown>")
def unknown_path(unknown: str) -> Never:
    """Fallback handler returning a 404 OPDS XML error for unknown paths."""
    abort(404)


@bp.errorhandler(HTTPException)
def error_document(error: HTTPException) -> Response:
    """Build the XML error document for a failure under ``OPDS_URL_PREFIXES``.

    Registering ``HTTPException`` instead of a fixed code list keeps rare
    statuses (405, 414, 416, …) from falling through to Werkzeug's HTML
    body. Unhandled exceptions reach this handler too, as an
    ``InternalServerError``. Routing failures skip the blueprint and are
    answered by ``create_app``'s fallback, which calls this directly.
    """
    status = error.code or 500
    description = error.description or "Request failed"
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f"<error><code>{status}</code>"
        f"<message>{xml_escape(description)}</message></error>"
    )
    return Response(body, status=status, content_type="application/xml")


# --- helpers ----------------------------------------------------------------


def _page() -> int:
    try:
        return max(int(request.args.get("page", "1")), 1)
    except (TypeError, ValueError):
        return 1


@dataclass(frozen=True)
class _Endpoints:
    """Endpoint names for one catalog (original or a device profile)."""

    root: str
    books: str
    recent: str
    authors: str
    author: str
    search: str
    folders: str
    folder_level: str
    book: str


def _endpoints(profile: str | None) -> _Endpoints:
    if profile is None:
        return _Endpoints(
            root="opds.root_feed",
            books="opds.books_feed",
            recent="opds.recent_feed",
            authors="opds.authors_feed",
            author="opds.author_feed",
            search="opds.search_feed",
            folders="opds.folders_feed",
            folder_level="opds.folder_feed",
            book="opds.book_feed",
        )
    prefix = f"opds.{profile}"
    return _Endpoints(
        root=f"{prefix}_feed",
        books=f"{prefix}_books_feed",
        recent=f"{prefix}_recent_feed",
        authors=f"{prefix}_authors_feed",
        author=f"{prefix}_author_feed",
        search=f"{prefix}_search_feed",
        folders=f"{prefix}_folders_feed",
        folder_level=f"{prefix}_folder_feed",
        book=f"{prefix}_book_feed",
    )


def _prefix(profile: str | None) -> str:
    """Return the title prefix for one catalog ("X3 — " or empty)."""
    return f"{profile.upper()} — " if profile else ""


def _search_link(ep: _Endpoints) -> Link:
    """Feed-level OpenSearch template for one catalog."""
    return Link(
        SEARCH_REL,
        f"{url_for(ep.search)}?q={{searchTerms}}",
        ACQUISITION_TYPE,
        title="Search",
    )


def _clean_path(value: str) -> str:
    """Normalize the ``path`` query argument into an index prefix.

    The result is only ever matched against ``Book.relative_path`` in SQL;
    it never reaches the filesystem.
    """
    parts = [
        part
        for part in value[:PATH_MAX_LENGTH].split("/")
        if part not in ("", ".", "..")
    ]
    return "/".join(parts[:PATH_MAX_DEPTH])


def _title_order() -> ColumnElement[str]:
    return func.lower(func.coalesce(Book.title, Book.relative_path))


@dataclass(frozen=True)
class _BookPage:
    """One page of books plus the numbers its feed links need."""

    page: int
    offset: int
    total: int
    updated: datetime | None
    books: list[Book]


def _book_page(
    *,
    condition: ColumnElement[bool] | None = None,
    updated_condition: ColumnElement[bool] | None = None,
    order: Sequence[ColumnElement] = (),
) -> _BookPage:
    """Fetch one page of books for an acquisition feed.

    ``condition`` scopes the rows, the page total and — unless
    ``updated_condition`` widens it — the feed's ``updated`` stamp.
    ``order`` defaults to the A→Z title order.
    """
    page = _page()
    offset = (page - 1) * PAGE_SIZE
    count_stmt = select(func.count(Book.id))
    updated_stmt = select(func.max(Book.updated_at))
    books_stmt = select(Book).order_by(*(order or (_title_order(),)))
    if condition is not None:
        count_stmt = count_stmt.where(condition)
        books_stmt = books_stmt.where(condition)
    if updated_condition is None:
        updated_condition = condition
    if updated_condition is not None:
        updated_stmt = updated_stmt.where(updated_condition)
    with session_scope() as session:
        total = int(session.scalar(count_stmt) or 0)
        updated = session.scalar(updated_stmt)
        books = list(
            session.scalars(books_stmt.offset(offset).limit(PAGE_SIZE))
        )
    return _BookPage(
        page=page,
        offset=offset,
        total=total,
        updated=updated,
        books=books,
    )


def _acquisition_response(
    book_page: _BookPage,
    *,
    endpoint: str,
    title: str,
    profile: str | None,
    url_kwargs: Mapping[str, object] | None = None,
    links: Sequence[Link] = (),
    entries: Sequence[ElementTree.Element] = (),
) -> Response:
    """Render one page of books as an acquisition feed response.

    ``url_kwargs`` are the endpoint's non-pagination arguments (author
    name, folder id, search terms); ``links``/``entries`` are the
    navigation section rendered alongside the books.
    """
    extra = dict(url_kwargs or {})
    page = book_page.page
    next_href = (
        url_for(endpoint, **extra, page=page + 1)
        if book_page.offset + len(book_page.books) < book_page.total
        else None
    )
    data = acquisition_feed(
        title=title,
        updated=book_page.updated,
        self_href=url_for(endpoint, **extra, page=page if page > 1 else None),
        books=book_page.books,
        links=[*links, _search_link(_endpoints(profile))],
        entries=entries,
        next_href=next_href,
        profile=profile,
    )
    return Response(data, content_type=ACQUISITION_TYPE)


def _optimized_download(book_id: int, profile: str) -> Response:
    source = book_file(book_id)
    suffix = source.suffix.lower()
    if suffix not in (".epub", ".cbz"):
        abort(404)
    try:
        optimized = optimize_book(book_id, profile, source)
    except OptimizationError:
        abort(
            500,
            description="The optimized publication could not be generated.",
        )
    target_name = source.stem + (".xtc" if suffix == ".cbz" else ".epub")
    return send_book_response(optimized, download_name=target_name)


def _search_condition(query: str) -> ColumnElement[bool]:
    pattern = f"%{_escape_like(query)}%"
    return or_(
        Book.title.ilike(pattern, escape="\\"),
        Book.authors.ilike(pattern, escape="\\"),
        Book.publisher.ilike(pattern, escape="\\"),
        Book.description.ilike(pattern, escape="\\"),
        Book.series.ilike(pattern, escape="\\"),
        Book.isbn.ilike(pattern, escape="\\"),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
