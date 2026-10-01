"""Domain models for Medical AOS.

Two families live here:

* **Orchestration** -- :class:`Node` / :class:`Graph` describe the task DAG the
  planner builds and the executor runs.
* **Clinical** -- :class:`StudyImage`, :class:`Study`, :class:`Finding` and
  :class:`RadiologyReport` describe what actually gets read and produced.

Everything is a pydantic model so a whole run can be serialised to JSON for
audit, replay, or hand-off to a PACS/EHR integration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ==========================================================================
# Orchestration
# ==========================================================================
NodeStatus = Literal["pending", "running", "done", "failed", "skipped"]


class Node(BaseModel):
    """One unit of work in the task graph."""

    id: str
    description: str
    capability: str
    depends_on: list[str] = Field(default_factory=list)
    # Best-effort joins may use successful parents even if another failed.
    allow_partial: bool = False
    # With allow_partial, at least one of *these* parents must have succeeded.
    # Consolidation lists the screening cascade as a parent, but cascade output
    # alone is not enough to build findings from; empty means "any parent".
    partial_requires: list[str] = Field(default_factory=list)
    input: Optional[str] = None
    output: Optional[str] = None
    status: NodeStatus = "pending"
    performed_by: Optional[str] = None
    error: Optional[str] = None
    duration_s: Optional[float] = None
    # Free-form structured payload a capability can attach alongside its text
    # output (e.g. classifier scores) without stuffing it into a string.
    artifacts: dict[str, Any] = Field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == "done"


class Graph(BaseModel):
    """A DAG of nodes plus the job description that produced it."""

    job: str
    nodes: list[Node] = Field(default_factory=list)
    deterministic: bool = False

    def by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    @property
    def failed_nodes(self) -> list[Node]:
        return [n for n in self.nodes if n.status == "failed"]


# ==========================================================================
# Clinical
# ==========================================================================
class Modality(str, Enum):
    """Imaging modality, either read from DICOM or inferred by CLIP."""

    XRAY = "X-ray"
    CT = "CT"
    MRI = "MRI"
    ULTRASOUND = "Ultrasound"
    MAMMOGRAPHY = "Mammography"
    PET = "PET"
    PATHOLOGY = "Pathology"
    UNKNOWN = "Unknown"


class Severity(str, Enum):
    """Clinical urgency attached to a finding or to the study overall."""

    CRITICAL = "critical"
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    NORMAL = "normal"
    INDETERMINATE = "indeterminate"


_SEVERITY_ORDER = [
    Severity.NORMAL,
    Severity.LOW,
    Severity.INDETERMINATE,
    Severity.MODERATE,
    Severity.HIGH,
    Severity.CRITICAL,
]


def max_severity(values: list["Severity"]) -> "Severity":
    """Return the most urgent severity in ``values`` (empty -> NORMAL)."""
    if not values:
        return Severity.NORMAL
    return max(values, key=_SEVERITY_ORDER.index)


class StudyImage(BaseModel):
    """A single decoded image from the study folder."""

    image_id: str
    source_path: str
    prepared_path: Optional[str] = None  # cached, resized, model-ready JPEG
    content_hash: str
    modality: Modality = Modality.UNKNOWN
    body_part: Optional[str] = None
    view: Optional[str] = None  # e.g. "PA", "AP", "lateral"
    width: int = 0
    height: int = 0
    is_dicom: bool = False
    # DICOM header fields, already de-identified when MAOS_DEIDENTIFY is on.
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Non-fatal problems: unreadable frame, missing header, downsampled, ...
    warnings: list[str] = Field(default_factory=list)

    @property
    def path_for_model(self) -> str:
        """Prefer the preprocessed copy; fall back to the original."""
        return self.prepared_path or self.source_path


class DocumentPage(BaseModel):
    """One physical page or bounded text chunk; numbering starts at one."""

    page_number: int = Field(ge=1)
    text: str = ""
    image_path: Optional[str] = None
    extraction: str = "text"
    warnings: list[str] = Field(default_factory=list)


class StudyDocument(BaseModel):
    """Extracted document, with source attribution retained for every page."""

    document_id: str
    source_path: str
    content_hash: str
    format: str
    pages: list[DocumentPage] = Field(default_factory=list)
    page_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class PatientContext(BaseModel):
    """Everything known about the patient that is not an image."""

    patient_id: Optional[str] = None
    age: Optional[str] = None
    sex: Optional[str] = None
    clinical_history: Optional[str] = None
    referring_question: Optional[str] = None
    prior_studies: list[str] = Field(default_factory=list)
    # Any .txt/.json/.md notes found alongside the images.
    notes: list[str] = Field(default_factory=list)


class Study(BaseModel):
    """One imaging study: a folder of images plus patient context."""

    study_id: str
    source_dir: str
    images: list[StudyImage] = Field(default_factory=list)
    documents: list[StudyDocument] = Field(default_factory=list)
    patient: PatientContext = Field(default_factory=PatientContext)
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    skipped_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def modalities(self) -> list[Modality]:
        seen: list[Modality] = []
        for image in self.images:
            if image.modality not in seen:
                seen.append(image.modality)
        return seen


class Finding(BaseModel):
    """One observation extracted from the imaging."""

    label: str
    description: str
    severity: Severity = Severity.INDETERMINATE
    confidence: Optional[float] = None  # 0..1 when a model reports a score
    image_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)  # e.g. labs.pdf#page=2
    source: str = "vlm"  # vlm | document_analysis | classifier | literature | manual
    body_part: Optional[str] = None


class PatientStatus(BaseModel):
    """The bottom line the referring doctor reads first."""

    overall_severity: Severity = Severity.INDETERMINATE
    summary: str = ""
    triage: str = "routine"  # immediate | urgent | routine
    red_flags: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


class RadiologyReport(BaseModel):
    """The structured deliverable handed back to the clinician."""

    study_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    patient: PatientContext = Field(default_factory=PatientContext)
    modalities: list[Modality] = Field(default_factory=list)
    image_count: int = 0
    document_count: int = 0
    source_reads: dict[str, str] = Field(default_factory=dict)
    document_types: dict[str, str] = Field(default_factory=dict)
    input_manifest: list[dict[str, Any]] = Field(default_factory=list)

    indication: str = ""
    technique: str = ""
    findings: list[Finding] = Field(default_factory=list)
    impression: str = ""
    status: PatientStatus = Field(default_factory=PatientStatus)
    references: list[str] = Field(default_factory=list)
    # Per-image screening cascade: {image_id: {modality, region, status, reason,
    # models, abnormal_score, candidates, confirmations}}.
    screening: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Provenance so a reviewer can see exactly how this was produced.
    models_used: dict[str, str] = Field(default_factory=dict)
    node_trace: list[dict[str, Any]] = Field(default_factory=list)
    degraded: bool = False  # True when some node failed and we carried on
    warnings: list[str] = Field(default_factory=list)
    runtime_s: float = 0.0

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


DISCLAIMER = (
    "AI-generated draft for clinician review. This report is decision *support* "
    "only: it is not a diagnosis, has not been validated for clinical use, and "
    "must be verified by a qualified clinician (and a radiologist for imaging) "
    "before it informs patient care."
)


def path_str(path: Path | str) -> str:
    return str(Path(path).resolve())
