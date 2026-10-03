"""Per-book locks serializing work that must not run twice at once.

Registries are process-wide: each gunicorn worker keeps its own, which
matches the single-user scope of the application.
"""

from __future__ import annotations

import threading

_locks: dict[tuple[int, str], threading.Lock] = {}
_progression_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def profile_lock(book_id: int, profile: str) -> threading.Lock:
    """Return the lock for one book/profile, creating it on first use."""
    key = (book_id, profile)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def progression_lock(book_id: int) -> threading.Lock:
    """Return the lock guarding one book's progression read→write."""
    with _locks_guard:
        lock = _progression_locks.get(book_id)
        if lock is None:
            lock = threading.Lock()
            _progression_locks[book_id] = lock
        return lock
