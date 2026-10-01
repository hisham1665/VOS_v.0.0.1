"""Planning agent: LLM decomposition for open-ended jobs, plus plan rendering.

For a study read, :mod:`agents.planner` builds the DAG in code -- faster and
auditable. This agent handles the other case: a free-form job whose shape is
genuinely unknown ("compare these two imaging guidelines and the study").

Three things changed from the original implementation:

* The planner may only emit **registered** capability names, checked against
  the registry rather than a hard-coded list that drifted out of sync.
* Repair is incremental. A rejected graph is sent back with the specific error
  and *one* retry, instead of regenerating from scratch in two nested loops.
* The completeness audit is **off by default** (``MAOS_COMPLETENESS_CHECK=1``
  re-enables it). It cost a full extra LLM round trip on every run.

It no longer rewrites ``README.md``: generated content overwriting hand-written
documentation was a data-loss bug, not a feature.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from capabilities import registry
from capabilities.hf_backend import chat_text, groq_client
from config import MODELS, RUNTIME
from core.errors import PlanningError
from core.logging_utils import get_logger
from core.retry import with_retry
from diagram_utils import build_mermaid
from graph_utils import GraphValidationError, build_waves, validate_graph
from models import Graph, Study

_log = get_logger("manager")


def _system_prompt() -> str:
    """Built at call time so it always reflects the live capability registry."""
    capability_lines = "\n".join(
        f"- {name}: {description}" for name, description in sorted(registry.describe().items())
    )
    return f"""\
You are a task planner for a medical imaging analysis system. Decompose the \
user's job into a directed acyclic graph (DAG) of tasks.

Available capabilities -- you may use ONLY these names:
{capability_lines}

Each node must have:
- id: a short identifier, e.g. "read1", "merge"
- description: a specific, actionable instruction. This text is passed
  verbatim to the capability as its instruction, so it must stand alone.
- capability: EXACTLY one name from the list above.
- depends_on: list of node ids it waits for. [] means it is a root node.

PARALLELISM: nodes that do not depend on each other run concurrently. Split
independent work into separate nodes so it runs in parallel.

MERGING: a node may depend on several parents and receives all their outputs.

IMAGE WORK: capabilities {sorted(registry.IMAGE_CAPABILITIES)} operate on the
study's images. Give each image its own node when the images should be read
independently; the system assigns images to those nodes.

ORDERING RULE: a report_synthesis node must be the single final node, and must
depend on whatever produced the findings.

Call the create_graph function. Do not reply with plain text."""


_CREATE_GRAPH_TOOL = {
    "type": "function",
    "function": {
        "name": "create_graph",
        "description": "Submit the decomposed task graph.",
        "parameters": {
            "type": "object",
            "properties": {
                "job": {"type": "string", "description": "The original user job."},
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "description": {"type": "string"},
                            "capability": {"type": "string"},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["id", "description", "capability", "depends_on"],
                    },
                },
            },
            "required": ["job", "nodes"],
        },
    },
}


class ManagerAgent:
    """Builds a task graph from a free-form job, and renders plans to disk."""

    MAX_REPAIRS = 1

    def create_plan(self, user_prompt: str, study: Optional[Study] = None) -> Graph:
        _log.info("decomposing job into a task graph")

        messages = self._build_messages(user_prompt, study)
        graph = self._call_planner(messages)

        # --- repair loop: structure first, then capability names ----------
        for attempt in range(self.MAX_REPAIRS + 1):
            problems = self._problems(graph)
            if not problems:
                break
            if attempt == self.MAX_REPAIRS:
                raise PlanningError(
                    "Planner could not produce a valid graph after "
                    f"{self.MAX_REPAIRS} repair attempt(s): {problems}"
                )
            _log.warning("plan rejected (%s); requesting a repair", problems)
            repair = list(messages) + [
                {
                    "role": "assistant",
                    "content": f"Proposed graph: {graph.model_dump_json()}",
                },
                {
                    "role": "user",
                    "content": (
                        f"That graph is invalid: {problems}\n\n"
                        "Fix exactly these problems, keep everything else, and "
                        "call create_graph again."
                    ),
                },
            ]
            graph = self._call_planner(repair)

        if RUNTIME.planner_completeness_check:
            verdict = self._check_completeness(user_prompt, graph)
            if verdict != "COMPLETE":
                _log.warning("completeness audit: %s -- continuing anyway", verdict)

        waves = build_waves(graph)
        _log.info("plan ready: %d node(s), %d wave(s)", len(graph.nodes), len(waves))
        return graph

    # ------------------------------------------------------------------
    def _problems(self, graph: Graph) -> str:
        """Return a human-readable problem string, or "" when the graph is fine."""
        issues: list[str] = []
        try:
            validate_graph(graph)
        except GraphValidationError as exc:
            issues.append(str(exc))

        unknown = sorted(
            {n.capability for n in graph.nodes if not registry.is_registered(n.capability)}
        )
        if unknown:
            issues.append(
                f"unknown capabilities {unknown}; valid names are {registry.names()}"
            )
        return "; ".join(issues)

    def _call_planner(self, messages: list) -> Graph:
        def _call() -> dict:
            response = groq_client().chat.completions.create(
                model=MODELS.text_llm,
                messages=messages,
                tools=[_CREATE_GRAPH_TOOL],
                tool_choice={"type": "function", "function": {"name": "create_graph"}},
                temperature=0.2,
            )
            message = response.choices[0].message
            if not message.tool_calls:
                raise PlanningError(
                    "planner did not call create_graph; "
                    f"it replied: {(message.content or '')[:300]}"
                )
            return json.loads(message.tool_calls[0].function.arguments)

        payload = with_retry(
            _call,
            attempts=RUNTIME.max_retries,
            base_delay=RUNTIME.retry_base_delay,
            label="planner",
        )
        return Graph.model_validate(payload)

    def _build_messages(self, user_prompt: str, study: Optional[Study]) -> list:
        text = user_prompt
        if study is not None:
            summary = "\n".join(
                f"- {img.image_id}: {img.modality.value}"
                f"{', ' + img.body_part if img.body_part else ''}"
                for img in study.images[:20]
            )
            text = (
                f"{user_prompt}\n\n"
                f"The study contains {len(study.images)} image(s):\n{summary}"
            )
        return [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": text},
        ]

    def _check_completeness(self, user_prompt: str, graph: Graph) -> str:
        """Optional audit: does every entity named in the job have a node?"""
        node_list = "\n".join(
            f"  - {n.id}: {n.description!r} [{n.capability}] <- {n.depends_on}"
            for n in graph.nodes
        )
        try:
            verdict, _ = chat_text(
                f"JOB: {user_prompt}\n\nGRAPH NODES:\n{node_list}",
                system=(
                    "You audit task graphs for coverage. Answer with exactly "
                    "'COMPLETE' or 'INCOMPLETE: <short reason>'. A node whose "
                    "description mentions an entity covers that entity. Only say "
                    "INCOMPLETE when something named in the job has no node at all."
                ),
                max_tokens=120,
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001 - the audit is advisory
            _log.warning("completeness audit unavailable: %s", exc)
            return "COMPLETE"
        return "COMPLETE" if verdict.strip().upper().startswith("COMPLETE") else verdict

    # ------------------------------------------------------------------
    @staticmethod
    def write_plan(graph: Graph, path: Path | str) -> Path:
        """Render the graph to markdown with a deterministic Mermaid diagram."""
        waves = build_waves(graph)

        lines = [
            "# Task Plan",
            "",
            f"**Job:** {graph.job}",
            "",
            f"**Shape:** {len(graph.nodes)} nodes across {len(waves)} waves",
            "",
            "| Node | Description | Capability | Depends on | Status | Time |",
            "|---|---|---|---|---|---|",
        ]
        for node in graph.nodes:
            deps = ", ".join(f"`{d}`" for d in node.depends_on) if node.depends_on else "-"
            duration = f"{node.duration_s:.2f}s" if node.duration_s else "-"
            lines.append(
                f"| `{node.id}` | {node.description} | {node.capability} "
                f"| {deps} | {node.status} | {duration} |"
            )

        lines += ["", "## Execution waves", ""]
        for index, wave in enumerate(waves):
            ids = ", ".join(f"`{n.id}`" for n in wave)
            lines.append(f"- **Wave {index}** ({len(wave)} in parallel): {ids}")

        failures = [n for n in graph.nodes if n.status == "failed"]
        if failures:
            lines += ["", "## Failures", ""]
            lines.extend(f"- `{n.id}` ({n.capability}): {n.error}" for n in failures)

        lines += ["", "## Graph", "", "```mermaid", build_mermaid(graph, waves), "```", ""]

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines), encoding="utf-8")
        return target
