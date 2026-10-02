"""Turn indexed book rows into on-disk paths.

This is the single source of book paths: every download, cover or
optimization resolves a book through :func:`book_file`, never through
filesystem paths built from user input. Directory paths chosen by the
admin are validated by :func:`resolve_readable_dir`.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import abort

from bookflow.database.database import session_scope
from bookflow.database.models import Book


def resolve_readable_dir(value: str) -> tuple[Path | None, str | None]:
    """Expand ``value`` into an existing, readable directory.

    Returns ``(path, None)`` on success, otherwise ``(None, message)``
    with the user-facing reason the path was refused.
    """
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return None, "Path must be absolute."
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return None, "Folder does not exist."
    except OSError:
        return None, "Folder could not be resolved."
    except RuntimeError:
        return None, "Folder path contains a symlink loop."
    if not resolved.is_dir():
        return None, "Path is not a directory."
    if not os.access(resolved, os.R_OK | os.X_OK):
        return None, "Folder is not readable."
    return resolved, None


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
