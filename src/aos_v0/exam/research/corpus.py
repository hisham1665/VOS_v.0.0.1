"""Phase 13 corpus runner -- run the AOS evaluator over the ground-truth
dataset and record the AI-vs-human comparison for every entry.

Each ground-truth entry is evaluated standalone through the real
``ExamOrchestrator`` (single-question exam built from the entry), so the
measured marks come from the actual AOS dynamic pipeline, never from a mock.
The runner records the marked decision (primary/verifier/final), the
confidence, whether the row was flagged for human review, and the trace for
selection-quality analysis.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from aos_v0.exam.models import (
    AnswerKey,
    EvaluationSettings,
    ExamConfiguration,
    ExamEvaluationTask,
    PaperReference,
    Question,
)
from aos_v0.exam.ocr.models import LayoutRegion, OcrBlock, OcrDocument, OcrPage, RegionType
from aos_v0.exam.orchestrator import ExamRunResult, ExamOrchestrator

from .ground_truth import GroundTruthEntry


def task_for(entry: GroundTruthEntry) -> ExamEvaluationTask:
    """Build a single-question exam task from a ground-truth entry."""
    rubric = dict(entry.rubric) or {
        "criteria": [
            {
                "criterion": concept.replace(" ", "_"),
                "marks": entry.max_marks // max(len(entry.expected_concepts), 1),
            }
            for concept in entry.expected_concepts
        ]
    }
    question = Question(
        question_id=entry.question_id,
        text=entry.question_text or "Explain.",
        max_marks=entry.max_marks,
        answer_key=AnswerKey(expected_concepts=entry.expected_concepts),
        rubric=rubric,
    )
    return ExamEvaluationTask(
        task_id=f"research-{entry.entry_id}",
        paper=PaperReference(paper_id=entry.entry_id, file_path=entry.ocr_image or ""),
        exam=ExamConfiguration(
            exam_id="R", title="Research", questions=[question]
        ),
    )


def doc_for_answer(
    entry: GroundTruthEntry, *, confidence: float = 0.95
) -> OcrDocument:
    """Construct an OcrDocument carrying the student answer."""
    blocks = [
        OcrBlock(text="Name: Research Subject", bbox=[0, 30, 400, 80], confidence=confidence),
        OcrBlock(text=f"Roll: {entry.entry_id}", bbox=[0, 90, 200, 130], confidence=confidence),
        OcrBlock(
            text=f"{entry.question_id}.",
            bbox=[0, 220, 300, 270],
            confidence=confidence,
        ),
        OcrBlock(text=entry.student_answer, bbox=[0, 280, 600, 360], confidence=confidence),
    ]
    regions = [
        LayoutRegion(
            region_type=RegionType.HEADER,
            bbox=[0, 20, 1000, 180],
            confidence=confidence,
            label="header",
        )
    ]
    return OcrDocument(
        pages=[
            OcrPage(
                page_no=1,
                source=entry.ocr_image or "research.png",
                blocks=blocks,
                regions=regions,
                confidence=confidence,
            )
        ]
    )


class CorpusEntryResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    entry_id: str
    question_id: str
    question_type: str
    student_answer: str
    human_marks: float
    max_marks: float
    ai_marks: Optional[float]
    agent1_marks: Optional[float]
    agent2_marks: Optional[float]
    confidence: float = 0.0
    needs_review: bool = False
    review_reasons: List[str] = Field(default_factory=list)
    paraphrase: bool = False
    status: str = "ok"


class CorpusReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    entries: List[CorpusEntryResult] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)


def evaluate_entry(
    entry: GroundTruthEntry,
    *,
    registry=None,
    runner=None,
    settings: Optional[EvaluationSettings] = None,
    log: Optional[Callable[[str], None]] = None,
) -> CorpusEntryResult:
    """Evaluate one ground-truth entry through the real orchestrator."""
    entry_result, _ = evaluate_entry_with_trace(
        entry, registry=registry, runner=runner, settings=settings, log=log
    )
    return entry_result


def evaluate_entry_with_trace(
    entry: GroundTruthEntry,
    *,
    registry=None,
    runner=None,
    settings: Optional[EvaluationSettings] = None,
    log: Optional[Callable[[str], None]] = None,
) -> tuple[CorpusEntryResult, ExamRunResult]:
    """Evaluate one entry and return ``(entry_result, full ExamRunResult)`` so
    experiments can inspect the ``ExecutionTrace`` and recovery records."""
    task = task_for(entry)
    orchestrator = ExamOrchestrator(registry=registry)
    result: ExamRunResult = orchestrator.execute(
        task,
        ocr_document=doc_for_answer(entry),
        settings=settings,
        runner=runner,
        log=log,
    )
    row = result.rows[0]
    entry_result = CorpusEntryResult(
        entry_id=entry.entry_id,
        question_id=entry.question_id,
        question_type=entry.question_type,
        student_answer=entry.student_answer,
        human_marks=entry.human_marks,
        max_marks=entry.max_marks,
        ai_marks=row.marks,
        agent1_marks=_agent_mark(row, "primary"),
        agent2_marks=_agent_mark(row, "verifier"),
        confidence=row.confidence,
        needs_review=row.needs_review,
        review_reasons=list(row.review_reasons),
        paraphrase=entry.paraphrase,
        status=result.status,
    )
    return entry_result, result


def _agent_mark(row, role: str) -> Optional[float]:
    agent = (row.agents or {}).get(role)
    if not agent:
        return None
    return agent.get("marks")


def _scored(eval_result: CorpusEntryResult) -> bool:
    return eval_result.ai_marks is not None


def _aggregate(report: CorpusReport) -> dict:
    from .metrics import (
        agent_agreement,
        exact_agreement,
        false_acceptance,
        false_rejection,
        mean_absolute_error,
        partial_mark_agreement,
        reconciliation_success,
        semantic_acceptance,
        tolerance_agreement,
    )

    evaluated = [e for e in report.entries if _scored(e)]
    humans = [e.human_marks for e in evaluated]
    ai = [e.ai_marks for e in evaluated]
    full = [e.max_marks for e in evaluated]
    metrics = {
        "entries": len(report.entries),
        "evaluated": len(evaluated),
        "mean_absolute_error": mean_absolute_error(humans, ai),
        "exact_agreement": exact_agreement(humans, ai),
        "tolerance_agreement": tolerance_agreement(humans, ai),
        "false_rejection": false_rejection(humans, ai),
        "false_acceptance": false_acceptance(humans, ai),
    }
    paraphrase = [e for e in evaluated if e.paraphrase]
    if paraphrase:
        metrics["semantic_acceptance"] = semantic_acceptance(
            [e.human_marks for e in paraphrase],
            [e.ai_marks for e in paraphrase],
            [e.max_marks for e in paraphrase],
        )
    if evaluated:
        hits, rate = partial_mark_agreement(humans, ai)
        metrics["partial_mark_agreement"] = (hits, rate)
    hit, rate = agent_agreement(
        [e.agent1_marks for e in evaluated],
        [e.agent2_marks for e in evaluated],
    )
    metrics["agent_agreement"] = (hit, rate)
    metrics["reconciliation_success"] = reconciliation_success(
        _synthetic_rows(report)
    )
    return metrics


def _synthetic_rows(report: CorpusReport) -> List[dict]:
    rows = []
    for entry in report.entries:
        agents = {}
        if entry.agent1_marks is not None:
            agents["primary"] = {"marks": entry.agent1_marks}
        if entry.agent2_marks is not None:
            agents["verifier"] = {"marks": entry.agent2_marks}
        rows.append(
            {
                "agents": agents,
                "needs_review": entry.needs_review,
            }
        )
    return rows


def run_corpus(
    entries: List[GroundTruthEntry],
    *,
    registry=None,
    settings=None,
    log=None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> CorpusReport:
    """Evaluate the whole dataset and aggregate the research metrics."""
    report = CorpusReport()
    for index, entry in enumerate(entries):
        report.entries.append(evaluate_entry(entry, registry=registry, settings=settings, log=log))
        if progress:
            progress(index + 1, len(entries))
    report.metrics = _aggregate(report)
    if report.metrics.get("semantic_acceptance") is None:
        report.metrics.pop("semantic_acceptance", None)
    return report


def write_corpus_json(report: CorpusReport, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report.model_dump(mode="json"), handle, indent=2)