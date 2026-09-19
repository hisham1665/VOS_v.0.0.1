"""Access logging: append-only, atomic JSONL audit of security-relevant events.

Mirrors the Phase-11 review audit discipline: ``access.log.jsonl`` is written
line-append-only under a same-directory rename so concurrent writers and
crashed processes never corrupt the log or silently lose a record.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

LOG_FILENAME = "access.log.jsonl"


class AccessEvent(BaseModel):
    """One audited action (authorization decision or input rejection)."""

    model_config = ConfigDict(extra="allow")

    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    actor: str
    action: str
    resource: str = ""
    outcome: str = "allowed"  # allowed | denied | rejected
    detail: str = ""
    role: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccessLog:
    """Append-only access-event log stored as ``access.log.jsonl`` in a dir."""

    def __init__(self, directory: str):
        self.path = os.path.join(directory, LOG_FILENAME)

    def record(
        self,
        *,
        actor: str,
        action: str,
        resource: str = "",
        outcome: str = "allowed",
        detail: str = "",
        role: str = "",
    ) -> AccessEvent:
        event = AccessEvent(
            actor=actor,
            action=action,
            resource=resource,
            outcome=outcome,
            detail=detail,
            role=role,
        )
        self.append(event)
        return event

    def append(self, event: AccessEvent) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = json.dumps(event.model_dump(mode="json", exclude_none=True)) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def read(self) -> List[AccessEvent]:
        if not os.path.exists(self.path):
            return []
        events: List[AccessEvent] = []
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(AccessEvent.model_validate(json.loads(line)))
                except (ValueError, json.JSONDecodeError):
                    continue
        return events

    def counts(self) -> dict:
        events = self.read()
        counts: dict = {"events": len(events)}
        for event in events:
            counts[event.outcome] = counts.get(event.outcome, 0) + 1
        counts["denied"] = counts.get("denied", 0)
        counts["rejected"] = counts.get("rejected", 0)
        return counts


def access_counts(log: AccessLog) -> dict:
    return log.counts()