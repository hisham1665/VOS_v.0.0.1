import math
from typing import List, Dict
from dataclasses import dataclass
from aos_v0.core.shared_memory import SharedMemoryBank, MemoryEntry

@dataclass
class ContextBudget:
    total_chars: int = 12000
    per_entry_chars: int = 4000

@dataclass
class RecallResult:
    selected_entries: List[MemoryEntry]
    injected_text: str
    total_chars_used: int

class MemoryRetriever:
    def __init__(self, min_relevance: float = 0.15):
        self.min_relevance = min_relevance

    def select(self, node, bank: SharedMemoryBank, budget: ContextBudget) -> RecallResult:
        all_entries = bank.get_all_entries()
        if not all_entries:
            return RecallResult([], "", 0)

        # 1. Build query
        # Head of node input up to 500 chars
        node_input_head = getattr(node, "input", "")[:500]
        query_text = f"{node.description} {node.capability} {node_input_head}".lower()
        query_tokens = set(query_text.split())

        # Build IDF for keys
        doc_freq = {}
        for entry in all_entries:
            for token in set(entry.key.lower().split()):
                doc_freq[token] = doc_freq.get(token, 0) + 1
        num_docs = len(all_entries)

        scored_entries = []
        for entry in all_entries:
            # 3. Exclude self-produced or exact hash matches in node input
            if entry.node_id == node.id:
                continue
            if entry.value_hash in getattr(node, "input", ""): # rudimentary check
                continue

            # 2. Score entry
            entry_tokens = set(entry.key.lower().split())
            overlap = query_tokens & entry_tokens
            
            score = 0.0
            for token in overlap:
                idf = math.log((num_docs - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5) + 1.0)
                score += idf
            
            # Normalize score
            if len(query_tokens) > 0:
                score /= len(query_tokens)

            # Boosts
            # Assuming we can check if entry.node_id is an ancestor
            if entry.node_id in getattr(node, "depends_on", []):
                score += 0.15
            
            if entry.capability != node.capability:
                score += 0.10

            if score >= self.min_relevance:
                scored_entries.append((score, entry))

        # Sort descending by score
        scored_entries.sort(key=lambda x: x[0], reverse=True)

        # 4. Greedily take entries
        selected = []
        chars_used = 0
        
        for score, entry in scored_entries:
            if chars_used >= budget.total_chars:
                break
            
            # Budget check
            entry_chars = len(entry.value)
            take_chars = min(entry_chars, budget.per_entry_chars)
            if chars_used + take_chars > budget.total_chars:
                take_chars = budget.total_chars - chars_used
                
            if take_chars > 0:
                selected.append((entry, take_chars))
                chars_used += take_chars
                # 5. Record recall
                if node.id not in entry.recalled_by:
                    entry.recalled_by.append(node.id)
                    entry.recall_count += 1

        if not selected and not all_entries:
            return RecallResult([], "", 0)

        # Build injection format
        injection = "SHARED MEMORY -- results already produced by earlier steps in this workflow.\nAvailable (summary only):\n"
        for entry in all_entries:
            injection += f"  [{entry.id}] {entry.key}\n"
        
        if selected:
            injection += "\nRetrieved in full for this step:\n"
            for entry, take_chars in selected:
                val = entry.value[:take_chars]
                if len(entry.value) > take_chars:
                    rem = len(entry.value) - take_chars
                    val += f"\n  [...truncated, {rem} chars remain in shared memory as {entry.id}]"
                injection += f"  [{entry.id}] {entry.key}\n  {val}\n"
        
        injection += "\n---\n\n"

        return RecallResult(
            selected_entries=[e for e, _ in selected],
            injected_text=injection,
            total_chars_used=chars_used
        )
