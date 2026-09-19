"""Phase 8 -- AOS dynamic orchestration (plan Phase 8, gap G3).

Fast-forwards the exam domain from a static, self-driven pipeline to the AOS
dynamic-orchestration flow the plan calls for:

    ExamEvaluationTask -> build_graph (DAG) -> build_waves
        -> Capability Registry (entries + CAPABILITY DNA)
        -> Dynamic Model Selection (CapabilityRegistry.select, continuous scorer)
        -> per-node execution through the selected resource's registered run_fn
        -> ExamRunResult

The kernel already owns the machinery: `ExamEvaluationTask.build_graph()`
emits the DAG, `build_waves()` orders it, `CapabilityRegistry.select()` grades
every registered resource against each node's Capability DNA and returns a
winner plus ranked substitutes, and `register_local_exam_transport()` hands
the registry runnable run_fns -- closing gap G3 for the requirement spine
(real transports for PaddleOCR / vLLM remain a deployment detail; the local
structural engines are the wired run_fns today).

Two Registry entry flavours coexist:

  * `register_exam_resources()` -- the Phase 1 *declared* interfaces
    (transport=declared): capability-based selection only, run_fn raises a
    typed ProviderError if invoked.
  * `register_local_exam_transport()` -- the Phase 8 *wire*: same resource
    ids, transport=wired, run_fns bound to the domain engines (intake+ocr,
    layout, identity, segmentation, primary evaluator, verifier,
    reconciliation, report). `ExamOrchestrator` upgrades the registry with
    these before executing, so the routability gate (`is_routable`) picks the
    executable transport while declared resources stay visible as fallback
    model offers in model-selection traces.

Model selection is genuinely dynamic: every node is hydrated with its
Capability DNA (`dna_for_node`) and routed through `CapabilityRegistry.select`
-- winner, score, latency, cost and runner-up are recorded per node in the
`ExecutionTrace`, and the winner's registered run_fn executes the node.

Golden rules are inherited from earlier phases and preserved by the run_fns:
no mark is ever decided by OCR/layout/identity; unanswered or unreadable
content yields None marks / review reasons, never an automatic zero; the
verifier never sees the primary's verdict (independence is structural).
"""

from __future__ import annotations

import json
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from aos_v0.core.capability_registry import (
    CapabilityManifest,
    CapabilityRegistry,
    IOSchema,
    required_input_modality,
)
from aos_v0.core.graph_utils import build_waves
from aos_v0.core.models import CapabilityDNA

from aos_v0.exam.confidence import ConfidenceEngine
from aos_v0.exam.dna import EXAM_DNA_TEMPLATES, _CAPABILITY_FLAG_MAP
from aos_v0.exam.evaluate.engine import round_half_up
from aos_v0.exam.intake import IntakeError, ingest
from aos_v0.exam.models import (
    EVALUATION_CAPABILITY,
    EvaluationSettings,
    EvalReviewReason,
    ExamEvaluationTask,
    LAYOUT_CAPABILITY,
    OCR_CAPABILITY,
    Question,
    RECONCILIATION_CAPABILITY,
    REPORT_CAPABILITY,
    Roster,
    SEGMENTATION_CAPABILITY,
    STUDENT_ID_CAPABILITY,
    VERIFICATION_CAPABILITY,
    _node_id,
)
from aos_v0.exam.ocr.models import OcrDocument, OcrResult, OcrStatus
from aos_v0.exam.ocr.pipeline import run_document
from aos_v0.exam.recovery import (
    RESOURCE_OUTAGE,
    ExamRecoveryManager,
    RecoveryOutcome,
    gap_marker,
    review_reason_for,
)
from aos_v0.exam.structure.identity import extract_student_identity
from aos_v0.exam.structure.models import AnswerSheetEntry, SheetStatus, StructuredAnswerSheet
from aos_v0.exam.structure.sheet import structure as structure_sheet
from aos_v0.exam.two_agent import (
    AgentEvaluation,
    IndependentVerifier,
    PrimaryEvaluator,
    ReconciliationResult,
    reconcile_agents,
)


# ---------------------------------------------------------------------------
# Errors + result surfaces
# ---------------------------------------------------------------------------


class ExamOrchestrationError(RuntimeError):
    """Raised when the Phase-8 orchestration cannot proceed honestly."""


class NodeTrace(BaseModel):
    """One executed DAG node and how the registry routed it."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    capability: str
    resource_id: str
    routing_mode: str
    score: float = 0.0
    model: str = ""
    latency_ms: float = 0.0
    status: str = "done"
    output_chars: int = 0
    failure_class: str = ""
    recovered: bool = False
    degraded: bool = False
    attempt_count: int = 0


class ExecutionTrace(BaseModel):
    """Wave-by-wave execution record (dynamic model selection audit trail)."""

    model_config = ConfigDict(extra="forbid")

    waves: int = 0
    nodes: List[NodeTrace] = Field(default_factory=list)


class QuestionRow(BaseModel):
    """One question's reconciled verdict in the final evaluation record.

    Carries the evidence a human reviewer needs (Phase 11 audit trail): the
    student's extracted answer text and each agent's proposed verdict. These
    feed the review queue and the detailed evaluation report, so the audit log
    never has to re-run an evaluation.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str
    marks: Optional[float]
    max_marks: float
    confidence: float = Field(ge=0.0, le=1.0)
    agreed: bool = True
    adopted_from: str = "none"
    review_reasons: List[str] = Field(default_factory=list)
    disputed_concepts: List[str] = Field(default_factory=list)
    needs_review: bool = False
    extraction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence_category: str = "medium"
    answer_text: str = Field(default="", description="student's extracted answer")
    agents: dict = Field(
        default_factory=dict,
        description="per-agent proposed verdicts, keyed by agent role",
    )


class ExamRunResult(BaseModel):
    """Plan-shaped result of orchestrating one ExamEvaluationTask."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    paper_id: str
    exam_title: str
    student: dict = Field(default_factory=dict)
    rows: List[QuestionRow] = Field(default_factory=list)
    total_marks: float = 0.0
    max_marks: float = 0.0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_category: str = "medium"
    confidence_evidence: dict = Field(default_factory=dict)
    escalations: int = 0
    recovery: List[dict] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)
    needs_review: bool = False
    status: str = "ok"
    source: Optional[str] = None
    artifacts: dict = Field(default_factory=dict)
    trace: ExecutionTrace = Field(default_factory=ExecutionTrace)

    @property
    def scored_rows(self) -> List[QuestionRow]:
        return [r for r in self.rows if r.marks is not None]

    def to_plan_json(self) -> dict:
        return self.model_dump()


# ---------------------------------------------------------------------------
# Node DNA hydration (the DAG -> Capability DNA seam)
# ---------------------------------------------------------------------------


def dna_for_node(capability: str) -> Optional[CapabilityDNA]:
    """Capability DNA for one *graph* capability string (Phase 8 node contract).

    ``build_graph()`` emits nodes keyed by coarse capability strings
    (``document_ocr``, ``semantic_answer_evaluation``, ...). This maps the
    capability to its DNA template and returns a CapabilityDNA carrying the
    node's *primary* requirement flag plus the template's ordinals and
    constraints. Dependencies between stages are expressed by the DAG edges,
    not by loading extra flags onto sibling node contracts, so each node
    selects the single resource that genuinely owns its capability.

    Returns None for capability strings the exam domain does not own.
    """
    flag = _CAPABILITY_FLAG_MAP.get(capability)
    template = EXAM_DNA_TEMPLATES.get(flag) if flag else None
    if template is None:
        return None
    primary = template.flags[0] if template.flags else flag
    return CapabilityDNA(
        flags=[primary],
        ordinals=template.ordinals,
        constraints=template.constraints,
        extracted_by="exam.orchestrator",
    )


# ---------------------------------------------------------------------------
# Local registry entries (gap G3): wired run_fns per requirement resource
# ---------------------------------------------------------------------------

# Local transport manifests: narrowed to the primary flags each runnable engine
# genuinely provides. The union still covers every REQUIRED flag
# (document.ocr, document.layout, student_id.extraction, question.segmentation,
# answer.extraction, semantic.answer_evaluation, answer.verification,
# evaluation.reconciliation, confidence.estimation, report.generation).
_LOCAL_EXAM_RESOURCES: Dict[str, dict] = {
    OCR_CAPABILITY: {
        "resource_class": "image",
        "capabilities": ["document.ocr"],
        "input_type": "image",
        "description": "OCR the answer sheet page (local structural engine) into "
                       "text blocks with bounding boxes and per-block confidence.",
    },
    LAYOUT_CAPABILITY: {
        "resource_class": "image",
        "capabilities": ["document.layout"],
        "input_type": "image",
        "description": "Analyse the OCR'd page layout: text, header, question and "
                       "answer regions, tables and diagrams.",
    },
    STUDENT_ID_CAPABILITY: {
        "resource_class": "vlm",
        "capabilities": ["student_id.extraction"],
        "input_type": "image",
        "description": "Extract name / roll / register number from the header and "
                       "validate against the roster when one is provided.",
    },
    SEGMENTATION_CAPABILITY: {
        "resource_class": "llm",
        "capabilities": ["question.segmentation", "answer.extraction"],
        "input_type": "text",
        "description": "Map every answer block to its question id; handle "
                       "out-of-order, continuation and multi-page answers.",
    },
    EVALUATION_CAPABILITY: {
        "resource_class": "llm",
        "capabilities": ["semantic.answer_evaluation"],
        "input_type": "text",
        "description": "Evaluator agent (Agent 1): marks, satisfied/missing "
                       "concepts, reasoning, confidence.",
    },
    VERIFICATION_CAPABILITY: {
        "resource_class": "llm",
        "capabilities": ["answer.verification"],
        "input_type": "text",
        "description": "Independent verifier agent (Agent 2): conservative "
                       "re-check of the same evidence.",
    },
    RECONCILIATION_CAPABILITY: {
        "resource_class": "llm",
        "capabilities": ["evaluation.reconciliation"],
        "input_type": "text",
        "description": "Compare both agents, run disagreement analysis, emit "
                       "final mark + confidence + review reason.",
    },
    REPORT_CAPABILITY: {
        "resource_class": "llm",
        "capabilities": ["report.generation", "confidence.estimation"],
        "input_type": "text",
        "description": "Produce the final evaluation record for the paper.",
    },
}

_LOCAL_LATENCY = {"p50_ms": 2, "p95_ms": 12}


def build_local_exam_manifests() -> List[CapabilityManifest]:
    """CapabilityManifest declarations for the wired local exam transport."""
    manifests = []
    for resource_id, spec in _LOCAL_EXAM_RESOURCES.items():
        manifests.append(
            CapabilityManifest(
                resource_id=resource_id,
                resource_class=spec["resource_class"],
                capabilities=list(spec["capabilities"]),
                input_schema=IOSchema(type=spec["input_type"], format="plain"),
                output_schema=IOSchema(type="text", format="plain"),
                latency=_LOCAL_LATENCY,
                availability={"status": "up", "rate_limit_rpm": 0},
                risk_class="low",
                quality_priors={flag: 0.95 for flag in spec["capabilities"]},
                metadata={
                    "provider": "local",
                    "model": "local-structural-engine",
                    "interface": "local",
                    "transport": "wired",
                    "domain": "exam",
                    "description": spec["description"],
                },
            )
        )
    return manifests


# ---------------------------------------------------------------------------
# LocalExamRunner -- the wired run_fns' domain dispatch
# ---------------------------------------------------------------------------


def _mean(values: List[float], default: float = 0.0) -> float:
    return round(sum(values) / len(values), 4) if values else default


class LocalExamRunner:
    """Executes each registered exam capability against the domain engines.

    The runner holds the run-level state the handlers need (task, source,
    roster, the OCR result, the structured sheet, identities and reconciled
    question verdicts). Handlers are pure functions over that state; node
    identity arrives through the ``node_id`` instruction token, which the
    controller sets to the DAG node id -- this is how the reconciler maps a
    node back to its question.
    """

    def __init__(
        self,
        task: ExamEvaluationTask,
        *,
        source: Optional[str] = None,
        roster: Optional[Roster] = None,
        ocr_document: Optional[OcrDocument] = None,
        settings: Optional[EvaluationSettings] = None,
    ) -> None:
        self.task = task
        self.source = source
        self.roster = roster
        self.settings = settings or task.settings
        self.ocr_result: Optional[OcrResult] = (
            OcrResult(document=ocr_document) if ocr_document is not None else None
        )
        self.sheet: Optional[StructuredAnswerSheet] = None
        self.identity: Optional[dict] = None
        self.recons: Dict[str, ReconciliationResult] = {}
        self.question_lookup: Dict[str, str] = {}
        self.eval_node_map: Dict[str, Tuple[Question, str]] = {}
        self.confidence_engine = ConfidenceEngine(settings=self.settings)
        self.primary_eval_failed: Set[str] = set()
        self.verifier_eval_failed: Set[str] = set()
        self.recovery_outcomes: List[RecoveryOutcome] = []
        self.escalation_count: int = 0

    # -- capability dispatch ------------------------------------------------

    def dispatch(
        self,
        resource_id: str,
        inputs: dict,
        *,
        node_id: Optional[str] = None,
    ) -> dict:
        handler = _RESOURCE_HANDLERS.get(resource_id)
        if handler is None:
            raise ExamOrchestrationError(
                f"no local handler registered for resource '{resource_id}'"
            )
        return handler(self, inputs, node_id=node_id)

    # -- shared helpers ------------------------------------------------------

    def ensure_ocr(self) -> None:
        """Run the intake + OCR ladder once; reused by every sink node."""
        if self.ocr_result is not None:
            return
        if not self.source:
            raise ExamOrchestrationError(
                "no OCR evidence: pass source=<path> or ocr_document=<OcrDocument>"
            )
        try:
            intake_result = ingest(self.source)
        except (IntakeError, OSError) as exc:  # noqa: BLE001
            raise ExamOrchestrationError(f"intake failed: {exc}") from exc
        self.ocr_result = run_document(intake_result.pages, strategy="auto")

    def dispatch_eval(self, node_id: str, agent: str) -> AgentEvaluation:
        question, agent_name = self.eval_node_map[node_id]
        entry = self.entry_for(question)
        if agent_name == "verifier":
            return IndependentVerifier().evaluate(question, entry)
        return PrimaryEvaluator().evaluate(question, entry)

    def entry_for(self, question: Question) -> AnswerSheetEntry:
        if self.sheet is None:
            raise ExamOrchestrationError(
                "question segmentation must run before evaluation"
            )
        wanted = question.question_id.lower()
        for entry in self.sheet.answers:
            if entry.question_id.lower() == wanted:
                return entry
        return AnswerSheetEntry(question_id=question.question_id, confidence=0.99)

    def question_rows(self) -> List[QuestionRow]:
        rows: List[QuestionRow] = []
        for question in self.task.exam.questions:
            recon = self.recons.get(question.question_id)
            if recon is None:
                continue
            entry = self.entry_for(question)
            agents: dict = {}
            if recon.primary is not None:
                agents["primary"] = recon.primary.to_plan_json()
            if recon.verifier is not None:
                agents["verifier"] = recon.verifier.to_plan_json()
            rows.append(
                QuestionRow(
                    question_id=recon.question_id,
                    marks=recon.final_marks,
                    max_marks=recon.primary.max_marks,
                    confidence=round(recon.final_confidence, 4),
                    agreed=recon.agreed,
                    adopted_from=recon.adopted_from,
                    review_reasons=[r.value for r in recon.review_reasons],
                    disputed_concepts=list(recon.disputed_concepts),
                    needs_review=recon.needs_review,
                    extraction_confidence=round(entry.confidence, 4),
                    confidence_category=self.confidence_engine.categorize(
                        recon.final_confidence
                    ).value,
                    answer_text=entry.text,
                    agents=agents,
                )
            )
        return rows


# -- resource handlers -------------------------------------------------------


# Paper-level (structural) stages: when one of these exhausts its recovery
# ladder the whole paper is routed to review -- there is no honest partial
# evaluation of a paper whose OCR / layout / identity / segmentation / merge /
# report stage failed. Per-question stages (primary evaluator / verifier)
# escalate just that question instead.
_STRUCTURAL_CAPABILITIES = {
    OCR_CAPABILITY,
    LAYOUT_CAPABILITY,
    STUDENT_ID_CAPABILITY,
    SEGMENTATION_CAPABILITY,
    RECONCILIATION_CAPABILITY,
    REPORT_CAPABILITY,
}


def _question_for(runner: LocalExamRunner, question_id: str) -> Question:
    for question in runner.task.exam.questions:
        if question.question_id == question_id:
            return question
    raise ExamOrchestrationError(
        f"question '{question_id}' is not part of the configured exam"
    )


def escalated_reconciliation(
    runner: LocalExamRunner,
    question: Question,
    failed_agent: str,
) -> ReconciliationResult:
    """Honest "not scored" verdict for a question whose agent stage escalated.

    ``final_marks`` stays None and the answer routes to review -- the recovery
    loop never invents a zero, never substitutes a guess.
    """
    failure_classes = [cls for outcome in runner.recovery_outcomes
                       for cls in [outcome.failure_class]
                       if outcome.node_id.startswith(
                           _node_id("eval", _node_id("q", question.question_id))
                       )]
    reasons: List[EvalReviewReason] = []
    for cls in failure_classes or [""]:
        reason = review_reason_for(cls)
        if reason not in reasons:
            reasons.append(reason)
    if EvalReviewReason.RECOVERY_FAILED not in reasons:
        reasons.append(EvalReviewReason.RECOVERY_FAILED)

    synthetic = AgentEvaluation(
        agent="primary",
        capability=EVALUATION_CAPABILITY,
        marks=None,
        max_marks=question.max_marks,
        concepts_satisfied=[],
        missing_concepts=[],
        reasoning=(
            f"{failed_agent} evaluation stage exhausted its recovery ladder; "
            "question routed to review"
        ),
        confidence=0.0,
    )
    return ReconciliationResult(
        question_id=question.question_id,
        final_marks=None,
        final_confidence=0.0,
        agreed=False,
        adopted_from="none",
        mark_difference=0.0,
        disputed_concepts=[],
        review_reasons=reasons,
        reasoning=["recovery escalation -> review"],  # type: ignore[list-item]
        primary=synthetic,
        verifier=None,
    )


def _paper_confidence(runner: LocalExamRunner, rows: List[QuestionRow]):
    """Fold the plan's confidence components (Phase 9) into evidence + category.

    The numeric overall reflects all components with evidence; the category is
    the *weakest relevant reading*, so a genuinely weak component routes the
    paper to review even when other components look fine.
    """
    scored = [row for row in rows if row.marks is not None]
    semantic = _mean([row.confidence for row in scored], 0.0)
    agreement = round(
        len([row for row in scored if row.agreed]) / len(scored), 4
    ) if scored else 0.0
    extraction = _mean(
        [row.extraction_confidence for row in rows], 0.0
    )
    ocr_value = None
    if runner.ocr_result is not None:
        ocr_value = float(runner.ocr_result.document.mean_confidence)
    rubric_value = _mean(
        [1.0 if not row.review_reasons else 0.5 for row in scored], None
    )
    estimate = runner.confidence_engine.estimate_paper(
        ocr=ocr_value,
        answer_extraction=extraction,
        semantic=semantic,
        rubric=rubric_value,
        agent_agreement=agreement,
    )
    evidence = {
        "overall": estimate.overall,
        "category": estimate.category,
        "components": {
            component.name: {
                "value": component.value,
                "weight": component.weight,
                "category": component.category,
            }
            for component in estimate.components
        },
    }
    return evidence, estimate.category


def _paper_artifacts(runner: LocalExamRunner) -> dict:
    artifacts: dict = {"source": runner.source}
    if runner.ocr_result is not None:
        artifacts.update(
            {
                "pages": len(runner.ocr_result.document.pages),
                "blocks": sum(len(p.blocks) for p in runner.ocr_result.document.pages),
                "mean_confidence": runner.ocr_result.document.mean_confidence,
                "ocr_status": runner.ocr_result.status.value,
            }
        )
    if runner.sheet is not None:
        artifacts["issues"] = [i.value for i in runner.sheet.issues]
    return artifacts


def _call(
    fn: Callable[..., object], instruction: Optional[str]
) -> Tuple[Optional[dict], Optional[BaseException]]:
    """Invoke a node callable; return (payload, error) — never raise."""
    try:
        result = fn(instruction)
    except BaseException as exc:  # noqa: BLE001 - recovery loop classifies all
        return None, exc
    return result if isinstance(result, dict) else (None, None), None


def _record_escalation(
    runner: LocalExamRunner, node, outcome: RecoveryOutcome
) -> None:
    """Track a degraded per-question node so the reconciler routes to review."""
    if node.capability == EVALUATION_CAPABILITY:
        entry = runner.eval_node_map.get(node.id)
        if entry is not None:
            question, agent = entry
            if agent == "verifier":
                runner.verifier_eval_failed.add(question.question_id)
            else:
                runner.primary_eval_failed.add(question.question_id)
    elif node.capability == VERIFICATION_CAPABILITY:
        entry = runner.eval_node_map.get(node.id)
        if entry is not None:
            question, agent = entry
            if agent == "verifier":
                runner.verifier_eval_failed.add(question.question_id)


def _review_only_result(
    runner: LocalExamRunner,
    traces: List[NodeTrace],
    wave_count: int,
) -> ExamRunResult:
    """Review-only result for a paper whose structural stage escalated.

    No rows, no marks: the recovery ladder is exhausted, so nothing has been
    graded -- that is the honest state, not an automatic zero.
    """
    reasons: List[EvalReviewReason] = []
    for outcome in runner.recovery_outcomes:
        reason = review_reason_for(outcome.failure_class)
        if reason not in reasons:
            reasons.append(reason)
    if not reasons:
        reasons.append(EvalReviewReason.RECOVERY_FAILED)

    return ExamRunResult(
        task_id=runner.task.task_id,
        paper_id=runner.task.paper.paper_id,
        exam_title=runner.task.exam.title,
        student=runner.identity or {},
        rows=[],
        total_marks=0.0,
        max_marks=round(
            sum(q.max_marks for q in runner.task.exam.questions), 4
        ),
        confidence=0.0,
        confidence_category="low",
        confidence_evidence={},
        escalations=runner.escalation_count,
        recovery=[o.to_dict() for o in runner.recovery_outcomes],
        review_reasons=[r.value for r in reasons],
        needs_review=True,
        status="review",
        source=runner.source,
        artifacts=_paper_artifacts(runner),
        trace=ExecutionTrace(waves=wave_count, nodes=traces),
    )


def _handle_ocr(runner: LocalExamRunner, inputs: dict, *, node_id: str) -> dict:
    runner.ensure_ocr()
    assert runner.ocr_result is not None
    return {
        "document": runner.ocr_result.model_dump(mode="json"),
        "summary": runner.ocr_result.summary,
    }


def _handle_layout(runner: LocalExamRunner, inputs: dict, *, node_id: str) -> dict:
    runner.ensure_ocr()
    assert runner.ocr_result is not None
    regions = []
    for page in runner.ocr_result.document.pages:
        for region in page.regions:
            regions.append(
                {
                    "page": page.page_no,
                    "type": region.region_type.value,
                    "label": region.label,
                    "bbox": list(region.bbox),
                    "confidence": region.confidence,
                }
            )
    return {"regions": regions, "region_count": len(regions)}


def _handle_sid(runner: LocalExamRunner, inputs: dict, *, node_id: str) -> dict:
    runner.ensure_ocr()
    assert runner.ocr_result is not None
    student, issues = extract_student_identity(
        runner.ocr_result.document, runner.roster
    )
    runner.identity = student.model_dump()
    return {
        "student": student.model_dump(),
        "issues": [i.value for i in issues],
    }


def _handle_qseg(runner: LocalExamRunner, inputs: dict, *, node_id: str) -> dict:
    runner.ensure_ocr()
    assert runner.ocr_result is not None
    sheet = structure_sheet(
        runner.ocr_result.document,
        config=runner.task.exam,
        roster=runner.roster,
    )
    runner.sheet = sheet
    return {
        "sheet": sheet.to_plan_json(),
        "status": sheet.status.value,
        "issues": [i.value for i in sheet.issues],
        "review_reasons": [r.value for r in sheet.review_reasons],
    }


def _handle_eval_primary(runner: LocalExamRunner, inputs: dict, *, node_id: str) -> dict:
    return runner.dispatch_eval(node_id, "primary").to_plan_json()


def _handle_eval_verifier(runner: LocalExamRunner, inputs: dict, *, node_id: str) -> dict:
    return runner.dispatch_eval(node_id, "verifier").to_plan_json()


def _handle_recon(runner: LocalExamRunner, inputs: dict, *, node_id: str) -> dict:
    primary: Optional[AgentEvaluation] = None
    verifier: Optional[AgentEvaluation] = None
    for payload in inputs.values():
        if not payload or not isinstance(payload, dict) or "agent" not in payload:
            continue
        evaluation = AgentEvaluation(**payload)
        if evaluation.agent == "verifier":
            verifier = evaluation
        elif evaluation.agent == "primary":
            primary = evaluation
    question_id = runner.question_lookup.get(node_id)
    if question_id is None:
        raise ExamOrchestrationError(
            f"reconciliation node '{node_id}' is not mapped to a question"
        )
    question = _question_for(runner, question_id)

    if primary is None:
        if question_id in runner.primary_eval_failed:
            result = escalated_reconciliation(runner, question, "primary")
            runner.recons[question_id] = result
            return result.to_plan_json()
        raise ExamOrchestrationError(
            "reconciliation received no primary evaluation from its parents"
        )

    if not runner.settings.two_agent_evaluation:
        verifier = None
    result = reconcile_agents(question_id, primary, verifier, runner.settings)

    if question_id in runner.verifier_eval_failed:
        result.review_reasons.append(EvalReviewReason.RECOVERY_FAILED)

    runner.recons[question_id] = result
    return result.to_plan_json()


def _handle_report(runner: LocalExamRunner, inputs: dict, *, node_id: str) -> dict:
    rows = runner.question_rows()
    if not rows:
        raise ExamOrchestrationError("report generation received no reconciled rows")

    review_reasons: List[str] = []
    for row in rows:
        for reason in row.review_reasons:
            if reason not in review_reasons:
                review_reasons.append(reason)
    if runner.ocr_result is not None:
        for reason in runner.ocr_result.review_reasons:
            value = reason.value
            if value not in review_reasons:
                review_reasons.append(value)
    if runner.sheet is not None:
        for reason in runner.sheet.review_reasons:
            value = reason.value
            if value not in review_reasons:
                review_reasons.append(value)

    scored = [row for row in rows if row.marks is not None]
    total = round_half_up(sum(row.marks for row in scored))
    max_marks = round(sum(row.max_marks for row in rows), 4)
    confidence = _mean([row.confidence for row in scored], 0.0)
    needs_review = bool(review_reasons)

    status = "ok"
    if runner.ocr_result is not None and runner.ocr_result.status == OcrStatus.REVIEW:
        status = "review"
    elif runner.sheet is not None and runner.sheet.status == SheetStatus.REVIEW:
        status = "review"
    elif needs_review:
        status = "degraded"
    if needs_review and status == "degraded":
        status = "review"

    confidence_evidence, confidence_category = _paper_confidence(runner, rows)

    return {
        "task_id": runner.task.task_id,
        "paper_id": runner.task.paper.paper_id,
        "exam_title": runner.task.exam.title,
        "student": runner.identity or {},
        "rows": [row.model_dump() for row in rows],
        "total_marks": total,
        "max_marks": max_marks,
        "confidence": confidence,
        "confidence_category": confidence_category,
        "confidence_evidence": confidence_evidence,
        "escalations": runner.escalation_count,
        "recovery": [o.to_dict() for o in runner.recovery_outcomes],
        "review_reasons": review_reasons,
        "needs_review": needs_review,
        "status": status,
        "source": runner.source,
        "artifacts": _paper_artifacts(runner),
    }


_RESOURCE_HANDLERS: Dict[str, Callable] = {
    OCR_CAPABILITY: _handle_ocr,
    LAYOUT_CAPABILITY: _handle_layout,
    STUDENT_ID_CAPABILITY: _handle_sid,
    SEGMENTATION_CAPABILITY: _handle_qseg,
    EVALUATION_CAPABILITY: _handle_eval_primary,
    VERIFICATION_CAPABILITY: _handle_eval_verifier,
    RECONCILIATION_CAPABILITY: _handle_recon,
    REPORT_CAPABILITY: _handle_report,
}


def register_local_exam_transport(
    registry: CapabilityRegistry, runner: LocalExamRunner
) -> List[CapabilityManifest]:
    """Register the wired local run_fns under the exam resource ids.

    Idempotent: re-registering overwrites the run_fn (and manifest) so an
    orchestrator executing again with a fresh runner upgrades the same ids.
    """
    manifests = []
    for manifest in build_local_exam_manifests():
        resource_id = manifest.resource_id

        def _make_run(resource_id: str):
            def run(inputs, instruction=None):
                payload = inputs if isinstance(inputs, dict) else {}
                return runner.dispatch(resource_id, payload, node_id=instruction)

            return run

        registry.register(manifest, _make_run(resource_id))
        manifests.append(manifest)
    return manifests


# ---------------------------------------------------------------------------
# The AOS controller: task -> capability requirements -> registry ->
# dynamic selection -> DAG waves -> execution -> result
# ---------------------------------------------------------------------------


def build_exam_registry(
    pessimising_factor: float = 1.0,
    optimising_factor: float = 1.0,
) -> CapabilityRegistry:
    """A registry containing the exam declared resources (upgraded to wired
    by `ExamOrchestrator.execute` before a run)."""
    from aos_v0.exam.resources import register_exam_resources

    registry = CapabilityRegistry(
        pessimising_factor=pessimising_factor,
        optimising_factor=optimising_factor,
    )
    register_exam_resources(registry)
    return registry


class ExamOrchestrator:
    """Runs one ExamEvaluationTask's DAG against the Capability Registry.

    Flow (plan Phase 8 architecture): the controller turns the task into a
    capability requirements snapshot (the DAG + per-node Capability DNA),
    selects a model for every node through the registry's continuous scorer,
    and executes the wave-ordered DAG, binding each node to the selected
    resource's registered run_fn. The result is the plan-shaped
    `ExamRunResult` plus a full `ExecutionTrace` of the dynamic selection.
    """

    def __init__(
        self,
        registry: Optional[CapabilityRegistry] = None,
    ) -> None:
        self.registry = registry or build_exam_registry()

    # -- execution -----------------------------------------------------------

    def execute(
        self,
        task: ExamEvaluationTask,
        *,
        source: Optional[str] = None,
        roster: Optional[Roster] = None,
        ocr_document: Optional[OcrDocument] = None,
        settings: Optional[EvaluationSettings] = None,
        runner: Optional[LocalExamRunner] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> ExamRunResult:
        runner = runner or LocalExamRunner(
            task,
            source=source,
            roster=roster,
            ocr_document=ocr_document,
            settings=settings,
        )
        if runner.source is None and runner.ocr_result is None:
            raise ExamOrchestrationError(
                "no OCR evidence: pass source=<path> or ocr_document=<OcrDocument>"
            )
        register_local_exam_transport(self.registry, runner)

        graph = task.build_graph()
        waves = build_waves(graph)
        nodes_by_id = {node.id: node for node in graph.nodes}

        runner.eval_node_map = {
            _node_id("eval", _node_id("q", question.question_id), suffix): (
                question,
                "primary" if suffix == "a" else "verifier",
            )
            for question in task.exam.questions
            for suffix in ("a", "b")
        }

        qkey_by_recon = {
            _node_id("recon", _node_id("q", question.question_id)): question.question_id
            for question in task.exam.questions
        }
        runner.question_lookup = qkey_by_recon

        recovery = ExamRecoveryManager(settings=runner.settings, log=log)

        outputs: Dict[str, dict] = {}
        traces: List[NodeTrace] = []

        for wave in waves:
            for node in wave:
                inputs = {
                    parent: outputs[parent]
                    for parent in node.depends_on
                    if parent in outputs
                }
                dna = dna_for_node(node.capability)
                if dna is None:
                    raise ExamOrchestrationError(
                        f"node '{node.id}' capability '{node.capability}' has no "
                        "Capability DNA template"
                    )
                modality = required_input_modality(dna.flags, node.capability)
                decision = self.registry.select(dna, required_modality=modality)
                resource_id = decision.resource_id
                manifest = self.registry.describe(resource_id)

                node.bound_resource = resource_id
                node.routing_mode = "dna"

                # Substitution candidates must be *capability-compatible* with
                # the node: the registry also lists lookalikes that share no
                # required flag (same model profile here), and letting one of
                # those "recover" the node would be fabrication -- a degraded
                # stage must degrade, not borrow unrelated output.
                required_flags = set(dna.flags)
                candidate_ids = [
                    candidate.resource_id
                    for candidate in decision.candidates
                    if candidate.resource_id != resource_id
                    and required_flags.issubset(
                        set(self.registry.describe(candidate.resource_id).capabilities)
                    )
                ]

                def primary_call(
                    instruction: Optional[str] = None,
                    _run_fn=self.registry.run_fn(resource_id),
                ) -> dict:
                    return _run_fn(inputs, instruction=instruction)

                def substitute_call(
                    candidate_resource_id: str,
                ) -> dict:
                    return self.registry.run_fn(candidate_resource_id)(
                        inputs, instruction=node.id
                    )

                started = time.monotonic()
                if runner.settings.recovery_enabled:
                    payload, outcome = recovery.run_node(
                        node.id,
                        primary_call,
                        candidates=candidate_ids,
                        instruction=node.id,
                        substitute_call=substitute_call,
                        low_confidence_threshold=runner.settings.confidence_low,
                    )
                else:
                    outcome = RecoveryOutcome(node_id=node.id)
                    payload, error = _call(primary_call, node.id)
                    if error is not None:
                        outcome.failure_class = RESOURCE_OUTAGE
                        outcome.symptom = f"{type(error).__name__}: {error}"
                        outcome.degraded = True
                        payload = gap_marker(node.id, outcome.failure_class, outcome.symptom)
                    recovery.outcomes.append(outcome)
                elapsed_ms = round((time.monotonic() - started) * 1000, 2)

                node.status = "degraded" if outcome.degraded else "done"
                serialised = json.dumps(payload)
                node.output = serialised
                outputs[node.id] = payload
                runner.recovery_outcomes.extend(
                    o for o in recovery.outcomes
                    if o.failure_class or o.recovered or o.degraded
                )
                recovery.outcomes.clear()

                if outcome.degraded:
                    runner.escalation_count += 1
                    node.status = "degraded"
                    _record_escalation(runner, node, outcome)

                traces.append(
                    NodeTrace(
                        node_id=node.id,
                        capability=node.capability,
                        resource_id=resource_id,
                        routing_mode=node.routing_mode,
                        score=round(decision.score, 4),
                        model=manifest.metadata.get("model", ""),
                        latency_ms=elapsed_ms,
                        status=node.status,
                        output_chars=len(serialised),
                        failure_class=outcome.failure_class,
                        recovered=outcome.recovered,
                        degraded=outcome.degraded,
                        attempt_count=len(outcome.attempts),
                    )
                )

                if outcome.degraded and node.capability in _STRUCTURAL_CAPABILITIES:
                    return _review_only_result(runner, traces, len(waves))

        report_id = _node_id("report", task.paper.paper_id)
        if report_id not in outputs:
            raise ExamOrchestrationError(
                "report node did not produce an output; the DAG did not complete"
            )
        result = ExamRunResult(**outputs[report_id])
        result.trace = ExecutionTrace(waves=len(waves), nodes=traces)
        return result