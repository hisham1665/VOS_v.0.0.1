"""Phase 13 ground-truth dataset.

The dataset pairs student answers with human marks so the AOS evaluation can
be measured against a human baseline (plan Ph13 "Ground-Truth Dataset"). Each
entry carries the question, the student answer, the human mark, the rubric,
the question type, and optionally an OCR image path and the OCR ground truth
text (needed for OCR-accuracy measurements). The dataset is plain JSONL so
human evaluators can annotate.marks without touching code.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_QUESTION_TYPES = {"concept", "math", "diagram", "multi", "paraphrase"}


class GroundTruthEntry(BaseModel):
    """One scored student answer (the ground-truth record)."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str
    question_id: str
    question_text: str = ""
    question_type: str = "concept"
    student_answer: str
    expected_concepts: List[str] = Field(default_factory=list)
    human_marks: float = Field(ge=0.0)
    max_marks: float = Field(default=1.0, gt=0.0)
    rubric: dict = Field(default_factory=dict)
    paraphrase: bool = Field(
        default=False, description="answer is a valid paraphrase of the key"
    )
    ocr_image: Optional[str] = Field(
        default=None, description="path to the source scan for OCR experiments"
    )
    ocr_truth: Optional[str] = Field(
        default=None, description="the human-transcribed text (OCR accuracy)"
    )
    notes: str = ""

    @field_validator("question_type")
    @classmethod
    def _valid_type(cls, value: str) -> str:
        if value not in _QUESTION_TYPES:
            raise ValueError(f"question_type must be one of {sorted(_QUESTION_TYPES)}")
        return value


class DatasetManifest(BaseModel):
    category: str = "research"
    question_count: int = 0
    entry_count: int = 0
    notes: str = ""


def build_dataset(
    exam,
    pairs: List[dict],
) -> List[GroundTruthEntry]:
    """Turn ``(question_id, answer, human_marks, **extra)`` pairs into entries,
    pulling question text / concepts / max marks from the exam configuration."""
    by_id = {q.question_id: q for q in exam.questions}
    entries: List[GroundTruthEntry] = []
    for index, pair in enumerate(pairs):
        question_id = pair["question_id"]
        question = by_id[question_id]
        concepts = [
            c.strip()
            for c in pair.get("expected_concepts", [])
            or (question.answer_key.expected_concepts if question.answer_key else [])
        ]
        entries.append(
            GroundTruthEntry(
                entry_id=pair.get("entry_id") or f"gt-{index + 1:03d}",
                question_id=question_id,
                question_text=question.text,
                question_type=pair.get("question_type", "concept"),
                student_answer=pair["answer"],
                expected_concepts=concepts,
                human_marks=pair["human_marks"],
                max_marks=question.max_marks,
                rubric=dict(question.rubric or {}),
                paraphrase=bool(pair.get("paraphrase", False)),
                ocr_image=pair.get("ocr_image"),
                ocr_truth=pair.get("ocr_truth"),
                notes=pair.get("notes", ""),
            )
        )
    return entries


_ENTRY_FILENAME = "entries.jsonl"


def write_dataset(entries: List[GroundTruthEntry], directory: str) -> dict:
    """Write the ``benchmark_dataset/`` deliverable (manifest + entries)."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, _ENTRY_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry.model_dump(mode="json")) + "\n")
    unique_questions = {entry.question_id for entry in entries}
    manifest = DatasetManifest(
        question_count=len(unique_questions),
        entry_count=len(entries),
    )
    with open(os.path.join(directory, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest.model_dump(mode="json"), handle, indent=2)
    return manifest.model_dump(mode="json")


def read_dataset(directory: str) -> List[GroundTruthEntry]:
    entries: List[GroundTruthEntry] = []
    with open(os.path.join(directory, _ENTRY_FILENAME), "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entries.append(GroundTruthEntry.model_validate(json.loads(line)))
    return entries