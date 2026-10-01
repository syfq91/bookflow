"""Admin dashboard, library folder management, and health pages."""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from bookflow.auth.decorators import login_required
from bookflow.auth.service import validate_csrf
from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import Book, LibraryFolder, OptimizedBook
from bookflow.health import HealthCheck, library_statistics, run_checks
from bookflow.library.browse import BrowseResult, browse_directory
from bookflow.library.paths import book_file
from bookflow.library.scanner import ScanInProgress, ScanResult, scan_folder
from bookflow.library.service import (
    add_folder,
    get_folder,
    list_folders,
    remove_folder,
)
from bookflow.opds.generator import book_mime_type
from bookflow.optimizer.service import clear_optimized_cache

bp = Blueprint("admin", __name__, url_prefix="/admin")

_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

_DASHBOARD_COMPONENTS = ("Database", "Library", "OPDS", "epubkit")


@bp.before_request
def _require_csrf_for_writes():
    """Every state-changing admin request must carry a valid CSRF token."""
    if request.method in _WRITE_METHODS and not validate_csrf():
        abort(400, description="Invalid or missing CSRF token")


@bp.get("/")
@login_required
def dashboard():
    settings = current_app.config["SETTINGS"]
    return render_template(
        "dashboard.html",
        stats=_dashboard_stats(),
        checks=_dashboard_checks(settings),
    )


@bp.get("/folders")
@login_required
def folders():
    return _folders_response()


@bp.get("/folders/new")
@login_required
def folder_new():
    return render_template(
        "add_folder.html", path=request.args.get("path", ""), error=None
    )


@bp.get("/folders/browse")
@login_required
def folder_browse():
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
def folder_create():
    raw = request.form.get("path", "")
    result = add_folder(raw)
    if not result.ok:
        return render_template("add_folder.html", path=raw, error=result.error), 400
    busy = _run_scan(result.folder_id, result.path)
    if busy:
        flash(busy, "error")
        return _folders_response(), 409
    return redirect(url_for("admin.folders"))


@bp.post("/folders/<int:folder_id>/scan")
@login_required
def folder_scan(folder_id: int):
    folder = get_folder(folder_id)
    if folder is None:
        abort(404)
    extensions = current_app.config["SETTINGS"].scan_extensions
    try:
        result = scan_folder(folder_id, extensions)
    except ScanInProgress:
        flash("A scan for this folder is already in progress.", "error")
        return _folders_response(), 409
    flash(
        _scan_message(folder["path"], result),
        "ok" if result.status == "ok" else "error",
    )
    return redirect(url_for("admin.folders"))


@bp.post("/folders/<int:folder_id>/delete")
@login_required
def folder_delete(folder_id: int):
    folder = get_folder(folder_id)
    if folder is None:
        abort(404)
    remove_folder(folder_id)
    flash(
        f"Removed {folder['path']} from BookFlow. Library files were not changed.",
        "ok",
    )
    return redirect(url_for("admin.folders"))


@bp.get("/library")
@login_required
def library_index():
    """The root list lives on the Library page; keep the old URL working."""
    return redirect(url_for("admin.folders"))


@bp.get("/library/<int:folder_id>")
@login_required
def library_tree(folder_id: int):
    """Show one directory level of a registered folder, from the index."""
    folder = get_folder(folder_id)
    if folder is None:
        abort(404)
    path = _clean_relative_path(request.args.get("path", ""))
    prefix = f"{path}/" if path else ""

    dirs: set[str] = set()
    books: list[dict[str, object]] = []
    with session_scope() as session:
        rows = session.scalars(
            select(Book).where(
                Book.folder_id == folder_id,
                Book.relative_path.startswith(prefix, autoescape=True),
            )
        ).all()
        for book in rows:
            rest = book.relative_path[len(prefix) :]
            if "/" in rest:
                dirs.add(rest.split("/", 1)[0])
            else:
                books.append(
                    {
                        "id": book.id,
                        "name": book.relative_path,
                        "format": book.file_format,
                        "size": book.file_size,
                    }
                )

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
            (name, f"{path}/{name}" if path else name)
            for name in sorted(dirs, key=str.casefold)
        ],
        books=sorted(books, key=lambda item: str(item["name"]).casefold()),
    )


@bp.get("/books/<int:book_id>/download")
@login_required
def book_download(book_id: int):
    target = book_file(book_id)
    return send_file(
        target,
        mimetype=book_mime_type(target.name),
        as_attachment=True,
        download_name=target.name,
        conditional=True,
    )


@bp.post("/cache/clear")
@login_required
def cache_clear():
    files, rows = clear_optimized_cache()
    flash(
        f"Cleared optimization cache: {files} file(s) and {rows} index "
        "row(s) removed. The next X3/X4 download rebuilds on demand.",
        "ok",
    )
    return redirect(url_for("admin.dashboard"))


@bp.get("/health")
@login_required
def health():
    settings = current_app.config["SETTINGS"]
    return render_template(
        "health.html",
        checks=run_checks(settings),
        stats=_health_stats(),
    )


def _folders_response():
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


def _run_scan(folder_id: int | None, path: str | None) -> str | None:
    """Scan a folder and flash the result; return a message when busy."""
    if folder_id is None or path is None:
        return "Folder could not be scanned."
    extensions = current_app.config["SETTINGS"].scan_extensions
    try:
        result = scan_folder(folder_id, extensions)
    except ScanInProgress:
        return f"Registered {path}; a scan is already running."
    flash(
        _scan_message(path, result),
        "ok" if result.status == "ok" else "error",
    )
    return None


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
    try:
        return library_statistics()
    except OperationalError:
        return {
            "db_error": (
                "Database not initialized. Run: uv run alembic upgrade head"
            )
        }


def _dashboard_stats() -> dict[str, object]:
    try:
        return _query_stats()
    except OperationalError:
        return {
            "books": 0,
            "folders": 0,
            "total_size": 0,
            "last_scan": None,
            "last_scan_duration": None,
            "scan_errors": 0,
            "cache": {"x3": 0, "x4": 0},
            "db_error": "Database not initialized. Run: uv run alembic upgrade head",
        }


def _query_stats() -> dict[str, object]:
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
        cache = {"x3": 0, "x4": 0}
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
