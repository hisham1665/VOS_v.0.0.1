"""Deterministic memory keys make recall inspectable without another model call."""

from __future__ import annotations

import re


def build_summary_key(node_id: str, capability: str, description: str, value: str | None = None,
                     max_chars: int = 200) -> str:
    """Build a compact, stable gloss from a node instruction and its result.

    ``node_id`` is accepted for compatibility and intentionally omitted from the
    natural-language key so semantically similar work can be retrieved.
    """
    if value is None:
        # v5.2 called this as (description, capability, value).
        value, description, node_id = description, node_id, ""
    clean_description = re.sub(r"\s+", " ", description or "").strip()[:60]
    first_line = next((re.sub(r"\s+", " ", line).strip() for line in (value or "").splitlines() if line.strip()), "no output")
    digest = f"{first_line[:80]} ({len(value or ''):,} chars)"
    return f"{capability} on '{clean_description}' -> {digest}"[:max_chars]
