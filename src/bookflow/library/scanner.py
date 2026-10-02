"""Filesystem scanner: index library folders into the SQLite book index.

The scanner only ever reads the library. It never creates, modifies or
deletes files inside a configured library folder.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from bookflow.database.database import session_scope
from bookflow.database.models import Book, LibraryFolder
from bookflow.library.metadata import extract_metadata
from bookflow.library.paths import resolve_readable_dir

logger = logging.getLogger(__name__)

_MAX_REPORTED_ERRORS = 10


class ScanInProgress(Exception):
    """Raised when a scan is requested while one is already running."""

    def __init__(self, folder_id: int) -> None:
        super().__init__(f"scan already running for folder {folder_id}")
        self.folder_id = folder_id


@dataclass
class ScanResult:
    """Statistics for a single completed scan."""

    folder_id: int
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    status: str = "ok"
    duration: float = 0.0

    @property
    def error_summary(self) -> str | None:
        if not self.errors:
            return None
        shown = "; ".join(self.errors[:_MAX_REPORTED_ERRORS])
        remaining = len(self.errors) - _MAX_REPORTED_ERRORS
        if remaining > 0:
            shown += f" (+{remaining} more)"
        return shown


_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def folder_lock(folder_id: int) -> threading.Lock:
    """Return the per-folder scan lock, creating it on first use."""
    with _locks_guard:
        lock = _locks.get(folder_id)
        if lock is None:
            lock = threading.Lock()
            _locks[folder_id] = lock
        return lock


def scan_folder(folder_id: int, extensions: tuple[str, ...]) -> ScanResult:
    """Scan one library folder synchronously and reconcile the index.

    Raises ScanInProgress when another scan of the same folder is running.
    """
    lock = folder_lock(folder_id)
    if not lock.acquire(blocking=False):
        raise ScanInProgress(folder_id)
    try:
        return _scan(folder_id, extensions)
    finally:
        lock.release()


def _snapshot(folder_id: int) -> tuple[Path, dict[str, tuple[int, datetime | None]]]:
    """Read the folder path and how the index currently sees its files."""
    with session_scope() as session:
        folder = session.get(LibraryFolder, folder_id)
        if folder is None:
            raise LookupError(f"unknown library folder {folder_id}")
        rows = session.execute(
            select(Book.relative_path, Book.file_size, Book.file_modified_at).where(
                Book.folder_id == folder_id
            )
        )
        known = {path: (size, modified) for path, size, modified in rows}
    return Path(folder.path), known


def _scan(folder_id: int, extensions: tuple[str, ...]) -> ScanResult:
    started = time.monotonic()
    result = ScanResult(folder_id=folder_id)
    root, known = _snapshot(folder_id)

    # Filesystem work happens with no session open, so a scan never holds a
    # pooled connection across the walk or the metadata parsing.
    found: dict[str, tuple[int, float]] | None = None
    fields: dict[str, dict[str, object]] = {}
    unavailable = _unavailable_reason(root)
    if unavailable is None:
        found, walk_errors = _walk(root, extensions)
        result.errors.extend(walk_errors)
        if result.errors:
            result.status = "partial"
        fields = _changed_fields(root, found, known)
    else:
        result.status = "error"
        result.errors.append(unavailable)

    with session_scope() as session:
        folder = session.get(LibraryFolder, folder_id)
        if folder is None:
            raise LookupError(f"unknown library folder {folder_id}")
        if found is not None:
            _reconcile(session, folder_id, root, found, fields, result)

        result.duration = time.monotonic() - started
        folder.last_scan_at = _utcnow()
        folder.last_scan_duration = result.duration
        folder.last_scan_status = result.status
        folder.last_scan_error = result.error_summary

    logger.info(
        "scan folder=%s status=%s added=%d updated=%d "
        "removed=%d unchanged=%d errors=%d",
        folder_id,
        result.status,
        result.added,
        result.updated,
        result.removed,
        result.unchanged,
        len(result.errors),
    )
    return result


def _changed_fields(
    root: Path,
    found: dict[str, tuple[int, float]],
    known: dict[str, tuple[int, datetime | None]],
) -> dict[str, dict[str, object]]:
    """Parse metadata for every file that is new or changed since ``known``."""
    fields: dict[str, dict[str, object]] = {}
    for relative_path, (size, mtime) in sorted(found.items()):
        modified = _mtime_to_datetime(mtime)
        if known.get(relative_path) != (size, modified):
            fields[relative_path] = _fields(root / relative_path, size, modified)
    return fields


def _reconcile(
    session,
    folder_id: int,
    root: Path,
    found: dict[str, tuple[int, float]],
    fields: dict[str, dict[str, object]],
    result: ScanResult,
) -> None:
    existing = {
        book.relative_path: book
        for book in session.scalars(
            select(Book).where(Book.folder_id == folder_id)
        )
    }

    for relative_path, (size, mtime) in sorted(found.items()):
        book = existing.get(relative_path)
        modified = _mtime_to_datetime(mtime)
        if (
            book is not None
            and book.file_size == size
            and book.file_modified_at == modified
        ):
            result.unchanged += 1
            continue

        value = fields.get(relative_path)
        if value is None:
            # Only folder removal can drop a row mid-scan, and it drops the
            # folder too; parse here rather than lose the file's metadata.
            value = _fields(root / relative_path, size, modified)
        if book is None:
            session.add(
                Book(folder_id=folder_id, relative_path=relative_path, **value)
            )
            result.added += 1
        else:
            for key, item in value.items():
                setattr(book, key, item)
            result.updated += 1

    for relative_path in existing.keys() - found.keys():
        session.delete(existing[relative_path])
        result.removed += 1


def _fields(path: Path, size: int, modified: datetime) -> dict[str, object]:
    meta = extract_metadata(path)
    return {
        "title": meta.title,
        "authors": meta.authors,
        "publisher": meta.publisher,
        "language": meta.language,
        "isbn": meta.isbn,
        "description": meta.description,
        "series": meta.series,
        "series_index": meta.series_index,
        "file_format": meta.file_format,
        "file_size": size,
        "file_modified_at": modified,
    }


def _walk(
    root: Path, extensions: tuple[str, ...]
) -> tuple[dict[str, tuple[int, float]], list[str]]:
    allowed = {ext.lower() for ext in extensions}
    found: dict[str, tuple[int, float]] = {}
    errors: list[str] = []

    def onerror(exc: OSError) -> None:
        location = getattr(exc, "filename", None) or root
        errors.append(f"{location}: {exc.strerror or exc}")

    for dirpath, _dirnames, filenames in os.walk(
        root, onerror=onerror, followlinks=False
    ):
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() not in allowed:
                continue
            try:
                if path.is_symlink():
                    continue
                stat = path.stat()
            except OSError as exc:
                errors.append(f"{path}: {exc.strerror or exc}")
                continue
            found[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime)
    return found, errors


def _unavailable_reason(root: Path) -> str | None:
    """Why ``root`` cannot be scanned, or None when it is usable."""
    _path, error = resolve_readable_dir(str(root))
    if error is None:
        return None
    return f"Folder is unavailable: {root} ({error})"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _mtime_to_datetime(mtime: float) -> datetime:
    return datetime.fromtimestamp(mtime, tz=UTC).replace(tzinfo=None)
