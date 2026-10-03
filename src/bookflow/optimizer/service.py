"""On-demand EPUB optimization with a validated per-profile cache."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import uuid
from pathlib import Path

from flask import current_app
from sqlalchemy import delete, select

from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import OptimizedBook
from bookflow.optimizer.epubkit import ProcessingOptions, process_epub
from bookflow.optimizer.locks import profile_lock

logger = logging.getLogger(__name__)

PROFILES = ("x3", "x4")

_scratch_guard = threading.Lock()
_scratch_running: set[Path] = set()


class OptimizationError(RuntimeError):
    """Raised when a publication could not be optimized."""


def optimize_book(book_id: int, profile: str, source: Path) -> Path:
    """Return the cached optimized EPUB, building it on demand.

    The cache entry is invalidated when the source file's size or
    modification time changes. Concurrent callers for the same book/profile
    serialize on a shared lock; the loser reads the freshly cached result.
    """
    if profile not in PROFILES:
        raise OptimizationError(f"unknown profile: {profile}")
    stat = source.stat()
    source_mtime = int(stat.st_mtime * 1000)
    source_size = stat.st_size

    with profile_lock(book_id, profile):
        cache_file = _cache_file(book_id, profile)
        if _cache_is_valid(
            book_id, profile, cache_file, source_mtime, source_size
        ):
            return cache_file
        _generate(book_id, source, cache_file, profile)
        _record(book_id, profile, cache_file, source_mtime, source_size)
        return cache_file


def clear_optimized_cache() -> tuple[int, int]:
    """Delete every cached rendition and its index row.

    Only ``data/cache/optimized/`` is touched: source library files are
    never modified. Scratch files of a generation that is running right
    now are left in place (and the ``.tmp`` directory itself is never
    removed), so a concurrent download still finishes; stale scratch files
    are swept. Returns ``(files_removed, rows_removed)``.
    """
    settings = current_app.config["SETTINGS"]
    files = 0
    for profile, directory in _cache_dirs(settings):
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                files += _sweep_scratch(entry)
                continue
            book_id = _book_id_of(entry)
            if book_id is None:
                entry.unlink(missing_ok=True)
                files += 1
                continue
            with profile_lock(book_id, profile):
                if entry.is_file():
                    entry.unlink()
                    files += 1
    with session_scope() as session:
        rows = int(session.execute(delete(OptimizedBook)).rowcount or 0)
    logger.info(
        "cleared optimization cache: %d file(s), %d row(s)", files, rows
    )
    return files, rows


def prune_orphaned_cache() -> int:
    """Delete cached EPUBs whose ``optimized_books`` row is gone.

    Removing a book from the index drops its rows but used to leave the
    rendered EPUBs behind, so files accumulated until the next clear. A
    row-less file can never be served (it is a miss), so dropping it only
    discards work the cache would not reuse. Each file is removed under
    its profile lock, so a generation recording its row at that moment is
    never cut short. Returns the number of files removed.
    """
    settings = current_app.config["SETTINGS"]
    with session_scope() as session:
        known = {
            (book_id, profile)
            for book_id, profile in session.execute(
                select(OptimizedBook.book_id, OptimizedBook.profile)
            )
        }
    removed = 0
    for profile, directory in _cache_dirs(settings):
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                continue
            book_id = _book_id_of(entry)
            if book_id is None or (book_id, profile) in known:
                continue
            with profile_lock(book_id, profile):
                if _row_exists(book_id, profile):
                    continue
                entry.unlink(missing_ok=True)
                removed += 1
    if removed:
        logger.info("pruned %d orphaned cache file(s)", removed)
    return removed


def _cache_dirs(settings: Settings) -> tuple[tuple[str, Path], ...]:
    """The per-profile cache directories, paired with their profile name."""
    return (
        ("x3", settings.x3_cache_dir),
        ("x4", settings.x4_cache_dir),
    )


def _book_id_of(path: Path) -> int | None:
    """The book id a cache file name encodes, or None for anything else."""
    if path.suffix != ".epub":
        return None
    try:
        return int(path.stem)
    except ValueError:
        return None


def _row_exists(book_id: int, profile: str) -> bool:
    with session_scope() as session:
        return (
            session.scalar(
                select(OptimizedBook.id).where(
                    OptimizedBook.book_id == book_id,
                    OptimizedBook.profile == profile,
                )
            )
            is not None
        )


def _sweep_scratch(directory: Path) -> int:
    """Remove leftover scratch entries, sparing generations still running.

    The ``.tmp`` directory itself stays: a generation creates it and then
    claims its file, so removing it would race that window.
    """
    removed = 0
    for entry in sorted(directory.iterdir()):
        with _scratch_guard:
            if entry in _scratch_running:
                continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        removed += 1
    return removed


def _cache_file(book_id: int, profile: str) -> Path:
    settings = current_app.config["SETTINGS"]
    directory = (
        settings.x3_cache_dir if profile == "x3" else settings.x4_cache_dir
    )
    return directory / f"{book_id}.epub"


def _cache_is_valid(
    book_id: int,
    profile: str,
    cache_file: Path,
    source_mtime: int,
    source_size: int,
) -> bool:
    if not cache_file.is_file():
        return False
    with session_scope() as session:
        row = session.scalar(
            select(OptimizedBook).where(
                OptimizedBook.book_id == book_id,
                OptimizedBook.profile == profile,
            )
        )
        if row is None:
            return False
        return (
            row.source_mtime == source_mtime
            and row.source_size == source_size
            and row.optimized_size is not None
            and row.optimized_size == cache_file.stat().st_size
        )


def _generate(
    book_id: int, source: Path, cache_file: Path, profile: str
) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = cache_file.parent / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / f"{uuid.uuid4().hex}.epub"
    with _scratch_guard:
        _scratch_running.add(tmp_file)
    try:
        report = process_epub(
            str(source),
            str(tmp_file),
            options=ProcessingOptions(device=profile),
        )
        if not report.success:
            logger.warning(
                "optimization failed for book_id=%d source=%s profile=%s: %s",
                book_id,
                source.name,
                profile,
                report.error or "unknown error",
            )
            raise OptimizationError(report.error or "optimization failed")
        if not tmp_file.is_file() or tmp_file.stat().st_size == 0:
            raise OptimizationError("optimizer produced no output")
        os.replace(tmp_file, cache_file)
    except OptimizationError:
        tmp_file.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_file.unlink(missing_ok=True)
        logger.exception(
            "optimization failed for book_id=%d source=%s profile=%s",
            book_id,
            source.name,
            profile,
        )
        raise OptimizationError("optimization failed") from exc
    finally:
        with _scratch_guard:
            _scratch_running.discard(tmp_file)


def _record(
    book_id: int,
    profile: str,
    cache_file: Path,
    source_mtime: int,
    source_size: int,
) -> None:
    with session_scope() as session:
        row = session.scalar(
            select(OptimizedBook).where(
                OptimizedBook.book_id == book_id,
                OptimizedBook.profile == profile,
            )
        )
        if row is None:
            row = OptimizedBook(book_id=book_id, profile=profile)
            session.add(row)
        row.source_mtime = source_mtime
        row.source_size = source_size
        row.optimized_size = cache_file.stat().st_size
