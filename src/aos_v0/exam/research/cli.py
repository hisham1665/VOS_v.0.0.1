"""Phase 13 CLI -- `python -m aos_v0.exam.research <exam.json> ...`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from aos_v0.exam.config import load_exam_configuration

from .corpus import run_corpus, write_corpus_json
from .experiments import (
    ExperimentResult,
    batch_throughput,
    experiment_a_single_vs_two_agent,
    experiment_b_exact_vs_semantic,
    experiment_c_fixed_vs_dynamic,
    experiment_d_no_recovery_vs_recovery,
    experiment_e_single_pass_vs_fallback_ocr,
    experiment_f_single_model_vs_routing,
)
from .ground_truth import GroundTruthEntry, build_dataset, read_dataset, write_dataset
from .report import write_benchmark_report, write_evaluation_csv, write_experiment_files


def _sample_pairs(exam) -> List[dict]:
    """One demonstration answer per question: full / partial / paraphrase /
    incorrect. The structural engines are token-exact, so a paraphrase that
    does not reuse the key tokens is scored honestly (usually partial or 0)."""
    pairs: List[dict] = []
    for question in exam.questions:
        concepts = (
            question.answer_key.expected_concepts
            if question.answer_key
            else []
        )
        full_answer = "; ".join(concepts) if concepts else "correct response"
        partial_answer = concepts[0] if concepts else "related"
        incorrect_answer = "no relevant content"
        paraphrase_answer = (
            "A restatement with different wording" if concepts else full_answer
        )
        pairs.extend(
            [
                {
                    "question_id": question.question_id,
                    "answer": full_answer,
                    "human_marks": question.max_marks,
                    "paraphrase": False,
                    "notes": "full-mark answer",
                },
                {
                    "question_id": question.question_id,
                    "answer": partial_answer,
                    "human_marks": (
                        question.max_marks / max(len(concepts), 1)
                        if concepts
                        else question.max_marks
                    ),
                    "paraphrase": False,
                    "notes": "partial answer",
                },
                {
                    "question_id": question.question_id,
                    "answer": paraphrase_answer,
                    "human_marks": question.max_marks,
                    "paraphrase": True,
                    "notes": "valid paraphrase of the key",
                },
                {
                    "question_id": question.question_id,
                    "answer": incorrect_answer,
                    "human_marks": 0.0,
                    "paraphrase": False,
                    "notes": "incorrect answer",
                },
            ]
        )
    return pairs


def research_cli_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aos_v0.exam.research",
        description="Phase 13 research evaluation harness (human vs AOS marks)",
    )
    parser.add_argument("exam", help="exam configuration JSON")
    parser.add_argument("--dataset", help="existing benchmark_dataset/ directory")
    parser.add_argument(
        "--sample-dataset", help="build a demo benchmark_dataset/ in this directory"
    )
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument(
        "--json", action="store_true", help="emit results.json"
    )
    parser.add_argument(
        "--throughput", type=int, default=0,
        help="measure batch throughput with this many synthetic papers (0=skip)",
    )
    args = parser.parse_args(argv)

    if args.dataset and args.sample_dataset:
        parser.error("pass either --dataset or --sample-dataset, not both")

    exam = load_exam_configuration(args.exam)

    if args.sample_dataset:
        entries = build_dataset(exam, _sample_pairs(exam))
        write_dataset(entries, args.sample_dataset)
        print(f"sample dataset written to {args.sample_dataset} "
              f"({len(entries)} entries)")
        dataset_dir = args.sample_dataset
    elif args.dataset:
        entries = read_dataset(args.dataset)
        dataset_dir = args.dataset
    else:
        parser.error("pass --dataset or --sample-dataset")

    if not entries:
        parser.error("empty dataset")

    print(f"running corpus over {len(entries)} ground-truth entries ...")
    report = run_corpus(entries)

    experiments: List[ExperimentResult] = [
        experiment_a_single_vs_two_agent(entries),
        experiment_b_exact_vs_semantic(entries),
        experiment_c_fixed_vs_dynamic(entries),
        experiment_d_no_recovery_vs_recovery(entries),
        experiment_e_single_pass_vs_fallback_ocr(entries),
        experiment_f_single_model_vs_routing(entries),
    ]

    throughput = None
    if args.throughput > 0:
        throughput = batch_throughput(exam, size=args.throughput)
        print(f"throughput: {throughput['papers_per_second']} papers/second "
              f"for {throughput['papers']} papers")

    os.makedirs(args.out, exist_ok=True)
    write_evaluation_csv(report, os.path.join(args.out, "evaluation_results.csv"))
    write_benchmark_report(
        report,
        os.path.join(args.out, "BENCHMARK_REPORT.md"),
        experiments,
        dataset_entry_count=len(entries),
        throughput=throughput,
    )
    write_experiment_files(
        experiments, os.path.join(args.out, "experiment_results")
    )
    if args.json:
        write_corpus_json(report, os.path.join(args.out, "research_results.json"))

    print("deliverables written to " + args.out)
    print("  " + os.path.join(args.out, "evaluation_results.csv"))
    print("  " + os.path.join(args.out, "BENCHMARK_REPORT.md"))
    print("  " + os.path.join(args.out, "experiment_results"))
    print(
        "metrics: "
        + json.dumps(
            {k: v for k, v in report.metrics.items() if k in (
                "entries", "evaluated", "mean_absolute_error", "exact_agreement"
            )}
        )
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return research_cli_main(argv)


if __name__ == "__main__":
    sys.exit(research_cli_main())