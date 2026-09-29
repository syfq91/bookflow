"""Ebook metadata extraction.

Library files are opened read-only; nothing here writes to source files.
Extraction never raises: a corrupt or unsupported book falls back to
filename-derived metadata.
"""

from __future__ import annotations

import html
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader

logger = logging.getLogger(__name__)

_CONTAINER_PATH = "META-INF/container.xml"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class BookMetadata:
    """Metadata describing a single ebook file."""

    title: str | None = None
    authors: str | None = None
    publisher: str | None = None
    language: str | None = None
    isbn: str | None = None
    description: str | None = None
    series: str | None = None
    series_index: float | None = None
    file_format: str | None = None


def extract_metadata(path: Path) -> BookMetadata:
    """Extract metadata for ``path``, falling back to the filename."""
    file_format = path.suffix.lower().lstrip(".") or None
    meta = BookMetadata(file_format=file_format, title=_fallback_title(path))
    try:
        if file_format == "epub":
            _read_epub(path, meta)
        elif file_format == "pdf":
            _read_pdf(path, meta)
    except Exception:  # a bad file must never abort a scan
        logger.debug("metadata extraction failed for %s", path, exc_info=True)
    return meta


def _fallback_title(path: Path) -> str:
    stem = _WS_RE.sub(" ", path.stem.replace("_", " ").replace("-", " ")).strip()
    return stem or path.name


# --- EPUB ------------------------------------------------------------------


def _read_epub(path: Path, meta: BookMetadata) -> None:
    with zipfile.ZipFile(path) as archive:
        opf_path = _opf_path(archive)
        if not opf_path:
            return
        root = ElementTree.fromstring(archive.read(opf_path))
    _apply_opf(root, meta)


def _opf_path(archive: zipfile.ZipFile) -> str | None:
    try:
        container = ElementTree.fromstring(archive.read(_CONTAINER_PATH))
    except KeyError:
        return None
    for element in container.iter():
        if not isinstance(element.tag, str):
            continue
        if _localname(element.tag) == "rootfile":
            full_path = element.get("full-path")
            if full_path:
                return full_path
    return None


def _apply_opf(root: ElementTree.Element, meta: BookMetadata) -> None:
    title = _first_text(root, "title")
    if title:
        meta.title = title

    creators = _all_text(root, "creator")
    if creators:
        meta.authors = ", ".join(creators)

    publisher = _first_text(root, "publisher")
    if publisher:
        meta.publisher = publisher

    language = _first_text(root, "language")
    if language:
        meta.language = language

    description = _first_text(root, "description")
    if description:
        meta.description = _strip_html(description)

    isbn = _extract_isbn(root)
    if isbn:
        meta.isbn = isbn

    series, series_index = _extract_series(root)
    if series:
        meta.series = series
    if series_index is not None:
        meta.series_index = series_index


def _extract_isbn(root: ElementTree.Element) -> str | None:
    fallback: str | None = None
    for element in _elements(root, "identifier"):
        text = element.text or ""
        candidate = _normalize_isbn(text)
        if candidate is None:
            continue
        if "ISBN" in _attr(element, "scheme").upper():
            return candidate
        if fallback is None:
            fallback = candidate
    return fallback


def _normalize_isbn(text: str) -> str | None:
    value = text.lower().removeprefix("urn:isbn:")
    value = value.replace("-", "").replace(" ", "").upper()
    if len(value) in (10, 13) and all(ch in "0123456789X" for ch in value):
        return value
    return None


def _extract_series(root: ElementTree.Element) -> tuple[str | None, float | None]:
    series: str | None = None
    series_index: float | None = None
    for element in root.iter():
        if not isinstance(element.tag, str) or _localname(element.tag) != "meta":
            continue
        name = element.get("name") or ""
        content = (element.get("content") or "").strip()
        if name in ("calibre:series", "series"):
            series = content or None
        elif name in ("calibre:series_index", "series_index") and content:
            try:
                series_index = float(content)
            except ValueError:
                series_index = None
    return series, series_index


# --- PDF -------------------------------------------------------------------


def _read_pdf(path: Path, meta: BookMetadata) -> None:
    info = PdfReader(str(path)).metadata
    if not info:
        return
    if info.title:
        meta.title = str(info.title).strip() or meta.title
    if info.author:
        meta.authors = str(info.author).strip() or meta.authors
    if info.subject:
        meta.description = _strip_html(str(info.subject)) or meta.description


# --- XML helpers -----------------------------------------------------------


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _elements(root: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [
        element
        for element in root.iter()
        if isinstance(element.tag, str) and _localname(element.tag) == name
    ]


def _element_text(element: ElementTree.Element) -> str:
    """All text inside an element, including text nested in child markup."""
    return _WS_RE.sub(" ", "".join(element.itertext())).strip()


def _first_text(root: ElementTree.Element, name: str) -> str | None:
    for element in _elements(root, name):
        text = _element_text(element)
        if text:
            return text
    return None


def _all_text(root: ElementTree.Element, name: str) -> list[str]:
    values = []
    for element in _elements(root, name):
        text = _element_text(element)
        if text:
            values.append(text)
    return values


def _attr(element: ElementTree.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if _localname(key).lower() == name.lower():
            return value or ""
    return ""


def _strip_html(value: str) -> str:
    text = _TAG_RE.sub(" ", html.unescape(value))
    return _WS_RE.sub(" ", text).strip()
