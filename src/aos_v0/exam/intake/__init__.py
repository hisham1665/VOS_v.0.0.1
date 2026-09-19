"""Document intake and pre-processing (implementation plan Phase 3).

Supported inputs: PDF, JPG, JPEG, PNG, TIFF (single + multipage), ZIP archives
and folders of images. The public entry point is :func:`ingest`, which runs the
full pipeline:

    upload -> file validation -> page extraction -> image normalization
    -> rotation detection -> deskew -> quality detection -> page ordering

Every produced page carries a serializable :class:`PageMeta` record (the page
metadata system) with :class:`PageMetrics` and detected :class:`PageIssue`s;
the packet-level result is an :class:`IntakeResult` targeted by
:func:`aos_v0.exam.intake.pipeline.main` / the ``python3 -m aos_v0.exam.intake``
CLI.

PDF support uses ``pypdfium2`` (its absence yields an actionable error). Image
quality heuristics are pure-Pillow; heavy numerics (numpy/OpenCV) are not
required.
"""

from aos_v0.exam.intake.formats import IMAGE_EXTENSIONS, Frame, sniff_kind
from aos_v0.exam.intake.models import (
    IntakeError,
    IntakePage,
    IntakeResult,
    IntakeSummary,
    PageIssue,
    PageMeta,
    PageMetrics,
    PageStatus,
    UnsupportedFormatError,
)
from aos_v0.exam.intake.pipeline import ingest, main as cli_main
from aos_v0.exam.intake.quality import score_page, status_for_score

__all__ = [
    "Frame",
    "IMAGE_EXTENSIONS",
    "IntakeError",
    "IntakePage",
    "IntakeResult",
    "IntakeSummary",
    "PageIssue",
    "PageMeta",
    "PageMetrics",
    "PageStatus",
    "UnsupportedFormatError",
    "cli_main",
    "ingest",
    "score_page",
    "sniff_kind",
    "status_for_score",
]