"""Phase 5 -- OCR to structured answer sheet (implementation plan Phase 5).

Consumes the Phase-4 `OcrDocument` and produces the plan's structured answer
sheet record: student identity (+ roster validation), question detection,
out-of-order / continuation-aware question-to-answer mapping, page grouping,
and the plan's edge-case taxonomy. The parser never decides anything:
ambiguous extractions surface as `MappingIssue` + `EvalReviewReason` so they
route to human review.
"""

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
    MappedBlock,
    canonical_question_id,
    looks_crossed_out,
    looks_like_question_attempt,
    map_answers,
    parse_question_label,
)
from aos_v0.exam.structure.sheet import (
    main as cli_main,
    ocr_document_from_json,
    structure,
)

__all__ = [
    "AnswerMapping",
    "AnswerSheetEntry",
    "MappingIssue",
    "MappedBlock",
    "SheetStatus",
    "StructuringError",
    "StructuringSettings",
    "StructuredAnswerSheet",
    "StudentIdentity",
    "canonical_question_id",
    "cli_main",
    "extract_student_identity",
    "looks_crossed_out",
    "looks_like_question_attempt",
    "main",
    "map_answers",
    "ocr_document_from_json",
    "parse_question_label",
    "structure",
]

main = cli_main