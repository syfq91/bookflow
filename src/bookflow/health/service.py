"""Component health checks and library statistics for the admin UI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from flask import current_app
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import (
    Book,
    LibraryFolder,
    OptimizedBook,
    Progression,
)


@dataclass(frozen=True)
class HealthCheck:
    """Status of one application component."""

    component: str
    status: str  # "ok" | "warn" | "error"
    detail: str


class FormatStats(TypedDict):
    format: str
    books: int
    size: int


class FolderStats(TypedDict):
    name: str
    path: str
    books: int
    size: int


class CacheProfile(TypedDict):
    books: int
    size: int


class ProgressionStats(TypedDict):
    books: int
    devices: list[str]


class LastSuccess(TypedDict):
    folder: str
    at: datetime | None
    duration: float | None


class ScanError(TypedDict):
    folder: str
    message: str


class ScannerStats(TypedDict):
    last_success: LastSuccess | None
    errors: list[ScanError]


class HealthStats(TypedDict):
    """Shape of the ``stats`` object the health page renders.

    ``db_error`` is added by the admin view when the index has not been
    migrated yet; every other key is always present, on both the queried
    and the empty fallback shape.
    """

    total_books: int
    total_size: int
    formats: list[FormatStats]
    folders: list[FolderStats]
    cache: dict[str, CacheProfile]
    progression: ProgressionStats
    scanner: ScannerStats
    db_error: NotRequired[str]


def run_checks(settings: Settings) -> list[HealthCheck]:
    """Run every component check, in dashboard order."""
    return [
        _database_check(),
        _library_check(),
        _cache_check(settings),
        _optimizer_check(),
        _opds_check(),
    ]


def library_statistics() -> HealthStats:
    """Aggregate index statistics from the database (no filesystem walk)."""
    with session_scope() as session:
        total_books = int(session.scalar(select(func.count(Book.id))) or 0)
        total_size = int(
            session.scalar(select(func.coalesce(func.sum(Book.file_size), 0)))
            or 0
        )

        formats: list[FormatStats] = [
            {
                "format": row[0] or "unknown",
                "books": int(row[1]),
                "size": int(row[2]),
            }
            for row in session.execute(
                select(
                    Book.file_format,
                    func.count(Book.id),
                    func.coalesce(func.sum(Book.file_size), 0),
                )
                .group_by(Book.file_format)
                .order_by(func.count(Book.id).desc())
            )
        ]

        folders: list[FolderStats] = [
            {
                "name": row[0],
                "path": row[1],
                "books": int(row[2]),
                "size": int(row[3]),
            }
            for row in session.execute(
                select(
                    LibraryFolder.name,
                    LibraryFolder.path,
                    func.count(Book.id),
                    func.coalesce(func.sum(Book.file_size), 0),
                )
                .outerjoin(Book, Book.folder_id == LibraryFolder.id)
                .group_by(
                    LibraryFolder.id,
                    LibraryFolder.name,
                    LibraryFolder.path,
                )
                .order_by(LibraryFolder.path)
            )
        ]

        cache: dict[str, CacheProfile] = {
            "x3": {"books": 0, "size": 0},
            "x4": {"books": 0, "size": 0},
        }
        for row in session.execute(
            select(
                OptimizedBook.profile,
                func.count(OptimizedBook.id),
                func.coalesce(func.sum(OptimizedBook.optimized_size), 0),
            ).group_by(OptimizedBook.profile)
        ):
            cache[row[0]] = {"books": int(row[1]), "size": int(row[2])}

        progression: ProgressionStats = {
            "books": int(session.scalar(select(func.count(Progression.id))) or 0),
            "devices": sorted(
                {
                    name
                    for name in session.scalars(
                        select(Progression.device_name)
                    )
                    if name
                }
            ),
        }

        errors: list[ScanError] = [
            {"folder": row[0], "message": row[2] or (row[1] or "scan failed")}
            for row in session.execute(
                select(
                    LibraryFolder.name,
                    LibraryFolder.last_scan_status,
                    LibraryFolder.last_scan_error,
                ).where(
                    LibraryFolder.last_scan_status.is_not(None),
                    LibraryFolder.last_scan_status != "ok",
                )
            )
        ]

        last_ok = session.scalar(
            select(LibraryFolder)
            .where(
                LibraryFolder.last_scan_status == "ok",
                LibraryFolder.last_scan_at.is_not(None),
            )
            .order_by(LibraryFolder.last_scan_at.desc())
            .limit(1)
        )
        last_success: LastSuccess | None = None
        if last_ok is not None:
            last_success = {
                "folder": last_ok.name,
                "at": last_ok.last_scan_at,
                "duration": last_ok.last_scan_duration,
            }

    return {
        "total_books": total_books,
        "total_size": total_size,
        "formats": formats,
        "folders": folders,
        "cache": cache,
        "progression": progression,
        "scanner": {"last_success": last_success, "errors": errors},
    }


def _database_check() -> HealthCheck:
    try:
        with session_scope() as session:
            session.execute(text("SELECT count(*) FROM library_folders"))
    except SQLAlchemyError as exc:
        return HealthCheck("Database", "error", f"Query failed: {exc}")
    return HealthCheck("Database", "ok", "Connection and query succeeded.")


def _library_check() -> HealthCheck:
    try:
        with session_scope() as session:
            paths = list(session.scalars(select(LibraryFolder.path)))
    except SQLAlchemyError as exc:
        return HealthCheck("Library", "error", f"Could not list folders: {exc}")
    if not paths:
        return HealthCheck("Library", "warn", "No library folders registered.")
    broken = [path for path in paths if not _folder_usable(path)]
    if broken:
        return HealthCheck(
            "Library",
            "error",
            "Missing or unreadable: " + ", ".join(broken),
        )
    return HealthCheck(
        "Library", "ok", f"{len(paths)} folder(s) accessible."
    )


def _folder_usable(path: str) -> bool:
    target = Path(path)
    return target.is_dir() and os.access(target, os.R_OK | os.X_OK)


def _cache_check(settings: Settings) -> HealthCheck:
    cache_dir = settings.cache_dir
    if not cache_dir.is_dir():
        return HealthCheck("Cache", "error", f"{cache_dir} does not exist.")
    if not os.access(cache_dir, os.W_OK):
        return HealthCheck("Cache", "error", f"{cache_dir} is not writable.")
    return HealthCheck("Cache", "ok", f"{cache_dir} is writable.")


def _optimizer_check() -> HealthCheck:
    try:
        # Importability only — optimization itself runs solely inside
        # optimize_book() (invariant 2), never from a health check.
        from bookflow.optimizer.epubkit import process_epub  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on environment
        return HealthCheck("epubkit", "error", f"Pipeline unavailable: {exc}")
    return HealthCheck(
        "epubkit", "ok", "EPUB optimization pipeline importable."
    )


def _opds_check() -> HealthCheck:
    rules = [
        rule
        for rule in current_app.url_map.iter_rules()
        if rule.endpoint.startswith("opds.")
    ]
    if not rules:
        return HealthCheck("OPDS", "error", "No catalog routes registered.")
    return HealthCheck(
        "OPDS", "ok", f"{len(rules)} catalog routes registered."
    )
