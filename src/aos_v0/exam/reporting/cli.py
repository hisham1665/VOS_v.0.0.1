"""CLI for the report / analytics suite (Phase 12 deliverable).

Usage::

    python -m aos_v0.exam.reporting <exam.json> <results.jsonl> \\
            [--out DIR] [--review-dir <store-dir>] [--json-analysis]

Inputs: a Phase-2 exam configuration and the batch driver's append-only
results JSONL (``--results`` of ``python -m aos_v0.exam.batch``). The option
``--review-dir`` points at a Phase-11 review store so resolved cards replace
proposed marks and unresolved papers stay flagged ``Review``.
"""

from __future__ import annotations

import argparse
import os
from typing import List, Optional

from .analytics import (
    batch_report,
    class_analytics,
    question_stats,
    render_analytics,
    student_report,
)
from .assemble import assemble_papers, read_results
from .csv import (
    detailed_evaluation_csv,
    issues_csv,
    student_results_csv,
)
from .export import export_json


def _write(directory: str, filename: str, text: str) -> None:
    path = os.path.join(directory, filename)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def generate_reports(
    exam,
    results_path: str,
    *,
    review_dir: Optional[str] = None,
    out_dir: str,
) -> dict:
    """Generate the full Phase 12 report set; returns a manifest dict."""
    store = None
    if review_dir is not None:
        from aos_v0.exam.review import ReviewStore

        store = ReviewStore(review_dir)

    results = read_results(results_path)
    question_ids = [q.question_id for q in exam.questions]
    records = assemble_papers(results, exam, store=store)
    stats = question_stats(records, question_ids)
    analytics = class_analytics(records, stats)

    os.makedirs(out_dir, exist_ok=True)
    _write(out_dir, "student_results.csv", student_results_csv(records, question_ids))
    _write(out_dir, "detailed_evaluation.csv", detailed_evaluation_csv(records))
    _write(out_dir, "issues.csv", issues_csv(records))
    _write(out_dir, "class_analytics.txt", render_analytics(analytics))
    _write(out_dir, "batch_report.txt", batch_report(records, analytics))
    for record in records:
        _write(
            out_dir,
            f"reports/{record.roll_no}.txt",
            student_report(record) + "\n",
        )
    export_json(
        records,
        analytics,
        os.path.join(out_dir, "results.json"),
        exam_title=exam.title,
    )
    return {
        "out_dir": out_dir,
        "candidates": len(results),
        "reported": len(records),
        "files": [
            "student_results.csv",
            "detailed_evaluation.csv",
            "issues.csv",
            "class_analytics.txt",
            "batch_report.txt",
            "results.json",
        ],
    }


def report_cli_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aos_v0.exam.reporting",
        description="CSV / JSON / report / analytics generation from evaluated "
                    "papers (Phase 12).",
    )
    parser.add_argument("exam_config", help="exam configuration JSON file")
    parser.add_argument("results", help="batch results JSONL (from --results)")
    parser.add_argument("--out", default="reports", help="output directory")
    parser.add_argument("--review-dir", default=None,
                        help="Phase-11 review store directory (applies resolved "
                             "final marks)")
    parser.add_argument("--json-analysis", action="store_true",
                        help="print the analytics JSON instead of text")
    args = parser.parse_args(argv)

    from aos_v0.exam.config import load_exam_configuration

    config_data = load_exam_configuration(args.exam_config)
    exam = config_data.exam if hasattr(config_data, "exam") else config_data

    if args.json_analysis:
        import json as _json

        from .export import export_payload
        from .analytics import question_stats as qstats

        if args.review_dir is not None:
            from aos_v0.exam.review import ReviewStore

            store = ReviewStore(args.review_dir)
        else:
            store = None
        records = assemble_papers(
            read_results(args.results), exam, store=store
        )
        question_ids = [q.question_id for q in exam.questions]
        payload = export_payload(
            records,
            class_analytics(records, qstats(records, question_ids)),
        )
        print(_json.dumps(payload, indent=2))
        return 0

    manifest = generate_reports(
        exam,
        args.results,
        review_dir=args.review_dir,
        out_dir=args.out,
    )
    print(f"reports written to {manifest['out_dir']} "
          f"({manifest['reported']} papers)")
    for filename in manifest["files"]:
        print("  " + os.path.join(manifest["out_dir"], filename))
    return 0


if __name__ == "__main__":
    raise SystemExit(report_cli_main())