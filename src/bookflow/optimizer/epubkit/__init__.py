"""Vendored copy of epubkit's EPUB processing pipeline.

Upstream: https://github.com/b1rdmania/epubkit (MIT). See NOTICE.
"""

from bookflow.optimizer.epubkit.epub_processor import (
    ProcessingOptions,
    ProcessingReport,
    process_epub,
)

__all__ = ["ProcessingOptions", "ProcessingReport", "process_epub"]
