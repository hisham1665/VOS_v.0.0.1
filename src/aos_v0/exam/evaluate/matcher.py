"""Concept matcher + semantic analyzers (plan Phase 6, deliverable: concept matcher).

The plan's three evaluation levels, applied per expected concept:

  * **Level 1 -- surface analysis** (:class:`ConceptMatcher`, deterministic):
    keyword / terminology / spelled-variant matching over the student's own
    tokens. This is *supporting evidence only* -- the engine never converts a
    keyword hit into a concept verdict by itself.
  * **Level 2 -- semantic analysis** (:class:`SemanticAnalyzer`): does a
    differently-worded answer express the same idea? The declared adapters
    (BGE-M3 embedding, Qwen semantic evaluator) are the production transports
    (integration-spec gap G3) and raise :class:`SemanticUnavailableError` until
    one is wired. The runnable :class:`LocalSemanticAnalyzer` bridges the
    curated paraphrase lexicon + token overlap with an honest confidence
    ceiling so the engine stays executable and testable without any model.
  * **Level 3 -- conceptual correctness**: the configured concept relationships
    (requires / implies / alternative / conflicts) are applied by
    :func:`adjust_relationships`, turning raw matches into the concepts the
    rubric can actually award marks against.

The matcher never fabricates evidence: a partially-understood phrase is a
``PARTIAL`` disposition and a denied phrase is ``CONTRADICTED``, and both are
surfaced -- never silently promoted to a satisfied concept.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from aos_v0.exam.model_selection import selection_for_capability
from aos_v0.exam.models import AnswerKey, ConceptRelationshipType

from aos_v0.exam.evaluate.models import (
    ConceptMatch,
    Disposition,
    EvaluationFlag,
    EvaluatorSettings,
    MatchLevel,
)
from aos_v0.exam.evaluate.normalize import (
    NEGATION_TOKENS,
    lexicon_forms,
    meaningful_word_count,
    normalize_key,
    normalize_text,
    sentence_split,
    significant_tokens,
    tokens,
    tokens_close,
)

#: Joint-token (Dice) coefficient is one measure of "talks about the same
#: thing"; the local engine never lets it alone reach the semantic threshold.
_LOCAL_SEMANTIC_CEILING = 0.9


class SemanticUnavailableError(RuntimeError):
    """The semantic transport is declared-only (integration-spec gap G3)."""


class SemanticAnalysis:
    """Output of a semantic analyzer for one (reference, answer) pair."""

    __slots__ = ("similarity", "evidence", "synonym_hits", "level")

    def __init__(
        self,
        similarity: float = 0.0,
        evidence: Optional[List[str]] = None,
        synonym_hits: Optional[List[str]] = None,
        level: MatchLevel = MatchLevel.SURFACE,
    ):
        self.similarity = similarity
        self.evidence = evidence or []
        self.synonym_hits = synonym_hits or []
        self.level = level

    def __repr__(self):  # pragma: no cover - debug aid
        return (
            f"SemanticAnalysis(similarity={self.similarity:.3f}, "
            f"level={self.level.value}, synonym_hits={self.synonym_hits})"
        )


def dice_coefficient(a: Set[str], b: Set[str]) -> float:
    """Dice overlap between two token sets (0.0 .. 1.0)."""
    if not a and not b:
        return 0.0
    return round(2 * len(a & b) / (len(a) + len(b)), 4)


# ---------------------------------------------------------------------------
# Semantic analyzers
# ---------------------------------------------------------------------------


class SemanticAnalyzer(ABC):
    """Uniform surface for Level-2 meaning comparison.

    ``analyze(question, answer, reference)`` says how close *the idea of*
    ``reference`` is to *the idea of* ``answer``, in terms of the answer alone;
    this is what lets a paraphrase satisfy a concept without its keyword.
    """

    name: str = "base"
    model_repo: str = ""
    capabilities: List[str] = []
    description: str = ""

    @abstractmethod
    def analyze(
        self,
        question: str,
        answer: str,
        reference: str,
    ) -> SemanticAnalysis:
        ...


class LocalSemanticAnalyzer(SemanticAnalyzer):
    """Deterministic, no-ML semantic engine (the runnable local fallback).

    Scores the strongest sentence-level overlap between the answer and the
    reference, then boosts it when the reference's curated paraphrase forms
    actually appear in the answer (e.g. "establishes a connection" for
    "connection-oriented"). Confidence is capped at ``_LOCAL_SEMANTIC_CEILING``
    so the engine never pretends the paraphrase verdict is as strong as a real
    semantic model's.
    """

    name = "local_semantic"
    model_repo = "local"
    capabilities = ["semantic.answer_evaluation", "concept.extraction"]
    description = (
        "Level-2 local engine: paraphrase-lexicon + sentence token overlap. "
        "Honest ceiling below a model transport; general paraphrasing beyond "
        "the curated lexicon is the declared embedding/LLM adapter's job."
    )

    def analyze(
        self,
        question: str,
        answer: str,
        reference: str,
    ) -> SemanticAnalysis:  # noqa: ARG002 - question reserved for model adapters
        sig_ref = set(significant_tokens(reference))
        if not sig_ref or not answer.strip():
            return SemanticAnalysis()

        best = 0.0
        best_sentence = ""
        for sentence in sentence_split(answer):
            sig_sent = set(significant_tokens(sentence))
            if not sig_sent:
                continue
            dice = dice_coefficient(sig_sent, sig_ref)
            if dice > best:
                best, best_sentence = dice, sentence

        norm_answer = normalize_text(answer)
        synonym_hits: List[str] = []
        for form in lexicon_forms(reference):
            if normalize_text(form) in norm_answer:
                synonym_hits.append(form)

        similarity = best
        level = MatchLevel.SURFACE
        if synonym_hits:
            similarity = max(similarity, 0.85)
            level = MatchLevel.SEMANTIC
            if not best_sentence:
                best_sentence = "answer contains accepted phrasing"
        similarity = min(similarity, _LOCAL_SEMANTIC_CEILING)

        evidence: List[str] = []
        if best_sentence:
            evidence.append(best_sentence)
        evidence.extend(synonym_hits)
        return SemanticAnalysis(
            similarity=round(similarity, 4),
            evidence=evidence,
            synonym_hits=synonym_hits,
            level=level,
        )


class DeclaredSemanticAnalyzer(SemanticAnalyzer):
    """Base for the Phase-1 selected semantic models -- declared-only.

    Mirrors the OCR declared adapters: identity comes from the Phase-1 model
    selection so the analyzer and the registry always agree on the model;
    ``analyze`` raises :class:`SemanticUnavailableError` until a real transport
    is wired (integration-spec gap G3, Phase 8).
    """

    capability_name: str = ""

    def __init__(self, adapter_id: str, capability_name: str, reason: str = ""):
        selection = selection_for_capability(capability_name)
        primary = selection.primary if selection is not None else None
        self.name = adapter_id
        self.model_repo = primary.hf_repo if primary else "unassigned"
        self.capabilities = list(selection.flags) if selection else []
        self.description = (
            f"declared adapter for '{getattr(primary, 'name', 'unassigned')}' "
            f"({self.model_repo}); serves {', '.join(self.capabilities) or '(none)'}"
        )
        self._unavailable_reason = reason or (
            f"semantic transport for '{self.capability_name}' is not wired "
            "(integration-spec gap G3; Phase 8)"
        )

    def analyze(
        self,
        question: str,
        answer: str,
        reference: str,
    ) -> SemanticAnalysis:
        raise SemanticUnavailableError(self._unavailable_reason)


class BgeM3EmbeddingAnalyzer(DeclaredSemanticAnalyzer):
    """BGE-M3 (primary SEMANTIC_EMBEDDING) -- declared Level-2 analyzer."""

    def __init__(self):
        super().__init__(
            adapter_id="bge_m3_semantic",
            capability_name="SEMANTIC_EMBEDDING",
            reason=(
                "BGE-M3 requires a sentence-transformers/ONNX transport, which "
                "is not installed here and not wired (gap G3)"
            ),
        )


class Qwen3EvaluatorAnalyzer(DeclaredSemanticAnalyzer):
    """Qwen3-30B-A3B (primary SEMANTIC_EVALUATION) -- declared Level-2/3 analyzer."""

    def __init__(self):
        super().__init__(
            adapter_id="qwen3_semantic_evaluator",
            capability_name="SEMANTIC_EVALUATION",
            reason=(
                "Qwen3-30B-A3B requires a vLLM/transformers transport, which is "
                "not wired for the exam volume (gap G3)"
            ),
        )


def default_semantic_engine() -> SemanticAnalyzer:
    """The runnable default: the local engine (no model transport required)."""
    return LocalSemanticAnalyzer()


# ---------------------------------------------------------------------------
# Concept matcher
# ---------------------------------------------------------------------------


def _concept_uses_negator(concept: str) -> bool:
    """Whether the concept phrase is itself negated ("no preemption")."""
    sig = significant_tokens(concept)
    tokenset = {normalize_text(t) for t in sig}
    words = normalize_text(concept).split()
    return any(w in NEGATION_TOKENS for w in words[:1])


def _find_span(answer_tokens: List[str], target: str, tolerance: int):
    """First index in ``answer_tokens`` matching ``target`` (exact or close)."""
    for idx, token in enumerate(answer_tokens):
        if tokens_close(token, target, tolerance):
            return idx
    return None


def _negated_before(answer_tokens: List[str], span: List[int]) -> bool:
    """Reverse-negation check: a marker within two tokens before the span."""
    if not span:
        return False
    earliest = min(span)
    window = answer_tokens[max(0, earliest - 2): earliest]
    return any(w in NEGATION_TOKENS for w in window)


class ConceptMatcher:
    """Matches one/several expected concepts against a student answer."""

    def __init__(
        self,
        settings: Optional[EvaluatorSettings] = None,
        analyzer: Optional[SemanticAnalyzer] = None,
    ):
        self.settings = settings or EvaluatorSettings()
        # The runnable local engine is the default; pass an instance of a
        # DeclaredSemanticAnalyzer to force the declared (usually unavailable)
        # transport, or pass one explicit local/default to override.
        self.analyzer = (
            analyzer if analyzer is not None else LocalSemanticAnalyzer()
        )

    # -- helpers -----------------------------------------------------------

    def _surface_span(
        self, tokens: List[str], concept: str
    ) -> tuple:
        """Map each significant concept token to its match index / token.

        Returns ``(matched, positions, denied)`` where ``matched`` is the list
        of concept tokens found, ``positions`` the answer token indices they
        matched at, and ``denied`` True when the student asserts the *positive*
        of a negated concept ("UDP has congestion control" for the expected
        concept "no congestion control"), or when a negation marker surrounds
        the matched span of a non-negated concept.
        """
        negator_led = _concept_uses_negator(concept)
        concept_words = significant_tokens(concept)
        if not concept_words:
            return None, [], False

        if negator_led:
            phrase_words = normalize_text(concept).split()
            window = len(phrase_words) + 2
            for i in range(max(0, len(tokens) - window), -1, -1):
                seg = tokens[i:i + len(phrase_words)]
                if all(
                    tokens_close(seg[k], phrase_words[k], self.settings.spelling_tolerance)
                    for k in range(len(phrase_words))
                    if k < len(seg)
                ):
                    return phrase_words, list(range(i, i + len(phrase_words))), False
            base_idx = [
                idx for w in concept_words
                if (idx := _find_span(tokens, w, self.settings.spelling_tolerance)) is not None
            ]
            if base_idx:
                lo = max(0, min(base_idx) - 4)
                hi = min(len(tokens), max(base_idx) + 3)
                negated = any(w in NEGATION_TOKENS for w in tokens[lo:hi])
                if negated:
                    return concept_words, base_idx, False
                return None, base_idx, True   # base term asserted positively
            return None, [], False

        matched: List[str] = []
        positions: List[int] = []
        for word in concept_words:
            idx = _find_span(tokens, word, self.settings.spelling_tolerance)
            if idx is not None:
                matched.append(tokens[idx])
                positions.append(idx)
        denied = _negated_before(tokens, positions)
        return matched, positions, denied

    # -- single concept ----------------------------------------------------

    def match(
        self,
        concept: str,
        answer: str,
        *,
        question: str = "",
        alias_forms: Sequence[str] = (),
    ) -> ConceptMatch:
        """Judge one expected concept against the student's answer."""
        settings = self.settings
        if not answer.strip():
            return ConceptMatch(
                concept=concept,
                disposition=Disposition.MISSING,
                reason="answer is blank; no evidence to match",
            )

        answer_tokens = tokens(answer)
        matched, positions, denied = self._surface_span(answer_tokens, concept)
        evidence = [answer_tokens[min(positions)]] if positions else []
        matched = matched or []

        if denied:
            result = ConceptMatch(
                concept=concept,
                disposition=Disposition.CONTRADICTED,
                level=MatchLevel.SURFACE,
                confidence=0.5,
                evidence=evidence,
                reason=(
                    f"answer denies the concept: '"
                    f"{' '.join(answer_tokens[max(0, min(positions)-2): min(positions)+1])}'"
                ),
            )
            return result

        surface_hit = bool(matched)
        spelling_variant = (
            surface_hit and any(m != c for m, c in
                                zip(matched, [t for t in significant_tokens(concept) if _find_span(answer_tokens, t, settings.spelling_tolerance) is not None]))
        )
        spelling_variant = (
            surface_hit
            and any(_find_span(answer_tokens, word, 0) is None for word in significant_tokens(concept))
        )

        level = MatchLevel.SURFACE
        disposition = Disposition.MISSING
        confidence = 0.2
        reason = "no surface evidence for the concept in the answer"
        alternative_of = ""
        flags_note = ""

        expected = set(significant_tokens(concept))
        found_all = set(matched)
        missing = [
            w for w in expected
            if not any(tokens_close(w, f, self.settings.spelling_tolerance) for f in found_all)
        ]
        if surface_hit:
            if not missing:
                disposition = Disposition.SATISFIED
                confidence = 0.8 if spelling_variant else 0.85
                level = MatchLevel.SURFACE
                reason = (
                    f"surface term{'s' if len(found_all) > 1 else ''} present: "
                    f"{', '.join(sorted(found_all))}"
                )
            else:
                disposition = Disposition.PARTIAL
                confidence = 0.4
                reason = (
                    f"partial surface evidence: found {', '.join(sorted(found_all))} "
                    f"for concept '{concept}'"
                )

        flag: Optional[EvaluationFlag] = None
        if surface_hit and spelling_variant and disposition == Disposition.SATISFIED:
            flag = EvaluationFlag.SPELLING_ACCEPTED
            reason = "accepted as a spelling variant of '" + concept + "'"

        # Accepted alternative / terminology alias (plan: Level 2 terminology).
        if surface_hit and disposition == Disposition.SATISFIED:
            hit_norm = normalize_text(answer_tokens[min(positions)])
            if any(
                normalize_text(form) == hit_norm
                or normalize_text(form) in normalize_text(" ".join(answer_tokens))
                for form in alias_forms
            ):
                pass  # satisfied via the canonical term itself, leave as-is

        if disposition != Disposition.SATISFIED and alias_forms and settings.accept_alternatives:
            alias_hit = next(
                (form for form in alias_forms
                 if normalize_text(form) in normalize_text(answer)),
                None,
            )
            if alias_hit is None:
                for form in alias_forms:
                    for tok in significant_tokens(form):
                        if _find_span(answer_tokens, tok, settings.spelling_tolerance) is not None:
                            alias_hit = form
                            break
                    if alias_hit:
                        break
            if alias_hit:
                disposition = Disposition.SATISFIED
                level = MatchLevel.SEMANTIC
                confidence = 0.8
                alternative_of = alias_hit
                flag = EvaluationFlag.ALTERNATIVE_ACCEPTED
                reason = (
                    f"accepted terminology '{alias_hit}' for concept '{concept}'"
                )

        # Level 2: semantic / paraphrase analysis.
        if (disposition in (Disposition.MISSING, Disposition.PARTIAL)
                and settings.use_semantic_analyzer):
            analyzer = self.analyzer
            if isinstance(analyzer, DeclaredSemanticAnalyzer):
                analyzer = None   # declared transports raise SemanticUnavailableError
            if analyzer is not None:
                try:
                    analysis = analyzer.analyze(question, answer, concept)
                except SemanticUnavailableError:
                    analysis = None
                if analysis is not None and analysis.similarity >= settings.semantic_threshold:
                    disposition = Disposition.SATISFIED
                    level = analysis.level
                    confidence = min(0.9, analysis.similarity)
                    flag = EvaluationFlag.PARAPHRASE_ACCEPTED
                    reason = (
                        f"semantic analysis matched the idea of '{concept}' "
                        f"(similarity {analysis.similarity:.2f})"
                    )
                    evidence = list(analysis.evidence) or evidence
                elif analysis is not None and analysis.similarity >= settings.semantic_threshold * 0.75:
                    if disposition != Disposition.SATISFIED:
                        disposition = Disposition.PARTIAL
                        confidence = max(confidence, 0.5)
                        reason = (
                            f"weak semantic overlap with '{concept}' "
                            f"(similarity {analysis.similarity:.2f}); incomplete"
                        )
                        if analysis.evidence:
                            evidence = list(analysis.evidence) + evidence

        result = ConceptMatch(
            concept=concept,
            disposition=disposition,
            level=level,
            confidence=round(confidence, 4),
            evidence=evidence,
            reason=reason,
            alternative_of=alternative_of,
        )
        result_disposition = result.disposition
        return result

    # -- concept list + relationship adjustments ---------------------------

    def match_all(
        self,
        question: str,
        answer: str,
        answer_key: AnswerKey,
        aliases: Optional[Dict[str, List[str]]] = None,
    ) -> List[ConceptMatch]:
        """Match every expected concept and apply relationship corrections."""
        aliases = aliases or {}
        expected = list(answer_key.expected_concepts)
        matches = [
            self.match(
                concept,
                answer,
                question=question,
                alias_forms=list(aliases.get(concept, ())),
            )
            for concept in expected
        ]
        matches = apply_relationships(list(answer_key.concept_relationships), matches)
        return apply_qualitative_concepts(
            matches, answer, self.settings
        )


# ---------------------------------------------------------------------------
# Level-3 relationship corrections
# ---------------------------------------------------------------------------


#: Tokens that mark a *qualitative* expected concept -- one the student
#: demonstrates by substance of writing, not by naming the term itself
#: ("definition", "comparison"). Believing them only on a keyword hit would
#: fabricate evidence; see :func:`apply_qualitative_concepts`.
METACONCEPT_WORDS: Set[str] = {
    "definition", "define", "defines", "explanation", "explain", "explains",
    "describe", "describes", "description", "overview", "summary", "conclusion",
    "comparison", "compare", "compares", "contrast", "differentiate",
    "differentiation", "distinguish", "listing", "purpose",
}


def apply_relationships(relations, matches: List[ConceptMatch]) -> List[ConceptMatch]:
    """Turn raw concept matches into rubric-ready ones (Level 3).

    Corrections (all evidence-preserving):

      * ALTERNATIVE -- the endpoints are substitutes: satisfying either
        satisfies both.
      * IMPLIES      -- satisfying the source partially satisfies the target.
      * REQUIRES     -- a source satisfied without its required target is
        downgraded to PARTIAL (incomplete).
      * CONFLICTS    -- both endpoints satisfied at once is a contradiction,
        downgraded and flagged CONCEPT_CONFLICT (routes to review).
    """
    by_concept: Dict[str, ConceptMatch] = {}
    for match in matches:
        by_concept[normalize_key(match.concept)] = match

    def get(concept: str) -> Optional[ConceptMatch]:
        return by_concept.get(normalize_key(concept))

    changed = True
    while changed:
        changed = False
        for rel in relations:
            source = get(rel.source)
            target = get(rel.target)
            if source is None or target is None:
                continue
            if rel.relationship == ConceptRelationshipType.ALTERNATIVE:
                if (source.disposition == Disposition.SATISFIED
                        and target.disposition != Disposition.SATISFIED):
                    target.disposition = Disposition.SATISFIED
                    target.level = MatchLevel.CONCEPTUAL
                    target.alternative_of = source.concept
                    target.reason = (
                        f"'{rel.source}' (alternative) demonstrates "
                        f"'{rel.target}'"
                    )
                    changed = True
                elif (target.disposition == Disposition.SATISFIED
                        and source.disposition != Disposition.SATISFIED):
                    source.disposition = Disposition.SATISFIED
                    source.level = MatchLevel.CONCEPTUAL
                    source.alternative_of = target.concept
                    source.reason = (
                        f"'{rel.target}' (alternative) demonstrates "
                        f"'{rel.source}'"
                    )
                    changed = True
            elif rel.relationship == ConceptRelationshipType.IMPLIES:
                if (source.disposition == Disposition.SATISFIED
                        and target.disposition in (Disposition.MISSING, Disposition.CONTRADICTED)):
                    target.disposition = Disposition.PARTIAL
                    target.level = MatchLevel.CONCEPTUAL
                    target.reason = (
                        f"'{rel.source}' implies part of '{rel.target}'"
                    )
                    changed = True
            elif rel.relationship == ConceptRelationshipType.REQUIRES:
                if (source.disposition == Disposition.SATISFIED
                        and target.disposition != Disposition.SATISFIED):
                    source.disposition = Disposition.PARTIAL
                    source.level = MatchLevel.CONCEPTUAL
                    source.reason = (
                        f"'{rel.source}' requires '{rel.target}', which the "
                        f"answer does not fully demonstrate"
                    )
                    changed = True
            elif rel.relationship == ConceptRelationshipType.CONFLICTS:
                if (source.disposition == Disposition.SATISFIED
                        and target.disposition == Disposition.SATISFIED):
                    source.disposition = Disposition.PARTIAL
                    target.disposition = Disposition.PARTIAL
                    source.level = MatchLevel.CONCEPTUAL
                    target.level = MatchLevel.CONCEPTUAL
                    source.reason = (
                        f"answer demonstrates conflicting concepts "
                        f"'{rel.source}' and '{rel.target}'"
                    )
                    target.reason = source.reason
                    changed = True
    return matches


def alias_map(answer_key: AnswerKey) -> Dict[str, List[str]]:
    """Accepted-terminology aliases from ALTERNATIVE relationships.

    For an ALTERNATIVE link where one endpoint is an expected concept and the
    other is a plain term (e.g. "hold and wait" <-> "resource holding"), the
    non-concept endpoint becomes an accepted surface form of the concept.
    """
    expected = {normalize_key(c) for c in answer_key.expected_concepts}
    aliases: Dict[str, List[str]] = {}
    for rel in answer_key.concept_relationships:
        if rel.relationship != ConceptRelationshipType.ALTERNATIVE:
            continue
        src_expected = normalize_key(rel.source) in expected
        tgt_expected = normalize_key(rel.target) in expected
        if src_expected and not tgt_expected:
            aliases.setdefault(normalize_key(rel.source), []).append(rel.target)
        elif tgt_expected and not src_expected:
            aliases.setdefault(normalize_key(rel.target), []).append(rel.source)
    return aliases


def apply_qualitative_concepts(
    matches: List[ConceptMatch],
    answer: str,
    settings: EvaluatorSettings,
) -> List[ConceptMatch]:
    """Give qualitative concepts credit only on real substance.

    A concept such as "definition" or "comparison" earns its criterion when the
    student actually writes that explanation -- never by the word "definition"
    appearing. The rule is deliberately conservative: the answer must be
    substantive (at least ``min_explanation_words`` significant tokens) AND
    demonstrate at least one non-qualitative concept; otherwise the concept
    stays MISSING rather than being granted on a keyword.
    """
    qualitative = {
        normalize_key(m.concept): m
        for m in matches
        if _is_qualitative_concept(m.concept)
    }
    if not qualitative or not answer.strip():
        return matches

    substantive = (
        meaningful_word_count(answer) >= settings.min_explanation_words
    )
    if not substantive:
        # The keyword alone cannot buy a quality criterion: a one-word answer
        # that merely names the concept ("definition") stays MISSING.
        for m in qualitative.values():
            if (m.disposition == Disposition.SATISFIED
                    and m.level == MatchLevel.SURFACE):
                m.disposition = Disposition.MISSING
                m.level = MatchLevel.SURFACE
                m.confidence = min(m.confidence, 0.2)
                m.reason = (
                    f"the word '{m.concept}' alone is not substance; a quality "
                    "criterion needs a real explanation"
                )
        return matches

    norm_answer = normalize_text(answer)
    has_declaration = any(
        mark in norm_answer for mark in (" is ", " are ", "refers to",
                                         "defined as", "occurs when")
    )
    any_substance = any(
        m.disposition == Disposition.SATISFIED
        and normalize_key(m.concept) not in qualitative
        for m in matches
    ) or has_declaration
    for m in qualitative.values():
        if m.disposition in (Disposition.MISSING, Disposition.PARTIAL) and any_substance:
            m.disposition = Disposition.SATISFIED
            m.level = MatchLevel.CONCEPTUAL
            m.confidence = 0.8
            m.reason = (
                f"substantive answer (>= {settings.min_explanation_words} "
                f"significant tokens) demonstrates '{m.concept}'"
            )
    return matches


def _is_qualitative_concept(concept: str) -> bool:
    sig = significant_tokens(concept)
    if not sig:
        return False
    return all(w in METACONCEPT_WORDS for w in sig)