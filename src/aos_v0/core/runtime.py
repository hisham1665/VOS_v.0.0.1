"""Request/session state and central telemetry for interactive clients."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

from aos_v0.core.events import OrchestrationEvent
from aos_v0.core.models import Artifact


@dataclass
class RequestContext:
    request_id: str
    session_id: str
    user_input: str
    artifacts: List[Artifact] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, session_id: str, user_input: str, artifacts: List[Artifact] | None = None) -> "RequestContext":
        return cls(f"req_{uuid.uuid4().hex[:12]}", session_id, user_input, artifacts or [])


@dataclass
class Telemetry:
    request_latency_ms: float = 0.0
    capability_latency_ms: float = 0.0
    routing_latency_ms: float = 0.0
    artifact_processing_latency_ms: float = 0.0
    agent_latencies_ms: Dict[str, float] = field(default_factory=dict)
    failures: int = 0
    recoveries: int = 0
    model_used: List[str] = field(default_factory=list)


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: f"aos_{uuid.uuid4().hex[:8]}")
    requests: List[RequestContext] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)
    events: List[OrchestrationEvent] = field(default_factory=list)
    results: Dict[str, str] = field(default_factory=dict)
    telemetry: Telemetry = field(default_factory=Telemetry)
    started_at: float = field(default_factory=time.monotonic)

    def record_event(self, event: OrchestrationEvent) -> None:
        self.events.append(event)
        if event.type.value == "agent_failed": self.telemetry.failures += 1
        if event.type.value == "recovery_completed" and event.payload.get("recovered"):
            self.telemetry.recoveries += 1

    def summary(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "requests": len(self.requests), "artifacts": len(self.artifacts),
                "agents_executed": len(self.telemetry.agent_latencies_ms), "failures": self.telemetry.failures,
                "recoveries": self.telemetry.recoveries, "total_execution_time_s": round(time.monotonic() - self.started_at, 2)}
