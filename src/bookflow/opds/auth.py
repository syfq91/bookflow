"""HTTP Basic authentication and OPDS Authentication Document discovery."""

from __future__ import annotations

import json

from flask import Response, url_for

from bookflow.auth.service import PasswordVerifier

REALM = "BookFlow"
CATALOG_TITLE = "BookFlow"

AUTH_DOCUMENT_TYPE = "application/opds-authentication+json"
AUTH_DOCUMENT_REL = "http://opds-spec.org/auth/document"
BASIC_AUTH_FLOW = "http://opds-spec.org/auth/basic"


def authenticate(verifier: PasswordVerifier, authorization) -> Response | None:
    """Return an error response unless the request carries valid credentials.

    ``authorization`` is the parsed ``request.authorization`` value. A 401
    carries the OPDS Authentication Document, as required by the OPDS
    Progression and Authentication for OPDS 1.0 specifications.
    """
    if not verifier.configured:
        return Response(
            "OPDS is disabled: set OPDS_ADMIN_PASSWORD.\n",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
    if authorization is None or authorization.type != "basic":
        return unauthorized()
    if not verifier.verify(authorization.username, authorization.password):
        return unauthorized()
    return None


def authentication_document() -> bytes:
    """Build the OPDS Authentication Document for this catalog."""
    payload = {
        "id": url_for("opds.authentication", _external=True),
        "title": CATALOG_TITLE,
        "authentication": [{"type": BASIC_AUTH_FLOW}],
    }
    return json.dumps(payload).encode("utf-8")


def unauthorized() -> Response:
    document_href = url_for("opds.authentication", _external=True)
    return Response(
        authentication_document(),
        status=401,
        content_type=AUTH_DOCUMENT_TYPE,
        headers={
            "WWW-Authenticate": f'Basic realm="{REALM}"',
            "Link": (
                f'<{document_href}>; rel="{AUTH_DOCUMENT_REL}";'
                f' type="{AUTH_DOCUMENT_TYPE}"'
            ),
        },
    )
