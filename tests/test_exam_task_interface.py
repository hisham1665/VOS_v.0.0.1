"""Phase 0 regression tests for the exam-domain interface.

These prove the ExamEvaluationTask contract without executing the kernel:
the task builds a structurally valid AOS DAG, the two evaluation agents are
independent per question, and validation rules (unique question ids, positive
marks, rubric consistency) are enforced. No existing AOS behaviour is touched.
"""

import unittest

from aos_v0.core.graph_utils import build_waves, validate_graph
from aos_v0.exam.models import (
    EXAM_REQUIRED_FLAGS,
    ExamConfiguration,
    ExamEvaluationTask,
    EvaluationSettings,
    PaperReference,
    Question,
    QuestionType,
    Rubric,
    RubricCriterion,
)


def make_exam() -> ExamConfiguration:
    return ExamConfiguration(
        exam_id="CS2301",
        title="Computer Networks End Sem",
        subject="CS",
        version="1",
        questions=[
            Question(
                question_id="Q1",
                text="Explain deadlock.",
                max_marks=10,
                question_type=QuestionType.LONG_ANSWER,
                expected_concepts=["mutual exclusion", "hold and wait",
                                   "no preemption", "circular wait"],
                rubric=Rubric(criteria=[
                    RubricCriterion(criterion="definition", marks=2),
                    RubricCriterion(criterion="mutual_exclusion", marks=2),
                    RubricCriterion(criterion="hold_and_wait", marks=2),
                    RubricCriterion(criterion="no_preemption", marks=2),
                    RubricCriterion(criterion="circular_wait", marks=2),
                ]),
            ),
            Question(
                question_id="Q2",
                text="What is TCP?",
                max_marks=5,
                question_type=QuestionType.SHORT_ANSWER,
                expected_concepts=["connection-oriented", "reliable", "transport layer"],
            ),
        ],
    )


def make_task() -> ExamEvaluationTask:
    return ExamEvaluationTask(
        task_id="task_001",
        exam=make_exam(),
        paper=PaperReference(paper_id="23CS042", file_path="data/inputs/23CS042.pdf"),
    )


class ExamTaskValidationTests(unittest.TestCase):
    def test_rejects_duplicate_question_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate question_id"):
            ExamConfiguration(
                exam_id="E",
                title="T",
                questions=[
                    Question(question_id="Q1", text="first", max_marks=5),
                    Question(question_id="Q1", text="second", max_marks=5),
                ],
            )

    def test_rejects_non_positive_max_marks(self):
        with self.assertRaises(ValueError):
            Question(question_id="Q1", text="x", max_marks=0)

    def test_rubric_total_matches_question(self):
        exam = make_exam()
        q1 = exam.questions[0]
        self.assertEqual(q1.rubric.total, q1.max_marks)

    def test_requires_at_least_one_question(self):
        with self.assertRaises(ValueError):
            ExamConfiguration(exam_id="E", title="T", questions=[])

    def test_requirement_flags_cover_dependency_spine(self):
        flags = set(make_task().requirement_flags())
        self.assertTrue(EXAM_REQUIRED_FLAGS.issubset(flags))

    def test_json_round_trip(self):
        task = make_task()
        restored = ExamEvaluationTask.model_validate(task.model_dump())
        self.assertEqual(restored.exam.exam_id, task.exam.exam_id)
        self.assertEqual(restored.paper.paper_id, task.paper.paper_id)


class ExamTaskGraphTests(unittest.TestCase):
    def setUp(self):
        self.graph = make_task().build_graph()

    def test_graph_is_structurally_valid(self):
        validate_graph(self.graph)
        self.assertEqual(len(self.graph.nodes), 11)  # 1 ocr + 1 layout
        # + 1 sid + 1 qseg + 2 questions * (2 agents + 1 recon) + 1 report

    def test_ocr_is_root_and_report_is_sink(self):
        waves = build_waves(self.graph)
        by_id = {n.id: n for n in self.graph.nodes}
        ocr = [n for n in self.graph.nodes if n.capability == "document_ocr"]
        self.assertEqual(ocr[0].depends_on, [])
        self.assertIn(ocr[0], waves[0])
        report = [n for n in self.graph.nodes if n.capability == "report_generation"][0]
        self.assertTrue(all(n.id != report.id for n in self.graph.nodes if n is not report
                            for _dep in n.depends_on))

    def test_two_agents_are_independent_per_question(self):
        by_id = {n.id: n for n in self.graph.nodes}
        for q in ("1", "2"):
            a = by_id[f"evalqq{q}a"]
            b = by_id[f"evalqq{q}b"]
            self.assertNotIn(b.id, a.depends_on)
            self.assertNotIn(a.id, b.depends_on)

    def test_reconciliation_depends_on_both_agents(self):
        by_id = {n.id: n for n in self.graph.nodes}
        for q in ("1", "2"):
            recon = by_id[f"reconqq{q}"]
            self.assertIn(f"evalqq{q}a", recon.depends_on)
            self.assertIn(f"evalqq{q}b", recon.depends_on)

    def test_every_agent_is_parallel_with_its_verifier(self):
        waves = build_waves(self.graph)
        for q in ("1", "2"):
            a = [n for n in self.graph.nodes if n.id == f"evalqq{q}a"][0]
            b = [n for n in self.graph.nodes if n.id == f"evalqq{q}b"][0]
            wave_a = next(i for i, w in enumerate(waves) if a in w)
            wave_b = next(i for i, w in enumerate(waves) if b in w)
            self.assertEqual(wave_a, wave_b)

    def test_two_agent_toggle_documented_in_settings(self):
        settings = EvaluationSettings(two_agent_evaluation=False)
        self.assertFalse(settings.two_agent_evaluation)
        task = make_task().model_copy(update={"settings": settings})
        self.assertFalse(task.settings.two_agent_evaluation)


if __name__ == "__main__":
    unittest.main()