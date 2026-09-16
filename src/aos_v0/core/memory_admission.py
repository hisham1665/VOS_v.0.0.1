import json
import os
import time
from typing import Protocol, List, Dict
from aos_v0.core.shared_memory import MemoryCandidate, AdmissionDecision, SharedMemoryBank
from aos_v0.config import MEMORY_MIN_SUBSTANCE_CHARS

class AdmissionController(Protocol):
    def decide(self, candidate: MemoryCandidate, bank: SharedMemoryBank, node: 'Node') -> AdmissionDecision: ...

class HeuristicController:
    def __init__(self):
        self.capability_priors = {
            "web_search": 0.95,
            "document_extraction": 0.95,
            "speech_transcription": 0.95,
            "vision": 0.85,
            "code.generation": 0.80,
            "text.summarization": 0.45,
            "answer.synthesis": 0.05
        }
        
    def decide(self, candidate: MemoryCandidate, bank: SharedMemoryBank, node) -> AdmissionDecision:
        # Hard rejects
        if getattr(node, "status", None) in ("degraded", "failed"):
            return AdmissionDecision(False, 0.0, {}, "node failed/degraded")
        
        value_lower = candidate.value.lower()
        if "[artifact '" in value_lower and "' not found]" in value_lower:
            return AdmissionDecision(False, 0.0, {}, "artifact not found error")
            
        error_markers = ["traceback", "^error:", "i cannot", "i'm unable", "document extraction failed"]
        for marker in error_markers:
            if marker in value_lower:
                return AdmissionDecision(False, 0.0, {}, f"error marker '{marker}'")
                
        # Failure manager gap marker
        if "<GAP>" in candidate.value or "FAILURE_MANAGER" in candidate.value: # Approximate for _gap_marker
            return AdmissionDecision(False, 0.0, {}, "gap marker signature")

        if len(candidate.value) < MEMORY_MIN_SUBSTANCE_CHARS:
            return AdmissionDecision(False, 0.0, {}, "trivial step length")

        for entry in bank.get_all_entries():
            if entry.value_hash == candidate.value_hash:
                return AdmissionDecision(False, 0.0, {}, "exact duplicate hash")

        # Weighted score computation
        cap_prior = self.capability_priors.get(candidate.capability, 0.5)
        
        # Novelty: Approximate via fast token overlap
        cand_tokens = set(candidate.value.lower().split())
        max_jaccard = 0.0
        for entry in bank.get_all_entries():
            ent_tokens = set(entry.value.lower().split())
            if not cand_tokens or not ent_tokens: continue
            jaccard = len(cand_tokens & ent_tokens) / len(cand_tokens | ent_tokens)
            if jaccard > max_jaccard:
                max_jaccard = jaccard
        novelty = 1.0 - max_jaccard
        
        # Cost prior
        cost_prior = 0.5
        if hasattr(node, "dna") and hasattr(node.dna, "ordinals") and hasattr(node.dna.ordinals, "demand"):
            cost_prior = min(1.0, node.dna.ordinals.demand() / 10.0) # Assume 0-10 scale
            
        # Substance
        val_len = len(candidate.value)
        substance = min(1.0, (val_len - MEMORY_MIN_SUBSTANCE_CHARS) / 1800.0) if val_len > MEMORY_MIN_SUBSTANCE_CHARS else 0.0
        
        # Fanout prior (approximation: dependants count)
        fanout_prior = min(1.0, len(getattr(node, "dependants", [])) / 5.0)

        score = (0.30 * cap_prior) + (0.30 * novelty) + (0.20 * cost_prior) + (0.10 * substance) + (0.10 * fanout_prior)
        
        signals = {
            "capability_prior": cap_prior,
            "novelty": novelty,
            "cost_prior": cost_prior,
            "substance": substance,
            "fanout_prior": fanout_prior
        }

        # Adaptive sparsity
        stats = bank.get_stats()
        utilization = stats.total_bytes / bank.max_total_bytes
        threshold = 0.50 + (0.35 * utilization) # 0.50 to 0.85

        if score >= threshold:
            return AdmissionDecision(True, score, signals, "passed threshold")
        else:
            return AdmissionDecision(False, score, signals, "below adaptive threshold")


class DecisionLog:
    def __init__(self, log_dir: str = "log/memory_decisions"):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        
    def log_decision(self, request_id: str, candidate: MemoryCandidate, decision: AdmissionDecision, bank: SharedMemoryBank, entry_id: str = None):
        log_file = os.path.join(self.log_dir, f"{request_id}.jsonl")
        
        existing_keys = [e.key for e in bank.get_all_entries()]
        
        record = {
            "ts": time.time(),
            "request_id": request_id,
            "node_id": candidate.node_id,
            "capability": candidate.capability,
            "context": {
                "existing_keys": existing_keys,
                "agent_output": candidate.value[:4000],
                "summary": candidate.key
            },
            "decision": "ADMIT" if decision.admit else "DISCARD",
            "score": decision.score,
            "signals": decision.signals,
            "entry_id": entry_id
        }
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def log_outcome(self, request_id: str, reward_proxy: Dict, utilised: List[str], runtime_s: float, total_injected_chars: int):
        log_file = os.path.join(self.log_dir, f"{request_id}.jsonl")
        record = {
            "ts": time.time(),
            "record": "outcome",
            "request_id": request_id,
            "reward_proxy": reward_proxy,
            "utilised": utilised,
            "runtime_s": runtime_s,
            "total_injected_chars": total_injected_chars
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
