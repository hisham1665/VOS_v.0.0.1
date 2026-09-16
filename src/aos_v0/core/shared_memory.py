import threading
import time
from typing import Optional, List, Dict
from pydantic import BaseModel
from dataclasses import dataclass
from aos_v0.config import MEMORY_MAX_ENTRIES, MEMORY_MAX_TOTAL_BYTES

class MemoryEntry(BaseModel):
    id: str
    key: str
    value: str
    node_id: str
    capability: str
    resource_id: Optional[str] = None
    input_hash: str
    value_hash: str
    tokens_est: int
    scope: str
    admitted_by: str
    admission_score: float
    created_at: float
    recalled_by: List[str]
    recall_count: int

@dataclass
class MemoryCandidate:
    node_id: str
    capability: str
    resource_id: Optional[str]
    input_hash: str
    value_hash: str
    tokens_est: int
    key: str
    value: str

@dataclass
class AdmissionDecision:
    admit: bool
    score: float
    signals: Dict[str, float]
    reason: str

@dataclass
class MemoryStats:
    entries_count: int
    total_bytes: int

class SharedMemoryBank:
    """Thread-safe two-tier (request/session) store. Handles eviction."""
    def __init__(self, request_id: str, session_bank: Optional['SharedMemoryBank'] = None):
        self.request_id = request_id
        self.session_bank = session_bank
        self.entries: Dict[str, MemoryEntry] = {}
        self._lock = threading.RLock()
        self._next_id = 1
        self.max_entries = MEMORY_MAX_ENTRIES
        self.max_total_bytes = MEMORY_MAX_TOTAL_BYTES

    def get_all_entries(self) -> List[MemoryEntry]:
        with self._lock:
            entries = list(self.entries.values())
            if self.session_bank:
                entries.extend(self.session_bank.get_all_entries())
            return entries

    def get_request_entries(self) -> List[MemoryEntry]:
        with self._lock:
            return list(self.entries.values())
            
    def get_stats(self) -> MemoryStats:
        with self._lock:
            return MemoryStats(
                entries_count=len(self.entries),
                total_bytes=sum(len(e.value) for e in self.entries.values())
            )

    def _generate_id(self) -> str:
        with self._lock:
            eid = f"M{self._next_id}"
            self._next_id += 1
            return eid

    def admit(self, candidate: MemoryCandidate, decision: AdmissionDecision) -> MemoryEntry:
        with self._lock:
            # Enforce limits
            while len(self.entries) >= self.max_entries or \
                  self.get_stats().total_bytes + len(candidate.value) > self.max_total_bytes:
                if not self._evict():
                    break
                    
            entry = MemoryEntry(
                id=self._generate_id(),
                key=candidate.key,
                value=candidate.value,
                node_id=candidate.node_id,
                capability=candidate.capability,
                resource_id=candidate.resource_id,
                input_hash=candidate.input_hash,
                value_hash=candidate.value_hash,
                tokens_est=candidate.tokens_est,
                scope="request",
                admitted_by="heuristic",
                admission_score=decision.score,
                created_at=time.time(),
                recalled_by=[],
                recall_count=0
            )
            self.entries[entry.id] = entry
            return entry

    def _evict(self) -> bool:
        """Evicts the lowest recall, then oldest entry. Return True if evicted."""
        if not self.entries:
            return False
        # Evict policy: lowest recall_count, then oldest (created_at)
        evict_target = min(self.entries.values(), key=lambda e: (e.recall_count, e.created_at))
        del self.entries[evict_target.id]
        return True

    def lookup_reusable(self, capability: str, input_hash: str) -> Optional[MemoryEntry]:
        with self._lock:
            for entry in self.get_all_entries():
                if entry.capability == capability and entry.input_hash == input_hash:
                    return entry
        return None

    def promote_to_session(self):
        """Called at request end to promote useful entries to session tier."""
        if not self.session_bank:
            return
            
        promotable_caps = {"web_search", "document_extraction", "speech_transcription", "vision"}
        with self._lock:
            for entry in self.entries.values():
                if entry.recall_count > 0 or entry.capability in promotable_caps:
                    entry.scope = "session"
                    self.session_bank._add_promoted(entry)
                    
    def _add_promoted(self, entry: MemoryEntry):
        with self._lock:
            while len(self.entries) >= self.max_entries or \
                  self.get_stats().total_bytes + len(entry.value) > self.max_total_bytes:
                if not self._evict():
                    break
            self.entries[entry.id] = entry

    def dump_to_text(self, filepath: str):
        """Saves the memory bank contents to a human-readable text file."""
        import os
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with self._lock:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("=== AOS SHARED MEMORY DUMP ===\n")
                f.write(f"Request ID: {self.request_id}\n")
                f.write(f"Timestamp: {time.time()}\n\n")
                
                f.write("--- REQUEST TIER ---\n")
                if not self.entries:
                    f.write("(empty)\n")
                for eid, entry in self.entries.items():
                    f.write(f"\n[{eid}] {entry.key}\n")
                    f.write(f"Capability: {entry.capability} | Recalls: {entry.recall_count}\n")
                    f.write("-" * 40 + "\n")
                    f.write(entry.value + "\n")
                    f.write("-" * 40 + "\n")
                    
                if self.session_bank:
                    f.write("\n--- SESSION TIER ---\n")
                    session_entries = self.session_bank.get_request_entries()
                    if not session_entries:
                        f.write("(empty)\n")
                    for entry in session_entries:
                        f.write(f"\n[{entry.id}] {entry.key}\n")
                        f.write(f"Capability: {entry.capability} | Recalls: {entry.recall_count}\n")
                        f.write("-" * 40 + "\n")
                        f.write(entry.value + "\n")
                        f.write("-" * 40 + "\n")
