"""Turn an evaluation result into the review queue (Phase 11 source step).

The orchestrator never blocks on a reviewer -- it produces *proposed marks*
and a per-question evidence trail, and flags which rows need a human. This
module converts an ``ExamRunResult`` into one ``ReviewItem`` per flagged row,
plus one paper-level item for reasons that no single row owns (identity
uncertainty, low OCR confidence, missing pages).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from aos_v0.exam.batch.models import BatchReport
from aos_v0.exam.models import Question
from aos_v0.exam.orchestrator import ExamRunResult
from .models import ReviewItem, utc_now_iso

_PAPER_QUESTION_ID = "*"

# Reasons that describe the *whole paper*, not one answer. They are only
# forwarded to the paper-level card when no question row carries them too.
_PAPER_OWNED_REASONS = {
    "low_ocr_confidence",
    "identity_uncertain",
    "missing_page",
}
_ROW_OWNED_REASONS = {
    "agent_disagreement",
    "ambiguous_answer",
    "diagram_uncertain",
    "mathematical_uncertainty",
    "multiple_answers",
    "low_evaluation_confidence",
    "recovery_failed",
}


def _question_text_lookup(
    questions: Optional[List[Question]],
) -> Callable[[str], str]:
    text_by_id: Dict[str, str] = {}
    for question in questions or []:
        text_by_id[question.question_id] = getattr(question, "text", "")
    return lambda qid: text_by_id.get(qid, "")


def _agent_role(agent_key: str) -> str:
    return "Agent 1" if agent_key == "primary" else "Agent 2"


def row_to_review_item(
    result: ExamRunResult,
    row,
    *,
    answer_key_version: str = "1",
    rubric_version: str = "1",
    question_text: str = "",
) -> ReviewItem:
    """One question row that needs a human -> one ``ReviewItem`` card."""
    agents = dict(row.agents)
    reconciliation = {
        "adopted_from": row.adopted_from,
        "agreed": row.agreed,
        "reconciled": bool(row.marks is not None),
    }
    item = ReviewItem(
        id=f"{result.paper_id}:{row.question_id}",
        paper_id=result.paper_id,
        question_id=row.question_id,
        question_text=question_text,
        student=result.student or {},
        source=result.source,
        pages=[],
        answer_text=row.answer_text or "",
        ocr_confidence=row.extraction_confidence,
        answer_key_version=answer_key_version,
        rubric_version=rubric_version,
        agent1=agents.get("primary"),
        agent2=agents.get("verifier"),
        disagreement=not row.agreed,
        disputed_concepts=list(row.disputed_concepts),
        reconciliation=reconciliation,
        proposed_marks=row.marks,
        max_marks=row.max_marks,
        proposed_confidence=row.confidence,
        review_reasons=list(row.review_reasons),
        model_info=_model_info(result),
        created_at=utc_now_iso(),
    )
    return item


def _model_info(result: ExamRunResult) -> dict:
    if result.trace is None:
        return {}
    snapshots: Dict[str, str] = {}
    for node in result.trace.nodes:
        if node.capability:
            snapshots[node.capability] = node.model or node.resource_id or ""
    return snapshots


def collect_review_items(
    result: ExamRunResult,
    *,
    questions: Optional[List[Question]] = None,
    answer_key_version: str = "1",
    rubric_version: str = "1",
) -> List[ReviewItem]:
    """One item per flagged row + one paper-level card for paper-level reasons.

    Returns an empty list for a clean evaluation: nothing goes to the queue
    unless the evaluator asked for a human. Paper-level *and* row-level cards
    are kept distinct so the reviewer route stays honest.
    """
    text_for = _question_text_lookup(questions)
    items: List[ReviewItem] = []
    if not result.needs_review:
        return items

    row_reasons = set()
    for row in result.rows:
        if not getattr(row, "needs_review", False):
            continue
        row_reasons.update(row.review_reasons)
        items.append(
            row_to_review_item(
                result,
                row,
                answer_key_version=answer_key_version,
                rubric_version=rubric_version,
                question_text=text_for(row.question_id),
            )
        )

    orphan_reasons = [
        r for r in result.review_reasons if r not in row_reasons
    ]
    paper_reasons = [r for r in orphan_reasons if r in _PAPER_OWNED_REASONS]
    if paper_reasons:
        items.append(
            ReviewItem(
                id=f"{result.paper_id}:{_PAPER_QUESTION_ID}",
                paper_id=result.paper_id,
                question_id=_PAPER_QUESTION_ID,
                student=result.student or {},
                source=result.source,
                answer_text="",
                proposed_marks=None,
                max_marks=result.max_marks,
                proposed_confidence=result.confidence,
                review_reasons=paper_reasons,
                model_info=_model_info(result),
                created_at=utc_now_iso(),
            )
        )
    return items


def enqueue_batch_results(
    report: BatchReport,
    store,
    exam,
    *,
    flow_id: str = "batch",
    actor: str = "batch",
    answer_key_version: str = "1",
    rubric_version: str = "1",
    force: bool = False,
) -> List[str]:
    """Push every unflagged card from a finished batch into the review store.

    Consumes the per-paper ``ExamRunResult`` the driver already recorded -- the
    audit trail is never rebuilt by re-running an evaluation.
    """
    added: List[str] = []
    for item in report.items:
        if item.result is None:
            continue
        cards = collect_review_items(
            item.result,
            questions=getattr(exam, "questions", None),
            answer_key_version=answer_key_version,
            rubric_version=rubric_version,
        )
        added.extend(
            store.enqueue(
                cards,
                flow_id=flow_id,
                actor=actor,
                force=force,
            )
        )
    return added