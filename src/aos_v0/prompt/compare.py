def _check_satisfiable(graph, registry) -> None:
    """Fail fast, naming the missing capability, before any node executes."""
    problems = []
    for node in graph.nodes:
        if not node.dna:
            continue
        missing = registry.unsatisfiable_flags(node.dna)
        if missing:
            problems.append(f"node '{node.id}' requires {missing}")
    if problems:
        raise RuntimeError(
            "[admission-control] plan rejected — no registered resource provides: "
            + "; ".join(problems)
        )
    print("[admission-control] all DNA flags satisfiable by registered resources")