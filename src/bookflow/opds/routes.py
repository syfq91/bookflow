"""OPDS catalog routes: navigation and acquisition feeds."""

from __future__ import annotations

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
from bookflow.database.models import Book, LibraryFolder
from bookflow.library.metadata import extract_cover
from bookflow.library.paths import book_file
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
FOLDER_SEGMENTS_LIMIT = 500
PATH_MAX_DEPTH = 16
PATH_MAX_LENGTH = 1024


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
            url_for("opds.folders_feed"),
            NAVIGATION_TYPE,
            title="Folders",
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
        links=[
            Link(
                SUBSECTION_REL,
                url_for(f"opds.{profile}_folders_feed"),
                NAVIGATION_TYPE,
                title="Folders",
            )
        ],
        next_href=next_href,
        profile=profile,
    )
    return Response(data, content_type=ACQUISITION_TYPE)


# --- folders ----------------------------------------------------------------


@bp.get("/folders")
def folders_feed() -> Response:
    """Navigation feed listing every registered library folder."""
    return _folders_index(None)


@bp.get("/folders/<int:folder_id>")
def folder_feed(folder_id: int) -> Response:
    """One directory level of a registered folder: subfolders and books."""
    return _folder_level(folder_id, None)


@bp.get("/x3/folders")
def x3_folders_feed() -> Response:
    """Navigation feed listing folders for the X3 catalog."""
    return _folders_index("x3")


@bp.get("/x3/folders/<int:folder_id>")
def x3_folder_feed(folder_id: int) -> Response:
    """One directory level, with X3-optimized EPUB acquisitions."""
    return _folder_level(folder_id, "x3")


@bp.get("/x4/folders")
def x4_folders_feed() -> Response:
    """Navigation feed listing folders for the X4 catalog."""
    return _folders_index("x4")


@bp.get("/x4/folders/<int:folder_id>")
def x4_folder_feed(folder_id: int) -> Response:
    """One directory level, with X4-optimized EPUB acquisitions."""
    return _folder_level(folder_id, "x4")


def _folders_index(profile: str | None) -> Response:
    index_endpoint, level_endpoint = _folder_endpoints(profile)
    with session_scope() as session:
        folders = list(
            session.execute(
                select(LibraryFolder.id, LibraryFolder.name).order_by(
                    func.lower(LibraryFolder.name)
                )
            )
        )
        updated = session.scalar(select(func.max(Book.updated_at)))
    links = [
        Link(
            SUBSECTION_REL,
            url_for(level_endpoint, folder_id=folder_id),
            ACQUISITION_TYPE,
            title=name,
        )
        for folder_id, name in folders
    ]
    prefix = f"{profile.upper()} — " if profile else ""
    data = navigation_feed(
        title=f"BookFlow — {prefix}Folders",
        updated=updated,
        self_href=url_for(index_endpoint),
        links=links,
    )
    return Response(data, content_type=NAVIGATION_TYPE)


def _folder_level(folder_id: int, profile: str | None) -> Response:
    _, endpoint = _folder_endpoints(profile)
    path = _clean_path(request.args.get("path", ""))
    prefix = f"{path}/" if path else ""
    page = _page()
    offset = (page - 1) * PAGE_SIZE

    scope = [Book.folder_id == folder_id]
    if prefix:
        scope.append(Book.relative_path.startswith(prefix, autoescape=True))
    rest = func.substr(Book.relative_path, len(prefix) + 1)
    in_level = func.instr(rest, "/") == 0

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
        total = int(
            session.scalar(
                select(func.count(Book.id)).where(*scope, in_level)
            )
            or 0
        )
        updated = session.scalar(
            select(func.max(Book.updated_at)).where(*scope)
        )
        books = list(
            session.scalars(
                select(Book)
                .where(*scope, in_level)
                .order_by(_title_order())
                .offset(offset)
                .limit(PAGE_SIZE)
            )
        )

    links = [
        Link(
            SUBSECTION_REL,
            url_for(
                endpoint,
                folder_id=folder_id,
                path=f"{path}/{segment}" if path else segment,
            ),
            ACQUISITION_TYPE,
            title=segment,
        )
        for segment in segments
    ]
    label = folder.name if not path else f"{folder.name} / {path}"
    prefix_label = f"{profile.upper()} — " if profile else ""
    next_href = (
        url_for(endpoint, folder_id=folder_id, path=path or None, page=page + 1)
        if offset + len(books) < total
        else None
    )
    data = acquisition_feed(
        title=f"BookFlow — {prefix_label}{label}",
        updated=updated,
        self_href=url_for(
            endpoint,
            folder_id=folder_id,
            path=path or None,
            page=page if page > 1 else None,
        ),
        books=books,
        links=links,
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
    target = book_file(book_id)
    return send_file(
        target,
        mimetype=book_mime_type(target.name),
        as_attachment=True,
        download_name=target.name,
        conditional=True,
    )


@bp.get("/cover/<int:book_id>")
def cover(book_id: int):
    target = book_file(book_id)
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


def _folder_endpoints(profile: str | None) -> tuple[str, str]:
    """Return the (index, level) endpoint names for a catalog profile."""
    if profile is None:
        return "opds.folders_feed", "opds.folder_feed"
    return f"opds.{profile}_folders_feed", f"opds.{profile}_folder_feed"


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


def _title_order():
    return func.lower(func.coalesce(Book.title, Book.relative_path))


def _page_of_books(session, offset: int, condition=None):
    statement = select(Book).order_by(_title_order())
    if condition is not None:
        statement = statement.where(condition)
    return list(session.scalars(statement.offset(offset).limit(PAGE_SIZE)))


def _optimized_download(book_id: int, profile: str) -> Response:
    source = book_file(book_id)
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
