"""Phase 12 report/analytics data contract.

One evaluated paper becomes a ``PaperRecord`` (the CSV-1 row plus per-question
``DetailRow`` rows for CSV-2 / the individual report). ``ClassAnalytics`` is
the class-level metric snapshot. All of it is derived from the evidence the
orchestrator already recorded -- reporting never re-runs an evaluation and
never invents a mark.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DetailRow(BaseModel):
    """One question's row in CSV-2 and the individual report."""

    model_config = ConfigDict(extra="forbid")

    roll_no: str
    name: str
    paper_id: str
    question_id: str
    question_text: str = ""
    max_marks: float = 0.0
    agent1_marks: Optional[float] = None
    agent2_marks: Optional[float] = None
    final_marks: Optional[float] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    review_required: bool = False
    review_reasons: List[str] = Field(default_factory=list)
    review_disposition: str = Field(
        default="proposed",
        description="proposed | accepted | modified | escalated | pending",
    )
    concepts_satisfied: List[str] = Field(default_factory=list)
    missing_concepts: List[str] = Field(default_factory=list)

    @property
    def agent1(self) -> str:
        return "" if self.agent1_marks is None else f"{self.agent1_marks:g}"

    @property
    def agent2(self) -> str:
        return "" if self.agent2_marks is None else f"{self.agent2_marks:g}"

    @property
    def final(self) -> str:
        return "" if self.final_marks is None else f"{self.final_marks:g}"


class PaperRecord(BaseModel):
    """One paper as reports / analytics see it."""

    model_config = ConfigDict(extra="forbid")

    paper_id: str
    roll_no: str
    name: str
    status: str  # Evaluated | Review
    total_marks: Optional[float] = None
    max_marks: float = 0.0
    percentage: Optional[float] = None
    needs_review: bool = False
    rows: List[DetailRow] = Field(default_factory=list)
    paper_level_reasons: List[str] = Field(default_factory=list)

    @property
    def question_ids(self) -> List[str]:
        return [row.question_id for row in self.rows]

    @property
    def has_ocr_failure(self) -> bool:
        return "low_ocr_confidence" in self.paper_level_reasons or any(
            "low_ocr_confidence" in row.review_reasons for row in self.rows
        )

    @property
    def has_agent_disagreement(self) -> bool:
        return any(
            "agent_disagreement" in row.review_reasons for row in self.rows
        )

    def marks_for(self, question_id: str) -> Optional[float]:
        for row in self.rows:
            if row.question_id == question_id:
                return row.final_marks
        return None


class QuestionStat(BaseModel):
    question_id: str
    question_text: str = ""
    max_marks: float = 0.0
    mean: Optional[float] = None
    difficulty: Optional[float] = Field(
        default=None, description="1 - mean/max; higher means harder"
    )


class ClassAnalytics(BaseModel):
    """Class-level analytics (the plan's metrics list)."""

    model_config = ConfigDict(extra="forbid")

    total_students: int = 0
    average: Optional[float] = None      # mean percentage
    median: Optional[float] = None
    highest: Optional[float] = None
    lowest: Optional[float] = None
    question_stats: List[QuestionStat] = Field(default_factory=list)
    review_rate: float = 0.0
    ocr_failure_rate: float = 0.0
    agent_disagreement_rate: float = 0.0

    def to_plan_json(self) -> dict:
        return self.model_dump(mode="json")