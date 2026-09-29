"""Library folder management.

Registering or removing a folder only changes the SQLite index: the
filesystem is never written to, renamed or deleted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from bookflow.database.database import session_scope
from bookflow.database.models import Book, LibraryFolder


@dataclass(frozen=True)
class FolderResult:
    """Outcome of an attempt to register a folder."""

    ok: bool
    folder_id: int | None = None
    path: str | None = None
    name: str | None = None
    error: str | None = None


def add_folder(raw_path: str) -> FolderResult:
    """Validate and register a library folder."""
    value = (raw_path or "").strip()
    if not value:
        return FolderResult(ok=False, error="Enter a folder path.")

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return FolderResult(ok=False, error="Path must be absolute.")

    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return FolderResult(ok=False, error="Folder does not exist.")
    except OSError:
        return FolderResult(ok=False, error="Folder could not be resolved.")
    except RuntimeError:
        return FolderResult(ok=False, error="Folder path contains a symlink loop.")

    if not resolved.is_dir():
        return FolderResult(ok=False, error="Path is not a directory.")
    if not os.access(resolved, os.R_OK | os.X_OK):
        return FolderResult(ok=False, error="Folder is not readable.")

    path = str(resolved)
    name = resolved.name or path

    with session_scope() as session:
        existing = session.scalars(select(LibraryFolder)).all()
        for folder in existing:
            other = Path(folder.path)
            if other == resolved:
                return FolderResult(ok=False, error="Folder is already registered.")
            if resolved.is_relative_to(other):
                return FolderResult(
                    ok=False, error=f"Path is inside registered folder {other}."
                )
            if other.is_relative_to(resolved):
                return FolderResult(
                    ok=False, error=f"Registered folder {other} is inside this path."
                )

        folder = LibraryFolder(path=path, name=name)
        session.add(folder)
        session.flush()
        return FolderResult(ok=True, folder_id=folder.id, path=path, name=name)


def remove_folder(folder_id: int) -> bool:
    """Unregister a folder and drop its indexed books. Files are untouched."""
    with session_scope() as session:
        folder = session.get(LibraryFolder, folder_id)
        if folder is None:
            return False
        session.delete(folder)
        return True


def get_folder(folder_id: int) -> dict[str, object] | None:
    """Return display data for one folder, or None when unknown."""
    folders = {folder["id"]: folder for folder in list_folders()}
    return folders.get(folder_id)


def list_folders() -> list[dict[str, object]]:
    """Return every registered folder with its index statistics."""
    with session_scope() as session:
        folders = session.scalars(
            select(LibraryFolder).order_by(LibraryFolder.path)
        ).all()
        stats = {
            row[0]: (row[1], row[2])
            for row in session.execute(
                select(
                    Book.folder_id,
                    func.count(Book.id),
                    func.coalesce(func.sum(Book.file_size), 0),
                ).group_by(Book.folder_id)
            )
            if row[0] is not None
        }
        return [
            {
                "id": folder.id,
                "path": folder.path,
                "name": folder.name,
                "books": stats.get(folder.id, (0, 0))[0],
                "size": int(stats.get(folder.id, (0, 0))[1]),
                "last_scan_at": folder.last_scan_at,
                "last_scan_duration": folder.last_scan_duration,
                "last_scan_status": folder.last_scan_status,
                "last_scan_error": folder.last_scan_error,
            }
            for folder in folders
        ]
