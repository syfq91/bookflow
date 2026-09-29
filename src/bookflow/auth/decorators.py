"""Route protection helpers."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import redirect, request, session, url_for

from bookflow.auth.service import ADMIN_SESSION_KEY


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
