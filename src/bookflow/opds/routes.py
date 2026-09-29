"""OPDS catalog routes: navigation and acquisition feeds."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    request,
    send_file,
    url_for,
)
from sqlalchemy import false, func, or_, select
from sqlalchemy.exc import OperationalError

from bookflow.database.database import session_scope
from bookflow.database.models import Book
from bookflow.library.metadata import extract_cover
from bookflow.opds.auth import (
    AUTH_DOCUMENT_TYPE,
    authenticate,
    authentication_document,
)
from bookflow.opds.generator import (
    ACQUISITION_TYPE,
    NAVIGATION_TYPE,
    SEARCH_REL,
    SUBSECTION_REL,
    Link,
    acquisition_feed,
    author_entry,
    book_mime_type,
    navigation_feed,
)
from bookflow.optimizer.service import OptimizationError, optimize_book

bp = Blueprint("opds", __name__, url_prefix="/opds")

PAGE_SIZE = 50


@bp.before_request
def _require_basic_auth():
    if request.endpoint == "opds.authentication":
        return None
    verifier = current_app.extensions["password_verifier"]
    return authenticate(verifier, request.authorization)


# --- discovery --------------------------------------------------------------


@bp.get("/authentication")
def authentication():
    """Serve the OPDS Authentication Document (public, per specification)."""
    return Response(
        authentication_document(), content_type=AUTH_DOCUMENT_TYPE
    )


@bp.get("/")
@bp.get("")
def root_feed():
    links = [
        Link(
            SUBSECTION_REL,
            url_for("opds.books_feed"),
            ACQUISITION_TYPE,
            title="All Books",
        ),
        Link(
            SUBSECTION_REL,
            url_for("opds.recent_feed"),
            ACQUISITION_TYPE,
            title="Recent",
        ),
        Link(
            SUBSECTION_REL,
            url_for("opds.authors_feed"),
            NAVIGATION_TYPE,
            title="Authors",
        ),
        Link(
            SUBSECTION_REL,
            url_for("opds.x3_feed"),
            ACQUISITION_TYPE,
            title="X3 Catalog",
        ),
        Link(
            SUBSECTION_REL,
            url_for("opds.x4_feed"),
            ACQUISITION_TYPE,
            title="X4 Catalog",
        ),
        Link(
            SEARCH_REL,
            f"{url_for('opds.search_feed')}?q={{searchTerms}}",
            ACQUISITION_TYPE,
            title="Search",
        ),
    ]
    with session_scope() as session:
        try:
            updated = session.scalar(select(func.max(Book.updated_at)))
        except OperationalError:
            updated = None
    data = navigation_feed(
        title="BookFlow",
        updated=updated,
        self_href=url_for("opds.root_feed"),
        links=links,
    )
    return Response(data, content_type=NAVIGATION_TYPE)


@bp.get("/books")
def books_feed():
    page = _page()
    offset = (page - 1) * PAGE_SIZE
    with session_scope() as session:
        total = int(session.scalar(select(func.count(Book.id))) or 0)
        updated = session.scalar(select(func.max(Book.updated_at)))
        books = _page_of_books(session, offset)
    next_href = (
        url_for("opds.books_feed", page=page + 1)
        if offset + len(books) < total
        else None
    )
    data = acquisition_feed(
        title="BookFlow — All Books",
        updated=updated,
        self_href=url_for("opds.books_feed", page=page if page > 1 else None),
        books=books,
        next_href=next_href,
    )
    return Response(data, content_type=ACQUISITION_TYPE)


@bp.get("/recent")
def recent_feed():
    page = _page()
    offset = (page - 1) * PAGE_SIZE
    with session_scope() as session:
        total = int(session.scalar(select(func.count(Book.id))) or 0)
        updated = session.scalar(select(func.max(Book.updated_at)))
        books = list(
            session.scalars(
                select(Book)
                .order_by(Book.created_at.desc(), Book.id.desc())
                .offset(offset)
                .limit(PAGE_SIZE)
            )
        )
    next_href = (
        url_for("opds.recent_feed", page=page + 1)
        if offset + len(books) < total
        else None
    )
    data = acquisition_feed(
        title="BookFlow — Recent",
        updated=updated,
        self_href=url_for("opds.recent_feed", page=page if page > 1 else None),
        books=books,
        next_href=next_href,
    )
    return Response(data, content_type=ACQUISITION_TYPE)


# --- device catalogs --------------------------------------------------------


@bp.get("/x3")
def x3_feed():
    return _device_feed("x3")


@bp.get("/x4")
def x4_feed():
    return _device_feed("x4")


def _device_feed(profile: str):
    page = _page()
    offset = (page - 1) * PAGE_SIZE
    with session_scope() as session:
        total = int(session.scalar(select(func.count(Book.id))) or 0)
        updated = session.scalar(select(func.max(Book.updated_at)))
        books = _page_of_books(session, offset)
    next_href = (
        url_for(f"opds.{profile}_feed", page=page + 1)
        if offset + len(books) < total
        else None
    )
    data = acquisition_feed(
        title=f"BookFlow — {profile.upper()} Catalog",
        updated=updated,
        self_href=url_for(
            f"opds.{profile}_feed", page=page if page > 1 else None
        ),
        books=books,
        next_href=next_href,
        profile=profile,
    )
    return Response(data, content_type=ACQUISITION_TYPE)


# --- authors ----------------------------------------------------------------


@bp.get("/authors")
def authors_feed():
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
            url_for("opds.author_feed", author=name),
            updated,
        )
        for name, count in rows
    ]
    data = navigation_feed(
        title="BookFlow — Authors",
        updated=updated,
        self_href=url_for("opds.authors_feed"),
        entries=entries,
    )
    return Response(data, content_type=NAVIGATION_TYPE)


@bp.get("/authors/<path:author>")
def author_feed(author: str):
    page = _page()
    offset = (page - 1) * PAGE_SIZE
    with session_scope() as session:
        condition = Book.authors == author
        total = int(
            session.scalar(select(func.count(Book.id)).where(condition)) or 0
        )
        updated = session.scalar(select(func.max(Book.updated_at)).where(condition))
        books = _page_of_books(session, offset, condition)
    next_href = (
        url_for("opds.author_feed", author=author, page=page + 1)
        if offset + len(books) < total
        else None
    )
    data = acquisition_feed(
        title=f"BookFlow — {author}",
        updated=updated,
        self_href=url_for(
            "opds.author_feed", author=author, page=page if page > 1 else None
        ),
        books=books,
        next_href=next_href,
    )
    return Response(data, content_type=ACQUISITION_TYPE)


# --- search -----------------------------------------------------------------


@bp.get("/search")
def search_feed():
    query = request.args.get("q", "").strip()
    page = _page()
    offset = (page - 1) * PAGE_SIZE
    condition = _search_condition(query) if query else false()
    with session_scope() as session:
        total = int(
            session.scalar(select(func.count(Book.id)).where(condition)) or 0
        )
        updated = session.scalar(select(func.max(Book.updated_at)).where(condition))
        books = _page_of_books(session, offset, condition)
    next_href = (
        url_for("opds.search_feed", q=query, page=page + 1)
        if offset + len(books) < total
        else None
    )
    data = acquisition_feed(
        title=f"BookFlow — Search: {query}" if query else "BookFlow — Search",
        updated=updated,
        self_href=url_for(
            "opds.search_feed",
            q=query or None,
            page=page if page > 1 else None,
        ),
        books=books,
        next_href=next_href,
    )
    return Response(data, content_type=ACQUISITION_TYPE)


# --- single book ------------------------------------------------------------


@bp.get("/books/<int:book_id>")
def book_feed(book_id: int):
    return _book_feed(book_id, None)


@bp.get("/x3/books/<int:book_id>")
def x3_book_feed(book_id: int):
    return _book_feed(book_id, "x3")


@bp.get("/x4/books/<int:book_id>")
def x4_book_feed(book_id: int):
    return _book_feed(book_id, "x4")


def _book_feed(book_id: int, profile: str | None):
    with session_scope() as session:
        book = session.get(Book, book_id)
        if book is None:
            abort(404)
        title = book.title or book.relative_path
        updated = book.updated_at or book.created_at
        prefix = f"{profile.upper()} — " if profile else ""
        endpoint = (
            f"opds.{profile}_book_feed" if profile else "opds.book_feed"
        )
        data = acquisition_feed(
            title=f"BookFlow — {prefix}{title}",
            updated=updated,
            self_href=url_for(endpoint, book_id=book_id),
            books=[book],
            profile=profile,
        )
    return Response(data, content_type=ACQUISITION_TYPE)


@bp.get("/download/<int:book_id>")
def download(book_id: int):
    target = _book_file(book_id)
    return send_file(
        target,
        mimetype=book_mime_type(target.name),
        as_attachment=True,
        download_name=target.name,
        conditional=True,
    )


@bp.get("/cover/<int:book_id>")
def cover(book_id: int):
    target = _book_file(book_id)
    if target.suffix.lower() != ".epub":
        abort(404)
    extracted = extract_cover(target)
    if extracted is None:
        abort(404)
    data, media_type = extracted
    return Response(
        data,
        content_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


# --- optimized acquisition --------------------------------------------------


@bp.get("/x3/download/<int:book_id>")
def x3_download(book_id: int):
    return _optimized_download(book_id, "x3")


@bp.get("/x4/download/<int:book_id>")
def x4_download(book_id: int):
    return _optimized_download(book_id, "x4")


# --- errors -----------------------------------------------------------------


@bp.get("/<path:unknown>")
def unknown_path(unknown: str):
    abort(404)


@bp.errorhandler(404)
@bp.errorhandler(403)
@bp.errorhandler(500)
def _catalog_error(error):
    description = getattr(error, "description", None) or "Request failed"
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f"<error><code>{error.code}</code>"
        f"<message>{xml_escape(description)}</message></error>"
    )
    return Response(body, status=error.code, content_type="application/xml")


# --- helpers ----------------------------------------------------------------


def _page() -> int:
    try:
        return max(int(request.args.get("page", "1")), 1)
    except (TypeError, ValueError):
        return 1


def _title_order():
    return func.lower(func.coalesce(Book.title, Book.relative_path))


def _page_of_books(session, offset: int, condition=None):
    statement = select(Book).order_by(_title_order())
    if condition is not None:
        statement = statement.where(condition)
    return list(session.scalars(statement.offset(offset).limit(PAGE_SIZE)))


def _book_file(book_id: int) -> Path:
    """Return the on-disk book file, refusing paths outside the folder root."""
    with session_scope() as session:
        book = session.get(Book, book_id)
        if book is None:
            abort(404)
        folder_path = Path(book.folder.path)
        relative_path = book.relative_path
    root = folder_path.resolve()
    target = (folder_path / relative_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        abort(404)
    return target


def _optimized_download(book_id: int, profile: str) -> Response:
    source = _book_file(book_id)
    if source.suffix.lower() != ".epub":
        abort(404)
    try:
        optimized = optimize_book(book_id, profile, source)
    except OptimizationError:
        abort(
            500,
            description="The optimized publication could not be generated.",
        )
    return send_file(
        optimized,
        mimetype="application/epub+zip",
        as_attachment=True,
        download_name=source.name,
        conditional=True,
    )


def _search_condition(query: str):
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
