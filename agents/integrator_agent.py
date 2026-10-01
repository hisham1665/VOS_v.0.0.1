"""Assemble the finished :class:`RadiologyReport` from the executed graph.

The original integrator concatenated the text of whatever sink nodes existed.
That is fine for a chat answer and wrong for a medical report: the deliverable
has a fixed shape, and a partially failed run must still produce something the
clinician can act on -- clearly marked as degraded.

This agent therefore pulls typed data out of the nodes' artifacts rather than
re-parsing their prose, and records exactly which nodes failed.
"""

from __future__ import annotations

import time

from core.logging_utils import get_logger
from capabilities.hf_backend import models_used
from models import (
    DISCLAIMER,
    Finding,
    Graph,
    PatientStatus,
    RadiologyReport,
    Severity,
    Study,
    max_severity,
)

_log = get_logger("integrator")


class IntegratorAgent:
    """Turns an executed graph plus its study into the final report object."""

    def integrate(
        self, graph: Graph, study: Study, *, runtime_s: float | None = None
    ) -> RadiologyReport:
        started = time.perf_counter()

        report = RadiologyReport(
            study_id=study.study_id,
            patient=study.patient,
            modalities=study.modalities,
            image_count=len(study.images),
            document_count=len(study.documents),
            models_used=models_used(),
            input_manifest=self._input_manifest(study),
        )

        # --- gather artifacts across every successful node -----------------
        merged = self._merge_artifacts(graph)

        report.findings = self._build_findings(merged.get("findings", []))
        report.references = [str(r) for r in merged.get("references", [])][:12]
        report.source_reads = {
            str(key): str(value) for key, value in (merged.get("document_reads", {}) or {}).items()
        }
        report.document_types = {
            str(key): str(value) for key, value in (merged.get("document_types", {}) or {}).items()
        }

        report.screening = self._build_screening(merged)

        report.indication = (
            merged.get("indication")
            or study.patient.referring_question
            or study.patient.clinical_history
            or "Not stated by the referring clinician."
        )
        report.technique = merged.get("technique") or self._describe_technique(study)
        report.impression = merged.get("impression") or self._fallback_impression(graph, report)
        report.status = self._build_status(merged.get("status"), report.findings)

        # --- provenance and degradation -----------------------------------
        failed = [n for n in graph.nodes if n.status in {"failed", "skipped"}]
        report.degraded = bool(failed)
        report.node_trace = [
            {
                "id": node.id,
                "capability": node.capability,
                "status": node.status,
                "duration_s": node.duration_s,
                "error": node.error,
                "output_chars": len(node.output or ""),
            }
            for node in graph.nodes
        ]

        report.warnings = list(study.warnings)
        for document in study.documents:
            for warning in document.warnings:
                report.warnings.append(f"Document {document.document_id}: {warning}")
            for page in document.pages:
                for warning in page.warnings:
                    report.warnings.append(
                        f"Document {document.document_id} page {page.page_number}: {warning}"
                    )
        for item in study.skipped_files:
            report.warnings.append(f"File not analysed -- {item}")
        for node in failed:
            report.warnings.append(
                f"Pipeline stage '{node.id}' ({node.capability}) {node.status}: "
                f"{node.error or 'no detail'}"
            )
        for issue in merged.get("quality_issues", []):
            if isinstance(issue, dict):
                for source_ref, details in issue.items():
                    values = details if isinstance(details, list) else [details]
                    for detail in values:
                        report.warnings.append(f"Document quality ({source_ref}) -- {detail}")
            else:
                report.warnings.append(f"Image quality -- {issue}")
        for error in merged.get("screen_errors", []) + merged.get("confirm_errors", []):
            report.warnings.append(f"Screening model unavailable -- {error}")
        for discrepancy in merged.get("discrepancies", []):
            report.warnings.append(f"Model disagreement -- {discrepancy}")
        for image_id in merged.get("non_medical", []):
            report.warnings.append(
                f"'{image_id}' does not appear to be a medical image; "
                "its read should be disregarded."
            )
        for source_ref, document_type in report.document_types.items():
            if document_type == "non_medical":
                report.warnings.append(
                    f"'{source_ref}' was classified as non-medical; it is not evidence of normality."
                )

        report.runtime_s = round(
            runtime_s if runtime_s is not None else time.perf_counter() - started, 2
        )

        _log.info(
            "report assembled: %d finding(s), status=%s, degraded=%s",
            len(report.findings),
            report.status.overall_severity.value,
            report.degraded,
        )
        return report

    # ------------------------------------------------------------------
    def _merge_artifacts(self, graph: Graph) -> dict:
        """Collect artifacts from completed nodes, latest stage winning.

        Nodes are walked in graph order, so a later stage (the report node)
        overrides an earlier one (consolidation) for scalar keys, while list
        keys accumulate.
        """
        merged: dict = {}
        for node in graph.nodes:
            if node.status != "done":
                continue
            for key, value in node.artifacts.items():
                if key.startswith("_"):
                    continue
                if key == "findings":
                    # Raw page/image findings feed consolidation. Prefer the
                    # later reconciled list so the report has no duplicates.
                    if node.capability in {"cross_image_synthesis", "report_synthesis"}:
                        if value not in (None, [], {}):
                            merged[key] = value
                    elif isinstance(value, list):
                        merged[key] = (merged.get(key) or []) + value
                elif isinstance(value, list) and isinstance(merged.get(key), list):
                    merged[key] = merged[key] + value
                elif isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                elif value not in (None, "", [], {}):
                    merged[key] = value
        return merged

    @staticmethod
    def _build_screening(merged: dict) -> dict[str, dict]:
        """Join stage-1 screening and stage-2 confirmations per image."""
        screening = merged.get("screening") or {}
        confirmation = merged.get("confirmation") or {}
        out: dict[str, dict] = {}
        for image_id, entry in screening.items():
            if not isinstance(entry, dict):
                continue
            out[str(image_id)] = {
                **entry,
                "confirmations": list(confirmation.get(image_id, []) or []),
            }
        return out

    def _build_findings(self, raw_findings) -> list[Finding]:
        findings: list[Finding] = []
        for entry in raw_findings or []:
            if not isinstance(entry, dict) or not entry.get("label"):
                continue
            try:
                severity = Severity(str(entry.get("severity", "")).lower())
            except ValueError:
                severity = Severity.INDETERMINATE
            findings.append(
                Finding(
                    label=str(entry["label"]),
                    description=str(entry.get("description", "")),
                    severity=severity,
                    confidence=self._confidence(entry.get("confidence")),
                    image_ids=[str(i) for i in entry.get("image_ids", [])],
                    document_ids=[str(i) for i in entry.get("document_ids", [])],
                    source_refs=[str(i) for i in entry.get("source_refs", [])],
                    source=str(entry.get("source", "vlm")),
                    body_part=entry.get("body_part"),
                )
            )
        # Most urgent first: that is the order a clinician reads in.
        order = [
            Severity.CRITICAL,
            Severity.HIGH,
            Severity.MODERATE,
            Severity.INDETERMINATE,
            Severity.LOW,
            Severity.NORMAL,
        ]
        findings.sort(key=lambda f: order.index(f.severity))
        return findings

    @staticmethod
    def _confidence(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return min(1.0, max(0.0, number))

    @staticmethod
    def _input_manifest(study: Study) -> list[dict]:
        manifest = []
        for image in study.images:
            manifest.append({
                "id": image.image_id,
                "path": image.source_path,
                "kind": "image",
                "format": "dicom" if image.is_dicom else image.source_path.rsplit(".", 1)[-1].lower(),
                "analysed": True,
            })
        for document in study.documents:
            manifest.append({
                "id": document.document_id,
                "path": document.source_path,
                "kind": "document",
                "format": document.format,
                "pages": document.page_count,
                "analysed": bool(document.pages),
            })
        for skipped in study.skipped_files:
            manifest.append({"kind": "skipped", "detail": skipped, "analysed": False})
        return manifest

    def _build_status(self, raw_status, findings: list[Finding]) -> PatientStatus:
        # No findings means we learned nothing, not that the patient is well.
        # max_severity([]) returns NORMAL, which would be a dangerous default
        # here -- a run whose every stage failed must not read as "all clear".
        computed = (
            max_severity([f.severity for f in findings])
            if findings
            else Severity.INDETERMINATE
        )

        if not isinstance(raw_status, dict):
            return PatientStatus(
                overall_severity=computed,
                summary=(
                    "Status synthesis did not complete; severity was derived "
                    "from the extracted findings alone."
                ),
                triage="immediate"
                if computed is Severity.CRITICAL
                else "urgent"
                if computed is Severity.HIGH
                else "routine",
                red_flags=[f.label for f in findings if f.severity is Severity.CRITICAL],
                recommended_actions=["Manual radiologist review required."],
            )

        try:
            severity = Severity(str(raw_status.get("overall_severity", "")).lower())
        except ValueError:
            severity = computed

        # The findings are the source of truth: never report a status less
        # urgent than the worst finding.
        severity = max_severity([severity, computed])

        return PatientStatus(
            overall_severity=severity,
            summary=str(raw_status.get("summary", "")).strip(),
            triage=str(raw_status.get("triage", "routine")).lower(),
            red_flags=[str(f) for f in raw_status.get("red_flags", [])],
            recommended_actions=[
                str(a) for a in raw_status.get("recommended_actions", [])
            ],
        )

    def _describe_technique(self, study: Study) -> str:
        modalities = ", ".join(m.value for m in study.modalities) or "unspecified imaging"
        views = sorted({img.view for img in study.images if img.view})
        view_text = f", views: {', '.join(views)}" if views else ""
        return (
            f"{len(study.images)} image(s) and {len(study.documents)} document(s), "
            f"{modalities}{view_text}."
        )

    def _fallback_impression(self, graph: Graph, report: RadiologyReport) -> str:
        """Used when the report node did not complete."""
        if report.findings:
            labels = "; ".join(f.label for f in report.findings[:5])
            return (
                "Impression generation did not complete. Findings extracted from "
                f"the imaging: {labels}. Direct radiologist review is required."
            )
        salvaged = [
            node.output
            for node in graph.nodes
            if node.status == "done" and node.capability == "vision" and node.output
        ]
        if salvaged:
            return (
                "No consolidated impression was produced. Raw per-image reads "
                "are included below and require radiologist review.\n\n"
                + "\n\n".join(salvaged[:3])
            )
        return (
            "No interpretation could be produced for this study. "
            "Every analysis stage failed; see the warnings section."
        )


__all__ = ["IntegratorAgent", "DISCLAIMER"]
