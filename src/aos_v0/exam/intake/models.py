"""Page metadata system for the document intake pipeline (implementation plan Phase 3).

The pipeline is: upload -> file validation -> page extraction -> image
normalization -> rotation detection -> deskew -> quality detection -> page
ordering. The metadata models here are the serializable record of that pipeline
for every produced page (the "page metadata system" deliverable); the in-memory
PIL image itself is carried by the runtime-only :class:`IntakePage`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class PageStatus(StrEnum):
    """Result of the quality pass for a single page."""

    OK = "ok"
    REVIEW = "review"
    REJECTED = "rejected"


class PageIssue(StrEnum):
    """Quality problems the pipeline can detect (plan Phase 3 quality checks)."""

    BLURRY = "blurry"
    LOW_RESOLUTION = "low_resolution"
    EXCESSIVE_DARKNESS = "excessive_darkness"
    EXCESSIVE_BRIGHTNESS = "excessive_brightness"
    ROTATED = "rotated"
    SKEWED = "skewed"
    CROPPED = "cropped"
    BLANK = "blank"
    DUPLICATE = "duplicate"
    DAMAGED = "damaged"
    UNSUPPORTED_FORMAT = "unsupported_format"


class IntakeError(Exception):
    """Raised when a source cannot be ingested at all."""


class UnsupportedFormatError(IntakeError):
    """The source file is not a recognized PDF/image/zip."""


class PageMetrics(BaseModel):
    """Numeric measurements for one page's quality record.

    `rotation_degrees` is the corrective rotation the pipeline detected (and
    applied when `auto_rotate=True`); it is PIL-convention: +90 rotates 90 deg
    counterclockwise, -90 clockwise. 180-degree turns are indistinguishable
    from upside-down pages by projection heuristics and are reported as 0.
    `skew_degrees` is the rotation applied to deskew (negative of the tilt).
    """

    width: int = 0
    height: int = 0
    dpi: float = 150.0
    mean_luma: float = 255.0
    stddev_luma: float = 0.0
    blur_variance: float = 0.0
    border_ink_ratio: float = 0.0
    rotation_degrees: int = 0
    rotation_confidence: float = 0.0
    skew_degrees: float = 0.0
    duplicate_of: Optional[int] = None


class PageMeta(BaseModel):
    """Serializable metadata record for a single ingested page."""

    index: int
    source: str
    member: Optional[str] = None
    page_no: Optional[int] = None
    status: PageStatus = PageStatus.OK
    issues: List[PageIssue] = Field(default_factory=list)
    adjusted: List[str] = Field(default_factory=list)
    quality_score: float = 1.0
    metrics: PageMetrics = Field(default_factory=PageMetrics)


class IntakeSummary(BaseModel):
    """Packet-level record: verdict, aggregate issues and edge-case findings."""

    source: str
    kind: str
    page_count: int = 0
    rejected_count: int = 0
    verdict: PageStatus = PageStatus.OK
    quality_score: float = 1.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    unique_page_sizes: List[List[int]] = Field(default_factory=list)
    mixed_page_sizes: bool = False
    issues: List[PageIssue] = Field(default_factory=list)
    duplicate_pages: List[int] = Field(default_factory=list)
    out_of_order_pages: List[int] = Field(default_factory=list)
    missing_page_count: int = 0
    extra_page_count: int = 0
    truncated: bool = False


class IntakePage:
    """A normalized page: serializable :class:`PageMeta` plus the live image."""

    __slots__ = ("meta", "image")

    def __init__(self, meta: PageMeta, image=None) -> None:
        self.meta = meta
        self.image = image


class IntakeResult:
    """The outcome of ingesting one source (file / zip / folder / PDF)."""

    __slots__ = ("summary", "pages", "rejected")

    def __init__(
        self,
        summary: IntakeSummary,
        pages: List[IntakePage],
        rejected: Optional[List[str]] = None,
    ) -> None:
        self.summary = summary
        self.pages = pages
        self.rejected = list(rejected or [])

    @property
    def quality_score(self) -> float:
        return self.summary.quality_score

    @property
    def verdict(self) -> PageStatus:
        return self.summary.verdict

    def page_documents(self) -> List[Dict[str, object]]:
        """Serializable page metadata (JSON-storable via model_dump on items)."""
        return [p.meta.model_dump(mode="json") for p in self.pages]