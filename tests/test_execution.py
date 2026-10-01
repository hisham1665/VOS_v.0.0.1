"""Executor behaviour under failure, and artifact propagation along edges.

These are the tests that cover the original pipeline's worst failure mode: one
capability raising took down the entire run, losing every other image's work.
"""

from __future__ import annotations

import threading
import time

import pytest

from agents.graph_executor import GraphExecutor
from capabilities.registry import CapabilityResult
from models import Graph, Node


def _node(node_id: str, *deps: str, capability: str = "stub") -> Node:
    return Node(
        id=node_id,
        description=f"task {node_id}",
        capability=capability,
        depends_on=list(deps),
    )


class TestFailureContainment:
    def test_failed_node_skips_only_its_descendants(self, fake_capability):
        def boom(_request):
            raise RuntimeError("model exploded")

        fake_capability("stub", lambda r: CapabilityResult(text=f"ok {r.node_id}"))
        fake_capability("bad", boom)

        graph = Graph(
            job="j",
            nodes=[
                _node("a"),
                _node("b", capability="bad"),
                _node("child_of_b", "b"),
                _node("c", "a"),
            ],
        )
        GraphExecutor().run(graph)

        status = {n.id: n.status for n in graph.nodes}
        assert status == {
            "a": "done",
            "b": "failed",
            "child_of_b": "skipped",
            "c": "done",  # the independent branch completed
        }

    def test_failure_records_the_reason(self, fake_capability):
        def boom(_request):
            raise ValueError("bad input")

        fake_capability("bad", boom)
        graph = Graph(job="j", nodes=[_node("a", capability="bad")])

        GraphExecutor().run(graph)

        assert "ValueError: bad input" in graph.nodes[0].error
        assert graph.nodes[0].duration_s is not None

    def test_unknown_capability_fails_that_node_only(self, fake_capability):
        fake_capability("stub", lambda r: CapabilityResult(text="ok"))
        graph = Graph(
            job="j", nodes=[_node("a"), _node("b", capability="does_not_exist")]
        )

        GraphExecutor().run(graph)

        assert graph.nodes[0].status == "done"
        assert graph.nodes[1].status == "failed"
        assert "Unknown capability" in graph.nodes[1].error

    def test_empty_output_is_treated_as_failure(self, fake_capability):
        fake_capability("empty", lambda r: CapabilityResult(text="   "))
        graph = Graph(job="j", nodes=[_node("a", capability="empty")])

        GraphExecutor().run(graph)

        assert graph.nodes[0].status == "failed"
        assert "no output" in graph.nodes[0].error

    def test_wrong_return_type_is_caught(self, fake_capability):
        fake_capability("wrong", lambda r: "just a string")
        graph = Graph(job="j", nodes=[_node("a", capability="wrong")])

        GraphExecutor().run(graph)

        assert graph.nodes[0].status == "failed"
        assert "expected CapabilityResult" in graph.nodes[0].error

    def test_slow_capability_times_out(self, fake_capability, override_runtime):
        override_runtime(call_timeout=0.3)

        def slow(_request):
            time.sleep(5)
            return CapabilityResult(text="too late")

        fake_capability("slow", slow)
        graph = Graph(job="j", nodes=[_node("a", capability="slow")])

        started = time.perf_counter()
        GraphExecutor().run(graph)
        elapsed = time.perf_counter() - started

        assert graph.nodes[0].status == "failed"
        assert "exceeded" in graph.nodes[0].error
        assert elapsed < 4  # did not wait for the full sleep


class TestConcurrency:
    def test_independent_nodes_run_in_parallel(self, fake_capability):
        barrier = threading.Barrier(3, timeout=5)

        def synchronised(_request):
            # Deadlocks unless all three nodes are genuinely concurrent.
            barrier.wait()
            return CapabilityResult(text="ok")

        fake_capability("sync", synchronised)
        graph = Graph(
            job="j",
            nodes=[_node(n, capability="sync") for n in ("a", "b", "c")],
        )

        GraphExecutor(max_workers=4).run(graph)

        assert all(n.status == "done" for n in graph.nodes)

    def test_worker_ceiling_is_respected(self, fake_capability, override_runtime):
        override_runtime(call_timeout=10)
        active = 0
        peak = 0
        lock = threading.Lock()

        def tracked(_request):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return CapabilityResult(text="ok")

        fake_capability("tracked", tracked)
        graph = Graph(
            job="j", nodes=[_node(f"n{i}", capability="tracked") for i in range(10)]
        )

        GraphExecutor(max_workers=2).run(graph)

        assert peak <= 2
        assert all(n.status == "done" for n in graph.nodes)


class TestDataFlow:
    def test_parent_text_reaches_the_child(self, fake_capability):
        seen: dict[str, str] = {}

        def recorder(request):
            seen[request.node_id] = request.text
            return CapabilityResult(text=f"output of {request.node_id}")

        fake_capability("rec", recorder)
        graph = Graph(
            job="the original job",
            nodes=[_node("a", capability="rec"), _node("b", "a", capability="rec")],
        )

        GraphExecutor().run(graph)

        assert seen["a"] == "the original job"  # root nodes receive the job
        assert "output of a" in seen["b"]

    def test_artifacts_propagate_downstream(self, fake_capability):
        received: dict[str, dict] = {}

        def producer(request):
            return CapabilityResult(
                text="scores", artifacts={"classification": {"img1": [{"label": "x"}]}}
            )

        def consumer(request):
            received[request.node_id] = request.artifacts
            return CapabilityResult(text="read")

        fake_capability("produce", producer)
        fake_capability("consume", consumer)

        graph = Graph(
            job="j",
            nodes=[
                _node("screen", capability="produce"),
                _node("read", "screen", capability="consume"),
            ],
        )
        GraphExecutor().run(graph)

        assert received["read"]["classification"] == {"img1": [{"label": "x"}]}

    def test_multi_parent_artifacts_merge(self, fake_capability):
        received: dict[str, dict] = {}

        def make_producer(key, value):
            return lambda r: CapabilityResult(text=key, artifacts={key: value})

        fake_capability("p1", make_producer("findings", [{"label": "a"}]))
        fake_capability("p2", make_producer("references", ["ref1"]))
        fake_capability(
            "merge",
            lambda r: (received.update({r.node_id: r.artifacts}), CapabilityResult(text="m"))[1],
        )

        graph = Graph(
            job="j",
            nodes=[
                _node("x", capability="p1"),
                _node("y", capability="p2"),
                _node("m", "x", "y", capability="merge"),
            ],
        )
        GraphExecutor().run(graph)

        assert received["m"]["findings"] == [{"label": "a"}]
        assert received["m"]["references"] == ["ref1"]

    def test_image_assignment_reaches_the_capability(self, fake_capability):
        seen: dict[str, list] = {}

        fake_capability(
            "imgcap",
            lambda r: (seen.update({r.node_id: r.image_ids}), CapabilityResult(text="ok"))[1],
        )

        node = _node("read0", capability="imgcap")
        node.artifacts["image_ids"] = ["a.png", "b.png"]
        GraphExecutor().run(Graph(job="j", nodes=[node]))

        assert seen["read0"] == ["a.png", "b.png"]

    def test_long_upstream_output_is_truncated(self, fake_capability):
        lengths: dict[str, int] = {}

        fake_capability("big", lambda r: CapabilityResult(text="x" * 50000))
        fake_capability(
            "measure",
            lambda r: (lengths.update({r.node_id: len(r.text)}), CapabilityResult(text="ok"))[1],
        )

        graph = Graph(
            job="j",
            nodes=[_node("a", capability="big"), _node("b", "a", capability="measure")],
        )
        GraphExecutor().run(graph)

        assert lengths["b"] < 31000
        assert lengths["b"] > 1000


class TestSubAgentContract:
    @pytest.mark.parametrize("status", ["done", "failed"])
    def test_node_always_reaches_a_terminal_state(self, fake_capability, status):
        if status == "done":
            fake_capability("t", lambda r: CapabilityResult(text="fine"))
        else:
            fake_capability("t", lambda r: (_ for _ in ()).throw(RuntimeError("no")))

        graph = Graph(job="j", nodes=[_node("a", capability="t")])
        GraphExecutor().run(graph)

        assert graph.nodes[0].status == status
        assert graph.nodes[0].status not in {"pending", "running"}
