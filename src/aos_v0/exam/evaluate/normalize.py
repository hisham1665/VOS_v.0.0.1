"""Text normalization for the Phase-6 semantic evaluator.

Level-1 groundwork of the plan's evaluation pipeline:

  * stable tokenization (case/punctuation folding),
  * spelling-tolerant matching via a bounded Levenshtein distance,
  * a canonical key form that maps a concept phrase ("hold and wait") onto the
    underscore form a rubric criterion uses ("hold_and_wait"), and
  * a small curated paraphrase lexicon the local semantic engine uses to bridge
    "connection-oriented" -> "establishes a connection" (Level-2 evidence)
    without a model transport.

Normalization never rewrites evidence: keys are derived strictly from the
original answer text for matching purposes, and every accepted variation is
recorded on the resulting :class:`ConceptMatch`.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

# Stopwords that never count as significant evidence in a concept phrase.
STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with",
    "is", "are", "was", "were", "be", "been", "by", "at", "from", "as", "it",
    "its", "this", "that", "these", "those", "than", "then", "but", "so",
    "if", "no", "not", "into", "over", "up", "down", "after", "before",
}

#: Negation markers that, adjacent to a *student's* matched span, flip the
#: concept to CONTRADICTED. Deliberately absent from the phrase "no preemption"
#: handling -- a concept whose own phrase starts with a negator is matched by
#: its surface term, not treated as denied by the student.
NEGATION_MARKERS: Set[str] = {
    "not", "never", "doesnt", "dont", "isnt", "arent", "wont", "cannot",
    "cant", "no", "without", "rather than", "instead of",
}

#: A single-token negation marker, matched exactly (normalized).
NEGATION_TOKENS: Set[str] = {
    "not", "never", "no", "without", "cannot", "cant", "doesnt", "dont",
    "isnt", "arent", "wont",
}

#: Small curated paraphrase lexicon for the plan's flagship semantic cases.
#: Each key is a canonical term; each value is a set of surface forms that a
#: LocalSemanticEngine treats as *the same idea* (Level 2). This is deliberate
#: MVP supporting evidence only -- a general paraphraser is the declared
#: semantic transport (gap G3), which the engine plugs in when one exists.
PARAPHRASE_LEXICON: Dict[str, List[str]] = {
    "connection-oriented": [
        "connection-oriented", "connects endpoints", "establishes a connection",
        "creates a connection", "connection between endpoints", "connection setup",
        "connection established", "end to end connection", "maintains a connection",
    ],
    "reliable": [
        "reliable", "reliable communication", "reliable delivery", "delivered reliably",
        "ensures delivery", "guarantees delivery", "data is delivered reliably",
        "delivery is guaranteed", "no packet loss", "packets delivered in order",
    ],
    "reliable communication": [
        "reliable communication", "reliable delivery", "delivered reliably",
        "ensures delivery", "guarantees delivery", "no packet loss",
    ],
    "connectionless": [
        "connectionless", "no connection setup", "without a connection",
        "sends datagrams", "best effort", "each packet independently routed",
    ],
    "no congestion control": [
        "no congestion control", "does not control congestion", "no congestion avoidance",
        "best effort", "no rate control", "does not slow down on congestion",
    ],
    "statistical multiplexing": [
        "statistical multiplexing", "shares links on demand", "links shared on demand",
        "on demand sharing", "bandwidth shared on demand",
    ],
    "store and forward": [
        "store and forward", "buffer the packet", "buffers the packet",
        "forward only after the entire frame is received", "entire frame received",
        "holds the entire frame before forwarding", "buffers the entire frame",
        "buffer the entire frame", "entire frame is buffered first",
    ],
    "mutual exclusion": [
        "mutual exclusion", "mutual exclusion of resources",
        "only one process at a time", "one process at a time",
        "only one process can use a resource", "only one process can use",
        "one process can use", "exclusive access to a resource",
        "resource used by only one process",
    ],
    "hold and wait": [
        "hold and wait", "holds a resource while waiting for another",
        "holding a resource while requesting another", "holds one and waits for another",
        "process holds a resource and waits for another",
    ],
    "circular wait": [
        "circular wait", "circular waiting", "wait for cycle", "cycle of waiting",
        "each process waits for a resource held by another",
        "processes wait for each other in a cycle", "waits for the next in a cycle",
        "waiting for the next one in a cycle", "waiting for the next",
    ],
}


def normalize_text(text: str, *, lower: bool = True) -> str:
    """Fold a text to a stable matching key: lower, punctuation-free, collapsed.

    Non-alphanumeric characters become spaces so "store-and-forward",
    "store and forward" and "store_and_forward" all fold to the same key.
    """
    if not text:
        return ""
    if lower:
        text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> List[str]:
    """Normalized word tokens (stopwords retained)."""
    return normalize_text(text).split()


def significant_tokens(text: str) -> List[str]:
    """Word tokens with stopwords removed -- what counts as evidence."""
    return [tok for tok in tokens(text) if tok not in STOPWORDS]


def normalize_key(phrase: str) -> str:
    """Canonical key: lower, alphanumerics joined by '_'.

    Maps both a concept phrase ("mutual exclusion", "hold and wait") and a
    rubric criterion id ("mutual_exclusion", "hold_and_wait") onto the same
    underscore key so they can be matched.
    """
    return "_".join(normalize_text(phrase).split())


def edit_distance(a: str, b: str, bound: Optional[int] = None) -> int:
    """Levenshtein distance between two normalized tokens.

    ``bound`` enables the early-exit optimisation used by the matcher; the
    distance is capped at ``bound + 1`` once it is known to exceed the bound.
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if bound is not None and abs(la - lb) > bound:
        return bound + 1
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        curr = [i] + [0] * lb
        row_min = i
        bj = b[0]
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == bj else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
            if curr[j] < row_min:
                row_min = curr[j]
            bj = b[j] if j < lb else bj
        prev = curr
        if bound is not None and row_min > bound:
            return bound + 1
    return prev[lb]


#: Morphological variants the Level-1 matcher accepts for a concept token.
#: Deliberately small and curated (MVP supporting evidence only): a general
#: lemmatizer is the declared normalisation transport (gap G3), not here.
WORD_VARIANTS: Dict[str, Set[str]] = {
    "preemption": {"preemption", "preempt", "preempted", "preempting", "preemptive"},
    "connection": {"connection", "connected", "connecting", "connects", "connectivity"},
    "reliability": {"reliability", "reliable", "reliably", "reliableness"},
    "exclusion": {"exclusion", "exclusive", "exclusively", "exclusivity"},
    "multiplexing": {"multiplexing", "multiplexed", "multiplex"},
    "encapsulation": {"encapsulation", "encapsulate", "encapsulated", "encapsulating"},
    "datagram": {"datagram", "datagrams"},
    "congestion": {"congestion", "congested", "congesting"},
    "diffraction": {"diffraction", "diffracted", "diffracting"},
}


def tokens_close(a: str, b: str, tolerance: int, min_prefix: int = 3) -> bool:
    """True when two tokens match exactly or within ``tolerance`` edits.

    Safety rails against concept-vs-word confusion: numbers match exactly only
    (a digit typo is a different value, not a spelling slip), and the tokens
    must share a common prefix so "contention" can never satisfy "connection".
    Curated morphological variants ("preempted" ~ "preemption") are accepted
    explicitly via :data:`WORD_VARIANTS`.
    """
    if a == b:
        return True
    if not a or not b:
        return False
    if a.isdigit() or b.isdigit():
        return False
    if a in WORD_VARIANTS.get(b, ()) or b in WORD_VARIANTS.get(a, ()):
        return True
    common = min(len(a), len(b), min_prefix)
    if a[:common] != b[:common]:
        return False
    eff_tolerance = tolerance
    # Long near-identical words tolerate a morphological variant ("preempted"
    # vs "preemption") so spelling tolerance never has to decide a real
    # inflection difference.
    if min(len(a), len(b)) >= 8 and a[:6] == b[:6]:
        eff_tolerance += 1
    return edit_distance(a, b, bound=eff_tolerance) <= eff_tolerance


def sentence_split(text: str) -> List[str]:
    """Split an answer into sentences (for sentence-level semantic evidence)."""
    parts = re.split(r"[.!?\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def meaningful_word_count(text: str) -> int:
    """Number of stopword-filtered tokens -- the explanation-length heuristic."""
    return len(significant_tokens(text))


def lexicon_forms(term: str) -> List[str]:
    """All accepted surface forms for ``term`` (term itself plus paraphrases)."""
    key = normalize_key(term)
    direct = {term}
    for canonical, forms in PARAPHRASE_LEXICON.items():
        if normalize_key(canonical) == key:
            direct.update(forms)
    return sorted(direct)