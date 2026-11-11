"""On-demand EPUB optimization with a validated per-profile cache."""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from flask import current_app
from sqlalchemy import delete, select

# SQLite-specific upsert: BookFlow is exclusively designed for SQLite, where
# INSERT ... ON CONFLICT DO UPDATE provides atomic single-statement caching.
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import DeviceProfile, OptimizedBook
from bookflow.optimizer.cbz2xtc import ConvertOptions, convert_cbz_to_xtc
from bookflow.optimizer.epubkit import ProcessingOptions, process_epub
from bookflow.optimizer.locks import profile_lock

logger = logging.getLogger(__name__)

PROFILES = ("x3", "x4")
SCRATCH_EXPIRY_SECONDS = 900


@dataclass(frozen=True, slots=True)
class DeviceProfileConfig:
    """Configured parameters for an optimization profile."""

    name: str
    display_name: str
    enabled: bool
    target_width: int
    target_height: int
    cbz_dither: bool
    cbz_split_spreads: bool
    cbz_rotate_spreads: bool
    epub_grayscale: bool
    epub_contrast_boost: bool
    epub_quality: int
    epub_remove_fonts: bool
    epub_remove_unused_css: bool
    epub_text_cleanup: bool


DEFAULT_PROFILES: dict[str, DeviceProfileConfig] = {
    "x3": DeviceProfileConfig(
        name="x3",
        display_name="Xteink X3",
        enabled=True,
        target_width=528,
        target_height=792,
        cbz_dither=True,
        cbz_split_spreads=True,
        cbz_rotate_spreads=True,
        epub_grayscale=True,
        epub_contrast_boost=True,
        epub_quality=70,
        epub_remove_fonts=True,
        epub_remove_unused_css=True,
        epub_text_cleanup=True,
    ),
    "x4": DeviceProfileConfig(
        name="x4",
        display_name="Xteink X4",
        enabled=True,
        target_width=480,
        target_height=800,
        cbz_dither=True,
        cbz_split_spreads=True,
        cbz_rotate_spreads=True,
        epub_grayscale=True,
        epub_contrast_boost=True,
        epub_quality=70,
        epub_remove_fonts=True,
        epub_remove_unused_css=True,
        epub_text_cleanup=True,
    ),
}


def get_device_profile_config(profile_name: str) -> DeviceProfileConfig:
    """Return the profile's configuration from the database, or sensible defaults."""
    try:
        with session_scope() as session:
            row = session.scalar(
                select(DeviceProfile).where(DeviceProfile.name == profile_name)
            )
            if row is not None:
                return DeviceProfileConfig(
                    name=row.name,
                    display_name=row.display_name,
                    enabled=row.enabled,
                    target_width=row.target_width,
                    target_height=row.target_height,
                    cbz_dither=row.cbz_dither,
                    cbz_split_spreads=row.cbz_split_spreads,
                    cbz_rotate_spreads=row.cbz_rotate_spreads,
                    epub_grayscale=row.epub_grayscale,
                    epub_contrast_boost=row.epub_contrast_boost,
                    epub_quality=row.epub_quality,
                    epub_remove_fonts=row.epub_remove_fonts,
                    epub_remove_unused_css=row.epub_remove_unused_css,
                    epub_text_cleanup=row.epub_text_cleanup,
                )
    except Exception:
        pass
    return DEFAULT_PROFILES.get(
        profile_name,
        DeviceProfileConfig(
            name=profile_name,
            display_name=profile_name.upper(),
            enabled=True,
            target_width=480,
            target_height=800,
            cbz_dither=True,
            cbz_split_spreads=True,
            cbz_rotate_spreads=True,
            epub_grayscale=True,
            epub_contrast_boost=True,
            epub_quality=70,
            epub_remove_fonts=True,
            epub_remove_unused_css=True,
            epub_text_cleanup=True,
        ),
    )

# Tracks temp files active in the current worker. In multi-worker deployments,
# each worker manages its own set; SCRATCH_EXPIRY_SECONDS provides a 15-minute
# grace period preventing sweepers from removing active files of sibling workers.
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
    cfg = get_device_profile_config(profile)
    if not cfg.enabled:
        raise OptimizationError(f"profile {profile} is disabled")
    stat = source.stat()
    source_mtime = int(stat.st_mtime * 1000)
    source_size = stat.st_size
    ext = ".xtc" if source.suffix.lower() == ".cbz" else ".epub"

    with profile_lock(book_id, profile):
        cache_file = _cache_file(book_id, profile, ext=ext)
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
                    entry.unlink(missing_ok=True)
                    files += 1
    with session_scope() as session:
        rows = int(session.execute(delete(OptimizedBook)).rowcount or 0)
    logger.info(
        "cleared optimization cache: %d file(s), %d row(s)", files, rows
    )
    return files, rows


def clear_profile_cache(
    profile: str, settings: Settings | None = None
) -> tuple[int, int]:
    """Delete every cached rendition and index row for a single profile."""
    if settings is None:
        settings = current_app.config["SETTINGS"]
    directory = settings.cache_dir / profile
    files = 0
    if directory.is_dir():
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
                    entry.unlink(missing_ok=True)
                    files += 1
    with session_scope() as session:
        rows = int(
            session.execute(
                delete(OptimizedBook).where(OptimizedBook.profile == profile)
            ).rowcount
            or 0
        )
    logger.info(
        "cleared profile %s cache: %d file(s), %d row(s)", profile, files, rows
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
    if path.suffix not in (".epub", ".xtc"):
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
    Files modified within ``SCRATCH_EXPIRY_SECONDS`` are preserved to spare
    in-flight generations running on other workers.
    """
    now = time.time()
    removed = 0
    for entry in sorted(directory.iterdir()):
        with _scratch_guard:
            if entry in _scratch_running:
                continue
        try:
            if now - entry.stat().st_mtime < SCRATCH_EXPIRY_SECONDS:
                continue
        except OSError:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        removed += 1
    return removed


def _cache_file(book_id: int, profile: str, ext: str = ".epub") -> Path:
    settings = current_app.config["SETTINGS"]
    directory = (
        settings.x3_cache_dir if profile == "x3" else settings.x4_cache_dir
    )
    return directory / f"{book_id}{ext}"


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
    tmp_file = tmp_dir / f"{uuid.uuid4().hex}{cache_file.suffix}"
    cfg = get_device_profile_config(profile)
    with _scratch_guard:
        _scratch_running.add(tmp_file)
    try:
        suffix = source.suffix.lower()
        if suffix == ".epub":
            report = process_epub(
                str(source),
                str(tmp_file),
                options=ProcessingOptions(
                    device=profile,
                    grayscale=cfg.epub_grayscale,
                    contrast_boost=cfg.epub_contrast_boost,
                    quality=cfg.epub_quality,
                    remove_fonts=cfg.epub_remove_fonts,
                    remove_unused_css=cfg.epub_remove_unused_css,
                    text_cleanup=cfg.epub_text_cleanup,
                ),
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
        elif suffix == ".cbz":
            opts = ConvertOptions(
                target_width=cfg.target_width,
                target_height=cfg.target_height,
                dither=cfg.cbz_dither,
                split_spreads=cfg.cbz_split_spreads,
                rotate_spreads=cfg.cbz_rotate_spreads,
            )
            success, error = convert_cbz_to_xtc(source, tmp_file, options=opts)
            if not success:
                logger.warning(
                    "CBZ optimization failed for book_id=%d source=%s profile=%s: %s",
                    book_id,
                    source.name,
                    profile,
                    error or "unknown error",
                )
                raise OptimizationError(error or "optimization failed")
        else:
            raise OptimizationError(f"unsupported format: {suffix}")

        if not tmp_file.is_file() or tmp_file.stat().st_size == 0:
            raise OptimizationError("optimizer produced no output")
        tmp_file.replace(cache_file)
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
    optimized_size = cache_file.stat().st_size
    stmt = (
        sqlite_insert(OptimizedBook)
        .values(
            book_id=book_id,
            profile=profile,
            source_mtime=source_mtime,
            source_size=source_size,
            optimized_size=optimized_size,
        )
        .on_conflict_do_update(
            index_elements=["book_id", "profile"],
            set_={
                "source_mtime": source_mtime,
                "source_size": source_size,
                "optimized_size": optimized_size,
            },
        )
    )
    with session_scope() as session:
        session.execute(stmt)
