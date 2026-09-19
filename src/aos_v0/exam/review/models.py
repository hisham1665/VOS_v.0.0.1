"""Phase 11 review-domain models: the per-question review item and audit log.

A ``ReviewItem`` is the counterpart of the plan's "REVIEW REQUIRED" card. It
carries everything a human reviewer needs to make a decision -- the proposed
marks (kept as *proposed*, never silently turned into a final grade), the
evidence (answer text, OCR confidence), both agents' verdicts, and the
reconciliation summary. Rendering and persistence live in ``dashboard`` /
``store``; this module defines the shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# The plan's canonical review reasons (Phase 11). They mirror
# ``EvalReviewReason`` in ``exam.models``; humans can also tag new reasons on
# their own items.
REVIEW_REASONS = (
    "LOW_OCR_CONFIDENCE",
    "AGENT_DISAGREEMENT",
    "AMBIGUOUS_ANSWER",
    "IDENTITY_UNCERTAIN",
    "DIAGRAM_UNCERTAIN",
    "MATHEMATICAL_UNCERTAINTY",
    "MISSING_PAGE",
    "MULTIPLE_ANSWERS",
    "LOW_EVALUATION_CONFIDENCE",
    "RECOVERY_FAILED",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ReviewStatus(StrEnum):
    PENDING = "pending"
    REVIEWED = "reviewed"


class ReviewDecision(StrEnum):
    ACCEPT = "accept"
    MODIFY = "modify"
    ESCALATE = "escalate"


class ReviewItem(BaseModel):
    """One question's full audit record and its review state.

    ``proposed_marks`` is the evaluator's honest guess; ``final_marks`` is the
    human's verdict (None until the item is resolved). The item never converts
    a proposal into a grade by itself -- that is the reviewer's explicit step.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="'<paper>:<question>' or '<paper>:*' for paper-level")
    paper_id: str
    question_id: str = Field(description="'*' marks a paper-level review item")
    question_text: str = ""
    student: dict = Field(default_factory=dict)
    source: Optional[str] = Field(default=None, description="original image reference")
    pages: List[int] = Field(default_factory=list)
    answer_text: str = ""
    ocr_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    answer_key_version: str = "1"
    rubric_version: str = "1"

    # -- evaluation evidence (from the audit trail) -------------------------
    agent1: Optional[dict] = Field(default=None, description="primary agent verdict")
    agent2: Optional[dict] = Field(default=None, description="verifier agent verdict")
    disagreement: bool = False
    disputed_concepts: List[str] = Field(default_factory=list)
    reconciliation: dict = Field(default_factory=dict)
    proposed_marks: Optional[float] = Field(default=None)
    max_marks: float = Field(default=0.0, ge=0.0)
    proposed_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    review_reasons: List[str] = Field(default_factory=list)
    model_info: dict = Field(default_factory=dict)

    # -- review state (mutated by the dashboard actions) --------------------
    status: ReviewStatus = ReviewStatus.PENDING
    decision: Optional[ReviewDecision] = None
    final_marks: Optional[float] = None
    reviewer: str = ""
    note: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    reviewed_at: Optional[str] = None

    @property
    def paper_level(self) -> bool:
        return self.question_id == "*"

    def to_card(self) -> dict:
        """Card-shaped payload for the ASCII dashboard (plan Phase 11 UI)."""
        return {
            "id": self.id,
            "paper_id": self.paper_id,
            "question_id": self.question_id,
            "question_text": self.question_text,
            "student": self.student,
            "source": self.source,
            "pages": list(self.pages),
            "answer_text": self.answer_text,
            "ocr_confidence": self.ocr_confidence,
            "answer_key_version": self.answer_key_version,
            "rubric_version": self.rubric_version,
            "agent1": self.agent1,
            "agent2": self.agent2,
            "disagreement": self.disagreement,
            "disputed_concepts": list(self.disputed_concepts),
            "reconciliation": dict(self.reconciliation),
            "proposed_marks": self.proposed_marks,
            "max_marks": self.max_marks,
            "proposed_confidence": self.proposed_confidence,
            "review_reasons": list(self.review_reasons),
            "model_info": dict(self.model_info),
            "status": self.status.value,
            "decision": self.decision.value if self.decision else None,
            "final_marks": self.final_marks,
            "reviewer": self.reviewer,
            "note": self.note,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
        }

    def to_plan_json(self) -> dict:
        return self.to_card()


class AuditEvent(BaseModel):
    """One immutable line of the review audit log (append-only JSONL)."""

    model_config = ConfigDict(extra="forbid")

    flow_id: str = Field(description="batch or queue run this event belongs to")
    ts: str = Field(default_factory=utc_now_iso)
    actor: str = "system"
    action: str = Field(description="ENQUEUE | REENQUEUE | RESOLVE")
    item_id: str = ""
    paper_id: str = ""
    question_id: str = ""
    payload: dict = Field(default_factory=dict)

    def to_plan_json(self) -> dict:
        return self.model_dump()


class ReviewStats(BaseModel):
    """Snapshot of the review queue."""

    total: int = 0
    pending: int = 0
    reviewed: int = 0
    by_status: dict = Field(default_factory=dict)

    def to_plan_json(self) -> dict:
        return self.model_dump()