"""Condense upstream text without losing clinical specifics.

The failure mode this guards against is a summariser that turns "4cm right
lower lobe consolidation" into "an abnormality was noted". Numbers, laterality
and measurements are exactly what the referring clinician acts on, so the
prompt forbids softening them.
"""

from __future__ import annotations

from core.errors import CapabilityError
from core.logging_utils import get_logger
from capabilities.hf_backend import chat_text, record_model
from capabilities.registry import CapabilityRequest, CapabilityResult, register

_log = get_logger("summarize")

_SYSTEM = (
    "You are a clinical summarizer. Preserve every specific fact: measurements, "
    "laterality (left/right), counts, dates, anatomical locations, probabilities "
    "and named conditions. Never replace a specific value with a vague word such "
    "as 'several', 'various' or 'not specified' when the source states it. "
    "Never add a fact the source does not contain. "
    "Return only the summary, with no preamble."
)

# Text below this length is already a summary; another model call would only
# add latency and a chance to lose detail.
_PASSTHROUGH_CHARS = 600

# Hard cap on what we send, to keep cost and latency bounded.
_MAX_INPUT_CHARS = 24000


@register("summarization", "Condense text while preserving all clinical specifics.")
def run(request: CapabilityRequest) -> CapabilityResult:
    text = (request.text or "").strip()
    if not text:
        return CapabilityResult(text="No input text was supplied to summarize.")

    if len(text) <= _PASSTHROUGH_CHARS:
        return CapabilityResult(
            text=text, artifacts={"summarization": "passthrough (input already short)"}
        )

    truncated = len(text) > _MAX_INPUT_CHARS
    payload = text[:_MAX_INPUT_CHARS]

    instruction = request.instruction or "Summarize the following clinical text."
    prompt = f"TASK: {instruction}\n\nTEXT:\n{payload}"

    try:
        summary, model_id = chat_text(
            prompt, system=_SYSTEM, max_tokens=1200, temperature=0.2
        )
    except Exception as exc:  # noqa: BLE001
        raise CapabilityError("summarization", str(exc), exc) from exc

    if not summary:
        raise CapabilityError("summarization", f"{model_id} returned empty text")

    record_model("summarization", model_id)
    return CapabilityResult(
        text=summary,
        artifacts={
            "model": model_id,
            "input_chars": len(text),
            "output_chars": len(summary),
            "truncated": truncated,
        },
    )
