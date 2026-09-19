"""JSON export of the Phase 12 report set."""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

from .models import ClassAnalytics, PaperRecord


def export_payload(
    records: Sequence[PaperRecord], analytics: ClassAnalytics, **extra: Any
) -> dict:
    payload = {
        "papers": [record.model_dump(mode="json") for record in records],
        "analytics": analytics.to_plan_json(),
    }
    payload.update(**extra)
    return payload


def export_json(
    records: Sequence[PaperRecord],
    analytics: ClassAnalytics,
    target: Union[str, Any],
    **extra: Any,
) -> None:
    """Write the JSON export (the plan's JSON-export deliverable)."""
    text = json.dumps(export_payload(records, analytics, **extra), indent=2)
    if isinstance(target, str):
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        target.write(text)