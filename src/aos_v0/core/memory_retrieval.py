"""Key-first, relevance-scored memory retrieval with a strict context budget."""

from __future__ import annotations

import re
from dataclasses import dataclass

from aos_v0.core.shared_memory import SharedMemoryBank


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 3}


@dataclass
class ContextBudget:
    total_chars: int = 12000
    per_entry_chars: int = 4000
    min_relevance: float = 0.15
    max_entries: int = 4


@dataclass
class RecallResult:
    keys_block: str
    values_block: str
    selected: list[str]
    chars_used: int
    scores: dict[str, float]
    limit: int = 0

    @property
    def injected_text(self) -> str:
        if not self.keys_block:
            return ""
        text = self.keys_block + ("\n" + self.values_block if self.values_block else "") + "\n---\n\n"
        return text[:self.limit] if self.limit else text


class MemoryRetriever:
    def select(self, node, bank: SharedMemoryBank, ancestors=None, budget: ContextBudget | None = None) -> RecallResult:
        budget = budget or ContextBudget()
        entries = bank.get_all_entries()
        if not entries:
            return RecallResult("", "", [], 0, {}, budget.total_chars)
        query = _tokens(f"{node.description} {node.capability} {(node.input or '')[:500]}")
        key_lines = ["SHARED MEMORY -- prior results available for this step:"]
        key_lines.extend(f"  [{entry.id}] {entry.key}" for entry in entries)
        keys = "\n".join(key_lines)[:max(0, budget.total_chars // 3)]
        scored = []
        ancestors = ancestors or set()
        for entry in entries:
            if entry.node_id == node.id:
                continue
            score = len(query & _tokens(entry.key)) / max(1, len(query))
            if entry.node_id in ancestors:
                score += 0.15
            if score >= budget.min_relevance:
                scored.append((min(1.0, score), entry))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        selected, blocks, scores, used = [], [], {}, 0
        for score, entry in scored[:budget.max_entries]:
            remaining = max(0, budget.total_chars - len(keys) - used - 80 - len(entry.key))
            value = entry.value[: min(budget.per_entry_chars, remaining)]
            if not value:
                break
            selected.append(entry.id)
            scores[entry.id] = score
            blocks.append(f"  [{entry.id}] {entry.key}\n  {value}")
            used += len(blocks[-1]) + 1
            bank.get(entry.id, recalled_by=node.id)
        values = "Retrieved in full for this step:\n" + "\n".join(blocks) if blocks else ""
        return RecallResult(keys, values, selected, used, scores, budget.total_chars)
