"""Capability contract and registry.

A *capability* is one thing the system knows how to do to a study. Every
capability has the same signature -- it takes a :class:`CapabilityRequest` and
returns a :class:`CapabilityResult` -- so the executor can run any node without
knowing what is inside it, and new capabilities can be added without touching
the orchestrator.

The request carries more than a string. The original design passed only the
concatenated parent text, which meant a vision node had no way to know *which*
images it was responsible for. Here each node names its own ``image_ids`` and
receives the parent nodes' structured ``artifacts`` alongside their prose.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from core.errors import UnknownCapabilityError
from models import Study, StudyImage


@dataclass
class CapabilityRequest:
    """Everything a capability needs to do its job."""

    instruction: str  # the node description; doubles as the prompt
    text: str = ""  # concatenated outputs of parent nodes
    study: Study | None = None
    image_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    document_page_numbers: list[int] = field(default_factory=list)
    node_id: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)  # merged from parents

    def documents(self):
        """Return the document pages assigned to this node."""
        if self.study is None:
            return []
        wanted = set(self.document_ids)
        pages = []
        for document in self.study.documents:
            if wanted and document.document_id not in wanted:
                continue
            for page in document.pages:
                if self.document_page_numbers and page.page_number not in self.document_page_numbers:
                    continue
                pages.append((document, page))
        return pages

    def images(self) -> list[StudyImage]:
        """The :class:`StudyImage` objects this node is responsible for."""
        if self.study is None:
            return []
        if not self.image_ids:
            return list(self.study.images)
        wanted = set(self.image_ids)
        return [img for img in self.study.images if img.image_id in wanted]


@dataclass
class CapabilityResult:
    """What a capability produces: prose for the next node, data for the report."""

    text: str
    artifacts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.text = (self.text or "").strip()


class Capability(Protocol):
    def __call__(self, request: CapabilityRequest) -> CapabilityResult: ...


_REGISTRY: dict[str, Capability] = {}
_DESCRIPTIONS: dict[str, str] = {}


def register(name: str, description: str) -> Callable[[Capability], Capability]:
    """Decorator that adds a function to the capability registry."""

    def decorator(fn: Capability) -> Capability:
        _REGISTRY[name] = fn
        _DESCRIPTIONS[name] = description
        return fn

    return decorator


def get(name: str) -> Capability:
    """Look up a capability, with a helpful error listing the valid names."""
    _ensure_loaded()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise UnknownCapabilityError(
            f"Unknown capability '{name}'. Registered: {sorted(_REGISTRY)}"
        ) from exc


def names() -> list[str]:
    _ensure_loaded()
    return sorted(_REGISTRY)


def describe() -> dict[str, str]:
    _ensure_loaded()
    return dict(_DESCRIPTIONS)


def is_registered(name: str) -> bool:
    _ensure_loaded()
    return name in _REGISTRY


_loaded = False
_load_lock = threading.Lock()


def _ensure_loaded() -> None:
    """Import the capability modules so their decorators fire.

    Done lazily to keep ``import registry`` cheap and to avoid a circular
    import: capability modules import this module for the decorator.

    The lock matters: the executor calls ``get()`` from several worker threads
    at once. Setting the flag before the imports completed let a second thread
    observe ``_loaded = True`` with an empty registry and fail the node with
    "Unknown capability".
    """
    global _loaded
    if _loaded:
        return
    with _load_lock:
        if _loaded:
            return
        from capabilities import (  # noqa: F401
            cross_image_synthesis,
            document_analysis,
            image_classification,
            modality_triage,
            report_synthesis,
            specialist_confirmation,
            summarization,
            vision,
            web_search,
        )

        _loaded = True  # only after every module has registered itself


# Capabilities that consume images directly. The planner uses this to decide
# which nodes need an ``image_ids`` assignment.
IMAGE_CAPABILITIES = {
    "vision",
    "image_classification",
    "modality_triage",
    "specialist_confirmation",
}
