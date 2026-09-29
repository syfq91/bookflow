"""Factories for building sample ebook files in tests."""

from __future__ import annotations

import base64
import mimetypes
import secrets
import zipfile
from pathlib import Path

from pypdf import PdfWriter

# 1x1 transparent PNG
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def make_epub(
    path: Path,
    *,
    title: str,
    authors: tuple[str, ...] = (),
    publisher: str | None = None,
    language: str | None = None,
    isbn: str | None = None,
    description: str | None = None,
    series: str | None = None,
    series_index: float | None = None,
    cover: tuple[str, bytes] | None = None,
) -> Path:
    """Write a minimal EPUB carrying the given metadata."""
    entries = [
        '<dc:identifier id="uid">urn:uuid:bookflow-test</dc:identifier>',
        f"<dc:title>{title}</dc:title>",
    ]
    entries += [f"<dc:creator>{author}</dc:creator>" for author in authors]
    if publisher:
        entries.append(f"<dc:publisher>{publisher}</dc:publisher>")
    if language:
        entries.append(f"<dc:language>{language}</dc:language>")
    if isbn:
        entries.append(f'<dc:identifier opf:scheme="ISBN">{isbn}</dc:identifier>')
    if description:
        entries.append(f"<dc:description>{description}</dc:description>")
    if series:
        entries.append(f'<meta name="calibre:series" content="{series}"/>')
    if series_index is not None:
        entries.append(
            f'<meta name="calibre:series_index" content="{series_index}"/>'
        )

    cover_item = ""
    if cover:
        cover_name, _cover_bytes = cover
        entries.append('<meta name="cover" content="cover-image"/>')
        cover_media_type = mimetypes.guess_type(cover_name)[0] or "image/jpeg"
        cover_item = (
            f'<item id="cover-image" href="{cover_name}" '
            f'media-type="{cover_media_type}"/>'
        )

    metadata = "\n    ".join(entries)
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf"
         xmlns:opf="http://www.idpf.org/2007/opf"
         version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    {metadata}
  </metadata>
  <manifest>
    <item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    {cover_item}
  </manifest>
  <spine>
    <itemref idref="c1"/>
  </spine>
</package>
"""
    chapter = (
        "<html xmlns='http://www.w3.org/1999/xhtml'>"
        "<body><p>text</p></body></html>"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/chapter.xhtml", chapter)
        if cover:
            archive.writestr(f"OEBPS/{cover[0]}", cover[1])
    return path


def make_pdf(path: Path, *, title: str | None = None, author: str | None = None):
    """Write a one-page PDF carrying the given document information."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    info: dict[str, str] = {}
    if title:
        info["/Title"] = title
    if author:
        info["/Author"] = author
    if info:
        writer.add_metadata(info)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def csrf_token(client) -> str:
    """Return the test client's session CSRF token, minting one if needed."""
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            sess["csrf_token"] = token
        return token
