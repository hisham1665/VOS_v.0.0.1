"""Batch driver data contract (plan Phase 10 -- Mass Paper Evaluation).

The driver owns per-paper status, retry accounting and the durable checkpoint;
the kernel owns nothing about the batch. An item's lifecycle is
PENDING -> PROCESSING -> (COMPLETED | REVIEW | FAILED), and a checkpoint can be
reloaded so a later run resumes exactly where the batch stopped -- completed
papers are never re-graded, an honest mass-evaluation run never restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from aos_v0.exam.orchestrator import ExamRunResult


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ItemStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    REVIEW = "review"
    FAILED = "failed"


class BatchConfig(BaseModel):
    """Tuning knobs for one batch run (driver-level, never kernel-level)."""

    model_config = ConfigDict(extra="forbid")

    max_workers: int = Field(default=4, ge=1, le=32)
    retries: int = Field(default=1, ge=0, le=5)  # extra attempts beyond the first
    checkpoint_path: Optional[Path] = None
    results_path: Optional[Path] = Field(
        default=None,
        description="append-only JSONL of each paper's ExamRunResult (Phase 12 "
                    "reporting input; driver-owned, like the checkpoint)",
    )
    timeout_seconds: Optional[float] = Field(default=None, ge=1.0)
    force: bool = False  # reprocess completed papers as well as unfinished ones


class BatchItem(BaseModel):
    """One answer sheet in the batch (discovery fills the pending items)."""

    model_config = ConfigDict(extra="forbid")

    paper_id: str
    source: str  # on-disk path of the answer sheet
    status: ItemStatus = ItemStatus.PENDING
    attempts: int = 0
    error: str = ""
    review_reasons: List[str] = Field(default_factory=list)
    total_marks: Optional[float] = None
    max_marks: Optional[float] = None
    confidence: Optional[float] = None
    student: dict = Field(default_factory=dict)
    completed_at: Optional[str] = None
    result: Optional["ExamRunResult"] = Field(
        default=None,
        description="full per-question evidence record (review-queue input)",
    )

    def to_dict(self) -> dict:
        return self.model_dump()

    def to_plan_json(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "status": self.status.value,
            "attempts": self.attempts,
            "error": self.error or None,
            "review_reasons": self.review_reasons,
            "total_marks": self.total_marks,
            "max_marks": self.max_marks,
            "confidence": self.confidence,
            "student": self.student,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BatchCheckpoint(BaseModel):
    """Durable resume point: one entry per discovered paper, nothing else."""

    version: int = 1
    started_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)
    items: List[BatchItem] = Field(default_factory=list)

    def item(self, paper_id: str) -> Optional[BatchItem]:
        for item in self.items:
            if item.paper_id == paper_id:
                return item
        return None


class BatchReport(BaseModel):
    """Batch status snapshot (the "per-document status" deliverable)."""

    model_config = ConfigDict(extra="forbid")

    totals: dict
    progress: float = 0.0  # completed+review+failed over total
    items: List[BatchItem]
    started_at: str
    finished_at: Optional[str] = None
    elapsed_ms: int = 0

    def to_plan_json(self) -> dict:
        return {
            "totals": self.totals,
            "progress": round(self.progress, 4),
            "items": [item.to_plan_json() for item in self.items],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: BatchCheckpoint,
        *,
        finished_at: Optional[str] = None,
        elapsed_ms: int = 0,
    ) -> "BatchReport":
        totals = {
            status.value: 0 for status in ItemStatus
        }
        for item in checkpoint.items:
            totals[item.status.value] += 1
        total = len(checkpoint.items) or 1
        done = totals[ItemStatus.COMPLETED.value] + totals[ItemStatus.REVIEW.value] \
            + totals[ItemStatus.FAILED.value]
        return cls(
            totals=totals,
            progress=done / total,
            items=list(checkpoint.items),
            started_at=checkpoint.started_at,
            finished_at=finished_at,
            elapsed_ms=elapsed_ms,
        )


def load_checkpoint(path: Path) -> Optional[BatchCheckpoint]:
    """Load a saved checkpoint, or None when absent / unreadable."""
    import json

    path = Path(path)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return BatchCheckpoint(**data)
    except (OSError, ValueError):  # noqa: PERF203 - malformed checkpoint is a resume-anyway
        return None


def save_checkpoint(checkpoint: BatchCheckpoint, path: Path) -> None:
    """Atomically persist the checkpoint (write temp, rename over)."""
    import json
    import os

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.updated_at = _utc_now()
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as fh:
        json.dump(checkpoint.model_dump(), fh, indent=2)
    os.replace(temp, path)