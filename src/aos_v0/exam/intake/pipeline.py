"""Document ingestion service and pre-processing pipeline (implementation plan Phase 3).

Orchestrates the full pipeline for one source:

    upload -> file validation -> page extraction -> image normalization
    -> rotation detection -> deskew -> quality detection -> page ordering

and produces an :class:`IntakeResult` with per-page metadata (the page
metadata system deliverable) and packet-level findings for the edge cases
(missing / duplicate / out-of-order / blank / mixed-size pages, damaged files,
unsupported formats).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from PIL import Image

from .formats import collect_frames, sniff_kind
from .models import (
    IntakeError,
    IntakePage,
    IntakeResult,
    IntakeSummary,
    PageIssue,
    PageMeta,
    PageStatus,
    UnsupportedFormatError,
)
from .normalize import (
    deskew,
    detect_rotation,
    detect_skew,
    normalize_base,
    to_grayscale_thumb,
)
from .quality import (
    DARK_MAX_MEAN,
    DUPLICATE_HAMMING_THRESHOLD,
    MIN_ROTATION_CONFIDENCE,
    hamming,
    luminance_stats,
    page_hash,
    score_from_issues,
    score_page,
    status_for_score,
)

DEFAULT_DPI = 150.0
DEFAULT_MAX_PAGES = 500


def ingest(
    source: str | Path,
    *,
    dpi: float = DEFAULT_DPI,
    max_pages: int = DEFAULT_MAX_PAGES,
    auto_rotate: bool = False,
    deskew_enabled: bool = True,
    expected_page_count: Optional[int] = None,
    write_pages_to: Optional[str | Path] = None,
) -> IntakeResult:
    """Run the intake pipeline on a file (PDF/image/zip) or a folder of images."""
    src = Path(source)
    if not src.exists():
        raise IntakeError(f"source not found: {src}")
    if not src.is_dir() and not src.is_file():
        raise IntakeError(f"source is neither a file nor a directory: {src}")

    kind = "folder" if src.is_dir() else sniff_kind(src)
    if kind == "unknown":
        raise UnsupportedFormatError(
            f"'{src}' is not a recognized PDF, image or ZIP archive"
        )

    frames, rejected, truncated = collect_frames(
        src, kind=kind, dpi=dpi, max_pages=max_pages
    )
    if not frames:
        detail = f"; rejected members: {rejected}" if rejected else ""
        raise IntakeError(f"no usable pages found in {source}{detail}")

    pages: List[IntakePage] = []
    for index, frame in enumerate(frames):
        image = normalize_base(frame.image)
        adjusted: List[str] = []

        rotation = detect_rotation(image)
        rotation_degrees = rotation.degrees
        if (
            auto_rotate
            and rotation.transpose is not None
            and rotation.confidence >= MIN_ROTATION_CONFIDENCE
        ):
            image = image.transpose(rotation.transpose)
            adjusted.append("rotation")

        # Flooded/dark pages are not deskewed: their projection statistics are
        # meaningless and would produce sweep-edge artifacts.
        dark_page = luminance_stats(to_grayscale_thumb(image, 160))[0] < DARK_MAX_MEAN
        skew_degrees = detect_skew(image) if (deskew_enabled and not dark_page) else 0.0
        if deskew_enabled and abs(skew_degrees) >= 1e-9:
            image = deskew(image, skew_degrees)
            adjusted.append("deskew")

        metrics, issues, score = score_page(
            image,
            dpi=dpi,
            rotation_degrees=rotation_degrees,
            rotation_confidence=rotation.confidence,
            skew_degrees=skew_degrees,
        )
        meta = PageMeta(
            index=index,
            source=frame.label,
            member=frame.member,
            page_no=frame.declared_no,
            status=status_for_score(score),
            issues=issues,
            adjusted=adjusted,
            quality_score=score,
            metrics=metrics,
        )
        pages.append(IntakePage(meta, image))

    _ordering_pass(pages)

    if write_pages_to is not None:
        _write_pages(pages, Path(write_pages_to))

    summary = _build_summary(
        source=src,
        kind=kind,
        pages=pages,
        rejected=rejected,
        truncated=truncated,
        expected_page_count=expected_page_count,
    )
    return IntakeResult(summary=summary, pages=pages, rejected=rejected)


def _ordering_pass(pages: List[IntakePage]) -> None:
    """Detect duplicate pages (dHash) and out-of-order declared page numbers."""
    hashes = [page_hash(p.image) for p in pages]
    for i in range(1, len(pages)):
        for j in range(i):
            if hamming(hashes[i], hashes[j]) <= DUPLICATE_HAMMING_THRESHOLD:
                meta = pages[i].meta
                meta.metrics.duplicate_of = j
                if PageIssue.DUPLICATE not in meta.issues:
                    meta.issues.append(PageIssue.DUPLICATE)
                meta.quality_score = score_from_issues(meta.issues)
                meta.status = status_for_score(meta.quality_score)
                for k in range(i):  # duplicate of a duplicate resolves to original
                    if pages[j].meta.metrics.duplicate_of is not None:
                        meta.metrics.duplicate_of = pages[j].meta.metrics.duplicate_of
                        break
                break


def _write_pages(pages: List[IntakePage], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for page in pages:
        page_path = out_dir / f"page_{page.meta.index + 1:04d}.png"
        if page.image is not None:
            page.image.save(page_path)


def _build_summary(
    source: Path,
    kind: str,
    pages: List[IntakePage],
    rejected: List[str],
    truncated: bool,
    expected_page_count: Optional[int],
) -> IntakeSummary:
    issues: List[PageIssue] = []
    sizes: List[List[int]] = []
    for page in pages:
        for issue in page.meta.issues:
            if issue not in issues:
                issues.append(issue)
        size = [page.meta.metrics.width, page.meta.metrics.height]
        if size not in sizes:
            sizes.append(size)

    scores = [p.meta.quality_score for p in pages]
    min_score = min(scores)
    avg_score = round(sum(scores) / len(scores), 4)

    out_of_order: List[int] = []
    prev_no: Optional[int] = None
    for i, page in enumerate(pages):
        no = page.meta.page_no
        if no is not None:
            if prev_no is not None and no < prev_no:
                out_of_order.append(i)
            prev_no = no

    missing = 0
    extra = 0
    if expected_page_count is not None:
        if len(pages) < expected_page_count:
            missing = expected_page_count - len(pages)
        elif len(pages) > expected_page_count:
            extra = len(pages) - expected_page_count

    return IntakeSummary(
        source=str(source),
        kind=kind,
        page_count=len(pages),
        rejected_count=len(rejected),
        verdict=status_for_score(min_score),
        quality_score=avg_score,
        unique_page_sizes=sizes,
        mixed_page_sizes=len(sizes) > 1,
        issues=issues,
        duplicate_pages=[
            i for i, p in enumerate(pages) if PageIssue.DUPLICATE in p.meta.issues
        ],
        out_of_order_pages=out_of_order,
        missing_page_count=missing,
        extra_page_count=extra,
        truncated=truncated,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aos_v0.exam.intake",
        description="Exam document intake and pre-processing pipeline (Phase 3).",
    )
    parser.add_argument("source", help="PDF / image / ZIP file or folder of images")
    parser.add_argument("--dpi", type=float, default=DEFAULT_DPI, help="nominal scan DPI")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--auto-rotate", action="store_true")
    parser.add_argument("--no-deskew", action="store_true")
    parser.add_argument("--expected-pages", type=int, default=None)
    parser.add_argument("--write-pages", default=None, help="output dir for normalized PNGs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = ingest(
            args.source,
            dpi=args.dpi,
            max_pages=args.max_pages,
            auto_rotate=args.auto_rotate,
            deskew_enabled=not args.no_deskew,
            expected_page_count=args.expected_pages,
            write_pages_to=args.write_pages,
        )
    except IntakeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "summary": result.summary.model_dump(mode="json"),
            "pages": result.page_documents(),
            "rejected": result.rejected,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"Intake: {result.summary.source} [{result.summary.kind}]")
    for page in result.pages:
        m = page.meta
        issues = ",".join(i.value for i in m.issues) or "-"
        print(
            f"  {m.index + 1:4d} {m.source:<40} {m.metrics.width}x{m.metrics.height} "
            f" score={m.quality_score:.2f} {m.status.value:<8} issues={issues}"
        )
    s = result.summary
    print(
        f"Summary: {s.page_count} page(s) score={s.quality_score:.2f} "
        f"verdict={s.verdict.value} issues={[i.value for i in s.issues]}"
    )
    if s.duplicate_pages:
        print(f"  duplicates: {s.duplicate_pages}")
    if s.out_of_order_pages:
        print(f"  out of order: {s.out_of_order_pages}")
    if s.missing_page_count:
        print(f"  missing pages: {s.missing_page_count}")
    if s.mixed_page_sizes:
        print(f"  mixed page sizes: {s.unique_page_sizes}")
    if s.truncated:
        print(f"  truncated at {s.page_count} pages (max_pages)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())