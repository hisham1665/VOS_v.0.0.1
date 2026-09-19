"""Phase 9 tests -- fault recovery for the exam DAG (plan Phase 9).

Covers: the conservative detection ensemble (`detect_node_failure`) classifying
node outputs into kernel failure classes; the `EXAM_RECOVERY_TABLE` policy
ladders (every ladder terminates in `ESCALATE_TO_REVIEW`); the retry->
substitute->degrade loop of `ExamRecoveryManager` (registry-ranked runner-ups,
honest gap markers with no fabricated output keys); and the escalation mapping
that turns an exhausted ladder into an `EvalReviewReason` review routing.
"""

import unittest

from aos_v0.exam.models import EvalReviewReason, EvaluationSettings
from aos_v0.exam.recovery import (
    CLASS_EVALUATION_DISAGREEMENT,
    EXAM_RECOVERY_TABLE,
    RESOURCE_OUTAGE,
    TOOL_EMPTY_RESULT,
    TOOL_LOW_CONFIDENCE,
    TOOL_OUTPUT_CORRUPT,
    ExamRecoveryManager,
    RecoveryStrategy,
    detect_node_failure,
    disagreement_requires_review,
    disagreements_escalate_to_review,
    gap_marker,
    review_reason_for,
)
from aos_v0.exam.two_agent import AgentEvaluation, ReconciliationResult


def _agent(marks=None, confidence=0.0, max_marks=6.0) -> AgentEvaluation:
    return AgentEvaluation(
        agent="primary",
        capability="semantic_answer_evaluation",
        marks=marks,
        max_marks=max_marks,
        concepts_satisfied=[],
        missing_concepts=[],
        reasoning="test agent",
        confidence=confidence,
    )


def _manager(max_attempts: int = 2):
    return ExamRecoveryManager(
        EvaluationSettings(recovery_enabled=True, recovery_max_attempts=max_attempts)
    )


def _healthy_payload():
    return {"document": {"pages": []}, "summary": {"mean_confidence": 0.95}}


class DetectNodeFailureTests(unittest.TestCase):
    def test_exception_is_a_resource_outage(self):
        error = RuntimeError("backend down")
        klass, symptom = detect_node_failure(None, error)
        self.assertEqual(klass, RESOURCE_OUTAGE)
        self.assertIn("RuntimeError", symptom)

    def test_none_payload_is_empty_result(self):
        self.assertEqual(
            detect_node_failure(None)[0], TOOL_EMPTY_RESULT
        )

    def test_non_dict_or_empty_is_empty_result(self):
        for payload in ("just text", [], {}, object()):
            klass, _ = detect_node_failure(payload)
            self.assertEqual(klass, TOOL_EMPTY_RESULT)

    def test_corrupt_status_flags_are_detected(self):
        for status in ("error", "degraded"):
            klass, symptom = detect_node_failure({"status": status, "detail": "boom"})
            self.assertEqual(klass, TOOL_OUTPUT_CORRUPT)
            self.assertIn("boom", symptom)

    def test_low_confidence_only_on_structured_ocr_payload(self):
        low = detect_node_failure({
            "document": {"pages": []},
            "summary": {"mean_confidence": 0.5},
        })
        self.assertEqual(low[0], TOOL_LOW_CONFIDENCE)
        self.assertIsNone(detect_node_failure(_healthy_payload()))

    def test_confidence_outside_unit_range_is_corrupt(self):
        klass, _ = detect_node_failure(
            {"summary": {"mean_confidence": 1.05}}
        )
        self.assertEqual(klass, TOOL_OUTPUT_CORRUPT)

    def test_threshold_is_respected(self):
        payload = {"summary": {"mean_confidence": 0.5}}
        self.assertIsNone(detect_node_failure(payload, low_confidence_threshold=0.4))


class RecoveryTableTests(unittest.TestCase):
    def test_every_ladder_terminates_in_review(self):
        for failure_class, ladder in EXAM_RECOVERY_TABLE.items():
            self.assertEqual(
                ladder[-1], RecoveryStrategy.ESCALATE_TO_REVIEW,
                failure_class,
            )

    def test_disagreement_escalates_directly(self):
        self.assertEqual(
            EXAM_RECOVERY_TABLE[CLASS_EVALUATION_DISAGREEMENT],
            [RecoveryStrategy.ESCALATE_TO_REVIEW],
        )


class ManagerLoopTests(unittest.TestCase):
    def test_healthy_call_does_no_recovery(self):
        manager = _manager()
        payload, outcome = manager.run_node(
            "ocrp1", lambda *a: _healthy_payload(), candidates=["document_ocr_b"]
        )
        self.assertEqual(outcome.failure_class, "")
        self.assertFalse(outcome.recovered)
        self.assertFalse(outcome.degraded)
        self.assertEqual(outcome.attempts, [])
        self.assertEqual(payload, _healthy_payload())

    def test_transient_outage_recovers_via_retry(self):
        manager = _manager()
        calls = {"n": 0}

        def flaky(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("socket timeout")
            return _healthy_payload()

        payload, outcome = manager.run_node(
            "ocrp1", flaky, candidates=["document_ocr_b"]
        )
        self.assertTrue(outcome.recovered)
        self.assertFalse(outcome.degraded)
        self.assertEqual(outcome.failure_class, RESOURCE_OUTAGE)
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(outcome.attempts[0].strategy, RecoveryStrategy.RETRY_SAME)
        self.assertEqual(payload, _healthy_payload())

    def test_persistent_outage_degrades_with_gap_marker(self):
        manager = _manager()

        def dead(*_a, **_k):
            raise RuntimeError("backend down")

        payload, outcome = manager.run_node(
            "ocrp1", dead, candidates=["document_ocr_b"]
        )
        self.assertFalse(outcome.recovered)
        self.assertTrue(outcome.degraded)
        self.assertEqual(outcome.failure_class, RESOURCE_OUTAGE)
        self.assertEqual(gap_marker("ocrp1", outcome.failure_class, outcome.symptom),
                         payload)
        self.assertEqual(payload.get("status"), "degraded")
        self.assertNotIn("marks", payload)
        self.assertNotIn("document", payload)

    def test_substitution_uses_registry_ranked_runner_ups(self):
        manager = _manager()

        def dead(*_a, **_k):
            raise RuntimeError("primary always crashes")

        used = []

        def substitute(resource_id: str):
            used.append(resource_id)
            return _healthy_payload()

        payload, outcome = manager.run_node(
            "ocrp1", dead, candidates=["first", "second"],
            substitute_call=substitute,
        )
        self.assertTrue(outcome.recovered)
        self.assertEqual(used, ["first"])
        self.assertEqual(outcome.attempts[-1].strategy,
                         RecoveryStrategy.RESOURCE_SUBSTITUTION)

    def test_max_attempts_slices_the_ladder(self):
        manager = _manager(max_attempts=1)

        def dead(*_a, **_k):
            raise RuntimeError("always down")

        _, outcome = manager.run_node("ocrp1", dead, candidates=["b1", "b2"])
        self.assertTrue(outcome.degraded)
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(outcome.attempts[0].strategy, RecoveryStrategy.RETRY_SAME)

    def test_no_substitute_candidates_is_recorded_not_crashed(self):
        manager = _manager()

        def dead(*_a, **_k):
            raise RuntimeError("always down")

        _, outcome = manager.run_node("ocrp1", dead, candidates=[])
        self.assertTrue(outcome.degraded)
        self.assertIn(
            "no substitute candidates",
            outcome.attempts[-1].detail,
        )

    def test_reformulated_retry_keeps_evidence_honest(self):
        manager = _manager()
        seen_instructions = []

        def failing_then_ok(instruction=None):
            seen_instructions.append(instruction)
            if len(seen_instructions) < 2:
                return {"status": "error", "detail": "malformed"}
            return _healthy_payload()

        _, outcome = manager.run_node("ocrp1", failing_then_ok, candidates=["b"])
        self.assertTrue(outcome.recovered)
        self.assertEqual(outcome.attempts[-1].strategy,
                         RecoveryStrategy.RETRY_WITH_FEEDBACK)
        self.assertIn("recovery:", seen_instructions[1])


class EscalationMappingTests(unittest.TestCase):
    def test_low_confidence_maps_to_ocr_review_reason(self):
        self.assertEqual(
            review_reason_for(TOOL_LOW_CONFIDENCE),
            EvalReviewReason.LOW_OCR_CONFIDENCE,
        )
        self.assertEqual(
            review_reason_for(RESOURCE_OUTAGE),
            EvalReviewReason.RECOVERY_FAILED,
        )

    def test_gap_marker_carries_no_marks_keys(self):
        marker = gap_marker("evalqq1a", RESOURCE_OUTAGE, "crashed")
        self.assertNotIn("marks", marker)
        self.assertNotIn("verdict", marker)
        self.assertEqual(marker["status"], "degraded")

    def test_disagreement_escalates_only_on_agent_disagreement(self):
        disputed = ReconciliationResult(
            question_id="Q1",
            final_marks=None,
            final_confidence=0.5,
            agreed=False,
            adopted_from="none",
            review_reasons=[
                EvalReviewReason.AGENT_DISAGREEMENT,
                EvalReviewReason.RECOVERY_FAILED,
            ],
            primary=_agent(),
        )
        clean = ReconciliationResult(
            question_id="Q1",
            final_marks=4.0,
            final_confidence=0.9,
            agreed=True,
            adopted_from="both",
            review_reasons=[],
            primary=_agent(marks=4.0, confidence=0.9),
        )
        self.assertTrue(disagreement_requires_review(disputed))
        self.assertFalse(disagreement_requires_review(clean))
        self.assertEqual(
            disagreements_escalate_to_review(disputed),
            RecoveryStrategy.ESCALATE_TO_REVIEW,
        )
        self.assertEqual(
            disagreements_escalate_to_review(clean), RecoveryStrategy.RETRY_SAME
        )


if __name__ == "__main__":
    unittest.main()