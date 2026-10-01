"""Exception hierarchy for Medical AOS.

Every failure raised by our own code inherits from :class:`MedicalAOSError`, so
callers can distinguish "our pipeline said no" from "a dependency blew up".
"""

from __future__ import annotations


class MedicalAOSError(Exception):
    """Base class for every error raised by this package."""


class ConfigError(MedicalAOSError):
    """Missing credentials or an impossible configuration combination."""


class StudyError(MedicalAOSError):
    """The study folder is missing, empty, or contains nothing readable."""


class UnsupportedFileError(StudyError):
    """A file in the study folder could not be decoded as an image."""


class CapabilityError(MedicalAOSError):
    """A capability failed after exhausting its retries."""

    def __init__(self, capability: str, message: str, cause: Exception | None = None):
        super().__init__(f"[{capability}] {message}")
        self.capability = capability
        self.cause = cause


class UnknownCapabilityError(MedicalAOSError):
    """The planner requested a capability that is not registered."""


class BackendError(MedicalAOSError):
    """A model backend (local transformers or HF Inference) is unusable."""


class GraphValidationError(MedicalAOSError):
    """The task graph is structurally invalid (cycle, dangling edge, ...)."""


class PlanningError(MedicalAOSError):
    """The planner could not produce a usable graph."""
