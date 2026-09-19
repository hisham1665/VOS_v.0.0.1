"""Phase 7 tests -- two-agent evaluation with disagreement analysis.

Covers the deliverable: two structurally independent agents evaluated the same
evidence (Agent 1 primary evaluator vs Agent 2 independent verifier, the
verifier never seeing Agent 1's verdict), the plan's structured agent output
surface, and the comparison engine -- agreed marks adopted, small divergence
confidence-weighted, large divergence routed to AGENT_DISAGREEMENT review with
the mark confidence-gated. Golden rules hold: a withheld/unreadable verdict is
never silently turned into a zero.
"""

import unittest

from aos_v0.exam.models import (
    AnswerKey,
    EvaluationSettings,
    EvalReviewReason,
    Question,
)
from aos_v0.exam.structure.models import AnswerSheetEntry
from aos_v0.exam.two_agent import (
    AgentEvaluation,
    IndependentVerifier,
    PrimaryEvaluator,
    evaluate_two_agent,
    reconcile_agents,
)


def concept_question(qid, concepts, marks=10, text="Q"):
    return Question(
        question_id=qid,
        text=text,
        max_marks=marks,
        question_type="long_answer",
        answer_key=AnswerKey(expected_concepts=concepts),
        rubric={"criteria": [
            {"criterion": concept.replace(" ", "_"), "marks": marks / len(concepts)}
            for concept in concepts
        ]},
    )


def entry(text, confidence=0.95, **kw):
    return AnswerSheetEntry(question_id="Q1", text=text,
                            confidence=confidence, **kw)


class IndependenceAndSchemasTests(unittest.TestCase):
    def test_agents_are_structurally_different(self):
        primary = PrimaryEvaluator()
        verifier = IndependentVerifier()
        self.assertNotEqual(
            primary._settings.use_semantic_analyzer,
            verifier._settings.use_semantic_analyzer,
        )
        self.assertNotEqual(
            primary._settings.accept_alternatives,
            verifier._settings.accept_alternatives,
        )
        self.assertLess(
            verifier._settings.spelling_tolerance,
            primary._settings.spelling_tolerance,
        )

    def test_verifier_never_sees_primary_result(self):
        q = concept_question("Q1", ["store and forward"])
        v_first = IndependentVerifier().evaluate(
            q, entry("switch buffers whole frames and forwards them"))
        evaluate_two_agent(q, entry("switch buffers whole frames and forwards them"))
        v_after = IndependentVerifier().evaluate(
            q, entry("switch buffers whole frames and forwards them"))
        self.assertEqual(v_first.marks, v_after.marks)
        self.assertEqual(v_first.agent, "verifier")

    def test_capability_tags_match_the_graph(self):
        q = concept_question("Q1", ["reliable"])
        a = PrimaryEvaluator().evaluate(q, entry("reliable"))
        b = IndependentVerifier().evaluate(q, entry("reliable"))
        self.assertEqual(a.capability, "semantic_answer_evaluation")
        self.assertEqual(b.capability, "answer_verification")

    def test_agent_to_plan_json_surface(self):
        q = concept_question("Q1", ["reliable", "connectionless"])
        plan = PrimaryEvaluator().evaluate(q, entry("reliable")).to_plan_json()
        self.assertIn("concepts_satisfied", plan)
        self.assertIn("missing_concepts", plan)
        self.assertIn("confidence", plan)
        self.assertIn("marks", plan)


class TwoAgentAgreementTests(unittest.TestCase):
    def test_literal_answer_agents_agree(self):
        q = concept_question("Q1", ["mutual exclusion", "hold and wait"])
        result = evaluate_two_agent(
            q, entry("mutual exclusion and hold and wait are present"))
        self.assertTrue(result.agreed)
        self.assertEqual(result.adopted_from, "both")
        self.assertEqual(result.final_marks, q.max_marks)
        self.assertFalse(result.needs_review)

    def test_blank_answer_agents_agree_on_zero(self):
        q = concept_question("Q1", ["hold and wait"])
        result = evaluate_two_agent(q, entry(""))
        self.assertTrue(result.agreed)
        self.assertEqual(result.final_marks, 0.0)
        self.assertEqual(
            [m for m in (result.primary.marks, result.verifier.marks)],
            [0.0, 0.0],
        )

    def test_both_withhold_on_unreadable(self):
        q = concept_question("Q1", ["hold and wait"])
        result = evaluate_two_agent(
            q, entry("hold and wait", confidence=0.3))
        self.assertIsNone(result.primary.marks)
        self.assertIsNone(result.verifier.marks)
        self.assertIsNone(result.final_marks)
        self.assertIn(EvalReviewReason.LOW_EVALUATION_CONFIDENCE,
                      result.review_reasons)


class DisagreementTests(unittest.TestCase):
    def test_paraphrase_diverges_to_agent_disagreement_review(self):
        q = concept_question("Q1", ["store and forward"])
        result = evaluate_two_agent(
            q, entry("a switch first buffers the entire frames and then "
                     "sends them on to the next hop"))
        self.assertFalse(result.agreed)
        self.assertEqual(result.adopted_from, "primary")
        self.assertEqual(result.final_marks, q.max_marks)   # confident side
        self.assertIn(EvalReviewReason.AGENT_DISAGREEMENT,
                      result.review_reasons)
        self.assertTrue(result.needs_review)
        self.assertIn("store and forward", result.disputed_concepts)

    def test_spelling_variant_diverges_between_agents(self):
        q = concept_question("Q1", ["no preemption"])
        result = evaluate_two_agent(
            q, entry("the resource can never be preempttiond away"))
        self.assertIn(EvalReviewReason.AGENT_DISAGREEMENT,
                      result.review_reasons)
        self.assertEqual(result.final_marks, q.max_marks)

    def test_small_divergence_confidence_weighted_no_review(self):
        primary = AgentEvaluation(
            agent="primary", capability="semantic_answer_evaluation",
            marks=9.0, max_marks=10.0,
            concepts_satisfied=["a", "b"], missing_concepts=["c"],
            reasoning="primary", confidence=0.92)
        verifier = AgentEvaluation(
            agent="verifier", capability="answer_verification",
            marks=8.0, max_marks=10.0,
            concepts_satisfied=["a"], missing_concepts=["b", "c"],
            reasoning="verifier", confidence=0.88)
        result = reconcile_agents("Q1", primary, verifier)
        self.assertTrue(result.agreed)
        self.assertEqual(result.adopted_from, "weighted")
        self.assertTrue(8.0 <= result.final_marks <= 9.0)
        self.assertNotIn(EvalReviewReason.AGENT_DISAGREEMENT,
                         result.review_reasons)

    def test_large_divergence_takes_confident_side(self):
        primary = AgentEvaluation(
            agent="primary", capability="semantic_answer_evaluation",
            marks=10.0, max_marks=10.0,
            concepts_satisfied=["a"], missing_concepts=[],
            reasoning="primary", confidence=0.9)
        verifier = AgentEvaluation(
            agent="verifier", capability="answer_verification",
            marks=2.0, max_marks=10.0,
            concepts_satisfied=[], missing_concepts=["a"],
            reasoning="verifier", confidence=0.85)
        result = reconcile_agents("Q1", primary, verifier)
        self.assertFalse(result.agreed)
        self.assertEqual(result.adopted_from, "primary")
        self.assertEqual(result.final_marks, 10.0)
        self.assertIn(EvalReviewReason.AGENT_DISAGREEMENT,
                      result.review_reasons)
        self.assertEqual(result.mark_difference, 8.0)


class UnscoredTests(unittest.TestCase):
    def test_one_unscored_adopts_scored_side_with_review(self):
        primary = AgentEvaluation(
            agent="primary", capability="semantic_answer_evaluation",
            marks=None, max_marks=10.0,
            concepts_satisfied=[], missing_concepts=["a"],
            reasoning="could not decide", confidence=0.3)
        verifier = AgentEvaluation(
            agent="verifier", capability="answer_verification",
            marks=4.0, max_marks=10.0,
            concepts_satisfied=["a"], missing_concepts=[],
            reasoning="verifier", confidence=0.8)
        result = reconcile_agents("Q1", primary, verifier)
        self.assertEqual(result.final_marks, 4.0)
        self.assertEqual(result.adopted_from, "verifier")
        self.assertIn(EvalReviewReason.LOW_EVALUATION_CONFIDENCE,
                      result.review_reasons)
        self.assertNotIn(EvalReviewReason.AGENT_DISAGREEMENT,
                         result.review_reasons)


class ToggleTests(unittest.TestCase):
    def test_two_agent_disabled_returns_single_agent(self):
        q = concept_question("Q1", ["reliable"])
        settings = EvaluationSettings(two_agent_evaluation=False)
        result = evaluate_two_agent(
            q, entry("reliable connection"), settings=settings)
        self.assertIsNone(result.verifier)
        self.assertTrue(result.agreed)
        self.assertEqual(result.adopted_from, "primary")
        self.assertNotIn(EvalReviewReason.AGENT_DISAGREEMENT,
                         result.review_reasons)


if __name__ == "__main__":
    unittest.main()