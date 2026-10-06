"""Domain-neutral capability DNA, requirements, and performance-aware ranking.

No clinical imports or disease-specific decisions. A registry may use this for
models, tools, or arbitrary agents. Unknown cost/resource values remain unknown.
"""
from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class CapabilityDNA:
    name: str
    skills: tuple[str, ...]
    inputs: tuple[str, ...] = ("text",)
    outputs: tuple[str, ...] = ("text",)
    domain: str = "general"
    tools: tuple[str, ...] = ()
    context_tokens: int | None = None
    memory_gb: float | None = None
    cost_per_call: float | None = None
    expected_latency_s: float | None = None
    priority: int = 0
    enabled: bool = True
    # Alternatives must independently satisfy the required skills + outputs.
    fallbacks: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TaskRequirements:
    skills: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    context_tokens: int = 0
    max_memory_gb: float | None = None
    max_cost: float | None = None
    max_latency_s: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def mismatch(dna: CapabilityDNA, task: TaskRequirements) -> list[str]:
    reasons = []
    if not dna.enabled:
        reasons.append("disabled")
    for field in ("skills", "inputs", "outputs", "tools"):
        missing = set(getattr(task, field)) - set(getattr(dna, field))
        if missing:
            reasons.append(f"missing {field}: {sorted(missing)}")
    if task.context_tokens and (dna.context_tokens is None or task.context_tokens > dna.context_tokens):
        reasons.append("context budget unknown or exceeded")
    for limit, value, label in ((task.max_memory_gb, dna.memory_gb, "memory"),
                               (task.max_cost, dna.cost_per_call, "cost"),
                               (task.max_latency_s, dna.expected_latency_s, "latency")):
        if limit is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                  or not math.isfinite(value) or value < 0 or value > limit):
            reasons.append(f"{label} unknown or exceeds constraint")
    return reasons


class PerformanceHistory:
    """Local telemetry containing identifiers and aggregates, never task text."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.RLock()
        self.records: dict[str, dict] = {}
        if path and path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    for name, row in payload.items():
                        if not isinstance(name, str) or not isinstance(row, dict):
                            continue
                        if any(not isinstance(row.get(key, 0), (int, float))
                               or not math.isfinite(row.get(key, 0)) or row.get(key, 0) < 0
                               for key in ("attempts", "successes", "latency_s", "cooldown_until")):
                            continue
                        self.records[name] = row
            except (OSError, ValueError):
                pass

    def record(self, name: str, *, success: bool, seconds: float, cooldown_s: float = 0) -> None:
        with self._lock:
            row = self.records.setdefault(name, {"attempts": 0, "successes": 0})
            row.setdefault("attempts", 0)
            row.setdefault("successes", 0)
            row["attempts"] += 1
            row["successes"] += int(success)
            row["latency_s"] = round(0.75 * row.get("latency_s", seconds) + 0.25 * seconds, 4)
            row["last_success"] = success
            row["cooldown_until"] = 0 if success else time.time() + max(0, cooldown_s)
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = self.path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(self.records, indent=2), encoding="utf-8")
                    tmp.replace(self.path)
                except OSError:
                    pass  # telemetry is optional, never block execution

    def snapshot(self, name: str) -> dict:
        with self._lock:
            return dict(self.records.get(name, {}))


def rank_candidates(candidates: list[CapabilityDNA], task: TaskRequirements,
                    history: PerformanceHistory, *, preferred: str | None = None,
                    excluded: set[str] | None = None) -> tuple[list[CapabilityDNA], list[dict]]:
    ranked, trace = [], []
    for dna in candidates:
        stats = history.snapshot(dna.name)
        reasons = mismatch(dna, task)
        if dna.name in (excluded or set()):
            reasons.append("already attempted")
        if stats.get("cooldown_until", 0) > time.time():
            reasons.append("temporarily unavailable after recent failure")
        # A small prior prevents a single lucky run from dominating selection.
        reliability = (stats.get("successes", 0) + 2) / (stats.get("attempts", 0) + 3)
        latency = stats.get("latency_s", dna.expected_latency_s or 1)
        score = dna.priority + 5 * reliability - math.log1p(max(0, latency))
        if dna.name == preferred:
            score += 2
        trace.append({"candidate": dna.name, "eligible": not reasons, "reasons": reasons,
                      "score": round(score, 4), "history": stats})
        if not reasons:
            ranked.append((score, dna.name, dna))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    return [entry[2] for entry in ranked], trace


_HISTORY = None
_HISTORY_LOCK = threading.Lock()


def performance_history() -> PerformanceHistory:
    global _HISTORY
    with _HISTORY_LOCK:
        if _HISTORY is None:
            from config import RUNTIME

            _HISTORY = PerformanceHistory(RUNTIME.cache_dir / "orchestration-performance.json")
        return _HISTORY
