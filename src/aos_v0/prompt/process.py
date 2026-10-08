"""
This script serves as the primary execution engine and orchestrator for the aos_v0 system,
defining the central run() pipeline that manages the full lifecycle of a user request
from input to final output.
"""

import time

from aos_v0.config import DEFAULT_BUDGET_USD
from aos_v0.agents.graph_executor import GraphExecutor
from aos_v0.agents.integrator_agent import IntegratorAgent
from aos_v0.agents.manager_agent import ManagerAgent
from aos_v0.core.constraint_policy import ConstraintPolicy
from aos_v0.core.dna_extractor import DNAExtractor
from aos_v0.core.failure_manager import FailureManager
from aos_v0.core.events import EventType, OrchestrationEvent
from aos_v0.logbook import SessionLogger
from aos_v0.services.resource_registration import build_hf_enabled_registry
from aos_v0.core.runtime import RequestContext
from aos_v0.prompt.display import _print_memory_summary, _print_routing_summary
from aos_v0.prompt.compare import _check_satisfiable
from aos_v0.core.shared_memory import SharedMemoryBank
from aos_v0.config import AOS_MEMORY


def run(
    user_prompt: str,
    inputs: dict[str, str] | None = None,
    budget_usd: float = DEFAULT_BUDGET_USD,
    context: RequestContext | None = None,
    event_sink=None,
    session_bank=None,
) -> str:
    """Execute the established kernel, optionally publishing real lifecycle events."""
    
    with SessionLogger(prompt=user_prompt, tag="run") as _logger:
        request_id = context.request_id if context else ""
        started = time.monotonic()

        # Initialize Request-tier Memory Bank if AOS_MEMORY is enabled
        request_bank = SharedMemoryBank(request_id, session_bank) if AOS_MEMORY else None

        def emit(kind: EventType, **payload) -> None:
            if event_sink:
                event_sink(OrchestrationEvent(kind, request_id, payload))

        emit(EventType.REQUEST_RECEIVED, prompt=user_prompt,
             artifacts=[artifact.model_dump() for artifact in (context.artifacts if context else [])])
        registry = build_hf_enabled_registry()

        manager = ManagerAgent()
        graph = manager.create_plan(user_prompt, inputs)
        if context:
            graph.artifacts.update({artifact.id: artifact for artifact in context.artifacts})
        emit(EventType.INTENT_DETECTED, planned_capabilities=[node.capability for node in graph.nodes])

        capability_started = time.monotonic()
        emit(EventType.CAPABILITY_ANALYSIS_STARTED, node_count=len(graph.nodes))
        graph = DNAExtractor().extract_graph(graph)
        emit(EventType.CAPABILITY_DETECTED, elapsed_ms=round((time.monotonic() - capability_started) * 1000, 2),
             nodes=[{"node_id": node.id, "flags": node.dna.flags if node.dna else []} for node in graph.nodes])

        ConstraintPolicy(registry, job_budget_usd=budget_usd).apply(graph)
        _check_satisfiable(graph, registry)
        manager.write_plan(graph)

        failure_manager = FailureManager(registry)
        emit(EventType.ROUTING_STARTED, node_count=len(graph.nodes))
        # Pass memory bank to GraphExecutor
        graph = GraphExecutor(registry, failure_manager, event_sink=event_sink, request_id=request_id, memory_bank=request_bank).run(graph, inputs)

        final_output = IntegratorAgent().integrate(graph)
        emit(EventType.RESULT_READY, result=final_output)
        emit(EventType.REQUEST_COMPLETED, elapsed_ms=round((time.monotonic() - started) * 1000, 2),
             status="completed")

        _print_routing_summary(graph)
        if request_bank:
            _print_memory_summary(request_bank)
            request_bank.promote_to_session()
            dump_file = f"log/memory_dump_{request_id or int(time.time())}.txt"
            request_bank.dump_to_text(dump_file)
            print(f"  [memory] detailed dump saved to: {dump_file}")
            
        print("\n" + failure_manager.report())

        print("\n=== FINAL OUTPUT ===")
        try:
            print(final_output)
        except UnicodeEncodeError:
            print(final_output.encode("ascii", "replace").decode("ascii"))
        print(f"\n[logbook] session log saved to: {_logger.log_path}")
        return final_output