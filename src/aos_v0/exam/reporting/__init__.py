"""Phase 12 -- Reports, CSV and Analytics.

Turn evaluated papers into the plan's deliverables: the three CSVs (student
results, detailed evaluation, issues), an individual per-student report,
a batch report, a JSON export, and the class-analytics API + dashboard.
Everything is derived from the evidence the orchestrator already recorded
(`--results` JSONL of the batch driver); reporting never re-runs an
evaluation. Optional Phase-11 review-store feedback replaces proposed marks
with resolved final marks and keeps unresolved papers flagged ``Review``.

Key entry points:

- ``read_results`` / ``assemble_papers`` -- results JSONL -> report records.
- ``student_results_csv`` / ``detailed_evaluation_csv`` / ``issues_csv``.
- ``class_analytics`` / ``question_stats`` / ``render_analytics``.
- ``student_report`` / ``batch_report``.
- ``export_json`` / ``report_cli_main`` (``python -m aos_v0.exam.reporting``).
"""

from __future__ import annotations

from .analytics import (
    batch_report,
    class_analytics,
    question_stats,
    render_analytics,
    student_report,
)
from .assemble import assemble_papers, read_results
from .cli import generate_reports, report_cli_main
from .csv import (
    detailed_evaluation_csv,
    issues_csv,
    student_results_csv,
    write_detailed_evaluation,
    write_issues,
    write_student_results,
)
from .export import export_json, export_payload
from .models import (
    ClassAnalytics,
    DetailRow,
    PaperRecord,
    QuestionStat,
)

__all__ = [
    "ClassAnalytics",
    "DetailRow",
    "PaperRecord",
    "QuestionStat",
    "assemble_papers",
    "batch_report",
    "class_analytics",
    "detailed_evaluation_csv",
    "export_json",
    "export_payload",
    "generate_reports",
    "issues_csv",
    "question_stats",
    "read_results",
    "render_analytics",
    "report_cli_main",
    "student_report",
    "student_results_csv",
    "write_detailed_evaluation",
    "write_issues",
    "write_student_results",
]