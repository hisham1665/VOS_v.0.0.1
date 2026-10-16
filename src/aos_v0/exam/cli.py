"""Friendly command line entry point for paper evaluation.

Examples::

    aos exam init --out exam.json
    aos exam validate exam.json
    aos exam evaluate exam.json scans/student_001.pdf --out data/outputs/student_001.json
    aos exam batch exam.json scans/ --checkpoint data/outputs/batch.json \
        --results data/outputs/results.jsonl
    aos exam report exam.json data/outputs/results.jsonl --out reports
    aos exam benchmark exam.json --sample-dataset benchmark_dataset --out research
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import collect_issues, dump_exam_configuration, load_exam_configuration, summarize
from .models import AnswerKey, ExamConfiguration, ExamEvaluationTask, PaperReference, Question, Rubric, RubricCriterion


def _template() -> ExamConfiguration:
    return ExamConfiguration(
        exam_id="EXAMPLE-001",
        title="Example examination",
        subject="Subject",
        questions=[
            Question(
                question_id="Q1",
                text="Explain the key concept.",
                max_marks=5,
                answer_key=AnswerKey(
                    reference_answers=["A correct explanation."],
                    expected_concepts=["key concept"],
                    accepted_alternatives=["valid equivalent explanation"],
                    keywords=["evidence"],
                ),
                rubric=Rubric(criteria=[RubricCriterion(criterion="explanation", marks=5)]),
            )
        ],
    )


def _load(path: str) -> ExamConfiguration:
    config = load_exam_configuration(path)
    # Kept as a helper so future config envelope versions can be supported in
    # one place without changing all subcommands.
    return config.exam if hasattr(config, "exam") else config


def _print_validation(config: ExamConfiguration) -> int:
    info = summarize(config)
    print(f"Exam: {info['title']} ({info['exam_id']}, v{info['version']})")
    print(f"  subject={info['subject'] or '-'} questions={info['questions']} total_marks={info['total_marks']}")
    issues = collect_issues(config)
    if not issues:
        print("validation: OK")
        return 0
    for issue in issues:
        print(issue.render())
    errors = [issue for issue in issues if issue.severity == "error"]
    print(f"validation: {len(issues)} issue(s), {len(errors)} error(s)")
    return 1 if errors else 0


def _evaluate(args) -> int:
    config = _load(args.exam)
    task = ExamEvaluationTask(
        task_id=args.task_id or f"paper-{Path(args.paper).stem}",
        paper=PaperReference(paper_id=args.paper_id or Path(args.paper).stem, file_path=args.paper),
        exam=config,
    )
    from .orchestrator import ExamOrchestrator

    result = ExamOrchestrator().execute(task, source=args.paper)
    payload = result.model_dump(mode="json")
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"evaluation written to {target}")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"status={result.status} marks={result.total_marks}/{result.max_marks} confidence={result.confidence:.3f}")
    return 0 if result.status in {"ok", "degraded"} else 2


def _batch(args) -> int:
    config = _load(args.exam)
    from .batch import BatchConfig, BatchRunner

    runner = BatchRunner(
        config,
        config=BatchConfig(
            max_workers=args.workers,
            retries=args.retries,
            checkpoint_path=Path(args.checkpoint) if args.checkpoint else None,
            results_path=Path(args.results) if args.results else None,
            timeout_seconds=args.timeout,
            force=args.force,
        ),
        log=print,
    )
    report = runner.status(args.answers) if args.status else runner.run(args.answers)
    print(json.dumps(report.to_plan_json(), indent=2, ensure_ascii=False))
    return 0


def _report(args) -> int:
    config = _load(args.exam)
    from .reporting.cli import generate_reports

    manifest = generate_reports(config, args.results, review_dir=args.review_dir, out_dir=args.out)
    print(json.dumps(manifest, indent=2))
    return 0


def _benchmark(args) -> int:
    config = _load(args.exam)
    from .research.cli import _sample_pairs
    from .research.ground_truth import build_dataset, write_dataset
    from .research.cli import research_cli_main

    dataset = args.dataset or args.sample_dataset
    if args.sample_dataset:
        write_dataset(build_dataset(config, _sample_pairs(config)), args.sample_dataset)
        dataset = args.sample_dataset
    if not dataset:
        raise SystemExit("benchmark requires --dataset or --sample-dataset")
    argv = [args.exam, "--dataset", dataset, "--out", args.out]
    if args.throughput:
        argv += ["--throughput", str(args.throughput)]
    return research_cli_main(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aos exam", description="Configure, run, review, and benchmark paper evaluation")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write a starter exam configuration")
    init.add_argument("--out", default="exam.json")
    init.set_defaults(func=lambda args: (dump_exam_configuration(_template(), args.out), print(f"starter exam written to {args.out}"), 0)[-1])

    validate = sub.add_parser("validate", help="validate exam JSON/YAML and report issues")
    validate.add_argument("exam")
    validate.set_defaults(func=lambda args: _print_validation(_load(args.exam)))

    evaluate = sub.add_parser("evaluate", help="evaluate one paper")
    evaluate.add_argument("exam")
    evaluate.add_argument("paper")
    evaluate.add_argument("--out")
    evaluate.add_argument("--paper-id")
    evaluate.add_argument("--task-id")
    evaluate.set_defaults(func=_evaluate)

    batch = sub.add_parser("batch", help="evaluate a folder, ZIP, or paper")
    batch.add_argument("exam")
    batch.add_argument("answers")
    batch.add_argument("--checkpoint")
    batch.add_argument("--results")
    batch.add_argument("--workers", type=int, default=4)
    batch.add_argument("--retries", type=int, default=1)
    batch.add_argument("--timeout", type=float)
    batch.add_argument("--force", action="store_true")
    batch.add_argument("--status", action="store_true")
    batch.set_defaults(func=_batch)

    report = sub.add_parser("report", help="generate CSV, JSON, and text reports")
    report.add_argument("exam")
    report.add_argument("results")
    report.add_argument("--out", default="reports")
    report.add_argument("--review-dir")
    report.set_defaults(func=_report)

    review = sub.add_parser("review", help="inspect or resolve the human review queue")
    review.add_argument("queue_dir")
    review.add_argument("review_args", nargs=argparse.REMAINDER,
                        help="review action, for example: list or resolve ID --decision accept")
    review.set_defaults(func=lambda args: _review(args))

    benchmark = sub.add_parser("benchmark", help="run the paper-evaluation research harness")
    benchmark.add_argument("exam")
    benchmark.add_argument("--dataset")
    benchmark.add_argument("--sample-dataset")
    benchmark.add_argument("--out", required=True)
    benchmark.add_argument("--throughput", type=int, default=0)
    benchmark.set_defaults(func=_benchmark)
    return parser


def _review(args) -> int:
    from .review.cli import review_cli_main

    return review_cli_main([args.queue_dir, *args.review_args])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
