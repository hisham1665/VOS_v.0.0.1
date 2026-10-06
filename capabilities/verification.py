"""Registered evidence checks selected by generic runtime recovery.

These checks report their scope: provenance/numeric/temporal checks do not prove
that a clinical inference is correct. A rendered-page reread is separately
identified as model interpretation, never independent clinical ground truth.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from capabilities.registry import CapabilityRequest, CapabilityResult, register
from core.clinical_reasoning import authoritative_material, source_material
from core.evidence_checks import unsupported_numbers
from core.routing import CapabilityDNA

_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.])")
_DATE = re.compile(r"\b\d{4}[-/]\d{2}[-/]\d{2}\b")


def request_verification(skill: str, reason: str, *, artifacts: dict | None = None) -> dict:
    return {"reason": reason, "description": reason,
            "requirements": {"skills": [skill], "outputs": ["verification"]},
            "artifacts": artifacts or {}}


@register("document_verification", "Verify uncertain extraction against source text or a rendered page.",
          dna=CapabilityDNA("document_verification", skills=("verify_documents",),
                            inputs=("documents", "text"), outputs=("verification",), domain="medical"))
def verify_documents(request: CapabilityRequest) -> CapabilityResult:
    from capabilities import document_analysis

    records, verified_reads, models = [], {}, set()
    for document, page in request.documents():
        ref = f"{document.document_id}#page={page.page_number}"
        record = {"kind": "document_extraction", "source_refs": [ref], "issues": [],
                  "scope": "source provenance and exact numeric tokens; clinical interpretation unverified"}
        if page.image_path:
            try:
                visual_request = CapabilityRequest(instruction="Recheck uncertain extraction against the visible page",
                                                   study=request.study, artifacts={"verify_page_image": True})
                analysis = document_analysis._analyze_page(document, page, ref, visual_request)
                verified_reads[ref] = analysis["raw"]
                models.add(analysis["model"])
                record.update(status="reviewed", method="rendered-page model reread", model=analysis["model"],
                              scope="automated visual recheck; not independent ground truth",
                              issues=analysis["quality_issues"])
                original = [f for f in request.artifacts.get("findings", []) if ref in f.get("source_refs", [])]
                if {(f.get("label"), f.get("description")) for f in original} != {
                    (f.get("label"), f.get("description")) for f in analysis["findings"]
                }:
                    record["issues"].append("Visual and original extraction differ; reconcile with the original page before clinical use")
            except Exception as exc:
                record.update(status="unavailable", method="rendered-page model reread",
                              issues=[f"Visual verification unavailable: {type(exc).__name__}: {exc}"])
        elif page.text.strip():
            issues = []
            for finding in request.artifacts.get("findings", []):
                if ref in finding.get("source_refs", []):
                    missing = unsupported_numbers(finding.get("description", ""), page.text)
                    if missing:
                        issues.append(f"{finding.get('label')}: numeric values absent from native source: {missing}")
            record.update(status="limited", method="native-text numeric audit", issues=issues +
                          ["No rendered page available for independent extraction recheck"])
        else:
            record.update(status="unavailable", issues=["Source page has no readable representation"])
        records.append(record)
    if not records:
        records = [{"kind": "document_extraction", "status": "unavailable", "issues": ["No requested pages available"]}]
    return CapabilityResult(text="Document verification:\n" + json.dumps(records, ensure_ascii=False),
                            artifacts={"verification": records, "verification_document_reads": verified_reads,
                                       "_models_used": {"document_verification": ", ".join(sorted(models))} if models else {}})


def temporal_candidates(findings: list[dict], sources: dict[str, str]) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for finding in findings:
        description = str(finding.get("description", ""))
        if not _NUMBER.search(description):
            continue
        key = str(finding.get("label", "")).strip().casefold()
        for ref in finding.get("source_refs", []):
            dates = sorted(set(_DATE.findall(sources.get(ref, ""))))
            # Multiple dates on a page do not establish which one dates a result.
            grouped[key].append({"source_ref": ref, "date": dates[0].replace("/", "-") if len(dates) == 1 else None,
                                 "description": description})
    return {key: rows for key, rows in grouped.items()
            if len({row["source_ref"] for row in rows}) > 1
            and len({row["description"].casefold() for row in rows}) > 1}


@register("temporal_comparison", "Compare source-dated repeated observations without inferring diagnosis.",
          dna=CapabilityDNA("temporal_comparison", skills=("compare_temporal_evidence",),
                            inputs=("text", "findings"), outputs=("verification",), domain="medical",
                            cost_per_call=0, expected_latency_s=0.1, memory_gb=0.05))
def compare_temporal(request: CapabilityRequest) -> CapabilityResult:
    sources = source_material(request.study, request.artifacts)
    groups = temporal_candidates(request.artifacts.get("findings", []), sources)
    records = []
    for label, rows in sorted(groups.items()):
        dated = all(row["date"] for row in rows)
        records.append({"kind": "temporal_comparison", "label": label,
                        "status": "reviewed" if dated else "limited",
                        "observations": sorted(rows, key=lambda row: (row["date"] or "9999", row["source_ref"])),
                        "issues": [] if dated else ["Result dates missing or ambiguous; do not assume chronology"],
                        "scope": "Dates are page-level hints; different values can reflect time, units, methods or extraction errors. No trend/diagnosis inferred."})
    return CapabilityResult(text="Temporal evidence comparison:\n" + json.dumps(records, ensure_ascii=False),
                            artifacts={"verification": records, "temporal_context": records})


@register("evidence_verification", "Audit evidence references and exact numeric claims against native sources.",
          dna=CapabilityDNA("evidence_verification", skills=("verify_evidence",),
                            inputs=("text", "findings"), outputs=("verification",), domain="medical",
                            cost_per_call=0, expected_latency_s=0.1, memory_gb=0.05))
def verify_evidence(request: CapabilityRequest) -> CapabilityResult:
    sources = source_material(request.study, request.artifacts)
    native = authoritative_material(request.study)
    known_refs = set(native) | set(request.artifacts.get("reads", {})) | set(request.artifacts.get("document_reads", {}))
    issues = list(request.artifacts.get("validation_issues", []))
    claims = list(request.artifacts.get("findings", []))
    assessment = request.artifacts.get("clinical_assessment", {})
    for hypothesis in assessment.get("differential", []):
        for field in ("supporting_evidence", "opposing_evidence"):
            claims.extend({"label": hypothesis.get("condition"), "description": e.get("detail", ""),
                           "source_refs": e.get("source_refs", [])} for e in hypothesis.get(field, []))
    for claim in claims:
        refs = claim.get("source_refs", []) + claim.get("image_ids", [])
        for ref in refs:
            if ref not in known_refs:
                issues.append(f"{claim.get('label')}: unknown source {ref}")
        text = "\n".join(native[ref] for ref in refs if ref in native)
        if text:
            missing = unsupported_numbers(claim.get("description", ""), text)
            if missing:
                issues.append(f"{claim.get('label')}: numeric claims absent from cited native evidence: {missing}")
    record = {"kind": "evidence_audit", "status": "limited" if issues else "reviewed",
              "issues": list(dict.fromkeys(issues)),
              "scope": "provenance/numeric audit only; entailment and clinical correctness require review"}
    return CapabilityResult(text="Evidence verification:\n" + json.dumps(record, ensure_ascii=False),
                            artifacts={"verification": [record]})
