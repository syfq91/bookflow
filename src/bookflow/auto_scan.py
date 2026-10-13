"""Periodic background rescanning of every registered library folder.

One daemon thread per process re-indexes the folders on an interval
(``OPDS_AUTO_SCAN_INTERVAL``, default hourly, ``0`` disables it). The
scanner itself is unchanged — it still only reads the library — and the
same per-folder lock guards it, so a manual **Scan** and an automatic one
never run together. ``data/.auto_scan.lock`` additionally keeps several
gunicorn workers from walking the library in parallel.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from flask import Flask
from sqlalchemy import select

from bookflow.database.database import session_scope
from bookflow.database.models import LibraryFolder
from bookflow.library.scanner import ScanInProgress, scan_folder
from bookflow.optimizer.service import prune_orphaned_cache

logger = logging.getLogger(__name__)

STARTUP_DELAY = 5.0
LOCK_NAME = ".auto_scan.lock"


class AutoScanner:
    """Background thread rescanning every library folder on a timer."""

    def __init__(self, app: Flask, interval: int) -> None:
        self.app = app
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="auto-scan", daemon=True
        )

    @property
    def running(self) -> bool:
        """Whether the background thread is still alive."""
        return self._thread.is_alive()

    @property
    def lock_path(self) -> Path:
        return self.app.config["SETTINGS"].data_dir / LOCK_NAME

    def start(self) -> None:
        """Start the background thread."""
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the thread to exit and wait briefly for it to finish."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _run(self) -> None:
        # Scan once shortly after boot, so files added while the server was
        # down show up, then repeat every interval. A short interval shortens
        # the startup delay too, which keeps the cadence observable.
        if self._stop.wait(min(STARTUP_DELAY, self.interval)):
            return
        while not self._stop.is_set():
            # A failed cycle — a database still being migrated, a folder
            # disappearing — must not kill the thread; the next one retries.
            try:
                self.run_once()
            except Exception:
                logger.exception("auto scan cycle failed")
            if self._stop.wait(self.interval):
                return

    def run_once(self) -> None:
        """Scan every folder once, unless another worker is already scanning."""
        with _single_worker(self.lock_path) as acquired:
            if not acquired:
                logger.debug("auto scan skipped: another worker holds the lock")
                return
            self._scan_all()

    def _scan_all(self) -> None:
        extensions = self.app.config["SETTINGS"].scan_extensions
        with session_scope() as session:
            folder_ids = list(session.scalars(select(LibraryFolder.id)))
        scanned = 0
        for folder_id in folder_ids:
            try:
                scan_folder(folder_id, extensions)
                scanned += 1
            except ScanInProgress:
                continue
            except Exception:
                logger.exception("auto scan failed for folder %s", folder_id)
        if scanned:
            self._prune_cache()
        logger.info("auto scan finished: %d folder(s)", scanned)

    def _prune_cache(self) -> None:
        """Drop cache files whose books the scan just removed from the index."""
        with self.app.app_context():
            try:
                prune_orphaned_cache()
            except Exception:
                logger.exception("auto scan could not prune the cache")


@contextmanager
def _single_worker(path: Path) -> Iterator[bool]:
    """Hold an exclusive advisory lock on ``path`` for the scan's duration.

    Yields ``False`` when another process already holds it, so the scan is
    skipped rather than duplicated. Non-Unix platforms have no ``fcntl``;
    there the scan simply runs.
    """
    try:
        import fcntl
    except ImportError:
        yield True
        return

    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
