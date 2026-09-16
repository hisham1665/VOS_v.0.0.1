import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from aos_v0.core.capability_registry import CapabilityRegistry
from aos_v0.core.failure_manager import FailureManager
from aos_v0.core.graph_utils import build_waves
from aos_v0.core.models import Artifact, ArtifactModalityMismatch, Graph
from aos_v0.core.events import EventType, OrchestrationEvent
from aos_v0.agents.sub_agent import SubAgent

_print_lock = threading.Lock()

_AUDIO_FLAGS = frozenset({
    "speech.transcription", "speech_recognition",
    "automatic_speech_recognition", "transcription",
    "audio_input", "audio_understanding",
    "audio_analysis", "audio_to_text",
    "multilingual_speech", "speech_understanding",
})

_IMAGE_FLAGS = frozenset({
    "vision.understanding", "vision_input",
    "image_classification", "object_detection",
    "object_identification", "object_localization",
    "multi_object_detection", "image_understanding",
    "visual_question_answering", "visual_reasoning",
    "vision_language",
})

_DOCUMENT_FLAGS = frozenset({
    "document.extraction",
})


def _safe_print(msg: str) -> None:
    with _print_lock:
        print(msg)


def _infer_modality(node_flags: set, capability: str) -> Optional[str]:
    """Infer the file modality a node needs from its DNA flags and capability."""
    if node_flags & _AUDIO_FLAGS or capability in ("speech_transcription", "audio"):
        return "audio"
    if node_flags & _IMAGE_FLAGS or capability == "vision":
        return "image"
    if node_flags & _DOCUMENT_FLAGS or capability in ("document_extraction", "document"):
        return "document"
    return None


class GraphExecutor:
    """Runs the DAG wave by wave, handing each node to a SubAgent.

    The registry is held here rather than built per node so every sub-agent
    shares one manifest table -- the Learning Manager's prior updates would
    otherwise be lost between nodes. The FailureManager is shared for the same
    reason: one fault report per workflow, not one per node.
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        failure_manager: Optional[FailureManager] = None,
        event_sink=None,
        request_id: str = "",
        memory_bank=None,
    ):
        self.registry = registry
        self.failure_manager = failure_manager or FailureManager(registry)
        self.event_sink = event_sink
        self.request_id = request_id
        self.memory_bank = memory_bank
        
        if self.memory_bank:
            from aos_v0.core.memory_admission import HeuristicController, DecisionLog
            from aos_v0.core.memory_retrieval import MemoryRetriever
            self.admission_controller = HeuristicController()
            self.decision_log = DecisionLog()
            self.memory_retriever = MemoryRetriever()
        else:
            self.admission_controller = None
            self.decision_log = None
            self.memory_retriever = None

    def _emit(self, event_type: EventType, **payload) -> None:
        if self.event_sink:
            self.event_sink(OrchestrationEvent(event_type, self.request_id, payload))

    def run(self, graph: Graph, inputs: Optional[dict[str, str]] = None) -> Graph:
        waves = build_waves(graph)
        by_id = {n.id: n for n in graph.nodes}
        inputs = inputs or {}

        # --- artifact lineage tracking ---
        # Register user-supplied input artifacts so parallel branches from the
        # same original both receive the correct file.
        artifacts: dict[str, Artifact] = dict(graph.artifacts)
        for modality, path in inputs.items():
            if modality in ("audio", "image", "document") and path:
                aid = f"input_{modality}"
                if aid not in artifacts:
                    artifacts[aid] = Artifact(
                        id=aid, modality=modality, path=path, source="user_input",
                    )
        graph.artifacts = artifacts

        for wave_idx, wave in enumerate(waves):
            _safe_print(
                f"[graph-executor] wave {wave_idx} starting "
                f"({len(wave)} nodes, running concurrently)"
            )

            def _run_node(node):
                started = time.monotonic()
                self._emit(EventType.AGENT_STARTED, node_id=node.id, agent=node.capability)
                # --- determine node input ---
                import hashlib
                
                # Base input from data_inputs or job
                if node.data_inputs:
                    parts = []
                    for aid in node.data_inputs:
                        art = artifacts.get(aid)
                        if art is None:
                            parts.append(f"[artifact '{aid}' not found]")
                            continue
                        self._validate_modality(node, art)
                        content = self._read_artifact(art)
                        parts.append(
                            f"From artifact '{aid}' ({art.modality}):\n{content}"
                        )
                    node.input = "\n\n".join(parts)
                else:
                    node_flags = set(node.dna.flags) if node.dna else set()
                    modality = _infer_modality(node_flags, node.capability)
                    if modality and modality in inputs:
                        node.input = inputs[modality]
                    else:
                        node.input = graph.job

                # Memory Retrieval and Injection
                if self.memory_bank:
                    from aos_v0.core.memory_retrieval import ContextBudget
                    budget = ContextBudget()
                    recall_result = self.memory_retriever.select(node, self.memory_bank, budget)
                    if recall_result.injected_text:
                        node.input = recall_result.injected_text + node.input
                        self._emit(EventType.MEMORY_RECALLED, node_id=node.id, 
                                   recalled_entries=[e.id for e in recall_result.selected_entries])
                else:
                    # Legacy unconditional injection (fallback if AOS_MEMORY is off)
                    if node.depends_on:
                        parts = []
                        for parent_id in node.depends_on:
                            parent = by_id[parent_id]
                            parts.append(
                                f"From node '{parent.id}' ({parent.description}):\n"
                                f"{parent.output or '(no output produced)'}"
                            )
                        node.input = "\n\n".join(parts) + "\n\n" + node.input
                        
                    # Legacy vision/audio/document context
                    vision_arts = [a for a in artifacts.values() if a.modality == "image" and a.source != "user_input"]
                    if node.capability != "vision" and vision_arts:
                        vision_ctx = "\n\n".join(f"[IMAGE IDENTIFICATION]: {self._read_artifact(a)}" for a in vision_arts)
                        node.input = f"IMPORTANT CONTEXT — an image was analyzed and the following was identified:\n{vision_ctx}\n\n---\n\n{node.input}"
                    
                    audio_arts = [a for a in artifacts.values() if a.modality == "audio" and a.source != "user_input"]
                    if node.capability not in ("speech_transcription", "audio") and audio_arts:
                        audio_ctx = "\n\n".join(f"[AUDIO TRANSCRIPTION]: {self._read_artifact(a)}" for a in audio_arts)
                        node.input = f"IMPORTANT CONTEXT — an audio file was transcribed and the following was identified:\n{audio_ctx}\n\n---\n\n{node.input}"

                    doc_arts = [a for a in artifacts.values() if a.modality == "document" and a.source != "user_input"]
                    if node.capability not in ("document_extraction", "document") and doc_arts:
                        doc_parts = [self._resolve_node_output(a, by_id) or self._read_artifact(a) for a in doc_arts]
                        doc_ctx = "\n\n".join(f"[DOCUMENT CONTENT]: {part}" for part in doc_parts)
                        node.input = f"IMPORTANT CONTEXT — a document was extracted and the following content was identified:\n{doc_ctx}\n\n---\n\n{node.input}"

                # Reuse short-circuit
                skip_execution = False
                if self.memory_bank:
                    reusable_caps = {"web_search", "document_extraction", "speech_transcription", "vision"}
                    normalized_input = f"{node.capability}|{node.input}".lower().strip()
                    input_hash = hashlib.sha256(normalized_input.encode('utf-8')).hexdigest()
                    
                    hit = self.memory_bank.lookup_reusable(node.capability, input_hash)
                    if hit and node.capability in reusable_caps:
                        node.output = hit.value
                        node.status = "done"
                        node.performed_by = "memory-reuse"
                        node.bound_resource = hit.resource_id
                        node.routing_mode = "memory"
                        self._emit(EventType.MEMORY_REUSE_HIT, node_id=node.id, saved_tokens=hit.tokens_est)
                        skip_execution = True

                if not skip_execution:

                    agent = SubAgent(
                        name=f"sub-agent-{node.id}",
                        capability=node.capability,
                        registry=self.registry,
                        failure_manager=self.failure_manager,
                        event_sink=self.event_sink,
                        request_id=self.request_id,
                    )
                    agent.perform(node)

                # Memory Admission
                if self.memory_bank and node.output and not skip_execution:
                    from aos_v0.core.shared_memory import MemoryCandidate
                    from aos_v0.core.memory_summary import build_summary_key
                    import hashlib
                    
                    normalized_val = node.output.lower().strip()
                    val_hash = hashlib.sha256(normalized_val.encode('utf-8')).hexdigest()
                    normalized_input = f"{node.capability}|{node.input}".lower().strip()
                    in_hash = hashlib.sha256(normalized_input.encode('utf-8')).hexdigest()

                    summary_key = build_summary_key(node.description, node.capability, node.output)
                    
                    candidate = MemoryCandidate(
                        node_id=node.id,
                        capability=node.capability,
                        resource_id=node.bound_resource,
                        input_hash=in_hash,
                        value_hash=val_hash,
                        tokens_est=len(node.output) // 4,
                        key=summary_key,
                        value=node.output
                    )
                    decision = self.admission_controller.decide(candidate, self.memory_bank, node)
                    
                    entry_id = None
                    if decision.admit:
                        entry = self.memory_bank.admit(candidate, decision)
                        entry_id = entry.id
                        self._emit(EventType.MEMORY_ADMITTED, node_id=node.id, entry_id=entry.id)
                    else:
                        self._emit(EventType.MEMORY_DISCARDED, node_id=node.id, reason=decision.reason)
                        
                    self.decision_log.log_decision(self.request_id, candidate, decision, self.memory_bank, entry_id)

                # Register node output as a new artifact for downstream nodes.
                if node.output and node.status != "degraded":
                    node_flags = set(node.dna.flags) if node.dna else set()
                    modality = _infer_modality(node_flags, node.capability)
                    out_modality = modality or "text"
                    out_aid = f"output_{node.id}"
                    artifacts[out_aid] = Artifact(
                        id=out_aid,
                        modality=out_modality,
                        source=f"node:{node.id}",
                    )

                _safe_print(
                    f"[graph-executor] node '{node.id}' {node.status} "
                    f"({len(node.output or '')} chars)"
                )
                elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                event_type = EventType.AGENT_COMPLETED if node.status == "done" else EventType.AGENT_FAILED
                self._emit(event_type, node_id=node.id, agent=node.capability,
                           resource=node.bound_resource, status=node.status, elapsed_ms=elapsed_ms)
                return node

            with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                futures = {pool.submit(_run_node, node): node for node in wave}
                for future in as_completed(futures):
                    future.result()

            _safe_print(f"[graph-executor] wave {wave_idx} complete")

        return graph

    # -- modality validation -------------------------------------------------

    @staticmethod
    def _validate_modality(node, artifact: Artifact) -> None:
        """Raise ArtifactModalityMismatch if the artifact doesn't match the node's input requirement."""
        node_flags = set(node.dna.flags) if node.dna else set()
        expected = _infer_modality(node_flags, node.capability)
        if expected and artifact.modality != expected:
            raise ArtifactModalityMismatch(
                node.id, artifact.id, expected, artifact.modality,
            )

    @staticmethod
    def _read_artifact(artifact: Artifact) -> str:
        """Read artifact content.

        Image/audio artifacts carry a path so vision/ASR providers can load the
        file themselves. Document artifacts are different: no provider consumes
        a raw PDF path, so return the *extracted text* so LLM nodes receive
        real content instead of a useless file path.
        """
        if artifact.path and artifact.modality == "document":
            from aos_v0.capabilities.document import run as extract_document
            try:
                content = extract_document(artifact.path)
            except Exception as exc:  # noqa: BLE001 -- degrade to a visible gap
                return f"[document extraction failed for {artifact.path}: {exc}]"
            return content
        if artifact.path:
            return artifact.path
        return f"[artifact {artifact.id} ({artifact.modality})]"

    @staticmethod
    def _resolve_node_output(artifact: Artifact, nodes_by_id: dict) -> Optional[str]:
        """For a node-produced artifact, return the node's completed text output."""
        if artifact.source.startswith("node:"):
            node_id = artifact.source.split(":", 1)[1]
            node = nodes_by_id.get(node_id)
            if node is not None and node.output:
                return node.output
        return None
