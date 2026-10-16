"""Deterministic summary keys, derived from task metadata rather than answers."""


def build_summary_key(node_id: str, capability: str, description: str, value: str = "",
                      max_chars: int = 200) -> str:
    return f"{capability}: {' '.join(description.split())}"[:max_chars]
