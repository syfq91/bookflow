"""Authentication services: password verification, rate limiting, and CSRF."""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import request, session

ADMIN_SESSION_KEY = "admin"
CSRF_SESSION_KEY = "csrf_token"

DEFAULT_MAX_FAILURES = 5
DEFAULT_WINDOW_SECONDS = 600.0


class PasswordVerifier:
    """Verify logins against a single admin username/password pair.

    The password is hashed with Argon2id at construction time; only the hash
    is retained. Plaintext credentials are never stored in the database.
    """

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._configured = bool(password)
        self._hash = PasswordHasher().hash(password) if password else ""

    @property
    def configured(self) -> bool:
        """Whether an admin password has been supplied."""
        return self._configured

    def verify(self, username: str, password: str) -> bool:
        """Return True when both username and password match."""
        if not self._configured:
            return False
        expected = self._username.encode("utf-8")
        actual = username.encode("utf-8")
        if not hmac.compare_digest(expected, actual):
            return False
        try:
            return PasswordHasher().verify(self._hash, password)
        except (InvalidHashError, VerificationError):
            return False


class LoginRateLimiter:
    """Sliding-window failure counter for login attempts.

    State is kept in memory only: counters reset when the process restarts.
    """

    def __init__(
        self,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def is_blocked(self, key: tuple[str, str]) -> bool:
        """Return True when the key has too many recent failures."""
        now = self._clock()
        with self._lock:
            return len(self._prune(key, now)) >= self._max_failures

    def record_failure(self, key: tuple[str, str]) -> None:
        """Count a failed attempt against the key."""
        now = self._clock()
        with self._lock:
            failures = self._prune(key, now)
            failures.append(now)
            self._failures[key] = failures

    def reset(self, key: tuple[str, str]) -> None:
        """Forget previous failures for the key (called after a success)."""
        with self._lock:
            self._failures.pop(key, None)

    def _prune(self, key: tuple[str, str], now: float) -> deque[float]:
        """Return the key's in-window failures. Caller must hold the lock."""
        failures = self._failures.get(key)
        if failures is None:
            return deque()
        cutoff = now - self._window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            self._failures.pop(key, None)
        return failures


def ensure_csrf_token() -> str:
    """Return the session's CSRF token, creating one when missing."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf() -> bool:
    """Check the submitted CSRF token against the session token."""
    expected = session.get(CSRF_SESSION_KEY)
    submitted = request.form.get("csrf_token") or ""
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)


def safe_next_target(target: str | None) -> str | None:
    """Return an internal redirect target, or None when the target is unsafe.

    Only same-origin absolute-path targets are allowed, which rejects
    external hosts, protocol-relative URLs, and backslash tricks.
    """
    if not target:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target:
        return None
    return target
