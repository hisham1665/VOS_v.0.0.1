"""Phase 11 -- Human Review and Audit.

The evaluation side *proposes* marks with evidence and flags what a human
should look at; this package is the human side: a persistent queue of
"REVIEW REQUIRED" cards, a CLI dashboard, manual mark editing, and an
append-only audit log / evaluation history per question and per paper.

Key entry points:

- ``collect_review_items(result, questions=...)`` -- turn an ``ExamRunResult``
  into the cards that need a human (row-level + paper-level).
- ``enqueue_batch_results(report, store, exam, ...)`` -- push every flagged
  card of a finished Phase-10 batch into a store, from the evidence the batch
  already recorded (never re-runs an evaluation).
- ``ReviewStore`` -- persistent queue + audit log in one directory.
- ``render_card`` / ``render_queue`` -- ASCII dashboard.
- ``review_cli_main`` -- ``python -m aos_v0.exam.review``.
"""

from __future__ import annotations

from .collector import collect_review_items, enqueue_batch_results
from .cli import review_cli_main
from .dashboard import render_card, render_queue, render_stats
from .models import (
    REVIEW_REASONS,
    AuditEvent,
    ReviewDecision,
    ReviewItem,
    ReviewStats,
    ReviewStatus,
)
from .store import ReviewStore

__all__ = [
    "collect_review_items",
    "enqueue_batch_results",
    "render_card",
    "render_queue",
    "render_stats",
    "REVIEW_REASONS",
    "AuditEvent",
    "ReviewDecision",
    "ReviewItem",
    "ReviewStats",
    "ReviewStatus",
    "ReviewStore",
    "review_cli_main",
]