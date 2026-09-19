"""Confidence extraction and aggregation for the OCR service (plan Phase 4).

Adapters emit per-block confidence; this module reduces those to a single page
confidence the fallback gate can act on. The math is deliberately simple and
deterministic -- confidence is a *gate*, not a correctness claim, so it only
needs to be stable and conservative:

  * area-weighted mean of block confidence (bigger regions count more), and
  * a coverage penalty when most of the page's ink landed in untranscribed
    blocks (those blocks carry near-zero confidence anyway and drag the mean
    down -- the penalty makes the effect explicit and keeps the verdict honest).

Nothing here reads marks or decides marks; it only decides how much of the
page the OCR stage believes it actually read.
"""

from __future__ import annotations

from typing import List, Sequence

from aos_v0.exam.ocr.models import OcrBlock, OcrPage


def page_confidence(blocks: Sequence[OcrBlock]) -> float:
    """Combine per-block confidence into one page-level confidence in [0, 1]."""
    if not blocks:
        return 0.0
    weighted = sum(b.confidence * b.area for b in blocks)
    total_area = sum(b.area for b in blocks)
    base = weighted / total_area if total_area else 0.0

    # Coverage penalty: a page whose detected blocks are mostly untranscribed
    # was structurally found but not read. Untranscribed blocks sit near 0.0
    # confidence, but applying the penalty on their share makes the intent
    # explicit and immune to a single transcribed block inflating the mean.
    untranscribed = sum(1 for b in blocks if not b.transcribed)
    if untranscribed:
        share = untranscribed / len(blocks)
        base = base * (1.0 - 0.5 * share)
    return round(min(1.0, max(0.0, base)), 4)


def document_confidence(pages: Sequence[OcrPage]) -> float:
    """Worst-page-biased document confidence (a weak page should not hide)."""
    if not pages:
        return 0.0
    return round(min(p.confidence for p in pages), 4)


def confidence_report(blocks: Sequence[OcrBlock]) -> dict:
    """Diagnostics for one page: counts, coverage, weighted mean."""
    return {
        "blocks": len(blocks),
        "transcribed": sum(1 for b in blocks if b.transcribed),
        "untranscribed": sum(1 for b in blocks if not b.transcribed),
        "page_confidence": page_confidence(blocks),
    }


def merge_adapter_confidences(primary: float, fallback: float) -> float:
    """Combine two independent reads conservatively (min of the two).

    Cross-model agreement is expected to raise trust -- but until the benchmark
    (Phase 13) measures agreement curves, keeping the minimum is the safe,
    review-friendly choice: two weak reads never sum into a strong one.
    """
    return round(min(primary, fallback), 4)


def flag(confidence: float, low: float, review: bool = True) -> bool:
    """Does this confidence trip the fallback/review gate?"""
    return confidence < low if review else confidence <= low


def mean_confidence(blocks: List[OcrBlock]) -> float:
    """Unweighted block mean (used for reporting, not gating)."""
    if not blocks:
        return 0.0
    return round(sum(b.confidence for b in blocks) / len(blocks), 4)