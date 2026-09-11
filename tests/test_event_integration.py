import unittest

from aos_v0.agents.graph_executor import GraphExecutor
from aos_v0.core.capability_registry import CapabilityManifest, CapabilityRegistry
from aos_v0.core.events import EventType
from aos_v0.core.failure_manager import FailureManager
from aos_v0.core.models import CapabilityDNA, Graph, Node


class EventIntegrationTests(unittest.TestCase):
    def test_executor_publishes_real_agent_lifecycle(self):
        registry = CapabilityRegistry()
        registry.register(
            CapabilityManifest(resource_id="summarization", resource_class="llm",
                               capabilities=["text.summarization"]),
            lambda text, instruction=None: "A sufficiently detailed successful output for event verification.",
        )
        events = []
        graph = Graph(job="summarize", nodes=[Node(id="n1", description="Summarize", capability="summarization",
                                                    dna=CapabilityDNA(flags=["text.summarization"]))])
        GraphExecutor(registry, FailureManager(registry), events.append, "req_test").run(graph)
        event_types = [event.type for event in events]
        self.assertIn(EventType.AGENT_STARTED, event_types)
        self.assertIn(EventType.AGENT_SELECTED, event_types)
        self.assertIn(EventType.AGENT_COMPLETED, event_types)

    def test_failure_manager_publishes_recovery(self):
        registry = CapabilityRegistry()
        manager = FailureManager(registry, max_attempts=1)
        node = Node(id="bad", description="search", capability="summarization")
        events = []
        output = manager.execute(node, lambda *_args, **_kwargs: "", "fake", [], event_sink=events.append, request_id="req")
        self.assertIn("UNAVAILABLE", output)
        self.assertEqual(events[0].type, EventType.RECOVERY_STARTED)
        self.assertEqual(events[-1].type, EventType.RECOVERY_COMPLETED)
