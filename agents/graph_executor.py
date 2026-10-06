"""Dependency-ready parallel execution of the task graph.

Changes from the original executor, each fixing a concrete failure:

* **One bounded pool for the whole run** instead of ``max_workers=len(wave)``
  per wave. A wide study used to spawn one thread per image and, on an empty
  wave, ``ThreadPoolExecutor(max_workers=0)`` raised outright.
* **Failures are contained.** A failed node blocks strict descendants, while
  explicitly best-effort joins continue with successful branches.
* **Artifacts flow down the edges.** Parents hand structured data to children,
  not just prose -- that is how the classifier's scores reach the VLM prompt.
* **Ready scheduling.** Children start when their own parents finish, without
  waiting for unrelated slow roots. Each worker owns one node; downstream
  inputs are assembled on the orchestrating thread from terminal parents.
"""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from config import RUNTIME
from core.logging_utils import get_logger
from core.replanning import RuntimeReplanner
from core.task_understanding import bind_plan, evaluate_acceptance
from agents.sub_agent import SubAgent
from graph_utils import build_waves
from models import Graph, Node, Study

_log = get_logger("executor")

# Text handed to any single node is capped so one verbose upstream node cannot
# blow the context window of everything downstream.
_MAX_INPUT_CHARS = 30000


class GraphExecutor:
    """Runs a dependency-ready DAG without global wave barriers."""

    def __init__(self, max_workers: int | None = None):
        self.max_workers = max(1, max_workers or RUNTIME.max_workers)

    def run(self, graph: Graph, study: Study | None = None) -> Graph:
        if graph.original_plan:
            graph.nodes = [Node.model_validate(row) for row in graph.original_plan]
        build_waves(graph)  # validate the DAG before invoking any work
        bind_plan(graph)
        graph.original_plan = [node.model_dump(mode="json") for node in graph.nodes]
        graph.revisions = []
        graph.acceptance_results = []
        by_id = graph.by_id()
        started = time.perf_counter()
        replanner = RuntimeReplanner(graph, started)
        reserved_cost = 0.0
        for node in graph.nodes:
            node.status = "pending"
            node.error = None
            node.output = None

        with ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="maos-node"
        ) as pool:
            pending = list(graph.nodes)
            futures = {}
            while pending or futures:
                progressed = False
                in_flight = {n.id for n in futures.values()}
                for node in list(pending):
                    parents = [by_id.get(dep) for dep in node.depends_on]
                    if any(parent is not None and (parent.id in in_flight or parent.status not in {"done", "failed", "skipped"}) for parent in parents):
                        continue
                    if len(futures) >= self.max_workers:
                        break
                    failed = [
                        parent for parent in parents
                        if parent is not None and parent.status in {"failed", "skipped"}
                        and parent.id not in node.optional_dependencies
                    ]
                    successful = [
                        parent for parent in parents
                        if parent is not None and parent.status == "done"
                    ]
                    if node.partial_requires:
                        successful = [
                            parent for parent in successful
                            if parent.id in node.partial_requires
                        ]
                    if failed and not (node.allow_partial and successful):
                        node.status = "skipped"
                        node.error = node.error or "upstream dependency failed"
                    else:
                        node.input, inherited = self._assemble_input(node, by_id, graph)
                        budget = graph.task.budget
                        remaining = (budget.max_elapsed_s - (time.perf_counter() - started)
                                     if budget.max_elapsed_s is not None else None)
                        if remaining is not None and remaining <= 0:
                            node.status = "skipped"
                            node.error = "task elapsed-time budget exhausted"
                            pending.remove(node)
                            progressed = True
                            continue
                        remaining_cost = budget.max_cost - reserved_cost if budget.max_cost is not None else None
                        reservation = self._reserve_cost(node, budget.max_attempts_per_node, remaining_cost)
                        if reservation is None:
                            node.status = "failed"
                            node.error = "No compatible capability with a known cost fits the remaining task budget"
                            pending.remove(node)
                            progressed = True
                            continue
                        reserved_cost += reservation
                        node.artifacts["_cost_reservation"] = reservation if remaining_cost is not None else None
                        inherited["task_understanding"] = graph.task.model_dump(mode="json")
                        node.status = "running"  # set before submit so dependants cannot race
                        future = pool.submit(SubAgent(f"sub-agent-{node.id}", node.capability).perform,
                                             node, study=study, inherited_artifacts=inherited,
                                             task_budget=budget, timeout_s=remaining,
                                             remaining_cost=reservation if remaining_cost is not None else None)
                        futures[future] = node
                        in_flight.add(node.id)
                        _log.info("stage ready: running %s (%s)", node.id, node.capability)
                    pending.remove(node)
                    progressed = True

                if not futures:
                    if pending and not progressed:
                        raise RuntimeError("No runnable nodes remain in a validated DAG")
                    continue
                completed, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in sorted(completed, key=lambda f: graph.nodes.index(futures[f])):
                    node = futures[future]
                    del futures[future]
                    try:
                        future.result()
                    except Exception as exc:  # noqa: BLE001 - defensive: SubAgent
                        # contains its own errors, so reaching here means the
                        # worker itself broke.
                        node.status = "failed"
                        node.error = f"executor-level failure: {exc}"
                        _log.exception("node %s crashed the worker", node.id)
                    reservation = node.artifacts.get("_cost_reservation")
                    if reservation is not None:
                        consumed = node.artifacts.get("_declared_cost_consumed", reservation)
                        reserved_cost -= max(0, reservation - consumed)

                    if node.status == "failed":
                        _log.warning(
                            "node %s failed; dependent nodes will evaluate their partial-input policy",
                            node.id,
                        )

                    _log.info("stage %s complete: %s", node.id, node.status)
                    for added in replanner.after(node):
                        by_id[added.id] = added
                        pending.append(added)

        elapsed = time.perf_counter() - started
        done = sum(1 for n in graph.nodes if n.status == "done")
        failed = sum(1 for n in graph.nodes if n.status == "failed")
        skipped_count = sum(1 for n in graph.nodes if n.status == "skipped")
        _log.info(
            "graph finished in %.2fs: %d done, %d failed, %d skipped",
            elapsed,
            done,
            failed,
            skipped_count,
        )
        graph.acceptance_results = evaluate_acceptance(graph)
        return graph

    @staticmethod
    def _reserve_cost(node, attempts, remaining):
        if remaining is None:
            return 0.0
        remaining = max(0, remaining)
        from capabilities import registry
        from core.routing import TaskRequirements

        try:
            base = registry.requirements(node.capability).to_dict()
            task = TaskRequirements(**{**base, **(node.artifacts.get("requirements") or {}),
                                       "max_cost": max(0, remaining)})
            candidates, _ = registry.select(task, preferred=node.capability,
                                            excluded=set(node.artifacts.get("_excluded_capabilities", [])))
            prices = registry.metadata()
            # Ranking can change while other workers update history. Reserve
            # the worst eligible declared prices, capped by remaining budget;
            # the worker enforces the allocation sequentially between attempts.
            worst = sorted((prices[name]["cost_per_call"] for name in candidates), reverse=True)[:attempts]
            return min(remaining, sum(worst)) if candidates else None
        except (ValueError, TypeError, KeyError):
            return None

    def _assemble_input(
        self, node: Node, by_id: dict[str, Node], graph: Graph
    ) -> tuple[str, dict]:
        """Build a node's text input and inherited artifacts from its parents."""
        if not node.depends_on:
            return graph.job, {}

        parts: list[str] = []
        inherited: dict = {}

        for parent_id in node.depends_on:
            parent = by_id.get(parent_id)
            if parent is None or parent.status != "done" or not parent.output:
                continue

            parts.append(
                f"--- From node '{parent.id}' ({parent.description}) ---\n"
                f"{parent.output}"
            )

            # Merge parent artifacts. Lists concatenate, dicts merge, scalars
            # take the last writer -- which is deterministic because
            # ``depends_on`` order is fixed by the plan.
            for key, value in parent.artifacts.items():
                if key.startswith("_") or key in {"image_ids", "document_ids", "document_page_numbers", "requirements", "verification_requests"}:
                    continue
                if isinstance(value, list) and isinstance(inherited.get(key), list):
                    inherited[key] = inherited[key] + value
                elif isinstance(value, dict) and isinstance(inherited.get(key), dict):
                    inherited[key] = {**inherited[key], **value}
                else:
                    inherited[key] = value

        if not parts:
            return (
                f"No usable output was produced by upstream nodes "
                f"({', '.join(node.depends_on)}). Original job: {graph.job}"
            ), inherited

        text = "\n\n".join(parts)
        if len(text) > _MAX_INPUT_CHARS:
            text = (
                text[:_MAX_INPUT_CHARS]
                + f"\n\n[...truncated at {_MAX_INPUT_CHARS} characters...]"
            )
        return text, inherited
