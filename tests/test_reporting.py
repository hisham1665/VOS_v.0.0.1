"""Phase 12 -- Reports, CSV and Analytics.

Covers the three CSVs, JSON export, individual + batch report generators, the
analytics API and dashboard, the assembler's review-store overlay (proposed ->
final marks), and the reporting CLI over a real batch results JSONL.
"""

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from aos_v0.exam.batch import BatchConfig, run_batch
from aos_v0.exam.orchestrator import ExamOrchestrator
from aos_v0.exam.review import (
    ReviewDecision,
    ReviewItem,
    ReviewStore,
)
from aos_v0.exam.reporting import (
    assemble_papers,
    batch_report,
    class_analytics,
    detailed_evaluation_csv,
    generate_reports,
    issues_csv,
    question_stats,
    read_results,
    render_analytics,
    student_report,
    student_results_csv,
)

from tests.test_orchestration import (
    make_task,
    pages_for,
    question,
)
from tests.test_batch import make_png


def two_q_exam():
    task = make_task(
        question("Q1", ["hold and wait", "circular wait"], marks=6),
        question("Q2", ["connection oriented", "reliable"], marks=4),
    )
    return task.exam


def run_two_q(paper_id, answers, *, with_identity=True):
    task = make_task(
        question("Q1", ["hold and wait", "circular wait"], marks=6),
        question("Q2", ["connection oriented", "reliable"], marks=4),
    )
    task = task.model_copy(update={
        "task_id": f"t-{paper_id}",
        "paper": task.paper.model_copy(update={"paper_id": paper_id}),
    })
    return ExamOrchestrator().execute(
        task, ocr_document=pages_for(answers, with_identity=with_identity)
    )


def result_jsonl(results, path):
    with open(path, "w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.model_dump(mode="json")) + "\n")


class CsvGenerationTests(unittest.TestCase):
    def setUp(self):
        self.exam = two_q_exam()
        self.clean = run_two_q("p7", [
            ("Q1. Explain", "hold and wait and circular wait"),
            ("Q2. What", "connection oriented and reliable"),
        ])
        self.dispute = run_two_q("p8", [
            ("Q1. Explain", "a process holds a resource and waits for another "
                            "while holding it, with cycles"),
            ("Q2. What", "connection oriented and reliable"),
        ])
        self.no_identity = run_two_q("p9", [
            ("Q1. Explain", "hold and wait and circular wait"),
            ("Q2. What", "connection oriented and reliable"),
        ], with_identity=False)

    def test_csv1_student_results_shape(self):
        records = assemble_papers([self.clean, self.no_identity], self.exam)
        text = student_results_csv(records, ["Q1", "Q2"])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0],
                         ["Roll No", "Student Name", "Q1", "Q2",
                          "Total", "Percentage", "Status"])
        by_roll = {r[0]: r for r in rows[1:]}
        clean_row = [r for r in rows[1:] if r[6] == "Evaluated"][0]
        self.assertEqual(clean_row[2], "6")
        self.assertEqual(clean_row[3], "4")
        self.assertEqual(clean_row[4], "10")
        self.assertEqual(clean_row[5], "100")
        self.assertEqual(clean_row[6], "Evaluated")
        review_rows = [r for r in rows[1:] if r[6] == "Review"]
        self.assertEqual(len(review_rows), 1)
        self.assertEqual(review_rows[0][0], "p9")

    def test_csv2_detailed_evaluation_shape(self):
        records = assemble_papers([self.dispute], self.exam)
        text = detailed_evaluation_csv(records)
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], ["Roll No", "Question", "Max Marks",
                                   "Agent 1", "Agent 2", "Final Marks",
                                   "Confidence", "Review Required"])
        q1 = [r for r in rows[1:] if r[1] == "Q1"][0]
        self.assertEqual(q1[2], "6")
        self.assertNotEqual(q1[3], "")
        self.assertEqual(q1[7], "true")

    def test_csv3_issues_with_severity_and_resolution(self):
        records = assemble_papers([self.dispute, self.no_identity], self.exam)
        text = issues_csv(records)
        rows = list(csv.reader(io.StringIO(text))) or []
        body = rows[1:]
        self.assertTrue(all(r[5] == "Human Review" for r in body))
        self.assertTrue(any(
            r[2] == "Q1" and r[3] == "Agent Disagreement" for r in body
        ), f"rows: {body}")

    def test_results_carry_per_question_evidence_for_csv2(self):
        row = [r for r in self.dispute.rows if r.question_id == "Q1"][0]
        self.assertEqual(len(row.agents), 2)
        self.assertEqual(row.agents["primary"]["agent"], "primary")
        self.assertTrue(row.needs_review)


class AssembleReviewOverlayTests(unittest.TestCase):
    def setUp(self):
        self.exam = two_q_exam()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def make_queued_result(self):
        return run_two_q("p8", [
            ("Q1. Explain", "a process holds a resource and waits for another "
                            "while holding it, with cycles"),
            ("Q2. What", "connection oriented and reliable"),
        ])

    def test_unresolved_row_keeps_paper_in_review(self):
        record = assemble_papers([self.make_queued_result()], self.exam)[0]
        self.assertEqual(record.status, "Review")
        q1 = [r for r in record.rows if r.question_id == "Q1"][0]
        self.assertEqual(q1.review_disposition, "pending")

    def test_modify_applies_resolved_final_marks(self):
        store = ReviewStore(self.dir)
        result = self.make_queued_result()
        cards = [ReviewItem(
            id=f"{result.paper_id}:Q1",
            paper_id=result.paper_id,
            question_id="Q1",
            proposed_marks=result.total_marks / 2,
            max_marks=6.0,
            proposed_confidence=0.6,
            review_reasons=["agent_disagreement"],
        )]
        store.enqueue(cards)
        store.resolve(f"{result.paper_id}:Q1", ReviewDecision.MODIFY,
                      final_marks=4.0, reviewer="ravi")
        record = assemble_papers([result], self.exam, store=store)[0]
        self.assertEqual(record.status, "Evaluated")
        q1 = [r for r in record.rows if r.question_id == "Q1"][0]
        self.assertEqual(q1.final_marks, 4.0)
        self.assertEqual(q1.review_disposition, "modified")

    def test_escalate_leaves_no_invented_mark(self):
        store = ReviewStore(self.dir)
        result = self.make_queued_result()
        store.enqueue([ReviewItem(
            id=f"{result.paper_id}:Q1",
            paper_id=result.paper_id,
            question_id="Q1",
            proposed_marks=result.total_marks / 2,
            max_marks=6.0,
            proposed_confidence=0.6,
            review_reasons=["agent_disagreement"],
        )])
        store.resolve(f"{result.paper_id}:Q1", ReviewDecision.ESCALATE,
                      reviewer="siren")
        record = assemble_papers([result], self.exam, store=store)[0]
        self.assertEqual(record.status, "Review")
        q1 = [r for r in record.rows if r.question_id == "Q1"][0]
        self.assertIsNone(q1.final_marks)


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.exam = two_q_exam()
        self.clean = run_two_q("p7", [
            ("Q1. Explain", "hold and wait and circular wait"),
            ("Q2. What", "connection oriented and reliable"),
        ])
        self.no_identity = run_two_q("p9", [
            ("Q1. Explain", "hold and wait and circular wait"),
            ("Q2. What", "connection oriented and reliable"),
        ], with_identity=False)

    def test_question_stats_and_class_metrics(self):
        records = assemble_papers([self.clean, self.no_identity], self.exam)
        stats = question_stats(records, ["Q1", "Q2"])
        self.assertEqual(stats[0].question_id, "Q1")
        self.assertEqual(stats[0].max_marks, 6.0)
        self.assertEqual(stats[0].mean, 6.0)
        self.assertEqual(stats[0].difficulty, 0.0)
        analytics = class_analytics(records, stats)
        self.assertEqual(analytics.total_students, 2)
        self.assertEqual(analytics.highest, analytics.lowest)
        self.assertEqual(analytics.highest, 100.0)
        self.assertEqual(analytics.review_rate, 0.5)
        self.assertEqual(analytics.agent_disagreement_rate, 0.0)
        dashboard = render_analytics(analytics)
        for needle in ("Total Students", "Q1", "Difficulty", "Review Rate"):
            self.assertIn(needle, dashboard)

    def test_ocr_failure_and_disagreement_rates(self):
        gray = run_two_q("p11", [("Q1. Explain", "hold and wait and circular wait")],
                         with_identity=False)
        gray = gray.model_copy(update={
            "review_reasons": ["low_ocr_confidence", "identity_uncertain"],
            "needs_review": True,
        })
        records = assemble_papers([self.clean, gray], self.exam)
        analytics = class_analytics(records, question_stats(records, ["Q1"]))
        self.assertEqual(analytics.ocr_failure_rate, 0.5)


class ReportTextTests(unittest.TestCase):
    def test_student_report_lists_satisfied_and_missing_concepts(self):
        result = run_two_q("p7", [
            ("Q1. Explain", "hold and wait and circular wait"),
            ("Q2. What", "connection oriented and reliable"),
        ])
        record = assemble_papers([result], two_q_exam())[0]
        text = student_report(record)
        for needle in ("Student:", "Roll No:", "Total:", "Q1:", "Q2:"):
            self.assertIn(needle, text)
        self.assertIn("satisfied", text)

    def test_batch_report_lists_every_roll(self):
        clean = run_two_q("p7", [
            ("Q1. Explain", "hold and wait and circular wait"),
            ("Q2. What", "connection oriented and reliable"),
        ])
        records = assemble_papers([clean], two_q_exam())
        text = batch_report(records, class_analytics(records, question_stats(records, [])))
        self.assertIn("42", text)


class EndToEndBatchToReportsTests(unittest.TestCase):
    """Real batch --results JSONL -> reporting CLI output files."""

    def test_generate_reports_over_a_real_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = tmp / "sheets"
            root.mkdir()
            make_png(root / "student001.png")
            res = tmp / "results.jsonl"
            exam = two_q_exam()
            run_batch(exam, root, config=BatchConfig(
                max_workers=1, retries=0, results_path=res))

            out = tmp / "out"
            manifest = generate_reports(exam, str(res), out_dir=str(out))
            self.assertEqual(manifest["reported"], 1)
            expected = ["student_results.csv", "detailed_evaluation.csv",
                        "issues.csv", "class_analytics.txt", "batch_report.txt",
                        "results.json"]
            for filename in expected:
                self.assertTrue((out / filename).exists())

            rows = list(csv.reader(open(out / "detailed_evaluation.csv")))
            self.assertEqual(rows[0][0], "Roll No")
            analysis = json.loads((out / "results.json").read_text())
            summary = json.loads((out / "results.json").read_text())["analytics"]
            self.assertEqual(summary["total_students"], 1)
            self.assertEqual(summary["review_rate"], 1.0)

    def test_read_results_skips_corrupt_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "results.jsonl"
            result = run_two_q("p7", [
                ("Q1. Explain", "hold and wait and circular wait"),
                ("Q2. What", "connection oriented and reliable"),
            ])
            res.write_text(
                "{not json}\n"
                + json.dumps(result.model_dump(mode="json"))
                + "\n[truncated\n"
            )
            loaded = read_results(str(res))
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].paper_id, "p7")


if __name__ == "__main__":
    unittest.main()