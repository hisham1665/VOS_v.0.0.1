"""Phase 5 structured answer-sheet data contract (implementation plan Phase 5).

The phase-5 deliverable is the plan's canonical JSON surface:

    {"student": {"name": ..., "roll_no": ...},
     "answers": [{"question_id": "Q1", "pages": [1], "text": "...",
                  "regions": [...]}]}

On top of the Phase-4 OCR contract this module defines the answer-sheet
parser's schema: student identity (+ roster validation), question-to-answer
entries, the edge-case issue taxonomy the plan lists (missing question numbers,
question-number OCR errors, subquestions, continuations across pages,
out-of-order answers, crossed-out answers, multiple attempts, blank answers,
answers outside the answer region), and the `StructuredAnswerSheet` envelope.

Golden rule: the parser never decides anything by itself. An extraction it
cannot anchor is surfaced as a `MappingIssue` plus an `EvalReviewReason`, so an
ambiguous paper routes to human review instead of being marked from structurally
uncertain evidence.
"""

from __future__ import annotations

from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from aos_v0.exam.models import EvalReviewReason
from aos_v0.exam.ocr.models import LayoutRegion, OcrBlock


class SheetStatus(StrEnum):
    """Whether the structured sheet can be evaluated as-is."""

    OK = "ok"
    REVIEW = "review"      # a review reason demands a human pass


class StructuringError(RuntimeError):
    """Base error for the answer-sheet parser."""


class MappingIssue(StrEnum):
    """Edge cases the plan lists, surfaced per-entry and/or at sheet level."""

    MISSING_QUESTION_NUMBER = "missing_question_number"
    QUESTION_NUMBER_OCR_ERROR = "question_number_ocr_error"
    MULTIPLE_ATTEMPTS = "multiple_attempts"
    CROSSED_OUT = "crossed_out"
    BLANK_ANSWER = "blank_answer"
    AMBIGUOUS_MAPPING = "ambiguous_mapping"
    UNKNOWN_QUESTION = "unknown_question"
    CONTINUATION = "continuation"
    OUT_OF_ORDER = "out_of_order"
    IDENTITY_UNVERIFIED = "identity_unverified"
    IDENTITY_MISMATCH = "identity_mismatch"


class StudentIdentity(BaseModel):
    """Extracted candidate identity, plus roster cross-check evidence."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    roll_no: str = ""
    register_no: str = ""
    class_: str = ""
    department: str = ""
    exam: str = ""
    subject: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_page: int = 0
    # Roster validation evidence (empty when no roster configured).
    matched: bool = False
    roster_roll: str = ""
    roster_name: str = ""

    @property
    def identity_ok(self) -> bool:
        """A usable identity needs at least name and roll number."""
        return bool(self.name.strip() and self.roll_no.strip())

    @property
    def populated_fields(self) -> int:
        return sum(1 for f in (
            self.name, self.roll_no, self.register_no, self.class_,
            self.department, self.exam, self.subject,
        ) if f.strip())


class AnswerSheetEntry(BaseModel):
    """One structured answer: the plan's per-question record."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    pages: List[int] = Field(default_factory=list)
    text: str = ""
    blocks: List[OcrBlock] = Field(default_factory=list)
    regions: List[LayoutRegion] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    issues: List[MappingIssue] = Field(default_factory=list)
    crossed_out: bool = False

    @property
    def blank(self) -> bool:
        return not self.text.strip()


class StructuredAnswerSheet(BaseModel):
    """Output of the answer-sheet parser (plan Phase 5 output record)."""

    model_config = ConfigDict(extra="forbid")

    student: StudentIdentity
    answers: List[AnswerSheetEntry] = Field(default_factory=list)
    issues: List[MappingIssue] = Field(default_factory=list)
    review_reasons: List[EvalReviewReason] = Field(default_factory=list)
    status: SheetStatus = SheetStatus.OK
    source_pages: int = 0

    def to_plan_json(self) -> dict:
        """Exactly the plan's phase-5 JSON surface (plus region labels)."""
        return {
            "student": {
                "name": self.student.name,
                "roll_no": self.student.roll_no,
            },
            "answers": [
                {
                    "question_id": entry.question_id,
                    "pages": list(entry.pages),
                    "text": entry.text,
                    "regions": [
                        {
                            "region_type": region.region_type.value,
                            "bbox": list(region.bbox),
                            "label": region.label,
                        }
                        for region in entry.regions
                    ],
                }
                for entry in self.answers
            ],
        }

    @property
    def summary(self) -> dict:
        return {
            "student": {
                "name": self.student.name,
                "roll_no": self.student.roll_no,
                "matched": self.student.matched,
                "confidence": self.student.confidence,
            },
            "answer_count": len(self.answers),
            "blank_answers": [a.question_id for a in self.answers if a.blank],
            "continuations": [
                a.question_id for a in self.answers if len(a.pages) > 1
            ],
            "issues": [i.value for i in self.issues],
            "review_reasons": [r.value for r in self.review_reasons],
            "status": self.status.value,
        }


class StructuringSettings(BaseModel):
    """Toggles for the answer-sheet parser's edge-case handling."""

    strip_crossed_out: bool = True
    merge_continuations: bool = True
    flag_out_of_order: bool = True
    flag_multiple_attempts: bool = True
    required_identity_fields: List[str] = Field(
        default_factory=lambda: ["name", "roll_no"]
    )