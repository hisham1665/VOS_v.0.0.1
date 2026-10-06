"""Bounded, capability-driven DAG patches. No domain-specific trigger logic.

Capabilities request additional verification through structured artifacts. Only
pending consumers can be rewired; completed/running work is immutable. Failed
work can be replaced only by an untried implementation of the same contract.
"""
from __future__ import annotations

import time
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from capabilities import registry
from core.routing import TaskRequirements
from graph_utils import MAX_NODES, validate_graph
from models import Graph, Node


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    description: str = Field(min_length=1, max_length=2000)
    requirements: TaskRequirements
    artifacts: dict = Field(default_factory=dict)


class RuntimeReplanner:
    def __init__(self, graph: Graph, started: float):
        self.graph = graph
        self.started = started
        self.added = 0
        self.replans = 0
        self.seen: set[str] = set()

    def after(self, node: Node) -> list[Node]:
        if node.artifacts.get("_adaptation_depth", 0):
            return []  # bounded one-level verification/recovery, never recursive
        if node.status == "failed":
            return self._alternative(node)
        added = []
        requests = node.artifacts.get("verification_requests") or []
        if not isinstance(requests, list):
            self._record(node, "verification", "rejected", "requests must be a list")
            return []
        for raw in requests[:8]:
            try:
                request = VerificationRequest.model_validate(raw)
                if not request.requirements.skills or not request.requirements.outputs:
                    raise ValueError("verification must declare required skills and outputs")
                if any(str(key).startswith("_") or key in {"requirements", "verification_requests"}
                       for key in request.artifacts):
                    raise ValueError("verification cannot overwrite orchestration controls")
                requirements = self._constrain(request.requirements)
                candidates, trace = registry.select(requirements)
                if not candidates:
                    self._record(node, "verification", "unavailable", request.reason, selection=trace)
                    continue
                patch = self._insert(node, candidates[0], request.description, request.reason,
                                     requirements, request.artifacts, replace=False)
                if patch:
                    added.append(patch)
            except (ValueError, TypeError) as exc:
                self._record(node, "verification", "rejected", str(exc))
        if len(requests) > 8:
            self._record(node, "verification", "rejected", "request count exceeds per-node limit of 8")
        return added

    def _constrain(self, task: TaskRequirements) -> TaskRequirements:
        values = task.to_dict()
        budget = self.graph.task.budget
        for key, value in (("max_memory_gb", budget.max_memory_gb), ("max_cost", budget.max_cost)):
            if value is not None:
                values[key] = min(value, values[key]) if values[key] is not None else value
        if budget.max_elapsed_s is not None:
            remaining = max(0, budget.max_elapsed_s - (time.perf_counter() - self.started))
            values["max_latency_s"] = min(remaining, values["max_latency_s"]) if values["max_latency_s"] is not None else remaining
        return TaskRequirements(**values)

    def _alternative(self, node: Node) -> list[Node]:
        selection = node.artifacts.get("_selection") or {}
        if not selection.get("requirements"):
            return []
        excluded = set(node.artifacts.get("_excluded_capabilities", [])) | {
            attempt["capability"] for attempt in selection.get("attempts", []) if attempt.get("status") != "budget_rejected"
        }
        requirements = self._constrain(TypeAdapter(TaskRequirements).validate_python(selection["requirements"]))
        candidates, _ = registry.select(requirements, excluded=excluded)
        if not candidates:
            return []
        artifacts = {key: deepcopy(value) for key, value in node.artifacts.items()
                     if key in {"image_ids", "document_ids", "document_page_numbers"}}
        patch = self._insert(node, candidates[0], node.description,
                             "All initial implementations failed; use an untried compatible capability",
                             requirements, artifacts, replace=True)
        if patch:
            patch.artifacts["_excluded_capabilities"] = sorted(excluded)
        return [patch] if patch else []

    def _insert(self, source, capability, description, reason, requirements, artifacts, *, replace):
        from core import cache

        kind = "replacement" if replace else "verification"
        key = cache.make_key(source.id, kind, requirements.to_dict(), artifacts)
        if key in self.seen:
            return None
        self.seen.add(key)
        budget = self.graph.task.budget
        if (self.replans >= budget.max_replans or self.added >= budget.max_added_nodes
                or len(self.graph.nodes) >= MAX_NODES):
            self._record(source, kind, "budget_exhausted", reason)
            return None
        if budget.max_elapsed_s is not None and time.perf_counter() - self.started >= budget.max_elapsed_s:
            self._record(source, kind, "budget_exhausted", "elapsed-time limit reached")
            return None

        consumers = [n for n in self.graph.nodes if n.status == "pending" and source.id in n.depends_on]
        new_id = f"adapt{self.added + 1}"
        while new_id in self.graph.by_id():
            new_id += "_"
        added = Node(id=new_id, description=description, capability=capability,
                     depends_on=list(source.depends_on) if replace else [source.id],
                     allow_partial=source.allow_partial if replace else True,
                     partial_requires=list(source.partial_requires) if replace else [],
                     artifacts={**deepcopy(artifacts), "requirements": requirements.to_dict(),
                                "_adaptation_depth": 1, "_adapted_from": source.id})
        # Validate a detached candidate before changing any live node/edge.
        candidate = self.graph.model_copy(deep=True)
        candidate.nodes.append(added.model_copy(deep=True))
        changed = {}
        for consumer in consumers:
            deps = ([added.id if dep == source.id else dep for dep in consumer.depends_on]
                    if replace else consumer.depends_on + [added.id])
            partial = ([added.id if dep == source.id else dep for dep in consumer.partial_requires]
                       if replace else list(consumer.partial_requires))
            optional = ([added.id if dep == source.id else dep for dep in consumer.optional_dependencies]
                        if replace else consumer.optional_dependencies + [added.id])
            changed[consumer.id] = (deps, partial, optional)
            candidate.by_id()[consumer.id].depends_on = deps
            candidate.by_id()[consumer.id].partial_requires = partial
            candidate.by_id()[consumer.id].optional_dependencies = optional
        validate_graph(candidate)
        for consumer in consumers:
            consumer.depends_on, consumer.partial_requires, consumer.optional_dependencies = changed[consumer.id]
        self.graph.nodes.append(added)
        self.added += 1
        self.replans += 1
        self._record(source, kind, "applied", reason, added_node=added.id, capability=capability,
                     requirements=requirements.to_dict(), rewired_consumers=list(changed),
                     revised_plan=[{"id": n.id, "capability": n.capability, "depends_on": n.depends_on}
                                   for n in self.graph.nodes])
        return added

    def _record(self, source, kind, status, reason, **extra):
        if len(self.graph.revisions) < 64:
            self.graph.revisions.append({"source_node": source.id, "kind": kind, "status": status,
                                         "reason": reason, "elapsed_s": round(time.perf_counter() - self.started, 3),
                                         **extra})
