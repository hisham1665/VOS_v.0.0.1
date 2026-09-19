"""Phase 6 semantic-evaluation data contract (implementation plan Phase 6).

Each evaluated question produces a ``QuestionEvaluation`` whose mark is derived
from meaning, concepts, correctness, completeness and the configured rubric --
never from exact wording. The evaluation carries the evidence for *every* mark
decision: matched concepts (with the student's own supporting spans), per
rubric-criterion results, the optional mathematical step evaluation, and the
behavioural flags and review reasons that explain *why* a margin was taken.

Golden rules inherited from the plan and enforced by the engine:

  * Evidence is preserved, never fabricated. ``evidence``/``reasoning`` lists
    quote the student's own words that justify each mark.
  * OCR failure can never become an automatic zero. An answer whose OCR
    confidence is below the gate is ``UNSCORED`` (``marks=None``) and routed to
    ``EvalReviewReason.LOW_OCR_CONFIDENCE`` review -- never silently marked 0.
  * Ambiguity and contradiction route to review with the mark withheld rather
    than guessed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from aos_v0.exam.models import EvalReviewReason
from aos_v0.exam.structure.models import StudentIdentity


class MatchLevel(StrEnum):
    """The plan's three evaluation levels, attached to every concept match."""

    SURFACE = "surface"            # L1: keyword / terminology / equation evidence
    SEMANTIC = "semantic"          # L2: different wording expresses the same idea
    CONCEPTUAL = "conceptual"      # L3: the required concept is demonstrated


class Disposition(StrEnum):
    """What the student's answer demonstrates for one concept/criterion."""

    SATISFIED = "satisfied"
    PARTIAL = "partial"
    MISSING = "missing"
    CONTRADICTED = "contradicted"   # the answer states the opposite


class EvaluationStatus(StrEnum):
    """Verdict-level state of one evaluated question."""

    OK = "ok"                    # scored from accepted evidence
    REVIEW = "review"            # scored but routed to human review
    UNSCORED = "unscored"        # marks withheld (None) pending review


class EvaluationFlag(StrEnum):
    """Behavioural notes that preserve *why* a mark decision was made.

    Positive flags record accepted evidence (a paraphrase, a spelling variant,
    an accepted alternative, preserved intermediate work); problem flags record
    things the engine could not honestly decide. They are not scores -- they are
    the audit trail behind the marks.
    """

    PARAPHRASE_ACCEPTED = "paraphrase_accepted"
    SPELLING_ACCEPTED = "spelling_accepted"
    ALTERNATIVE_ACCEPTED = "alternative_accepted"
    MULTILINGUAL_ACCEPTED = "multilingual_accepted"
    STEP_MARKS_PRESERVED = "step_marks_preserved"
    PARTIAL_CREDIT_APPLIED = "partial_credit_applied"
    NEGATIVE_MARKING_APPLIED = "negative_marking_applied"
    BLANK_ANSWER = "blank_answer"
    CROSSED_OUT = "crossed_out"
    LOW_ANSWER_CONFIDENCE = "low_answer_confidence"
    LOW_EVALUATION_CONFIDENCE = "low_evaluation_confidence"
    AMBIGUOUS_ANSWER = "ambiguous_answer"
    CONCEPT_CONFLICT = "concept_conflict"
    UNVERIFIED_ALTERNATIVE = "unverified_alternative"


#: How the engine maps its own flags onto the Phase-11 review reasons. Only
#: honest uncertainty routes to review; accepted positive evidence never does.
FLAG_REVIEW_REASON = {
    EvaluationFlag.LOW_ANSWER_CONFIDENCE: EvalReviewReason.LOW_OCR_CONFIDENCE,
    EvaluationFlag.AMBIGUOUS_ANSWER: EvalReviewReason.AMBIGUOUS_ANSWER,
    EvaluationFlag.CONCEPT_CONFLICT: EvalReviewReason.AMBIGUOUS_ANSWER,
    EvaluationFlag.LOW_EVALUATION_CONFIDENCE: EvalReviewReason.LOW_EVALUATION_CONFIDENCE,
}


class EvaluationError(RuntimeError):
    """Base error for the semantic-evaluation engine."""


class ConceptMatch(BaseModel):
    """The verdict for one expected concept, with the student's evidence."""

    model_config = ConfigDict(extra="forbid")

    concept: str
    disposition: Disposition = Disposition.MISSING
    level: MatchLevel = MatchLevel.SURFACE
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: List[str] = Field(default_factory=list)   # the student's own spans
    reason: str = ""
    alternative_of: str = ""   # when satisfied via an accepted alternative

    @property
    def summary(self) -> dict:
        return {
            "concept": self.concept,
            "disposition": self.disposition.value,
            "level": self.level.value,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }


class CriterionResult(BaseModel):
    """Marks for one rubric criterion, carrying the supporting evidence."""

    model_config = ConfigDict(extra="forbid")

    criterion: str
    marks: float = Field(default=0.0, ge=0.0)
    max_marks: float = Field(default=0.0, ge=0.0)
    disposition: Disposition = Disposition.MISSING
    bound_concept: str = ""    # expected concept this criterion was bound to
    evidence: List[str] = Field(default_factory=list)
    reason: str = ""


class MathStepResult(BaseModel):
    """One evaluated step in a mathematical/numerical answer."""

    model_config = ConfigDict(extra="forbid")

    step: str
    present: bool = False
    satisfied: bool = False
    evidence: List[str] = Field(default_factory=list)
    marks: float = Field(default=0.0, ge=0.0)
    max_marks: float = Field(default=0.0, ge=0.0)
    reason: str = ""


class MathEvaluation(BaseModel):
    """Mathematical evaluation: formula, substitution, calculation, units,
    and the final answer, with step marks that survive a wrong final value.
    """

    model_config = ConfigDict(extra="forbid")

    expected_value: Optional[str] = None
    student_value: Optional[str] = None
    final_answer_correct: Optional[bool] = None   # None = indeterminate
    tolerance: float = Field(default=0.0, ge=0.0)
    steps: List[MathStepResult] = Field(default_factory=list)
    marks: float = Field(default=0.0, ge=0.0)
    max_marks: float = Field(default=0.0, ge=0.0)

    @property
    def summary(self) -> dict:
        return {
            "expected_value": self.expected_value,
            "student_value": self.student_value,
            "final_answer_correct": self.final_answer_correct,
            "marks": self.marks,
            "max_marks": self.max_marks,
            "steps": [
                {
                    "step": s.step,
                    "satisfied": s.satisfied,
                    "marks": s.marks,
                    "max_marks": s.max_marks,
                }
                for s in self.steps
            ],
        }


class QuestionEvaluation(BaseModel):
    """The full evaluation of one student answer against one question."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    max_marks: float = Field(gt=0.0)
    answered: bool = True
    status: EvaluationStatus = EvaluationStatus.OK
    # None means the mark was withheld (UNSCORED) -- never an invented zero.
    marks: Optional[float] = None
    concepts: List[ConceptMatch] = Field(default_factory=list)
    criteria: List[CriterionResult] = Field(default_factory=list)
    math: Optional[MathEvaluation] = None
    flags: List[EvaluationFlag] = Field(default_factory=list)
    review_reasons: List[EvalReviewReason] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: List[str] = Field(default_factory=list)   # decision log / audit

    @property
    def awarded_marks(self) -> Optional[float]:
        """The mark to count toward a total; None cannot be totalled."""

        return self.marks

    @property
    def summary(self) -> dict:
        return {
            "question_id": self.question_id,
            "status": self.status.value,
            "marks": self.marks,
            "max_marks": self.max_marks,
            "confidence": self.confidence,
            "review_reasons": [r.value for r in self.review_reasons],
            "flags": [f.value for f in self.flags],
        }


class ExamEvaluation(BaseModel):
    """End-to-end evaluation of one structured answer sheet.

    ``total_marks`` sums only *scored* questions; an ``UNSCORED`` question
    contributes nothing to the total and its review reason is surfaced so the
    partial result is never mistaken for a final grade.
    """

    model_config = ConfigDict(extra="forbid")

    paper_id: str = ""
    student: Optional[StudentIdentity] = None
    question_evaluations: List[QuestionEvaluation] = Field(default_factory=list)
    max_marks: float = 0.0
    total_marks: float = 0.0
    confidence: float = 0.0
    flags: List[EvaluationFlag] = Field(default_factory=list)
    review_reasons: List[EvalReviewReason] = Field(default_factory=list)

    @property
    def unscored_questions(self) -> List[str]:
        return [
            q.question_id
            for q in self.question_evaluations
            if q.status == EvaluationStatus.UNSCORED
        ]

    @property
    def scored_questions(self) -> List[str]:
        return [
            q.question_id
            for q in self.question_evaluations
            if q.status != EvaluationStatus.UNSCORED
        ]

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)

    def to_plan_json(self) -> dict:
        """A plan-shaped JSON surface with marks, evidence and review flags."""
        student = self.student
        return {
            "student": {
                "name": student.name if student else "",
                "roll_no": student.roll_no if student else "",
            },
            "total_marks": self.total_marks,
            "max_marks": self.max_marks,
            "confidence": self.confidence,
            "review_reasons": [r.value for r in self.review_reasons],
            "questions": [
                {
                    "question_id": q.question_id,
                    "marks": q.marks,
                    "max_marks": q.max_marks,
                    "status": q.status.value,
                    "concepts": [c.summary for c in q.concepts],
                    "math": q.math.summary if q.math else None,
                    "review_reasons": [r.value for r in q.review_reasons],
                    "reasoning": list(q.reasoning),
                }
                for q in self.question_evaluations
            ],
        }

    @property
    def summary(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "scored": self.total_marks,
            "max_marks": self.max_marks,
            "confidence": self.confidence,
            "unscored_questions": self.unscored_questions,
            "review_reasons": [r.value for r in self.review_reasons],
            "flag_count": len(self.flags),
        }


class EvaluatorSettings(BaseModel):
    """Toggles and thresholds for the Phase-6 semantic evaluator.

    Mirrors the OCR/evaluation philosophy: thresholds decide whether extra
    effort or human review is needed -- they never override an evaluation
    silently, and an unreadable answer is never an automatic zero.
    """

    spelling_tolerance: int = Field(default=1, ge=0, le=2)
    semantic_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    partial_credit_default_fraction: float = Field(default=0.5, ge=0.0, le=1.0)
    accept_alternatives: bool = True
    multilingual: bool = False
    languages: List[str] = Field(default_factory=lambda: ["en"])
    min_answer_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    review_on_low_confidence: bool = True
    use_semantic_analyzer: bool = True
    negative_marking_floor: float = Field(default=0.0, ge=0.0)
    min_explanation_words: int = Field(default=6, ge=0)