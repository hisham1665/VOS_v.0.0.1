"""The plan's three CSVs (Phase 12 deliverables).

- CSV-1 ``student_results``: one row per student, one column per question.
- CSV-2 ``detailed_evaluation``: one row per question per student.
- CSV-3 ``issues``: one row per review reason with severity + resolution.

All writers accept either a filesystem path or a file-like object; the
``*_csv_text`` helpers return the CSV as a string for the CLI / tests.
"""

from __future__ import annotations

import csv
import io
from typing import Iterable, List, Optional, Sequence, TextIO, Union

from .models import DetailRow, PaperRecord

PathOrWriter = Union[str, TextIO]


def _writer_for(target: PathOrWriter) -> TextIO:
    if isinstance(target, str):
        return open(target, "w", newline="", encoding="utf-8")
    return target


def _empty_cell(value: Optional[float]) -> str:
    return "" if value is None else f"{value:g}"


def student_results_rows(
    records: Sequence[PaperRecord], question_ids: Sequence[str]
) -> List[List[str]]:
    header = ["Roll No", "Student Name", *question_ids,
              "Total", "Percentage", "Status"]
    rows = [header]
    for record in records:
        cells = [record.roll_no, record.name]
        for qid in question_ids:
            marks = record.marks_for(qid)
            cells.append(_empty_cell(marks))
        total = _empty_cell(record.total_marks)
        pct = "" if record.percentage is None else f"{record.percentage:g}"
        cells += [total, pct, record.status]
        rows.append(cells)
    return rows


def write_student_results(
    records: Sequence[PaperRecord], question_ids: Sequence[str], target: PathOrWriter
) -> None:
    text = student_results_csv(records, question_ids)
    writer = _writer_for(target)
    try:
        writer.write(text)
    finally:
        if isinstance(target, str):
            writer.close()


def student_results_csv(
    records: Sequence[PaperRecord], question_ids: Sequence[str]
) -> str:
    return _to_csv(student_results_rows(records, question_ids))


def detailed_evaluation_csv(records: Sequence[PaperRecord]) -> str:
    header = ["Roll No", "Question", "Max Marks", "Agent 1", "Agent 2",
              "Final Marks", "Confidence", "Review Required"]
    rows = [header]
    for record in records:
        for row in record.rows:
            rows.append([
                row.roll_no,
                row.question_id,
                f"{row.max_marks:g}",
                row.agent1,
                row.agent2,
                row.final,
                f"{row.confidence:g}",
                "true" if row.review_required else "false",
            ])
    return _to_csv(rows)


def write_detailed_evaluation(
    records: Sequence[PaperRecord], target: PathOrWriter
) -> None:
    text = detailed_evaluation_csv(records)
    writer = _writer_for(target)
    try:
        writer.write(text)
    finally:
        if isinstance(target, str):
            writer.close()


_ISSUE_SEVERITY = {
    "low_ocr_confidence": "High",
    "identity_uncertain": "High",
    "missing_page": "High",
    "multiple_answers": "Medium",
    "ambiguous_answer": "Medium",
    "diagram_uncertain": "Medium",
    "mathematical_uncertainty": "Medium",
    "agent_disagreement": "Medium",
    "low_evaluation_confidence": "Medium",
    "recovery_failed": "Medium",
}


def issue_label(reason: str) -> str:
    return " ".join(word.capitalize() for word in reason.split("_"))


def issues_csv(records: Sequence[PaperRecord]) -> str:
    header = ["Roll No", "Page", "Question", "Issue", "Severity", "Resolution"]
    rows = [header]
    for record in records:
        for row in record.rows:
            for reason in row.review_reasons:
                rows.append([
                    row.roll_no,
                    "",
                    row.question_id,
                    issue_label(reason),
                    _ISSUE_SEVERITY.get(reason, "Medium"),
                    "Human Review",
                ])
        for reason in record.paper_level_reasons:
            rows.append([
                record.roll_no,
                "",
                "*",
                issue_label(reason),
                _ISSUE_SEVERITY.get(reason, "High"),
                "Human Review",
            ])
    return _to_csv(rows)


def write_issues(records: Sequence[PaperRecord], target: PathOrWriter) -> None:
    text = issues_csv(records)
    writer = _writer_for(target)
    try:
        writer.write(text)
    finally:
        if isinstance(target, str):
            writer.close()


def _to_csv(rows: Iterable[List[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue()