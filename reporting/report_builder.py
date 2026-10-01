"""Render a :class:`RadiologyReport` for humans and for machines.

Two outputs, from one object:

* **Markdown** -- what the doctor reads. Ordered the way a clinician triages:
  status and red flags first, evidence second, provenance last.
* **JSON** -- what an EHR/PACS integration or an audit consumes.

The disclaimer is emitted unconditionally, at the top, in both. It is not a
formatting choice.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from config import REPORT_DIR
from core.logging_utils import get_logger
from models import DISCLAIMER, Finding, RadiologyReport, Severity, Study

_log = get_logger("report-builder")

_SEVERITY_BADGE = {
    Severity.CRITICAL: "🔴 CRITICAL",
    Severity.HIGH: "🟠 HIGH",
    Severity.MODERATE: "🟡 MODERATE",
    Severity.LOW: "🔵 LOW",
    Severity.NORMAL: "🟢 NORMAL",
    Severity.INDETERMINATE: "⚪ INDETERMINATE",
}

_TRIAGE_TEXT = {
    "immediate": "**IMMEDIATE** -- review before the patient leaves the department.",
    "urgent": "**URGENT** -- review within the same clinical session.",
    "routine": "**ROUTINE** -- review at the next scheduled opportunity.",
}


def render_markdown(report: RadiologyReport, study: Study | None = None) -> str:
    """Render the full clinician-facing report."""
    lines: list[str] = []

    lines.append(f"# Preliminary Imaging Report -- {report.study_id}")
    lines.append("")
    lines.append(f"> ⚠️ **{DISCLAIMER}**")
    lines.append("")

    if report.degraded:
        lines.append(
            "> 🟠 **DEGRADED RUN** -- one or more analysis stages did not "
            "complete. This report is incomplete; see *Run diagnostics*."
        )
        lines.append("")

    # --- the bottom line, first ------------------------------------------
    status = report.status
    lines.append("## Patient status")
    lines.append("")
    lines.append(
        f"| | |\n|---|---|\n"
        f"| **Overall** | {_SEVERITY_BADGE.get(status.overall_severity, status.overall_severity.value)} |\n"
        f"| **Triage** | {_TRIAGE_TEXT.get(status.triage, status.triage)} |\n"
        f"| **Images** | {report.image_count} "
        f"({', '.join(m.value for m in report.modalities) or 'unknown'}) |\n"
        f"| **Documents** | {report.document_count} |\n"
        f"| **Generated** | {_fmt_time(report.generated_at)} in {report.runtime_s:.1f}s |"
    )
    lines.append("")
    if status.summary:
        lines.append(f"{status.summary}")
        lines.append("")

    if status.red_flags:
        lines.append("### 🚩 Red flags")
        lines.extend(f"- {flag}" for flag in status.red_flags)
        lines.append("")

    if status.recommended_actions:
        lines.append("### Recommended next steps")
        lines.extend(f"1. {action}" for action in status.recommended_actions)
        lines.append("")

    # --- the report proper ------------------------------------------------
    lines.append("## Clinical details")
    lines.append("")
    patient = report.patient
    detail_rows = [
        ("Age", patient.age),
        ("Sex", patient.sex),
        ("History", patient.clinical_history),
        ("Referring question", patient.referring_question),
        ("Indication", report.indication),
        ("Technique", report.technique),
    ]
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    for label, value in detail_rows:
        lines.append(f"| {label} | {value or '_not supplied_'} |")
    lines.append("")

    lines.append("## Findings")
    lines.append("")
    if report.findings:
        lines.append("| Severity | Finding | Location | Confidence | Seen on | Document sources |")
        lines.append("|---|---|---|---|---|---|")
        for finding in report.findings:
            lines.append(
                f"| {_SEVERITY_BADGE.get(finding.severity, finding.severity.value)} "
                f"| **{finding.label}** "
                f"| {finding.body_part or '-'} "
                f"| {_fmt_confidence(finding.confidence)} "
                f"| {', '.join(finding.image_ids) or '-'} "
                f"| {', '.join(finding.source_refs) or '-'} |"
            )
        lines.append("")
        for finding in report.findings:
            if finding.description:
                lines.append(f"- **{finding.label}** -- {finding.description}")
        lines.append("")
    else:
        lines.append("_No findings were extracted. See run diagnostics below._")
        lines.append("")

    lines.append("## Impression")
    lines.append("")
    lines.append(report.impression or "_Not generated._")
    lines.append("")

    if report.references:
        lines.append("## Reference context")
        lines.append("")
        lines.append(
            "_Background literature retrieved for the findings above. "
            "Not patient-specific advice._"
        )
        lines.append("")
        lines.extend(f"- {reference}" for reference in report.references)
        lines.append("")

    # --- documents and images and provenance ------------------------------
    if study is not None and study.documents:
        lines.append("## Documents analysed")
        lines.append("")
        lines.append("| Document | Format | Pages | Source | Warnings |")
        lines.append("|---|---|---:|---|---|")
        for document in study.documents:
            warnings = "; ".join(document.warnings) or "-"
            lines.append(
                f"| `{document.document_id}` | {document.format.upper()} "
                f"| {document.page_count} | `{document.source_path}` | {warnings} |"
            )
        lines.append("")
        if report.source_reads:
            lines.append("### Extracted document reads")
            lines.append("")
            for source_ref, read in sorted(report.source_reads.items()):
                lines.append(f"#### `{source_ref}`")
                lines.append("")
                lines.append(read)
                lines.append("")

    if study is not None and study.images:
        lines.append("## Images analysed")
        lines.append("")
        lines.append("| Image | Modality | Region | Size | Source |")
        lines.append("|---|---|---|---|---|")
        for image in study.images:
            lines.append(
                f"| `{image.image_id}` | {image.modality.value} "
                f"| {image.body_part or '-'} | {image.width}x{image.height} "
                f"| {'DICOM' if image.is_dicom else Path(image.source_path).suffix.lstrip('.').upper()} |"
            )
        lines.append("")

    if report.screening:
        lines.extend(_render_screening(report))

    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        lines.extend(f"- {warning}" for warning in report.warnings)
        lines.append("")

    lines.append("## Run diagnostics")
    lines.append("")
    lines.append("| Stage | Capability | Status | Time | Detail |")
    lines.append("|---|---|---|---|---|")
    for entry in report.node_trace:
        detail = entry.get("error") or f"{entry.get('output_chars', 0)} chars"
        duration = entry.get("duration_s")
        lines.append(
            f"| `{entry['id']}` | {entry['capability']} | {_status_badge(entry['status'])} "
            f"| {f'{duration:.2f}s' if duration else '-'} | {detail} |"
        )
    lines.append("")

    if report.models_used:
        lines.append("### Models used")
        lines.append("")
        for capability, model_id in sorted(report.models_used.items()):
            lines.append(f"- `{capability}` -> `{model_id}`")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"_{DISCLAIMER}_ Report generated by Medical AOS at "
        f"{_fmt_time(report.generated_at)}."
    )
    lines.append("")
    return "\n".join(lines)


_VERDICT_BADGE = {
    "supported": "✅ supported",
    "not_supported": "❌ not supported",
    "equivocal": "❔ equivocal",
    "unverifiable": "➖ unverifiable",
}


def _render_screening(report: RadiologyReport) -> list[str]:
    """Stage-1 candidates and stage-2 specialist verdicts, per image."""
    lines = [
        "## Screening cascade",
        "",
        "_Stage 1 routes each image to a screener trained for its modality and "
        "region; stage 2 re-checks each candidate with a disease-specific model. "
        "Research checkpoints trained on public 2D datasets -- corroborating "
        "evidence only. An unscreened image was not checked by any supervised model._",
        "",
        "| Image | Modality / region | Stage 1: screener -> candidates | Stage 2: specialist re-check |",
        "|---|---|---|---|",
    ]
    for image_id, entry in report.screening.items():
        where = f"{entry.get('modality', '-')} / {entry.get('region') or '?'}"
        if entry.get("status") != "screened":
            stage1 = f"**not screened** -- {entry.get('reason', '')}"
        else:
            candidates = entry.get("candidates") or []
            models = ", ".join(entry.get("models") or [])
            abnormal = entry.get("abnormal_score")
            found = (
                ", ".join(f"{c['condition']} {c['score']:.0%}" for c in candidates)
                if candidates
                else "nothing above threshold"
            )
            abnormal_text = f" (abnormality {abnormal:.0%})" if abnormal is not None else ""
            stage1 = f"`{models}`: {found}{abnormal_text}"
        checks = []
        for record in entry.get("confirmations") or []:
            badge = _VERDICT_BADGE.get(record.get("verdict"), record.get("verdict"))
            if record.get("verdict") == "unverifiable":
                checks.append(f"{record['target']}: {badge}")
            else:
                checks.append(
                    f"{record['target']}: {badge} ({record.get('probability', 0):.0%}, "
                    f"`{record.get('model')}`)"
                )
        stage2 = "<br>".join(checks) or "-"
        lines.append(f"| `{image_id}` | {where} | {stage1} | {stage2} |")
    lines.append("")
    return lines


def render_summary(report: RadiologyReport) -> str:
    """One-screen terminal summary, printed at the end of a CLI run."""
    status = report.status
    lines = [
        "",
        "=" * 68,
        f" PATIENT STATUS: {_SEVERITY_BADGE.get(status.overall_severity, '?')}"
        f"   |   TRIAGE: {status.triage.upper()}",
        "=" * 68,
    ]
    if status.summary:
        lines.append(f" {status.summary}")
        lines.append("-" * 68)
    if status.red_flags:
        lines.append(" RED FLAGS:")
        lines.extend(f"   ! {flag}" for flag in status.red_flags)
        lines.append("-" * 68)
    if report.findings:
        lines.append(f" FINDINGS ({len(report.findings)}):")
        for finding in report.findings[:8]:
            lines.append(
                f"   [{finding.severity.value:>13}] {finding.label}"
                f" ({_fmt_confidence(finding.confidence)})"
            )
        lines.append("-" * 68)
    if status.recommended_actions:
        lines.append(" NEXT STEPS:")
        lines.extend(f"   {i}. {a}" for i, a in enumerate(status.recommended_actions, 1))
        lines.append("-" * 68)
    if report.degraded:
        lines.append(" ** DEGRADED RUN -- some stages failed, see warnings **")
        lines.append("-" * 68)
    lines.append(f" {DISCLAIMER}")
    lines.append("=" * 68)
    return "\n".join(lines)


def write_report(
    report: RadiologyReport, study: Study | None = None, output_dir: Path | None = None
) -> dict[str, Path]:
    """Write the markdown and JSON report. Returns the paths written."""
    directory = Path(output_dir or REPORT_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    stamp = report.generated_at.strftime("%Y%m%d-%H%M%S")
    base = f"{report.study_id}-{stamp}"

    markdown_path = directory / f"{base}.md"
    json_path = directory / f"{base}.json"

    markdown_path.write_text(render_markdown(report, study), encoding="utf-8")
    json_path.write_text(report.to_json(), encoding="utf-8")

    _log.info("report written to %s", markdown_path)
    return {"markdown": markdown_path, "json": json_path}


def _fmt_confidence(confidence: float | None) -> str:
    if confidence is None:
        return "-"
    return f"{confidence:.0%}"


def _fmt_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def _status_badge(status: str) -> str:
    return {
        "done": "✅ done",
        "failed": "❌ failed",
        "skipped": "⏭️ skipped",
        "pending": "⏳ pending",
        "running": "🔄 running",
    }.get(status, status)


def findings_by_severity(report: RadiologyReport) -> dict[str, list[Finding]]:
    """Group findings for callers that want their own rendering."""
    grouped: dict[str, list[Finding]] = {}
    for finding in report.findings:
        grouped.setdefault(finding.severity.value, []).append(finding)
    return grouped
