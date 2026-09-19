"""Phase 9 -- fault recovery (plan Phase 9, "Fault Recovery and Confidence").

The exam orchestrator runs every DAG node under a recovery loop that speaks the
kernel's failure taxonomy (the failure classes imported from
`aos_v0.core.failure_manager`) and turns each class into a policy ladder with a
honest terminal: escalation to review.

    detect -> classify -> ladder (retry_same / retry_with_feedback /
                             resource_substitution) -> escalate to review

The golden rule from the earlier phases is the recovery policy's last resort:
a node that exhausts its ladder is **degraded**, never silently replaced with
guessed output, and never awarded an automatic zero. Downstream consumers see a
short evidence record that routes the affected question (or the whole paper,
for structural stages) to human review.

This is the exam-side loop; the kernel's own `FailureManager` keeps its
closed loop for kernel-driven execution. The same taxonomy means the two can
share vocabulary (detection reports, recovery attempts, degraded status).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, Dict, List, Optional, Tuple

from aos_v0.core.failure_manager import (
    CLASS_REASONING_REFUSAL,
    CLASS_RESOURCE_DEGRADED,
    CLASS_RESOURCE_OUTAGE,
    CLASS_TOOL_EMPTY_RESULT,
    CLASS_TOOL_LOW_CONFIDENCE,
    CLASS_TOOL_OUTPUT_CORRUPT,
)

from aos_v0.exam.models import EvaluationSettings, EvalReviewReason


# ---------------------------------------------------------------------------
# Recovery strategies + failure classes (plan Phase 9 "Recovery policies")
# ---------------------------------------------------------------------------


class RecoveryStrategy(StrEnum):
    """One rung of a recovery ladder (plan Phase 9 ladder)."""

    RETRY_SAME = "retry_same"
    RETRY_WITH_FEEDBACK = "retry_with_feedback"
    RESOURCE_SUBSTITUTION = "resource_substitution"
    ESCALATE_TO_REVIEW = "escalate_to_review"


# The disagreement outcome of the recon node is itself a recovery-class signal:
# the plan's "Agent Disagreement" ladder says a diff above threshold escalates.
CLASS_EVALUATION_DISAGREEMENT = "evaluation.disagreement"

# Failure classes reused verbatim from the kernel taxonomy, re-exported here so
# orchestrators and tests speak one vocabulary.
RESOURCE_OUTAGE = CLASS_RESOURCE_OUTAGE
RESOURCE_DEGRADED = CLASS_RESOURCE_DEGRADED
TOOL_EMPTY_RESULT = CLASS_TOOL_EMPTY_RESULT
TOOL_OUTPUT_CORRUPT = CLASS_TOOL_OUTPUT_CORRUPT
TOOL_LOW_CONFIDENCE = CLASS_TOOL_LOW_CONFIDENCE
REASONING_REFUSAL = CLASS_REASONING_REFUSAL

# Policy table: failure class -> ladder. Every ladder terminates in review;
# nothing is ever "recovered" by guessing.
EXAM_RECOVERY_TABLE: Dict[str, List[RecoveryStrategy]] = {
    RESOURCE_OUTAGE: [
        RecoveryStrategy.RETRY_SAME,
        RecoveryStrategy.RESOURCE_SUBSTITUTION,
        RecoveryStrategy.ESCALATE_TO_REVIEW,
    ],
    RESOURCE_DEGRADED: [
        RecoveryStrategy.RETRY_WITH_FEEDBACK,
        RecoveryStrategy.RESOURCE_SUBSTITUTION,
        RecoveryStrategy.ESCALATE_TO_REVIEW,
    ],
    TOOL_EMPTY_RESULT: [
        RecoveryStrategy.RETRY_WITH_FEEDBACK,
        RecoveryStrategy.RESOURCE_SUBSTITUTION,
        RecoveryStrategy.ESCALATE_TO_REVIEW,
    ],
    TOOL_OUTPUT_CORRUPT: [
        RecoveryStrategy.RESOURCE_SUBSTITUTION,
        RecoveryStrategy.RETRY_WITH_FEEDBACK,
        RecoveryStrategy.ESCALATE_TO_REVIEW,
    ],
    TOOL_LOW_CONFIDENCE: [
        RecoveryStrategy.RETRY_WITH_FEEDBACK,
        RecoveryStrategy.RESOURCE_SUBSTITUTION,
        RecoveryStrategy.ESCALATE_TO_REVIEW,
    ],
    REASONING_REFUSAL: [
        RecoveryStrategy.RETRY_WITH_FEEDBACK,
        RecoveryStrategy.ESCALATE_TO_REVIEW,
    ],
    CLASS_EVALUATION_DISAGREEMENT: [RecoveryStrategy.ESCALATE_TO_REVIEW],
}


# ---------------------------------------------------------------------------
# Structured failure detection (plan Phase 9 "Fallback routing / retry")
# ---------------------------------------------------------------------------

_CORRUPT_STATUS_KEYS = ("error", "status")


def _summary_confidence(payload: dict) -> Optional[float]:
    summary = payload.get("summary")
    if isinstance(summary, dict):
        value = summary.get("mean_confidence")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def detect_node_failure(
    payload: object,
    error: Optional[BaseException] = None,
    *,
    low_confidence_threshold: float = 0.6,
) -> Optional[Tuple[str, str]]:
    """Run the detection ensemble over one node call.

    Returns ``(failure_class, symptom)`` or None when the payload is healthy.
    The low-confidence rule is intentionally narrow: it fires only on
    structured OCR payloads carrying an explicit ``summary.mean_confidence``
    in [0, 1] below the threshold, so ordinary prose about confidence never
    trips it (mirrors the kernel's conservative detector posture).
    """
    if error is not None:
        return (
            RESOURCE_OUTAGE,
            f"{type(error).__name__}: {error}",
        )
    if payload is None:
        return (TOOL_EMPTY_RESULT, "empty output")
    if not isinstance(payload, dict) or not payload:
        return (TOOL_EMPTY_RESULT, "empty or non-structured output")

    corrupt_status = payload.get(_CORRUPT_STATUS_KEYS[1])
    if isinstance(corrupt_status, str) and corrupt_status in ("error", "degraded"):
        detail = payload.get("error") or payload.get("detail")
        return (
            TOOL_OUTPUT_CORRUPT,
            f"structured output flagged {corrupt_status!r}"
            + (f": {detail}" if detail else ""),
        )

    confidence = _summary_confidence(payload)
    if confidence is not None:
        if confidence < low_confidence_threshold:
            return (
                TOOL_LOW_CONFIDENCE,
                f"structured OCR payload claims mean confidence "
                f"{confidence:.3f} < {low_confidence_threshold}",
            )
        if confidence > 1.0:
            return (TOOL_OUTPUT_CORRUPT, "mean confidence outside [0, 1]")

    return None


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class RecoveryAttempt:
    strategy: RecoveryStrategy
    resource_id: str
    succeeded: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy.value,
            "resource_id": self.resource_id,
            "succeeded": self.succeeded,
            "detail": self.detail,
        }


@dataclass
class RecoveryOutcome:
    """Audit record of one node's recovery loop (phase-9 experience record)."""

    node_id: str
    failure_class: str = ""
    symptom: str = ""
    attempts: List[RecoveryAttempt] = field(default_factory=list)
    recovered: bool = False
    degraded: bool = False
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "failure_class": self.failure_class,
            "symptom": self.symptom,
            "attempts": [a.to_dict() for a in self.attempts],
            "recovered": self.recovered,
            "degraded": self.degraded,
            "elapsed_ms": self.elapsed_ms,
        }


# ---------------------------------------------------------------------------
# The recovery loop (plan Phase 9 "Retry handling / Fallback routing")
# ---------------------------------------------------------------------------


def _reformulate(instruction: Optional[str], failure_class: str) -> Optional[str]:
    """RETRY_WITH_FEEDBACK: rephrase the instruction with why it failed."""
    hint = {
        RESOURCE_DEGRADED: "the previous attempt returned degraded output",
        TOOL_EMPTY_RESULT: "the previous attempt returned an empty result",
        TOOL_OUTPUT_CORRUPT: "the previous attempt returned structurally "
                             "broken output",
        TOOL_LOW_CONFIDENCE: "the previous attempt was low-confidence",
        REASONING_REFUSAL: "the previous attempt declined to answer",
    }.get(failure_class, "the previous attempt failed")
    base = instruction or ""
    return f"{base} -- recovery: {hint}; retry with maximum completeness."


_GAP_FORMAT = {
    "status": "degraded",
}


def gap_marker(node_id: str, failure_class: str, symptom: str) -> dict:
    """Short, honest evidence record for a node that exhausted its ladder.

    Deliberately carries **no** answer/verdict keys: downstream consumers see
    a degraded stage and route to review instead of fabricating output.
    """
    return {
        "status": "degraded",
        "node_id": node_id,
        "failure_class": failure_class,
        "error": f"{symptom} (recovered: no; escalated: review)",
    }


class ExamRecoveryManager:
    """Closed detection->ladder loop for exam DAG node calls.

    ``primary_call`` runs the registry-selected winner; ``substitute_call``
    runs any runner-up resource as ranked by the registry scorer. When the
    ladder exhausts, the node is degraded (gap marker) -- never a guess, never
    an automatic zero.
    """

    def __init__(
        self,
        settings: Optional[EvaluationSettings] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.settings = settings or EvaluationSettings()
        self.max_attempts = self.settings.recovery_max_attempts
        self.log = log or (lambda _message: None)
        self.outcomes: List[RecoveryOutcome] = []

    # -- exposed -------------------------------------------

    def run_node(
        self,
        node_id: str,
        primary_call: Callable[..., dict],
        *,
        candidates: Optional[List[str]] = None,
        instruction: Optional[str] = None,
        substitute_call: Optional[Callable[[str], dict]] = None,
        low_confidence_threshold: float = 0.6,
    ) -> Tuple[dict, RecoveryOutcome]:
        """Execute one node under the recovery loop; returns payload + record."""
        started = time.monotonic()
        outcome = RecoveryOutcome(node_id=node_id)
        payload, error = _guard(primary_call, instruction)
        detection = self.detect_for(payload, error, low_confidence_threshold)

        if detection is None:
            outcome.elapsed_ms = int((time.monotonic() - started) * 1000)
            self.outcomes.append(outcome)
            return payload, outcome

        outcome.failure_class, outcome.symptom = detection
        self.log(
            f"[recovery] node '{node_id}': detected {outcome.failure_class} "
            f"({outcome.symptom})"
        )

        ladder = EXAM_RECOVERY_TABLE.get(
            outcome.failure_class, [RecoveryStrategy.ESCALATE_TO_REVIEW]
        )
        remaining = list(candidates or [])
        plan = ladder[: self.max_attempts]

        for strategy in plan:
            if strategy is RecoveryStrategy.ESCALATE_TO_REVIEW:
                break
            if strategy is RecoveryStrategy.RESOURCE_SUBSTITUTION and not remaining:
                outcome.attempts.append(
                    RecoveryAttempt(strategy, "-", False, "no substitute candidates")
                )
                self.log(
                    f"[recovery] node '{node_id}': no substitute resource, skipping"
                )
                continue

            attempt_fn, attempt_resource, attempt_instruction = _attempt_builder(
                strategy, primary_call, remaining, substitute_call, instruction,
                outcome.failure_class,
            )
            next_payload, next_error = _guard(attempt_fn, attempt_instruction)
            next_detection = self.detect_for(
                next_payload, next_error, low_confidence_threshold
            )
            if next_detection is None:
                outcome.attempts.append(
                    RecoveryAttempt(
                        strategy, attempt_resource, True, "output healthy after recovery"
                    )
                )
                outcome.recovered = True
                outcome.elapsed_ms = int((time.monotonic() - started) * 1000)
                self.outcomes.append(outcome)
                self.log(
                    f"[recovery] node '{node_id}': recovered via {strategy.value}"
                )
                return next_payload, outcome

            outcome.attempts.append(
                RecoveryAttempt(
                    strategy, attempt_resource, False,
                    f"{next_detection[0]}: {next_detection[1]}",
                )
            )

        # Ladder exhausted: degrade, escalate to review, never fabricate.
        outcome.degraded = True
        outcome.elapsed_ms = int((time.monotonic() - started) * 1000)
        self.outcomes.append(outcome)
        self.log(
            f"[recovery] node '{node_id}': exhausted ladder -> review escalation"
        )
        return gap_marker(node_id, outcome.failure_class, outcome.symptom), outcome

    # -- detection -----------------------------------------

    def detect_for(
        self,
        payload: object,
        error: Optional[BaseException] = None,
        low_confidence_threshold: float = 0.6,
    ) -> Optional[Tuple[str, str]]:
        return detect_node_failure(
            payload, error, low_confidence_threshold=low_confidence_threshold
        )


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------


def _guard(
    fn: Callable[..., object], instruction: Optional[str]
) -> Tuple[Optional[dict], Optional[BaseException]]:
    """Call ``fn``, catching everything so the loop always gets a payload+error."""
    try:
        if instruction is None:
            result = fn()
        else:
            result = fn(instruction)
    except Exception as exc:  # noqa: BLE001 - the loop classifies every failure
        return None, exc
    if isinstance(result, dict):
        return result, None
    return result, None


def _attempt_builder(
    strategy: RecoveryStrategy,
    primary_call: Callable[..., dict],
    remaining: List[str],
    substitute_call: Optional[Callable[[str], dict]],
    instruction: Optional[str],
    failure_class: str,
) -> Tuple[Callable[[Optional[str]], dict], str, Optional[str]]:
    """Resolve one ladder rung into (callable, resource_id, instruction)."""
    if strategy is RecoveryStrategy.RETRY_SAME:
        return primary_call, "primary", instruction
    if strategy is RecoveryStrategy.RETRY_WITH_FEEDBACK:
        return primary_call, "primary", _reformulate(instruction, failure_class)
    # RESOURCE_SUBSTITUTION -- pop the registry-ranked runner-up in score order.
    resource_id = remaining.pop(0)
    if substitute_call is None:
        def _missing(instruction: Optional[str] = None) -> dict:
            raise RuntimeError(f"no substitute runner available for {resource_id}")

        return _missing, resource_id, instruction
    def _bound(instruction: Optional[str] = None) -> dict:
        return substitute_call(resource_id)

    return _bound, resource_id, instruction


# ---------------------------------------------------------------------------
# Escalation helpers (plan Phase 9 "Disagreement escalation")
# ---------------------------------------------------------------------------


def review_reason_for(failure_class: str) -> EvalReviewReason:
    """Map a failed recovery to the review reason the report should carry."""
    if failure_class in (TOOL_LOW_CONFIDENCE,):
        return EvalReviewReason.LOW_OCR_CONFIDENCE
    return EvalReviewReason.RECOVERY_FAILED


def disagreement_requires_review(result) -> bool:
    """Phase-9 disagreement ladder decision on a reconciliation result.

    Delegates to the authoritative `reconcile_agents` verdict: an adopted-from
    disagreement (AGENT_DISAGREEMENT review reason) escalates to review. This
    keeps the escalation policy in lock-step with the Phase-7 reconciler
    instead of duplicating its thresholds.
    """
    return result.needs_review and (
        EvalReviewReason.AGENT_DISAGREEMENT
        in result.review_reasons
    )


def disagreements_escalate_to_review(result) -> RecoveryStrategy:
    """Map the disagreement ladder to a recovery strategy for audit records."""
    if disagreement_requires_review(result):
        return RecoveryStrategy.ESCALATE_TO_REVIEW
    return RecoveryStrategy.RETRY_SAME