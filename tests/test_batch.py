"""Phase 10 tests -- mass paper evaluation (the batch driver).

Covers the deliverables: file discovery + validation (folder / ZIP / single
sheet, junk and unreadable members rejected, natural ordering); the parallel
worker system with failure isolation (one failing paper never aborts or
restarts the batch); retry accounting; the durable checkpoint + resume
mechanism (completed papers are never re-graded); review routing of uncertain
papers; and the per-document status API. All batch state lives in the driver --
the kernel never sees the batch.
"""

import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from aos_v0.exam.batch import (
    BatchConfig,
    BatchItem,
    BatchReport,
    BatchRunner,
    ItemStatus,
    batch_cli_main,
    discover_answer_sheets,
    load_checkpoint,
)
from aos_v0.exam.batch.driver import ProcessedOutcome, run_batch
from aos_v0.exam.models import AnswerKey, ExamConfiguration, Question


def make_exam():
    return ExamConfiguration(
        exam_id="E",
        title="Mid",
        questions=[
            Question(
                question_id="Q1",
                text="Explain X.",
                max_marks=4,
                answer_key=AnswerKey(expected_concepts=["hold and wait"]),
                rubric={"criteria": [{"criterion": "hold_wait", "marks": 4}]},
            )
        ],
    )


def make_png(path: Path, dark: bool = False) -> None:
    tone = 60 if dark else 250
    Image.new("RGB", (400, 300), (tone, tone, tone)).save(path)


def done(outcome_status: str = "completed", **kwargs):
    return ProcessedOutcome(
        status=outcome_status,
        total_marks=kwargs.get("total_marks", 4.0),
        max_marks=kwargs.get("max_marks", 4.0),
        confidence=kwargs.get("confidence", 0.85),
        student=kwargs.get("student", {"roll_no": "NA"}),
        review_reasons=kwargs.get("review_reasons", []),
    )


class DiscoveryTests(unittest.TestCase):
    def test_folder_finds_sorted_valid_sheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_png(root / "student003.png")
            make_png(root / "student010.png")
            make_png(root / "student002.png")
            (root / "notes.txt").write_text("skip me")
            (root / "broken.png").write_bytes(b"definitely not an image")
            (root / "__MACOSX" / "student009.pdf").parent.mkdir(parents=True)
            (root / "__MACOSX" / "student009.pdf").write_bytes(b"%PDF-1.4 hidden")
            items = discover_answer_sheets(root)
            self.assertEqual(
                [i.paper_id for i in items],
                ["student002", "student003", "student010"],
            )
            self.assertTrue(all(i.status == ItemStatus.PENDING for i in items))

    def test_zip_filters_junk_and_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_png(root / "src.png")
            archive = root / "answers.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("student001.png", (root / "src.png").read_bytes())
                zf.writestr("student004.pdf", b"%PDF-1.4 dummy")
                zf.writestr("__MACOSX/._student001.png", "junk")
                zf.writestr("README.txt", "skip")
            items = discover_answer_sheets(archive)
            self.assertEqual([i.paper_id for i in items],
                             ["student001", "student004"])

    def test_single_file_is_one_paper_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "only.pdf"
            path.write_bytes(b"%PDF-1.4 dummy")
            items = discover_answer_sheets(path)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].paper_id, "only")

    def test_missing_input_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                discover_answer_sheets(Path(tmp) / "nope")


class IsolationAndParallelTests(unittest.TestCase):
    def setUp(self):
        self.exam = make_exam()

    def test_failure_isolation_never_aborts_the_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("student001", "student002", "student003"):
                make_png(root / f"{name}.png")
            fail = {"student002"}
            lock = threading.Lock()
            calls = []

            def process(item: BatchItem):
                with lock:
                    calls.append(item.paper_id)
                if item.paper_id in fail:
                    raise RuntimeError("sheet unreadable")
                return done()

            report = BatchRunner(
                self.exam, config=BatchConfig(max_workers=3, retries=0),
                process=process,
            ).run(root)
            self.assertEqual(report.totals["completed"], 2)
            self.assertEqual(report.totals["failed"], 1)
            self.assertEqual(sorted(calls),
                             ["student001", "student002", "student003"])
            failed = [i for i in report.items
                      if i.paper_id == "student002"][0]
            self.assertIn("RuntimeError", failed.error)

    def test_all_workers_touched_for_a_big_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(1, 9):
                make_png(root / f"p{i:03d}.png")
            report = BatchRunner(
                self.exam, config=BatchConfig(max_workers=4, retries=0),
                process=lambda item: done(),
            ).run(root)
            self.assertEqual(report.totals["completed"], 8)

    def test_retry_then_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "student001.png"
            make_png(path)
            counts = {"n": 0}

            def flaky(item: BatchItem):
                counts["n"] += 1
                if counts["n"] < 3:
                    raise RuntimeError("transient outage")
                return done(confidence=0.9)

            report = BatchRunner(
                self.exam, config=BatchConfig(max_workers=1, retries=2),
                process=flaky,
            ).run(path)
            self.assertEqual(counts["n"], 3)
            item = report.items[0]
            self.assertEqual(item.status, ItemStatus.COMPLETED)
            self.assertEqual(item.attempts, 3)

    def test_retry_exhausted_marks_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "student001.png"
            make_png(path)

            def always_down(item: BatchItem):
                raise RuntimeError("down")

            report = BatchRunner(
                self.exam, config=BatchConfig(max_workers=1, retries=2),
                process=always_down,
            ).run(path)
            item = report.items[0]
            self.assertEqual(item.status, ItemStatus.FAILED)
            self.assertEqual(item.attempts, 3)
            self.assertIn("RuntimeError", item.error)


class CheckpointResumeTests(unittest.TestCase):
    def setUp(self):
        self.exam = make_exam()

    def test_resume_never_regrades_completed_papers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(1, 4):
                make_png(root / f"paper{i:03d}.png")
            checkpoint = Path(tmp) / "ck.json"
            calls = []

            def process(item: BatchItem):
                calls.append(item.paper_id)
                return done()

            runner = BatchRunner(
                self.exam, config=BatchConfig(
                    max_workers=2, retries=0, checkpoint_path=checkpoint
                ),
                process=process,
            )
            first = runner.run(root)
            self.assertEqual(first.totals["completed"], 3)

            calls.clear()
            second = runner.run(root)  # resume
            self.assertEqual(second.totals["completed"], 3)
            self.assertEqual(calls, [], "completed papers must not be re-graded")
            self.assertTrue(checkpoint.exists())

    def test_checkpoint_file_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_png(root / "paper001.png")
            checkpoint = Path(tmp) / "ck.json"
            BatchRunner(
                self.exam, config=BatchConfig(
                    max_workers=1, retries=1, checkpoint_path=checkpoint
                ),
                process=lambda item: done(),
            ).run(root)
            loaded = load_checkpoint(checkpoint)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.items[0].paper_id, "paper001")
            self.assertEqual(loaded.items[0].status, ItemStatus.COMPLETED)

    def test_force_reprocesses_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper001.png"
            make_png(path)
            checkpoint = Path(tmp) / "ck.json"
            calls = []

            def process(item: BatchItem):
                calls.append(item.paper_id)
                return done()

            config = BatchConfig(
                max_workers=1, retries=0, checkpoint_path=checkpoint
            )
            BatchRunner(self.exam, config=config, process=process).run(path)
            calls.clear()
            BatchRunner(
                self.exam, config=config.model_copy(update={"force": True}),
                process=process,
            ).run(path)
            self.assertEqual(calls, ["paper001"])


class StatusApiTests(unittest.TestCase):
    def setUp(self):
        self.exam = make_exam()

    def test_review_routing_is_per_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_png(root / "good.png")
            make_png(root / "grey.png")

            def process(item: BatchItem):
                if item.paper_id == "grey":
                    return done("review", review_reasons=["low_ocr_confidence"])
                return done()

            report = run_batch(
                self.exam, root, config=BatchConfig(max_workers=2, retries=0),
                process=process,
            )
            self.assertIsInstance(report, BatchReport)
            self.assertEqual(report.totals["completed"], 1)
            self.assertEqual(report.totals["review"], 1)
            self.assertEqual(report.totals["failed"], 0)
            self.assertEqual(report.progress, 1.0)
            grey = [i for i in report.items if i.paper_id == "grey"][0]
            self.assertEqual(grey.status, ItemStatus.REVIEW)
            self.assertEqual(grey.review_reasons, ["low_ocr_confidence"])
            plan = report.to_plan_json()
            self.assertIn("totals", plan)
            self.assertIn("items", plan)
            self.assertEqual(plan["items"][0]["status"], "completed")

    def test_no_answer_sheets_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                run_batch(make_exam(), Path(tmp), config=BatchConfig())


class ZipInputTests(unittest.TestCase):
    def test_zip_members_are_materialized_for_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_png(root / "src.png")
            archive = root / "answers.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("student1.png", (root / "src.png").read_bytes())
                zf.writestr("student2.png", (root / "src.png").read_bytes())
            seen = []

            def process(item: BatchItem):
                seen.append(item.source)
                self.assertTrue(Path(item.source).is_file())
                return done()

            report = run_batch(
                make_exam(), archive, config=BatchConfig(max_workers=2, retries=0),
                process=process,
            )
            self.assertEqual(report.totals["completed"], 2)
            self.assertEqual(len(seen), 2)
            for source in seen:
                self.assertEqual(Path(source).name.split(".")[0][:7], "student")


class CliSmokeTests(unittest.TestCase):
    def test_cli_runs_a_batch_and_prints_json(self):
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_png(root / "student001.png")
            config_path = Path(tmp) / "exam.json"
            config_path.write_text(
                '{"exam_id": "E", "title": "Mid", "questions": [{'
                '"question_id": "Q1", "text": "x", "max_marks": 4, '
                '"answer_key": {"expected_concepts": ["hold and wait"]}, '
                '"rubric": {"criteria": [{"criterion": "hold_wait", "marks": 4}]}'
                "}]}"
            )
            out = io.StringIO()
            with redirect_stdout(out):
                code = batch_cli_main([
                    str(config_path), str(root), "--workers", "1",
                    "--retries", "0", "--json",
                ])
            self.assertEqual(code, 0)
            self.assertIn("student001", out.getvalue())
            self.assertIn("totals", out.getvalue())


if __name__ == "__main__":
    unittest.main()