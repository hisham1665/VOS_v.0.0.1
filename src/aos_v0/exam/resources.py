"""Exam-evaluation Capability Registry entries (Phase 1 deliverable).

Registers one declared resource per node type the Phase-0 graph skeleton
emits. Resource ids deliberately match the coarse capability strings in
`exam/models.py` (e.g. ``document_ocr``), mirroring the existing
`resource_registration.py` convention, so the exact-match fallback path
(`SubAgent._route_exact`) can resolve them even before DNA routing runs.

Like the HF embedding/rerank entries, every exam resource is registered as a
*declared interface* (``transport=declared``): it proves capability-based
selection but its run_fn raises a typed error if invoked. Real execution
transport (PaddleOCR, Transformers, vLLM) is Phase 3/4 work (gap G3). Opt-in:
`build_default_registry()` is untouched -- callers add these explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from aos_v0.core.capability_registry import (
    Availability,
    CapabilityManifest,
    CapabilityRegistry,
    IOSchema,
)
from aos_v0.providers.errors import ProviderError

from aos_v0.exam.model_selection import selection_for_capability


@dataclass(frozen=True)
class ExamResourceSpec:
    """One registered exam capability resource (declared interface)."""

    resource_id: str
    resource_class: str
    capabilities: List[str]
    input_type: str
    output_type: str
    capability_name: str      # plan-key lookup in EXAM_MODEL_SELECTION
    model: str                # primary model HF repo (from the selection)
    description: str


_EXAM_RESOURCE_SPECS: List[ExamResourceSpec] = [
    ExamResourceSpec(
        resource_id="document_ocr",
        resource_class="image",
        capabilities=["document.ocr", "document.layout"],
        input_type="image",
        output_type="text",
        capability_name="DOCUMENT_OCR",
        model="",
        description="OCR the answer sheet page into text blocks with bounding "
                    "boxes and per-block confidence",
    ),
    ExamResourceSpec(
        resource_id="document_layout",
        resource_class="image",
        capabilities=["document.layout", "document.ocr"],
        input_type="image",
        output_type="text",
        capability_name="DOCUMENT_LAYOUT",
        model="",
        description="Analyse OCR'd page layout: text, question, answer regions, "
                    "tables and diagrams",
    ),
    ExamResourceSpec(
        resource_id="student_id_extraction",
        resource_class="vlm",
        capabilities=["student_id.extraction", "document.ocr"],
        input_type="image",
        output_type="text",
        capability_name="STUDENT_ID_EXTRACTION",
        model="",
        description="Extract name / roll / register number and validate against "
                    "the roster",
    ),
    ExamResourceSpec(
        resource_id="question_segmentation",
        resource_class="llm",
        capabilities=["question.segmentation", "document.layout"],
        input_type="text",
        output_type="text",
        capability_name="QUESTION_SEGMENTATION",
        model="",
        description="Map answer blocks to question ids; handle out-of-order and "
                    "continuation answers",
    ),
    ExamResourceSpec(
        resource_id="semantic_answer_evaluation",
        resource_class="llm",
        capabilities=["semantic.answer_evaluation", "rubric.evaluation",
                      "concept.extraction"],
        input_type="text",
        output_type="text",
        capability_name="SEMANTIC_EVALUATION",
        model="",
        description="Evaluator agent (Agent 1): marks, satisfied/missing "
                    "concepts, reasoning, confidence",
    ),
    ExamResourceSpec(
        resource_id="answer_verification",
        resource_class="llm",
        capabilities=["answer.verification", "answer.extraction"],
        input_type="text",
        output_type="text",
        capability_name="SEMANTIC_EVALUATION",
        model="",
        description="Independent verifier agent (Agent 2) over the same evidence",
    ),
    ExamResourceSpec(
        resource_id="evaluation_reconciliation",
        resource_class="llm",
        capabilities=["evaluation.reconciliation", "semantic.answer_evaluation"],
        input_type="text",
        output_type="text",
        capability_name="EVALUATION_RECONCILIATION",
        model="",
        description="Compare both agents, run disagreement analysis, emit final "
                    "mark + confidence + review reason",
    ),
    ExamResourceSpec(
        resource_id="report_generation",
        resource_class="llm",
        capabilities=["report.generation", "confidence.estimation"],
        input_type="text",
        output_type="text",
        capability_name="GENERAL_REASONING",
        model="",
        description="Produce the final evaluation record for the paper",
    ),
]


def exam_model_for(capability_name: str) -> str:
    """HF repo of the primary candidate for a selection, if it exists."""
    selection = selection_for_capability(capability_name)
    if selection is None:
        return "unassigned"
    return selection.primary.hf_repo


def build_exam_manifests() -> List[CapabilityManifest]:
    """CapabilityManifest declarations for every exam resource (no run_fn)."""
    manifests = []
    for spec in _EXAM_RESOURCE_SPECS:
        model = spec.model or exam_model_for(spec.capability_name)
        manifests.append(
            CapabilityManifest(
                resource_id=spec.resource_id,
                resource_class=spec.resource_class,
                capabilities=list(spec.capabilities),
                input_schema=IOSchema(type=spec.input_type, format="plain"),
                output_schema=IOSchema(type=spec.output_type, format="plain"),
                availability=Availability(),
                risk_class="low",
                metadata={
                    "provider": "huggingface",
                    "model": model,
                    "interface": "declared",
                    "transport": "declared",
                    "domain": "exam",
                    "description": spec.description,
                    "declared": (
                        "declared-only interface for capability-based selection; "
                        "execution transport wired in Phase 3/4 (gap G3)"
                    ),
                },
            )
        )
    return manifests


def _declared_only_run(resource_id: str):
    """run_fn for declared exam resources -- raises a typed error if invoked."""

    def run(text, instruction=None):
        raise ProviderError(
            f"[capability-registry] exam resource '{resource_id}' is a declared "
            f"interface for capability-based selection; its execution transport "
            f"is not wired yet (Phase 3/4)"
        )

    return run


def register_exam_resources(registry: CapabilityRegistry) -> List[CapabilityManifest]:
    """Opt-in registration of the exam capability resources.

    The default pool is untouched: only a caller that explicitly wants the exam
    resources on the routing candidate set calls this.
    """
    manifests = build_exam_manifests()
    for manifest in manifests:
        registry.register(manifest, _declared_only_run(manifest.resource_id))
    return manifests


def exam_resource_ids() -> Dict[str, List[str]]:
    """resource_id -> capability flags, for reporting / tests."""
    return {spec.resource_id: list(spec.capabilities) for spec in _EXAM_RESOURCE_SPECS}