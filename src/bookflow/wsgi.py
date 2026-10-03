"""WSGI entry point for production servers.

``gunicorn bookflow.wsgi:app`` builds the application once at import; the
factory itself lives in :mod:`bookflow.app`.
"""

from __future__ import annotations

from bookflow.app import create_app

app = create_app()
