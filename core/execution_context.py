"""Cooperative deadline for nested capability/model calls."""
from __future__ import annotations

import time
from contextvars import ContextVar

call_deadline: ContextVar[float | None] = ContextVar("maos_call_deadline", default=None)


def remaining_time(default: float) -> float:
    deadline = call_deadline.get()
    remaining = min(default, deadline - time.perf_counter()) if deadline is not None else default
    if remaining <= 0:
        raise TimeoutError("capability deadline exceeded; no additional model call started")
    return remaining
