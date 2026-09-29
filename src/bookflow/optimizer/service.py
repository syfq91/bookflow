"""On-demand EPUB optimization with a validated per-profile cache."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from flask import current_app
from sqlalchemy import select

from bookflow.database.database import session_scope
from bookflow.database.models import OptimizedBook
from bookflow.optimizer.epubkit import ProcessingOptions, process_epub
from bookflow.optimizer.locks import profile_lock

logger = logging.getLogger(__name__)

PROFILES = ("x3", "x4")


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
        _generate(source, cache_file, profile)
        _record(book_id, profile, cache_file, source_mtime, source_size)
        return cache_file


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


def _generate(source: Path, cache_file: Path, profile: str) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = cache_file.parent / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / f"{uuid.uuid4().hex}.epub"
    try:
        report = process_epub(
            str(source),
            str(tmp_file),
            options=ProcessingOptions(device=profile),
        )
        if not report.success:
            logger.warning(
                "optimization failed for %s profile: %s",
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
        logger.warning("optimization failed for %s profile: %s", profile, exc)
        raise OptimizationError("optimization failed") from exc


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
        row.optimized_path = str(cache_file)
        row.optimized_size = cache_file.stat().st_size
