"""Bounded, selective memory shared by graph nodes and sessions."""

from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel, Field

from aos_v0.config import MEMORY_MAX_ENTRIES, MEMORY_MAX_TOTAL_BYTES


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def compute_input_hash(capability: str, agent_input: str) -> str:
    return hashlib.sha256(f"{capability}|{agent_input}".encode()).hexdigest()


def compute_value_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class MemoryEntry(BaseModel):
    id: str
    key: str
    value: str
    node_id: str
    capability: str
    resource_id: str | None = None
    input_hash: str
    value_hash: str
    tokens_est: int = 0
    scope: str = "request"
    admitted_by: str = "heuristic"
    admission_score: float = 0.0
    created_at: float = Field(default_factory=time.time)
    recalled_by: list[str] = Field(default_factory=list)
    recall_count: int = 0
    reusable: bool = True


class MemoryKey(BaseModel):
    id: str
    key: str
    capability: str
    node_id: str
    tokens_est: int = 0


@dataclass
class MemoryCandidate:
    node_id: str
    capability: str
    description: str = ""
    resource_id: str | None = None
    status: str = "done"
    value: str = ""
    agent_input: str = ""
    dna_demand: float = 0.0
    fanout: int = 0
    input_hash: str | None = None
    value_hash: str | None = None
    tokens_est: int | None = None
    key: str = ""


@dataclass
class AdmissionDecision:
    admitted: bool
    score: float
    reason: str
    signals: dict[str, float] = field(default_factory=dict)

    @property
    def admit(self) -> bool:
        return self.admitted


@dataclass
class MemoryStats:
    entries: int
    total_bytes: int
    admitted: int = 0
    discarded: int = 0
    recalls: int = 0
    evictions: int = 0
    by_capability: dict[str, int] = field(default_factory=dict)

    @property
    def entries_count(self) -> int:
        return self.entries


class SharedMemoryBank:
    """Thread-safe request/session store with exact reuse and bounded eviction."""

    def __init__(self, request_id: str, session_bank=None, *, max_entries: int = MEMORY_MAX_ENTRIES,
                 max_value_bytes: int = 32000, max_total_bytes: int = MEMORY_MAX_TOTAL_BYTES,
                 scope: str = "request", ttl_s: float = 3600):
        self.request_id = request_id
        self.session_bank = session_bank
        self.max_entries = max(1, max_entries)
        self.max_value_bytes = max(256, max_value_bytes)
        self.max_total_bytes = max(256, max_total_bytes)
        self.scope = scope
        self.ttl_s = ttl_s
        self._prefix = uuid.uuid4().hex[:8]
        self.entries: dict[str, MemoryEntry] = {}
        self._lock = threading.RLock()
        self._next_id = 1
        self._admitted = 0
        self._discarded = 0
        self._evictions = 0
        self.last_evicted: list[MemoryEntry] = []

    def _new_id(self) -> str:
        prefix = "S" if self.scope == "session" else "M"
        result = f"{prefix}{self._prefix}-{self._next_id}"
        self._next_id += 1
        return result

    def get_all_entries(self) -> list[MemoryEntry]:
        with self._lock:
            self._expire()
            own = list(self.entries.values())
            if self.session_bank is None:
                return own
            seen = {entry.id for entry in own}
            return own + [entry for entry in self.session_bank.get_all_entries() if entry.id not in seen]

    def get_request_entries(self) -> list[MemoryEntry]:
        with self._lock:
            self._expire()
            return list(self.entries.values())

    def _expire(self):
        now = time.time()
        for key, entry in list(self.entries.items()):
            if now - entry.created_at >= self.ttl_s:
                del self.entries[key]

    def keys(self, include_session: bool = True) -> list[MemoryKey]:
        entries = self.get_all_entries() if include_session else self.get_request_entries()
        return [MemoryKey(id=e.id, key=e.key, capability=e.capability, node_id=e.node_id, tokens_est=e.tokens_est) for e in entries]

    def stats(self) -> MemoryStats:
        entries = self.get_request_entries()
        return MemoryStats(
            entries=len(entries),
            total_bytes=sum(len(e.value.encode()) for e in entries),
            admitted=self._admitted,
            discarded=self._discarded,
            recalls=sum(e.recall_count for e in entries),
            evictions=self._evictions,
            by_capability={cap: sum(e.capability == cap for e in entries) for cap in {e.capability for e in entries}},
        )

    get_stats = stats

    def admit(self, candidate: MemoryCandidate, score: float | AdmissionDecision = 1.0,
              admitted_by: str = "forced", reason: str = "") -> MemoryEntry:
        if isinstance(score, AdmissionDecision):
            decision = score
            score, admitted_by, reason = decision.score, "heuristic", decision.reason
        value = candidate.value or ""
        raw_bytes = len(value.encode())
        limit = min(self.max_value_bytes, self.max_total_bytes)
        truncated = raw_bytes > limit
        if truncated:
            suffix = "\n[…truncated by AOS]"
            value = value.encode()[:limit - len(suffix.encode())].decode("utf-8", errors="ignore") + suffix
        with self._lock:
            entry = MemoryEntry(
                id=self._new_id(),
                key=(candidate.key or f"{candidate.capability}: {candidate.description[:100]}")[:200],
                value=value,
                node_id=candidate.node_id,
                capability=candidate.capability,
                resource_id=candidate.resource_id,
                input_hash=candidate.input_hash or compute_input_hash(candidate.capability, candidate.agent_input),
                value_hash=candidate.value_hash or compute_value_hash(value),
                tokens_est=candidate.tokens_est or max(1, len(value) // 4),
                scope=self.scope,
                admitted_by=admitted_by,
                admission_score=float(score),
                reusable=not truncated and candidate.status == "done",
            )
            self.last_evicted = self._evict_for(len(value.encode()))
            self.entries[entry.id] = entry
            self._admitted += 1
            return entry

    def _evict_for(self, needed_bytes: int) -> list[MemoryEntry]:
        evicted = []
        while self.entries and (len(self.entries) >= self.max_entries or sum(len(e.value.encode()) for e in self.entries.values()) + needed_bytes > self.max_total_bytes):
            target = min(self.entries.values(), key=lambda e: (e.recall_count, e.created_at, e.id))
            evicted.append(self.entries.pop(target.id))
            self._evictions += 1
        return evicted

    def record_discard(self) -> None:
        with self._lock:
            self._discarded += 1

    def consume_evicted(self) -> list[MemoryEntry]:
        with self._lock:
            result, self.last_evicted = self.last_evicted, []
            return result

    def get(self, entry_id: str, recalled_by: str | None = None):
        with self._lock:
            self._expire()
            entry = self.entries.get(entry_id)
            if entry is None and self.session_bank is not None:
                return self.session_bank.get(entry_id, recalled_by)
            if entry is not None and recalled_by and recalled_by not in entry.recalled_by:
                entry.recalled_by.append(recalled_by)
                entry.recall_count += 1
            return entry

    def lookup_reusable(self, capability: str, input_hash: str):
        return next((e for e in self.get_all_entries() if e.capability == capability and e.input_hash == input_hash and e.value and e.reusable), None)

    def contains_value_hash(self, value_hash: str) -> bool:
        return any(e.value_hash == value_hash for e in self.get_all_entries())

    def promote(self, predicate: Callable[[MemoryEntry], bool]) -> int:
        if self.session_bank is None:
            return 0
        moved = []
        with self._lock:
            for entry_id, entry in list(self.entries.items()):
                if predicate(entry):
                    moved.append(entry.model_copy(update={"scope": "session"}))
                    del self.entries[entry_id]
        for entry in moved:
            self.session_bank._insert_promoted(entry)
        return len(moved)

    def promote_to_session(self) -> int:
        return self.promote(lambda e: e.recall_count > 0 or e.capability in {"document_extraction", "speech_transcription", "web_search", "vision"})

    def _insert_promoted(self, entry: MemoryEntry) -> None:
        with self._lock:
            self._evict_for(len(entry.value.encode()))
            self.entries[entry.id] = entry

    def clear(self) -> None:
        with self._lock:
            self.entries.clear()
            self.last_evicted.clear()

    def dump_to_text(self, filepath: str) -> None:
        target = Path(filepath)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            handle.write(f"AOS memory dump: {self.request_id}\n")
            for entry in self.get_all_entries():
                handle.write(f"\n[{entry.id}] {entry.key}\n{entry.value}\n")
