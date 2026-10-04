"""OPDS Progression 1.0 endpoints: read and update the reading position."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Never

from flask import Blueprint, Response, abort, g, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from bookflow.database.database import session_scope
from bookflow.database.models import Book, Progression
from bookflow.opds.auth import require_basic_auth
from bookflow.opds.generator import PROGRESSION_TYPE
from bookflow.optimizer.locks import progression_lock

bp = Blueprint("progression", __name__, url_prefix="/opds/publications")

bp.before_request(require_basic_auth)

PROBLEM_TYPE = "application/problem+json"

INVALID_PAYLOAD_TYPE = "https://registry.opds.io/error#progression-invalid-payload"
INVALID_PAYLOAD_TITLE = (
    "Progression could not be updated due to an invalid payload."
)
STALE_TYPE = "https://registry.opds.io/error#progression-date"
STALE_TITLE = "A more recent progression point is already available."
BLANK_TYPE = "about:blank"
NOT_FOUND_TITLE = "The requested publication was not found."

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class _Document:
    modified: datetime
    progression: float
    device_id: str
    device_name: str
    title: str | None
    references: list[str] | None


@bp.get("/<int:book_id>/progression")
def publication_progression(book_id: int) -> Response:
    """Return the stored reading progression for a publication."""
    user_id = g.current_user.id
    with session_scope() as session:
        if session.get(Book, book_id) is None:
            abort(404)
        row = session.scalar(
            select(Progression).where(
                Progression.user_id == user_id,
                Progression.book_id == book_id,
            )
        )
        if row is None:
            return Response(b"", content_type=PROGRESSION_TYPE)
        body = json.dumps(_to_document(row))
    return Response(body, content_type=PROGRESSION_TYPE)


@bp.put("/<int:book_id>/progression")
def update_publication_progression(book_id: int) -> Response:
    """Save or update reading progression for a publication."""
    user_id = g.current_user.id
    with progression_lock(user_id, book_id), session_scope() as session:
        if session.get(Book, book_id) is None:
            abort(404)
        if request.mimetype != PROGRESSION_TYPE:
            abort(400)
        try:
            payload = json.loads(request.get_data(cache=False, as_text=True))
            document = _parse_document(payload)
        except (ValueError, UnicodeDecodeError):
            abort(400)
        row = session.scalar(
            select(Progression).where(
                Progression.user_id == user_id,
                Progression.book_id == book_id,
            )
        )
        created = row is None
        if created:
            row = Progression(user_id=user_id, book_id=book_id)
            session.add(row)
        elif row.modified is not None and document.modified < _as_aware(
            row.modified
        ):
            abort(409)
        row.progression = document.progression
        row.modified = _as_naive(document.modified)
        row.device_id = document.device_id
        row.device_name = document.device_name
        row.title = document.title
        row.references = document.references
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            row = session.scalar(
                select(Progression).where(
                    Progression.user_id == user_id,
                    Progression.book_id == book_id,
                )
            )
            if row is None:
                raise
            if row.modified is not None and document.modified < _as_aware(
                row.modified
            ):
                abort(409)
            created = False
            row.progression = document.progression
            row.modified = _as_naive(document.modified)
            row.device_id = document.device_id
            row.device_name = document.device_name
            row.title = document.title
            row.references = document.references
            session.flush()
        body = json.dumps(_to_document(row))
    return Response(
        body, status=201 if created else 200, content_type=PROGRESSION_TYPE
    )


@bp.get("/<path:unknown>")
def unknown_publication_path(unknown: str) -> Never:
    """Own unmatched paths under this prefix.

    Without this rule the catalog blueprint's ``/<path:unknown>`` answers
    them, and the client would get an XML catalog error instead of an RFC
    7807 problem document.
    """
    abort(404)


# --- errors -----------------------------------------------------------------


@bp.errorhandler(HTTPException)
def problem_document(error: HTTPException) -> Response:
    """Build the RFC 7807 document for a failure under ``/opds/publications``.

    The registry types cover the statuses this endpoint defines itself:
    a malformed payload, an unknown publication, a stale progression
    point. Anything else — 405 from a wrong method, 500 from an
    unhandled exception — uses a blank type with the status' summary.
    Routing failures skip the blueprint and are answered by
    ``create_app``'s fallback, which calls this directly.
    """
    status = error.code or 500
    if status == 400:
        return _problem(status, INVALID_PAYLOAD_TYPE, INVALID_PAYLOAD_TITLE)
    if status == 404:
        return _problem(status, BLANK_TYPE, NOT_FOUND_TITLE)
    if status == 409:
        return _problem(status, STALE_TYPE, STALE_TITLE)
    return _problem(status, BLANK_TYPE, error.description or error.name)


def _problem(status: int, type_uri: str, title: str) -> Response:
    body = json.dumps({"type": type_uri, "title": title})
    return Response(body, status=status, content_type=PROBLEM_TYPE)


# --- document mapping -------------------------------------------------------


def _parse_document(payload: object) -> _Document:
    if not isinstance(payload, dict):
        raise ValueError("progression payload must be an object")
    title = payload.get("title")
    if title is not None and not isinstance(title, str):
        raise ValueError("title must be a string")
    references = payload.get("references")
    if references is not None and (
        not isinstance(references, list)
        or not all(isinstance(item, str) for item in references)
    ):
        raise ValueError("references must be an array of strings")
    device = payload.get("device")
    if not isinstance(device, dict):
        raise ValueError("device is required")
    device_id = device.get("id")
    device_name = device.get("name")
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("device.id is required")
    if not isinstance(device_name, str) or not device_name.strip():
        raise ValueError("device.name is required")
    return _Document(
        modified=_parse_timestamp(payload.get("modified")),
        progression=_parse_progression(payload.get("progression")),
        device_id=device_id,
        device_name=device_name,
        title=title,
        references=references,
    )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("modified is required")
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("modified must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_progression(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("progression must be a number")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("progression must be between 0 and 1")
    return number


def _to_document(row: Progression) -> dict[str, object]:
    document: dict[str, object] = {}
    if row.title is not None:
        document["title"] = row.title
    document["modified"] = _format_timestamp(
        _as_aware(row.modified) if row.modified is not None else EPOCH
    )
    document["device"] = {"id": row.device_id, "name": row.device_name}
    document["progression"] = row.progression
    if row.references is not None:
        document["references"] = row.references
    return document


def _format_timestamp(value: datetime) -> str:
    stamp = value.astimezone(UTC)
    if stamp.microsecond:
        return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _as_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)
