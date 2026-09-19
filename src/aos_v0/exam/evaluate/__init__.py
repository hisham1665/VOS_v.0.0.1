"""Phase 6 -- semantic evaluation engine (implementation plan Phase 6).

The engine evaluates a structured answer sheet (plan Phase 5 output) against an
exam configuration (plan Phase 2): it matches the student's own words to the
expected concepts at three levels (surface keywords -> meaning -> concept
correctness), awards marks through the configured rubric with partial credit and
math step-marking, and preserves every piece of evidence behind a mark. Anything
it cannot decide honestly is routed to human review -- an unreadable answer is
never an automatic zero.

Public API:

    evaluate_sheet(config, sheet, settings=None, matcher=None) -> ExamEvaluation
    evaluate_question(question, entry, settings=None, matcher=None) -> QuestionEvaluation

Run the CLI with:

    python -m aos_v0.exam.evaluate sheet.json --config exam.json [--roster r.json] [--json]
"""

from aos_v0.exam.evaluate.engine import (
    evaluate_question,
    evaluate_sheet,
    parse_sheet_json,
    round_half_up,
)
from aos_v0.exam.evaluate.matcher import (
    BgeM3EmbeddingAnalyzer,
    ConceptMatcher,
    DeclaredSemanticAnalyzer,
    LocalSemanticAnalyzer,
    Qwen3EvaluatorAnalyzer,
    SemanticAnalysis,
    SemanticAnalyzer,
    SemanticUnavailableError,
    alias_map,
    apply_relationships,
    default_semantic_engine,
    dice_coefficient,
)
from aos_v0.exam.evaluate.math import (
    MathKey,
    MathStep,
    derive_math_key,
    math_evaluate,
    math_steps_same,
)
from aos_v0.exam.evaluate.models import (
    ConceptMatch,
    CriterionResult,
    Disposition,
    EvaluationError,
    EvaluationFlag,
    EvaluationStatus,
    EvaluatorSettings,
    ExamEvaluation,
    MatchLevel,
    MathEvaluation,
    MathStepResult,
    QuestionEvaluation,
)
from aos_v0.exam.evaluate.normalize import (
    NEGATION_MARKERS,
    PARAPHRASE_LEXICON,
    STOPWORDS,
    edit_distance,
    normalize_key,
    normalize_text,
    significant_tokens,
    tokens,
    tokens_close,
)
from aos_v0.exam.evaluate.rubric import (
    evaluate_rubric,
    math_marks_from_criteria,
    partial_fraction,
    step_for_criterion_key,
)

__all__ = [
    "BgeM3EmbeddingAnalyzer",
    "ConceptMatch",
    "ConceptMatcher",
    "CriterionResult",
    "DeclaredSemanticAnalyzer",
    "Disposition",
    "EvaluationError",
    "EvaluationFlag",
    "EvaluationStatus",
    "EvaluatorSettings",
    "ExamEvaluation",
    "LocalSemanticAnalyzer",
    "MatchLevel",
    "MathEvaluation",
    "MathKey",
    "MathStep",
    "MathStepResult",
    "NEGATION_MARKERS",
    "PARAPHRASE_LEXICON",
    "Qwen3EvaluatorAnalyzer",
    "QuestionEvaluation",
    "STOPWORDS",
    "SemanticAnalysis",
    "SemanticAnalyzer",
    "SemanticUnavailableError",
    "alias_map",
    "apply_relationships",
    "default_semantic_engine",
    "derive_math_key",
    "dice_coefficient",
    "edit_distance",
    "evaluate_question",
    "evaluate_rubric",
    "evaluate_sheet",
    "math_evaluate",
    "math_marks_from_criteria",
    "math_steps_same",
    "normalize_key",
    "normalize_text",
    "parse_sheet_json",
    "partial_fraction",
    "round_half_up",
    "significant_tokens",
    "step_for_criterion_key",
    "tokens",
    "tokens_close",
]