"""Admin dashboard and placeholder pages."""

from __future__ import annotations

from flask import Blueprint, render_template
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from bookflow.auth.decorators import login_required
from bookflow.database.database import session_scope
from bookflow.database.models import Book, LibraryFolder

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _dashboard_stats() -> dict[str, object]:
    try:
        return _query_stats()
    except OperationalError:
        return {
            "books": 0,
            "folders": 0,
            "total_size": 0,
            "last_scan": None,
            "scan_errors": 0,
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
                    LibraryFolder.last_scan_status == "error"
                )
            )
            or 0
        )
        last_scan = session.scalar(select(func.max(LibraryFolder.last_scan_at)))
    return {
        "books": int(books),
        "folders": int(folders),
        "total_size": int(total_size),
        "last_scan": last_scan,
        "scan_errors": int(scan_errors),
    }


@bp.get("/")
@login_required
def dashboard():
    return render_template("dashboard.html", stats=_dashboard_stats())


@bp.get("/folders")
@login_required
def folders():
    return render_template("folders.html")


@bp.get("/health")
@login_required
def health():
    return render_template("health.html")
