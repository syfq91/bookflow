from __future__ import annotations

import hashlib
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from bookflow.config import Settings
from bookflow.database.database import init_engine, reset_engine, session_scope
from bookflow.database.models import Book, LibraryFolder, Progression
from bookflow.library import scanner
from bookflow.library.scanner import ScanInProgress, folder_lock, scan_folder
from bookflow.library.service import add_folder
from factories import make_epub, make_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSIONS = (".epub", ".pdf", ".cbz", ".cbr", ".mobi", ".azw3")


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest.fixture
def db(settings: Settings):
    reset_engine()
    command.upgrade(alembic_config(settings.database_url), "head")
    init_engine(settings)
    yield
    reset_engine()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "books"
    path.mkdir()
    return path


@pytest.fixture
def folder_id(db, root: Path) -> int:
    result = add_folder(str(root))
    assert result.ok, result.error
    assert result.folder_id is not None
    return result.folder_id


def _scan(folder_id: int):
    return scan_folder(folder_id, EXTENSIONS)


def _books() -> list[Book]:
    with session_scope() as session:
        return list(session.query(Book).order_by(Book.relative_path))


def _folder() -> LibraryFolder:
    with session_scope() as session:
        return session.query(LibraryFolder).one()


# --- acceptance ------------------------------------------------------------


def test_scan_creates_book_record(folder_id: int, root: Path) -> None:
    make_epub(root / "dune.epub", title="Dune", authors=("Frank Herbert",))

    result = _scan(folder_id)

    assert result.status == "ok"
    assert (result.added, result.updated, result.removed) == (1, 0, 0)

    books = _books()
    assert len(books) == 1
    book = books[0]
    assert book.relative_path == "dune.epub"
    assert book.title == "Dune"
    assert book.authors == "Frank Herbert"
    assert book.file_format == "epub"
    assert book.file_size == (root / "dune.epub").stat().st_size
    assert book.file_modified_at is not None


# --- discovery -------------------------------------------------------------


def test_scan_indexes_pdf(folder_id: int, root: Path) -> None:
    make_pdf(root / "report.pdf", title="Quarterly Report", author="Ada Lovelace")

    _scan(folder_id)

    books = _books()
    assert len(books) == 1
    assert books[0].title == "Quarterly Report"
    assert books[0].authors == "Ada Lovelace"
    assert books[0].file_format == "pdf"


def test_scan_indexes_nested_directories(folder_id: int, root: Path) -> None:
    make_epub(root / "fiction" / "dune.epub", title="Dune")
    make_pdf(root / "comics" / "issue1.pdf", title="Issue 1")

    _scan(folder_id)

    assert [book.relative_path for book in _books()] == [
        "comics/issue1.pdf",
        "fiction/dune.epub",
    ]


def test_relative_paths_are_posix(folder_id: int, root: Path) -> None:
    make_epub(root / "a" / "b" / "c.epub", title="C")

    _scan(folder_id)

    relative_path = _books()[0].relative_path
    assert relative_path == "a/b/c.epub"
    assert not relative_path.startswith("/")
    assert "\\" not in relative_path


def test_unsupported_extension_ignored(folder_id: int, root: Path) -> None:
    (root / "notes.txt").write_text("not a book")
    make_epub(root / "real.epub", title="Real")

    result = _scan(folder_id)

    assert result.added == 1
    assert [book.relative_path for book in _books()] == ["real.epub"]


def test_symlinked_directories_are_skipped(
    folder_id: int, root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    make_epub(outside / "hidden.epub", title="Hidden")
    (root / "link").symlink_to(outside, target_is_directory=True)

    result = _scan(folder_id)

    assert result.added == 0
    assert _books() == []


# --- reconciliation --------------------------------------------------------


def test_rescan_adds_new_files(folder_id: int, root: Path) -> None:
    make_epub(root / "a.epub", title="A")
    _scan(folder_id)

    make_epub(root / "b.epub", title="B")
    result = _scan(folder_id)

    assert (result.added, result.updated, result.removed, result.unchanged) == (
        1,
        0,
        0,
        1,
    )
    assert len(_books()) == 2


def test_rescan_updates_changed_file(folder_id: int, root: Path) -> None:
    make_epub(root / "dune.epub", title="Dune")
    _scan(folder_id)

    make_epub(root / "dune.epub", title="Dune Revised")
    result = _scan(folder_id)

    assert (result.added, result.updated, result.removed) == (0, 1, 0)
    assert _books()[0].title == "Dune Revised"


def test_rescan_removes_deleted_files(folder_id: int, root: Path) -> None:
    path = root / "gone.epub"
    make_epub(path, title="Gone")
    _scan(folder_id)
    with session_scope() as session:
        book = session.query(Book).one()
        session.add(Progression(book_id=book.id, progression=0.42))

    path.unlink()
    result = _scan(folder_id)

    assert (result.added, result.updated, result.removed) == (0, 0, 1)
    with session_scope() as session:
        assert session.query(Book).count() == 0
        assert session.query(Progression).count() == 0


def test_unavailable_folder_preserves_index(folder_id: int, root: Path) -> None:
    make_epub(root / "dune.epub", title="Dune")
    _scan(folder_id)

    shutil.rmtree(root)
    result = _scan(folder_id)

    assert result.status == "error"
    assert len(_books()) == 1
    folder = _folder()
    assert folder.last_scan_status == "error"
    assert folder.last_scan_error is not None
    assert "unavailable" in folder.last_scan_error.lower()


def test_unreadable_subdirectory_reports_partial(
    folder_id: int, root: Path
) -> None:
    make_epub(root / "ok.epub", title="OK")
    locked = root / "locked"
    locked.mkdir()
    make_epub(locked / "hidden.epub", title="Hidden")
    os.chmod(locked, 0o000)
    try:
        result = _scan(folder_id)
    finally:
        os.chmod(locked, 0o755)

    assert result.status == "partial"
    assert result.errors
    assert result.added == 1
    assert _folder().last_scan_status == "partial"


# --- metadata --------------------------------------------------------------


def test_epub_metadata_fields(folder_id: int, root: Path) -> None:
    make_epub(
        root / "dune.epub",
        title="Dune",
        authors=("Frank Herbert",),
        publisher="Chilton",
        language="en",
        isbn="9780441172719",
        description="<b>Epic</b> sci-fi",
        series="Dune",
        series_index=1.0,
    )

    _scan(folder_id)

    book = _books()[0]
    assert book.publisher == "Chilton"
    assert book.language == "en"
    assert book.isbn == "9780441172719"
    assert book.description == "Epic sci-fi"
    assert book.series == "Dune"
    assert book.series_index == 1.0


def test_corrupt_epub_falls_back_to_filename(folder_id: int, root: Path) -> None:
    (root / "the_book.epub").write_bytes(b"this is not a zip archive")

    result = _scan(folder_id)

    assert result.status == "ok"
    assert _books()[0].title == "the book"


def test_unsupported_format_uses_filename_fallback(
    folder_id: int, root: Path
) -> None:
    (root / "mystery_book.cbz").write_bytes(b"PK\x03\x04 not really a parsed archive")

    _scan(folder_id)

    book = _books()[0]
    assert book.title == "mystery book"
    assert book.file_format == "cbz"


# --- safety and statistics -------------------------------------------------


def test_scan_never_modifies_source_files(folder_id: int, root: Path) -> None:
    path = root / "dune.epub"
    make_epub(path, title="Dune")

    def snapshot() -> tuple[str, int, int]:
        current = path.stat()
        return (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            current.st_mtime_ns,
            current.st_size,
        )

    before = snapshot()
    _scan(folder_id)
    _scan(folder_id)

    assert snapshot() == before


def test_scan_records_statistics(folder_id: int, root: Path) -> None:
    make_epub(root / "dune.epub", title="Dune")

    result = _scan(folder_id)

    folder = _folder()
    assert folder.last_scan_status == "ok"
    assert folder.last_scan_at is not None
    assert folder.last_scan_duration is not None
    assert folder.last_scan_duration >= 0
    assert folder.last_scan_error is None
    assert result.duration >= 0


def test_concurrent_scan_rejected(folder_id: int) -> None:
    lock = folder_lock(folder_id)
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(ScanInProgress):
            scan_folder(folder_id, EXTENSIONS)
    finally:
        lock.release()


def test_scan_unknown_folder_raises(db) -> None:
    with pytest.raises(LookupError):
        scan_folder(9999, EXTENSIONS)


def test_scan_walks_and_parses_with_no_session_open(
    folder_id: int, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_epub(root / "dune.epub", title="Dune")
    open_sessions = 0
    observed: list[int] = []
    real_scope = scanner.session_scope

    @contextmanager
    def counting_scope():
        nonlocal open_sessions
        open_sessions += 1
        try:
            with real_scope() as session:
                yield session
        finally:
            open_sessions -= 1

    def observing(func):
        def wrapper(*args, **kwargs):
            observed.append(open_sessions)
            return func(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(scanner, "session_scope", counting_scope)
    monkeypatch.setattr(scanner, "_walk", observing(scanner._walk))
    monkeypatch.setattr(scanner, "_fields", observing(scanner._fields))

    result = _scan(folder_id)

    assert result.status == "ok"
    assert result.added == 1
    assert observed and set(observed) == {0}
