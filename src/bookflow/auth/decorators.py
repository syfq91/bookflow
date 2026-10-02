"""Route protection helpers."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import abort, redirect, request, session, url_for

from bookflow.auth.service import ADMIN_SESSION_KEY, validate_csrf

_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def require_csrf() -> None:
    """Reject a state-changing request that carries no valid CSRF token.

    Register once per blueprint that serves write routes:
    ``bp.before_request(require_csrf)``. Reads of every kind pass through.
    """
    if request.method in _WRITE_METHODS and not validate_csrf():
        abort(400, description="Invalid or missing CSRF token")


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Redirect unauthenticated requests to the admin login page."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if session.get(ADMIN_SESSION_KEY):
            return view(*args, **kwargs)
        target = request.path
        if request.query_string:
            target += "?" + request.query_string.decode("utf-8", "replace")
        return redirect(url_for("auth.login", next=target))

    return wrapped
