"""Graph validation, wave scheduling, and the planner's shape."""

from __future__ import annotations

import pytest

from core.errors import GraphValidationError
from graph_utils import build_waves, descendants_of, get_sink_nodes, validate_graph
from models import Graph, Node


def _node(node_id: str, *deps: str, capability: str = "summarization") -> Node:
    return Node(
        id=node_id,
        description=f"task {node_id}",
        capability=capability,
        depends_on=list(deps),
    )


def test_waves_group_independent_nodes():
    graph = Graph(
        job="j",
        nodes=[_node("a"), _node("b"), _node("c", "a"), _node("d", "b"), _node("e", "c", "d")],
    )
    waves = [[n.id for n in wave] for wave in build_waves(graph)]
    assert waves == [["a", "b"], ["c", "d"], ["e"]]


def test_cycle_is_rejected():
    graph = Graph(job="j", nodes=[_node("a", "b"), _node("b", "a"), _node("c")])
    with pytest.raises(GraphValidationError):
        validate_graph(graph)


def test_dangling_dependency_is_rejected():
    graph = Graph(job="j", nodes=[_node("a"), _node("b", "ghost")])
    with pytest.raises(GraphValidationError, match="does not exist"):
        validate_graph(graph)


def test_self_dependency_is_rejected():
    graph = Graph(job="j", nodes=[_node("a", "a")])
    with pytest.raises(GraphValidationError, match="depends on itself"):
        validate_graph(graph)


def test_duplicate_ids_are_rejected():
    graph = Graph(job="j", nodes=[_node("a"), _node("a")])
    with pytest.raises(GraphValidationError, match="Duplicate"):
        validate_graph(graph)


def test_empty_graph_is_rejected():
    with pytest.raises(GraphValidationError, match="no nodes"):
        validate_graph(Graph(job="j", nodes=[]))


def test_oversized_graph_is_rejected():
    graph = Graph(job="j", nodes=[_node(f"n{i}") for i in range(65)])
    with pytest.raises(GraphValidationError, match="exceeding the limit"):
        validate_graph(graph)


def test_descendants_are_transitive():
    graph = Graph(job="j", nodes=[_node("a"), _node("b", "a"), _node("c", "b"), _node("d")])
    assert descendants_of(graph, "a") == {"b", "c"}
    assert descendants_of(graph, "d") == set()


def test_sink_nodes_are_terminal():
    graph = Graph(job="j", nodes=[_node("a"), _node("b", "a"), _node("c", "a")])
    assert {n.id for n in get_sink_nodes(graph)} == {"b", "c"}


def test_mermaid_escapes_label_metacharacters():
    from diagram_utils import build_mermaid

    graph = Graph(
        job="j",
        nodes=[
            Node(
                id="a-1",
                description='Read "chest" [PA] | view {urgent}',
                capability="vision",
            )
        ],
    )
    mermaid = build_mermaid(graph, build_waves(graph))
    label_line = next(line for line in mermaid.splitlines() if "Read" in line)
    assert '"' not in label_line.split('["')[1].rsplit('"]', 1)[0]
    assert "a_1" in mermaid  # id sanitised for Mermaid


class TestRadiologyPlan:
    def _study(self, count: int):
        from models import Modality, Study, StudyImage

        return Study(
            study_id="s",
            source_dir="/tmp",
            images=[
                StudyImage(
                    image_id=f"img{i}.png",
                    source_path=f"/tmp/img{i}.png",
                    content_hash=f"h{i}",
                    modality=Modality.XRAY,
                )
                for i in range(count)
            ],
        )

    def test_plan_is_a_valid_dag_with_one_read_per_image(self):
        from agents.planner import build_radiology_plan

        graph = build_radiology_plan(self._study(3))
        validate_graph(graph)

        reads = [n for n in graph.nodes if n.capability == "vision"]
        assert len(reads) == 3
        assert all(len(n.artifacts["image_ids"]) == 1 for n in reads)

    def test_report_is_the_single_sink(self):
        from agents.planner import build_radiology_plan

        graph = build_radiology_plan(self._study(2))
        sinks = get_sink_nodes(graph)
        assert len(sinks) == 1
        assert sinks[0].capability == "report_synthesis"

    def test_large_studies_are_grouped_not_fanned_out(self):
        from agents.planner import build_radiology_plan

        graph = build_radiology_plan(self._study(40))
        reads = [n for n in graph.nodes if n.capability == "vision"]
        assert len(reads) <= 12
        covered = [i for n in reads for i in n.artifacts["image_ids"]]
        assert len(covered) == 40  # every image is still read exactly once
        assert len(set(covered)) == 40

    def test_literature_stage_is_optional(self):
        from agents.planner import build_radiology_plan

        graph = build_radiology_plan(self._study(1), with_literature=False)
        assert not any(n.capability == "web_search" for n in graph.nodes)
        validate_graph(graph)

    def test_screening_runs_before_the_read(self):
        """The VLM prompt depends on classifier scores, so ordering matters."""
        from agents.planner import build_radiology_plan

        graph = build_radiology_plan(self._study(1))
        waves = build_waves(graph)
        wave_of = {n.id: i for i, wave in enumerate(waves) for n in wave}
        assert wave_of["screen"] < wave_of["read0"]
        assert wave_of["triage"] < wave_of["read0"]
        assert wave_of["consolidate"] < wave_of["report"]
