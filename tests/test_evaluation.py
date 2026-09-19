"""Phase 6 tests -- semantic evaluation engine.

Covers the evaluator deliverable: the three-level concept matcher (surface ->
meaning -> concept correctness), math step-marking with symbolic/numeric keys,
the rubric's partial-credit and expression rules, choice-type questions (MCQ /
true-false / fill-blank) with negative marking, and the engine's golden rules
-- evidence preservation, and the *never auto-zero* principle (an unreadable or
indeterminate answer is UNSCORED or routed to review, never silently marked 0).
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from aos_v0.exam.evaluate.engine import (
    evaluate_question,
    evaluate_sheet,
    main,
    parse_sheet_json,
    round_half_up,
)
from aos_v0.exam.evaluate.matcher import (
    ConceptMatcher,
    LocalSemanticAnalyzer,
    SemanticUnavailableError,
)
from aos_v0.exam.evaluate.math import (
    MathStep,
    derive_math_key,
    math_evaluate,
    math_steps_same,
)
from aos_v0.exam.evaluate.models import (
    Disposition,
    EvaluationFlag,
    EvaluationStatus,
    EvaluatorSettings,
    MatchLevel,
)
from aos_v0.exam.models import (
    AnswerKey,
    ConceptRelationship,
    ConceptRelationshipType,
    EvalReviewReason,
    ExamConfiguration,
    Question,
)
from aos_v0.exam.structure.models import (
    AnswerSheetEntry,
    MappingIssue,
    StudentIdentity,
    StructuredAnswerSheet,
)


def config(*questions):
    return ExamConfiguration(
        exam_id="midterm",
        title="Midterm",
        questions=questions,
    )


def question(qid, text="Q", max_marks=10, qtype="long_answer", key=None,
             rubric=None, **kw):
    kw.setdefault("answer_key", key or AnswerKey())
    return Question(
        question_id=qid,
        text=text,
        max_marks=max_marks,
        question_type=qtype,
        rubric=rubric,
        **kw,
    )


def sheet(*entries):
    return StructuredAnswerSheet(
        student=StudentIdentity(name="Anjali", roll_no="23CS042"),
        answers=entries,
    )


def entry(qid, text, confidence=0.9, **kw):
    return AnswerSheetEntry(question_id=qid, text=text,
                            confidence=confidence, **kw)


# ---------------------------------------------------------------------------
# Level 1/2/3 concept matcher
# ---------------------------------------------------------------------------


class ConceptMatcherTests(unittest.TestCase):
    def setUp(self):
        self.matcher = ConceptMatcher()
        self.q = question("Q2", text="Transport layer properties?",
                          answer_key=AnswerKey(expected_concepts=[
                              "connection oriented", "reliable",
                              "connectionless", "no congestion control"]))

    def test_surface_keywords_satisfy(self):
        got = self.matcher.match("reliable", "TCP is reliable and ordered",
                                 question=self.q.text)
        self.assertEqual(got.disposition, Disposition.SATISFIED)
        self.assertEqual(got.level, MatchLevel.SURFACE)

    def test_paraphrase_satisfied_at_semantic_level(self):
        answer = ("TCP is connection-oriented and reliable. UDP is "
                  "connectionless and performs no congestion control.")
        matches = self.matcher.match_all(
            self.q.text, answer, self.q.answer_key)
        by_name = {m.concept: m for m in matches}
        for concept in ("connection oriented", "reliable",
                        "connectionless", "no congestion control"):
            self.assertEqual(by_name[concept].disposition,
                             Disposition.SATISFIED, concept)
            self.assertTrue(by_name[concept].evidence, concept)

    def test_paraphrase_detected_without_the_exact_term(self):
        got = self.matcher.match(
            "store and forward",
            "a switch first buffers the entire frames and then sends them "
            "on to the next hop",
            question="switching")
        self.assertEqual(got.disposition, Disposition.SATISFIED)
        self.assertEqual(got.level, MatchLevel.SEMANTIC)

    def test_negated_concept_denied_is_contradicted(self):
        got = self.matcher.match("no congestion control",
                                 "UDP has congestion control",
                                 question=self.q.text)
        self.assertEqual(got.disposition, Disposition.CONTRADICTED)

    def test_negated_concept_satisfied_by_window(self):
        got = self.matcher.match("no preemption",
                                 "a resource can never be preempted",
                                 question=self.q.text)
        self.assertEqual(got.disposition, Disposition.SATISFIED)

    def test_spelling_variant_accepted_with_flag_note(self):
        got = self.matcher.match("congestion control",
                                 "TCP applies congestion controll",
                                 question=self.q.text)
        self.assertEqual(got.disposition, Disposition.SATISFIED)
        self.assertIn("spelling", got.reason)

    def test_missing_when_no_evidence(self):
        got = self.matcher.match("circular wait", "no relevant content",
                                 question=self.q.text)
        self.assertEqual(got.disposition, Disposition.MISSING)

    def test_alias_accepted(self):
        matcher = ConceptMatcher()
        key = AnswerKey(expected_concepts=["data link layer"])
        got = matcher.match("data link layer",
                            "the MAC layer handles framing",
                            question="Layers?", alias_forms=["mac layer"])
        self.assertEqual(got.disposition, Disposition.SATISFIED)
        self.assertEqual(got.alternative_of, "mac layer")


class QualifierAndRelationshipsTests(unittest.TestCase):
    def test_alternative_relationship_satisfies_both(self):
        key = AnswerKey(
            expected_concepts=["hold and wait", "resource holding"],
            concept_relationships=[ConceptRelationship(
                source="hold and wait", target="resource holding",
                relationship=ConceptRelationshipType.ALTERNATIVE)],
        )
        matches = ConceptMatcher().match_all(
            "Deadlock?", "each process holds a resource and waits", key)
        by = {m.concept: m for m in matches}
        self.assertEqual(by["hold and wait"].disposition, Disposition.SATISFIED)
        self.assertEqual(by["resource holding"].disposition,
                         Disposition.SATISFIED)
        # Relationship corrections never fabricate evidence: the target shows
        # the alternative it was satisfied through, with its own spans intact.
        self.assertEqual(by["resource holding"].alternative_of,
                         "hold and wait")

    def test_definition_concept_requires_substance(self):
        key = AnswerKey(expected_concepts=["definition", "no preemption"])
        got = ConceptMatcher().match_all(
            "Q", "definition", key)
        by = {m.concept: m for m in got}
        self.assertEqual(by["definition"].disposition, Disposition.MISSING)

    def test_definition_satisfied_by_substantive_answer(self):
        key = AnswerKey(expected_concepts=["definition", "no preemption"])
        got = ConceptMatcher().match_all(
            "Q",
            "deadlock is a state where a set of processes are blocked because "
            "each holds a resource and waits and resources are never preempted",
            key)
        by = {m.concept: m for m in got}
        self.assertEqual(by["definition"].disposition, Disposition.SATISFIED)
        self.assertEqual(by["definition"].level, MatchLevel.CONCEPTUAL)


class DeclaredAnalyzerTests(unittest.TestCase):
    def test_declared_transports_raise_semantic_unavailable(self):
        from aos_v0.exam.evaluate.matcher import BgeM3EmbeddingAnalyzer
        with self.assertRaises(SemanticUnavailableError):
            BgeM3EmbeddingAnalyzer().analyze("q", "a", "concept")


# ---------------------------------------------------------------------------
# Math evaluation
# ---------------------------------------------------------------------------


class MathEvaluationTests(unittest.TestCase):
    def test_derive_symbolic_key_from_reference(self):
        q = question("Q6", max_marks=5, qtype="numerical",
                     answer_key=AnswerKey(
                         reference_answers=["T = (N + 1) * L / R"]))
        key = derive_math_key(q)
        self.assertIsNotNone(key)
        self.assertEqual(key.expression, "T = (N + 1) * L / R")
        self.assertFalse(key.is_numeric)

    def test_steps_same_handles_ordering(self):
        self.assertTrue(
            math_steps_same("(N + 1) * L / R", "L / R * (N + 1)"))
        self.assertFalse(
            math_steps_same("(N + 1) * L / R", "N * L / R"))

    def test_symbolic_full_credit(self):
        q = question("Q6", max_marks=5, qtype="numerical",
                     answer_key=AnswerKey(
                         reference_answers=["T = (N + 1) * L / R"]))
        key = derive_math_key(q)
        ev = math_evaluate("T = (N + 1) * L / R", key)
        self.assertTrue(ev.final_answer_correct)
        formula = next(s for s in ev.steps if s.step == MathStep.FORMULA.value)
        self.assertTrue(formula.satisfied)

    def test_numeric_tolerance(self):
        q = question("Q7", max_marks=3, qtype="numerical",
                     answer_key=AnswerKey(reference_answers=["2.5 seconds"]))
        key = derive_math_key(q)
        ev = math_evaluate("the result is 2.5 seconds", key)
        self.assertTrue(ev.final_answer_correct)

    def test_wrong_formula_is_indeterminate_not_wrong(self):
        q = question("Q6", max_marks=5, qtype="numerical",
                     answer_key=AnswerKey(
                         reference_answers=["T = (N + 1) * L / R"]))
        key = derive_math_key(q)
        ev = math_evaluate("T = N * L / R", key)
        self.assertIsNone(ev.final_answer_correct)


# ---------------------------------------------------------------------------
# Rubric + engine golden rules
# ---------------------------------------------------------------------------


class RubricTests(unittest.TestCase):
    def test_partial_credit_across_criteria(self):
        q = question("Q4", max_marks=4, qtype="long_answer",
                     answer_key=AnswerKey(expected_concepts=[
                         "mutual exclusion", "circular wait"]),
                     rubric={"criteria": [
                         {"criterion": "mutual exclusion", "marks": 2},
                         {"criterion": "circular wait", "marks": 2}]})
        evaluation = evaluate_question(
            q, entry("Q4", "processes request resources one at a time; "
                            "mutual exclusion prevents simultaneous use"))
        self.assertEqual(evaluation.marks, 2.0)
        self.assertEqual(evaluation.status, EvaluationStatus.OK)

    def test_expressive_criterion_needs_substance(self):
        q = question("Q5", max_marks=10, qtype="long_answer",
                     special_rules="only a full set of all four conditions "
                                   "earns the circular_wait criterion",
                     answer_key=AnswerKey(expected_concepts=[
                         "definition", "mutual exclusion", "hold and wait",
                         "no preemption", "circular wait", "deadlock prevention"]),
                     rubric={"criteria": [
                         {"criterion": "definition", "marks": 2},
                         {"criterion": "mutual_exclusion", "marks": 2},
                         {"criterion": "hold_and_wait", "marks": 2},
                         {"criterion": "no_preemption", "marks": 2},
                         {"criterion": "circular_wait", "marks": 2}]})
        evaluation = evaluate_question(
            q, entry("Q5",
                     "Deadlock is a state where processes are blocked because "
                     "each holds a resource and waits for another held by "
                     "another process. The four conditions are mutual "
                     "exclusion, hold and wait, no preemption and circular "
                     "wait."))
        self.assertEqual(evaluation.marks, 10.0)
        by = {c.criterion: c for c in evaluation.criteria}
        self.assertEqual(by["definition"].marks, 2.0)

    def test_special_rule_keeps_circular_wait_at_partial(self):
        q = question("Q5", max_marks=10, qtype="long_answer",
                     special_rules="only a full set of all four conditions "
                                   "earns the circular_wait criterion",
                     answer_key=AnswerKey(expected_concepts=[
                         "mutual exclusion", "hold and wait",
                         "no preemption", "circular wait"]),
                     rubric={"criteria": [
                         {"criterion": "mutual_exclusion", "marks": 2},
                         {"criterion": "hold_and_wait", "marks": 2},
                         {"criterion": "no_preemption", "marks": 2},
                         {"criterion": "circular_wait", "marks": 2}]})
        evaluation = evaluate_question(
            q, entry("Q5",
                     "circular wait, mutual exclusion and hold and wait are "
                     "among the deadlock conditions"))
        circular = next(c for c in evaluation.criteria
                        if c.criterion == "circular_wait")
        self.assertEqual(circular.disposition, Disposition.PARTIAL)
        self.assertEqual(circular.marks, 1.0)

    def test_wrong_final_value_preserves_step_marks(self):
        q = question("Q7", max_marks=6, qtype="numerical",
                     answer_key=AnswerKey(reference_answers=["2.5 seconds"]),
                     rubric={"criteria": [
                         {"criterion": "substitution", "marks": 2},
                         {"criterion": "calculation", "marks": 2},
                         {"criterion": "units", "marks": 1},
                         {"criterion": "final_value", "marks": 1}]})
        evaluation = evaluate_question(
            q, entry("Q7", "substituting the values gives 3 seconds"))
        self.assertIn(EvaluationFlag.STEP_MARKS_PRESERVED, evaluation.flags)
        self.assertEqual(evaluation.marks, 5.0)
        final_step = evaluation.math.steps[-1]
        self.assertEqual(final_step.step, MathStep.FINAL_ANSWER.value)
        self.assertFalse(final_step.satisfied)

    def test_indeterminate_math_routes_to_review_not_zero(self):
        q = question("Q6", max_marks=5, qtype="numerical",
                     answer_key=AnswerKey(
                         reference_answers=["T = (N + 1) * L / R"]),
                     rubric={"criteria": [
                         {"criterion": "formula", "marks": 2},
                         {"criterion": "substitution", "marks": 2},
                         {"criterion": "final_value", "marks": 1}]})
        evaluation = evaluate_question(
            q, entry("Q6", "T = N * L / R, then substituting values"))
        self.assertIn(EvaluationFlag.LOW_EVALUATION_CONFIDENCE,
                      evaluation.flags)
        self.assertIn(EvalReviewReason.LOW_EVALUATION_CONFIDENCE,
                      evaluation.review_reasons)
        self.assertEqual(evaluation.status, EvaluationStatus.REVIEW)
        self.assertIsNotNone(evaluation.marks)


# ---------------------------------------------------------------------------
# Choice questions
# ---------------------------------------------------------------------------


class ChoiceQuestionTests(unittest.TestCase):
    def test_mcq_option_match(self):
        q = question("Q1", max_marks=2, qtype="mcq", negative_marks=0.5,
                     answer_key=AnswerKey(
                         reference_answers=["b. store and forward switching"]))
        evaluation = evaluate_question(q, entry("Q1", "b"))
        self.assertEqual(evaluation.marks, 2.0)

    def test_mcq_content_match(self):
        q = question("Q1", max_marks=2, qtype="mcq", negative_marks=0.5,
                     answer_key=AnswerKey(
                         reference_answers=["b. store and forward switching"]))
        evaluation = evaluate_question(
            q, entry("Q1", "store and forward switching"))
        self.assertEqual(evaluation.marks, 2.0)

    def test_mcq_wrong_applies_negative_marking(self):
        q = question("Q1", max_marks=2, qtype="mcq", negative_marks=0.5,
                     answer_key=AnswerKey(
                         reference_answers=["b. store and forward switching"]))
        evaluation = evaluate_question(q, entry("Q1", "c"))
        self.assertEqual(evaluation.marks, -0.5)
        self.assertIn(EvaluationFlag.NEGATIVE_MARKING_APPLIED,
                      evaluation.flags)

    def test_fill_blank_accepted_form(self):
        q = question("Q3", max_marks=1, qtype="fill_blank",
                     answer_key=AnswerKey(
                         reference_answers=["Maximum Transmission Unit"]))
        evaluation = evaluate_question(
            q, entry("Q3", "MTU is the Maximum Transmission Unit"))
        self.assertEqual(evaluation.marks, 1.0)

    def test_true_false_explicit(self):
        q = question("Q2", max_marks=1, qtype="true_false",
                     answer_key=AnswerKey(reference_answers=["true"]))
        self.assertEqual(evaluate_question(q, entry("Q2", "true")).marks, 1.0)
        self.assertEqual(
            evaluate_question(q, entry("Q2", "no")).marks, -0.0)

    def test_true_false_false_side_via_concepts(self):
        q = question("Q2", max_marks=2, qtype="true_false",
                     answer_key=AnswerKey(
                         reference_answers=["false"],
                         expected_concepts=["no congestion control"]))
        evaluation = evaluate_question(
            q, entry("Q2", "UDP has no congestion control"))
        self.assertEqual(evaluation.marks, 2.0)

    def test_true_false_false_side_denied(self):
        q = question("Q2", max_marks=2, qtype="true_false",
                     answer_key=AnswerKey(
                         reference_answers=["false"],
                         expected_concepts=["no congestion control"]))
        evaluation = evaluate_question(
            q, entry("Q2", "UDP has congestion control"))
        self.assertNotEqual(evaluation.marks, 2.0)


# ---------------------------------------------------------------------------
# Engine golden rules
# ---------------------------------------------------------------------------


class EngineTests(unittest.TestCase):
    def test_blank_is_zero_not_unscored(self):
        q = question("Q4", max_marks=5,
                     answer_key=AnswerKey(expected_concepts=["mutual exclusion"]))
        evaluation = evaluate_question(q, entry("Q4", ""))
        self.assertEqual(evaluation.marks, 0.0)
        self.assertIn(EvaluationFlag.BLANK_ANSWER, evaluation.flags)
        self.assertEqual(evaluation.status, EvaluationStatus.OK)

    def test_crossed_out_blank_is_zero_with_flag(self):
        q = question("Q4", max_marks=5,
                     answer_key=AnswerKey(expected_concepts=["mutual exclusion"]))
        evaluation = evaluate_question(
            q, entry("Q4", "", crossed_out=True))
        self.assertEqual(evaluation.marks, 0.0)
        self.assertIn(EvaluationFlag.CROSSED_OUT, evaluation.flags)

    def test_unreadable_answer_never_auto_zero(self):
        q = question("Q4", max_marks=5,
                     answer_key=AnswerKey(expected_concepts=["mutual exclusion"]))
        evaluation = evaluate_question(
            q, entry("Q4", "unreadable text", confidence=0.3))
        self.assertIsNone(evaluation.marks)
        self.assertEqual(evaluation.status, EvaluationStatus.UNSCORED)
        self.assertIn(EvalReviewReason.LOW_OCR_CONFIDENCE,
                      evaluation.review_reasons)

    def test_unreadable_blank_routes_to_review(self):
        q = question("Q4", max_marks=5,
                     answer_key=AnswerKey(expected_concepts=["mutual exclusion"]))
        evaluation = evaluate_question(q, entry("Q4", "", confidence=0.3))
        self.assertIsNone(evaluation.marks)
        self.assertEqual(evaluation.status, EvaluationStatus.UNSCORED)
        self.assertIn(EvalReviewReason.LOW_OCR_CONFIDENCE,
                      evaluation.review_reasons)

    def test_structuring_ambiguity_routes_to_review(self):
        q = question("Q4", max_marks=5,
                     answer_key=AnswerKey(
                         expected_concepts=["mutual exclusion"]))
        evaluation = evaluate_question(
            q, entry("Q4", "mutual exclusion holds",
                     issues=[MappingIssue.MULTIPLE_ATTEMPTS]))
        self.assertEqual(evaluation.status, EvaluationStatus.REVIEW)
        self.assertIn(EvaluationFlag.AMBIGUOUS_ANSWER, evaluation.flags)
        self.assertIn(EvalReviewReason.AMBIGUOUS_ANSWER,
                      evaluation.review_reasons)

    def test_evaluate_sheet_totals(self):
        cfg = config(
            question("Q1", max_marks=2, qtype="mcq", negative_marks=0.5,
                     answer_key=AnswerKey(
                         reference_answers=["b. store and forward switching"])),
            question("Q4", max_marks=4,
                     answer_key=AnswerKey(
                         expected_concepts=["mutual exclusion"])),
        )
        evaluation = evaluate_sheet(cfg, sheet(
            entry("Q1", "b"),
            entry("Q4", "mutual exclusion prevents simultaneous use"),
        ))
        self.assertEqual(evaluation.total_marks, 6.0)
        self.assertEqual(evaluation.max_marks, 6.0)
        self.assertEqual(evaluation.scored_questions, ["Q1", "Q4"])

    def test_unanswered_question_maps_to_zero(self):
        cfg = config(
            question("Q1", max_marks=5,
                     answer_key=AnswerKey(
                         expected_concepts=["hold and wait"])))
        evaluation = evaluate_sheet(cfg, sheet(entry("Q2", "extra")))
        self.assertEqual(evaluation.question_evaluations[0].marks, 0.0)

    def test_low_confidence_not_totalled(self):
        cfg = config(question("Q1", max_marks=5,
                              answer_key=AnswerKey(
                                  expected_concepts=["hold and wait"])))
        evaluation = evaluate_sheet(
            cfg, sheet(entry("Q1", "hold and wait", confidence=0.3)))
        self.assertEqual(evaluation.total_marks, 0.0)
        self.assertIn("Q1", evaluation.unscored_questions)
        self.assertTrue(evaluation.needs_review)


class SheetJsonTests(unittest.TestCase):
    def test_parse_sheet_json_plan_surface(self):
        sheet_obj = parse_sheet_json({
            "student": {"name": "Anjali", "roll_no": "23CS042"},
            "answers": [{"question_id": "Q1", "pages": [1, 2],
                         "text": "hello"}],
        })
        self.assertEqual(sheet_obj.student.roll_no, "23CS042")
        self.assertEqual(sheet_obj.answers[0].text, "hello")
        self.assertEqual(sheet_obj.answers[0].confidence, 1.0)

    def test_round_half_up(self):
        self.assertEqual(round_half_up(2.5), 2.5)
        self.assertEqual(round_half_up(0.5), 0.5)
        self.assertEqual(round_half_up(1.0), 1.0)


class CliTests(unittest.TestCase):
    def test_cli_evaluates_and_prints_json_surface(self):
        cfg = config(
            question("Q1", max_marks=2, qtype="mcq", negative_marks=0.5,
                     answer_key=AnswerKey(
                         reference_answers=["b. store and forward switching"])),
            question("Q4", max_marks=4,
                     answer_key=AnswerKey(
                         expected_concepts=["mutual exclusion"])),
        )
        plan = {
            "student": {"name": "Anjali", "roll_no": "23CS042"},
            "answers": [
                {"question_id": "Q1", "pages": [1], "text": "b"},
                {"question_id": "Q4", "pages": [1],
                 "text": "mutual exclusion prevents simultaneous use"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "exam.json")
            sheet_path = os.path.join(tmp, "sheet.json")
            with open(cfg_path, "w") as fh:
                json.dump(cfg.model_dump(), fh)
            with open(sheet_path, "w") as fh:
                json.dump(plan, fh)

            out = io.StringIO()
            with redirect_stdout(out):
                code = main([sheet_path, "--config", cfg_path, "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result["total_marks"], 6.0)
            self.assertEqual(result["max_marks"], 6.0)
            self.assertEqual(result["student"]["roll_no"], "23CS042")

    def test_cli_roster_mismatch_is_not_a_crash(self):
        cfg = config(question("Q1", max_marks=2,
                              answer_key=AnswerKey(
                                  expected_concepts=["mutual exclusion"])))
        plan = {
            "student": {"name": "Anjali", "roll_no": "999"},
            "answers": [{"question_id": "Q1", "pages": [1],
                         "text": "mutual exclusion"}],
        }
        roster = {"entries": [{"roll_no": "23CS042", "name": "Anjali"}]}
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "exam.json")
            sheet_path = os.path.join(tmp, "sheet.json")
            roster_path = os.path.join(tmp, "roster.json")
            with open(cfg_path, "w") as fh:
                json.dump(cfg.model_dump(), fh)
            with open(sheet_path, "w") as fh:
                json.dump(plan, fh)
            with open(roster_path, "w") as fh:
                json.dump(roster, fh)
            code = main([sheet_path, "--config", cfg_path,
                         "--roster", roster_path])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()