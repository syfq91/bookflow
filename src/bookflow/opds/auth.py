"""HTTP Basic authentication for the OPDS catalog."""

from __future__ import annotations

from flask import Response

from bookflow.auth.service import PasswordVerifier

REALM = "BookFlow"


def authenticate(verifier: PasswordVerifier, authorization) -> Response | None:
    """Return an error response unless the request carries valid credentials.

    ``authorization`` is the parsed ``request.authorization`` value.
    """
    if not verifier.configured:
        return Response(
            "OPDS is disabled: set OPDS_ADMIN_PASSWORD.\n",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
    if authorization is None or authorization.type != "basic":
        return _unauthorized()
    if not verifier.verify(authorization.username, authorization.password):
        return _unauthorized()
    return None


def _unauthorized() -> Response:
    return Response(
        "Authentication required.\n",
        status=401,
        headers={"WWW-Authenticate": f'Basic realm="{REALM}"'},
        content_type="text/plain; charset=utf-8",
    )
