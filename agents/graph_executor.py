import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from aos_v0.graph_utils import build_waves
from aos_v0.models import Graph
from aos_v0.agents.sub_agent import SubAgent

_print_lock = threading.Lock()


def _safe_print(msg: str) -> None:
    with _print_lock:
        print(msg)


class GraphExecutor:
    def run(self, graph: Graph, image_path: Optional[str] = None) -> Graph:
        waves = build_waves(graph)
        by_id = {n.id: n for n in graph.nodes}

        vision_outputs: dict[str, str] = {}

        for wave_idx, wave in enumerate(waves):
            _safe_print(
                f"[graph-executor] wave {wave_idx} starting "
                f"({len(wave)} nodes, running concurrently)"
            )

            def _run_node(node):
                if not node.depends_on:
                    if node.capability == "vision":
                        if image_path:
                            node.input = image_path
                        else:
                            node.input = graph.job
                    else:
                        node.input = graph.job
                else:
                    parts = []
                    for parent_id in node.depends_on:
                        parent = by_id[parent_id]
                        parts.append(
                            f"From node '{parent.id}' ({parent.description}):\n"
                            f"{parent.output}"
                        )
                    node.input = "\n\n".join(parts)

                if node.capability != "vision" and vision_outputs:
                    vision_ctx = "\n\n".join(
                        f"[IMAGE IDENTIFICATION]: {vout}"
                        for vout in vision_outputs.values()
                    )
                    node.input = (
                        f"IMPORTANT CONTEXT — an image was analyzed and the following "
                        f"was identified:\n{vision_ctx}\n\n---\n\n{node.input}"
                    )

                agent = SubAgent(name=f"sub-agent-{node.id}", capability=node.capability)
                agent.perform(node)

                if node.capability == "vision":
                    vision_outputs[node.id] = node.output

                _safe_print(
                    f"[graph-executor] node '{node.id}' complete "
                    f"({len(node.output)} chars)"
                )
                return node

            with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                futures = {pool.submit(_run_node, node): node for node in wave}
                for future in as_completed(futures):
                    future.result()

            _safe_print(f"[graph-executor] wave {wave_idx} complete")

        return graph
