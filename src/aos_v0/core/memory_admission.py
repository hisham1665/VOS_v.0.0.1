"""Selective memory admission; the source AddAll ablation was faster but less accurate."""

from __future__ import annotations

import json
import re
import threading
import time
import warnings
from pathlib import Path
from typing import Protocol

from aos_v0.core.shared_memory import (AdmissionDecision, MemoryCandidate,
                                       SharedMemoryBank, compute_value_hash)
from aos_v0.config import MEMORY_MIN_SUBSTANCE_CHARS

CAPABILITY_PRIORS = {
    "web_search": 0.95, "document_extraction": 0.95, "speech_transcription": 0.95,
    "vision": 0.85, "code.generation": 0.80, "text.summarization": 0.45,
    "answer.synthesis": 0.05,
}
ERROR_MARKERS = re.compile(
    r"Traceback|^Error:|\[artifact '[^']+' not found\]|I cannot|I'm unable|"
    r"document extraction failed|\(no output produced\)|\[UNAVAILABLE", re.I | re.M,
)


class AdmissionController(Protocol):
    def decide(self, candidate: MemoryCandidate, bank: SharedMemoryBank, node=None) -> AdmissionDecision: ...


class HeuristicController:
    def __init__(self, base_threshold: float = 0.50, max_threshold: float = 0.85,
                 min_substance_chars: int = MEMORY_MIN_SUBSTANCE_CHARS,
                 weights: dict[str, float] | None = None) -> None:
        self.base_threshold = base_threshold
        self.max_threshold = max_threshold
        self.min_substance_chars = min_substance_chars
        self.weights = weights or {"capability_prior": 0.30, "novelty": 0.30,
                                   "cost_prior": 0.20, "substance": 0.10,
                                   "fanout_prior": 0.10}

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) >= 3}

    def decide(self, candidate: MemoryCandidate, bank: SharedMemoryBank, node=None) -> AdmissionDecision:
        def reject(reason: str) -> AdmissionDecision:
            bank.record_discard()
            return AdmissionDecision(False, 0.0, reason, {})

        if candidate.status in {"degraded", "failed"} or (node is not None and getattr(node, "status", "") in {"degraded", "failed"}):
            return reject(f"status:{candidate.status}")
        if ERROR_MARKERS.search(candidate.value):
            return reject("error_marker")
        if bank.contains_value_hash(candidate.value_hash or compute_value_hash(candidate.value)):
            return reject("duplicate_value")
        if len(candidate.value.strip()) < self.min_substance_chars:
            return reject("trivial")

        candidate_tokens = self._tokens(candidate.value)
        novelty = 1.0
        for entry in bank.get_all_entries()[-32:]:
            other = self._tokens(entry.value)
            if candidate_tokens and other:
                novelty = min(novelty, 1.0 - len(candidate_tokens & other) / len(candidate_tokens | other))
        demand = candidate.dna_demand
        if node is not None and getattr(node, "dna", None):
            demand = node.dna.ordinals.demand()
        fanout = candidate.fanout
        if node is not None:
            fanout = max(fanout, len(getattr(node, "dependants", [])))
        signals = {
            "capability_prior": CAPABILITY_PRIORS.get(candidate.capability, 0.5),
            "novelty": max(0.0, min(1.0, novelty)),
            "cost_prior": max(0.0, min(1.0, demand)),
            "substance": max(0.0, min(1.0, (len(candidate.value) - self.min_substance_chars) / 1800)),
            "fanout_prior": max(0.0, min(1.0, fanout / 4)),
        }
        total = sum(signals[name] * weight for name, weight in self.weights.items())
        pressure = min(1.0, bank.stats().total_bytes / max(1, bank.max_total_bytes))
        threshold = self.base_threshold + (self.max_threshold - self.base_threshold) * pressure
        signals["threshold"] = threshold
        return AdmissionDecision(total >= threshold, total,
                                 "admitted" if total >= threshold else "below_threshold", signals)


class DecisionLog:
    def __init__(self, request_id: str = "", log_dir: str | Path = "log/memory_decisions", enabled: bool = True) -> None:
        self.request_id = request_id
        self.path = Path(log_dir) / f"{request_id or 'request'}.jsonl"
        self.enabled = enabled
        self._lock = threading.Lock()
        self._disabled = False

    def _write(self, record: dict) -> None:
        if not self.enabled or self._disabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            self._disabled = True
            warnings.warn(f"AOS memory decision log disabled: {exc}", RuntimeWarning, stacklevel=2)

    def record(self, candidate: MemoryCandidate, decision: AdmissionDecision,
               existing_keys: list[str], task_query: str = "", entry_id: str | None = None) -> None:
        self._write({"ts": time.time(), "request_id": self.request_id, "node_id": candidate.node_id,
                     "capability": candidate.capability,
                     "context": {"query": task_query, "existing_keys": existing_keys,
                                 "agent_input": candidate.agent_input[:2000],
                                 "agent_output": candidate.value[:4000], "summary": candidate.key},
                     "decision": "ADMIT" if decision.admitted else "DISCARD", "score": decision.score,
                     "signals": decision.signals, "entry_id": entry_id})

    def log_decision(self, request_id, candidate, decision, bank, entry_id=None):
        self.request_id = request_id
        self.record(candidate, decision, [item.key for item in bank.keys()], entry_id=entry_id)

    def record_outcome(self, reward_proxy: dict, utilised: list[str], runtime_s: float,
                       total_injected_chars: int) -> None:
        self._write({"ts": time.time(), "record": "outcome", "request_id": self.request_id,
                     "reward_proxy": reward_proxy, "utilised": utilised, "runtime_s": runtime_s,
                     "total_injected_chars": total_injected_chars})

    def log_outcome(self, request_id, reward_proxy, utilised, runtime_s, total_injected_chars):
        self.request_id = request_id
        self.record_outcome(reward_proxy, utilised, runtime_s, total_injected_chars)
