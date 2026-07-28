from graph_utils import get_sink_nodes
from models import Graph


class IntegratorAgent:
    def integrate(self, graph: Graph) -> str:
        sinks = get_sink_nodes(graph)
        print(f"[integrator-agent] combining outputs from {len(sinks)} sink node(s)")

        if len(sinks) == 1:
            result = sinks[0].output
        else:
            parts = []
            for node in sinks:
                parts.append(
                    f"From node '{node.id}' ({node.description}):\n"
                    f"{node.output}"
                )
            result = "\n\n".join(parts)

        print("[integrator-agent] done")
        return result
