"""Interactive-client adapter; contains no planning, routing or agent logic."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from aos_v0.cli import DEFAULT_BUDGET_USD, run
from aos_v0.core.artifacts import ArtifactManager
from aos_v0.core.events import EventBus, OrchestrationEvent
from aos_v0.core.runtime import RequestContext, Session


class InteractiveService:
    def __init__(self, storage_dir: str | Path = "data/artifacts") -> None:
        self.session = Session()
        self.artifacts = ArtifactManager(storage_dir)
        # Most recent upload per modality. `submit()` without explicit
        # artifact_ids defaults to these, so a newly uploaded document replaces
        # an earlier one instead of the older file winning via setdefault().
        self._latest_per_modality: dict[str, Artifact] = {}
        self.events = EventBus()
        self.events.subscribe(self._record)

    def _record(self, event: OrchestrationEvent) -> None:
        self.session.record_event(event)
        if event.type.value == "agent_completed":
            self.session.telemetry.agent_latencies_ms[event.payload["node_id"]] = event.payload.get("elapsed_ms", 0.0)
        if event.type.value == "agent_selected":
            self.session.telemetry.routing_latency_ms += event.payload.get("routing_elapsed_ms", 0.0)
            if event.payload.get("model"):
                self.session.telemetry.model_used.append(event.payload["model"])
        if event.type.value == "request_completed":
            self.session.telemetry.request_latency_ms = event.payload.get("elapsed_ms", 0.0)

    def upload(self, paths: list[str]) -> list:
        started = time.monotonic()
        uploaded = self.artifacts.register_many(paths)
        self.session.artifacts.extend(uploaded)
        for artifact in uploaded:
            self._latest_per_modality[artifact.modality] = artifact
        self.session.telemetry.artifact_processing_latency_ms += (time.monotonic() - started) * 1000
        return uploaded

    def submit(self, prompt: str, artifact_ids: list[str] | None = None, budget_usd: float = DEFAULT_BUDGET_USD) -> str:
        if artifact_ids is not None:
            selected = [
                artifact for artifact_id in artifact_ids if (artifact := self.artifacts.get(artifact_id))
            ]
        elif self._latest_per_modality:
            # Default to the single most recently uploaded file per modality.
            # This is why "upload doc A, then upload doc B, ask about B"
            # answers from B: the latest upload replaces the earlier one.
            selected = list(self._latest_per_modality.values())
        else:
            selected = self.session.artifacts
        context = RequestContext.create(self.session.session_id, prompt, selected)
        self.session.requests.append(context)
        # Existing manager/executor accepts one direct media path per modality.
        # Full references remain on graph.artifacts for any future capability.
        inputs = {}
        for artifact in selected:
            if artifact.modality in {"image", "audio", "document"} and artifact.path:
                inputs.setdefault(artifact.modality, artifact.path)
        result = run(prompt, inputs or None, budget_usd, context, self.events.emit)
        self.session.results[context.request_id] = result
        return result

    def subscribe(self, listener: Callable[[OrchestrationEvent], None]):
        return self.events.subscribe(listener)
