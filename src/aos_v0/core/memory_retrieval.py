"""Key-only memory retrieval keeps context bounded while retaining discoverability."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from aos_v0.core.shared_memory import SharedMemoryBank

_STOPWORDS = {"the", "and", "for", "from", "with", "this", "that", "into", "are", "was", "were", "use", "all"}


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", text.lower()) if len(token) >= 3 and token not in _STOPWORDS}


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
    def selected_entries(self):
        return self.selected

    @property
    def injected_text(self) -> str:
        return self.render()

    @property
    def total_chars_used(self) -> int:
        return self.chars_used

    def render(self) -> str:
        if not self.keys_block:
            return ""
        text = self.keys_block
        if self.values_block:
            text += "\n" + self.values_block
        rendered = text + "\n---\n\n"
        if self.limit and len(rendered) > self.limit:
            return rendered[:max(0, self.limit - 1)].rstrip() + "…"
        return rendered


class MemoryRetriever:
    def __init__(self, budget: ContextBudget | None = None, min_relevance: float | None = None) -> None:
        self.budget = budget or ContextBudget()
        if min_relevance is not None:
            self.budget.min_relevance = min_relevance

    @staticmethod
    def fit(text: str, limit: int) -> tuple[str, int]:
        if len(text) <= limit:
            return text, 0
        cut = text[:max(0, limit)]
        boundary = cut.rfind(" ", max(0, limit - 100))
        if boundary > 0:
            cut = cut[:boundary]
        return cut.rstrip() + "…", len(text) - len(cut.rstrip())

    def select(self, node, bank: SharedMemoryBank, ancestors: set[str] | None = None,
               budget: ContextBudget | None = None) -> RecallResult:
        if isinstance(ancestors, ContextBudget):
            budget, ancestors = ancestors, None
        budget = budget or self.budget
        entries = bank.get_all_entries()
        if not entries:
            return RecallResult("", "", [], 0, {}, budget.total_chars)
        query = _tokens(f"{node.description} {node.capability} {(node.input or '')[:500]}")
        document_tokens = [_tokens(entry.key) for entry in entries]
        key_lines = ["SHARED MEMORY -- results already produced by earlier steps in this workflow.", "Available (summary only):"]
        key_lines.extend(f"  [{entry.id}] {entry.key}" for entry in entries)
        suffix_chars = len("\n---\n\n")
        keys, _ = self.fit("\n".join(key_lines), max(0, budget.total_chars - suffix_chars))
        df: dict[str, int] = {}
        for tokens in document_tokens:
            for token in tokens:
                df[token] = df.get(token, 0) + 1
        scored: list[tuple[float, object]] = []
        for entry, key_tokens in zip(entries, document_tokens):
            if entry.node_id == node.id or (entry.value and entry.value[:200] in (node.input or "")):
                continue
            shared = query & key_tokens
            denominator = sum(math.log(1 + len(entries) / (1 + df.get(token, 0))) for token in query) or 1
            score = min(1.0, sum(math.log(1 + len(entries) / (1 + df.get(token, 0))) for token in shared) / denominator)
            if ancestors and entry.node_id in ancestors:
                score += 0.15
            if entry.capability != node.capability:
                score += 0.10
            score = min(1.0, score)
            if score >= budget.min_relevance:
                scored.append((score, entry))
        scored.sort(key=lambda item: (-item[0], item[1].id))

        selected: list[str] = []
        values: list[str] = []
        scores: dict[str, float] = {}
        used = 0
        value_budget = max(0, budget.total_chars - len(keys) - suffix_chars)
        for score, entry in scored[:budget.max_entries]:
            remaining = value_budget - used
            if remaining <= 0:
                break
            value, _ = self.fit(entry.value, min(budget.per_entry_chars, remaining))
            if not value:
                continue
            selected.append(entry.id)
            scores[entry.id] = score
            bank.get(entry.id, recalled_by=node.id)
            values.append(f"  [{entry.id}] {entry.key}\n  {value}")
            used += len(value)
        values_block = "Retrieved in full for this step:\n" + "\n".join(values) if values else ""
        return RecallResult(keys, values_block, selected, used, scores, budget.total_chars)
