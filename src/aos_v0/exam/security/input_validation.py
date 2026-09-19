"""Input validation on the accept path (defense in depth).

The exam configuration is validated by pydantic (``extra="forbid"``) at load
time; this module adds the *path*-level checks the batch/review/reporting
accept paths need before touching the filesystem: existence checks, a strict
extension whitelist for single-file answers, a traversal guard so a source
outside the sanctioned root is refused, and structural validation of a raw
exam payload so errors surface before any evaluation runs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

MAX_SOURCE_BYTES = 500 * 1024 * 1024  # 500 MB single-file paper

# Extension whitelist shared with the batch driver's discovery.
ALLOWED_PAPER_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tiff",
    ".zip",
}


class PathIssue(BaseModel):
    """One input-validation finding. ``error=True`` means the input must be
    refused; otherwise it is a warning (e.g. unexpected extension)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    code: str
    message: str
    error: bool = False


def _issue(path: str, code: str, message: str, error: bool = False) -> PathIssue:
    return PathIssue(path=path, code=code, message=message, error=error)


def validate_paper_source(
    source: str | os.PathLike,
    *,
    root: Optional[str] = None,
    allowed: set = ALLOWED_PAPER_EXTENSIONS,
) -> List[PathIssue]:
    """Check a paper source (file/folder/zip) before it reaches the pipeline.

    Returns a list of issues; presence of any ``error`` issue means the source
    must not be processed.
    """
    issues: List[PathIssue] = []
    path = Path(source)
    if not path.exists():
        issues.append(_issue(str(path), "missing", "source does not exist", True))
        return issues

    if root is not None:
        root_path = Path(root).resolve()
        try:
            resolved = path.resolve()
        except OSError as exc:  # dangling symlink / permission error
            issues.append(
                _issue(str(path), "unresolvable", f"cannot resolve path: {exc}", True)
            )
            return issues
        if root_path not in resolved.parents and resolved != root_path:
            issues.append(
                _issue(
                    str(path),
                    "traversal",
                    "source escapes the sanctioned root",
                    True,
                )
            )

    if path.is_file():
        if path.suffix.lower() not in allowed:
            issues.append(
                _issue(
                    str(path),
                    "extension",
                    f"unsupported extension '{path.suffix}' (allowed: {sorted(allowed)})",
                    False,
                )
            )
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                issues.append(
                    _issue(str(path), "oversized", "source exceeds 500 MB limit", True)
                )
        except OSError as exc:
            issues.append(_issue(str(path), "stat", f"cannot stat file: {exc}", True))
    return issues


def validate_exam_payload(data: dict) -> List[PathIssue]:
    """Structural checks on a raw exam payload (before pydantic strict load)."""
    issues: List[PathIssue] = []
    if not isinstance(data, dict):
        return [_issue("payload", "type", "exam payload must be a JSON object", True)]
    if "exam_id" not in data or not data.get("exam_id"):
        issues.append(_issue("exam_id", "missing", "exam_id is required", True))
    questions = data.get("questions")
    if "questions" not in data:
        issues.append(
            _issue("questions", "questions", "questions section is absent", True)
        )
    if not isinstance(questions, list) or not questions:
        issues.append(_issue("questions", "missing", "no questions declared", True))
        return issues
    question_ids = [q.get("question_id") for q in questions]
    if any(not qid for qid in question_ids):
        issues.append(_issue("questions", "question_id", "a question has no id", True))
    duplicates = {qid for qid in question_ids if question_ids.count(qid) > 1}
    if duplicates:
        issues.append(
            _issue(
                "questions",
                "duplicate_question_id",
                f"duplicate question ids: {sorted(duplicates)}",
                True,
            )
        )
    for question in questions:
        max_marks = question.get("max_marks")
        if max_marks is None or not isinstance(max_marks, (int, float)) or max_marks <= 0:
            issues.append(
                _issue(
                    f"questions/{question.get('question_id')}",
                    "max_marks",
                    "max_marks must be a positive number",
                    True,
                )
            )
    return issues