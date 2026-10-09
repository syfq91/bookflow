"""Native XTG/XTC binary encoder."""

from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image

XTC_MARK = 0x00435458
XTG_MARK = 0x00475458
XTC_HEADER_SIZE = 56
XTG_HEADER_SIZE = 22
INDEX_ENTRY_SIZE = 16
XTC_VERSION = 0x0100


def image_to_xtg_bytes(
    image: Image.Image,
    *,
    size: tuple[int, int] = (480, 800),
    threshold: int = 200,
    pre_binarized: bool = True,
) -> bytes:
    """Encode an image as spec-compliant XTG bytes.

    Uses Pillow's native C-level .tobytes() for fast bit-packing into 1-bit
    monochrome scanlines (8 pixels per byte, MSB-first).
    """
    width, height = size
    if image.size != size:
        raise ValueError(f"Expected image size {size}, got {image.size}")

    if pre_binarized:
        if image.mode != "1":
            image = image.convert("1")
    else:
        if image.mode != "L":
            image = image.convert("L")
        image = image.point(lambda p: 255 if p >= threshold else 0).convert("1")

    data = image.tobytes()
    data_size = len(data)
    header = struct.pack(
        "<IHHBBI",
        XTG_MARK,
        width,
        height,
        0,  # colorMode: monochrome
        0,  # compression: none
        data_size,
    )
    return header + bytes(8) + data


def build_xtc(
    page_xtgs: list[bytes],
    out_path: Path,
) -> None:
    """Pack sorted XTG page frames into an XTC container file."""
    if not page_xtgs:
        raise ValueError("page_xtgs must not be empty")

    page_count = len(page_xtgs)
    index_offset = XTC_HEADER_SIZE
    data_offset = index_offset + page_count * INDEX_ENTRY_SIZE

    index_entries: list[tuple[int, int, int, int]] = []
    current_offset = data_offset
    for xtg in page_xtgs:
        width, height = struct.unpack_from("<HH", xtg, 4)
        index_entries.append((current_offset, len(xtg), width, height))
        current_offset += len(xtg)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        f.write(struct.pack("<I", XTC_MARK))
        f.write(struct.pack("<H", XTC_VERSION))
        f.write(struct.pack("<H", page_count))
        f.write(struct.pack("<B", 0))  # read direction: LTR (0)
        f.write(struct.pack("<BBB", 0, 0, 0))
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<Q", 0))
        f.write(struct.pack("<Q", index_offset))
        f.write(struct.pack("<Q", data_offset))
        f.write(struct.pack("<Q", 0))
        f.write(struct.pack("<Q", 0))

        for offset, page_size, width, height in index_entries:
            f.write(struct.pack("<Q", offset))
            f.write(struct.pack("<I", page_size))
            f.write(struct.pack("<HH", width, height))

        for xtg in page_xtgs:
            f.write(xtg)
