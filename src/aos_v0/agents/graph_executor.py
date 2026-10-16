"""AOS 1.0 graph execution: bounded scheduling, provenance, and selective memory."""

import hashlib
import json
import threading
import time
from pathlib import Path

from aos_v0.agents.sub_agent import SubAgent
from aos_v0.core.capability_registry import required_input_modality
from aos_v0.core.events import EventType, OrchestrationEvent
from aos_v0.core.failure_manager import FailureManager
from aos_v0.core.graph_utils import ancestors, descendants
from aos_v0.core.models import Artifact, ArtifactModalityMismatch
from aos_v0.core.scheduler import run_dag
from aos_v0.settings import load_settings


def _infer_modality(node_flags, capability):
    return required_input_modality(list(node_flags), capability)


class GraphExecutor:
    def __init__(self, registry, failure_manager=None, event_sink=None, request_id="",
                 memory_bank=None, controller=None, retriever=None, decision_log=None,
                 memory_budget=None, reuse_enabled=True, settings=None):
        from aos_v0.core.memory_admission import HeuristicController, DecisionLog
        from aos_v0.core.memory_retrieval import ContextBudget, MemoryRetriever

        self.settings = settings or load_settings()
        self.registry = registry
        self.failure_manager = failure_manager or FailureManager(registry, self.settings.runtime.max_retries)
        self.event_sink = event_sink
        self.request_id = request_id
        self.memory_bank = memory_bank
        self.reuse_enabled = reuse_enabled
        self.admission_controller = controller or HeuristicController()
        self.memory_retriever = retriever or MemoryRetriever()
        self.decision_log = decision_log or DecisionLog(request_id)
        self.memory_budget = memory_budget or ContextBudget()
        self._lock = threading.RLock()

    def _emit(self, kind, **payload):
        if self.event_sink:
            self.event_sink(OrchestrationEvent(kind, self.request_id, payload))

    def run(self, graph, inputs=None):
        inputs = inputs or {}
        if len(graph.nodes) > self.settings.runtime.max_nodes:
            raise ValueError(f"graph exceeds runtime.max_nodes={self.settings.runtime.max_nodes}")
        by_id = {node.id: node for node in graph.nodes}
        for modality, path in inputs.items():
            if modality in {"image", "audio", "document"}:
                aid = f"input_{modality}"
                graph.artifacts.setdefault(aid, Artifact(id=aid, modality=modality, path=path))
        graph.original_plan = graph.model_dump(exclude={"original_plan"})

        def execute(node):
            started = time.monotonic()
            self._emit(EventType.AGENT_STARTED, node_id=node.id, agent=node.capability)
            lineage = ancestors(graph, node.id)
            modality = _infer_modality(node.dna.flags if node.dna else [], node.capability)
            with self._lock:
                artifacts = dict(graph.artifacts)
            if node.data_inputs:
                selected = [artifacts[aid] for aid in node.data_inputs]
                for artifact in selected:
                    self._validate_modality(node, artifact)
                if modality:
                    if len(selected) != 1 or not selected[0].path:
                        raise ValueError("media reader requires one original file artifact")
                    node.input = selected[0].path
                else:
                    node.input = "\n\n".join(self._read_artifact(a) for a in selected)
            elif modality:
                node.input = inputs.get(modality)
                if not node.input:
                    raise ValueError(f"missing {modality} input for {node.id}")
            elif node.depends_on:
                node.input = "\n\n".join(
                    f"From node '{parent}':\n{by_id[parent].output or '[UNAVAILABLE]'}"
                    for parent in node.depends_on
                )
                node.parent_data = {parent: by_id[parent].result.data for parent in node.depends_on
                                    if by_id[parent].result is not None}
            else:
                node.input = node.input or graph.job

            # Reuse identity is exact and includes instructions, model profile,
            # and file contents. Changing a file in place invalidates the hit.
            from aos_v0.core.shared_memory import MemoryCandidate, compute_input_hash
            identity = {"input": node.input, "instruction": node.description,
                        "profiles": self.settings.model_dump()["models"]}
            if modality and node.input and Path(node.input).is_file():
                with Path(node.input).open("rb") as handle:
                    identity["file_hash"] = hashlib.file_digest(handle, "sha256").hexdigest()
            input_hash = compute_input_hash(node.capability, json.dumps(identity, sort_keys=True))
            reusable = {"document_extraction", "speech_transcription", "vision"}
            hit = self.memory_bank.lookup_reusable(node.capability, input_hash) if self.memory_bank and self.reuse_enabled else None
            if hit and node.capability in reusable:
                node.output, node.status = hit.value, "done"
                node.performed_by, node.routing_mode = "memory-reuse", "memory"
                node.bound_resource = hit.resource_id
                self.memory_bank.get(hit.id, recalled_by=node.id)
            else:
                if not modality and self.memory_bank:
                    recall = self.memory_retriever.select(node, self.memory_bank, lineage, self.memory_budget)
                    node.input = recall.injected_text + node.input
                elif not modality:
                    # Preserve legacy media evidence, restricted to ancestors.
                    context = [a for a in artifacts.values() if a.source.startswith("node:")
                               and a.source[5:] in lineage and a.modality in {"document", "image", "audio"}]
                    if context:
                        node.input = "IMPORTANT CONTEXT:\n" + "\n".join(self._read_artifact(a) for a in context) + "\n\n" + node.input
                SubAgent(f"sub-agent-{node.id}", node.capability, self.registry, self.failure_manager,
                         self.event_sink, self.request_id).perform(node)
                if self.memory_bank and node.output:
                    from aos_v0.core.memory_summary import build_summary_key
                    candidate = MemoryCandidate(
                        node_id=node.id, capability=node.capability, description=node.description,
                        resource_id=node.bound_resource, status=node.status, value=node.output,
                        agent_input=node.input, input_hash=input_hash, fanout=len(descendants(graph, node.id)),
                        key=build_summary_key(node.id, node.capability, node.description),
                    )
                    decision = self.admission_controller.decide(candidate, self.memory_bank, node)
                    entry = self.memory_bank.admit(candidate, decision) if decision.admit else None
                    self.decision_log.record(candidate, decision, entry_id=entry.id if entry else None)
            if node.output and node.status == "done":
                aid = f"output_{node.id}"
                with self._lock:
                    graph.artifacts[aid] = Artifact(id=aid, modality=modality or "text", content=node.output,
                                                   source=f"node:{node.id}", data=node.result.data if node.result else {})
            self._emit(EventType.AGENT_COMPLETED if node.status == "done" else EventType.AGENT_FAILED,
                       node_id=node.id, agent=node.capability, status=node.status, resource=node.bound_resource,
                       elapsed_ms=round((time.monotonic() - started) * 1000, 2))

        def failed(node, exc):
            node.status, node.error = "failed", f"{type(exc).__name__}: {exc}"
            node.output = f"[UNAVAILABLE — {node.id}: {node.error}]"
            self._emit(EventType.AGENT_FAILED, node_id=node.id, error=node.error)

        return run_dag(graph, execute, max_workers=self.settings.runtime.max_workers,
                       max_elapsed_s=self.settings.runtime.max_elapsed_s, on_error=failed)

    @staticmethod
    def _validate_modality(node, artifact):
        expected = _infer_modality(node.dna.flags if node.dna else [], node.capability)
        if expected and expected != artifact.modality:
            raise ArtifactModalityMismatch(node.id, artifact.id, expected, artifact.modality)

    @staticmethod
    def _read_artifact(artifact):
        if artifact.content is not None:
            return artifact.content
        if artifact.path and artifact.modality == "document":
            from aos_v0.capabilities.document import run
            return run(artifact.path)
        return artifact.path or f"[UNAVAILABLE artifact {artifact.id}]"

    @staticmethod
    def _resolve_node_output(artifact, nodes_by_id):
        node = nodes_by_id.get(artifact.source.removeprefix("node:"))
        return node.output if node else None
