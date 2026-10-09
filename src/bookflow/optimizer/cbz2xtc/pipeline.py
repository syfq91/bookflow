"""CBZ archive reading and conversion pipeline orchestration."""

from __future__ import annotations

import logging
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bookflow.optimizer.cbz2xtc.config import ConvertOptions
from bookflow.optimizer.cbz2xtc.encoder import build_xtc
from bookflow.optimizer.cbz2xtc.optimize import process_image

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
_OS_METADATA_PREFIX = "__macosx"


def _is_image_entry(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered.endswith(_IMAGE_EXTENSIONS)
        and not lowered.startswith(_OS_METADATA_PREFIX)
        and "/." not in lowered
        and not Path(lowered).name.startswith(".")
    )


def convert_cbz_to_xtc(
    cbz_path: Path,
    output_path: Path,
    *,
    options: ConvertOptions | None = None,
) -> tuple[bool, str | None]:
    """Convert a CBZ archive directly to an XTC container file.

    Extracts images in-memory, scales, dithers, pads, and packs into XTC
    without writing intermediate PNG files to disk. Returns (success, error).
    """
    opts = options or ConvertOptions()
    if not cbz_path.is_file():
        return False, f"CBZ source file does not exist: {cbz_path}"

    try:
        with zipfile.ZipFile(cbz_path, "r") as archive:
            image_names = sorted(
                name for name in archive.namelist() if _is_image_entry(name)
            )
            if not image_names:
                return False, f"no images found in CBZ archive: {cbz_path.name}"

            max_workers = min(opts.max_workers, os.cpu_count() or 1)

            def _process_one(name: str) -> list[bytes]:
                try:
                    data = archive.read(name)
                    return process_image(data, opts)
                except Exception as exc:
                    logger.warning(
                        "failed to extract page %s from %s: %s",
                        name,
                        cbz_path.name,
                        exc,
                    )
                    return []

            if max_workers > 1 and len(image_names) > 1:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    ordered_results = list(executor.map(_process_one, image_names))
            else:
                ordered_results = [_process_one(name) for name in image_names]

            all_xtgs: list[bytes] = [
                frame for frames in ordered_results for frame in frames
            ]

            if not all_xtgs:
                return False, f"no valid pages produced from {cbz_path.name}"

            build_xtc(all_xtgs, output_path)
            return True, None

    except zipfile.BadZipFile as exc:
        logger.warning("invalid zip file %s: %s", cbz_path, exc)
        return False, f"invalid CBZ zip archive: {exc}"
    except Exception as exc:
        logger.exception("unexpected error converting %s to XTC", cbz_path)
        return False, f"conversion error: {exc}"
