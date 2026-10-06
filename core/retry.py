"""Retry with exponential backoff and jitter.

Model endpoints fail transiently: cold starts, 429s, dropped sockets. Without
this, one flaky call kills an entire study run.
"""

from __future__ import annotations

import random
import time
from typing import Callable, Iterable, TypeVar

from core.logging_utils import get_logger

T = TypeVar("T")
_log = get_logger("retry")

# Errors worth retrying. Anything else (bad argument, missing file) is fatal
# immediately -- retrying a deterministic failure just wastes wall clock.
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "temporarily",
    "rate limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    "overloaded",
    "currently loading",
    "model is loading",
)


def is_transient(exc: BaseException) -> bool:
    """Heuristically decide whether ``exc`` is worth a retry."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    label: str = "call",
    retry_on: Callable[[BaseException], bool] = is_transient,
) -> T:
    """Call ``fn`` up to ``attempts`` times, backing off between tries."""
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            from core.execution_context import call_deadline, remaining_time
            if call_deadline.get() is not None:
                remaining_time(float("inf"))
            return fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            last = exc
            if attempt >= attempts or not retry_on(exc):
                raise
            delay = base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, base_delay * 0.5)
            if call_deadline.get() is not None:
                if delay >= remaining_time(float("inf")):
                    raise TimeoutError("capability deadline leaves no time for another retry") from exc
            _log.warning(
                "%s failed (attempt %d/%d): %s -- retrying in %.1fs",
                label,
                attempt,
                attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    assert last is not None
    raise last


def first_success(
    candidates: Iterable[Callable[[], T]], *, label: str = "fallback chain"
) -> T:
    """Try each callable in order, returning the first that succeeds.

    Used to fall back from a gated model to an open one, or from a remote
    endpoint to a local checkpoint.
    """
    errors: list[str] = []
    for idx, candidate in enumerate(candidates):
        try:
            return candidate()
        except Exception as exc:  # noqa: BLE001 - aggregated below
            _log.warning("%s: option %d failed: %s", label, idx, exc)
            errors.append(f"{type(exc).__name__}: {exc}")
    raise RuntimeError(f"{label}: all options failed -> {errors}")
