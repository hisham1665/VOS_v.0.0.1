"""Phase 7 -- two-agent evaluation with disagree-ment analysis (plan Phase 7).

Two independent agents evaluate the same evidence and a comparison engine
reconciles them -- the runnable counterpart of the S4 DAG spine that already
fans one question out to two parallel nodes and merges them.

  * Agent 1 (primary evaluator, capability `semantic_answer_evaluation`) and
    Agent 2 (independent verifier, capability `answer_verification`) are
    structurally independent: the verifier never receives Agent 1's output.
    In production each agent is a distinct model (SEMANTIC_EVALUATION vs
    ANSWER_VERIFICATION, docs/MODEL_SELECTION.md) with diversified prompts;
    locally, the two runnable engines use provably different acceptance
    policies (Agent 1: semantic + spelling + alternatives; Agent 2: literal
    surface concepts only, zero spelling tolerance) so real disagreements
    surface instead of being hidden by one shared matcher.
  * Both agents produce the plan's structured surface -- marks, max_marks,
    concepts_satisfied, missing_concepts, reasoning, confidence.
  * The comparison engine (``reconcile_agents``) turns those surfaces into a
    final verdict while the plan's golden rules hold: an honest disagreement
    routes to ``AGENT_DISAGREEMENT`` review with the mark withheld or
    confidence-gated -- never silently averaged over, and never an automatic
    zero.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from aos_v0.exam.models import (
    EVALUATION_CAPABILITY,
    EvaluationSettings,
    EvalReviewReason,
    Question,
    VERIFICATION_CAPABILITY,
)
from aos_v0.exam.structure.models import AnswerSheetEntry

from aos_v0.exam.evaluate.engine import evaluate_question, round_half_up
from aos_v0.exam.evaluate.matcher import ConceptMatcher
from aos_v0.exam.evaluate.models import (
    Disposition,
    EvaluatorSettings,
    QuestionEvaluation,
)


def agent_evaluation_from(
    agent: str,
    capability: str,
    evaluation: QuestionEvaluation,
) -> "AgentEvaluation":
    """Fold one engine verdict into the plan's structured agent surface."""
    concepts_satisfied = sorted({
        m.concept for m in evaluation.concepts
        if m.disposition == Disposition.SATISFIED
    })
    partial_concepts = sorted({
        m.concept for m in evaluation.concepts
        if m.disposition == Disposition.PARTIAL
    })
    missing_concepts = sorted({
        m.concept for m in evaluation.concepts
        if m.disposition == Disposition.MISSING
    })
    return AgentEvaluation(
        agent=agent,
        capability=capability,
        marks=evaluation.marks,
        max_marks=evaluation.max_marks,
        concepts_satisfied=concepts_satisfied,
        partial_concepts=partial_concepts,
        missing_concepts=missing_concepts,
        reasoning="\n".join(evaluation.reasoning),
        confidence=evaluation.confidence,
        flags=[f.value for f in evaluation.flags],
    )


class PrimaryEvaluator:
    """Agent 1 -- the full semantic evaluator (semantic + spelling + aliases)."""

    agent: str = "primary"
    capability: str = EVALUATION_CAPABILITY

    def __init__(self, settings: Optional[EvaluatorSettings] = None):
        self._settings = settings or EvaluatorSettings()

    def evaluate(
        self,
        question: Question,
        entry: AnswerSheetEntry,
    ) -> "AgentEvaluation":
        matcher = ConceptMatcher(self._settings)
        evaluation = evaluate_question(question, entry, self._settings, matcher)
        converted = agent_evaluation_from(
            self.agent, self.capability, evaluation
        )
        return converted


class IndependentVerifier:
    """Agent 2 -- conservative re-checker of the same evidence.

    Independence, by construction: the verifier receives only the question,
    the answer entry and the key/rubric -- never Agent 1's marks. It applies a
    deliberately stricter acceptance policy (literal surface concepts, no
    semantic paraphrase, no spelling variants, no aliases), so an answer that
    only "feels right" for Agent 1 is caught as a genuine disagreement instead
    of rubber-stamped.
    """

    agent: str = "verifier"
    capability: str = VERIFICATION_CAPABILITY

    def __init__(self, settings: Optional[EvaluatorSettings] = None):
        self._settings = settings or EvaluatorSettings(
            use_semantic_analyzer=False,
            accept_alternatives=False,
            spelling_tolerance=0,
        )

    def evaluate(
        self,
        question: Question,
        entry: AnswerSheetEntry,
    ) -> "AgentEvaluation":
        matcher = ConceptMatcher(self._settings)
        evaluation = evaluate_question(question, entry, self._settings, matcher)
        return agent_evaluation_from(self.agent, self.capability, evaluation)


# ---------------------------------------------------------------------------
# Comparison / disagreement-analysis engine (the reconciliation node)
# ---------------------------------------------------------------------------


class AgentEvaluation(BaseModel):
    """One agent's structured verdict (the plan's Phase 7 output surface)."""

    model_config = ConfigDict(extra="forbid")

    agent: str                      # "primary" | "verifier"
    capability: str                 # semantic_answer_evaluation / answer_verification
    marks: Optional[float]          # None = the agent withheld a mark
    max_marks: float = Field(gt=0.0)
    concepts_satisfied: List[str] = Field(default_factory=list)
    partial_concepts: List[str] = Field(default_factory=list)
    missing_concepts: List[str] = Field(default_factory=list)
    reasoning: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    flags: List[str] = Field(default_factory=list)

    @property
    def scored(self) -> bool:
        return self.marks is not None

    def to_plan_json(self) -> dict:
        return {
            "agent": self.agent,
            "capability": self.capability,
            "marks": self.marks,
            "max_marks": self.max_marks,
            "concepts_satisfied": list(self.concepts_satisfied),
            "missing_concepts": list(self.missing_concepts),
            "confidence": self.confidence,
        }


class ReconciliationResult(BaseModel):
    """The reconciled verdict for one question from the two agents.

    ``final_marks`` is None exactly when the answer must be reviewed first --
    the honest route; nothing here invents a zero.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str
    final_marks: Optional[float]
    final_confidence: float = Field(ge=0.0, le=1.0)
    agreed: bool = False
    adopted_from: str = "none"      # primary | verifier | weighted | both | none
    mark_difference: float = 0.0
    disputed_concepts: List[str] = Field(default_factory=list)
    review_reasons: List[EvalReviewReason] = Field(default_factory=list)
    reasoning: List[str] = Field(default_factory=list)
    primary: AgentEvaluation
    verifier: Optional[AgentEvaluation] = None

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)

    def to_plan_json(self) -> dict:
        return {
            "question_id": self.question_id,
            "final_marks": self.final_marks,
            "max_marks": self.primary.max_marks,
            "final_confidence": self.final_confidence,
            "agreed": self.agreed,
            "adopted_from": self.adopted_from,
            "mark_difference": self.mark_difference,
            "disputed_concepts": list(self.disputed_concepts),
            "review_reasons": [r.value for r in self.review_reasons],
            "reasoning": list(self.reasoning),
            "agents": [self.primary.to_plan_json()]
            + ([self.verifier.to_plan_json()] if self.verifier else []),
        }


def _disputed_concepts(a: "AgentEvaluation", b: "AgentEvaluation") -> List[str]:
    claimed_a = set(a.concepts_satisfied) | set(a.partial_concepts)
    claimed_b = set(b.concepts_satisfied) | set(b.partial_concepts)
    all_concepts = set(a.missing_concepts) | set(b.missing_concepts)
    return sorted(
        (claimed_a ^ claimed_b) | (all_concepts & (claimed_a ^ claimed_b))
    )


def reconcile_agents(
    question_id: str,
    primary: AgentEvaluation,
    verifier: Optional[AgentEvaluation],
    settings: Optional[EvaluationSettings] = None,
) -> ReconciliationResult:
    """Compare the two agents' verdicts and produce the final reconciled one.

    Policy (mirrors the plan's "Difference -> Disagreement Analysis"):

      * both agree on the same mark  -> adopt it (no review unless confidence
        is below the gate);
      * small divergence (fraction <= ``agent_disagreement_fraction``) ->
        adopt the confidence-weighted mark, ``agreed=True``;
      * large divergence -> ``AGENT_DISAGREEMENT`` review; the final mark is
        the *honest* most-confident side's mark (never a blend the model did
        not independently produce), confidence-gated, and the mark stays
        visible for the human to check -- never silently forced to zero.
      * either agent could not score (unscored) -> review, mark withheld.
    """
    settings = settings or EvaluationSettings()

    def add_reason(reason: EvalReviewReason) -> None:
        if reason not in review_reasons:
            review_reasons.append(reason)

    if settings.two_agent_evaluation is False or verifier is None:
        review: List[EvalReviewReason] = []
        if not primary.scored:
            review = [EvalReviewReason.LOW_EVALUATION_CONFIDENCE]
            note = "primary agent could not score; mark withheld for review"
        elif primary.confidence < settings.confidence_low:
            review = [EvalReviewReason.LOW_EVALUATION_CONFIDENCE]
            note = "primary confidence below the gate; routed to review"
        else:
            note = "primary agent verdict used"
        return ReconciliationResult(
            question_id=question_id,
            final_marks=primary.marks,
            final_confidence=round(primary.confidence, 4),
            agreed=True,
            adopted_from="primary",
            mark_difference=0.0,
            review_reasons=review,
            reasoning=[
                f"two-agent evaluation disabled; {note}",
            ],
            primary=primary,
            verifier=None,
        )

    if not primary.scored and not verifier.scored:
        review_reasons: List[EvalReviewReason] = []
        add_reason(EvalReviewReason.LOW_EVALUATION_CONFIDENCE)
        return ReconciliationResult(
            question_id=question_id,
            final_marks=None,
            final_confidence=round(min(primary.confidence, verifier.confidence), 4),
            agreed=False,
            adopted_from="none",
            mark_difference=0.0,
            review_reasons=review_reasons,
            reasoning=[
                "both agents withheld a mark; no mark can be awarded "
                "without review",
            ],
            primary=primary,
            verifier=verifier,
        )

    if not primary.scored or not verifier.scored:
        scorer = primary if primary.scored else verifier
        side = "primary" if primary is scorer else "verifier"
        return ReconciliationResult(
            question_id=question_id,
            final_marks=scorer.marks,
            final_confidence=round(scorer.confidence, 4),
            agreed=False,
            adopted_from=side,
            mark_difference=float(
                scorer.marks if scorer.marks is not None else 0.0
            ),
            disputed_concepts=_disputed_concepts(primary, verifier),
            review_reasons=[EvalReviewReason.LOW_EVALUATION_CONFIDENCE],
            reasoning=[
                f"only the {side} agent could score this answer; the other "
                "withheld, so the verdict routes to review",
            ],
            primary=primary,
            verifier=verifier,
        )

    diff = abs(primary.marks - verifier.marks)
    fraction = diff / primary.max_marks
    tolerance = settings.agent_disagreement_fraction

    claimed_a = set(primary.concepts_satisfied) | set(primary.partial_concepts)
    claimed_b = set(verifier.concepts_satisfied) | set(verifier.partial_concepts)
    disputed = sorted(claimed_a ^ claimed_b)

    if primary.marks == verifier.marks:
        mark = primary.marks
        confidence = min(primary.confidence, verifier.confidence)
        review: List[EvalReviewReason] = []
        if confidence < settings.confidence_low:
            review = [EvalReviewReason.LOW_EVALUATION_CONFIDENCE]
        return ReconciliationResult(
            question_id=question_id,
            final_marks=mark,
            final_confidence=round(confidence, 4),
            agreed=True,
            adopted_from="both",
            mark_difference=0.0,
            disputed_concepts=disputed,
            review_reasons=review,
            reasoning=[
                f"both agents scored {mark:g}/{primary.max_marks:g} "
                f"independently",
            ],
            primary=primary,
            verifier=verifier,
        )

    if fraction <= tolerance:
        w_a = primary.confidence / (primary.confidence + verifier.confidence)
        mark = round_half_up(
            w_a * primary.marks + (1 - w_a) * verifier.marks
        )
        confidence = min(primary.confidence, verifier.confidence)
        review: List[EvalReviewReason] = []
        if confidence < settings.confidence_low:
            review = [EvalReviewReason.LOW_EVALUATION_CONFIDENCE]
        return ReconciliationResult(
            question_id=question_id,
            final_marks=mark,
            final_confidence=round(confidence, 4),
            agreed=True,
            adopted_from="weighted",
            mark_difference=round(diff, 4),
            disputed_concepts=disputed,
            review_reasons=review,
            reasoning=[
                f"marks differ by {diff:g} ({fraction:.2%} of max) within the "
                f"agreement threshold ({tolerance:.0%}); adopted the "
                "confidence-weighted mark",
            ],
            primary=primary,
            verifier=verifier,
        )

    leader = primary if primary.confidence >= verifier.confidence else verifier
    side = "primary" if leader is primary else "verifier"
    review = [EvalReviewReason.AGENT_DISAGREEMENT]
    if leader.confidence < settings.confidence_low:
        review.append(EvalReviewReason.LOW_EVALUATION_CONFIDENCE)
    return ReconciliationResult(
        question_id=question_id,
        final_marks=leader.marks,
        final_confidence=round(leader.confidence, 4),
        agreed=False,
        adopted_from=side,
        mark_difference=round(diff, 4),
        disputed_concepts=disputed,
        review_reasons=review,
        reasoning=[
            f"agents disagree beyond the threshold: {primary.marks:g} vs "
            f"{verifier.marks:g} (fraction {fraction:.2%} of max, tolerance "
            f"{tolerance:.0%}); adopted the more confident agent's mark and "
            "routed to human review",
        ],
        primary=primary,
        verifier=verifier,
    )


def evaluate_two_agent(
    question: Question,
    entry: AnswerSheetEntry,
    settings: Optional[EvaluationSettings] = None,
    primary: Optional[PrimaryEvaluator] = None,
    verifier: Optional[IndependentVerifier] = None,
) -> ReconciliationResult:
    """Run both agents independently on one question and reconcile them.

    Each agent sees only the question, the answer entry and the key/rubric --
    the verifier never receives the primary's verdict, so independence is
    structural, and the comparison engine does the rest.
    """
    settings = settings or EvaluationSettings()
    primary = primary or PrimaryEvaluator()
    verifier = verifier or IndependentVerifier()

    if not settings.two_agent_evaluation:
        verdict = primary.evaluate(question, entry)
        return reconcile_agents(question.question_id, verdict, None, settings)

    verdict_a = primary.evaluate(question, entry)
    verdict_b = verifier.evaluate(question, entry)
    return reconcile_agents(question.question_id, verdict_a, verdict_b, settings)