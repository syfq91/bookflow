"""Read-only directory listing for the admin folder picker.

Browsing never writes to the filesystem and never touches the index;
it only lists directories so the admin can pick a path to register
through ``add_folder()``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from bookflow.library.paths import resolve_readable_dir

MAX_ENTRIES = 500


@dataclass(frozen=True, slots=True)
class BrowseResult:
    """Outcome of an attempt to list one directory."""

    ok: bool
    path: str | None = None
    parent: str | None = None
    entries: list[tuple[str, str]] = field(default_factory=list)
    crumbs: list[tuple[str, str]] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


def browse_directory(raw_path: str | None, root: Path) -> BrowseResult:
    """List the subdirectories of ``raw_path`` clamped to ``root``.

    An empty or missing ``raw_path`` starts at the browse root. The
    resolved current directory is always inside ``root``; ``parent`` is
    None at the root itself.
    """
    try:
        resolved_root = root.expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return BrowseResult(ok=False, error=f"Browse root {root} is not usable.")
    if not resolved_root.is_dir():
        return BrowseResult(ok=False, error=f"Browse root {root} is not a directory.")

    value = (raw_path or "").strip()
    if not value:
        return _list(resolved_root, resolved_root)

    resolved, error = resolve_readable_dir(value)
    if error is not None or resolved is None:
        return BrowseResult(ok=False, error=error)

    if not resolved.is_relative_to(resolved_root):
        return BrowseResult(ok=False, error="Path is outside the browse root.")

    return _list(resolved, resolved_root)


def _list(current: Path, root: Path) -> BrowseResult:
    entries: list[tuple[str, str]] = []
    truncated = False
    try:
        with os.scandir(current) as iterator:
            dirs = sorted(
                (entry for entry in iterator if _is_dir(entry)),
                key=lambda entry: entry.name.casefold(),
            )
    except PermissionError:
        return BrowseResult(ok=False, error="Folder is not readable.")
    except OSError:
        return BrowseResult(ok=False, error="Folder could not be listed.")

    entries = [(entry.name, str(current / entry.name)) for entry in dirs[:MAX_ENTRIES]]
    truncated = len(dirs) > MAX_ENTRIES

    parent: str | None = None
    if current != root and current.parent.is_relative_to(root):
        parent = str(current.parent)

    crumbs: list[tuple[str, str]] = [(root.name or str(root), str(root))]
    walked = root
    for part in current.relative_to(root).parts:
        walked = walked / part
        crumbs.append((part, str(walked)))

    return BrowseResult(
        ok=True,
        path=str(current),
        parent=parent,
        entries=entries,
        crumbs=crumbs,
        truncated=truncated,
    )


def _is_dir(entry: os.DirEntry[str]) -> bool:
    """True for directories, following symlinks; unreadable links are skipped."""
    try:
        return entry.is_dir()
    except OSError:
        return False
