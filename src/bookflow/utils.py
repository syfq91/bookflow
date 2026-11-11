"""Common utility functions for time and timezone conversions."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time as a naive UTC datetime."""
    return datetime.now(UTC).replace(tzinfo=None)


def as_aware(value: datetime) -> datetime:
    """Ensure a datetime is timezone-aware in UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def as_naive(value: datetime) -> datetime:
    """Convert an aware or naive datetime to a naive UTC datetime."""
    return value.astimezone(UTC).replace(tzinfo=None)


def mtime_to_datetime(mtime: float) -> datetime:
    """Convert a POSIX mtime timestamp to a naive UTC datetime."""
    return datetime.fromtimestamp(mtime, tz=UTC).replace(tzinfo=None)
