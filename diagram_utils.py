"""Deterministic Mermaid rendering of a task graph.

Diagrams are built from the validated graph, never by an LLM, so the picture
always matches what actually ran.
"""

from __future__ import annotations

from models import Graph, Node

# Colour per capability so the wave diagram reads at a glance.
CAPABILITY_COLORS = {
    "study_ingest": "#795548",
    "modality_triage": "#9C27B0",
    "image_classification": "#E91E63",
    "vision": "#FF9800",
    "cross_image_synthesis": "#00ACC1",
    "web_search": "#4CAF50",
    "summarization": "#2196F3",
    "report_synthesis": "#F44336",
}

_STATUS_STROKE = {
    "failed": "stroke:#B71C1C,stroke-width:3px,stroke-dasharray:4 2",
    "skipped": "stroke:#9E9E9E,stroke-width:2px,stroke-dasharray:2 2",
}


def _sanitize(text: str, max_len: int = 46) -> str:
    """Strip characters that break Mermaid node labels and truncate."""
    for bad, good in (
        ('"', "'"),
        ("[", "("),
        ("]", ")"),
        ("|", "-"),
        ("{", "("),
        ("}", ")"),
        ("\n", " "),
        (";", ","),
    ):
        text = text.replace(bad, good)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text


def _safe_id(node_id: str) -> str:
    """Mermaid ids must be alphanumeric/underscore."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in node_id) or "n"


def build_mermaid(graph: Graph, waves: list[list[Node]]) -> str:
    """Render the graph as a Mermaid flowchart grouped by execution wave."""
    lines: list[str] = ["flowchart TD"]

    for wave_idx, wave in enumerate(waves):
        lines.append(f'    subgraph W{wave_idx}["Wave {wave_idx}"]')
        for node in wave:
            label = _sanitize(node.description)
            lines.append(f'        {_safe_id(node.id)}["{label}<br/>({node.capability})"]')
        lines.append("    end")

    id_set = {n.id for n in graph.nodes}
    for node in graph.nodes:
        for dep in node.depends_on:
            if dep in id_set:
                lines.append(f"    {_safe_id(dep)} --> {_safe_id(node.id)}")

    lines.append("")
    used = {n.capability for n in graph.nodes if n.capability in CAPABILITY_COLORS}
    for cap in sorted(used):
        lines.append(
            f"    classDef {cap}Style fill:{CAPABILITY_COLORS[cap]},color:#fff"
        )
    for node in graph.nodes:
        if node.capability in used:
            lines.append(f"    class {_safe_id(node.id)} {node.capability}Style")

    # Outline nodes that did not complete, so a failed run is obvious.
    for node in graph.nodes:
        stroke = _STATUS_STROKE.get(node.status)
        if stroke:
            lines.append(f"    style {_safe_id(node.id)} {stroke}")

    return "\n".join(lines)
