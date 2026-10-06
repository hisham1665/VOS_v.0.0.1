"""Live configurable text-model capability profiles; selection is domain-neutral."""
from __future__ import annotations

import json
import os
from pathlib import Path

from config import MODELS
from core.logging_utils import get_logger
from core.routing import CapabilityDNA

_log = get_logger("text-catalog")
_SKILLS = ("extraction", "reconciliation", "clinical_reasoning", "conversation", "summarization", "planning")


def profiles(task: str) -> list[tuple[str, str, CapabilityDNA]]:
    def entry(provider, model, skills=_SKILLS, priority=0, context=128000, **kwargs):
        return provider, model, CapabilityDNA(name=f"{provider}:{model}", skills=tuple(skills),
                                             inputs=("text",), outputs=("text", "json"),
                                             context_tokens=context, priority=priority, **kwargs)

    candidates = [entry("groq", MODELS.text_llm, priority=8 if task == "clinical_reasoning" else 0)]
    if MODELS.fast_text_llm:
        candidates.append(entry("groq", MODELS.fast_text_llm,
                                skills=("extraction", "summarization", "conversation"), priority=8))
    if MODELS.text_llm_fallback:
        candidates.append(entry("groq", MODELS.text_llm_fallback))
    candidates.append(entry("hf", MODELS.hf_text_llm, priority=-3, context=32768))
    path = os.environ.get("MAOS_TEXT_MODEL_CATALOG", "").strip()
    if path:
        try:
            # Read on each decision so updates are discoverable without restart.
            overrides = json.loads(Path(path).read_text(encoding="utf-8"))
            by_name = {item[2].name: item for item in candidates}
            for raw in overrides:
                provider, model = raw["provider"], raw["model_id"]
                if provider not in {"groq", "hf"}:
                    raise ValueError(f"Unsupported text provider {provider}")
                dna = CapabilityDNA(name=f"{provider}:{model}",
                                    skills=tuple(raw.get("skills", _SKILLS)),
                                    inputs=("text",), outputs=("text", "json"),
                                    context_tokens=raw.get("context_tokens", 32768),
                                    memory_gb=raw.get("memory_gb"), cost_per_call=raw.get("cost_per_call"),
                                    expected_latency_s=raw.get("expected_latency_s"),
                                    priority=raw.get("priority", 0), enabled=raw.get("enabled", True))
                by_name[dna.name] = (provider, model, dna)
            candidates = list(by_name.values())
        except (OSError, ValueError, TypeError, KeyError) as exc:
            _log.warning("Text catalog override unavailable: %s", exc)
    # Configurations sometimes use the same primary/fast/fallback model.
    unique = {}
    for provider, model, dna in candidates:
        if not model:
            continue
        old = unique.get(dna.name)
        if old is None or (task in dna.skills and (task not in old[2].skills or dna.priority > old[2].priority)):
            unique[dna.name] = (provider, model, dna)
    return list(unique.values())
