"""Phase 4 OCR/document-understanding data contract (implementation plan Phase 4).

Defines the *structured OCR output* deliverable: the plan's canonical JSON
shape

    {"page": 2, "blocks": [{"type": "text", "text": "TCP is...",
                            "bbox": [100, 200, 800, 400], "confidence": 0.93}]}

plus the page/document/result envelope the OCR service (`pipeline.py`) returns,
and the typed errors the adapters raise. The schema is deliberately model
agnostic: an adapter (PaddleOCR, TrOCR, a VLM, or the local structural engine)
hands the service blocks in this shape, and downstream phases (5+ sheet
structuring, 6+ semantic evaluation) consume only this contract.

Golden rule inherited from the plan: an OCR block carries *what is physically
written on the page* and a confidence for it. It never decides marks, and text
whose transcription failed is reported as empty with low confidence so the
service escalates to human review instead of fabricating content.
"""

from __future__ import annotations

from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aos_v0.exam.models import EvalReviewReason


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OcrBlockType(StrEnum):
    """What a detected block is, per the plan's layout taxonomy."""

    TEXT = "text"
    HEADER = "header"
    QUESTION_LABEL = "question_label"
    ANSWER = "answer"
    TABLE = "table"
    DIAGRAM = "diagram"
    FIGURE = "figure"
    FORMULA = "formula"


class RegionType(StrEnum):
    """Layout regions a page is segmented into (plan Phase 4 layout step)."""

    TEXT = "text"
    QUESTION = "question"
    ANSWER = "answer"
    TABLE = "table"
    DIAGRAM = "diagram"
    HEADER = "header"
    FIGURE = "figure"


class OcrStatus(StrEnum):
    """Final state of one page / the whole document after the fallback chain."""

    OK = "ok"                      # accepted output, confidence >= gate
    REVIEW = "review"              # routed to human review by the fallback chain
    DEGRADED = "degraded"          # accepted structurally but damaged/partial
    UNAVAILABLE = "unavailable"    # no adapter could run at all


class OcrAdapterAttemptStatus(StrEnum):
    """Why one adapter's attempt ended, recorded in the fallback trace."""

    OK = "ok"
    LOW_CONFIDENCE = "low_confidence"
    UNAVAILABLE = "unavailable"    # declared transport / model not installed
    ERROR = "error"


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class OcrError(RuntimeError):
    """Base for all OCR-service errors. Messages never carry secrets."""


class OcrUnavailableError(OcrError):
    """The adapter's model transport is not wired (declared-only, gap G3).

    Raised by the declared adapters for the Phase-1 selected models (PP-OCRv6,
    TrOCR, Qwen2.5-VL, LayoutLMv3) until a real execution transport exists,
    mirroring the declared-only exam resources in `exam/resources.py`.
    """


class OcrRecognitionError(OcrError):
    """The adapter ran but could not parse the page (damaged input, bad crop)."""


# ---------------------------------------------------------------------------
# Blocks, regions, pages, document
# ---------------------------------------------------------------------------


class OcrLine(BaseModel):
    """One text line inside a block (optional fine structure)."""

    text: str = ""
    bbox: List[int] = Field(default_factory=list, min_length=4, max_length=4)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OcrBlock(BaseModel):
    """One OCR output block -- the plan's canonical block record.

    `bbox` is [x1, y1, x2, y2] in page-image pixels (top-left origin).
    `transcribed=False` marks a detected region whose text could not be read:
    `text` is then empty and `confidence` is low by construction, so the
    confidence gate escalates rather than emitting empty text as if valid.
    """

    model_config = ConfigDict(extra="forbid")

    type: OcrBlockType = OcrBlockType.TEXT
    text: str = ""
    bbox: List[int] = Field(default_factory=list, min_length=4, max_length=4)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    transcribed: bool = True
    lines: List[OcrLine] = Field(default_factory=list)

    @field_validator("bbox")
    @classmethod
    def _valid_bbox(cls, bbox: List[int]) -> List[int]:
        if len(bbox) != 4:
            raise ValueError("bbox must be [x1, y1, x2, y2]")
        x1, y1, x2, y2 = bbox
        if x1 < 0 or y1 < 0 or x2 < x1 or y2 < y1:
            raise ValueError(f"invalid bbox {bbox}")
        return bbox

    @property
    def text_clean(self) -> str:
        return self.text.strip()

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bbox
        return max(1, (x2 - x1) * (y2 - y1))


class LayoutRegion(BaseModel):
    """A layout-labelled region of the page (plan Phase 4 layout output)."""

    model_config = ConfigDict(extra="forbid")

    region_type: RegionType
    bbox: List[int] = Field(default_factory=list, min_length=4, max_length=4)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    label: str = ""

    @field_validator("bbox")
    @classmethod
    def _valid_bbox(cls, bbox: List[int]) -> List[int]:
        if len(bbox) != 4:
            raise ValueError("bbox must be [x1, y1, x2, y2]")
        x1, y1, x2, y2 = bbox
        if x1 < 0 or y1 < 0 or x2 < x1 or y2 < y1:
            raise ValueError(f"invalid bbox {bbox}")
        return bbox


class OcrPage(BaseModel):
    """Structured OCR output for one page (plan Phase 4 output schema)."""

    model_config = ConfigDict(extra="forbid")

    page_no: int
    source: str = ""
    blocks: List[OcrBlock] = Field(default_factory=list)
    regions: List[LayoutRegion] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: OcrStatus = OcrStatus.OK
    review_reasons: List[EvalReviewReason] = Field(default_factory=list)

    @property
    def text(self) -> str:
        """Joined, trimmed block text for downstream (Phase 5) consumers."""
        parts: List[str] = []
        for block in self.blocks:
            cleaned = block.text_clean
            if cleaned:
                parts.append(cleaned)
        return "\n".join(parts)

    @property
    def transcript_block(self) -> OcrBlock:
        """A single merged block the service surface can hand to run_fns.

        Layout/geometry is intentionally dropped here: the coarse kernel
        contract is `(input, instruction) -> str`, and structured blocks are the
        Phase 5/6 domain layer's job. A single aggregate ``confidence:`` marker
        leads the text so the kernel's `tool.low_confidence` detector (Seam S5)
        can gate on page-level readability -- per-block confidence never leaks
        into the coarse surface, which would false-positive the detector on an
        otherwise healthy page.
        """
        text = self.text
        return OcrBlock(
            type=OcrBlockType.TEXT,
            text=(f"confidence: {self.confidence:.2f}\n{text}" if text.strip()
                  else f"confidence: {self.confidence:.2f}"),
            bbox=[0, 0, 0, 0],
            confidence=self.confidence,
            transcribed=bool(self.text),
        )


class OcrDocument(BaseModel):
    """All pages of one paper, with document-level verdict fields."""

    model_config = ConfigDict(extra="forbid")

    pages: List[OcrPage] = Field(default_factory=list)

    @property
    def block_count(self) -> int:
        return sum(len(p.blocks) for p in self.pages)

    @property
    def mean_confidence(self) -> float:
        if not self.pages:
            return 0.0
        return round(sum(p.confidence for p in self.pages) / len(self.pages), 4)

    @property
    def needs_review_pages(self) -> List[int]:
        return [p.page_no for p in self.pages if p.status != OcrStatus.OK]

    @property
    def worst_status(self) -> OcrStatus:
        if not self.pages:
            return OcrStatus.UNAVAILABLE
        rank = {OcrStatus.OK: 0, OcrStatus.DEGRADED: 1,
                OcrStatus.REVIEW: 2, OcrStatus.UNAVAILABLE: 3}
        return max(self.pages, key=lambda p: rank[p.status]).status

    def to_plan_json(self) -> List[dict]:
        """Exactly the plan's phase-4 JSON surface: one dict per page."""
        return [
            {
                "page": page.page_no,
                "blocks": [
                    {
                        "type": block.type.value,
                        "text": block.text,
                        "bbox": block.bbox,
                        "confidence": block.confidence,
                    }
                    for block in page.blocks
                ],
            }
            for page in self.pages
        ]


# ---------------------------------------------------------------------------
# Service envelope
# ---------------------------------------------------------------------------


class OcrAdapterAttempt(BaseModel):
    """One step of the fallback chain, recorded for audit/review."""

    adapter_id: str
    model: str
    status: OcrAdapterAttemptStatus
    confidence: Optional[float] = None
    detail: str = ""


class OcrResult(BaseModel):
    """End-to-end OCR-service result for one page or one document."""

    model_config = ConfigDict(extra="forbid")

    document: OcrDocument
    status: OcrStatus = OcrStatus.OK
    fallback_trace: List[OcrAdapterAttempt] = Field(default_factory=list)
    review_reasons: List[EvalReviewReason] = Field(default_factory=list)

    @property
    def summary(self) -> dict:
        return {
            "pages": len(self.document.pages),
            "blocks": self.document.block_count,
            "mean_confidence": self.document.mean_confidence,
            "status": self.status.value,
            "needs_review_pages": self.document.needs_review_pages,
            "review_reasons": [r.value for r in self.review_reasons],
        }

    def to_plan_json(self) -> List[dict]:
        return self.document.to_plan_json()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class OcrSettings(BaseModel):
    """Thresholds gating the OCR fallback ladder (plan Phase 4 confidence check).

    Mirror the evaluation settings: confidence decides whether extra effort /
    human review is needed -- it never overrides an OCR output silently.
    """

    confidence_high: float = Field(default=0.9, ge=0.0, le=1.0)
    confidence_low: float = Field(default=0.6, ge=0.0, le=1.0)
    fallback_enabled: bool = True
    vision_fallback_enabled: bool = True
    review_on_low_confidence: bool = True

    def verdict(self, confidence: float) -> OcrStatus:
        """Map a page confidence to its disposition."""
        if confidence >= self.confidence_high:
            return OcrStatus.OK
        if confidence >= self.confidence_low:
            return OcrStatus.DEGRADED
        return OcrStatus.REVIEW


class OcrStrategy(StrEnum):
    """Which adapter chain the service walks (by handwriting family)."""

    AUTO = "auto"
    PRINTED = "printed"
    HANDWRITTEN = "handwritten"