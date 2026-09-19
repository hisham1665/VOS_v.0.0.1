"""Class analytics + ASCII dashboards (Phase 12 deliverables).

``class_analytics`` computes the plan's metric list; ``render_analytics`` draws
the analytics dashboard; ``student_report`` and ``batch_report`` generate the
individual and batch report text.
"""

from __future__ import annotations

import statistics
from typing import Iterable, List, Sequence

from .models import ClassAnalytics, PaperRecord, QuestionStat


def class_analytics(
    records: Sequence[PaperRecord], question_stats: Sequence[QuestionStat] = ()
) -> ClassAnalytics:
    percentages = [
        record.percentage
        for record in records
        if record.percentage is not None
    ]
    analysis = ClassAnalytics(
        total_students=len(records),
        question_stats=[stats.model_copy() for stats in question_stats],
    )
    if percentages:
        analysis.average = round(statistics.fmean(percentages), 1)
        analysis.median = round(statistics.median(percentages), 1)
        analysis.highest = round(max(percentages), 1)
        analysis.lowest = round(min(percentages), 1)
    if records:
        analysis.review_rate = round(
            sum(1 for r in records if r.status == "Review") / len(records), 3
        )
        analysis.ocr_failure_rate = round(
            sum(1 for r in records if r.has_ocr_failure) / len(records), 3
        )
        analysis.agent_disagreement_rate = round(
            sum(1 for r in records if r.has_agent_disagreement) / len(records), 3
        )
    return analysis


def question_stats(
    records: Sequence[PaperRecord], question_ids: Sequence[str]
) -> List[QuestionStat]:
    """Per-question mean and difficulty (1 - mean/max) over final marks."""
    stats: List[QuestionStat] = []
    for qid in question_ids:
        marks = [
            row.final_marks
            for record in records
            for row in record.rows
            if row.question_id == qid and row.final_marks is not None
        ]
        max_marks = max(
            (row.max_marks for record in records for row in record.rows
             if row.question_id == qid),
            default=0.0,
        )
        mean = round(statistics.fmean(marks), 2) if marks else None
        difficulty = None
        if mean is not None and max_marks > 0:
            difficulty = round(1 - mean / max_marks, 3)
        stats.append(
            QuestionStat(
                question_id=qid,
                question_text="",
                max_marks=max_marks,
                mean=mean,
                difficulty=difficulty,
            )
        )
    return stats


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def render_analytics(analytics: ClassAnalytics) -> str:
    """ASCII analytics dashboard."""
    lines = [
        "CLASS ANALYTICS",
        "=" * 44,
        f"Total Students          {analytics.total_students}",
        f"Average (percentage)    {_pct(analytics.average)}",
        f"Median                  {_pct(analytics.median)}",
        f"Highest                 {_pct(analytics.highest)}",
        f"Lowest                  {_pct(analytics.lowest)}",
        "",
        "Question-wise",
        "-" * 44,
    ]
    lines.append(f"{'Q':<10}{'Max':<8}{'Mean':<8}{'Difficulty'}")
    for stat in analytics.question_stats:
        mean = "—" if stat.mean is None else f"{stat.mean:g}"
        diff = "—" if stat.difficulty is None else f"{stat.difficulty:.2f}"
        lines.append(
            f"{stat.question_id:<10}{stat.max_marks:<8g}{mean:<8}{diff}"
        )
    lines += [
        "",
        "Rates",
        "-" * 44,
        f"Review Rate             {analytics.review_rate:.1%}",
        f"OCR Failure Rate        {analytics.ocr_failure_rate:.1%}",
        f"Agent Disagreement Rate {analytics.agent_disagreement_rate:.1%}",
        "",
    ]
    return "\n".join(lines)


def student_report(record: PaperRecord) -> str:
    """Individual per-student report (the plan's per-student deliverable)."""
    lines = [
        f"Student: {record.name or '—'}",
        f"Roll No: {record.roll_no}",
        "",
        f"Total: {record.total_marks if record.total_marks is not None else '—'}"
        f"/{record.max_marks:g}",
        f"Status: {record.status}",
        "",
    ]
    for row in record.rows:
        final = "—" if row.final_marks is None else f"{row.final_marks:g}"
        lines.append(f"{row.question_id}: {final}/{row.max_marks:g}")
        if row.review_reasons:
            lines.append(f"  review: {', '.join(row.review_reasons)}")
        for concept in row.concepts_satisfied:
            lines.append(f"  - {concept} satisfied")
        for concept in row.missing_concepts:
            lines.append(f"  - {concept} missing")
    if record.paper_level_reasons:
        lines.append("Paper-level review:")
        for reason in record.paper_level_reasons:
            lines.append(f"  - {reason}")
    return "\n".join(lines)


def batch_report(records: Sequence[PaperRecord], analytics: ClassAnalytics):
    """Batch report generator: a one-line summary per paper plus the metrics."""
    lines = [
        f"BATCH REPORT — {analytics.total_students} papers",
        "=" * 44,
    ]
    for record in records:
        pct = _pct(record.percentage)
        lines.append(
            f"{record.roll_no:<12}{record.name or '—':<20}"
            f"{pct:>6}%  {record.status}"
        )
    lines.append("-" * 44)
    lines.append(
        f"average {_pct(analytics.average)}% · review rate "
        f"{analytics.review_rate:.1%} · ocr failure "
        f"{analytics.ocr_failure_rate:.1%} · disagreement "
        f"{analytics.agent_disagreement_rate:.1%}"
    )
    return "\n".join(lines) + "\n"