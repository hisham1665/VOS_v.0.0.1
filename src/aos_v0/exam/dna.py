"""Exam-evaluation Capability DNA definitions (Phase 1 deliverable).

Per DOC1 5.2 / the implementation plan, "each capability should have its own
Capability DNA" (Capabilities to Add to AOS). A CapabilityDNA here is a
*per-subtask requirement vector*: the exam pipeline declares, for every
capability it needs, the discrete flag(s), ordinal complexity scores and
continuous constraints a serving resource must match.

These templates are the single source the Phase-8 dynamic-orchestration
adapter will hydrate nodes with (`build_graph()` currently emits coarse
capability strings only). They are deliberately conservative:

  * Flags come only from `CAPABILITY_FLAGS`, so every template validates
    against the kernel vocabulary the moment it is constructed.
  * Constraints use architecture defaults where no measurement exists; the
    MODEL_BENCHMARK phase will overwrite cost/latency/quality priors with
    telemetry without touching this module's structure.
"""

from __future__ import annotations

from typing import Dict, Optional

from aos_v0.core.models import CapabilityDNA, DNAConstraints, DNAOrdinals

# Graph capability names the Phase-0 skeleton emits (mirrors the constants in
# models.py) mapped to the DNA template they should hydrate with.
_CAPABILITY_FLAG_MAP: Dict[str, str] = {
    "document_ocr": "document.ocr",
    "document_layout": "document.layout",
    "student_id_extraction": "student_id.extraction",
    "question_segmentation": "question.segmentation",
    "semantic_answer_evaluation": "semantic.answer_evaluation",
    "answer_verification": "answer.verification",
    "evaluation_reconciliation": "evaluation.reconciliation",
    "report_generation": "report.generation",
}


def _dna(flag: str, *, depth: int, horizon: int, tool: int,
         latency_ms: int, min_quality: float, cost_usd: float = 1.0,
         extra_flags: Optional[list[str]] = None) -> CapabilityDNA:
    """Build one CapabilityDNA template with a defined *primary* flag."""
    flags = [flag] + (extra_flags or [])
    return CapabilityDNA(
        flags=flags,
        ordinals=DNAOrdinals(
            reasoning_depth=depth,
            planning_horizon=horizon,
            tool_complexity=tool,
            parallelizability=1,
        ),
        constraints=DNAConstraints(
            cost_ceiling_usd=cost_usd,
            latency_slo_ms=latency_ms,
            min_quality=min_quality,
            risk_tolerance="low",
        ),
        extracted_by="exam.dna",
    )


# One template per exam capability flag. Reasoning depth tracks how strong a
# model the subtask genuinely needs: OCR/extraction is shallow, rubric
# evaluation and reconciliation need deep, self-checking reasoning. Latency
# SLOs and quality floors are starting points for the benchmark, not
# measurements. All values are conservative (permissive) so an unmeasured
# node can still execute rather than being rejected by admission control.
EXAM_DNA_TEMPLATES: Dict[str, CapabilityDNA] = {
    "document.ocr": _dna(
        "document.ocr",
        depth=2, horizon=0, tool=0,
        latency_ms=60_000, min_quality=0.85,
    ),
    "handwriting.ocr": _dna(
        "handwriting.ocr",
        depth=2, horizon=0, tool=0,
        latency_ms=90_000, min_quality=0.80,
    ),
    "document.layout": _dna(
        "document.layout",
        depth=2, horizon=1, tool=0,
        extra_flags=["document.ocr"],
        latency_ms=30_000, min_quality=0.80,
    ),
    "student_id.extraction": _dna(
        "student_id.extraction",
        depth=2, horizon=1, tool=0,
        extra_flags=["document.ocr"],
        latency_ms=30_000, min_quality=0.90,
    ),
    "question.segmentation": _dna(
        "question.segmentation",
        depth=3, horizon=2, tool=1,
        extra_flags=["document.layout"],
        latency_ms=45_000, min_quality=0.85,
    ),
    "answer.extraction": _dna(
        "answer.extraction",
        depth=2, horizon=1, tool=0,
        extra_flags=["question.segmentation"],
        latency_ms=30_000, min_quality=0.85,
    ),
    "text.normalization": _dna(
        "text.normalization",
        depth=2, horizon=1, tool=0,
        extra_flags=["answer.extraction"],
        latency_ms=20_000, min_quality=0.85,
    ),
    "semantic.embedding": _dna(
        "semantic.embedding",
        depth=1, horizon=0, tool=1,
        latency_ms=5_000, min_quality=0.80, cost_usd=0.05,
    ),
    "concept.extraction": _dna(
        "concept.extraction",
        depth=3, horizon=2, tool=0,
        extra_flags=["semantic.embedding"],
        latency_ms=30_000, min_quality=0.85,
    ),
    "semantic.answer_evaluation": _dna(
        "semantic.answer_evaluation",
        depth=4, horizon=2, tool=1,
        extra_flags=["concept.extraction", "rubric.evaluation"],
        latency_ms=60_000, min_quality=0.90,
    ),
    "rubric.evaluation": _dna(
        "rubric.evaluation",
        depth=3, horizon=2, tool=1,
        latency_ms=45_000, min_quality=0.85,
    ),
    "mathematical.evaluation": _dna(
        "mathematical.evaluation",
        depth=4, horizon=2, tool=2,
        latency_ms=90_000, min_quality=0.90,
    ),
    "diagram.evaluation": _dna(
        "diagram.evaluation",
        depth=4, horizon=2, tool=1,
        extra_flags=["vision.understanding"],
        latency_ms=60_000, min_quality=0.85,
    ),
    "answer.verification": _dna(
        "answer.verification",
        depth=4, horizon=2, tool=1,
        extra_flags=["answer.extraction"],
        latency_ms=60_000, min_quality=0.90,
    ),
    "evaluation.reconciliation": _dna(
        "evaluation.reconciliation",
        depth=4, horizon=3, tool=1,
        extra_flags=["semantic.answer_evaluation", "answer.verification"],
        latency_ms=90_000, min_quality=0.90,
    ),
    "confidence.estimation": _dna(
        "confidence.estimation",
        depth=3, horizon=2, tool=1,
        latency_ms=30_000, min_quality=0.80,
    ),
    "batch.processing": _dna(
        "batch.processing",
        depth=1, horizon=2, tool=2,
        latency_ms=300_000, min_quality=0.80, cost_usd=5.0,
    ),
    "report.generation": _dna(
        "report.generation",
        depth=3, horizon=2, tool=1,
        extra_flags=["confidence.estimation"],
        latency_ms=45_000, min_quality=0.80,
    ),
    "csv.generation": _dna(
        "csv.generation",
        depth=1, horizon=1, tool=1,
        extra_flags=["report.generation"],
        latency_ms=15_000, min_quality=0.80,
    ),
}


def dna_for_capability(capability: str) -> Optional[CapabilityDNA]:
    """DNA template for a *graph* capability string (coarse name, Phase 0).

    Returns None for capability strings the exam domain does not own (e.g. the
    kernel's `summarization`), so callers can fall back to their existing
    extraction path.
    """
    flag = _CAPABILITY_FLAG_MAP.get(capability)
    return EXAM_DNA_TEMPLATES.get(flag) if flag else None


def dna_for_flag(flag: str) -> Optional[CapabilityDNA]:
    """DNA template keyed directly by a capability flag."""
    return EXAM_DNA_TEMPLATES.get(flag)