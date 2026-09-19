"""Answer-sheet parser -- Phase 5 orchestrator.

`structure()` turns a Phase-4 `OcrDocument` into a `StructuredAnswerSheet`:
student identity (+ roster evidence), question detection, out-of-order and
continuation-aware question-to-answer mapping, page grouping, and the plan's
edge-case issues. Consensus rule: an extraction the parser is not sure about is
an issue that routes the sheet to human review (`EvalReviewReason`) -- the
parser never auto-fills, never rewrites a misread question number, and never
decides a mark.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from aos_v0.exam.config import ConfigLoadError, load_exam_configuration
from aos_v0.exam.intake import IntakeError, ingest
from aos_v0.exam.models import EvalReviewReason, ExamConfiguration, Roster
from aos_v0.exam.ocr.models import (
    LayoutRegion,
    OcrBlock,
    OcrDocument,
    OcrError,
    OcrPage,
)
from aos_v0.exam.ocr.pipeline import run_document
from aos_v0.exam.structure.identity import extract_student_identity
from aos_v0.exam.structure.models import (
    AnswerSheetEntry,
    MappingIssue,
    SheetStatus,
    StructuringError,
    StructuringSettings,
    StructuredAnswerSheet,
    StudentIdentity,
)
from aos_v0.exam.structure.questions import (
    AnswerMapping,
    looks_crossed_out,
    map_answers,
    parse_question_label,
    strip_label_text,
)

_REVIEW_BY_ISSUE = {
    MappingIssue.IDENTITY_UNVERIFIED: EvalReviewReason.IDENTITY_UNCERTAIN,
    MappingIssue.IDENTITY_MISMATCH: EvalReviewReason.IDENTITY_UNCERTAIN,
    MappingIssue.MULTIPLE_ATTEMPTS: EvalReviewReason.MULTIPLE_ANSWERS,
    MappingIssue.AMBIGUOUS_MAPPING: EvalReviewReason.AMBIGUOUS_ANSWER,
    MappingIssue.QUESTION_NUMBER_OCR_ERROR: EvalReviewReason.AMBIGUOUS_ANSWER,
    MappingIssue.MISSING_QUESTION_NUMBER: EvalReviewReason.AMBIGUOUS_ANSWER,
}

_LABEL_PARTS = re.compile(r"Q(\d{1,3})(?:\(([a-z]+)\))?")


def _number_letter(question_id: str) -> Tuple[Optional[int], Optional[str]]:
    """Split a canonical "Q3(a)" into (3, 'a'); '3a' style also works."""
    text = question_id if question_id[:1].lower() == "q" else f"Q{question_id}"
    parsed = parse_question_label(text)
    match = _LABEL_PARTS.match(parsed) if parsed else None
    if not match:
        return None, None
    return int(match.group(1)), match.group(2)


def _canonical_of(question_id: str) -> str:
    text = question_id if question_id[:1].lower() == "q" else f"Q{question_id}"
    parsed = parse_question_label(text)
    return parsed if parsed else question_id


def _label_disposition(canonical: str, expected_nums: set) -> MappingIssue:
    """Classify a detected label that matches no configured question id."""
    number, _letter = _number_letter(canonical)
    if number is not None and number in expected_nums:
        return MappingIssue.AMBIGUOUS_MAPPING
    return MappingIssue.QUESTION_NUMBER_OCR_ERROR


def _bboxes_overlap(a: List[int], b: List[int]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _entry_regions(
    blocks, mapping: AnswerMapping, doc: OcrDocument  # noqa: ARG001
) -> List[LayoutRegion]:
    per_page = {
        page.page_no: [r for r in page.regions if r.region_type.value == "answer"]
        for page in doc.pages
    }
    regions: List[LayoutRegion] = []
    seen = set()
    for mapped in blocks:
        for region in per_page.get(mapped.page_no, []):
            key = (mapped.page_no, tuple(region.bbox), region.label)
            if key in seen:
                continue
            if _bboxes_overlap(mapped.block.bbox, region.bbox):
                seen.add(key)
                regions.append(region)
    return regions


def _build_entry(
    canonical: str,
    mapped_blocks,
    doc: OcrDocument,
    settings: StructuringSettings,
    mapping: AnswerMapping,
    issues: Optional[List[MappingIssue]] = None,
) -> AnswerSheetEntry:
    issues = list(issues or [])
    blocks = [m.block for m in mapped_blocks]
    pages = sorted({m.page_no for m in mapped_blocks})
    crossed_out = any(looks_crossed_out(b.text_clean) for b in blocks)
    label_refs = {id(b) for b in mapping.label_blocks.get(canonical, [])}
    kept = [
        b for b in blocks
        if not (settings.strip_crossed_out and looks_crossed_out(b.text_clean))
    ]
    lines = [
        strip_label_text(b.text_clean) if id(b) in label_refs else b.text_clean
        for b in kept
    ]
    conf = round(sum(b.confidence for b in blocks) / len(blocks), 4) if blocks else 0.0

    entry = AnswerSheetEntry(
        question_id=canonical,
        pages=pages,
        text="\n".join(line for line in lines if line),
        blocks=blocks,
        regions=_entry_regions(mapped_blocks, mapping, doc),
        confidence=conf,
        issues=issues,
        crossed_out=crossed_out,
    )
    if crossed_out:
        entry.issues.append(MappingIssue.CROSSED_OUT)
    if len(entry.pages) > 1 and settings.merge_continuations:
        entry.issues.append(MappingIssue.CONTINUATION)
    if settings.flag_multiple_attempts:
        stretches = mapping.stretches_per_page(canonical)
        interleaved = any(count > 1 for count in stretches.values())
        separated_boxes = any(
            mapping.gap_stretches_for(canonical, page_no) > 1
            for page_no in {m.page_no for m in mapped_blocks}
        )
        if interleaved or separated_boxes:
            entry.issues.append(MappingIssue.MULTIPLE_ATTEMPTS)
    return entry


def _detect_out_of_order(mapping: AnswerMapping, expected_index: dict) -> bool:
    prev_rank: Optional[int] = None
    for canonical, _page in mapping.label_trace:
        rank = expected_index.get(canonical)
        if rank is None:
            continue
        if prev_rank is not None and rank < prev_rank:
            return True
        prev_rank = rank
    return False


def structure(
    doc: OcrDocument,
    config: Optional[ExamConfiguration] = None,
    roster: Optional[Roster] = None,
    settings: Optional[StructuringSettings] = None,
) -> StructuredAnswerSheet:
    """Parse an OCR document into a structured student answer sheet."""
    settings = settings or StructuringSettings()
    if not doc.pages:
        raise StructuringError("cannot structure an empty OCR document")

    student: StudentIdentity
    student, identity_issues = extract_student_identity(doc, roster)
    mapping = map_answers(doc)

    expected_ids: List[str] = []
    expected_nums: set = set()
    expected_index: dict = {}
    if config is not None:
        for rank, question in enumerate(config.questions):
            canonical = _canonical_of(question.question_id)
            expected_ids.append(canonical)
            expected_index[canonical] = rank
            number, _letter = _number_letter(question.question_id)
            if number is not None:
                expected_nums.add(number)
    else:
        for canonical, _page in mapping.label_trace:
            if canonical not in expected_ids:
                expected_ids.append(canonical)

    issues: List[MappingIssue] = list(identity_issues)
    # Configured order first; detected-but-unconfigured labels then, first-seen.
    ordered_ids = list(expected_ids)
    for canonical, _page in mapping.label_trace:
        if canonical not in ordered_ids:
            ordered_ids.append(canonical)

    seen = set()
    entries: List[AnswerSheetEntry] = []
    for canonical in ordered_ids:
        if canonical in seen:
            continue
        seen.add(canonical)
        blocks = mapping.by_id.get(canonical, [])
        if expected_index.get(canonical) is None and config is not None:
            gap = _label_disposition(canonical, expected_nums)
            issues.append(gap)
            entries.append(_build_entry(
                canonical, blocks, doc, settings, mapping, issues=[gap]
            ))
            continue
        if not blocks:
            entry = _build_entry(
                canonical, [], doc, settings, mapping,
                issues=[MappingIssue.BLANK_ANSWER],
            )
            entries.append(entry)
            continue
        entries.append(_build_entry(canonical, blocks, doc, settings, mapping))

    # Blocks preceding any question label -- kept for review, never merged.
    unlabelled = [m for m in mapping.unassigned if m.block.text_clean]
    if unlabelled:
        issues.append(MappingIssue.MISSING_QUESTION_NUMBER)
        entries.append(_build_entry(
            "", unlabelled, doc, settings, mapping,
            issues=[MappingIssue.MISSING_QUESTION_NUMBER],
        ))

    if settings.flag_out_of_order and _detect_out_of_order(mapping, expected_index):
        issues.append(MappingIssue.OUT_OF_ORDER)

    review_reasons: List[EvalReviewReason] = [
        _REVIEW_BY_ISSUE[i] for i in identity_issues if i in _REVIEW_BY_ISSUE
    ]
    for entry in entries:
        for issue in entry.issues:
            reason = _REVIEW_BY_ISSUE.get(issue)
            if reason is not None and reason not in review_reasons:
                review_reasons.append(reason)
    for page in doc.pages:
        for reason in page.review_reasons:
            if reason not in review_reasons:
                review_reasons.append(reason)
    if not student.identity_ok and EvalReviewReason.IDENTITY_UNCERTAIN not in review_reasons:
        review_reasons.append(EvalReviewReason.IDENTITY_UNCERTAIN)

    return StructuredAnswerSheet(
        student=student,
        answers=entries,
        issues=sorted(set(issues), key=lambda i: i.value),
        review_reasons=review_reasons,
        status=SheetStatus.REVIEW if review_reasons else SheetStatus.OK,
        source_pages=len(doc.pages),
    )


# ---------------------------------------------------------------------------
# OCR JSON reader (the `aos_v0.exam.ocr --json` surface)
# ---------------------------------------------------------------------------


def ocr_document_from_json(data) -> OcrDocument:
    """Rebuild an OcrDocument from the Phase-4 JSON surface."""
    pages_ocr = data.get("pages_ocr") if isinstance(data, dict) else data
    if not isinstance(pages_ocr, list):
        raise StructuringError("OCR JSON must be a list of {'page', 'blocks'}")
    pages = []
    for raw in pages_ocr:
        blocks = [
            OcrBlock(
                type=block.get("type", "text"),
                text=block.get("text", ""),
                bbox=block.get("bbox", [0, 0, 0, 0]),
                confidence=block.get("confidence", 0.0),
            )
            for block in raw.get("blocks", [])
        ]
        pages.append(OcrPage(
            page_no=int(raw["page"]),
            source=raw.get("source", ""),
            blocks=blocks,
            confidence=raw.get("confidence", 0.0),
        ))
    return OcrDocument(pages=pages)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_roster(path: str) -> Roster:
    try:
        with open(path, encoding="utf-8") as handle:
            return Roster.model_validate(json.load(handle))
    except Exception as exc:  # noqa: BLE001
        raise StructuringError(f"cannot load roster {path}: {exc}") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aos_v0.exam.structure",
        description=(
            "Phase 5 answer-sheet parser: identity + question mapping "
            "(out-of-order, continuations, edge cases)."
        ),
    )
    parser.add_argument(
        "source", help="OCR JSON (aos_v0.exam.ocr --json) or scan image/PDF/folder"
    )
    parser.add_argument("--config", help="exam configuration JSON/YAML (question ids)")
    parser.add_argument("--roster", help="roster JSON {'entries':[{'roll_no','name'}]}")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = load_exam_configuration(args.config) if args.config else None
        roster = _load_roster(args.roster) if args.roster else None
        source = Path(args.source)
        if source.suffix.lower() == ".json":
            with open(source, encoding="utf-8") as handle:
                doc = ocr_document_from_json(json.load(handle))
        else:
            intake_result = ingest(str(source))
            doc = run_document(intake_result.pages).document
        sheet = structure(doc, config=config, roster=roster)
    except (IntakeError, OcrError, ConfigLoadError, StructuringError,
            KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(sheet.to_plan_json(), indent=2, ensure_ascii=False))
        return 0

    print(f"Student: {sheet.student.name or '?'} / {sheet.student.roll_no or '?'} "
          f"(matched={'yes' if sheet.student.matched else 'no'})")
    for entry in sheet.answers:
        marker = " ".join(i.value for i in entry.issues) or "-"
        print(f"  {entry.question_id or '(unlabelled)':<6} pages={entry.pages} "
              f"conf={entry.confidence:.2f}  {marker}")
    print(f"Summary: status={sheet.status.value} "
          f"review={[r.value for r in sheet.review_reasons]} "
          f"issues={[i.value for i in sheet.issues]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())