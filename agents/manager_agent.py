import json
from pathlib import Path
from typing import Optional

from groq import Groq

from aos_v0.config import GROQ_API_KEY
from aos_v0.graph_utils import validate_graph, build_waves, GraphValidationError
from aos_v0.diagram_utils import build_mermaid
from aos_v0.models import Graph

_CAPABILITIES = ["web_search", "summarization", "vision"]

_MODEL = "llama-3.3-70b-versatile"

_SYSTEM_PROMPT = f"""\
You are a task planner. Given a user job, decompose it into a directed acyclic \
graph (DAG) of tasks.

Each node must have:
- id: a short lowercase letter or string identifier (e.g. "a", "b", "search1")
- description: a SPECIFIC, ACTIONABLE instruction for the node's capability. \
Not vague — this text IS the instruction passed to the capability. E.g. \
"Search for recent advances in solar panel efficiency" not "do research".
- capability: EXACTLY ONE from this fixed list: {_CAPABILITIES}
- depends_on: a list of node ids this node depends on. Empty list [] means \
root node (no dependencies).

PARALLELISM: When two or more nodes do not depend on each other, they can run \
in parallel. You SHOULD use multiple parallel branches when the task naturally \
splits (e.g. researching two different topics at the same time).

MERGING: A node can depend on MULTIPLE parents. Its input will be the \
concatenated outputs of all parent nodes. Use this for nodes that need to \
combine or compare results from parallel branches.

CRITICAL — ONE NODE PER NAMED ENTITY: When the job names multiple distinct \
entities that each need individual research, analysis, or comparison (e.g. a \
list of teams, products, companies, people, places), create ONE web_search node \
AND ONE summarization node PER NAMED ENTITY — never combine multiple named \
entities into a single node. Only merge individual entity results together at a \
later, separate comparison/aggregation node that depends on all of them.

Example: if the job is "analyze Brazil, Portugal, and Spain", produce:
  - 3 web_search nodes (one for Brazil, one for Portugal, one for Spain)
  - 3 summarization nodes (one per team, each depending on its own web_search)
  - optionally 1 comparison node depending on all 3 summarization nodes
NOT a single node covering all three teams.

If an image_path is provided in the job, include a root "vision" node that \
describes the image, feeding into downstream nodes. If no image is mentioned, \
do NOT include a vision node.

The id for each node must be a simple string like "a", "b", "c", etc.

You MUST call the create_graph function with the decomposed graph. Do not \
respond with plain text."""

_CREATE_GRAPH_TOOL = {
    "type": "function",
    "function": {
        "name": "create_graph",
        "description": "Submit the decomposed task graph.",
        "parameters": {
            "type": "object",
            "properties": {
                "job": {
                    "type": "string",
                    "description": "The original user job.",
                },
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "description": {"type": "string"},
                            "capability": {"type": "string"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["id", "description", "capability", "depends_on"],
                    },
                },
            },
            "required": ["job", "nodes"],
        },
    },
}

_README_SECTION = """\
## How AOS v0.0.2 plans and executes tasks

AOS decomposes a user job into a **task graph** — a directed acyclic graph
(DAG) of **nodes** connected by **edges**.

- **Node:** a single unit of work. Each node has a unique `id`, a `description`
  (which doubles as the instruction passed to its capability), a `capability`
  (the tool it calls, e.g. `web_search`, `summarization`, or `vision`), and a
  `depends_on` list of node ids it must wait for.
- **Edge:** a dependency from one node to another. If node C has
  `depends_on: ["a", "b"]`, it runs only after both A and B finish, and
  receives their outputs as input.

**Concurrency:** nodes in the same dependency "wave" are independent and run
concurrently via threads. For example, in the graph below, nodes `a` and `b`
run in parallel, then `c` and `d` run in parallel, and finally `e` runs.

**Multi-parent merging:** a node with multiple parents receives the labeled,
concatenated outputs of all its parents, so it can compare, combine, or
reconcile them.

**Diagrams:** the Mermaid diagrams in `outputs/plan.md` are generated
deterministically from the validated graph by `diagram_utils.build_mermaid()`,
not by the LLM. This is a deliberate reliability choice — the diagram always
faithfully represents the actual graph structure, wave assignments, and
capability types, with no risk of the LLM inventing or omitting nodes.

```mermaid
flowchart TD
    subgraph Wave 0
        a["Research solar energy (web_search)"]
        b["Research wind energy (web_search)"]
    end
    subgraph Wave 1
        c["Summarize solar (summarization)"]
        d["Summarize wind (summarization)"]
    end
    subgraph Wave 2
        e["Compare summaries (summarization)"]
    end
    a --> c
    b --> d
    c --> e
    d --> e

    classDef web_searchStyle fill:#4CAF50,color:#fff
    classDef summarizationStyle fill:#2196F3,color:#fff
    class a web_searchStyle
    class b web_searchStyle
    class c summarizationStyle
    class d summarizationStyle
    class e summarizationStyle
```
"""


class ManagerAgent:
    _MAX_STRUCTURAL_RETRIES = 1
    _MAX_COMPLETENESS_RETRIES = 1

    def __init__(self):
        self._client = Groq(api_key=GROQ_API_KEY)

    def create_plan(self, user_prompt: str, image_path: Optional[str] = None) -> Graph:
        print("[manager-agent] decomposing job into a task graph...")

        graph = self._call_llm(user_prompt, image_path)

        # --- structural validation loop ---
        structural_attempts = 0
        while True:
            try:
                validate_graph(graph)
                break
            except GraphValidationError as exc:
                structural_attempts += 1
                if structural_attempts > self._MAX_STRUCTURAL_RETRIES:
                    raise RuntimeError(
                        f"Graph still invalid after {self._MAX_STRUCTURAL_RETRIES} "
                        f"structural retry: {exc}"
                    ) from exc
                print(f"[manager-agent] structural validation failed, retrying ({structural_attempts}/{self._MAX_STRUCTURAL_RETRIES}): {exc}")
                graph = self._call_llm_with_retry(user_prompt, image_path, str(exc))

        # --- semantic completeness check ---
        completeness_attempts = 0
        while True:
            verdict = self._check_completeness(user_prompt, graph)
            if verdict == "COMPLETE":
                print("[manager-agent] completeness check: COMPLETE")
                break
            completeness_attempts += 1
            if completeness_attempts > self._MAX_COMPLETENESS_RETRIES:
                raise RuntimeError(
                    f"Graph still incomplete after {self._MAX_COMPLETENESS_RETRIES} "
                    f"completeness retry: {verdict}"
                )
            print(f"[manager-agent] completeness check: INCOMPLETE ({verdict}) — regenerating")
            graph = self._call_llm_with_completeness_retry(
                user_prompt, image_path, verdict
            )
            # re-validate structurally after completeness regeneration
            try:
                validate_graph(graph)
            except GraphValidationError as exc:
                print(f"[manager-agent] structural validation failed after completeness retry: {exc}")
                graph = self._call_llm_with_retry(user_prompt, image_path, str(exc))
                validate_graph(graph)

        self._write_plan_md(graph)
        self._ensure_readme()

        waves = build_waves(graph)
        print(
            "[manager-agent] graph written to outputs/plan.md "
            f"({len(graph.nodes)} nodes, {len(waves)} waves)"
        )
        return graph

    def _call_llm(self, user_prompt: str, image_path: Optional[str] = None) -> Graph:
        messages = self._build_messages(user_prompt, image_path)
        response = self._client.chat.completions.create(
            model=_MODEL,
            messages=messages,
            tools=[_CREATE_GRAPH_TOOL],
            tool_choice={"type": "function", "function": {"name": "create_graph"}},
            temperature=0.2,
        )
        return Graph.model_validate(self._extract_fn_call(response))

    def _call_llm_with_retry(
        self, user_prompt: str, image_path: Optional[str], error_msg: str
    ) -> Graph:
        messages = self._build_messages(user_prompt, image_path)
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your previous graph failed validation:\n"
                    f"{error_msg}\n\n"
                    "Please fix the errors and call create_graph again."
                ),
            }
        )
        response = self._client.chat.completions.create(
            model=_MODEL,
            messages=messages,
            tools=[_CREATE_GRAPH_TOOL],
            tool_choice={"type": "function", "function": {"name": "create_graph"}},
            temperature=0.2,
        )
        return Graph.model_validate(self._extract_fn_call(response))

    def _check_completeness(self, user_prompt: str, graph: Graph) -> str:
        node_list = "\n".join(
            f"  - id={n.id}, description={n.description!r}, "
            f"capability={n.capability}, depends_on={n.depends_on}"
            for n in graph.nodes
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a completeness auditor. Given a user job and a "
                    "proposed task graph, check whether every distinct entity "
                    "or subtask explicitly named in the job is COVERED by at "
                    "least one node in the graph.\n\n"
                    "COVERAGE RULES (apply these strictly):\n"
                    "- A vision node for an image = the image is covered.\n"
                    "- A web_search node mentioning an entity = that entity "
                    "is researched/analyzed.\n"
                    "- A summarization node for an entity = that entity is "
                    "summarized/analyzed.\n"
                    "- Any node whose description mentions an entity covers "
                    "that entity, regardless of capability type.\n"
                    "- A chain like vision -> web_search -> summarization for "
                    "an entity = fully covered.\n\n"
                    "Only respond INCOMPLETE if an entity or subtask named in "
                    "the job has ZERO nodes mentioning it. Do NOT require "
                    "extra 'analysis', 'deep dive', or 'dedicated analysis' "
                    "nodes beyond what already exists.\n\n"
                    "Respond with EXACTLY one of:\n"
                    "- COMPLETE\n"
                    "- INCOMPLETE: <short reason>\n\n"
                    "One line only."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"JOB: {user_prompt}\n\n"
                    f"GRAPH NODES:\n{node_list}"
                ),
            },
        ]
        response = self._client.chat.completions.create(
            model=_MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=200,
        )
        text = (response.choices[0].message.content or "").strip()
        if text.upper().startswith("COMPLETE"):
            return "COMPLETE"
        return text

    def _call_llm_with_completeness_retry(
        self, user_prompt: str, image_path: Optional[str], reason: str
    ) -> Graph:
        messages = self._build_messages(user_prompt, image_path)
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your previous graph was flagged as incomplete:\n"
                    f"{reason}\n\n"
                    "Please regenerate the graph ensuring every distinct entity "
                    "or subtask named in the job has its own dedicated node(s). "
                    "Do NOT combine multiple named entities into a single node. "
                    "Call create_graph with the corrected graph."
                ),
            }
        )
        response = self._client.chat.completions.create(
            model=_MODEL,
            messages=messages,
            tools=[_CREATE_GRAPH_TOOL],
            tool_choice={"type": "function", "function": {"name": "create_graph"}},
            temperature=0.2,
        )
        return Graph.model_validate(self._extract_fn_call(response))

    @staticmethod
    def _build_messages(
        user_prompt: str, image_path: Optional[str] = None
    ) -> list:
        messages: list = [
            {"role": "system", "content": _SYSTEM_PROMPT},
        ]

        text = user_prompt
        if image_path:
            from pathlib import Path as _P

            if not _P(image_path).exists():
                raise FileNotFoundError(f"Image not found: {image_path}")
            text = f"{user_prompt}\n\n[User has provided an image at: {image_path}]"

        messages.append({"role": "user", "content": text})

        return messages

    @staticmethod
    def _extract_fn_call(response) -> dict:
        message = response.choices[0].message
        if message.tool_calls:
            tool_call = message.tool_calls[0]
            return json.loads(tool_call.function.arguments)
        raise RuntimeError(
            "LLM did not return a create_graph function call. "
            f"Content: {message.content}"
        )

    @staticmethod
    def _write_plan_md(graph: Graph) -> None:
        waves = build_waves(graph)

        # --- node table ---
        lines = [
            "# Task Plan",
            f"**Job:** {graph.job}",
            "",
            "| # | Node ID | Description | Capability | Depends On |",
            "|---|---------|-------------|------------|------------|",
        ]
        for node in graph.nodes:
            deps = ", ".join(node.depends_on) if node.depends_on else "-"
            lines.append(
                f"| {node.id} | {node.id} | {node.description} "
                f"| {node.capability} | {deps} |"
            )

        # --- wave breakdown ---
        wave_parts = []
        for wave_idx, wave in enumerate(waves):
            ids = ", ".join(n.id for n in wave)
            wave_parts.append(f"Wave {wave_idx}: {ids}")
        lines += [
            "",
            "**Waves:** " + " | ".join(wave_parts),
            "",
        ]

        # --- mermaid diagram (deterministic, not LLM-generated) ---
        mermaid = build_mermaid(graph, waves)
        lines += [
            "```mermaid",
            mermaid,
            "```",
        ]

        path = Path(__file__).resolve().parent.parent / "outputs" / "plan.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _ensure_readme() -> None:
        path = Path(__file__).resolve().parent.parent / "README.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        marker = "## How AOS v0.0.2 plans and executes tasks"
        if marker in existing:
            start = existing.index(marker)
            content = existing[:start].rstrip("\n") + "\n\n" + _README_SECTION
        elif existing:
            content = existing.rstrip("\n") + "\n\n" + _README_SECTION
        else:
            content = _README_SECTION
        path.write_text(content, encoding="utf-8")
