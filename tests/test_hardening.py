"""Phase 14 -- Production hardening: security, health, provenance, perf,
readiness. Every check runs against the real kernel artifacts (a small real
batch), never against stubs.
"""

import json
import unittest
from pathlib import Path

from aos_v0.exam.health import healthcheck, render_health_table
from aos_v0.exam.perf import run_performance_probe
from aos_v0.exam.provenance import build_provenance, provenance_fingerprint
from aos_v0.exam.readiness import verify_production_readiness
from aos_v0.exam.security import (
    AccessEvent,
    AccessLog,
    RBAC_RULE,
    ROLE_ADMIN,
    ROLE_EXAMINER,
    ROLE_REVIEWER,
    authorize,
    validate_exam_payload,
    validate_paper_source,
)
from aos_v0.exam.security.policy import actions_for
from aos_v0.exam.batch import BatchConfig, run_batch

from tests.test_orchestration import make_task, pages_for, question


def _exam():
    task = make_task(
        question("Q1", ["hold and wait", "circular wait"], marks=6),
        question("Q2", ["connection oriented", "reliable"], marks=4),
    )
    return task.exam


class SecurityTests(unittest.TestCase):
    def test_validate_payload_accepts_good_config(self):
        issues = validate_exam_payload({
            "exam_id": "EX-1",
            "questions": [
                {"question_id": "Q1", "max_marks": 6},
                {"question_id": "Q2", "max_marks": 4},
            ],
        })
        self.assertEqual([i for i in issues if i.error], [])

    def test_validate_payload_flags_zero_max_and_duplicate_ids(self):
        issues = validate_exam_payload({
            "exam_id": "EX-1",
            "questions": [
                {"question_id": "Q1", "max_marks": 0},
                {"question_id": "Q1", "max_marks": 4},
            ],
        })
        codes = {i.code for i in issues if i.error}
        self.assertIn("max_marks", codes)
        self.assertIn("duplicate_question_id", codes)

    def test_validate_payload_rejects_missing_questions(self):
        issues = validate_exam_payload({"exam_id": "EX-1"})
        self.assertTrue(any(i.code == "questions" and i.error for i in issues))
        self.assertTrue(any(i.code == "missing" and i.error for i in issues))

    def test_validate_paper_source_missing_and_extension(self):
        missing = validate_paper_source("/does/not/exist.pdf")
        self.assertTrue(missing[0].error if missing else False)
        self.assertEqual(missing[0].code if missing else "", "missing")

    def test_access_log_append_only_and_counts(self):
        tmpdir = self._tmpdir()
        log = AccessLog(str(tmpdir))
        log.record(actor="alice", action="exam.batch.run", outcome="allowed")
        log.record(actor="bob", action="review.resolve", outcome="denied")
        self.assertEqual(log.counts()["events"], 2)
        self.assertEqual(log.counts()["allowed"], 1)
        self.assertEqual(log.counts()["denied"], 1)

    def test_access_log_records_are_events(self):
        tmpdir = self._tmpdir()
        log = AccessLog(str(tmpdir))
        event = log.record(actor="alice", action="exam.batch.run", detail="x")
        self.assertIsInstance(event, AccessEvent)
        self.assertEqual(log.read()[0].actor, "alice")

    def test_rbac_matrix_roles(self):
        self.assertIn(ROLE_EXAMINER, RBAC_RULE)
        self.assertIn(ROLE_REVIEWER, RBAC_RULE)
        self.assertIn(ROLE_ADMIN, RBAC_RULE)
        self.assertTrue(authorize(ROLE_REVIEWER, "review.resolve"))
        self.assertFalse(authorize(ROLE_REVIEWER, "exam.batch.run"))
        self.assertTrue(authorize(ROLE_ADMIN, "exam.batch.run"))

    def test_actions_for_unknown_role_empty(self):
        self.assertEqual(actions_for("root"), [])

    def _tmpdir(self):
        import tempfile

        return Path(tempfile.mkdtemp())


class HealthTests(unittest.TestCase):
    def test_healthcheck_overall_healthy_local(self):
        report = healthcheck(review_dir=str(self._tmpdir()))
        self.assertIn(report.overall, ("healthy",))
        self.assertGreater(len(report.resources), 0)
        self.assertTrue(all(r.healthy for r in report.resources))

    def test_healthcheck_default_storage_writable(self):
        report = healthcheck()
        self.assertTrue(report.storage_writable)

    def test_health_render_table_has_ids(self):
        report = healthcheck()
        table = render_health_table(report)
        self.assertIn(report.resources[0].resource_id, table)

    def _tmpdir(self):
        import tempfile

        return Path(tempfile.mkdtemp())


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        from aos_v0.exam.orchestrator import ExamOrchestrator

        task = make_task(
            question("Q1", ["hold and wait", "circular wait"], marks=6),
            question("Q2", ["connection oriented", "reliable"], marks=4),
        )
        task = task.model_copy(update={
            "paper": task.paper.model_copy(update={"paper_id": "p-prov"}),
        })
        self.result = ExamOrchestrator().execute(
            task, ocr_document=pages_for([
                ("Q1. Explain", "hold and wait and circular wait"),
                ("Q2. What", "connection oriented and reliable"),
            ])
        )
        self.exam = self.result  # any object with questions; but records need exam
        import copy
        self.exam = _exam()

    def test_provenance_captures_plan_fields(self):
        exam = _exam()
        record = build_provenance(exam, self.result)
        self.assertEqual(record.exam_id, exam.exam_id)
        self.assertEqual(record.paper_id, "p-prov")
        self.assertTrue(record.fingerprint)
        self.assertTrue(record.capability_dna)  # from traced capabilities
        self.assertNotEqual(record.answer_key_version, record.rubric_version)
        self.assertTrue(record.models)

    def test_provenance_fingerprint_stable(self):
        exam = _exam()
        first = build_provenance(exam, self.result)
        second = build_provenance(exam, self.result)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            provenance_fingerprint(first), first.fingerprint
        )

    def test_provenance_verify_against_same_exam_passes(self):
        from aos_v0.exam.provenance import verify_provenance

        exam = _exam()
        record = build_provenance(exam, self.result)
        result = verify_provenance(record, exam)
        self.assertTrue(all(item["matches"] for item in result.values()))


class PerfTests(unittest.TestCase):
    def test_probe_single_small_scale_runs_real_batch(self):
        import tempfile

        out = Path(tempfile.mkdtemp())
        exam = _exam()
        rows = run_performance_probe(exam, str(out), scales=(2,))
        csv_path = out / "performance_results.csv"
        self.assertTrue(csv_path.exists())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.scale, 2)
        self.assertEqual(row.papers, 2)
        self.assertGreaterEqual(row.papers_per_second, 0.0)
        self.assertEqual(row.results_rows, row.papers)
        self.assertLessEqual(row.avg_paper_seconds, 600)  # sane bound

    def test_probe_writes_csv_header(self):
        import tempfile

        out = Path(tempfile.mkdtemp())
        _ = run_performance_probe(_exam(), str(out), scales=(2,))
        header = (out / "performance_results.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("scale", header)
        self.assertIn("failure_rate", header)


class ReadinessTests(unittest.TestCase):
    def test_readiness_all_passes_on_clean_batch(self):
        import tempfile

        results_path = self._run_clean_batch()
        report = verify_production_readiness(str(results_path))
        self.assertEqual(report.overall, "ready", report.model_dump(mode="json"))
        self.assertTrue(all(c.passed for c in report.checks))

    def _real_process_factory(self, exam):
        """Real pipeline process fn: the actual orchestrator (real kernel DAG,
        real agents, real trace) with a genuine OCR document supplied through
        the injected transport -- the same pattern the rest of the suite uses.
        """
        from aos_v0.exam.batch.driver import ProcessedOutcome
        from aos_v0.exam.models import (
            EvaluationSettings,
            ExamEvaluationTask,
            PaperReference,
        )
        from aos_v0.exam.orchestrator import ExamOrchestrator

        def process(item):
            task = ExamEvaluationTask(
                task_id=f"batch-{item.paper_id}",
                paper=PaperReference(paper_id=item.paper_id, file_path=item.source),
                exam=exam,
                settings=EvaluationSettings(),
            )
            result = ExamOrchestrator().execute(
                task,
                source=item.source,
                ocr_document=pages_for([
                    ("Q1. Explain", "hold and wait and circular wait"),
                    ("Q2. What", "connection oriented and reliable"),
                ]),
            )
            return ProcessedOutcome(
                status="completed" if result.status == "ok" else "review",
                review_reasons=list(result.review_reasons),
                total_marks=result.total_marks,
                max_marks=result.max_marks,
                confidence=result.confidence,
                student=dict(result.student),
                result=result,
            )

        return process

    def test_readiness_flags_auto_zero(self):
        """A result with a zeroed unreadable row and no review flag fails."""
        from aos_v0.exam.orchestrator import ExamOrchestrator
        from aos_v0.exam.readiness import ReadinessItem

        import tempfile
        from pathlib import Path

        out = Path(tempfile.mkdtemp())
        results_path = out / "results.jsonl"
        # craft a tampered result with an auto-zero row
        task = make_task(
            question("Q1", ["hold and wait", "circular wait"], marks=6),
        )
        result = ExamOrchestrator().execute(
            task, ocr_document=pages_for([("Q1. Explain", "hold and wait circular wait")])
        )
        row = result.rows[0]
        result = result.model_copy(update={
            "rows": [
                row.model_copy(update={
                    "marks": 0.0,
                    "answer_text": "",
                    "extraction_confidence": 0.2,
                })
            ],
            "needs_review": False,
        })
        with open(results_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(result.model_dump(mode="json")) + "\n")
        report = verify_production_readiness(str(results_path))
        self.assertEqual(report.overall, "not_ready")
        auto_zero = [c for c in report.checks if c.requirement == "no automatic zero"]
        self.assertTrue(auto_zero and not auto_zero[0].passed)

    def test_readiness_flags_unevidenced_mark(self):
        import tempfile
        from pathlib import Path

        out = Path(tempfile.mkdtemp())
        results_path = out / "results.jsonl"
        task = make_task(
            question("Q1", ["hold and wait", "circular wait"], marks=6),
        )
        from aos_v0.exam.orchestrator import ExamOrchestrator

        result = ExamOrchestrator().execute(
            task, ocr_document=pages_for([("Q1. Explain", "hold and wait circular wait")])
        )
        row = result.rows[0]
        result = result.model_copy(update={
            "rows": [
                row.model_copy(update={
                    "marks": 5.0,
                    "agents": {},
                    "adopted_from": "none",
                })
            ],
        })
        with open(results_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(result.model_dump(mode="json")) + "\n")
        report = verify_production_readiness(str(results_path))
        self.assertEqual(report.overall, "not_ready")
        evidence = [c for c in report.checks if "evidence" in c.requirement]
        self.assertTrue(evidence and not evidence[0].passed)

    def _run_clean_batch(self):
        import tempfile

        from aos_v0.exam.batch import BatchConfig, run_batch
        from tests.test_batch import make_png

        tmp = Path(tempfile.mkdtemp())
        answers = tmp / "answers"
        answers.mkdir()
        make_png(answers / "p1_submit.png")
        make_png(answers / "p2_submit.png")
        results_path = tmp / "results.jsonl"
        exam = _exam()
        # Pin the exam configuration next to the results so the readiness
        # checker can re-derive provenance fingerprints from the same config.
        (tmp / "exam_config.json").write_text(
            json.dumps(exam.model_dump(mode="json")), encoding="utf-8"
        )
        report = run_batch(
            exam,
            answers,
            config=BatchConfig(max_workers=1, retries=0, results_path=results_path),
            process=self._real_process_factory(exam),
        )
        self.assertEqual(report.totals["completed"], 2)
        return results_path


if __name__ == "__main__":
    unittest.main()