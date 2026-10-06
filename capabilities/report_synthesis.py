"""Produce the clinician-facing impression and patient status.

This is the last reasoning step. It takes the consolidated findings, the
literature context and the patient history, and answers the two questions the
referring doctor actually opened the report for: *what is going on*, and *what
do I do now*.

Severity is computed from the structured findings rather than parsed out of the
model's prose, so triage cannot drift just because the wording changed.
"""

from __future__ import annotations

import json

from core.logging_utils import get_logger
from core.clinical_reasoning import ASSESSMENT_SCHEMA, REASONING_RULES, authoritative_material, evidence_body, render_assessment, source_material, validate_assessment
from capabilities.hf_backend import chat_text, record_model, text_selection_trace
from core import cache
from capabilities.registry import CapabilityRequest, CapabilityResult, register
from models import Severity, max_severity

_log = get_logger("report")

_SYSTEM = (
    "You are a clinical reasoning consultant writing an integrated assessment "
    "for the referring clinician. You are concise, you commit to a "
    "view where the imaging supports one, and you hedge where it does not. "
    "You state clearly that this is an AI-generated draft requiring "
    "radiologist verification. You never state a definitive diagnosis that the "
    "findings do not support, and you never omit a finding marked critical."
)

_INSTRUCTION = """\
Write the impression and patient status for this study.

PATIENT CONTEXT:
{context}

CONSOLIDATED FINDINGS AND LITERATURE CONTEXT:
{body}

Return ONLY a JSON object, no prose and no code fence:
{{
  "indication": "why the study was requested, one line, from the history given",
  "technique": "what was imaged and how, from the modality information given",
  "impression": "2-4 sentences connecting findings across sources to possible diseases, with exact citations",
  "summary": "one sentence a busy clinician can read in five seconds",
  "triage": "immediate|urgent|routine",
  "red_flags": ["findings needing action before the patient leaves, if any"],
  "recommended_actions": ["concrete next steps: further imaging, labs, referral, follow-up interval"],
  {assessment_schema}
}}

Rules:
- "triage" must be "immediate" if any finding is critical, "urgent" if any is
  high, otherwise "routine".
- Recommended actions must be investigations or follow-up, never treatment
  prescriptions.
- Normal tests do not exclude all disease or explain unresolved symptoms.
- If no images were supplied, describe review of written reports, not direct image interpretation.
{reasoning_rules}
"""


@register(
    "report_synthesis",
    "Write the impression, patient status and recommended next steps.",
)
def run(request: CapabilityRequest) -> CapabilityResult:
    findings = (request.artifacts or {}).get("findings") or []
    sources = source_material(request.study, request.artifacts)
    # Findings already have a structured representation. Do not pay to send both
    # raw JSON reads and a second verbose rendering of the same evidence.
    body = evidence_body(findings, sources)
    references = request.artifacts.get("references", [])
    if references:
        body += "\nBACKGROUND (not patient evidence):\n" + json.dumps(references)[:2000]
    for key in ("verification", "temporal_context", "image_quality"):
        if request.artifacts.get(key):
            body += f"\n{key.upper()} (respect stated scope/limitations):\n" + json.dumps(request.artifacts[key], ensure_ascii=False)[:6000]
    if request.study and request.study.series:
        body += "\nSERIES CONTEXT (2D frame reads, not validated volume interpretation):\n" + json.dumps(
            [series.model_dump(mode="json") for series in request.study.series], ensure_ascii=False)[:4000]

    context_lines: list[str] = []
    if request.study is not None:
        study = request.study
        patient = study.patient
        for label, value in (
            ("Age", patient.age),
            ("Sex", patient.sex),
            ("Clinical history", patient.clinical_history),
            ("Referring question", patient.referring_question),
        ):
            if value:
                context_lines.append(f"{label}: {value}")
        context_lines.append(
            f"Images: {len(study.images)}; documents: {len(study.documents)} "
            f"({', '.join(m.value for m in study.modalities) or 'no image modalities'})"
        )
        context_lines.append(f"Actually completed direct image reads: {len(request.artifacts.get('reads', {}))}/{len(study.images)}; do not imply unread pixels were interpreted.")
        if patient.notes:
            context_lines.append("Notes: " + " | ".join(patient.notes)[:1500])
    context = "\n".join(context_lines) or "No patient context was supplied."
    prior = request.study.patient.prior_assessment if request.study else {}
    context += "\nPRIOR DIFFERENTIAL (memory, not new evidence):\n" + json.dumps(prior, ensure_ascii=False)

    prompt = _INSTRUCTION.format(context=context, body=body,
                                assessment_schema=ASSESSMENT_SCHEMA, reasoning_rules=REASONING_RULES)

    # Severity comes from the structured findings, not from the prose.
    computed = _severity_from_findings(findings)

    from capabilities.text_catalog import profiles

    key = cache.make_key("clinical-report-v1", _SYSTEM, prompt,
                         [entry[2].to_dict() for entry in profiles("clinical_reasoning")])
    cached = cache.read_json("clinical_report", key)
    if cached:
        try:
            _validate_payload(cached["raw"])
            if not isinstance(cached["model"], str):
                raise ValueError("cached model identifier is invalid")
        except (ValueError, TypeError, KeyError):
            cached = None
    selection = {}
    model_id = ""
    try:
        if cached:
            raw, model_id = cached["raw"], cached["model"]
            _validate_payload(raw)
            selection = {"cache_hit": True, "model": model_id}
        else:
            raw, model_id = chat_text(
                prompt, system=_SYSTEM, max_tokens=2600, temperature=0.1,
                task="clinical_reasoning", json_mode=True, validator=_validate_payload,
            )
            selection = text_selection_trace()
        record_model("report_synthesis", model_id)
        payload = _parse_json(raw)
    except Exception as exc:  # noqa: BLE001 - emit a usable fallback report
        _log.warning("report synthesis failed: %s", exc)
        payload = _fallback_payload(findings, computed, exc)
        payload["synthesis_error"] = f"{type(exc).__name__}: {exc}"

    try:
        assessment = validate_assessment(payload.get("clinical_assessment"), sources, prior,
                                         status_sources=authoritative_material(request.study))
    except (ValueError, TypeError) as exc:
        assessment = validate_assessment({}, sources, prior)
        assessment.validation_issues.append(f"Invalid assessment response: {exc}")
    if not assessment.integrated_summary:
        assessment.integrated_summary = "Integrated reasoning was unavailable; observations are retained for clinician review."
        assessment.limitations.append("No validated source-linked differential was produced.")
    if assessment.validation_issues:
        payload["impression"] = assessment.integrated_summary
        payload["summary"] = assessment.integrated_summary
    elif not payload.get("synthesis_error") and payload.get("clinical_assessment"):
        cache.write_json("clinical_report", key, {"raw": raw, "model": model_id})

    # Enforce the triage floor: the model may not downgrade a critical finding.
    triage = str(payload.get("triage", "routine")).lower()
    if triage not in _TRIAGE_RANK:
        triage = "routine"
    required = _triage_for(computed)
    if _TRIAGE_RANK.get(required, 0) > _TRIAGE_RANK.get(triage, 0):
        _log.info("raising triage %s -> %s to match findings", triage, required)
        triage = required

    status = {
        "overall_severity": computed.value,
        "summary": str(payload.get("summary", "")).strip(),
        "triage": triage,
        "red_flags": _string_list(payload.get("red_flags")),
        "recommended_actions": _string_list(payload.get("recommended_actions")),
    }

    text_lines = [
        f"IMPRESSION: {payload.get('impression', '').strip()}",
        "",
        f"PATIENT STATUS: {computed.value.upper()} (triage: {triage})",
        f"SUMMARY: {status['summary']}",
    ]
    if status["red_flags"]:
        text_lines += ["RED FLAGS:"] + [f"- {f}" for f in status["red_flags"]]
    if status["recommended_actions"]:
        text_lines += ["RECOMMENDED NEXT STEPS:"] + [
            f"- {a}" for a in status["recommended_actions"]
        ]
    text_lines += ["", render_assessment(assessment)]

    from capabilities.verification import request_verification
    verification_requests = ([request_verification(
        "verify_evidence", "Assessment contains unsupported or invalid evidence arguments; perform a source audit",
        artifacts={"validation_issues": assessment.validation_issues},
    )] if assessment.validation_issues else [])
    return CapabilityResult(
        text="\n".join(text_lines),
        artifacts={
            "indication": str(payload.get("indication", "")).strip(),
            "technique": str(payload.get("technique", "")).strip(),
            "impression": str(payload.get("impression", "")).strip(),
            "status": status,
            "findings": findings,
            "clinical_assessment": assessment.model_dump(),
            "synthesis_error": payload.get("synthesis_error", ""),
            "verification_requests": verification_requests,
            "_model_selection": selection,
            "_models_used": {"report_synthesis": model_id} if model_id else {},
        },
    )


_TRIAGE_RANK = {"routine": 0, "urgent": 1, "immediate": 2}


def _triage_for(severity: Severity) -> str:
    if severity is Severity.CRITICAL:
        return "immediate"
    if severity is Severity.HIGH:
        return "urgent"
    return "routine"


def _severity_from_findings(findings: list) -> Severity:
    """Overall severity is the most urgent individual finding."""
    severities: list[Severity] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        try:
            severities.append(Severity(str(finding.get("severity", "")).lower()))
        except ValueError:
            severities.append(Severity.INDETERMINATE)
    if not severities:
        return Severity.INDETERMINATE
    return max_severity(severities)


def _string_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model response: {raw[:200]}")
    return json.loads(text[start : end + 1])


def _validate_payload(raw: str) -> None:
    from models import ClinicalAssessment

    payload = _parse_json(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("impression"), str) or not payload["impression"].strip():
        raise ValueError("report response has no usable impression")
    if not isinstance(payload.get("clinical_assessment"), dict):
        raise ValueError("report response has no structured clinical assessment")
    assessment = ClinicalAssessment.model_validate(payload["clinical_assessment"])
    if not assessment.integrated_summary.strip():
        raise ValueError("clinical assessment has no integrated summary")


def _fallback_payload(findings: list, severity: Severity, exc: Exception) -> dict:
    """A report still has to be delivered when the synthesis model is down."""
    labels = [
        str(f.get("label")) for f in findings if isinstance(f, dict) and f.get("label")
    ]
    listed = "; ".join(labels) if labels else "no findings were extracted"
    return {
        "indication": "Not available (report synthesis unavailable).",
        "technique": "See study metadata.",
        "impression": (
            f"Automated impression could not be generated ({type(exc).__name__}). "
            f"Findings extracted upstream: {listed}. "
            "This study requires direct radiologist review."
        ),
        "summary": f"Synthesis unavailable; {len(labels)} finding(s) carried forward.",
        "triage": _triage_for(severity),
        "red_flags": [
            f"{label} (auto-escalated: impression could not be generated)"
            for label, f in zip(labels, findings)
            if isinstance(f, dict) and f.get("severity") in {"critical", "high"}
        ],
        "recommended_actions": ["Manual radiologist review required."],
    }
