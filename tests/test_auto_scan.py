from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from bookflow.auto_scan import AutoScanner
from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import Book, LibraryFolder
from bookflow.library.service import add_folder
from factories import make_epub

TIMEOUT = 8.0


@pytest.fixture
def auto_settings(settings: Settings) -> Settings:
    """One second between scans, so a test can observe a cycle."""
    return replace(settings, auto_scan_interval=1)


@pytest.fixture
def auto_app(build_app, auto_settings: Settings):
    application = build_app(auto_settings)
    scanner = application.extensions["auto_scanner"]
    yield application
    scanner.stop()


@pytest.fixture
def off_app(build_app, settings: Settings):
    """A migrated app built with the conftest default: auto-scan off."""
    application = build_app(settings)
    assert "auto_scanner" not in application.extensions
    yield application


def _add_folder(root: Path) -> int:
    result = add_folder(str(root))
    assert result.ok, result.error
    assert result.folder_id is not None
    return result.folder_id


def _books() -> list[Book]:
    with session_scope() as session:
        return list(session.scalars(select(Book).order_by(Book.relative_path)))


def _last_scan(folder_id: int):
    with session_scope() as session:
        folder = session.get(LibraryFolder, folder_id)
        return None if folder is None else folder.last_scan_at


def _wait_for(predicate: Callable[[], bool], timeout: float = TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


# --- background thread ------------------------------------------------------


def test_auto_scan_indexes_files_added_after_startup(auto_app, root: Path) -> None:
    _add_folder(root)
    make_epub(root / "new.epub", title="New", authors=("A. Writer",))

    assert _wait_for(lambda: len(_books()) == 1)
    assert [book.relative_path for book in _books()] == ["new.epub"]
    assert _books()[0].title == "New"


def test_auto_scan_repeats_on_its_interval(auto_app, root: Path) -> None:
    folder_id = _add_folder(root)
    assert _wait_for(lambda: _last_scan(folder_id) is not None)

    make_epub(root / "later.epub", title="Later", authors=("A. Writer",))

    assert _wait_for(lambda: len(_books()) == 1)
    assert [book.relative_path for book in _books()] == ["later.epub"]


def test_stop_terminates_the_thread(auto_app) -> None:
    scanner = auto_app.extensions["auto_scanner"]

    assert scanner.running

    scanner.stop()

    assert not scanner.running


# --- disabled ---------------------------------------------------------------


def test_disabled_starts_no_thread_and_never_scans(off_app, root: Path) -> None:
    _add_folder(root)
    make_epub(root / "new.epub", title="New", authors=("A. Writer",))
    time.sleep(0.3)

    assert _books() == []


# --- run_once (the unit the thread calls) -----------------------------------


def test_run_once_indexes_every_folder(off_app, root: Path) -> None:
    _add_folder(root)
    make_epub(root / "one.epub", title="One", authors=("A. Writer",))

    AutoScanner(off_app, 3600).run_once()

    assert [book.relative_path for book in _books()] == ["one.epub"]


def test_run_once_is_skipped_when_another_worker_holds_the_lock(
    off_app, root: Path
) -> None:
    _add_folder(root)
    make_epub(root / "one.epub", title="One", authors=("A. Writer",))
    scanner = AutoScanner(off_app, 3600)
    scanner.lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(scanner.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scanner.run_once()
    finally:
        os.close(fd)

    assert _books() == []
