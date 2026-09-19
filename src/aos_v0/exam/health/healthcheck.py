"""Healthchecks: per-resource availability, storage, and review-queue state.

``availability > 0`` (the registry's continuous scorer field) means the
resource is selectable; a healthy local pool should report every capability
with exactly one available structural resource. ``review_dir`` optionally
reports the Phase-11 review queue depth.
"""

from __future__ import annotations

import os
import tempfile
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ResourceHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    capability: str
    resource_class: str = "local"
    availability: float
    healthy: bool


class HealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall: str = "healthy"  # healthy | degraded
    resources: List[ResourceHealth] = Field(default_factory=list)
    storage_writable: bool = True
    review_queue_depth: int = 0
    notes: List[str] = Field(default_factory=list)


def healthcheck(
    registry=None,
    *,
    review_dir: Optional[str] = None,
    storage_dir: Optional[str] = None,
) -> HealthReport:
    """Check every registered resource, storage write access, and the queue."""
    from aos_v0.exam.orchestrator import build_exam_registry

    registry = registry or build_exam_registry()
    resources: List[ResourceHealth] = []
    notes: List[str] = []
    for manifest in registry.manifests():
        availability_obj = getattr(manifest, "availability", None)
        status = getattr(availability_obj, "status", "down")
        healthy = status == "up"
        capabilities = ", ".join(manifest.capabilities or ["unknown"])
        resources.append(
            ResourceHealth(
                resource_id=manifest.resource_id,
                capability=capabilities,
                resource_class=getattr(manifest, "resource_class", "local"),
                availability=1.0 if healthy else 0.0,
                healthy=healthy,
            )
        )

    storage_writable = _probe_storage(storage_dir)
    if not storage_writable:
        notes.append("storage probe failed: target directory is not writable")

    queue_depth = 0
    if review_dir is not None:
        try:
            from aos_v0.exam.review import ReviewStore

            store = ReviewStore(review_dir)
            stats = store.stats()
            queue_depth = int(stats.pending)
        except Exception as exc:  # noqa: BLE001 - health must never crash itself
            notes.append(f"review store probe failed: {exc}")

    unhealthy = [r for r in resources if not r.healthy]
    overall = "degraded" if (unhealthy or not storage_writable) else "healthy"
    return HealthReport(
        overall=overall,
        resources=resources,
        storage_writable=storage_writable,
        review_queue_depth=queue_depth if review_dir is not None else 0,
        notes=notes,
    )


def _probe_storage(directory: Optional[str]) -> bool:
    target = directory or tempfile.gettempdir()
    try:
        probe = os.path.join(target, f".aos_health_{os.getpid()}.tmp")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def render_health_table(report: HealthReport) -> str:
    lines = [
        "RESOURCE HEALTH",
        "=" * 60,
        f"overall: {report.overall}   storage: "
        f"{'writable' if report.storage_writable else 'NOT writable'}"
        f"   review queue depth: {report.review_queue_depth}",
        "",
        f"{'resource_id':<38} {'class':<10} {'avail':<8} health",
    ]
    for resource in report.resources:
        lines.append(
            f"{resource.resource_id:<38} {resource.resource_class:<10} "
            f"{resource.availability:<8.2f} "
            f"{'OK' if resource.healthy else 'DOWN'}"
        )
    if report.notes:
        lines.append("")
        lines.extend(f"  note: {n}" for n in report.notes)
    return "\n".join(lines)