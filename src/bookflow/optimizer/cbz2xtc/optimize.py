"""Page optimization: spread-splitting, resizing, dithering, and padding."""

from __future__ import annotations

import io
import logging

from PIL import Image

from bookflow.optimizer.cbz2xtc.config import ConvertOptions
from bookflow.optimizer.cbz2xtc.encoder import image_to_xtg_bytes

logger = logging.getLogger(__name__)


def process_image(
    img_data: bytes,
    options: ConvertOptions,
) -> list[bytes]:
    """Process raw image bytes from CBZ into one or more XTG page frames.

    Handles two-page spread detection/splitting, aspect-ratio scaling,
    Floyd-Steinberg dithering, canvas padding, and conversion to XTG bytes.
    """
    try:
        with Image.open(io.BytesIO(img_data)) as img:
            if img.mode not in ("L", "RGB"):
                img = img.convert("RGB")

            width, height = img.size

            # Check if this is a horizontal spread (two pages side-by-side)
            if width > height and options.split_spreads:
                half_width = width // 2
                # Manga standard: right page is read first, then left page
                right_page = img.crop((half_width, 0, width, height))
                left_page = img.crop((0, 0, half_width, height))

                frames: list[bytes] = []
                frames.append(_render_page_frame(right_page, options))
                frames.append(_render_page_frame(left_page, options))
                return frames

            return [_render_page_frame(img, options)]
    except Exception as exc:
        logger.warning("failed to process comic page image: %s", exc)
        return []


def _render_page_frame(
    img: Image.Image,
    options: ConvertOptions,
) -> bytes:
    """Scale, dither, center-pad, and encode a single PIL image to XTG bytes."""
    target_width = options.target_width
    target_height = options.target_height
    img_width, img_height = img.size

    scale = min(target_width / img_width, target_height / img_height)
    new_width = max(int(img_width * scale), 1)
    new_height = max(int(img_height * scale), 1)

    resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    if options.dither:
        dithered = resized.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    else:
        dithered = (
            resized.convert("L")
            .point(lambda p: 255 if p >= options.threshold else 0)
            .convert("1")
        )

    canvas_color = 1 if options.padding_color >= 128 else 0
    canvas = Image.new("1", (target_width, target_height), color=canvas_color)
    x = (target_width - new_width) // 2
    y = (target_height - new_height) // 2
    canvas.paste(dithered, (x, y))

    return image_to_xtg_bytes(
        canvas,
        size=(target_width, target_height),
        pre_binarized=True,
    )
