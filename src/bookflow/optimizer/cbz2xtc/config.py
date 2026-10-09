"""Conversion options for CBZ → XTC pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ConvertOptions:
    """Options controlling CBZ extraction, page processing, and XTC output."""

    target_width: int = 480
    target_height: int = 800
    dither: bool = True
    split_spreads: bool = True
    rotate_spreads: bool = True
    rotate_angle: int = -90
    threshold: int = 200
    padding_color: int = 255
    max_workers: int = 4
