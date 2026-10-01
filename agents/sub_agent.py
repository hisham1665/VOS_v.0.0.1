"""The worker that executes one node.

Everything that can go wrong at the node level is contained here: an unknown
capability, a capability that raises, a capability that hangs, a capability
that returns nothing. The original version let any of those kill the whole run
-- for a study read that means losing every other image's work too.

The contract is: :meth:`SubAgent.perform` always returns, always leaves the
node in a terminal state (``done`` or ``failed``), and always records how long
it took.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from config import RUNTIME
from core.errors import CapabilityError, UnknownCapabilityError
from core.logging_utils import get_logger
from capabilities import registry
from capabilities.registry import CapabilityRequest, CapabilityResult
from models import Node, Study

_log = get_logger("sub-agent")

# A dedicated single-thread pool per timed call. Python cannot interrupt a
# blocking C call, so the timeout abandons the call rather than killing it --
# but that is enough to stop one stuck node from holding the whole wave.
_TIMEOUT_POOL = ThreadPoolExecutor(
    max_workers=max(4, RUNTIME.max_workers), thread_name_prefix="maos-timeout"
)


class SubAgent:
    """Runs a single node's capability and writes the result back onto it."""

    def __init__(self, name: str, capability: str):
        self.name = name
        self.capability = capability

    def perform(
        self,
        node: Node,
        *,
        study: Study | None = None,
        inherited_artifacts: dict | None = None,
    ) -> Node:
        node.status = "running"
        node.performed_by = self.name
        started = time.perf_counter()

        _log.info("%s -> %s: %s", self.name, node.capability, node.description)

        try:
            fn = registry.get(node.capability)
        except UnknownCapabilityError as exc:
            return self._fail(node, started, str(exc))

        request = CapabilityRequest(
            instruction=node.description,
            text=node.input or "",
            study=study,
            # The planner stashes the node's image assignment in artifacts.
            image_ids=list(node.artifacts.get("image_ids", []) or []),
            document_ids=list(node.artifacts.get("document_ids", []) or []),
            document_page_numbers=[
                int(p) for p in (node.artifacts.get("document_page_numbers", []) or [])
            ],
            node_id=node.id,
            artifacts=dict(inherited_artifacts or {}),
        )

        try:
            result = self._call_with_timeout(fn, request)
        except FutureTimeout:
            return self._fail(
                node,
                started,
                f"capability '{node.capability}' exceeded "
                f"{RUNTIME.call_timeout:.0f}s (raise MAOS_CALL_TIMEOUT to allow longer)",
            )
        except CapabilityError as exc:
            return self._fail(node, started, str(exc))
        except Exception as exc:  # noqa: BLE001 - node-level containment
            return self._fail(node, started, f"{type(exc).__name__}: {exc}")

        if not isinstance(result, CapabilityResult):
            return self._fail(
                node,
                started,
                f"capability '{node.capability}' returned "
                f"{type(result).__name__}, expected CapabilityResult",
            )
        if not result.text:
            return self._fail(
                node, started, f"capability '{node.capability}' produced no output"
            )

        node.output = result.text
        # Keep the planner's image assignment; layer the result on top.
        node.artifacts = {**node.artifacts, **result.artifacts}
        node.status = "done"
        node.duration_s = round(time.perf_counter() - started, 2)
        _log.info(
            "%s done in %.2fs (%d chars)", self.name, node.duration_s, len(node.output)
        )
        return node

    def _call_with_timeout(self, fn, request: CapabilityRequest) -> CapabilityResult:
        future = _TIMEOUT_POOL.submit(fn, request)
        return future.result(timeout=RUNTIME.call_timeout)

    def _fail(self, node: Node, started: float, message: str) -> Node:
        node.status = "failed"
        node.error = message
        node.duration_s = round(time.perf_counter() - started, 2)
        node.output = None
        _log.error("%s failed after %.2fs: %s", self.name, node.duration_s, message)
        return node
