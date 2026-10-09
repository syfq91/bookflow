from __future__ import annotations

import base64
import io
import os
import threading
import time
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from bookflow.config import Settings
from bookflow.database.database import session_scope
from bookflow.database.models import Book, OptimizedBook
from bookflow.library.scanner import scan_folder
from bookflow.library.service import add_folder
from bookflow.optimizer import service as optimizer_service
from bookflow.optimizer.locks import profile_lock
from bookflow.optimizer.service import clear_optimized_cache, optimize_book
from factories import (
    csrf_token,
    insert_books,
    login_admin,
    make_cbz,
    make_epub,
    make_pdf,
)

PASSWORD = "opds-pass"
EXTENSIONS = (".epub", ".pdf", ".cbz")
EPUB_TYPE = "application/epub+zip"
XTC_TYPE = "application/x-xtc"
XML_TYPE = "application/xml"


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


def _scan(folder_id: int):
    return scan_folder(folder_id, EXTENSIONS)


def _book_ids() -> list[int]:
    with session_scope() as session:
        return [book.id for book in session.scalars(select(Book).order_by(Book.id))]


def _insert_book(folder_id: int, relative_path: str) -> int:
    return insert_books(folder_id, [{"path": relative_path}])[0]


def _optimized_rows(book_id: int) -> list[OptimizedBook]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(OptimizedBook).where(
                    OptimizedBook.book_id == book_id
                )
            )
        )


def _library_book(root: Path, folder_id: int, title: str = "Dune") -> tuple[int, Path]:
    source = root / "dune.epub"
    make_epub(source, title=title, authors=("Frank Herbert",))
    _scan(folder_id)
    return _book_ids()[0], source


def _library_cbz(
    root: Path, folder_id: int, filename: str = "manga.cbz"
) -> tuple[int, Path]:
    source = root / filename
    make_cbz(source, page_count=3)
    _scan(folder_id)
    with session_scope() as session:
        book = session.scalar(select(Book).where(Book.relative_path == filename))
        assert book is not None
        return book.id, source


def _clear_cache(client):
    return client.post(
        "/admin/cache/clear",
        data={"csrf_token": csrf_token(client)},
    )


# --- optimized acquisition --------------------------------------------------


def test_x3_download_returns_optimized_epub(client, folder_id, root) -> None:
    book_id, _source = _library_book(root, folder_id)

    resp = _get(client, f"/opdsx3/download/{book_id}")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == EPUB_TYPE
    assert resp.headers["Content-Disposition"].startswith("attachment")
    with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"


def test_x4_download_returns_optimized_epub(client, folder_id, root) -> None:
    book_id, _source = _library_book(root, folder_id)

    resp = _get(client, f"/opdsx4/download/{book_id}")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == EPUB_TYPE
    with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"


def test_x3_download_returns_optimized_xtc(client, folder_id, root) -> None:
    book_id, _source = _library_cbz(root, folder_id, "naruto.cbz")

    resp = _get(client, f"/opdsx3/download/{book_id}")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == XTC_TYPE
    assert resp.headers["Content-Disposition"].startswith("attachment")
    assert "naruto.xtc" in resp.headers["Content-Disposition"]
    assert resp.data[:4] == b"XTC\x00"


def test_x4_download_returns_optimized_xtc(client, folder_id, root) -> None:
    book_id, _source = _library_cbz(root, folder_id, "naruto.cbz")

    resp = _get(client, f"/opdsx4/download/{book_id}")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == XTC_TYPE
    assert resp.headers["Content-Disposition"].startswith("attachment")
    assert "naruto.xtc" in resp.headers["Content-Disposition"]
    assert resp.data[:4] == b"XTC\x00"


def test_cbz_download_populates_cache_and_index(
    client, app, folder_id, root
) -> None:
    book_id, source = _library_cbz(root, folder_id, "bleach.cbz")

    resp = _get(client, f"/opdsx4/download/{book_id}")

    assert resp.status_code == 200
    cache_file = app.config["SETTINGS"].x4_cache_dir / f"{book_id}.xtc"
    assert cache_file.is_file()
    rows = _optimized_rows(book_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.profile == "x4"
    assert row.source_mtime is not None and row.source_mtime > 0
    assert row.source_size == source.stat().st_size
    assert row.optimized_size == cache_file.stat().st_size


def test_download_populates_cache_and_index(
    client, app, folder_id, root
) -> None:
    book_id, source = _library_book(root, folder_id)

    resp = _get(client, f"/opdsx4/download/{book_id}")

    assert resp.status_code == 200
    cache_file = app.config["SETTINGS"].x4_cache_dir / f"{book_id}.epub"
    assert cache_file.is_file()
    rows = _optimized_rows(book_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.profile == "x4"
    assert row.source_mtime is not None and row.source_mtime > 0
    assert row.source_size == source.stat().st_size
    assert row.optimized_size == cache_file.stat().st_size


def test_profiles_use_separate_cache_entries(
    client, app, folder_id, root
) -> None:
    book_id, _source = _library_book(root, folder_id)

    assert _get(client, f"/opdsx3/download/{book_id}").status_code == 200
    assert _get(client, f"/opdsx4/download/{book_id}").status_code == 200

    settings = app.config["SETTINGS"]
    assert (settings.x3_cache_dir / f"{book_id}.epub").is_file()
    assert (settings.x4_cache_dir / f"{book_id}.epub").is_file()
    assert sorted(row.profile for row in _optimized_rows(book_id)) == [
        "x3",
        "x4",
    ]


def test_requires_authentication(client, folder_id) -> None:
    assert client.get("/opdsx3/download/1").status_code == 401
    assert client.get("/opdsx4/download/1").status_code == 401


def test_unknown_book_returns_xml_404(client, folder_id) -> None:
    resp = _get(client, "/opdsx3/download/999999")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == XML_TYPE


def test_non_epub_book_returns_404(client, folder_id, root) -> None:
    make_pdf(root / "report.pdf", title="Report")
    _scan(folder_id)
    book_id = _book_ids()[0]

    resp = _get(client, f"/opdsx4/download/{book_id}")

    assert resp.status_code == 404
    assert resp.headers["Content-Type"] == XML_TYPE


def test_path_traversal_rejected(client, folder_id, tmp_path) -> None:
    make_epub(tmp_path / "outside.epub", title="Escape")
    book_id = _insert_book(folder_id, "../outside.epub")

    resp = _get(client, f"/opdsx3/download/{book_id}")

    assert resp.status_code == 404


# --- cache validation -------------------------------------------------------


def _counting(monkeypatch) -> list:
    calls: list = []
    original = optimizer_service.process_epub

    def counting(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(optimizer_service, "process_epub", counting)
    return calls


def test_cache_hit_skips_reoptimization(
    client, folder_id, root, monkeypatch
) -> None:
    book_id, _source = _library_book(root, folder_id)
    calls = _counting(monkeypatch)

    first = _get(client, f"/opdsx3/download/{book_id}")
    second = _get(client, f"/opdsx3/download/{book_id}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 1


def test_stale_cache_reoptimizes_when_mtime_changes(
    client, folder_id, root, monkeypatch
) -> None:
    book_id, source = _library_book(root, folder_id)
    calls = _counting(monkeypatch)
    _get(client, f"/opdsx3/download/{book_id}")

    stat = source.stat()
    os.utime(source, (stat.st_atime, stat.st_mtime + 10))
    resp = _get(client, f"/opdsx3/download/{book_id}")

    assert resp.status_code == 200
    assert len(calls) == 2


def test_source_modification_reoptimizes(
    client, folder_id, root, monkeypatch
) -> None:
    book_id, source = _library_book(root, folder_id)
    calls = _counting(monkeypatch)
    _get(client, f"/opdsx3/download/{book_id}")

    make_epub(source, title="Dune Revised", authors=("Frank Herbert",))
    resp = _get(client, f"/opdsx3/download/{book_id}")

    assert resp.status_code == 200
    assert len(calls) == 2
    assert _optimized_rows(book_id)[0].source_size == source.stat().st_size


def test_optimization_never_touches_source(
    client, folder_id, root
) -> None:
    book_id, source = _library_book(root, folder_id)
    before_bytes = source.read_bytes()
    before_stat = source.stat()
    before_dir = sorted(path.name for path in root.iterdir())

    assert _get(client, f"/opdsx3/download/{book_id}").status_code == 200
    assert _get(client, f"/opdsx4/download/{book_id}").status_code == 200

    assert source.read_bytes() == before_bytes
    assert source.stat().st_mtime_ns == before_stat.st_mtime_ns
    assert sorted(path.name for path in root.iterdir()) == before_dir


# --- failure handling -------------------------------------------------------


class _FailedReport:
    success = False
    error = "DRM protected"


def test_optimizer_failure_returns_xml_500(
    client, app, folder_id, root, monkeypatch, caplog
) -> None:
    book_id, _source = _library_book(root, folder_id)
    monkeypatch.setattr(
        optimizer_service, "process_epub", lambda *_args, **_kwargs: _FailedReport()
    )

    resp = _get(client, f"/opdsx3/download/{book_id}")

    assert f"book_id={book_id}" in caplog.text
    assert "DRM protected" in caplog.text

    assert resp.status_code == 500
    assert resp.headers["Content-Type"] == XML_TYPE
    cache_file = app.config["SETTINGS"].x3_cache_dir / f"{book_id}.epub"
    assert not cache_file.exists()
    assert _optimized_rows(book_id) == []
    tmp_dir = app.config["SETTINGS"].x3_cache_dir / ".tmp"
    assert not tmp_dir.exists() or list(tmp_dir.glob("*.epub")) == []


def test_optimizer_exception_cleans_up(
    client, app, folder_id, root, monkeypatch, caplog
) -> None:
    book_id, _source = _library_book(root, folder_id)

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(optimizer_service, "process_epub", explode)

    resp = _get(client, f"/opdsx4/download/{book_id}")

    assert f"book_id={book_id}" in caplog.text
    assert any(record.exc_info for record in caplog.records)

    assert resp.status_code == 500
    cache_file = app.config["SETTINGS"].x4_cache_dir / f"{book_id}.epub"
    assert not cache_file.exists()
    tmp_dir = app.config["SETTINGS"].x4_cache_dir / ".tmp"
    assert not tmp_dir.exists() or list(tmp_dir.glob("*.epub")) == []


# --- concurrency ------------------------------------------------------------


def test_concurrent_optimization_waits_for_lock(
    app, folder_id, root
) -> None:
    book_id, source = _library_book(root, folder_id)
    lock = profile_lock(book_id, "x4")
    assert lock.acquire(blocking=False)

    finished = threading.Event()
    failures: list[Exception] = []

    def worker():
        try:
            with app.app_context():
                optimize_book(book_id, "x4", source)
        except Exception as exc:
            failures.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    blocked = not finished.wait(timeout=0.5)
    lock.release()
    assert finished.wait(timeout=120)
    thread.join(timeout=120)

    assert blocked
    assert failures == []
    assert (app.config["SETTINGS"].x4_cache_dir / f"{book_id}.epub").is_file()


def test_profile_locks_are_independent() -> None:
    assert profile_lock(1, "x3") is profile_lock(1, "x3")
    assert profile_lock(1, "x3") is not profile_lock(1, "x4")
    assert profile_lock(1, "x3") is not profile_lock(2, "x3")


# --- cache management --------------------------------------------------------


def test_cache_clear_requires_login(client, folder_id, root) -> None:
    resp = client.post(
        "/admin/cache/clear", data={"csrf_token": csrf_token(client)}
    )

    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_cache_clear_requires_csrf(client, folder_id, root) -> None:
    login_admin(client, password=PASSWORD)

    resp = client.post("/admin/cache/clear")

    assert resp.status_code == 400


def test_cache_clear_removes_files_and_rows(
    client, app, folder_id, root
) -> None:
    book_id, source = _library_book(root, folder_id)
    source_bytes = source.read_bytes()
    assert _get(client, f"/opdsx3/download/{book_id}").status_code == 200
    settings = app.config["SETTINGS"]
    assert (settings.x3_cache_dir / f"{book_id}.epub").is_file()

    login_admin(client, password=PASSWORD)
    resp = _clear_cache(client)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/")
    assert _optimized_rows(book_id) == []
    leftover = [
        entry
        for directory in (settings.x3_cache_dir, settings.x4_cache_dir)
        if directory.is_dir()
        for entry in directory.iterdir()
        if entry.name != ".tmp"
    ]
    assert leftover == []
    scratch = settings.x3_cache_dir / ".tmp"
    assert not scratch.exists() or list(scratch.iterdir()) == []
    assert source.read_bytes() == source_bytes

    follow = client.get(resp.headers["Location"])
    assert follow.status_code == 200
    assert b"Cleared optimization cache" in follow.data


def test_download_regenerates_after_cache_clear(
    client, folder_id, root
) -> None:
    book_id, _source = _library_book(root, folder_id)
    first = _get(client, f"/opdsx3/download/{book_id}")
    assert first.status_code == 200

    login_admin(client, password=PASSWORD)
    assert _clear_cache(client).status_code == 302
    assert _optimized_rows(book_id) == []

    again = _get(client, f"/opdsx3/download/{book_id}")
    assert again.status_code == 200
    assert again.data[:2] == b"PK"
    assert len(_optimized_rows(book_id)) == 1


def test_cache_clear_sweeps_stale_scratch(client, app, folder_id, root) -> None:
    settings = app.config["SETTINGS"]
    stale = settings.x3_cache_dir / ".tmp" / "leftover.epub"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"stale")
    old_time = time.time() - 1000
    os.utime(stale, (old_time, old_time))

    login_admin(client, password=PASSWORD)
    resp = _clear_cache(client)

    assert resp.status_code == 302
    assert not stale.exists()
    # The scratch directory itself stays: removing it would race the
    # window between a generation creating it and claiming its file.
    assert stale.parent.is_dir()


def test_cache_clear_spares_recent_scratch(client, app, folder_id, root) -> None:
    settings = app.config["SETTINGS"]
    recent = settings.x3_cache_dir / ".tmp" / "in_flight.epub"
    recent.parent.mkdir(parents=True, exist_ok=True)
    recent.write_bytes(b"recent-scratch")

    login_admin(client, password=PASSWORD)
    resp = _clear_cache(client)

    assert resp.status_code == 302
    assert recent.is_file()


def test_cache_clear_spares_in_flight_scratch(
    app, folder_id, root, monkeypatch
) -> None:
    book_id, source = _library_book(root, folder_id)
    started = threading.Event()
    release = threading.Event()
    original = optimizer_service.process_epub

    def slow(source_path, output_path, **kwargs):
        scratch = Path(output_path)
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_bytes(b"in-flight")
        started.set()
        if not release.wait(timeout=60):
            raise RuntimeError("test never released the optimizer")
        if not scratch.is_file():
            raise RuntimeError("clear removed a scratch file mid-generation")
        return original(source_path, output_path, **kwargs)

    monkeypatch.setattr(optimizer_service, "process_epub", slow)
    failures: list[Exception] = []

    def worker():
        try:
            with app.app_context():
                optimize_book(book_id, "x4", source)
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(timeout=60)

    # The cache is cold, so clearing cannot block on the generation's lock:
    # only the claimed scratch file stands in the way, and it is skipped.
    with app.app_context():
        clear_optimized_cache()

    release.set()
    thread.join(timeout=60)

    assert not thread.is_alive()
    assert failures == []
    assert (app.config["SETTINGS"].x4_cache_dir / f"{book_id}.epub").is_file()


# --- cache pruning on delete ------------------------------------------------


def test_scan_prunes_cache_files_of_removed_books(
    client, app, folder_id, root
) -> None:
    make_epub(root / "dune.epub", title="Dune", authors=("Frank Herbert",))
    make_epub(root / "messiah.epub", title="Dune Messiah")
    _scan(folder_id)
    removed, kept = _book_ids()
    settings = app.config["SETTINGS"]
    for book_id in (removed, kept):
        assert _get(client, f"/opdsx3/download/{book_id}").status_code == 200
    removed_file = settings.x3_cache_dir / f"{removed}.epub"
    kept_file = settings.x3_cache_dir / f"{kept}.epub"
    assert removed_file.is_file() and kept_file.is_file()

    (root / "dune.epub").unlink()
    login_admin(client, password=PASSWORD)
    resp = client.post(
        f"/admin/folders/{folder_id}/scan",
        data={"csrf_token": csrf_token(client)},
    )

    assert resp.status_code == 302
    assert not removed_file.exists()
    assert _optimized_rows(removed) == []
    assert kept_file.is_file()
    assert len(_optimized_rows(kept)) == 1


def test_folder_delete_prunes_cache_files(
    client, app, folder_id, root
) -> None:
    book_id, source = _library_book(root, folder_id)
    settings = app.config["SETTINGS"]
    assert _get(client, f"/opdsx3/download/{book_id}").status_code == 200
    cache_file = settings.x3_cache_dir / f"{book_id}.epub"
    assert cache_file.is_file()

    login_admin(client, password=PASSWORD)
    resp = client.post(
        f"/admin/folders/{folder_id}/delete",
        data={"csrf_token": csrf_token(client)},
    )

    assert resp.status_code == 302
    assert not cache_file.exists()
    assert _optimized_rows(book_id) == []
    assert source.exists()


def test_record_upsert_updates_existing_row(app, folder_id, root) -> None:
    book_id, _source = _library_book(root, folder_id)
    cache_file = app.config["SETTINGS"].x3_cache_dir / f"{book_id}.epub"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(b"optimized-content")

    with app.app_context():
        optimizer_service._record(book_id, "x3", cache_file, 100, 200)
        optimizer_service._record(book_id, "x3", cache_file, 150, 250)

    with app.app_context(), session_scope() as session:
        row = session.scalar(
            select(OptimizedBook).where(
                OptimizedBook.book_id == book_id,
                OptimizedBook.profile == "x3",
            )
        )
        assert row is not None
        assert row.source_mtime == 150
        assert row.source_size == 250


def test_cbz_clear_cache_removes_xtc(client, app, folder_id, root) -> None:
    book_id, _source = _library_cbz(root, folder_id, "clear_test.cbz")
    assert _get(client, f"/opdsx4/download/{book_id}").status_code == 200
    cache_file = app.config["SETTINGS"].x4_cache_dir / f"{book_id}.xtc"
    assert cache_file.is_file()
    assert len(_optimized_rows(book_id)) == 1

    login_admin(client, password=PASSWORD)
    resp = _clear_cache(client)
    assert resp.status_code == 302
    assert not cache_file.exists()
    assert _optimized_rows(book_id) == []


def test_cbz_scan_prunes_cache_files_of_removed_books(
    client, app, folder_id, root
) -> None:
    book_id, source = _library_cbz(root, folder_id, "prune_test.cbz")
    assert _get(client, f"/opdsx4/download/{book_id}").status_code == 200
    cache_file = app.config["SETTINGS"].x4_cache_dir / f"{book_id}.xtc"
    assert cache_file.is_file()

    source.unlink()
    login_admin(client, password=PASSWORD)
    resp = client.post(
        f"/admin/folders/{folder_id}/scan",
        data={"csrf_token": csrf_token(client)},
    )
    assert resp.status_code == 302
    assert not cache_file.exists()
    assert _optimized_rows(book_id) == []

