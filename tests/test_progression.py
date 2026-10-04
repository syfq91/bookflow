from __future__ import annotations

import base64
import json
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import pytest

from bookflow.config import Settings
from bookflow.library.service import add_folder
from bookflow.optimizer.locks import progression_lock
from factories import basic_auth_headers, create_user, insert_books

PASSWORD = "opds-pass"

PROGRESSION_TYPE = "application/opds-progression+json"
AUTH_TYPE = "application/opds-authentication+json"
PROBLEM_TYPE = "application/problem+json"
PROGRESSION_REL = "http://opds-spec.org/progression"
STALE_TYPE = "https://registry.opds.io/error#progression-date"
INVALID_TYPE = "https://registry.opds.io/error#progression-invalid-payload"

ATOM = "http://www.w3.org/2005/Atom"
NS = {"a": ATOM}

DEVICE = {"id": "urn:uuid:019c0047-cc8d-7ec4-a3c3-938ccadc020a", "name": "Reader"}


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def opds_settings(settings: Settings) -> Settings:
    return replace(settings, admin_password=PASSWORD)


@pytest.fixture
def app(build_app, opds_settings: Settings):
    return build_app(opds_settings)


@pytest.fixture
def folder_id(root: Path) -> int:
    result = add_folder(str(root))
    assert result.ok, result.error
    assert result.folder_id is not None
    return result.folder_id


# --- helpers ----------------------------------------------------------------


def _headers() -> dict[str, str]:
    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _get(client, path: str):
    return client.get(path, headers=_headers())


def _put(client, book_id: int, document, content_type: str = PROGRESSION_TYPE):
    return client.put(
        f"/opds/publications/{book_id}/progression",
        data=json.dumps(document),
        content_type=content_type,
        headers=_headers(),
    )


def _book_id(folder_id: int, title: str = "Dune") -> int:
    return insert_books(folder_id, [{"path": f"{title.lower()}.epub", "title": title}])[
        0
    ]


def _document(**overrides) -> dict:
    document = {
        "modified": "2026-01-27T11:00:00Z",
        "device": dict(DEVICE),
        "progression": 0.0174,
        "title": "Chapter 1 - A New Dawn",
        "references": ["chapter1.html"],
    }
    document.update(overrides)
    return document


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


# --- authentication document ------------------------------------------------


def test_unauthenticated_request_returns_authentication_document(client) -> None:
    resp = client.get("/opds")

    assert resp.status_code == 401
    assert resp.headers["Content-Type"] == AUTH_TYPE
    assert resp.headers["WWW-Authenticate"] == 'Basic realm="BookFlow"'
    assert 'rel="http://opds-spec.org/auth/document"' in resp.headers["Link"]
    document = json.loads(resp.data)
    assert document["title"] == "BookFlow"
    assert document["id"].endswith("/opds/authentication")
    assert document["authentication"] == [
        {"type": "http://opds-spec.org/auth/basic"}
    ]


def test_authentication_document_is_public(client) -> None:
    resp = client.get("/opds/authentication")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == AUTH_TYPE
    document = json.loads(resp.data)
    assert document["authentication"][0]["type"] == (
        "http://opds-spec.org/auth/basic"
    )


def test_progression_endpoints_require_authentication(client) -> None:
    for method in (client.get, client.put):
        resp = method("/opds/publications/1/progression")

        assert resp.status_code == 401
        assert resp.headers["Content-Type"] == AUTH_TYPE


def test_progression_401_body_is_authentication_document(
    client, folder_id: int
) -> None:
    book_id = _book_id(folder_id)

    resp = client.get(f"/opds/publications/{book_id}/progression")

    assert resp.status_code == 401
    document = json.loads(resp.data)
    assert document["id"].endswith("/opds/authentication")


# --- discovery --------------------------------------------------------------


def test_catalog_entry_links_to_progression(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)

    resp = _get(client, "/opds/books")
    root = ElementTree.fromstring(resp.data)
    entry = root.find("a:entry", NS)
    links = [
        link
        for link in entry.findall("a:link", NS)
        if link.get("rel") == PROGRESSION_REL
    ]

    assert len(links) == 1
    assert links[0].get("type") == PROGRESSION_TYPE
    assert links[0].get("href") == f"/opds/publications/{book_id}/progression"


# --- GET --------------------------------------------------------------------


def test_get_missing_progression_returns_empty_payload(
    client, folder_id: int
) -> None:
    book_id = _book_id(folder_id)

    resp = _get(client, f"/opds/publications/{book_id}/progression")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == PROGRESSION_TYPE
    assert resp.data == b""


def test_get_returns_stored_document(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)
    _put(client, book_id, _document())

    resp = _get(client, f"/opds/publications/{book_id}/progression")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == PROGRESSION_TYPE
    document = json.loads(resp.data)
    assert document["title"] == "Chapter 1 - A New Dawn"
    assert document["modified"] == "2026-01-27T11:00:00Z"
    assert document["device"] == DEVICE
    assert document["progression"] == 0.0174
    assert document["references"] == ["chapter1.html"]


def test_get_unknown_publication_returns_problem(client, folder_id: int) -> None:
    resp = _get(client, "/opds/publications/999999/progression")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == PROBLEM_TYPE
    document = json.loads(resp.data)
    assert document["type"]
    assert document["title"]


# --- PUT --------------------------------------------------------------------


def test_put_creates_progression_with_201(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)

    resp = _put(client, book_id, _document())

    assert resp.status_code == 201
    assert resp.headers["Content-Type"] == PROGRESSION_TYPE
    assert json.loads(resp.data)["progression"] == 0.0174


def test_put_updates_progression_with_200(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)
    _put(client, book_id, _document())

    resp = _put(
        client,
        book_id,
        _document(
            modified="2026-02-01T09:00:00Z",
            progression=0.5,
            device={"id": "urn:uuid:other", "name": "Phone"},
        ),
    )

    assert resp.status_code == 200
    stored = json.loads(_get(client, f"/opds/publications/{book_id}/progression").data)
    assert stored["progression"] == 0.5
    assert stored["device"]["name"] == "Phone"


def test_put_with_equal_timestamp_is_accepted(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)
    _put(client, book_id, _document())

    resp = _put(client, book_id, _document())

    assert resp.status_code == 200


def test_put_with_older_timestamp_conflicts(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)
    _put(client, book_id, _document(modified="2026-02-01T09:00:00Z"))

    resp = _put(
        client, book_id, _document(modified="2026-01-27T11:00:00Z")
    )

    assert resp.status_code == 409
    assert resp.headers["Content-Type"] == PROBLEM_TYPE
    problem = json.loads(resp.data)
    assert problem["type"] == STALE_TYPE
    assert problem["title"]


def test_put_with_wrong_content_type_rejected(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)

    resp = _put(client, book_id, _document(), content_type="application/json")

    assert resp.status_code == 400
    assert resp.headers["Content-Type"] == PROBLEM_TYPE
    assert json.loads(resp.data)["type"] == INVALID_TYPE


def test_put_with_invalid_json_rejected(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)

    resp = client.put(
        f"/opds/publications/{book_id}/progression",
        data="{not json",
        content_type=PROGRESSION_TYPE,
        headers=_headers(),
    )

    assert resp.status_code == 400
    assert json.loads(resp.data)["type"] == INVALID_TYPE


def test_put_with_missing_device_rejected(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)

    resp = _put(
        client,
        book_id,
        {"modified": "2026-01-27T11:00:00Z", "progression": 0.5},
    )

    assert resp.status_code == 400
    assert json.loads(resp.data)["type"] == INVALID_TYPE


@pytest.mark.parametrize("value", [1.5, -0.1, "0.5", True, None])
def test_put_with_invalid_progression_value_rejected(
    client, folder_id: int, value
) -> None:
    book_id = _book_id(folder_id)

    resp = _put(client, book_id, _document(progression=value))

    assert resp.status_code == 400
    assert json.loads(resp.data)["type"] == INVALID_TYPE


@pytest.mark.parametrize("value", ["yesterday", "", None, 12345])
def test_put_with_invalid_timestamp_rejected(
    client, folder_id: int, value
) -> None:
    book_id = _book_id(folder_id)

    resp = _put(client, book_id, _document(modified=value))

    assert resp.status_code == 400
    assert json.loads(resp.data)["type"] == INVALID_TYPE


def test_put_optional_fields_omitted(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)
    minimal = {
        "modified": "2026-01-27T11:00:00Z",
        "device": DEVICE,
        "progression": 0.25,
    }

    _put(client, book_id, minimal)
    stored = json.loads(
        _get(client, f"/opds/publications/{book_id}/progression").data
    )

    assert "title" not in stored
    assert "references" not in stored
    assert stored["progression"] == 0.25


def test_put_preserves_sub_second_precision(client, folder_id: int) -> None:
    book_id = _book_id(folder_id)
    payload = _document(modified="2026-01-27T11:00:00.123456Z")

    created = _put(client, book_id, payload)
    fetched = _get(client, f"/opds/publications/{book_id}/progression")
    replayed = _put(client, book_id, payload)

    assert created.status_code == 201
    assert _parse(json.loads(fetched.data)["modified"]) == _parse(
        payload["modified"]
    )
    assert replayed.status_code == 200


def test_put_requires_auth_and_returns_document_on_401(
    client, folder_id: int
) -> None:
    book_id = _book_id(folder_id)

    resp = client.put(
        f"/opds/publications/{book_id}/progression",
        data=json.dumps(_document()),
        content_type=PROGRESSION_TYPE,
    )

    assert resp.status_code == 401
    assert resp.headers["Content-Type"] == AUTH_TYPE


def test_non_numeric_publication_id_returns_problem(client, folder_id: int) -> None:
    resp = _get(client, "/opds/publications/not-a-number/progression")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == PROBLEM_TYPE
    document = json.loads(resp.data)
    assert document["type"] == "about:blank"
    assert document["title"]


def test_wrong_method_returns_problem(client, folder_id: int) -> None:
    resp = client.post(
        "/opds/publications/1/progression",
        data=json.dumps(_document()),
        content_type=PROGRESSION_TYPE,
        headers=_headers(),
    )

    assert resp.status_code == 405
    assert resp.headers["Content-Type"] == PROBLEM_TYPE
    document = json.loads(resp.data)
    assert document["type"] == "about:blank"
    assert document["title"]


# --- concurrency ------------------------------------------------------------


def test_progression_locks_are_per_book() -> None:
    assert progression_lock(1) is progression_lock(1)
    assert progression_lock(1) is not progression_lock(2)


def test_progression_put_waits_for_book_lock(app, folder_id: int) -> None:
    """The read→compare→write runs inside the book's lock, not beside it."""
    book_id = _book_id(folder_id)
    lock = progression_lock(book_id)
    assert lock.acquire(blocking=False)

    client = app.test_client()
    finished = threading.Event()
    statuses: list[int] = []

    def worker() -> None:
        try:
            statuses.append(_put(client, book_id, _document()).status_code)
        finally:
            finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    blocked = not finished.wait(timeout=0.5)
    lock.release()
    assert finished.wait(timeout=60)
    thread.join(timeout=60)

    assert blocked
    assert statuses == [201]


def test_progression_locks_are_per_user_and_book() -> None:
    assert progression_lock(1, 10) is progression_lock(1, 10)
    assert progression_lock(1, 10) is not progression_lock(2, 10)
    assert progression_lock(1, 10) is not progression_lock(1, 20)


def test_multi_user_progression_isolation(app, folder_id: int) -> None:
    """User A and User B maintain separate progressions for the same book."""
    create_user("alice", "alicepass", is_admin=False)
    create_user("bob", "bobpass", is_admin=False)

    client = app.test_client()
    book_id = _book_id(folder_id, title="Dune")

    alice_header = basic_auth_headers("alice", "alicepass")
    bob_header = basic_auth_headers("bob", "bobpass")

    # Alice sets progression 0.25
    doc_alice = _document(progression=0.25, title="Alice Progress")
    resp_alice = client.put(
        f"/opds/publications/{book_id}/progression",
        data=json.dumps(doc_alice),
        content_type=PROGRESSION_TYPE,
        headers=alice_header,
    )
    assert resp_alice.status_code == 201

    # Bob has no progression initially
    resp_bob_get = client.get(
        f"/opds/publications/{book_id}/progression", headers=bob_header
    )
    assert resp_bob_get.status_code == 200
    assert resp_bob_get.data == b""

    # Bob sets progression 0.75
    doc_bob = _document(progression=0.75, title="Bob Progress")
    resp_bob = client.put(
        f"/opds/publications/{book_id}/progression",
        data=json.dumps(doc_bob),
        content_type=PROGRESSION_TYPE,
        headers=bob_header,
    )
    assert resp_bob.status_code == 201

    # Alice still gets 0.25
    alice_get = client.get(
        f"/opds/publications/{book_id}/progression", headers=alice_header
    )
    assert alice_get.status_code == 200
    assert json.loads(alice_get.data)["progression"] == 0.25
    assert json.loads(alice_get.data)["title"] == "Alice Progress"

    # Bob gets 0.75
    bob_get = client.get(
        f"/opds/publications/{book_id}/progression", headers=bob_header
    )
    assert bob_get.status_code == 200
    assert json.loads(bob_get.data)["progression"] == 0.75
    assert json.loads(bob_get.data)["title"] == "Bob Progress"
