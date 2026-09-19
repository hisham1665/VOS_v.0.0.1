"""Persistent review queue + append-only audit log (Phase 11 deliverables).

The store owns two files inside its directory:

- ``review_queue.json`` — the queue of ``ReviewItem`` cards, rewritten
  atomically every time a card is resolved or a batch is enqueued.
- ``audit.jsonl`` — one immutable ``AuditEvent`` per line, append-only.

The queue answers "what still needs a human"; the audit log answers "what
happened, when, by whom, and what was the final verdict" for every question.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import List, Optional

from .models import (
    AuditEvent,
    ReviewDecision,
    ReviewItem,
    ReviewStats,
    ReviewStatus,
    utc_now_iso,
)

_QUEUE_FILENAME = "review_queue.json"
_AUDIT_FILENAME = "audit.jsonl"


def _atomic_write(path: str, text: str) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".review_tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class ReviewStore:
    """Review queue + audit log for one evaluation run (or many)."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)
        self._queue_path = os.path.join(base_dir, _QUEUE_FILENAME)
        self._audit_path = os.path.join(base_dir, _AUDIT_FILENAME)
        self._items: List[ReviewItem] = []
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._queue_path):
            return
        with open(self._queue_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self._items = [ReviewItem.model_validate(entry) for entry in payload.get("items", [])]

    def _save(self) -> None:
        payload = {"items": [item.model_dump(mode="json") for item in self._items]}
        _atomic_write(self._queue_path, json.dumps(payload, indent=2))

    def _append_event(
        self,
        *,
        flow_id: str,
        action: str,
        item: ReviewItem,
        actor: str,
        payload: Optional[dict] = None,
    ) -> None:
        event = AuditEvent(
            flow_id=flow_id,
            actor=actor,
            action=action,
            item_id=item.id,
            paper_id=item.paper_id,
            question_id=item.question_id,
            payload=payload or {},
        )
        with open(self._audit_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json")) + "\n")

    # -- queue access -------------------------------------------------------

    def enqueue(
        self,
        items: List[ReviewItem],
        *,
        flow_id: str = "bulk",
        actor: str = "system",
        force: bool = False,
    ) -> List[str]:
        """Add cards to the queue. Idempotent: an id already on the queue is
        skipped unless ``force`` re-enqueues it (replacing the old card and
        leaving a REENQUEUE audit line)."""
        known = {item.id for item in self._items}
        added: List[str] = []
        for item in items:
            existed = item.id in known
            if existed and not force:
                continue
            if existed and force:
                self._items = [old for old in self._items if old.id != item.id]
            item.status = ReviewStatus.PENDING
            self._items.append(item)
            known.add(item.id)
            added.append(item.id)
            self._append_event(
                flow_id=flow_id,
                action="REENQUEUE" if existed else "ENQUEUE",
                item=item,
                actor=actor,
                payload={"review_reasons": list(item.review_reasons)},
            )
        if added:
            self._save()
        return added

    def all(self) -> List[ReviewItem]:
        return list(self._items)

    def pending(self) -> List[ReviewItem]:
        return [item for item in self._items if item.status == ReviewStatus.PENDING]

    def reviewed(self) -> List[ReviewItem]:
        return [item for item in self._items if item.status == ReviewStatus.REVIEWED]

    def get(self, item_id: str) -> Optional[ReviewItem]:
        for item in self._items:
            if item.id == item_id:
                return item
        return None

    def stats(self) -> ReviewStats:
        counts = {status.value: 0 for status in ReviewStatus}
        for item in self._items:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        return ReviewStats(
            total=len(self._items),
            pending=counts.get(ReviewStatus.PENDING.value, 0),
            reviewed=counts.get(ReviewStatus.REVIEWED.value, 0),
            by_status=counts,
        )

    # -- manual mark editing / resolution -------------------------------------

    def resolve(
        self,
        item_id: str,
        decision: ReviewDecision,
        *,
        final_marks: Optional[float] = None,
        reviewer: str = "",
        note: str = "",
        flow_id: str = "resolve",
    ) -> ReviewItem:
        """Apply the human's verdict and append an immutable audit line.

        - ACCEPT: the proposal becomes the final mark (it must exist).
        - MODIFY: the reviewer supplies a mark within [0, max_marks].
        - ESCALATE: no final mark; the card stays REVIEWED but unresolved.
        """
        item = self.get(item_id)
        if item is None:
            raise KeyError(f"no review item '{item_id}' in the queue")

        if decision == ReviewDecision.ACCEPT:
            if item.proposed_marks is None:
                raise ValueError(
                    f"item '{item_id}' has no proposed marks to accept"
                )
            final_marks = item.proposed_marks
        elif decision == ReviewDecision.MODIFY:
            if final_marks is None:
                raise ValueError(
                    f"item '{item_id}' requires --marks <N> for modify"
                )
            if final_marks < 0 or (item.max_marks and final_marks > item.max_marks):
                raise ValueError(
                    f"item '{item_id}' final mark {final_marks} outside "
                    f"[0, {item.max_marks}]"
                )
        elif decision == ReviewDecision.ESCALATE:
            if final_marks is not None:
                raise ValueError("escalate leaves the final mark empty")
            final_marks = item.final_marks

        item.decision = decision
        item.final_marks = final_marks
        item.reviewer = reviewer
        item.note = note
        item.reviewed_at = utc_now_iso()
        item.status = ReviewStatus.REVIEWED
        self._save()
        self._append_event(
            flow_id=flow_id,
            action="RESOLVE",
            item=item,
            actor=reviewer or "reviewer",
            payload={
                "decision": decision.value,
                "final_marks": final_marks,
                "note": note,
            },
        )
        return item

    # -- audit + history ------------------------------------------------------

    def audit_events(self, paper_id: Optional[str] = None) -> List[AuditEvent]:
        if not os.path.exists(self._audit_path):
            return []
        events: List[AuditEvent] = []
        with open(self._audit_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                event = AuditEvent.model_validate(json.loads(line))
                if paper_id is None or event.paper_id == paper_id:
                    events.append(event)
        return events

    def evaluation_history(self, paper_id: str) -> List[ReviewItem]:
        """Every review card for one paper (the final state, not the deltas)."""
        return [item for item in self._items if item.paper_id == paper_id]

    def to_plan_json(self) -> dict:
        return {
            "queue": [item.to_card() for item in self._items],
            "stats": self.stats().to_plan_json(),
        }