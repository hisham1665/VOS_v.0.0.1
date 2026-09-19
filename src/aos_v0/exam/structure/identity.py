"""Student identity extraction + roster validation (plan Phase 5).

Reads the header evidence the Phase-4 layout produced (HEADER regions, or
HEADER-typed OCR blocks), pulls name / roll / register / class / department /
exam / subject via labelled-value patterns, and -- when an institutional
`Roster` is supplied -- validates the extracted roll number as evidence
(matching roll joiner and name cross-check). Never decides: a missing or
unverifiable identity yields `IDENTITY_UNVERIFIED` / `IDENTITY_MISMATCH` so the
paper reaches review.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from aos_v0.exam.models import Roster
from aos_v0.exam.ocr.models import (
    OcrBlock,
    OcrBlockType,
    OcrDocument,
    RegionType,
)
from aos_v0.exam.structure.models import MappingIssue, StudentIdentity

_FIELD_PATTERNS = {
    "name": re.compile(
        r"(?i)\b(?:student\s+)?name\s*[:.\-]\s*([A-Za-z][A-Za-z .'\-]{1,40})"
    ),
    "roll_no": re.compile(
        r"(?i)\broll\s*(?:no|number)?\.?\s*[:.\-]\s*([A-Za-z0-9]{2,20})"
    ),
    "register_no": re.compile(
        r"(?i)\bregister\s*(?:no|number)?\.?\s*[:.\-]\s*([A-Za-z0-9]{2,20})"
    ),
    "class_": re.compile(
        r"(?i)\bclass\s*[:.\-]\s*([A-Za-z0-9][A-Za-z0-9 _/-]{0,19})"
    ),
    "department": re.compile(
        r"(?i)\b(?:dept|department)\s*[:.\-]\s*([A-Za-z][A-Za-z ).,]{0,24})"
    ),
    "exam": re.compile(
        r"(?i)\bexam(?:ination)?\s*[:.\-]\s*([^\n]{1,40})"
    ),
    "subject": re.compile(r"(?i)\bsubject\s*[:.\-]\s*([^\n]{1,40})"),
}


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _bbox_overlaps(a: List[int], b: List[int]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _header_blocks(doc: OcrDocument) -> List[OcrBlock]:
    """Header evidence: HEADER regions' blocks, else HEADER-typed blocks."""
    blocks: List[OcrBlock] = []
    for page in doc.pages:
        header_regions = [r for r in page.regions if r.region_type == RegionType.HEADER]
        if header_regions:
            for region in header_regions:
                blocks.extend(
                    b for b in page.blocks if _bbox_overlaps(b.bbox, region.bbox)
                )
        else:
            blocks.extend(b for b in page.blocks if b.type == OcrBlockType.HEADER)
    return blocks


def _identity_text(blocks: List[OcrBlock]) -> str:
    return "\n".join(b.text_clean for b in blocks if b.text_clean)


def _candidate_text(doc: OcrDocument) -> Tuple[str, int]:
    """Header text plus the page it came from (earliest wins)."""
    if not doc.pages:
        return "", 0
    for page in doc.pages:
        header_regions = [r for r in page.regions if r.region_type == RegionType.HEADER]
        if header_regions:
            blocks = [b for b in page.blocks if any(
                _bbox_overlaps(b.bbox, region.bbox) for region in header_regions
            )]
        else:
            blocks = [b for b in page.blocks if b.type == OcrBlockType.HEADER]
        if blocks:
            text = _identity_text(blocks)
            if text:
                return text, page.page_no
    first = doc.pages[0]
    return _identity_text(first.blocks), first.page_no


def extract_student_identity(
    doc: OcrDocument, roster: Optional[Roster] = None
) -> Tuple[StudentIdentity, List[MappingIssue]]:
    """Extract candidate identity from header evidence + validate the roster."""
    text, source_page = _candidate_text(doc)
    issues: List[MappingIssue] = []

    fields: dict = {}
    for field, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(text)
        if match:
            fields[field] = match.group(1).strip()

    students = StudentIdentity(
        name=fields.get("name", ""),
        roll_no=fields.get("roll_no", ""),
        register_no=fields.get("register_no", ""),
        class_=fields.get("class_", ""),
        department=fields.get("department", ""),
        exam=fields.get("exam", ""),
        subject=fields.get("subject", ""),
        source_page=source_page,
    )
    total = len(_FIELD_PATTERNS)
    students.confidence = round(students.populated_fields / total, 4) if text else 0.0

    if roster is not None and text:
        match = None
        for entry in roster.entries:
            if _normalize(entry.roll_no) == _normalize(students.roll_no):
                match = entry
                break
        if match is None:
            issues.append(MappingIssue.IDENTITY_UNVERIFIED)
        else:
            students.matched = True
            students.roster_roll = match.roll_no
            students.roster_name = match.name or ""
            if (
                students.name
                and match.name
                and _normalize(students.name) != _normalize(match.name)
            ):
                issues.append(MappingIssue.IDENTITY_MISMATCH)
    elif roster is not None:
        issues.append(MappingIssue.IDENTITY_UNVERIFIED)

    if not students.identity_ok:
        issues.append(MappingIssue.IDENTITY_UNVERIFIED)

    return students, issues