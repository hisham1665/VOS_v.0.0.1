"""Deterministic selective admission for shared AOS memory."""

from __future__ import annotations

import re
import json
import threading
import time
from pathlib import Path
from typing import Protocol

from aos_v0.config import MEMORY_MIN_SUBSTANCE_CHARS
from aos_v0.core.shared_memory import AdmissionDecision, MemoryCandidate, SharedMemoryBank, compute_value_hash

CAPABILITY_PRIORS = {
    "web_search": 0.95,
    "document_extraction": 0.95,
    "speech_transcription": 0.95,
    "vision": 0.85,
    "text.summarization": 0.45,
    "answer.synthesis": 0.05,
}
ERROR_MARKERS = re.compile(r"Traceback|^Error:|\[UNAVAILABLE|document extraction failed|I cannot|I'm unable", re.I | re.M)


class AdmissionController(Protocol):
    def decide(self, candidate: MemoryCandidate, bank: SharedMemoryBank, node=None) -> AdmissionDecision: ...


class HeuristicController:
    def __init__(self, base_threshold: float = 0.50, max_threshold: float = 0.85, min_substance_chars: int = MEMORY_MIN_SUBSTANCE_CHARS):
        self.base_threshold = base_threshold
        self.max_threshold = max_threshold
        self.min_substance_chars = min_substance_chars

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) >= 3}

    def decide(self, candidate: MemoryCandidate, bank: SharedMemoryBank, node=None) -> AdmissionDecision:
        def reject(reason: str):
            bank.record_discard()
            return AdmissionDecision(False, 0.0, reason, {})
        if candidate.status in {"degraded", "failed"} or ERROR_MARKERS.search(candidate.value):
            return reject(f"status:{candidate.status}" if candidate.status in {"degraded", "failed"} else "error_marker")
        if bank.contains_value_hash(candidate.value_hash or compute_value_hash(candidate.value)):
            return reject("duplicate_value")
        if len(candidate.value.strip()) < self.min_substance_chars:
            return reject("trivial")
        tokens = self._tokens(candidate.value)
        novelty = 1.0
        for entry in bank.get_all_entries()[-32:]:
            other = self._tokens(entry.value)
            if tokens and other:
                novelty = min(novelty, 1.0 - len(tokens & other) / len(tokens | other))
        demand = node.dna.ordinals.demand() if node is not None and getattr(node, "dna", None) else candidate.dna_demand
        fanout = candidate.fanout
        signals = {
            "capability_prior": CAPABILITY_PRIORS.get(candidate.capability, 0.5),
            "novelty": max(0.0, min(1.0, novelty)),
            "cost_prior": max(0.0, min(1.0, demand)),
            "substance": max(0.0, min(1.0, (len(candidate.value) - self.min_substance_chars) / 1800)),
            "fanout_prior": max(0.0, min(1.0, fanout / 4)),
        }
        weights = {"capability_prior": .3, "novelty": .3, "cost_prior": .2,
                   "substance": .1, "fanout_prior": .1}
        score = sum(signals[key] * weight for key, weight in weights.items())
        pressure = min(1.0, bank.stats().total_bytes / max(1, bank.max_total_bytes))
        threshold = self.base_threshold + (self.max_threshold - self.base_threshold) * pressure
        signals["threshold"] = threshold
        if score < threshold:
            bank.record_discard()
        return AdmissionDecision(score >= threshold, score, "admitted" if score >= threshold else "below_threshold", signals)


class DecisionLog:
    """Compatibility surface; content logging is opt-in and bounded."""
    def __init__(self, request_id: str = "", log_dir: str = "log/memory_decisions", enabled: bool = False,
                 log_content: bool = False):
        self.enabled = enabled
        self.log_content = log_content
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", request_id) or "request"
        self.path = Path(log_dir) / f"{name}.jsonl"
        self._lock = threading.Lock()

    def record(self, candidate, decision, existing_keys=None, entry_id=None):
        if not self.enabled:
            return
        record = {"ts": time.time(), "node_id": candidate.node_id, "capability": candidate.capability,
                  "decision": "admit" if decision.admit else "discard", "score": decision.score,
                  "reason": decision.reason, "entry_id": entry_id}
        if self.log_content:
            record["content"] = candidate.value[:4000]
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            self.enabled = False

    log_decision = record
