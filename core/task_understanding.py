"""Normalize task intent, evidence and acceptance independently of any domain."""
from __future__ import annotations

from models import AcceptanceCriterion, Graph, TaskBudget, TaskEvidence, TaskUnderstanding


def understand_task(
    intent: str, *, evidence: list[TaskEvidence] | None = None,
    missing_evidence: list[str] | None = None,
    acceptance_criteria: list[AcceptanceCriterion] | None = None,
    budget: TaskBudget | None = None,
) -> TaskUnderstanding:
    from config import RUNTIME

    return TaskUnderstanding(
        intent=" ".join(intent.split()), evidence=evidence or [],
        missing_evidence=missing_evidence or [], acceptance_criteria=acceptance_criteria or [],
        budget=budget or TaskBudget(max_replans=RUNTIME.max_replans,
                                   max_added_nodes=RUNTIME.max_added_nodes),
    )


def bind_plan(graph: Graph) -> None:
    """Record the actual live skills required by the chosen plan."""
    from capabilities import registry

    if graph.task is None:
        graph.task = understand_task(graph.job)
    metadata = registry.metadata()
    graph.task.required_skills = sorted({
        skill for node in graph.nodes
        for skill in metadata.get(node.capability, {}).get("skills", [node.capability])
    })
    if not graph.task.acceptance_criteria:
        from graph_utils import get_sink_nodes

        for sink in get_sink_nodes(graph):
            outputs = metadata.get(sink.capability, {}).get("outputs", ["text"])
            graph.task.acceptance_criteria.append(AcceptanceCriterion(
                id=f"deliverable:{sink.id}", requirement=f"Produce the declared output of {sink.id}",
                output_keys=[f"{sink.id}:{key}" for key in outputs],
            ))


def evaluate_acceptance(graph: Graph) -> list[dict]:
    """Structural output/source coverage, never a semantic correctness verdict."""
    artifacts: dict = {}
    for node in graph.nodes:
        if node.succeeded:
            artifacts[f"{node.id}:text"] = node.output
            for key, value in node.artifacts.items():
                artifacts[f"{node.id}:{key}"] = value
                if isinstance(value, dict) and isinstance(artifacts.get(key), dict):
                    artifacts[key] = {**artifacts[key], **value}
                elif value is not None:
                    artifacts[key] = value
    results = []
    for criterion in graph.task.acceptance_criteria if graph.task else []:
        missing_keys = [key for key in criterion.output_keys if not artifacts.get(key)]
        covered = set()
        for key in criterion.coverage_keys:
            value = artifacts.get(key)
            if isinstance(value, dict):
                covered.update(source for source, output in value.items() if output)
            elif isinstance(value, list):
                covered.update(item for item in value if isinstance(item, str))
        missing_sources = sorted(set(criterion.source_ids) - covered)
        results.append({"id": criterion.id, "requirement": criterion.requirement,
                        "status": "unmet" if missing_keys or missing_sources else "met",
                        "missing_outputs": missing_keys, "missing_sources": missing_sources,
                        "scope": "structural coverage only; clinician verification still required"})
    return results
