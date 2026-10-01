"""Wave-parallel execution of the task graph.

Changes from the original executor, each fixing a concrete failure:

* **One bounded pool for the whole run** instead of ``max_workers=len(wave)``
  per wave. A wide study used to spawn one thread per image and, on an empty
  wave, ``ThreadPoolExecutor(max_workers=0)`` raised outright.
* **Failures are contained.** A failed node blocks strict descendants, while
  explicitly best-effort joins continue with successful branches.
* **Artifacts flow down the edges.** Parents hand structured data to children,
  not just prose -- that is how the classifier's scores reach the VLM prompt.
* **No shared mutable state written from worker threads.** Results are merged
  on the orchestrating thread after each wave, which removes the race in the
  old ``vision_outputs`` dictionary.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import RUNTIME
from core.logging_utils import get_logger
from agents.sub_agent import SubAgent
from graph_utils import build_waves
from models import Graph, Node, Study

_log = get_logger("executor")

# Text handed to any single node is capped so one verbose upstream node cannot
# blow the context window of everything downstream.
_MAX_INPUT_CHARS = 30000


class GraphExecutor:
    """Runs every node of a graph in dependency order, wave by wave."""

    def __init__(self, max_workers: int | None = None):
        self.max_workers = max(1, max_workers or RUNTIME.max_workers)

    def run(self, graph: Graph, study: Study | None = None) -> Graph:
        waves = build_waves(graph)
        by_id = graph.by_id()
        started = time.perf_counter()

        with ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="maos-node"
        ) as pool:
            for wave_index, wave in enumerate(waves):
                runnable: list[Node] = []
                for node in wave:
                    parents = [by_id.get(dep) for dep in node.depends_on]
                    failed = [
                        parent for parent in parents
                        if parent is not None and parent.status in {"failed", "skipped"}
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
                        runnable.append(node)

                if not runnable:
                    _log.warning("wave %d entirely skipped", wave_index)
                    continue

                _log.info(
                    "wave %d: running %d node(s) [%s]",
                    wave_index,
                    len(runnable),
                    ", ".join(n.id for n in runnable),
                )

                # Inputs are assembled here, on one thread, before any node runs.
                for node in runnable:
                    node.input, inherited = self._assemble_input(node, by_id, graph)
                    node.artifacts.setdefault("_inherited", {}).update(inherited)

                wave_started = time.perf_counter()
                futures = {
                    pool.submit(
                        SubAgent(f"sub-agent-{node.id}", node.capability).perform,
                        node,
                        study=study,
                        inherited_artifacts=node.artifacts.pop("_inherited", {}),
                    ): node
                    for node in runnable
                }

                for future in as_completed(futures):
                    node = futures[future]
                    try:
                        future.result()
                    except Exception as exc:  # noqa: BLE001 - defensive: SubAgent
                        # contains its own errors, so reaching here means the
                        # worker itself broke.
                        node.status = "failed"
                        node.error = f"executor-level failure: {exc}"
                        _log.exception("node %s crashed the worker", node.id)

                    if node.status == "failed":
                        _log.warning(
                            "node %s failed; dependent nodes will evaluate their partial-input policy",
                            node.id,
                        )

                _log.info(
                    "wave %d complete in %.2fs",
                    wave_index,
                    time.perf_counter() - wave_started,
                )

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
        return graph

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
                if key.startswith("_") or key in {"image_ids", "document_ids", "document_page_numbers"}:
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
