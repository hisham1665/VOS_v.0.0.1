"""Reconcile independent image and document reads into one coherent finding list.

Images may be read in isolation and documents may be split into pages. This
stage merges duplicates, preserves source references and surfaces disagreements
without treating a failed or non-medical input as a normal result.

It also does the thing the per-image reads structurally cannot: compare the
narrative reads against the classifier probabilities and say when they diverge.
"""

from __future__ import annotations

import json

from core.errors import CapabilityError
from core.logging_utils import get_logger
from capabilities.hf_backend import chat_text, record_model
from capabilities.registry import CapabilityRequest, CapabilityResult, register
from models import Severity

_log = get_logger("synthesis")

_SYSTEM = (
    "You are a consultant radiologist consolidating preliminary image and "
    "clinical-document reads from one study into one findings list. You merge duplicates, "
    "reconcile disagreements between views, and preserve every distinct "
    "abnormality. You never introduce a finding that appears in none of the "
    "source reads, and you never drop one that does."
)

_INSTRUCTION = """\
Consolidate the per-image and per-document reads below into a single findings list for the study.

Rules:
- Merge findings that describe the same pathology across images into one entry,
  listing every image id in which it appears.
- Keep distinct findings separate, even when they are anatomically adjacent.
- When reads disagree, keep the finding and lower its confidence, recording the
  disagreement in the description.
- The input also contains a two-stage screening cascade (stage 1 screeners,
  stage 2 specialist re-checks). When a candidate is SUPPORTED by a specialist
  but no narrative read mentions it, include it with low confidence (<= 0.4)
  and record the discrepancy explicitly. A candidate the specialist did NOT
  SUPPORT, or that is UNVERIFIABLE, is not a finding on its own; mention it in
  discrepancies only if a narrative read contradicts it.
- An image marked NOT SCREENED had no supervised check; never describe it as
  "screened normal".
- Do not invent findings. If every read is normal, return a single entry with
  label "No acute abnormality" and severity "normal".

Return ONLY a JSON object, no prose and no code fence, of the form:
{{
  "findings": [
    {{
      "label": "short finding name",
      "description": "one or two sentences, including any disagreement",
      "severity": "critical|high|moderate|low|normal|indeterminate",
      "confidence": 0.0 to 1.0,
      "image_ids": ["image ids where seen"],
      "document_ids": ["document ids where seen"],
      "source_refs": ["exact source refs from the input, e.g. file.pdf#page=2"],
      "body_part": "region or null"
    }}
  ],
  "quality_issues": ["any limitation that affects interpretation"],
  "discrepancies": ["narrative vs classifier disagreements"]
}}

STUDY CONTEXT:
{context}

PER-IMAGE READS AND SCREENING SCORES:
{reads}
"""


@register(
    "cross_image_synthesis",
    "Merge per-image reads into one de-duplicated, reconciled findings list.",
)
def run(request: CapabilityRequest) -> CapabilityResult:
    if not request.text.strip():
        return CapabilityResult(
            text="No upstream reads were available to consolidate.",
            artifacts={"findings": []},
        )

    # The screening cascade also feeds this node. If every narrative image read
    # and every document read failed, what is left is classifier output alone,
    # and a findings list built only from that would look far more certain
    # than it is. Fail instead, so the report degrades visibly.
    artifacts = request.artifacts or {}
    if not artifacts.get("reads") and not artifacts.get("document_reads"):
        raise CapabilityError(
            "cross_image_synthesis",
            "no image or document read succeeded; refusing to build findings "
            "from screening-model output alone",
        )

    context_parts: list[str] = []
    if request.study is not None:
        patient = request.study.patient
        if patient.clinical_history:
            context_parts.append(f"History: {patient.clinical_history}")
        if patient.referring_question:
            context_parts.append(f"Referring question: {patient.referring_question}")
        context_parts.append(
            f"Images in study: {len(request.study.images)}; documents: {len(request.study.documents)} "
            f"({', '.join(m.value for m in request.study.modalities) or 'no image modalities'})"
        )
    context = "\n".join(context_parts) or "No additional context."

    prompt = _INSTRUCTION.format(context=context, reads=request.text[:24000])

    try:
        raw, model_id = chat_text(
            prompt, system=_SYSTEM, max_tokens=2000, temperature=0.1
        )
        record_model("cross_image_synthesis", model_id)
        payload = _parse_json(raw)
    except Exception as exc:  # noqa: BLE001 - degrade to a pass-through
        _log.warning("synthesis failed (%s); passing reads through unmerged", exc)
        return CapabilityResult(
            text=(
                "Automated consolidation failed "
                f"({type(exc).__name__}: {exc}). The per-image reads below are "
                "unmerged and may contain duplicates:\n\n" + request.text
            ),
            artifacts={"findings": [], "synthesis_error": str(exc)},
        )

    allowed_refs = set((request.artifacts or {}).get("document_reads", {}).keys())
    findings = _normalise_findings(payload.get("findings", []), allowed_refs=allowed_refs)

    lines = ["Consolidated findings:"]
    for finding in findings:
        images = ", ".join(finding["image_ids"]) or "study-wide"
        confidence = finding.get("confidence")
        confidence_text = f", confidence {confidence:.0%}" if confidence else ""
        lines.append(
            f"- [{finding['severity'].upper()}] {finding['label']} "
            f"({images}{confidence_text}): {finding['description']}"
        )

    for heading, key in (
        ("Quality issues", "quality_issues"),
        ("Discrepancies", "discrepancies"),
    ):
        items = [str(i) for i in payload.get(key, []) if str(i).strip()]
        if items:
            lines.append(f"{heading}:")
            lines.extend(f"- {item}" for item in items)

    return CapabilityResult(
        text="\n".join(lines),
        artifacts={
            "findings": findings,
            "quality_issues": payload.get("quality_issues", []),
            "discrepancies": payload.get("discrepancies", []),
        },
    )


def _parse_json(raw: str) -> dict:
    """Extract a JSON object from a model response that may be fenced."""
    text = raw.strip()
    if text.startswith("```"):
        # Strip a ```json ... ``` fence.
        text = text.split("```")[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model response: {raw[:200]}")
    return json.loads(text[start : end + 1])


def _normalise_findings(raw_findings, *, allowed_refs: set[str] | None = None) -> list[dict]:
    """Coerce model output into our schema, dropping anything unusable."""
    valid = {s.value for s in Severity}
    out: list[dict] = []
    for entry in raw_findings or []:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "")).strip()
        if not label:
            continue

        severity = str(entry.get("severity", "")).strip().lower()
        if severity not in valid:
            severity = Severity.INDETERMINATE.value

        confidence = entry.get("confidence")
        try:
            confidence = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = None

        image_ids = entry.get("image_ids") or []
        if not isinstance(image_ids, list):
            image_ids = [str(image_ids)]
        source_refs = entry.get("source_refs") or []
        if not isinstance(source_refs, list):
            source_refs = [str(source_refs)]
        if allowed_refs is not None:
            source_refs = [str(ref) for ref in source_refs if str(ref) in allowed_refs]

        out.append(
            {
                "label": label,
                "description": str(entry.get("description", "")).strip(),
                "severity": severity,
                "confidence": confidence,
                "image_ids": [str(i) for i in image_ids],
                "document_ids": [str(i) for i in (entry.get("document_ids") or [])],
                "source_refs": source_refs,
                "body_part": entry.get("body_part") or None,
                "source": str(entry.get("source", "vlm")),
            }
        )
    return out
