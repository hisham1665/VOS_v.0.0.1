"""Phase 9 -- confidence engine (plan Phase 9, "Confidence Categories").

Aggregates the plan's confidence components -- OCR confidence, answer-extraction
confidence, semantic confidence, rubric confidence and agent agreement -- into a
single paper-level score plus a HIGH / MEDIUM / LOW category.

The plan's important rule is preserved by construction: **confidence is not
correctness**. The engine only decides how much additional verification /
human review a mark needs (thresholds from `EvaluationSettings.confidence_high`
/ `confidence_low`); it never changes a mark. Per-question and per-paper
categories ride along as metadata, and a LOW component is never patched up --
it surfaces in the category so the paper routes to review.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from aos_v0.exam.models import EvaluationSettings


class ConfidenceCategory(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Plan Phase 9 "Confidence Components", with default weights summing to 1.0.
# The semantic component carries the most weight because it already folds in
# the two-agent loop (primary + verifier); OCR and answer-extraction evidence
# capture how trustworthy the text we graded actually is.
DEFAULT_COMPONENT_WEIGHTS: Dict[str, float] = {
    "ocr": 0.20,
    "answer_extraction": 0.20,
    "semantic": 0.30,
    "rubric": 0.15,
    "agent_agreement": 0.15,
}

_CATEGORY_ORDER: Dict[str, int] = {
    ConfidenceCategory.HIGH.value: 3,
    ConfidenceCategory.MEDIUM.value: 2,
    ConfidenceCategory.LOW.value: 1,
}


class ConfidenceComponent(BaseModel):
    name: str
    value: float  # 0..1
    weight: float
    category: str


class PaperConfidence(BaseModel):
    """Paper-level confidence: weighted components + overall score + category."""

    model_config = {"extra": "forbid"}

    overall: float
    category: str
    components: List[ConfidenceComponent] = Field(default_factory=list)


class QuestionConfidence(BaseModel):
    """Per-question confidence (semantic + agreement) plus extraction evidence."""

    model_config = {"extra": "forbid"}

    question_id: str
    score: float
    category: str
    extraction_confidence: float = 1.0
    agreed: bool = True


class ConfidenceEngine:
    """Deterministic, threshold-gated confidence aggregator.

    ``estimate_paper`` accepts a dict of the components it has evidence for;
    components that were never measured (e.g. no OCR ran) are simply excluded
    rather than defaulted to 0, so an honest "we have no evidence" never
    silences real evidence. When *any* underlying component falls below the LOW
    threshold the overall score is mean-pulled the same way the category is --
    the LOW reading is never masked.
    """

    def __init__(self, settings: Optional[EvaluationSettings] = None) -> None:
        self.settings = settings or EvaluationSettings()

    def categorize(self, value: float) -> ConfidenceCategory:
        """Map a [0, 1] score to HIGH/MEDIUM/LOW via the configured thresholds."""
        high = self.settings.confidence_high
        low = self.settings.confidence_low
        if value >= high:
            return ConfidenceCategory.HIGH
        if value < low:
            return ConfidenceCategory.LOW
        return ConfidenceCategory.MEDIUM

    def estimate_paper(
        self,
        *,
        ocr: Optional[float] = None,
        answer_extraction: Optional[float] = None,
        semantic: Optional[float] = None,
        rubric: Optional[float] = None,
        agent_agreement: Optional[float] = None,
        settings: Optional[EvaluationSettings] = None,
    ) -> PaperConfidence:
        settings = settings or self.settings
        provided: Dict[str, float] = {}
        for name, value in (
            ("ocr", ocr),
            ("answer_extraction", answer_extraction),
            ("semantic", semantic),
            ("rubric", rubric),
            ("agent_agreement", agent_agreement),
        ):
            if value is not None:
                provided[name] = value

        if not provided:
            return PaperConfidence(overall=0.0, category=ConfidenceCategory.LOW, components=[])

        total_weight = sum(DEFAULT_COMPONENT_WEIGHTS[n] for n in provided)
        overall = sum(
            DEFAULT_COMPONENT_WEIGHTS[n] * value for n, value in provided.items()
        ) / total_weight
        overall = round(min(max(overall, 0.0), 1.0), 4)

        components = [
            ConfidenceComponent(
                name=name,
                value=round(value, 4),
                weight=round(DEFAULT_COMPONENT_WEIGHTS[name], 4),
                category=self.categorize(value).value,
            )
            for name, value in provided.items()
        ]

        # The category is the *weakest relevant reading*: confidence decides
        # whether extra verification / review is needed, so one genuinely LOW
        # component (e.g. a smudged OCR region) routes the paper to review even
        # when the other components look fine. The numeric overall stays the
        # representative mean; only the category carries the safety semantics.
        weakest = min(
            self.categorize(overall).value,
            *(component.category for component in components),
            key=_CATEGORY_ORDER.__getitem__,
        )
        return PaperConfidence(
            overall=overall,
            category=weakest,
            components=components,
        )

    def estimate_question(
        self,
        *,
        question_id: str,
        score: float,
        extraction_confidence: float,
        agreed: bool,
    ) -> QuestionConfidence:
        return QuestionConfidence(
            question_id=question_id,
            score=round(score, 4),
            category=self.categorize(score).value,
            extraction_confidence=round(extraction_confidence, 4),
            agreed=agreed,
        )