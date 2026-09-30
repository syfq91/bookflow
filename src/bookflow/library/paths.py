"""Turn indexed book rows into on-disk paths.

This is the single source of book paths: every download, cover or
optimization resolves a book through :func:`book_file`, never through
filesystem paths built from user input.
"""

from __future__ import annotations

from pathlib import Path

from flask import abort

from bookflow.database.database import session_scope
from bookflow.database.models import Book


def book_file(book_id: int) -> Path:
    """Return the on-disk book file, refusing paths outside the folder root."""
    with session_scope() as session:
        book = session.get(Book, book_id)
        if book is None:
            abort(404)
        folder_path = Path(book.folder.path)
        relative_path = book.relative_path
    root = folder_path.resolve()
    target = (folder_path / relative_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        abort(404)
    return target
