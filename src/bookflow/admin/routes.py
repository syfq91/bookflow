"""Admin dashboard, library folder management, and health pages."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from argon2 import PasswordHasher
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import OperationalError

from bookflow.auth.decorators import login_required, require_csrf
from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import (
    Book,
    LibraryFolder,
    OptimizedBook,
    Progression,
    User,
)
from bookflow.health import (
    HealthCheck,
    HealthStats,
    library_statistics,
    run_checks,
)
from bookflow.library.browse import BrowseResult, browse_directory
from bookflow.library.paths import book_file, send_book_response
from bookflow.library.scanner import ScanInProgress, ScanResult, scan_folder
from bookflow.library.service import (
    add_folder,
    get_folder,
    list_folders,
    remove_folder,
)
from bookflow.optimizer.service import clear_optimized_cache, prune_orphaned_cache

bp = Blueprint("admin", __name__, url_prefix="/admin")

bp.before_request(require_csrf)

_DASHBOARD_COMPONENTS = ("Database", "Library", "OPDS", "epubkit")

_DB_UNINITIALIZED_MESSAGE = (
    "Database not initialized. Run: uv run alembic upgrade head"
)


class DashboardStats(TypedDict):
    """Shape of the ``stats`` object the dashboard renders."""

    books: int
    folders: int
    total_size: int
    last_scan: datetime | None
    last_scan_duration: float | None
    scan_errors: int
    cache: dict[str, int]
    db_error: NotRequired[str]


PAGE_SIZE = 50

FOLDER_SEGMENTS_LIMIT = 500


@bp.get("/")
@login_required
def dashboard() -> str:
    """Render the admin dashboard with component health and stats."""
    settings = current_app.config["SETTINGS"]
    return render_template(
        "dashboard.html",
        stats=_dashboard_stats(),
        checks=_dashboard_checks(settings),
    )


@bp.get("/folders")
@login_required
def folders() -> str:
    """Render the library folders management page."""
    return _folders_response()


@bp.get("/folders/new")
@login_required
def folder_new() -> str:
    """Render the form to register a new library folder."""
    return render_template(
        "add_folder.html", path=request.args.get("path", ""), error=None
    )


@bp.get("/folders/browse")
@login_required
def folder_browse() -> str | tuple[str, int]:
    """Browse filesystem directories under browse_root."""
    settings = current_app.config["SETTINGS"]
    raw = request.args.get("path", "")
    result = browse_directory(raw, settings.browse_root)
    registered = [folder["path"] for folder in list_folders()]
    response = render_template(
        "browse_folders.html",
        browse=result,
        raw=raw,
        registered=registered,
        conflicts=_browse_conflicts(result, registered),
    )
    return response, (200 if result.ok else 400)


@bp.post("/folders")
@login_required
def folder_create() -> str | Response | tuple[str, int]:
    """Register and scan a new library folder."""
    raw = request.form.get("path", "")
    result = add_folder(raw)
    if not result.ok:
        return render_template("add_folder.html", path=raw, error=result.error), 400
    return _scan_and_respond(result.folder_id, result.path)


@bp.post("/folders/<int:folder_id>/scan")
@login_required
def folder_scan(folder_id: int) -> Response | tuple[str, int]:
    """Trigger a rescan of an existing library folder."""
    folder = get_folder(folder_id)
    if folder is None:
        abort(404)
    return _scan_and_respond(folder_id, folder["path"])


@bp.post("/folders/<int:folder_id>/delete")
@login_required
def folder_delete(folder_id: int) -> Response:
    """Unregister a library folder and prune its cache."""
    folder = get_folder(folder_id)
    if folder is None:
        abort(404)
    remove_folder(folder_id)
    prune_orphaned_cache()
    flash(
        f"Removed {folder['path']} from BookFlow. Library files were not changed.",
        "ok",
    )
    return redirect(url_for("admin.folders"))


@bp.get("/library")
@login_required
def library_index() -> Response:
    """The root list lives on the Library page; keep the old URL working."""
    return redirect(url_for("admin.folders"))


@bp.get("/library/<int:folder_id>")
@login_required
def library_tree(folder_id: int) -> str:
    """Show one directory level of a registered folder, from the index."""
    folder = get_folder(folder_id)
    if folder is None:
        abort(404)
    path = _clean_relative_path(request.args.get("path", ""))
    prefix = f"{path}/" if path else ""
    page = _page()
    offset = (page - 1) * PAGE_SIZE

    scope: list[ColumnElement[bool]] = [Book.folder_id == folder_id]
    if prefix:
        scope.append(Book.relative_path.startswith(prefix, autoescape=True))
    rest = func.substr(Book.relative_path, len(prefix) + 1)
    direct = func.instr(rest, "/") == 0

    with session_scope() as session:
        dirs = sorted(
            session.scalars(
                select(func.substr(rest, 1, func.instr(rest, "/") - 1))
                .where(*scope, func.instr(rest, "/") > 0)
                .distinct()
                .limit(FOLDER_SEGMENTS_LIMIT)
            ),
            key=str.casefold,
        )
        rows = session.execute(
            select(Book.id, Book.relative_path, Book.file_format, Book.file_size)
            .where(*scope, direct)
            .order_by(func.lower(Book.relative_path), Book.relative_path)
            .offset(offset)
            .limit(PAGE_SIZE + 1)
        ).all()

    has_next = len(rows) > PAGE_SIZE
    books = [
        {
            "id": row.id,
            "name": row.relative_path,
            "format": row.file_format,
            "size": row.file_size,
        }
        for row in rows[:PAGE_SIZE]
    ]

    crumbs: list[tuple[str, str]] = [(folder["name"], "")]
    walked = ""
    for part in path.split("/") if path else []:
        walked = f"{walked}/{part}" if walked else part
        crumbs.append((part, walked))

    return render_template(
        "library.html",
        folder=folder,
        path=path,
        crumbs=crumbs,
        parent=path.rsplit("/", 1)[0] if path else None,
        dirs=[
            (name, f"{path}/{name}" if path else name) for name in dirs
        ],
        books=books,
        page=page,
        has_prev=page > 1,
        has_next=has_next,
    )


@bp.get("/books/<int:book_id>/download")
@login_required
def book_download(book_id: int) -> Response:
    """Download a book file directly from the admin panel."""
    return send_book_response(book_file(book_id))


@bp.post("/cache/clear")
@login_required
def cache_clear() -> Response:
    """Clear cached optimized books and their database rows."""
    files, rows = clear_optimized_cache()
    flash(
        f"Cleared optimization cache: {files} file(s) and {rows} index "
        "row(s) removed. The next X3/X4 download rebuilds on demand.",
        "ok",
    )
    return redirect(url_for("admin.dashboard"))


@bp.get("/health")
@login_required
def health() -> str:
    """Render system health checks and library statistics."""
    settings = current_app.config["SETTINGS"]
    return render_template(
        "health.html",
        checks=run_checks(settings),
        stats=_health_stats(),
    )


@bp.get("/users")
@login_required
def users() -> str:
    """Render the user accounts list."""
    with session_scope() as db:
        user_rows = db.execute(
            select(
                User,
                func.count(Progression.id).label("progression_count"),
            )
            .outerjoin(Progression, Progression.user_id == User.id)
            .group_by(User.id)
            .order_by(User.username)
        ).all()
        user_list = [
            {
                "id": u.id,
                "username": u.username,
                "is_admin": u.is_admin,
                "created_at": u.created_at,
                "progression_count": prog_count,
            }
            for u, prog_count in user_rows
        ]
    return render_template(
        "users.html",
        users=user_list,
        current_user_id=session.get("user_id"),
    )


@bp.get("/users/new")
@login_required
def user_new() -> str:
    """Render the new user creation form."""
    return render_template(
        "add_user.html", username="", is_admin_checked=False, error=None
    )


@bp.post("/users")
@login_required
def user_create() -> str | Response | tuple[str, int]:
    """Create a new user account."""
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    is_admin = bool(request.form.get("is_admin"))

    if not username:
        return (
            render_template(
                "add_user.html",
                username=username,
                is_admin_checked=is_admin,
                error="Username is required.",
            ),
            400,
        )

    if not re.match(r"^[a-zA-Z0-9_.-]{3,64}$", username):
        return (
            render_template(
                "add_user.html",
                username=username,
                is_admin_checked=is_admin,
                error=(
                    "Username must be 3-64 characters and contain only "
                    "letters, digits, '.', '-', or '_'."
                ),
            ),
            400,
        )

    if len(password) < 8:
        return (
            render_template(
                "add_user.html",
                username=username,
                is_admin_checked=is_admin,
                error="Password must be at least 8 characters long.",
            ),
            400,
        )

    with session_scope() as db:
        existing = db.scalar(select(User).where(User.username == username))
        if existing is not None:
            return (
                render_template(
                    "add_user.html",
                    username=username,
                    is_admin_checked=is_admin,
                    error=f"Username '{username}' is already taken.",
                ),
                400,
            )

        hasher = PasswordHasher()
        new_user = User(
            username=username,
            password_hash=hasher.hash(password),
            is_admin=is_admin,
        )
        db.add(new_user)

    flash(f"User '{username}' created successfully.", "ok")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>/password")
@login_required
def user_change_password(user_id: int) -> Response:
    """Change or reset a user's password."""
    password = request.form.get("password") or ""
    if len(password) < 8:
        flash("Password must be at least 8 characters long.", "error")
        return redirect(url_for("admin.users"))

    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            abort(404)
        hasher = PasswordHasher()
        user.password_hash = hasher.hash(password)
        username = user.username

    flash(f"Password for user '{username}' has been updated.", "ok")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>/delete")
@login_required
def user_delete(user_id: int) -> Response:
    """Delete a user account."""
    current_uid = session.get("user_id")
    if current_uid is not None and current_uid == user_id:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin.users"))

    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            abort(404)

        if user.is_admin:
            admin_count = (
                db.scalar(
                    select(func.count(User.id)).where(User.is_admin.is_(True))
                )
                or 0
            )
            if admin_count <= 1:
                flash("Cannot delete the last remaining administrator.", "error")
                return redirect(url_for("admin.users"))

        username = user.username
        db.delete(user)

    flash(f"User '{username}' has been deleted.", "ok")
    return redirect(url_for("admin.users"))


def _folders_response() -> str:
    return render_template("folders.html", folders=list_folders())


def _clean_relative_path(raw: str) -> str:
    """Validate a tree path from the query string; refuse anything odd."""
    value = raw or ""
    if not value:
        return ""
    parts = value.split("/")
    if (
        value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or any(part in ("", ".", "..") for part in parts)
    ):
        abort(400, description="Invalid folder path.")
    return "/".join(parts)


def _page() -> int:
    """The requested page of a paginated admin list (mirrors the OPDS feeds)."""
    try:
        return max(int(request.args.get("page", "1")), 1)
    except (TypeError, ValueError):
        return 1


def _browse_conflicts(result: BrowseResult, registered: list[str]) -> set[str]:
    """Entry paths ``add_folder()`` would reject: nested in a registered folder."""
    if not result.ok:
        return set()
    conflicts: set[str] = set()
    for _name, entry_path in result.entries:
        if entry_path in registered:
            continue
        candidate = Path(entry_path)
        if any(
            candidate.is_relative_to(other) or other.is_relative_to(candidate)
            for other in map(Path, registered)
        ):
            conflicts.add(entry_path)
    return conflicts


def _scan_and_respond(
    folder_id: int | None, path: str | None
) -> Response | tuple[str, int]:
    """Scan a folder, flash the outcome, and answer with its response."""
    if folder_id is None or path is None:
        flash("Folder could not be scanned.", "error")
        return _folders_response(), 409
    extensions = current_app.config["SETTINGS"].scan_extensions
    try:
        result = scan_folder(folder_id, extensions)
    except ScanInProgress:
        flash("A scan for this folder is already in progress.", "error")
        return _folders_response(), 409
    prune_orphaned_cache()
    flash(
        _scan_message(path, result),
        "ok" if result.status == "ok" else "error",
    )
    return redirect(url_for("admin.folders"))


def _scan_message(path: str, result: ScanResult) -> str:
    message = (
        f"Scanned {path}: {result.added} added, {result.updated} updated, "
        f"{result.removed} removed, {result.unchanged} unchanged"
    )
    if result.errors:
        message += f", {len(result.errors)} error(s)"
    return f"{message} in {result.duration:.1f}s"


def _dashboard_checks(settings: Settings) -> list[HealthCheck]:
    return [
        check
        for check in run_checks(settings)
        if check.component in _DASHBOARD_COMPONENTS
    ]


def _health_stats() -> dict[str, object]:
    return _stats_or_default(library_statistics, _empty_health_stats)


def _dashboard_stats() -> dict[str, object]:
    return _stats_or_default(_query_stats, _empty_dashboard_stats)


def _stats_or_default(
    load: Callable[[], Mapping[str, object]],
    empty: Callable[[], Mapping[str, object]],
) -> dict[str, object]:
    """Run a stats query, or answer with its complete empty shape."""
    try:
        return dict(load())
    except OperationalError:
        return {**empty(), "db_error": _DB_UNINITIALIZED_MESSAGE}


def _empty_health_stats() -> HealthStats:
    return {
        "total_books": 0,
        "total_size": 0,
        "formats": [],
        "folders": [],
        "cache": {"x3": {"books": 0, "size": 0}, "x4": {"books": 0, "size": 0}},
        "progression": {"books": 0, "devices": []},
        "scanner": {"last_success": None, "errors": []},
        "users": {"total": 0, "admins": 0, "readers": 0},
    }


def _empty_dashboard_stats() -> DashboardStats:
    return {
        "books": 0,
        "folders": 0,
        "total_size": 0,
        "last_scan": None,
        "last_scan_duration": None,
        "scan_errors": 0,
        "cache": {"x3": 0, "x4": 0},
    }


def _query_stats() -> DashboardStats:
    with session_scope() as session:
        books = session.scalar(select(func.count(Book.id))) or 0
        folders = session.scalar(select(func.count(LibraryFolder.id))) or 0
        total_size = session.scalar(
            select(func.coalesce(func.sum(Book.file_size), 0))
        ) or 0
        scan_errors = (
            session.scalar(
                select(func.count(LibraryFolder.id)).where(
                    LibraryFolder.last_scan_status.notin_(["ok"])
                )
            )
            or 0
        )
        last_scan = session.scalar(select(func.max(LibraryFolder.last_scan_at)))
        latest = session.scalar(
            select(LibraryFolder)
            .where(LibraryFolder.last_scan_at.is_not(None))
            .order_by(LibraryFolder.last_scan_at.desc())
            .limit(1)
        )
        cache: dict[str, int] = {"x3": 0, "x4": 0}
        for row in session.execute(
            select(
                OptimizedBook.profile,
                func.coalesce(func.sum(OptimizedBook.optimized_size), 0),
            ).group_by(OptimizedBook.profile)
        ):
            cache[row[0]] = int(row[1])
    return {
        "books": int(books),
        "folders": int(folders),
        "total_size": int(total_size),
        "last_scan": last_scan,
        "last_scan_duration": latest.last_scan_duration if latest else None,
        "scan_errors": int(scan_errors),
        "cache": cache,
    }
