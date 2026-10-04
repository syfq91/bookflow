"""Per-book locks serializing work that must not run twice at once.

Registries are process-wide: each gunicorn worker keeps its own.
"""

from __future__ import annotations

import threading

_locks: dict[tuple[int, str], threading.Lock] = {}
_progression_locks: dict[tuple[int, int], threading.Lock] = {}
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


def progression_lock(
    user_id_or_book_id: int, book_id: int | None = None
) -> threading.Lock:
    """Return the lock guarding one user/book progression read→write."""
    key = (
        (1, user_id_or_book_id)
        if book_id is None
        else (user_id_or_book_id, book_id)
    )
    with _locks_guard:
        lock = _progression_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _progression_locks[key] = lock
        return lock
