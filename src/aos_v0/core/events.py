"""Small, UI-independent orchestration event stream."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from threading import Lock
from typing import Any, Callable, Dict, List


class EventType(StrEnum):
    REQUEST_RECEIVED = "request_received"
    INTENT_DETECTED = "intent_detected"
    CAPABILITY_ANALYSIS_STARTED = "capability_analysis_started"
    CAPABILITY_DETECTED = "capability_detected"
    ROUTING_STARTED = "routing_started"
    AGENT_SELECTED = "agent_selected"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_COMPLETED = "recovery_completed"
    RESULT_READY = "result_ready"
    REQUEST_COMPLETED = "request_completed"
    MEMORY_ADMITTED = "memory_admitted"
    MEMORY_DISCARDED = "memory_discarded"
    MEMORY_RECALLED = "memory_recalled"
    MEMORY_REUSE_HIT = "memory_reuse_hit"
    MEMORY_EVICTED = "memory_evicted"


@dataclass(frozen=True)
class OrchestrationEvent:
    type: EventType
    request_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


EventListener = Callable[[OrchestrationEvent], None]


class EventBus:
    """Thread-safe observer used by CLI, TUI and future API adapters."""

    def __init__(self) -> None:
        self._listeners: List[EventListener] = []
        self._lock = Lock()

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def emit(self, event: OrchestrationEvent) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener(event)
