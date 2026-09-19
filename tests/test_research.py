"""Phase 13 tests -- research evaluation harness (human vs AOS marks).

Covers the ground-truth dataset, the pure metrics, the corpus runner, the six
research experiments, the batch-throughput probe, the report deliverables and
the CLI. The golden rules are asserted too: AI marks stay inside [0, max],
reviewed rows carry their reasons, and no mark is invented.
"""

import csv
import os
import tempfile
import unittest
from pathlib import Path

from aos_v0.exam.models import AnswerKey, ExamConfiguration, Question
from aos_v0.exam.research import (
    run_corpus,
    build_dataset,
    write_dataset,
    read_dataset,
)
from aos_v0.exam.research.corpus import evaluate_entry
from aos_v0.exam.research.experiments import (
    batch_throughput,
    experiment_a_single_vs_two_agent,
    experiment_b_exact_vs_semantic,
    experiment_c_fixed_vs_dynamic,
    experiment_d_no_recovery_vs_recovery,
    experiment_e_single_pass_vs_fallback_ocr,
    experiment_f_single_model_vs_routing,
)
from aos_v0.exam.research.ground_truth import GroundTruthEntry
from aos_v0.exam.research.metrics import (
    agent_agreement,
    character_accuracy,
    exact_agreement,
    false_acceptance,
    false_rejection,
    mean_absolute_error,
    ocr_accuracy,
    partial_mark_agreement,
    reconciliation_success,
    selection_overhead,
    selection_quality,
    semantic_acceptance,
    tolerance_agreement,
    word_accuracy,
)
from aos_v0.exam.research.report import (
    write_benchmark_report,
    write_evaluation_csv,
    write_experiment_files,
)
from aos_v0.exam.research.cli import research_cli_main


def make_exam(concepts=("hold and wait", "circular wait"), marks=6.0):
    question = Question(
        question_id="Q1",
        text="Explain.",
        max_marks=marks,
        answer_key=AnswerKey(expected_concepts=list(concepts)),
        rubric={
            "criteria": [
                {"criterion": c.replace(" ", "_"), "marks": marks // len(concepts)}
                for c in concepts
            ]
        },
    )
    return ExamConfiguration(exam_id="E", title="Mid", questions=[question])


def full_answer() -> str:
    return "hold and wait and circular wait"


def partial_answer() -> str:
    return "hold and wait"


def paraphrase_answer() -> str:
    return "a restatement in entirely different words"


def incorrect_answer() -> str:
    return "no relevant content"


def demo_entries(exam=None):
    exam = exam or make_exam()
    return build_dataset(
        exam,
        [
            {"question_id": "Q1", "answer": full_answer(), "human_marks": 6.0},
            {"question_id": "Q1", "answer": partial_answer(), "human_marks": 3.0},
            {
                "question_id": "Q1",
                "answer": paraphrase_answer(),
                "human_marks": 6.0,
                "paraphrase": True,
            },
            {"question_id": "Q1", "answer": incorrect_answer(), "human_marks": 0.0},
        ],
    )


class GroundTruthTests(unittest.TestCase):
    def test_build_dataset_derives_question_contract(self):
        exam = make_exam()
        entries = demo_entries(exam)
        self.assertEqual(len(entries), 4)
        entry = entries[0]
        self.assertEqual(entry.question_text, "Explain.")
        self.assertEqual(entry.max_marks, 6.0)
        self.assertEqual(entry.expected_concepts, ["hold and wait", "circular wait"])
        self.assertTrue(entries[2].paraphrase)

    def test_round_trip_dataset_through_jsonl(self):
        with tempfile.TemporaryDirectory() as folder:
            entries = demo_entries()
            manifest = write_dataset(entries, folder)
            self.assertEqual(manifest["entry_count"], 4)
            self.assertEqual(manifest["question_count"], 1)
            reloaded = read_dataset(folder)
            self.assertEqual(len(reloaded), 4)
            self.assertEqual(reloaded[2].paraphrase, True)
            self.assertEqual(reloaded[0].human_marks, 6.0)

    def test_rejects_unknown_question_type(self):
        with self.assertRaises(ValueError):
            GroundTruthEntry(
                entry_id="x",
                question_id="Q1",
                question_type="bogus",
                student_answer="a",
                human_marks=1.0,
                max_marks=1.0,
            )


class MetricsTests(unittest.TestCase):
    def test_mean_absolute_error_and_agreement(self):
        humans = [6.0, 3.0, 0.0]
        ai = [6.0, 4.0, 0.0]
        self.assertEqual(mean_absolute_error(humans, ai), 1 / 3)
        self.assertAlmostEqual(exact_agreement(humans, ai), 2 / 3)
        tol = tolerance_agreement(humans, ai)
        self.assertEqual(tol["0.5"], 2 / 3)
        self.assertEqual(tol["1.0"], 1.0)
        self.assertEqual(tolerance_agreement([], []), {"0.5": 0.0, "1.0": 0.0})

    def test_false_rejection_and_acceptance(self):
        hit, rate = false_rejection([6.0, 3.0], [0.0, 3.0])
        self.assertEqual((hit, rate), (1, 0.5))
        hit, rate = false_acceptance([0.0, 3.0], [1.0, 3.0])
        self.assertEqual((hit, rate), (1, 1.0))

    def test_semantic_acceptance_only_counts_full_marks(self):
        hit, rate = semantic_acceptance([6.0, 6.0], [6.0, 3.0], [6.0, 6.0])
        self.assertEqual((hit, rate), (1, 0.5))

    def test_partial_mark_agreement(self):
        hits, rate = partial_mark_agreement([3.0, 3.5], [3.0, 1.0])
        self.assertEqual(hits, 1)
        self.assertEqual(rate, 0.5)

    def test_ocr_accuracy(self):
        self.assertEqual(character_accuracy("abc", "abc"), 1.0)
        self.assertEqual(word_accuracy("the cat sat", "The cat sat"), 1.0)
        self.assertAlmostEqual(word_accuracy("cat sat", "the cat sat"), 2 / 3)
        self.assertEqual(ocr_accuracy("", "truth", "word"), 0.0)

    def test_agent_and_reconciliation_metrics(self):
        hit, rate = agent_agreement([6.0, 4.5], [6.0, 5.0])
        self.assertEqual((hit, rate), (1, 0.5))
        rows = [
            {"agents": {"primary": {"marks": 6}, "verifier": {"marks": 6}}, "needs_review": False},
            {"agents": {"primary": {"marks": 6}, "verifier": {"marks": 2}}, "needs_review": True},
        ]
        self.assertEqual(reconciliation_success(rows), 1.0)

    def test_selection_quality_and_overhead(self):
        selections = [
            {"flags": "x", "satisfies": True, "available": True, "resource": "r1"},
            {"flags": "x", "satisfies": False, "available": True, "resource": "r2"},
        ]
        self.assertEqual(selection_quality(selections), 0.5)
        overhead = selection_overhead(selections, 2)
        self.assertEqual(overhead["selects"], 2)
        self.assertEqual(overhead["nodes"], 2)


class CorpusTests(unittest.TestCase):
    def test_corpus_metrics_and_golden_rules(self):
        report = run_corpus(demo_entries())
        self.assertEqual(len(report.entries), 4)
        for entry in report.entries:
            self.assertIsNotNone(entry.ai_marks)
            self.assertGreaterEqual(entry.ai_marks, 0.0)
            self.assertLessEqual(entry.ai_marks, entry.max_marks)
        metrics = report.metrics
        self.assertEqual(metrics["entries"], 4)
        self.assertIn("mean_absolute_error", metrics)
        self.assertIn("tolerance_agreement", metrics)
        self.assertIn("reconciliation_success", metrics)

    def test_reviewed_row_carries_reasons_not_invented_marks(self):
        entry = [e for e in demo_entries() if e.paraphrase][0]
        result = evaluate_entry(entry)
        self.assertTrue(result.needs_review)
        self.assertIn("low_evaluation_confidence", result.review_reasons)
        self.assertIsNotNone(result.ai_marks)
        self.assertEqual(result.ai_marks, 0.0)


class ExperimentTests(unittest.TestCase):
    def test_a_single_vs_two_agent(self):
        result = experiment_a_single_vs_two_agent(demo_entries())
        self.assertEqual(result.name, "A")
        self.assertEqual(
            result.summary["single"]["mae"], result.summary["two_agent"]["mae"]
        )
        self.assertEqual(
            result.summary["verifier_coverage"][0], result.summary["verifier_coverage"][1]
        )

    def test_b_exact_vs_semantic(self):
        result = experiment_b_exact_vs_semantic(demo_entries())
        exact = result.summary["exact_token"]
        semantic = result.summary["semantic"]
        self.assertEqual(semantic["mae"], 1.875)
        self.assertEqual(exact["mae"], 1.5)
        self.assertEqual(semantic["exact"], 0.5)
        self.assertEqual(exact["exact"], 0.75)
        self.assertEqual(len(result.rows), 4)
        self.assertEqual(result.rows[0]["exact_token"], 6.0)
        self.assertEqual(result.rows[2]["exact_token"], 0.0)

    def test_c_fixed_vs_dynamic_selection_quality_perfect_locally(self):
        result = experiment_c_fixed_vs_dynamic(demo_entries())
        self.assertEqual(result.summary["selection_quality"], 1.0)
        self.assertEqual(
            result.summary["selection_overhead"]["selects"],
            result.summary["dynamic_nodes"],
        )

    def test_d_recovery_vs_no_recovery(self):
        result = experiment_d_no_recovery_vs_recovery(demo_entries())
        enabled = result.summary["recovery_enabled"]
        disabled = result.summary["recovery_disabled"]
        self.assertGreater(enabled["papers_recovered"], 0)
        self.assertEqual(disabled["papers_ok"], 0)
        self.assertGreaterEqual(enabled["total"], disabled["total"])

    def test_e_single_pass_vs_fallback(self):
        result = experiment_e_single_pass_vs_fallback_ocr(demo_entries())
        self.assertEqual(result.summary["single_pass_ok"], result.summary["fallback_ok"])
        self.assertEqual(result.summary["papers"], 4)

    def test_f_single_model_vs_routing(self):
        result = experiment_f_single_model_vs_routing(demo_entries())
        self.assertEqual(result.summary["selection_quality"], 1.0)
        self.assertEqual(result.summary["resources_per_capability"]["document.ocr"], 1)

    def test_batch_throughput_probe(self):
        result = batch_throughput(make_exam(), size=3)
        self.assertEqual(result["processed"], 3)
        self.assertEqual(result["results_rows"], 3)
        self.assertGreater(result["papers_per_second"], 0.0)


class DeliverableTests(unittest.TestCase):
    def test_evaluation_csv_shape(self):
        with tempfile.TemporaryDirectory() as folder:
            report = run_corpus(demo_entries())
            path = os.path.join(folder, "evaluation_results.csv")
            write_evaluation_csv(report, path)
            with open(path, newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                header = reader.fieldnames
                rows = list(reader)
            self.assertIn("entry_id", header)
            self.assertIn("human_marks", header)
            self.assertIn("ai_marks", header)
            self.assertIn("absolute_error", header)
            self.assertEqual(len(rows), 4)
            ai = float(rows[0]["ai_marks"])
            human = float(rows[0]["human_marks"])
            self.assertEqual(rows[0]["exact_agreement"], "True")
            if ai != human:
                self.assertEqual(rows[0]["tol_1_0"], "False")

    def test_benchmark_report_and_experiment_files(self):
        with tempfile.TemporaryDirectory() as folder:
            entries = demo_entries()
            report = run_corpus(entries)
            experiments = [
                experiment_a_single_vs_two_agent(entries),
                experiment_b_exact_vs_semantic(entries),
            ]
            md = os.path.join(folder, "BENCHMARK_REPORT.md")
            write_benchmark_report(
                report, md, experiments, dataset_entry_count=len(entries)
            )
            text = Path(md).read_text(encoding="utf-8")
            self.assertIn("# AOS Exam Evaluator -- Research Benchmark Report", text)
            self.assertIn("Experiment A", text)
            self.assertIn("Experiment B", text)
            files = write_experiment_files(
                experiments, os.path.join(folder, "experiment_results")
            )
            self.assertEqual(len(files), 2)
            self.assertTrue(Path(files[0]).exists())


class ResearchCliTests(unittest.TestCase):
    def test_cli_round_trip_with_sample_dataset(self):
        import shutil

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            exam_json = root / "exam.json"
            repo = Path(__file__).resolve().parent.parent
            shutil.copy(
                repo / "examples" / "sample_exam.json",
                exam_json,
            )
            rc = research_cli_main(
                [
                    str(exam_json),
                    "--sample-dataset",
                    str(root / "benchmark_dataset"),
                    "--out",
                    str(root / "out"),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue((root / "benchmark_dataset" / "entries.jsonl").exists())
            self.assertTrue((root / "out" / "evaluation_results.csv").exists())
            self.assertTrue((root / "out" / "BENCHMARK_REPORT.md").exists())
            self.assertTrue((root / "out" / "experiment_results" / "a.txt").exists())


if __name__ == "__main__":
    unittest.main()