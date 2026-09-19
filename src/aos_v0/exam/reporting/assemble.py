"""Build report records from evaluated results and the review store.

The record builder is the one place that knows how a *proposed* mark becomes
the *final* mark for reporting: an accepted/modified review card replaces the
proposal, an escalated or still-pending card keeps the paper flagged for
review. Nothing here re-runs evaluations.
"""

from __future__ import annotations

import json
from typing import List, Optional

from aos_v0.exam.models import ExamConfiguration
from aos_v0.exam.orchestrator import ExamRunResult
from aos_v0.exam.review.models import ReviewDecision, ReviewStatus

from .models import DetailRow, PaperRecord

_DISPOSITION = {
    ReviewDecision.ACCEPT: "accepted",
    ReviewDecision.MODIFY: "modified",
    ReviewDecision.ESCALATE: "escalated",
}

# Reasons that belong to the *paper*, not to a single answer.
_PAPER_OWNED_REASONS = {
    "low_ocr_confidence",
    "identity_uncertain",
    "missing_page",
}


def read_results(path) -> List[ExamRunResult]:
    """Read the batch driver's append-only results JSONL (skips bad lines)."""
    results: List[ExamRunResult] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                results.append(ExamRunResult.model_validate(json.loads(line)))
            except (ValueError, json.JSONDecodeError):
                continue
    return results


def _agent_marks(row, role: str) -> Optional[float]:
    agent = (row.agents or {}).get(role)
    if not agent:
        return None
    return agent.get("marks")


def _disposition_for(card, needs_review: bool) -> str:
    if card is None:
        return "pending" if needs_review else "proposed"
    if card.status != ReviewStatus.REVIEWED:
        return "pending"
    if card.decision is None:
        return "proposed"
    return _DISPOSITION.get(card.decision, "proposed")


def _is_resolved(store, paper_id: str, question_id: str) -> bool:
    """True when the row's review card exists and was accepted or modified."""
    if store is None:
        return False
    card = store.get(f"{paper_id}:{question_id}")
    if card is None or card.status != ReviewStatus.REVIEWED:
        return False
    return card.decision in (ReviewDecision.ACCEPT, ReviewDecision.MODIFY)


def question_rows_by_id(questions) -> dict:
    text = {}
    maxes = {}
    for question in questions or []:
        text[question.question_id] = getattr(question, "text", "")
        maxes[question.question_id] = question.max_marks
    return text, maxes


def assemble_papers(
    results: List[ExamRunResult],
    exam: ExamConfiguration,
    store=None,
) -> List[PaperRecord]:
    """One ``PaperRecord`` per evaluated paper, evidence carried in rows."""
    question_text, _ = question_rows_by_id(exam.questions)
    records: List[PaperRecord] = []
    for result in results:
        rows: List[DetailRow] = []
        roll = (result.student or {}).get("roll_no") or result.paper_id
        name = (result.student or {}).get("name") or ""
        any_unresolved = False
        for qrow in result.rows:
            card = store.get(f"{result.paper_id}:{qrow.question_id}") if store else None
            final_marks = qrow.marks
            if card is not None and card.status == ReviewStatus.REVIEWED:
                if card.decision in (ReviewDecision.ACCEPT, ReviewDecision.MODIFY):
                    final_marks = card.final_marks
                else:
                    final_marks = None  # escalated: never invented
            if qrow.needs_review and (
                card is None
                or card.status != ReviewStatus.REVIEWED
                or card.decision == ReviewDecision.ESCALATE
            ):
                any_unresolved = True
            rows.append(
                DetailRow(
                    roll_no=roll,
                    name=name,
                    paper_id=result.paper_id,
                    question_id=qrow.question_id,
                    question_text=question_text.get(qrow.question_id, ""),
                    max_marks=qrow.max_marks,
                    agent1_marks=_agent_marks(qrow, "primary"),
                    agent2_marks=_agent_marks(qrow, "verifier"),
                    final_marks=final_marks,
                    confidence=qrow.confidence,
                    review_required=qrow.needs_review,
                    review_reasons=list(qrow.review_reasons),
                    review_disposition=_disposition_for(card, qrow.needs_review),
                    concepts_satisfied=list(
                        (qrow.agents.get("primary") or {}).get(
                            "concepts_satisfied", []
                        )
                    ),
                    missing_concepts=list(
                        (qrow.agents.get("primary") or {}).get(
                            "missing_concepts", []
                        )
                    ),
                )
            )

        paper_card = None
        paper_reasons = list(result.review_reasons)
        if store:
            paper_card = store.get(f"{result.paper_id}:*")
        resolved_row_reasons = {
            reason
            for qrow in result.rows
            for reason in qrow.review_reasons
            if _is_resolved(store, result.paper_id, qrow.question_id)
        }
        paper_reasons = [
            r for r in paper_reasons
            if r in _PAPER_OWNED_REASONS and r not in resolved_row_reasons
        ]
        if paper_card is not None and paper_card.status == ReviewStatus.REVIEWED:
            if paper_card.decision != ReviewDecision.ESCALATE:
                paper_reasons = [
                    r for r in paper_reasons
                    if r not in paper_card.review_reasons
                ]
        paper_unresolved = bool(paper_reasons)
        if paper_card is not None and paper_card.decision == ReviewDecision.ESCALATE:
            paper_unresolved = True

        status = "Review" if (any_unresolved or paper_unresolved) else "Evaluated"
        scored = [row.final_marks for row in rows if row.final_marks is not None]
        total = round(sum(scored), 4) if scored else None
        max_marks = round(result.max_marks, 4)
        percentage = None
        if total is not None and max_marks > 0:
            percentage = round(total / max_marks * 100, 1)

        records.append(
            PaperRecord(
                paper_id=result.paper_id,
                roll_no=roll,
                name=name,
                status=status,
                total_marks=total,
                max_marks=max_marks,
                percentage=percentage,
                needs_review=result.needs_review,
                rows=rows,
                paper_level_reasons=paper_reasons,
            )
        )
    return records