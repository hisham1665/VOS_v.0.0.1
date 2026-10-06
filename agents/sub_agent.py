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
from core.routing import TaskRequirements, performance_history
from capabilities import registry
from capabilities.registry import CapabilityRequest, CapabilityResult
from models import Node, Study, TaskBudget

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
        task_budget: TaskBudget | None = None,
        timeout_s: float | None = None,
        remaining_cost: float | None = None,
    ) -> Node:
        node.status = "running"
        node.performed_by = self.name
        started = time.perf_counter()

        _log.info("%s -> %s: %s", self.name, node.capability, node.description)

        try:
            base = registry.requirements(node.capability)
            overrides = node.artifacts.get("requirements") or {}
            values = {**base.to_dict(), **overrides}
            attempts = task_budget.max_attempts_per_node if task_budget else 2
            for key, limit in (("max_memory_gb", task_budget.max_memory_gb if task_budget else None),
                               ("max_cost", max(0, remaining_cost) if remaining_cost is not None else None)):
                if limit is not None:
                    values[key] = min(values[key], limit) if values.get(key) is not None else limit
            requirements = TaskRequirements(**values)
            candidates, selection = registry.select(requirements, preferred=node.capability,
                                                     excluded=set(node.artifacts.get("_excluded_capabilities", [])))
        except (UnknownCapabilityError, ValueError, TypeError) as exc:
            return self._fail(node, started, str(exc))
        node.artifacts["_selection"] = {"requirements": requirements.to_dict(), "candidates": selection, "attempts": []}
        if not candidates:
            return self._fail(node, started, "No available capability satisfies task requirements")

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
            artifacts={**dict(inherited_artifacts or {}),
                       **{key: value for key, value in node.artifacts.items()
                          if not key.startswith("_") and key not in {"requirements", "image_ids", "document_ids", "document_page_numbers", "verification_requests"}}},
        )

        result = None
        prices = registry.metadata() if remaining_cost is not None else {}
        available_cost = remaining_cost
        node.artifacts["_declared_cost_consumed"] = 0.0
        deadline = started + timeout_s if timeout_s is not None else None
        for candidate in candidates[:attempts]:
            if available_cost is not None:
                price = prices[candidate]["cost_per_call"]
                if price > available_cost + 1e-12:
                    node.artifacts["_selection"]["attempts"].append({"capability": candidate, "status": "budget_rejected",
                                                                    "error": "remaining declared-cost allocation exhausted"})
                    continue
                available_cost -= price
                node.artifacts["_declared_cost_consumed"] += price
            attempt_started = time.perf_counter()
            try:
                timeout = min(RUNTIME.call_timeout, max(0, deadline - time.perf_counter())) if deadline else RUNTIME.call_timeout
                if timeout <= 0:
                    raise FutureTimeout()
                result = self._call_with_timeout(registry.get(candidate), request, timeout=timeout)
                if not isinstance(result, CapabilityResult):
                    raise CapabilityError(candidate, f"returned {type(result).__name__}, expected CapabilityResult")
                if not result.text:
                    raise CapabilityError(candidate, "produced no output")
                node.artifacts["_selection"]["attempts"].append({"capability": candidate, "status": "accepted"})
                performance_history().record(candidate, success=True, seconds=time.perf_counter() - attempt_started)
                node.artifacts["_selected_capability"] = candidate
                break
            except Exception as exc:  # noqa: BLE001 - adapt to an equivalent registered capability
                result = None
                message = (f"capability '{candidate}' exceeded its {timeout:.2f}s time budget"
                           if isinstance(exc, FutureTimeout) else f"{type(exc).__name__}: {exc}")
                node.artifacts["_selection"]["attempts"].append({"capability": candidate, "status": "rejected", "error": message})
                performance_history().record(candidate, success=False, seconds=time.perf_counter() - attempt_started)
                _log.warning("%s rejected; trying a compatible alternative: %s", candidate, message)
        if result is None:
            errors = "; ".join(a.get("error", "") for a in node.artifacts["_selection"]["attempts"])
            return self._fail(node, started, errors)

        node.output = result.text
        # Keep the planner's image assignment; layer the result on top.
        node.artifacts = {**node.artifacts, **result.artifacts}
        node.status = "done"
        node.duration_s = round(time.perf_counter() - started, 2)
        _log.info(
            "%s done in %.2fs (%d chars)", self.name, node.duration_s, len(node.output)
        )
        return node

    def _call_with_timeout(self, fn, request: CapabilityRequest, *, timeout: float | None = None) -> CapabilityResult:
        from core.execution_context import call_deadline, remaining_time

        limit = RUNTIME.call_timeout if timeout is None else timeout
        deadline = time.perf_counter() + limit

        def invoke():
            token = call_deadline.set(deadline)
            try:
                remaining_time(limit)  # expired queued calls must not start work
                return fn(request)
            finally:
                call_deadline.reset(token)

        future = _TIMEOUT_POOL.submit(invoke)
        try:
            return future.result(timeout=limit)
        except FutureTimeout:
            future.cancel()  # cancels queued work; running Python/C calls cannot be interrupted
            raise

    def _fail(self, node: Node, started: float, message: str) -> Node:
        node.status = "failed"
        node.error = message
        node.duration_s = round(time.perf_counter() - started, 2)
        node.output = None
        _log.error("%s failed after %.2fs: %s", self.name, node.duration_s, message)
        return node
