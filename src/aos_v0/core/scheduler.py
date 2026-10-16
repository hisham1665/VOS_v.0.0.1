"""Domain-neutral dependency-ready scheduler shared by AOS and exam workflows."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from time import monotonic

from aos_v0.core.graph_utils import validate_graph


def run_dag(graph, execute, *, max_workers=4, max_elapsed_s=600, on_error=None):
    """Execute ready nodes as soon as their parents finish, with bounded work.

    Provider calls enforce their own timeout. The workflow deadline stops new
    dispatches; Python threads cannot forcibly interrupt an in-flight adapter.
    Domain callbacks decide whether a failed dependency permits partial work.
    """
    validate_graph(graph)
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    pending = list(graph.nodes)
    completed = set()
    started = monotonic()
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aos") as pool:
        active = {}
        while pending or active:
            if monotonic() - started > max_elapsed_s:
                raise TimeoutError("AOS workflow deadline exceeded")
            ready = [node for node in pending if set(node.depends_on) <= completed]
            for node in ready[:max(0, max_workers - len(active))]:
                pending.remove(node)
                active[pool.submit(execute, node)] = node
            if not active:
                raise RuntimeError("DAG scheduler made no progress")
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                node = active.pop(future)
                try:
                    future.result()
                except Exception as exc:
                    if on_error is None:
                        raise
                    on_error(node, exc)
                completed.add(node.id)
    return graph
