"""Per-book/profile locks preventing duplicate optimization work."""

from __future__ import annotations

import threading

_locks: dict[tuple[int, str], threading.Lock] = {}
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
