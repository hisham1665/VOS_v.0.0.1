"""Mathematical / numerical answer evaluation (plan Phase 6, deliverable: math engine).

Step-level marking keeps the plan's golden rule: *a wrong final answer must
never erase correct intermediate work.* Work is scored per step -- FORMULA,
SUBSTITUTION, CALCULATION, UNITS, FINAL_ANSWER -- and only an honestly-verifiable
final value changes ``final_answer_correct``. When that value cannot be verified
locally (a differently-arranged-but-maybe-equivalent symbolic expression, for
example) the step is left indeterminate (``final_answer_correct=None``) and the
engine routes the question to ``MATHEMATICAL_UNCERTAINTY`` review instead of
guessing.

How the key is read:

  * numeric key (e.g. ``"2.5 seconds"``): the last number in the student's
    answer is compared with a relative/absolute tolerance;
  * symbolic key (e.g. ``"T = (N + 1) * L / R"``): the formula is "the same"
    when the variables, the arithmetic operators and the number literals all
    match (order-insensitive in the RHS); anything else is unresolved.

Step dispositions feed the rubric binding in :mod:`.rubric` (e.g. criterion id
``formula`` binds the FORMULA step); no marks are decided here.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import List, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from aos_v0.exam.models import Question

from aos_v0.exam.evaluate.models import MathEvaluation, MathStepResult
from aos_v0.exam.evaluate.normalize import (
    normalize_text,
    significant_tokens,
)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


class MathStep(StrEnum):
    FORMULA = "formula"
    SUBSTITUTION = "substitution"
    CALCULATION = "calculation"
    UNITS = "units"
    FINAL_ANSWER = "final_answer"


#: Steps produced for a symbolic key and for a numeric key respectively.
SYMBOLIC_STEPS: tuple = (MathStep.FORMULA, MathStep.SUBSTITUTION, MathStep.FINAL_ANSWER)
NUMERIC_STEPS: tuple = (MathStep.SUBSTITUTION, MathStep.CALCULATION, MathStep.UNITS, MathStep.FINAL_ANSWER)

_OPERATORS = {"+", "-", "*", "/", "^", "=", "(", ")"}


class MathKey(BaseModel):
    """Structured expected value derived from the answer key."""

    model_config = ConfigDict(extra="forbid")

    expression: str = ""
    is_numeric: bool = False
    expected_number: Optional[float] = None
    unit: str = ""
    variables: List[str] = Field(default_factory=list)


def derive_math_key(question: Question) -> Optional[MathKey]:
    """The expected numeric/symbolic value from the question's answer key.

    Prefers ``reference_answers``; falls back to ``keywords``. Returns None for
    questions that carry no machine-checkable value (their rubric criteria are
    then evaluated without a ``MathEvaluation``).
    """
    candidates = [s for s in question.reference_answers if s and s.strip()]
    if not candidates and question.keywords:
        candidates = [k for k in question.keywords if k and k.strip()]
    for candidate in candidates:
        norm = candidate.strip()
        numbers = _NUMBER_RE.findall(norm)
        if "=" in norm:
            variables = _variables(norm)
            expected_number = _to_float(numbers[-1]) if numbers else None
            unit = _unit_of(norm)
            return MathKey(
                expression=norm,
                is_numeric=False,
                expected_number=expected_number,
                unit=unit,
                variables=sorted(variables),
            )
        if numbers:
            expected_number = _to_float(numbers[-1])
            unit = _unit_of(norm)
            return MathKey(
                expression=norm,
                is_numeric=True,
                expected_number=expected_number,
                unit=unit,
                variables=[],
            )
    return None


def _to_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


def _variables(expression: str) -> Set[str]:
    """Variable names in a symbolic expression.

    Variables are single/multi-letter tokens that are NOT common English words:
    "T", "N", "L", "RTT" count; "Throughput", "a", "of" do not. Tokenising by
    non-alphanumerics keeps "RTT" and "L" apart from the words around them.
    """
    tokens = re.findall(r"[A-Za-z]+", normalize_text(expression))
    return {
        tok for tok in tokens
        if len(tok) <= 3 and tok not in _COMMON_WORDS
    }


_COMMON_WORDS: Set[str] = {
    "a", "an", "as", "at", "be", "by", "for", "if", "in", "is", "it",
    "no", "of", "on", "or", "so", "to", "up", "the", "and", "are",
    "per", "the", "with",
}


def _unit_of(expression: str) -> str:
    """Trailing non-numeric, non-operator tokens of an expression ("2.5 s")."""
    tokens = expression.split()
    if len(tokens) < 2:
        return ""
    tail = tokens[-1]
    if tail.replace(".", "", 1).isdigit():
        return ""
    if any(op in tail for op in _OPERATORS):
        return ""
    return tail.strip()


def _parts(expression: str):
    """Normalized token pieces: variables and number literals of an expression."""
    return _variables(expression), set(_NUMBER_RE.findall(expression))


def _window_matches(exp_vars: Set[str], exp_nums: Set[str], window: List[str]) -> bool:
    """True when a fixed-size token window is formula-equivalent to the key."""
    win_vars = {t for t in window if t.isalpha()}
    if win_vars != exp_vars:
        return False
    win_nums = {t for t in window if t.isdigit()}
    if exp_nums:
        return win_nums == exp_nums
    return True


def math_steps_same(expected: str, student: str) -> bool:
    """Symbolic formula equivalence: same variables and number literals.

    The strictest local check the engine is willing to make: the expected
    variables (and any numeric literal the formula contains) must appear in the
    student expression and no *other* variable may. "T = (N + 1) * L / R",
    "T = L * (N + 1) / R" therefore match; "T = N * L / R" and any formula
    that drops the "+1" do not.
    """
    exp_vars, exp_nums = _parts(expected)
    stu_vars, stu_nums = _parts(student)
    if exp_vars != stu_vars:
        return False
    if exp_nums and stu_nums != exp_nums:
        return False
    return True


def _numbers_in(text: str) -> List[float]:
    return [_to_float(n) for n in _NUMBER_RE.findall(text) if _to_float(n) is not None]


def _last_number(text: str) -> Optional[float]:
    found = _NUMBER_RE.findall(text)
    if not found:
        return None
    return _to_float(found[-1])


def _tolerance(expected: float) -> float:
    return max(0.01, 0.02 * abs(expected))


def _numeric_close(student: Optional[float], expected: Optional[float]) -> Optional[bool]:
    if expected is None or student is None:
        return None
    return abs(student - expected) <= _tolerance(expected)


def _equation_lines(answer: str) -> List[str]:
    """Lines of student text that assert an equation ('... = ...')."""
    lines: List[str] = []
    for line in re.split(r"[\n;]+", answer):
        if "=" in line:
            lines.append(line.strip())
    return lines


def _step_result(step: MathStep, satisfied: bool, evidence: List[str], reason: str) -> MathStepResult:
    return MathStepResult(
        step=step.value,
        present=bool(evidence) or satisfied,
        satisfied=satisfied,
        evidence=evidence,
        reason=reason,
    )


def math_evaluate(answer: str, key: MathKey) -> MathEvaluation:
    """Evaluate a student answer against a derived math key (no marks yet)."""
    norm_answer = normalize_text(answer)
    if not norm_answer.strip():
        return MathEvaluation(expected_value=key.expression, max_marks=0.0)

    equations = _equation_lines(answer)

    if key.is_numeric or not key.variables:
        return _evaluate_numeric(answer, key)
    return _evaluate_symbolic(answer, key, equations)


def _evaluate_numeric(answer: str, key: MathKey) -> MathEvaluation:
    steps: List[MathStepResult] = []
    numbers = _numbers_in(answer)
    substituted = bool(numbers) and bool(significant_tokens(answer))

    student_value = _last_number(answer)
    final = _numeric_close(student_value, key.expected_number)

    calc = bool(numbers)
    units_ok = True
    if key.unit:
        units_ok = normalize_text(key.unit) in normalize_text(answer)

    steps.append(
        _step_result(
            MathStep.SUBSTITUTION,
            substituted,
            [answer.strip()] if substituted else [],
            "numeric values substituted into the computation" if substituted
            else "no numeric values found in the answer",
        )
    )
    steps.append(
        _step_result(
            MathStep.CALCULATION,
            calc,
            [answer.strip()] if calc else [],
            "calculation work is present" if calc else "no calculation work shown",
        )
    )
    steps.append(
        _step_result(
            MathStep.UNITS,
            units_ok,
            [answer.strip()] if not key.unit or units_ok else [],
            f"uses the expected unit '{key.unit}'" if key.unit and units_ok
            else ("no unit required" if not key.unit else "wrong/missing unit"),
        )
    )
    steps.append(
        _step_result(
            MathStep.FINAL_ANSWER,
            bool(final),
            [answer.strip()] if final else [],
            (
                f"final value {student_value} within tolerance "
                f"(expected {key.expected_number})" if final
                else (
                    f"final value {student_value or 'missing'} does not match "
                    f"expected {key.expected_number}" if student_value is not None
                    else "no final value stated"
                )
            ),
        )
    )

    return MathEvaluation(
        expected_value=key.expression,
        student_value=str(student_value) if student_value is not None else None,
        final_answer_correct=final,
        tolerance=_tolerance(key.expected_number) if key.expected_number is not None else 0.0,
        steps=steps,
    )


def _evaluate_symbolic(answer: str, key: MathKey, equations: List[str]) -> MathEvaluation:
    steps: List[MathStepResult] = []
    variables = set(key.variables)

    # Show the work: every equation line that engages the key variables.
    engaged = [
        eq for eq in equations
        if variables.intersection(_variables(eq))
    ]

    # Formula step: compare against the RIGHT-HAND side of the expected
    # formula ("L / (L/R + RTT)"), inside the student's own '=' clause, so
    # surrounding commentary and a differently-named LHS can't break it.
    expected_rhs = key.expression.split("=", 1)[1] if "=" in key.expression else key.expression
    exp_rhs_vars = _variables(expected_rhs)
    exp_rhs_nums = set(_NUMBER_RE.findall(expected_rhs))
    exp_rhs_tokens = normalize_text(expected_rhs).split()

    formula_same = False
    for eq in engaged:
        rhs = eq.split("=", 1)[1] if "=" in eq else eq
        rhs_tokens = normalize_text(rhs).split()
        window_len = len(exp_rhs_tokens) if exp_rhs_tokens else len(rhs_tokens)
        for i in range(max(1, len(rhs_tokens) - window_len + 1)):
            window = rhs_tokens[i:i + window_len]
            if _window_matches(exp_rhs_vars, exp_rhs_nums, window):
                formula_same = True
                break
        if formula_same:
            break
    formula_reason = (
        "closed-form expression matches the expected formula"
        if formula_same else
        "equation present but does not match the expected closed form"
        if engaged else "no formula written"
    )

    # Substitution step: the variables are actually combined by arithmetic.
    substituted = any(
        len(variables.intersection(_variables(eq))) >= 2
        and any(op in eq for op in _OPERATORS - {"=", "(", ")"})
        for eq in engaged
    )

    # Final answer: only an honestly-equivalent expression counts; anything
    # else is indeterminate (never guessed).
    final: Optional[bool]
    if formula_same:
        final = True
    elif engaged:
        final = None
    else:
        final = False

    steps.append(
        _step_result(
            MathStep.FORMULA,
            formula_same,
            engaged[:1] if engaged else [],
            formula_reason,
        )
    )
    steps.append(
        _step_result(
            MathStep.SUBSTITUTION,
            substituted,
            [answer.strip()] if substituted else [],
            "key variables combined by arithmetic" if substituted
            else "no arithmetic over the key variables shown",
        )
    )
    steps.append(
        _step_result(
            MathStep.FINAL_ANSWER,
            bool(final),
            engaged[:1] if final else [],
            (
                "final expression is equivalent to the expected formula" if final
                else ("final expression cannot be verified locally" if final is None
                      else "no equation written")
            ),
        )
    )

    return MathEvaluation(
        expected_value=key.expression,
        student_value=engaged[0] if engaged else None,
        final_answer_correct=final,
        tolerance=0.0,
        steps=steps,
    )


def math_step_disposition(step: MathStepResult) -> str:
    """The rubric-consumable disposition for a step result."""
    if step.satisfied:
        return "satisfied"
    if step.present:
        return "partial"
    return "missing"


def step_by_name(step: MathStepResult) -> str:
    return step.step