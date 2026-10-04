"""Authentication services: password verification, rate limiting, and CSRF."""

from __future__ import annotations

import contextlib
import hmac
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import request, session
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from bookflow.database.database import session_scope
from bookflow.database.models import User

ADMIN_SESSION_KEY = "admin"
USER_ID_SESSION_KEY = "user_id"
USERNAME_SESSION_KEY = "username"
CSRF_SESSION_KEY = "csrf_token"

DEFAULT_MAX_FAILURES = 5
DEFAULT_WINDOW_SECONDS = 600.0

DEFAULT_HASHER = PasswordHasher()

AUTH_CACHE_TTL_SECONDS = 60.0
AUTH_CACHE_MAX_ENTRIES = 256
RATE_LIMITER_MAX_KEYS = 5000


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Decoupled user context attached to request g."""

    id: int
    username: str
    is_admin: bool


class DatabasePasswordVerifier:
    """Verifies credentials in SQLite with timing attack mitigation."""

    def __init__(
        self,
        admin_username: str = "admin",
        admin_password: str = "",
        hasher: PasswordHasher | None = None,
    ) -> None:
        self._hasher = hasher or DEFAULT_HASHER
        self._admin_username = admin_username
        self._admin_password = admin_password
        self._dummy_hash = self._hasher.hash("bookflow-timing-defense")
        self._cache: dict[tuple[str, bytes], tuple[float, AuthenticatedUser]] = {}
        self._cache_lock = threading.Lock()
        self._cache_salt = secrets.token_bytes(16)

    @property
    def configured(self) -> bool:
        """Whether an admin password or at least one user account exists."""
        if self._admin_password:
            return True
        try:
            with session_scope() as session:
                return bool(session.scalar(select(func.count(User.id))))
        except SQLAlchemyError:
            return False

    def verify_user(self, username: str, password: str) -> AuthenticatedUser | None:
        """Verify username and password, returning an AuthenticatedUser if valid."""
        clean_username = username.strip() if username else ""
        if not clean_username or not password:
            with contextlib.suppress(Exception):
                self._hasher.verify(self._dummy_hash, "dummy")
            return None

        cache_key = (
            clean_username,
            hmac.new(self._cache_salt, password.encode("utf-8"), "sha256").digest(),
        )
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                cached_at, cached_user = cached
                if now - cached_at < AUTH_CACHE_TTL_SECONDS:
                    return cached_user
                self._cache.pop(cache_key, None)

        def cache_and_return(auth_user: AuthenticatedUser) -> AuthenticatedUser:
            with self._cache_lock:
                if len(self._cache) >= AUTH_CACHE_MAX_ENTRIES:
                    cutoff = now - AUTH_CACHE_TTL_SECONDS
                    self._cache = {
                        k: v for k, v in self._cache.items() if v[0] >= cutoff
                    }
                    if len(self._cache) >= AUTH_CACHE_MAX_ENTRIES:
                        self._cache.clear()
                self._cache[cache_key] = (now, auth_user)
            return auth_user

        # Try SQLite database
        try:
            with session_scope() as session:
                user = session.scalar(
                    select(User).where(User.username == clean_username)
                )
                if user is not None:
                    try:
                        self._hasher.verify(user.password_hash, password)
                        return cache_and_return(
                            AuthenticatedUser(
                                id=user.id,
                                username=user.username,
                                is_admin=user.is_admin,
                            )
                        )
                    except (InvalidHashError, VerificationError):
                        if (
                            user.is_admin
                            and self._admin_password
                            and hmac.compare_digest(
                                self._admin_username.encode(), clean_username.encode()
                            )
                            and hmac.compare_digest(
                                self._admin_password.encode(), password.encode()
                            )
                        ):
                            user.password_hash = self._hasher.hash(
                                self._admin_password
                            )
                            return cache_and_return(
                                AuthenticatedUser(
                                    id=user.id,
                                    username=user.username,
                                    is_admin=user.is_admin,
                                )
                            )
                        return None
        except SQLAlchemyError:
            pass

        # Fallback when database tables are missing
        if (
            self._admin_password
            and hmac.compare_digest(
                self._admin_username.encode(), clean_username.encode()
            )
            and hmac.compare_digest(
                self._admin_password.encode(), password.encode()
            )
        ):
            return cache_and_return(
                AuthenticatedUser(
                    id=1, username=clean_username, is_admin=True
                )
            )

        with contextlib.suppress(Exception):
            self._hasher.verify(self._dummy_hash, password)
        return None

    def invalidate_cache(self, username: str | None = None) -> None:
        """Invalidate cached credentials (e.g. after password change or deletion)."""
        with self._cache_lock:
            if username is None:
                self._cache.clear()
            else:
                self._cache = {
                    k: v for k, v in self._cache.items() if k[0] != username
                }

    def verify(self, username: str, password: str) -> bool:
        """Return True when both username and password match a valid user."""
        return self.verify_user(username, password) is not None


PasswordVerifier = DatabasePasswordVerifier


class LoginRateLimiter:
    """Sliding-window failure counter for login attempts.

    State is kept in memory only: counters reset when the process restarts.
    """

    def __init__(
        self,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        max_keys: int = RATE_LIMITER_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._max_keys = max_keys
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
            if len(self._failures) >= self._max_keys:
                self._sweep(now)
            failures = self._prune(key, now)
            failures.append(now)
            self._failures[key] = failures

    def reset(self, key: tuple[str, str]) -> None:
        """Forget previous failures for the key (called after a success)."""
        with self._lock:
            self._failures.pop(key, None)

    def _sweep(self, now: float) -> None:
        """Evict expired keys when approaching memory capacity. Caller holds lock."""
        cutoff = now - self._window_seconds
        stale = [
            k for k, timestamps in self._failures.items()
            if not timestamps or timestamps[-1] <= cutoff
        ]
        for k in stale:
            self._failures.pop(k, None)
        if len(self._failures) >= self._max_keys:
            sorted_keys = sorted(
                self._failures.keys(),
                key=lambda k: self._failures[k][-1] if self._failures[k] else 0.0,
            )
            for k in sorted_keys[: len(sorted_keys) // 2]:
                self._failures.pop(k, None)

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
