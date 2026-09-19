"""Phase 13 deliverables: ``evaluation_results.csv``, ``BENCHMARK_REPORT.md``
and the ``experiment_results/`` text files.

Rendering is plain text / CSV so the research artifacts are diffable and
readable on any machine -- no markdown renderer is assumed.
"""

from __future__ import annotations

import csv
import datetime
import os
from typing import List, Optional

from .corpus import CorpusReport
from .experiments import ExperimentResult

EVALUATION_CSV_HEADER = [
    "entry_id",
    "question_id",
    "question_type",
    "student_answer",
    "human_marks",
    "max_marks",
    "ai_marks",
    "agent1_marks",
    "agent2_marks",
    "absolute_error",
    "exact_agreement",
    "tol_0_5",
    "tol_1_0",
    "confidence",
    "needs_review",
    "review_reasons",
    "paraphrase",
    "status",
]


def evaluation_results_csv(report: CorpusReport) -> List[dict]:
    """Per-entry AI-vs-human comparison rows."""
    rows: List[dict] = []
    for entry in report.entries:
        error = (
            abs(entry.ai_marks - entry.human_marks)
            if entry.ai_marks is not None
            else ""
        )
        rows.append(
            {
                "entry_id": entry.entry_id,
                "question_id": entry.question_id,
                "question_type": entry.question_type,
                "student_answer": entry.student_answer,
                "human_marks": entry.human_marks,
                "max_marks": entry.max_marks,
                "ai_marks": entry.ai_marks if entry.ai_marks is not None else "",
                "agent1_marks": entry.agent1_marks if entry.agent1_marks is not None else "",
                "agent2_marks": entry.agent2_marks if entry.agent2_marks is not None else "",
                "absolute_error": error,
                "exact_agreement": (
                    entry.ai_marks == entry.human_marks
                    if entry.ai_marks is not None
                    else ""
                ),
                "tol_0_5": (
                    abs(entry.ai_marks - entry.human_marks) <= 0.5
                    if entry.ai_marks is not None
                    else ""
                ),
                "tol_1_0": (
                    abs(entry.ai_marks - entry.human_marks) <= 1.0
                    if entry.ai_marks is not None
                    else ""
                ),
                "confidence": entry.confidence,
                "needs_review": entry.needs_review,
                "review_reasons": ";".join(entry.review_reasons),
                "paraphrase": entry.paraphrase,
                "status": entry.status,
            }
        )
    return rows


def write_evaluation_csv(report: CorpusReport, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVALUATION_CSV_HEADER)
        writer.writeheader()
        for row in evaluation_results_csv(report):
            writer.writerow(row)


def _fmt_metric(value, rubric) -> str:
    if isinstance(value, tuple):
        value = f"{value[0]}/{value[1]} ({value[2]:.1%})" if len(value) == 3 else str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_experiment(experiment: ExperimentResult) -> str:
    lines = [
        f"## Experiment {experiment.name} -- {experiment.tagline}",
        "",
    ]
    for key, value in experiment.summary.items():
        if isinstance(value, dict):
            lines.append(f"- **{key}**:")
            for sub, sub_value in value.items():
                lines.append(f"  - {sub}: {_fmt_metric(sub_value, '')}")
        else:
            lines.append(f"- {key}: {_fmt_metric(value, '')}")
    if experiment.rows:
        lines.append("")
        lines.append("| " + " | ".join(experiment.rows[0].keys()) + " |")
        lines.append("|" + "|".join("---" for _ in experiment.rows[0].keys()) + "|")
        for row in experiment.rows:
            lines.append("| " + " | ".join(str(row[k]) for k in row.keys()) + " |")
    if experiment.notes:
        lines.extend(["", "*" + experiment.notes + "*"])
    lines.append("")
    return "\n".join(lines)


def render_benchmark_report(
    report: CorpusReport,
    experiments: List[ExperimentResult],
    *,
    dataset_entry_count: Optional[int] = None,
    throughput: Optional[dict] = None,
) -> str:
    metrics = report.metrics
    lines = [
        "# AOS Exam Evaluator -- Research Benchmark Report",
        "",
        f"Generated: {datetime.datetime.now().isoformat(timespec='seconds')}",
        "", "## Metrics",
        "",
        f"- Corpus entries: {metrics.get('entries')}",
        f"- Evaluated: {metrics.get('evaluated')}",
        f"- Mean Absolute Error vs human: {metrics.get('mean_absolute_error'):.3f}",
        f"- Exact agreement: {metrics.get('exact_agreement'):.3f}",
    ]
    for key, value in metrics.get("tolerance_agreement", {}).items():
        lines.append(f"- Tolerance agreement {key}: {value:.3f}")
    lines.extend(
        [
            "- False rejection (correct answers marked 0): "
            f"{_fmt_metric(metrics.get('false_rejection'), '')}",
            "- False acceptance (incorrect answers given marks): "
            f"{_fmt_metric(metrics.get('false_acceptance'), '')}",
        ]
    )
    if "semantic_acceptance" in metrics:
        lines.append(
            "- Semantic acceptance (paraphrase rows accepted at full marks): "
            f"{_fmt_metric(metrics.get('semantic_acceptance'), '')}"
        )
    if "partial_mark_agreement" in metrics:
        lines.append(
            "- Partial-mark agreement (human partial marks within 0.5): "
            f"{_fmt_metric(metrics.get('partial_mark_agreement'), '')}"
        )
    if "agent_agreement" in metrics:
        lines.append(
            "- Agent agreement (primary == verifier): "
            f"{_fmt_metric(metrics.get('agent_agreement'), '')}"
        )
    if "reconciliation_success" in metrics:
        lines.append(
            "- Reconciliation success: "
            f"{metrics.get('reconciliation_success'):.3f}"
        )
    if dataset_entry_count is not None:
        lines.append(f"- Dataset entries: {dataset_entry_count}")
    if throughput is not None:
        lines.extend(
            [
                "- Batch throughput:",
                f"  - papers: {throughput.get('papers')}",
                f"  - papers/second: {throughput.get('papers_per_second')}",
                f"  - elapsed seconds: {throughput.get('elapsed_seconds')}",
            ]
        )
    lines.append("")
    lines.append("## Research Experiments")
    lines.append("")
    for experiment in experiments:
        lines.append(render_experiment(experiment))
    lines.append("---")
    lines.append(
        "Golden rules held during benchmarking: no auto-zeroing, no invented "
        "marks -- every AI mark originates from the AOS dynamic pipeline or a "
        "human review decision."
    )
    return "\n".join(lines)


def write_benchmark_report(report: CorpusReport, path: str, *args, **kwargs) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_benchmark_report(report, *args, **kwargs))


def write_experiment_files(results: List[ExperimentResult], directory: str) -> List[str]:
    os.makedirs(directory, exist_ok=True)
    written = []
    for experiment in results:
        slug = experiment.name.lower().replace(" ", "_")
        path = os.path.join(directory, f"{slug}.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(render_experiment(experiment))
        written.append(path)
    return written