"""Phase 14 health module.

Model health checks (registry availability), queue management visibility and
storage writability for the exam pipeline. GPU/VRAM/CPU utilization are
deployment-telemetry responsibilities (API layer, spec Ph14) and are
explicitly not fabricated here.
"""

from .healthcheck import (
    HealthReport,
    ResourceHealth,
    healthcheck,
    render_health_table,
)

__all__ = [
    "HealthReport",
    "ResourceHealth",
    "healthcheck",
    "render_health_table",
]