"""OCR service -- orchestrates adapters, confidence gating and fallback.

Implementation-plan Phase 4 pipeline:

    Page Image
        |
        v
    OCR Model  ---(adapter chain: printed -> handwritten -> vision -> local)
        |
        +-- Text blocks + bbox + confidence
        |
        v
    Confidence check  --(gate on OcrSettings.confidence_low/high)-->
        | high/ok -> layout analysis -> OcrDocument (status ok/degraded)
        | low    -> next fallback adapter, then human review
        v
    Layout Analysis (text regions, question regions, answer regions,
                     tables, diagrams)

Golden rule enforced here: the service never fabricates text and never decides
marks. When no adapter can read a page with sufficient confidence, the result
is status=review with `EvalReviewReason.LOW_OCR_CONFIDENCE` -- human review,
never an automatic zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from PIL import Image

from aos_v0.exam.intake import IntakeError, IntakePage, PageIssue, ingest
from aos_v0.exam.models import EvalReviewReason
from aos_v0.exam.ocr.adapters import (
    LocalStructuralAdapter,
    OcrAdapter,
    default_adapters,
)
from aos_v0.exam.ocr.confidence import page_confidence
from aos_v0.exam.ocr.layout import LayoutAdapter, default_layout_adapter
from aos_v0.exam.ocr.models import (
    OcrAdapterAttempt,
    OcrAdapterAttemptStatus,
    OcrBlock,
    OcrDocument,
    OcrError,
    OcrPage,
    OcrResult,
    OcrSettings,
    OcrStatus,
    OcrStrategy,
    OcrUnavailableError,
)

DEFAULT_DPI = 150.0


# ---------------------------------------------------------------------------
# Single-page OCR
# ---------------------------------------------------------------------------


def run_ocr(
    page_image: Image.Image,
    *,
    page_no: int = 1,
    source: str = "",
    strategy: str = "auto",
    settings: Optional[OcrSettings] = None,
    adapters: Optional[List[OcrAdapter]] = None,
    layout_adapter: Optional[LayoutAdapter] = None,
    hints: Optional[dict] = None,
) -> OcrResult:
    """Run the plan's OCR ladder on one normalized page image."""
    settings = settings or OcrSettings()
    if adapters is None:
        adapters = default_adapters(strategy)
    layout = layout_adapter or default_layout_adapter()
    hints = dict(hints or {})

    trace: List[OcrAdapterAttempt] = []
    best_blocks: List[OcrBlock] = []
    best_confidence = 0.0
    accepted: Optional[List[OcrBlock]] = None

    for adapter in adapters:
        try:
            blocks = adapter.run(page_image, hints=hints)
        except OcrError as exc:
            trace.append(
                OcrAdapterAttempt(
                    adapter_id=adapter.adapter_id,
                    model=adapter.model_name,
                    status=OcrAdapterAttemptStatus.UNAVAILABLE
                    if isinstance(exc, OcrUnavailableError)
                    else OcrAdapterAttemptStatus.ERROR,
                    detail=str(exc),
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 - chain must survive bad adapters
            trace.append(
                OcrAdapterAttempt(
                    adapter_id=adapter.adapter_id,
                    model=adapter.model_name,
                    status=OcrAdapterAttemptStatus.ERROR,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        confidence = page_confidence(blocks)
        trace.append(
            OcrAdapterAttempt(
                adapter_id=adapter.adapter_id,
                model=adapter.model_name,
                status=OcrAdapterAttemptStatus.OK,
                confidence=confidence,
                detail=f"{len(blocks)} block(s)",
            )
        )
        if confidence > best_confidence:
            best_blocks, best_confidence = blocks, confidence

        if confidence >= settings.confidence_low:
            accepted = blocks
            break
        trace[-1].status = OcrAdapterAttemptStatus.LOW_CONFIDENCE

    evidence = accepted if accepted is not None else best_blocks
    try:
        regions = layout.analyze(page_image, evidence) if evidence else []
    except OcrError:
        regions = []

    if accepted is not None:
        status = settings.verdict(best_confidence)
        reasons: List[EvalReviewReason] = []
    else:
        status = OcrStatus.REVIEW
        reasons = [EvalReviewReason.LOW_OCR_CONFIDENCE]

    if not adapters:
        status = OcrStatus.UNAVAILABLE
        reasons = []

    page = OcrPage(
        page_no=page_no,
        source=source,
        blocks=evidence,
        regions=regions,
        confidence=best_confidence,
        status=status,
        review_reasons=reasons,
    )
    document = OcrDocument(pages=[page])
    return OcrResult(
        document=document,
        status=document.worst_status,
        fallback_trace=trace,
        review_reasons=list(reasons),
    )


# ---------------------------------------------------------------------------
# Document-level OCR (consumes intake output)
# ---------------------------------------------------------------------------


def _hints_from_meta(page: IntakePage) -> dict:
    issues = {i.value for i in page.meta.issues}
    hints: dict = {}
    if PageIssue.BLANK.value in issues:
        hints["blank"] = True
    if PageIssue.EXCESSIVE_DARKNESS.value in issues:
        hints["dark"] = True
    if PageIssue.EXCESSIVE_BRIGHTNESS.value in issues:
        hints["washed"] = True
    if PageIssue.BLURRY.value in issues:
        hints["blurry"] = True
    if PageIssue.CROPPED.value in issues:
        hints["cropped"] = True
    return hints


def run_document(
    pages: List[IntakePage],
    *,
    strategy: str = "auto",
    settings: Optional[OcrSettings] = None,
    adapters: Optional[List[OcrAdapter]] = None,
    layout_adapter: Optional[LayoutAdapter] = None,
    run_page: Optional[Callable[..., OcrResult]] = None,
) -> OcrResult:
    """OCR a whole intake result (per-page ladder, document verdict)."""
    run_page = run_page or run_ocr
    ocr_pages: List[OcrPage] = []
    trace: List[OcrAdapterAttempt] = []
    reasons: List[EvalReviewReason] = []

    for page in pages:
        result = run_page(
            page.image,
            page_no=page.meta.page_no if page.meta.page_no is not None else page.meta.index + 1,
            source=page.meta.source,
            strategy=strategy,
            settings=settings,
            adapters=adapters,
            layout_adapter=layout_adapter,
            hints=_hints_from_meta(page),
        )
        ocr_pages.extend(result.document.pages)
        trace.extend(result.fallback_trace)
        for reason in result.review_reasons:
            if reason not in reasons:
                reasons.append(reason)

    document = OcrDocument(pages=ocr_pages)
    return OcrResult(
        document=document,
        status=document.worst_status,
        fallback_trace=trace,
        review_reasons=reasons,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aos_v0.exam.ocr",
        description="Phase 4 OCR service: intake -> OCR ladder -> layout -> review routing.",
    )
    parser.add_argument("source", help="PDF / image / ZIP file or folder of images")
    parser.add_argument("--dpi", type=float, default=DEFAULT_DPI)
    parser.add_argument("--strategy", choices=[s.value for s in OcrStrategy],
                        default=OcrStrategy.AUTO.value)
    parser.add_argument("--no-local", action="store_true",
                        help="omit the local structural engine from the chain")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        intake_result = ingest(args.source, dpi=args.dpi)
    except (IntakeError, OcrError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    adapters = default_adapters(args.strategy, local=not args.no_local)
    result = run_document(
        intake_result.pages, strategy=args.strategy, adapters=adapters
    )

    if args.json:
        payload = {
            "pages_ocr": result.to_plan_json(),
            "summary": result.summary,
            "fallback_trace": [
                attempt.model_dump(mode="json") for attempt in result.fallback_trace
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"OCR: {intake_result.summary.source} [{args.strategy}]")
    for page in result.document.pages:
        status = page.status.value
        reasons = ",".join(r.value for r in page.review_reasons) or "-"
        print(
            f"  p{page.page_no:<3} {page.source:<40} conf={page.confidence:.2f} "
            f"blocks={len(page.blocks):<3} regions={len(page.regions)} "
            f"status={status:<11} review={reasons}"
        )
    print(
        f"Summary: status={result.status.value} mean_conf={result.document.mean_confidence:.2f} "
        f"needs_review={result.document.needs_review_pages}"
    )
    for attempt in result.fallback_trace:
        conf = f"{attempt.confidence:.2f}" if attempt.confidence is not None else "-"
        print(
            f"  trace: {attempt.adapter_id:<26} {attempt.status.value:<15} "
            f"conf={conf}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())