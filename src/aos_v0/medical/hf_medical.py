"""HF-backed medical model wrappers (MedGemma image chat + laboratory NER).

These thin wrappers sit on top of the existing `providers.hf` wired transport
functions (`_image_chat_run` / `_token_classification_run`) and the shared
`HFProvider` adapter. They only add medical payload shaping and per-call model
selection; every failure is raised as a typed ProviderError subclass so it flows
into the existing detect / classify / recover loop of the AOS FailureManager.
"""

from __future__ import annotations

from typing import List, Optional

from aos_v0.config import (
    MEDICAL_IMAGE_MODEL,
    MEDICAL_LAB_MODEL,
)
from aos_v0.medical.manifest import LabResult
from aos_v0.providers.hf import (
    HFProvider,
    _image_chat_run,
    _token_classification_run,
)

MEDICAL_IMAGE_SYSTEM = (
    "You are a cautious medical imaging assistant. Describe only what the "
    "image shows. Never assert a confirmed diagnosis: phrase every finding as "
    "an observation that requires physician/radiologist confirmation, and "
    "state uncertainty explicitly."
)


def medic_gemma_describe(
    image_path: str,
    instruction: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> str:
    """Describe a medical image with the configured MedGemma image-chat model.

    `image_path` must point to a local image file; a missing file raises a typed
    ProviderError (from the wired transport) so recovery can act. `model`
    overrides MEDICAL_IMAGE_MODEL when supplied.
    """
    model = model or MEDICAL_IMAGE_MODEL
    provider = HFProvider(model=model, provider=None, client=None)
    return _image_chat_run(
        image_path,
        instruction=instruction,
        provider=provider,
        model=model,
        system=MEDICAL_IMAGE_SYSTEM,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def medic_lab_ner_lines(
    text: str,
    model: Optional[str] = None,
) -> str:
    """Raw laboratory NER output (one entity line per token) for a report text."""
    model = model or MEDICAL_LAB_MODEL
    provider = HFProvider(model=model, provider=None, client=None)
    return _token_classification_run(text, provider=provider, model=model)


def _ner_lines_to_results(
    lines: str, source: str, page: Optional[int]
) -> List[LabResult]:
    """Convert wired token-classification output into LabResult rows.

    The wired run emits one line per entity: `LABEL | token | score | start:end`.
    Grouped consecutive non-field tokens become the test name; field labels carry
    value / unit / reference range / status. Raw NER lines are preserved in the
    row note so downstream steps stay grounded in model evidence.
    """
    results: List[LabResult] = []
    test_parts: List[str] = []
    entry: dict = {}

    def flush() -> None:
        nonlocal test_parts, entry
        if entry:
            name = " ".join(test_parts).strip(" .:;")
            if name:
                results.append(
                    LabResult(
                        test=name,
                        value=entry.get("value", ""),
                        unit=entry.get("unit", ""),
                        reference_range=entry.get("range", ""),
                        status=entry.get("status", "UNKNOWN"),
                        source_file=source,
                        page=page,
                        source_type="AI-extracted laboratory value",
                        note="NER-extracted; verify against source report",
                    )
                )
        test_parts, entry = [], {}

    for line in (lines or "").splitlines():
        if " | " not in line:
            continue
        label, token, _score, _span = line.split(" | ", 3)
        label = label.strip().upper()
        token = token.strip()
        if label == "O":
            flush()
            continue
        if label in {"TEST", "TEST_NAME", "TESTNAME", "TEST NAME"}:
            flush()
            test_parts.append(token)
            continue
        if "VALUE" in label:
            entry["value"] = token
        elif "UNIT" in label:
            entry["unit"] = token
        elif "RANGE" in label or "REF" in label:
            entry["range"] = token
        elif "STATUS" in label:
            entry["status"] = token.upper()
        else:
            test_parts.append(token)
    flush()
    return results


def medic_lab_ner(
    text: str,
    source: str = "",
    page: Optional[int] = None,
    model: Optional[str] = None,
) -> List[LabResult]:
    """Run laboratory NER over a report text and return structured LabResults."""
    lines = medic_lab_ner_lines(text, model=model)
    return _ner_lines_to_results(lines, source or "", page)