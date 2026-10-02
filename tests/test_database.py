from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from bookflow.config import Settings
from bookflow.database.database import init_engine, reset_engine, session_scope
from bookflow.database.models import Book, LibraryFolder, OptimizedBook
from factories import alembic_config

EXPECTED_TABLES = {"library_folders", "books", "progressions", "optimized_books"}


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


def test_upgrade_head_creates_all_tables(settings: Settings) -> None:
    command.upgrade(alembic_config(settings.database_url), "head")

    engine = init_engine(settings)
    tables = set(inspect(engine).get_table_names())

    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables


def test_models_match_migrations(settings: Settings) -> None:
    command.upgrade(alembic_config(settings.database_url), "head")

    cfg = alembic_config(settings.database_url)
    command.check(cfg)


def test_session_roundtrip(settings: Settings) -> None:
    command.upgrade(alembic_config(settings.database_url), "head")
    init_engine(settings)

    with session_scope() as session:
        folder = LibraryFolder(path="/media/books", name="books")
        session.add(folder)
        session.flush()
        session.add(Book(folder_id=folder.id, relative_path="dune.epub", title="Dune"))

    with session_scope() as session:
        folder = session.scalars(select(LibraryFolder)).one()
        assert folder.path == "/media/books"
        assert folder.books[0].relative_path == "dune.epub"


def test_book_relative_path_unique(settings: Settings) -> None:
    command.upgrade(alembic_config(settings.database_url), "head")
    init_engine(settings)

    with session_scope() as session:
        folder = LibraryFolder(path="/media/books", name="books")
        session.add(folder)
        session.flush()
        session.add(Book(folder_id=folder.id, relative_path="dune.epub"))

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            folder_id = session.scalars(select(LibraryFolder)).one().id
            session.add(Book(folder_id=folder_id, relative_path="dune.epub"))


def test_optimized_book_profile_unique(settings: Settings) -> None:
    command.upgrade(alembic_config(settings.database_url), "head")
    init_engine(settings)

    with session_scope() as session:
        folder = LibraryFolder(path="/media/books", name="books")
        session.add(folder)
        session.flush()
        book = Book(folder_id=folder.id, relative_path="dune.epub")
        session.add(book)
        session.flush()
        session.add(OptimizedBook(book_id=book.id, profile="x4"))
        session.add(OptimizedBook(book_id=book.id, profile="x3"))

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            book_id = session.scalars(select(Book)).one().id
            session.add(OptimizedBook(book_id=book_id, profile="x4"))
