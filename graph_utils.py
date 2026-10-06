"""Task-graph validation and wave scheduling."""

from __future__ import annotations

from collections import defaultdict, deque

from core.errors import GraphValidationError
from models import Graph, Node

__all__ = ["GraphValidationError", "validate_graph", "build_waves", "get_sink_nodes"]

# A runaway planner can emit an enormous graph; cap it so one bad plan cannot
# fan out into hundreds of paid model calls.
MAX_NODES = 64


def validate_graph(graph: Graph) -> None:
    """Raise :class:`GraphValidationError` unless ``graph`` is a usable DAG."""
    if not graph.nodes:
        raise GraphValidationError("Graph has no nodes")
    if len(graph.nodes) > MAX_NODES:
        raise GraphValidationError(
            f"Graph has {len(graph.nodes)} nodes, exceeding the limit of {MAX_NODES}"
        )

    ids = [n.id for n in graph.nodes]
    seen: set[str] = set()
    dupes = sorted({i for i in ids if i in seen or seen.add(i)})  # type: ignore[func-returns-value]
    if dupes:
        raise GraphValidationError(f"Duplicate node ids: {dupes}")

    id_set = set(ids)
    for node in graph.nodes:
        if not node.id.strip():
            raise GraphValidationError("A node has an empty id")
        if node.id in node.depends_on:
            raise GraphValidationError(f"Node '{node.id}' depends on itself")
        for dep in node.depends_on:
            if dep not in id_set:
                raise GraphValidationError(
                    f"Node '{node.id}' depends on '{dep}' which does not exist"
                )
        for dep in node.optional_dependencies + node.partial_requires:
            if dep not in node.depends_on:
                raise GraphValidationError(f"Node '{node.id}' has a dependency policy for non-parent '{dep}'")

    if not any(not n.depends_on for n in graph.nodes):
        raise GraphValidationError("Graph has no root nodes (no starting point)")

    # Kahn's algorithm: if we cannot visit every node, a cycle exists.
    adj: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {n.id: 0 for n in graph.nodes}
    for node in graph.nodes:
        for dep in node.depends_on:
            adj[dep].append(node.id)
            in_degree[node.id] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    visited = 0
    while queue:
        nid = queue.popleft()
        visited += 1
        for child in adj[nid]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if visited != len(ids):
        stuck = sorted(nid for nid, deg in in_degree.items() if deg > 0)
        raise GraphValidationError(f"Graph contains a cycle involving: {stuck}")


def build_waves(graph: Graph) -> list[list[Node]]:
    """Group nodes into dependency waves; every node in a wave can run in parallel."""
    validate_graph(graph)

    by_id = graph.by_id()
    adj: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {n.id: 0 for n in graph.nodes}
    for node in graph.nodes:
        for dep in node.depends_on:
            adj[dep].append(node.id)
            in_degree[node.id] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    waves: list[list[Node]] = []
    while queue:
        wave_ids: list[str] = []
        next_queue: deque[str] = deque()
        while queue:
            nid = queue.popleft()
            wave_ids.append(nid)
            for child in adj[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_queue.append(child)
        waves.append([by_id[i] for i in wave_ids])
        queue = next_queue
    return waves


def get_sink_nodes(graph: Graph) -> list[Node]:
    """Nodes nothing depends on -- the graph's terminal outputs."""
    referenced: set[str] = set()
    for node in graph.nodes:
        referenced.update(node.depends_on)
    return [n for n in graph.nodes if n.id not in referenced]


def descendants_of(graph: Graph, node_id: str) -> set[str]:
    """Every node transitively downstream of ``node_id``.

    Used to skip work that can no longer succeed once a node fails.
    """
    adj: dict[str, list[str]] = defaultdict(list)
    for node in graph.nodes:
        for dep in node.depends_on:
            adj[dep].append(node.id)

    out: set[str] = set()
    stack = list(adj[node_id])
    while stack:
        current = stack.pop()
        if current in out:
            continue
        out.add(current)
        stack.extend(adj[current])
    return out
