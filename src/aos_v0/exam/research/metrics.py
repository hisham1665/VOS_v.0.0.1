"""Phase 13 research metrics (pure functions over AI-vs-human comparisons).

Every metric is a plain function so the numbers are auditable and testable
without the orchestrator. The definitions follow the plan's Ph13 "Metrics"
section exactly:

* MAE / exact agreement / tolerance agreement (<=0.5 and <=1)
* false rejection (correct answers marked 0) and false acceptance
  (incorrect answers given marks)
* semantic acceptance (valid paraphrases accepted)
* partial-mark agreement (partial marks consistent with the human)
* OCR accuracy vs a transcription
* agent agreement, reconciliation success, fault-recovery success,
  model-selection quality and selection overhead
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

_TOLERANCES = (0.5, 1.0)

_PAPER_OWNED = {"low_ocr_confidence", "identity_uncertain", "missing_page"}


def mean_absolute_error(humans: Sequence[float], ai: Sequence[float]) -> float:
    """Average |ai - human| over the scored pairs."""
    if not humans:
        return 0.0
    return sum(abs(a - h) for h, a in zip(humans, ai)) / len(humans)


def exact_agreement(humans: Sequence[float], ai: Sequence[float]) -> float:
    """Fraction of entries where AI mark == human mark."""
    if not humans:
        return 0.0
    return sum(1 for h, a in zip(humans, ai) if a == h) / len(humans)


def tolerance_agreement(
    humans: Sequence[float], ai: Sequence[float], tolerances: Sequence[float] = _TOLERANCES
) -> dict:
    """Fraction within each tolerance (|ai - human| <= t)."""
    if not humans:
        return {str(t): 0.0 for t in tolerances}
    return {
        str(t): sum(1 for h, a in zip(humans, ai) if abs(a - h) <= t) / len(humans)
        for t in tolerances
    }


def false_rejection(
    humans: Sequence[float], ai: Sequence[float]
) -> tuple[int, float]:
    """Correct answers (human gave marks) marked 0 by the AI. (count, rate)."""
    total = sum(1 for h in humans if h > 0)
    hits = sum(1 for h, a in zip(humans, ai) if h > 0 and a == 0)
    return hits, (hits / total if total else 0.0)


def false_acceptance(
    humans: Sequence[float], ai: Sequence[float]
) -> tuple[int, float]:
    """Incorrect answers (human gave 0) incorrectly given marks. (count, rate)."""
    total = sum(1 for h in humans if h == 0)
    hits = sum(1 for h, a in zip(humans, ai) if h == 0 and a > 0)
    return hits, (hits / total if total else 0.0)


def semantic_acceptance(
    humans: Sequence[float], ai: Sequence[float], full: Sequence[float]
) -> tuple[int, float]:
    """Of paraphrase entries, the fraction accepted at full marks."""
    total = len(humans)
    hits = sum(
        1 for h, a, m in zip(humans, ai, full) if a == m
    )
    return hits, (hits / total if total else 0.0)


def partial_mark_agreement(
    humans: Sequence[float], ai: Sequence[float], tolerance: float = 0.5
) -> tuple[int, float]:
    """Of partial human marks (0 < h < full), the fraction within tolerance.
    Full marks are passed as unusable, so partial agreement is measured only
    on genuinely partial scores."""
    partials = [(h, a) for h, a in zip(humans, ai) if h > 0]
    total = len(partials)
    hits = sum(1 for h, a in partials if abs(a - h) <= tolerance)
    return hits, (hits / total if total else 0.0)


def character_accuracy(ocr_text: str, truth: str) -> float:
    """Fraction of ground-truth characters reproduced by the OCR text."""
    if not truth:
        return 0.0
    if not ocr_text:
        return 0.0
    matches = sum(1 for a, b in zip(ocr_text, truth) if a == b)
    return max(0.0, min(1.0, matches / max(len(truth), 1)))


def word_accuracy(ocr_text: str, truth: str) -> float:
    """Fraction of ground-truth words appearing verbatim in the OCR text."""
    granted = truth.split()
    if not granted:
        return 0.0
    given = re.findall(r"\S+", ocr_text.lower()) if ocr_text else []
    present = sum(1 for word in granted if word.lower() in given)
    return present / len(granted)


def ocr_accuracy(
    ocr_text: str, truth: str, level: str = "word"
) -> float:
    """Word- or character-level OCR accuracy vs the transcription."""
    if level == "char":
        return character_accuracy(ocr_text, truth)
    return word_accuracy(ocr_text, truth)


def agent_agreement(
    agent1: Sequence[Optional[float]], agent2: Sequence[Optional[float]]
) -> tuple[int, float]:
    """Fraction of rows where primary and verifier marks agree exactly."""
    total = sum(1 for a, b in zip(agent1, agent2) if a is not None and b is not None)
    hits = sum(1 for a, b in zip(agent1, agent2) if a is not None and b is not None and a == b)
    return hits, (hits / total if total else 0.0)


def reconciliation_success(rows) -> float:
    """Rate at which agreed rows proceed without review and disagreed rows are
    flagged (agents agreed -> proposed, agents differed -> needs_review)."""
    scored = 0
    ok = 0
    for row in rows:
        agents = row.get("agents") if isinstance(row, dict) else row.agents
        if not agents or not agents.get("primary") or not agents.get("verifier"):
            continue
        scored += 1
        primary = agents.get("primary", {}).get("marks")
        verifier = agents.get("verifier", {}).get("marks")
        needs_review = row.get("needs_review") if isinstance(row, dict) else row.needs_review
        if (primary == verifier) == (not needs_review):
            ok += 1
    return ok / scored if scored else 0.0


def recovery_success(recoveries: Iterable[dict]) -> tuple[int, int, float]:
    """(recovered, attempted, rate) from a corpus of recovery dicts. A recovery
    counts as attempted when a stage was retried and recovered when the final
    outcome is not ``recovery_failed``/``terminal`` for that stage."""
    attempted = 0
    recovered = 0
    for rec in recoveries:
        for outcome in rec.values() if isinstance(rec, dict) else rec:
            stage = outcome.get("outcome") if isinstance(outcome, dict) else None
            if stage is None:
                continue
            attempted += 1
            if stage not in ("recovery_failed", "terminal"):
                recovered += 1
    rate = recovered / attempted if attempted else 0.0
    return recovered, attempted, rate


def selection_quality(rows) -> float:
    """Fraction of trace nodes whose dynamically selected resource satisfies the
    node's capability DNA requirements (coverage + availability). Pass a list of
    ``selection`` dicts: {flags, satisfies, available}."""
    selected = [row for row in rows if row.get("satisfies") is not None]
    if not selected:
        return 0.0
    ok = sum(
        1
        for row in selected
        if row.get("satisfies") and row.get("available") and row.get("resource")
    )
    return ok / len(selected)


def selection_overhead(selections: Sequence[dict], node_count: int) -> dict:
    """How much the registry was consulted vs the number of executed nodes."""
    return {
        "selects": len(selections or []),
        "nodes": node_count,
        "mode_switches": len(
            {(s.get("resource")) for s in selections or []}
        )
        - (1 if selections else 0),
    }