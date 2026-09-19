"""Phase-2 tests for the exam configuration and answer-key engine."""

import importlib
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from aos_v0.exam.config import (
    ConfigIssue,
    ConfigLoadError,
    collect_issues,
    dump_exam_configuration,
    load_exam_configuration,
    main,
    summarize,
)
from aos_v0.exam.models import (
    AnswerKey,
    ConceptRelationship,
    ConceptRelationshipType,
    ExamConfiguration,
    MarkingRules,
    PartialCreditRule,
    Question,
    QuestionType,
    Rubric,
    RubricCriterion,
)

_HAS_YAML = importlib.util.find_spec("yaml") is not None

_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "sample_exam.json"


def _minimal_exam(**q_kwargs) -> ExamConfiguration:
    """Build a minimal valid exam with one question."""
    base = dict(
        question_id="Q1",
        text="Sample",
        max_marks=5,
        question_type=QuestionType.SHORT_ANSWER,
    )
    base.update(q_kwargs)
    return ExamConfiguration(
        exam_id="E1",
        title="T",
        questions=[Question(**base)],
    )


def _full_exam(**q_kwargs) -> ExamConfiguration:
    q1 = Question(
        question_id="Q1",
        text="Explain deadlock.",
        max_marks=10,
        question_type=QuestionType.LONG_ANSWER,
        answer_key=AnswerKey(
            expected_concepts=["mutual exclusion", "hold and wait", "no preemption", "circular wait"],
            reference_answers=["Deadlock is a permanent blocking..."],
            accepted_alternatives=[],
            keywords=["blocked", "cycle"],
            concept_relationships=[
                ConceptRelationship(
                    source="circular wait",
                    relationship=ConceptRelationshipType.REQUIRES,
                    target="mutual exclusion",
                    weight=1.0,
                )
            ],
        ),
        rubric=Rubric(criteria=[
            RubricCriterion(criterion="definition", marks=5),
            RubricCriterion(criterion="condition_1", marks=2.5),
            RubricCriterion(criterion="condition_2", marks=2.5),
        ]),
        partial_credit=True,
        partial_credit_rules=[
            PartialCreditRule(criterion="definition", fraction=0.5),
        ],
    )
    q2 = Question(
        question_id="Q2",
        text="What is ARP?",
        max_marks=4,
        question_type=QuestionType.SHORT_ANSWER,
        answer_key=AnswerKey(
            expected_concepts=["address resolution"],
            reference_answers=["ARP resolves IP to MAC."],
            accepted_alternatives=["L2 mapping"],
        ),
        rubric=Rubric(criteria=[
            RubricCriterion(criterion="concept", marks=4),
        ]),
    )
    q1_overrides = dict(q_kwargs)
    return ExamConfiguration(
        exam_id="E2",
        title="Sample Exam",
        subject="Systems",
        version="2",
        institution="Test University",
        duration_minutes=120,
        instructions="Answer all.",
        marking_rules=MarkingRules(
            negative_marking_default=0.25,
            partial_credit_default=True,
            max_marks_floor=0.0,
            rounding="half_up",
        ),
        questions=[q1, q2],
        **q1_overrides,
    )


class TestBackwardCompatibility(unittest.TestCase):
    """Fold legacy flat fields into AnswerKey and expose via properties."""

    def test_fold_flat_expected_concepts(self):
        q = Question(
            question_id="Q1",
            text="t",
            max_marks=5,
            expected_concepts=["a", "b", "c"],
        )
        self.assertIsInstance(q.answer_key, AnswerKey)
        self.assertEqual(q.expected_concepts, ["a", "b", "c"])
        self.assertEqual(q.answer_key.expected_concepts, ["a", "b", "c"])

    def test_fold_flat_all_layers(self):
        q = Question(
            question_id="Q1",
            text="t",
            max_marks=5,
            expected_concepts=["A"],
            reference_answers=["B"],
            accepted_alternatives=["C"],
            keywords=["D"],
        )
        self.assertEqual(q.expected_concepts, ["A"])
        self.assertEqual(q.reference_answers, ["B"])
        self.assertEqual(q.accepted_alternatives, ["C"])
        self.assertEqual(q.keywords, ["D"])

    def test_fold_merges_with_existing_answer_key(self):
        q = Question(
            question_id="Q1",
            text="t",
            max_marks=5,
            expected_concepts=["c", "a"],
            answer_key=AnswerKey(expected_concepts=["a", "b"]),
        )
        self.assertEqual(q.expected_concepts, ["a", "b", "c"])

    def test_fold_no_duplicate_in_merged(self):
        q = Question(
            question_id="Q1",
            text="t",
            max_marks=5,
            expected_concepts=["b", "a"],
            answer_key=AnswerKey(expected_concepts=["a", "b"]),
        )
        self.assertEqual(q.expected_concepts, ["a", "b"])

    def test_extra_forbid_rejects_unknown_key(self):
        with self.assertRaises(ValidationError):
            Question(
                question_id="Q1",
                text="t",
                max_marks=5,
                typo_field="x",  # type: ignore[arg-type]
            )


class TestQuestionAlias(unittest.TestCase):
    """Accept the plan's illustrative JSON keys ("question"/"type")."""

    def test_alias_construction(self):
        q = Question(**{
            "question_id": "Q1",
            "question": "What is 2+2?",
            "type": "numerical",
            "max_marks": 2,
        })
        self.assertEqual(q.text, "What is 2+2?")
        self.assertEqual(q.question_type, QuestionType.NUMERICAL)

    def test_alias_roundtrip_uses_canonical_keys(self):
        q = Question(**{
            "question_id": "Q1",
            "question": "What is 2+2?",
            "type": "numerical",
            "max_marks": 2,
        })
        dumped = q.model_dump(mode="json")
        self.assertIn("text", dumped)
        self.assertIn("question_type", dumped)
        self.assertNotIn("question", dumped)
        self.assertNotIn("type", dumped)


class TestAnswerKeyLayers(unittest.TestCase):
    def test_answer_key_fields(self):
        ak = AnswerKey(
            reference_answers=["a"],
            expected_concepts=["b"],
            accepted_alternatives=["c"],
            keywords=["d"],
            concept_relationships=[
                ConceptRelationship(source="b", target="c", relationship="implies")
            ],
        )
        self.assertEqual(ak.reference_answers, ["a"])
        self.assertEqual(len(ak.concept_relationships), 1)

    def test_concept_relationship_types_enum(self):
        self.assertEqual(set(ConceptRelationshipType), {
            ConceptRelationshipType.REQUIRES,
            ConceptRelationshipType.IMPLIES,
            ConceptRelationshipType.ALTERNATIVE,
            ConceptRelationshipType.CONFLICTS,
        })


class TestPartialCreditRule(unittest.TestCase):
    def test_schema_roundtrip(self):
        r = PartialCreditRule(criterion="definition", fraction=0.5, description="partial")
        dumped = r.model_dump()
        restored = PartialCreditRule.model_validate(dumped)
        self.assertEqual(restored.criterion, "definition")
        self.assertAlmostEqual(restored.fraction, 0.5)


class TestMarkingRules(unittest.TestCase):
    def test_defaults(self):
        m = MarkingRules()
        self.assertAlmostEqual(m.negative_marking_default, 0.0)
        self.assertTrue(m.partial_credit_default)
        self.assertAlmostEqual(m.max_marks_floor, 0.0)
        self.assertEqual(m.rounding, "none")

    def test_override(self):
        m = MarkingRules(
            negative_marking_default=0.33,
            partial_credit_default=False,
            max_marks_floor=2.0,
            rounding="truncate",
        )
        self.assertAlmostEqual(m.negative_marking_default, 0.33)
        self.assertFalse(m.partial_credit_default)
        self.assertEqual(m.rounding, "truncate")

    def test_extra_forbid(self):
        with self.assertRaises(ValidationError):
            MarkingRules(unknown=True)  # type: ignore[call-arg]


class TestExamConfigurationFull(unittest.TestCase):
    def test_fields_and_properties(self):
        exam = _full_exam()
        self.assertEqual(exam.exam_id, "E2")
        self.assertEqual(exam.question_count, 2)
        self.assertAlmostEqual(exam.total_marks, 14.0)
        self.assertEqual(exam.marking_rules.rounding, "half_up")
        self.assertEqual(exam.duration_minutes, 120)

    def test_json_roundtrip(self):
        exam = _full_exam()
        dumped = exam.model_dump(mode="json")
        restored = ExamConfiguration.model_validate(dumped)
        self.assertEqual(restored.exam_id, exam.exam_id)
        self.assertEqual(restored.question_count, exam.question_count)
        self.assertAlmostEqual(restored.total_marks, exam.total_marks)
        self.assertEqual(restored.marking_rules.rounding, exam.marking_rules.rounding)

    def test_negative_marks_validates(self):
        exam = _full_exam()
        q = exam.questions[0]
        self.assertAlmostEqual(q.negative_marks, 0.0)


class TestLoadExamConfiguration(unittest.TestCase):
    def test_load_example(self):
        if not _EXAMPLE_PATH.is_file():
            self.skipTest("sample_exam.json not found")
        exam = load_exam_configuration(_EXAMPLE_PATH)
        self.assertEqual(exam.question_count, 11)
        self.assertAlmostEqual(exam.total_marks, 56.0)
        self.assertEqual(exam.institution, "Example Institute of Technology")

    def test_load_missing_file(self):
        with self.assertRaises(ConfigLoadError):
            load_exam_configuration("nonexistent_file.json")

    def test_load_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{bad json}}} bad")
            f.flush()
            path = f.name
        try:
            with self.assertRaises(ConfigLoadError):
                load_exam_configuration(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_load_invalid_extension(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<config/>")
            f.flush()
            path = f.name
        try:
            with self.assertRaises(ConfigLoadError):
                load_exam_configuration(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_load_bad_schema(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"exam_id": "E", "title": "T"}, f)
            f.flush()
            path = f.name
        try:
            with self.assertRaises(ConfigLoadError):
                load_exam_configuration(path)
        finally:
            Path(path).unlink(missing_ok=True)


class TestDumpExamConfiguration(unittest.TestCase):
    def test_json_round_trip(self):
        exam = _minimal_exam()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            dump_path = f.name
        try:
            dump_exam_configuration(exam, dump_path)
            restored = load_exam_configuration(dump_path)
            self.assertEqual(restored.exam_id, exam.exam_id)
            self.assertAlmostEqual(restored.total_marks, exam.total_marks)
        finally:
            Path(dump_path).unlink(missing_ok=True)

    @unittest.skipUnless(_HAS_YAML, "PyYAML not installed")
    def test_yaml_round_trip(self):
        exam = _minimal_exam()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            dump_path = f.name
        try:
            dump_exam_configuration(exam, dump_path)
            restored = load_exam_configuration(dump_path)
            self.assertEqual(restored.exam_id, exam.exam_id)
            self.assertAlmostEqual(restored.total_marks, exam.total_marks)
        finally:
            Path(dump_path).unlink(missing_ok=True)

    def test_dump_invalid_extension(self):
        exam = _minimal_exam()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            dump_path = f.name
        try:
            with self.assertRaises(ConfigLoadError):
                dump_exam_configuration(exam, dump_path)
        finally:
            Path(dump_path).unlink(missing_ok=True)


class TestCollectIssues(unittest.TestCase):
    def test_example_has_no_issues(self):
        if not _EXAMPLE_PATH.is_file():
            self.skipTest("sample_exam.json not found")
        exam = load_exam_configuration(_EXAMPLE_PATH)
        issues = collect_issues(exam)
        self.assertEqual(issues, [], "\n".join(i.render() for i in issues))

    def test_clean_minimal_exam(self):
        exam = _minimal_exam(
            answer_key=AnswerKey(reference_answers=["x"]),
            rubric=Rubric(criteria=[RubricCriterion(criterion="a", marks=5)]),
        )
        self.assertEqual(collect_issues(exam), [])

    def test_duplicate_rubric_criteria(self):
        exam = _minimal_exam(
            rubric=Rubric(criteria=[
                RubricCriterion(criterion="a", marks=2.5),
                RubricCriterion(criterion="a", marks=2.5),
            ]),
        )
        issues = collect_issues(exam)
        self.assertTrue(
            any("duplicate criterion" in i.message for i in issues),
            issues,
        )

    def test_rubric_total_mismatch(self):
        exam = _minimal_exam(
            rubric=Rubric(criteria=[
                RubricCriterion(criterion="a", marks=3.0),
            ]),
        )
        issues = collect_issues(exam)
        self.assertTrue(
            any(i.severity == "warning" and "rubric total" in i.message for i in issues),
            issues,
        )

    def test_partial_credit_rule_unknown_criterion(self):
        exam = _minimal_exam(
            rubric=Rubric(criteria=[RubricCriterion(criterion="a", marks=5)]),
            partial_credit_rules=[PartialCreditRule(criterion="nonexistent", fraction=0.5)],
        )
        issues = collect_issues(exam)
        self.assertTrue(
            any(i.severity == "error" and "nonexistent" in i.message for i in issues),
            issues,
        )

    def test_partial_credit_rule_no_rubric(self):
        exam = _minimal_exam(
            partial_credit_rules=[PartialCreditRule(criterion="x", fraction=0.5)],
        )
        issues = collect_issues(exam)
        self.assertTrue(
            any(i.severity == "error" and "require a rubric" in i.message for i in issues),
            issues,
        )

    def test_relationship_unknown_concept(self):
        exam = _minimal_exam(
            answer_key=AnswerKey(
                expected_concepts=["A"],
                concept_relationships=[
                    ConceptRelationship(source="A", target="NONEXISTENT", relationship="requires")
                ],
            ),
        )
        issues = collect_issues(exam)
        self.assertTrue(
            any(i.severity == "error" and "NONEXISTENT" in i.message for i in issues),
            issues,
        )

    def test_self_referencing_relationship(self):
        exam = _minimal_exam(
            answer_key=AnswerKey(
                expected_concepts=["A"],
                concept_relationships=[
                    ConceptRelationship(source="A", target="A", relationship="requires")
                ],
            ),
        )
        issues = collect_issues(exam)
        self.assertTrue(
            any(i.severity == "warning" and "self-referencing" in i.message for i in issues),
            issues,
        )

    def test_negative_marks_exceeds_max(self):
        exam = _minimal_exam(negative_marks=99.0)
        issues = collect_issues(exam)
        self.assertTrue(
            any(i.severity == "warning" and "negative marks" in i.message for i in issues),
            issues,
        )

    def test_empty_answer_key_no_rubric(self):
        exam = _minimal_exam()
        issues = collect_issues(exam)
        self.assertTrue(
            any(i.severity == "warning" and "neither a rubric" in i.message for i in issues),
            issues,
        )

    def test_partial_credit_muted_warning(self):
        exam = _minimal_exam(
            partial_credit=False,
            rubric=Rubric(criteria=[RubricCriterion(criterion="a", marks=5)]),
            partial_credit_rules=[PartialCreditRule(criterion="a", fraction=0.5)],
        )
        issues = collect_issues(exam)
        self.assertTrue(
            any(i.severity == "warning" and "mutes" in i.message for i in issues),
            issues,
        )

    def test_mcq_multiple_reference_answers(self):
        exam = _minimal_exam(
            question_type=QuestionType.MCQ,
            answer_key=AnswerKey(reference_answers=["a", "b"]),
        )
        issues = collect_issues(exam)
        self.assertTrue(
            any(i.severity == "advisory" and "mcq" in i.message for i in issues),
            issues,
        )

    def test_self_referencing_criterion_rules(self):
        exam = _minimal_exam(
            rubric=Rubric(criteria=[RubricCriterion(criterion="a", marks=5)]),
            partial_credit=False,
        )
        issues = collect_issues(exam)
        self.assertTrue(
            any(i.severity == "advisory" and "all-or-nothing" in i.message for i in issues),
            issues,
        )


class TestSummarize(unittest.TestCase):
    def test_returns_expected_keys(self):
        exam = _full_exam()
        s = summarize(exam)
        self.assertIn("exam_id", s)
        self.assertIn("total_marks", s)
        self.assertIn("question_types", s)
        self.assertEqual(s["exam_id"], "E2")
        self.assertEqual(s["questions"], 2)


class TestConfigCLI(unittest.TestCase):
    def test_no_args_returns_usage(self):
        self.assertEqual(main([]), 2)

    def test_help_returns_zero(self):
        self.assertEqual(main(["--help"]), 0)

    def test_valid_file_returns_zero(self):
        if not _EXAMPLE_PATH.is_file():
            self.skipTest("sample_exam.json not found")
        self.assertEqual(main([str(_EXAMPLE_PATH)]), 0)

    def test_invalid_file_returns_one(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("}bad{")
            f.flush()
            path = f.name
        try:
            self.assertEqual(main([path]), 1)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_bad_schema_returns_one(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"exam_id": "E"}, f)
            f.flush()
            path = f.name
        try:
            self.assertEqual(main([path]), 1)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
