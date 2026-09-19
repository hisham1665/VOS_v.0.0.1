"""Rubric evaluation -- distributing marks across criteria (plan Phase 6).

The rubric binds each criterion to *evidence*, never to exact wording:

  * a criterion whose id matches an expected concept by :func:`normalize_key`
    ("mutual_exclusion" <-> "mutual exclusion <-> "mutual exclusion") is awarded
    from that concept's disposition;
  * a criterion that names a mathematical step ("formula", "substitution",
    "final_value") is awarded from the matching step result;
  * an *expressive* criterion (e.g. Q4's "definition"/"comparison") that binds
    no concept or step is awarded from the overall substance of the answer: it
    earns full marks only when a majority of the expected concepts are
    demonstrated, partial when at least one is -- so a keyword or a stray
    sentence can never buy a quality criterion.
  * partial credit applies per configured ``PartialCreditRule`` (else the
    settings default fraction), and only when the question enables it.

Contradictions are never rewarded: a CONTRADICTED concept earns zero marks and
is surfaced (the engine attaches CONCEPT_CONFLICT + AMBIGUOUS_ANSWER) rather
than silently skipped.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from aos_v0.exam.models import Question

from aos_v0.exam.evaluate.math import MathEvaluation, MathStep
from aos_v0.exam.evaluate.models import (
    ConceptMatch,
    CriterionResult,
    Disposition,
    EvaluatorSettings,
)
from aos_v0.exam.evaluate.normalize import normalize_key

#: criterion id -> the mathematical step it awards.
_STEP_BY_KEY: Dict[str, MathStep] = {
    "formula": MathStep.FORMULA,
    "substitution": MathStep.SUBSTITUTION,
    "calculation": MathStep.CALCULATION,
    "units": MathStep.UNITS,
    "final_answer": MathStep.FINAL_ANSWER,
}

#: criterion ids that mean "the final answer" in the answer's own words.
_FINAL_KEYS: frozenset = frozenset({
    "final_value", "final", "answer", "result", "value",
    "final_expression", "conclusion",
})


def step_for_criterion_key(key: str) -> Optional[MathStep]:
    if key in _STEP_BY_KEY:
        return _STEP_BY_KEY[key]
    if key in _FINAL_KEYS:
        return MathStep.FINAL_ANSWER
    return None


def _expressive_rule(matches: List[ConceptMatch]) -> Disposition:
    """Full/partial credit for a criterion that binds no concept or step."""
    satisfied = sum(1 for m in matches if m.disposition == Disposition.SATISFIED)
    total = len(matches)
    if total == 0:
        return Disposition.MISSING
    if satisfied == 0:
        return Disposition.MISSING
    if satisfied >= math.ceil(total / 2):
        return Disposition.SATISFIED
    return Disposition.PARTIAL


def partial_fraction(
    question: Question,
    criterion: str,
    settings: EvaluatorSettings,
) -> float:
    """The partial-credit fraction for one criterion on one question."""
    if not question.partial_credit:
        return 0.0
    for rule in question.partial_credit_rules:
        if normalize_key(rule.criterion) == normalize_key(criterion):
            return rule.fraction
    return settings.partial_credit_default_fraction


def _award(disposition: Disposition, criterion: Question,
           fraction: float, marks: float, partial_toggle: bool) -> float:
    if disposition == Disposition.SATISFIED:
        return round(marks, 2)
    if disposition == Disposition.PARTIAL and partial_toggle:
        return round(marks * fraction, 2)
    return 0.0


def evaluate_rubric(
    question: Question,
    matches: List[ConceptMatch],
    math_eval: Optional[MathEvaluation],
    settings: EvaluatorSettings,
) -> List[CriterionResult]:
    """Award marks for every rubric criterion of ``question``."""
    rubric = question.rubric
    if rubric is None:
        return []

    by_concept: Dict[str, ConceptMatch] = {}
    for match in matches:
        by_concept.setdefault(normalize_key(match.concept), match)

    steps_by_name: Dict[str, object] = {}
    if math_eval is not None:
        steps_by_name = {step.step: step for step in math_eval.steps}

    results: List[CriterionResult] = []
    for criterion in rubric.criteria:
        key = normalize_key(criterion.criterion)
        step = step_for_criterion_key(key)
        bound = by_concept.get(key)
        partial_toggle = question.partial_credit
        fraction = partial_fraction(question, criterion.criterion, settings)

        evidence: List[str] = []
        disposition = Disposition.MISSING
        reason = ""

        if step is not None and step.value in steps_by_name:
            step_result = steps_by_name[step.value]
            disposition = (
                Disposition.SATISFIED if step_result.satisfied else
                Disposition.PARTIAL if step_result.present else
                Disposition.MISSING
            )
            evidence = list(getattr(step_result, "evidence", []))
            reason = f"math step '{step.value}': {step_result.reason}"
        elif bound is not None:
            disposition = bound.disposition
            evidence = list(bound.evidence)
            reason = f"bound to concept '{bound.concept}': {bound.reason}"
        else:
            disposition = _expressive_rule(matches)
            reason = (
                "expressive criterion: awarded from overall concept coverage "
                f"({disposition.value})"
            )

        marks = _award(disposition, criterion, fraction, criterion.marks,
                       partial_toggle)
        results.append(
            CriterionResult(
                criterion=criterion.criterion,
                marks=marks,
                max_marks=criterion.marks,
                disposition=disposition,
                bound_concept=bound.concept if bound is not None else (
                    f"math:{step.value}" if step is not None else ""
                ),
                evidence=evidence,
                reason=reason,
            )
        )

    _apply_special_rules(question, matches, by_concept, results)
    return results


def _apply_special_rules(
    question: Question,
    matches: List[ConceptMatch],
    by_concept: Dict[str, ConceptMatch],
    results: List[CriterionResult],
) -> None:
    """Downward-only corrections driven by ``special_rules`` text.

    Only *honest* adjustments are made here -- a partial set can at most keep a
    criterion at partial credit; nothing is ever upgraded from these rules.
    Implemented for the plan's flagship case: "Only a full set of all four
    conditions earns the circular_wait criterion" (Q5).
    """
    rule_text = (question.special_rules or "").lower()
    if "all four conditions" in rule_text or "full set of all" in rule_text:
        four_keys = [
            k for k in ("mutual_exclusion", "hold_and_wait",
                        "no_preemption", "circular_wait")
            if k in by_concept
        ]
        if len(four_keys) < 4:
            return
        missing = [
            k for k in four_keys
            if by_concept[k].disposition != Disposition.SATISFIED
        ]
        if not missing:
            return
        circular = by_concept.get("circular_wait")
        circular_satisfied = (
            circular is not None
            and circular.disposition == Disposition.SATISFIED
        )
        for result in results:
            if normalize_key(result.criterion) == "circular_wait" and circular_satisfied:
                result.marks = result.max_marks / 2
                result.disposition = Disposition.PARTIAL
                result.reason += (
                    "; full set of the four conditions not demonstrated, "
                    "circular_wait kept at partial"
                )


def math_marks_from_criteria(results: List[CriterionResult]) -> tuple:
    """(marks, max_marks) for criteria that bind a mathematical step."""
    marks = sum(r.marks for r in results if r.bound_concept.startswith("math:"))
    max_marks = sum(r.max_marks for r in results if r.bound_concept.startswith("math:"))
    return round(marks, 2), round(max_marks, 2)