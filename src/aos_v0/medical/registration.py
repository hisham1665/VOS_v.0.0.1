"""Register the seven Medical AOS capabilities into an existing registry.

This is a *second* registration layer on top of the established pool:
``register_medical_resources(registry)`` mutates the same CapabilityRegistry the
kernel already owns (routing, admission control and recovery are the untouched
kernel paths). It never re-creates or replaces the default pool --
``build_default_registry()`` / ``build_hf_enabled_registry()`` stay frozen.
"""

from __future__ import annotations

from typing import Callable, Optional

from aos_v0.core.capability_registry import (
    Availability,
    CapabilityManifest,
    CapabilityRegistry,
    CostModel,
    IOSchema,
    LatencyModel,
)
from aos_v0.medical.capabilities import (
    medical_document_analysis_run,
    medical_folder_ingestion_run,
    medical_image_analysis_run,
    medical_laboratory_analysis_run,
    medical_patient_synthesis_run,
    medical_prescription_analysis_run,
    medical_report_analysis_run,
)

# resource_id (== coarse manager capability string) -> (run fn, [flags]).
_MEDICAL_CAPABILITIES: dict[
    str, tuple[Callable[[str, Optional[str]], str], list[str]]
] = {
    "medical_folder_ingestion": (
        medical_folder_ingestion_run,
        ["medical.folder_ingestion"],
    ),
    "medical_document_analysis": (
        medical_document_analysis_run,
        ["medical.document_analysis"],
    ),
    "medical_laboratory_analysis": (
        medical_laboratory_analysis_run,
        ["medical.laboratory_analysis"],
    ),
    "medical_image_analysis": (
        medical_image_analysis_run,
        ["medical.image_analysis"],
    ),
    "medical_prescription_analysis": (
        medical_prescription_analysis_run,
        ["medical.prescription_analysis"],
    ),
    "medical_report_analysis": (
        medical_report_analysis_run,
        ["medical.report_analysis"],
    ),
    "medical_patient_synthesis": (
        medical_patient_synthesis_run,
        ["medical.patient_synthesis"],
    ),
}

# Estimated cost/latency priors for the medical capabilities (per PDF/image
# batch). Deliberately below the HF NER / MedGemma per-call resources so routing
# prefers this self-contained wrapper over the raw model resource for the same
# flag; the raw model resources remain as automatic substitutes.
_COST = 0.001
_LAT_P50, _LAT_P95 = 1500, 6000


def register_medical_resources(registry: CapabilityRegistry) -> CapabilityRegistry:
    """Add the seven medical capabilities into ``registry`` (in place).

    Registers under resource_ids matching the coarse capability strings so both
    DNA routing and the exact-match fallback (`find_by_capability`) resolve to
    the same functions the manager plans against. Returns the registry for
    chaining.
    """
    for resource_id, (run_fn, capabilities) in _MEDICAL_CAPABILITIES.items():
        if any(m.resource_id == resource_id for m in registry.manifests()):
            continue  # idempotent against double registration
        manifest = CapabilityManifest(
            resource_id=resource_id,
            resource_class="tool",
            capabilities=capabilities,
            input_schema=IOSchema(type="text", format="plain"),
            output_schema=IOSchema(type="text", format="plain"),
            cost_model=CostModel(unit="per_call", estimate_usd=_COST),
            latency_model=LatencyModel(p50_ms=_LAT_P50, p95_ms=_LAT_P95),
            quality_priors={c: 0.99 for c in capabilities},
            availability=Availability(status="up", rate_limit_rpm=60),
            risk_class="medium",  # clinical-ish output; keep reviewable
            metadata={
                "provider": "medical-aos",
                "transport": "wired",
                "feature": "medical-assistant",
            },
        )
        registry.register(manifest, run_fn)
    return registry