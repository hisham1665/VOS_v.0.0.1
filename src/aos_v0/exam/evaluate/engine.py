"""Phase 6 semantic-evaluation orchestrator + CLI (plan Phase 6, deliverable).

``evaluate_question`` turns one structured answer entry into a
:class:`QuestionEvaluation`, and ``evaluate_sheet`` totals one paper's marks
into an :class:`ExamEvaluation`. Everything the earlier phases established is
honoured here:

  * evidence is preserved, never fabricated (concept matches quote the student's
    own spans; ``reasoning`` logs every decision);
  * an unreadable answer (low OCR confidence) is ``UNSCORED`` with a review
    reason -- never an automatic zero;
  * an unverifiable mathematical final value preserves step marks and routes to
    ``MATHEMATICAL_UNCERTAINTY`` review;
  * contradictions surface as ``AMBIGUOUS_ANSWER`` / ``CONCEPT_CONFLICT``;
  * the CLI consumes either the plan's phase-5 JSON surface
    (``{"student", "answers":[{question_id, pages, text, regions}]}``) or a full
    ``StructuredAnswerSheet`` record.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Dict, List, Optional

from aos_v0.exam.models import (
    EvalReviewReason,
    ExamConfiguration,
    Question,
    QuestionType,
    Roster,
)
from aos_v0.exam.structure.models import (
    AnswerSheetEntry,
    MappingIssue,
    StudentIdentity,
    StructuredAnswerSheet,
)

from aos_v0.exam.evaluate.matcher import ConceptMatcher, alias_map
from aos_v0.exam.evaluate.math import derive_math_key, math_evaluate
from aos_v0.exam.evaluate.models import (
    ConceptMatch,
    Disposition,
    EvaluationFlag,
    EvaluationStatus,
    EvaluatorSettings,
    ExamEvaluation,
    FLAG_REVIEW_REASON,
    MathEvaluation,
    QuestionEvaluation,
)
from aos_v0.exam.evaluate.normalize import (
    normalize_key,
    normalize_text,
    significant_tokens,
)
from aos_v0.exam.evaluate.rubric import evaluate_rubric, math_marks_from_criteria

_OPTION_LETTER = re.compile(r"^\s*([a-z])\s*(?:[.)\s-]|$)")


def _leading_letter(text: str) -> str:
    match = _OPTION_LETTER.match(text)
    return match.group(1) if match else ""


def _without_option(text: str) -> str:
    return normalize_text(_OPTION_LETTER.sub("", text, count=1))


def _mcq_content_is_in(norm: str, contents: List[str]) -> bool:
    """Whether the answer carries a reference option's full content.

    A lone option letter ("c") must never satisfy on a substring; the reference
    phrase (its significant tokens) has to be demonstrated as a whole.
    """
    for content in contents:
        if not content:
            continue
        if content in norm:
            return True
        wanted = significant_tokens(content)
        if len(wanted) < 2:
            continue
        present = significant_tokens(norm)
        if all(w in present for w in wanted):
            return True
    return False


def _mean(values: List[float], default: float = 0.85) -> float:
    return round(sum(values) / len(values), 4) if values else default


def round_half_up(value: float) -> float:
    return float(int(value * 2 + 0.5 if value >= 0 else value * 2 - 0.5) / 2)


def _apply_rounding(config: ExamConfiguration, total: float) -> float:
    mode = config.marking_rules.rounding
    if mode == "half_up":
        return round_half_up(total)
    if mode == "truncate":
        return float(int(total * 100) / 100)
    return round(total, 4)


# ---------------------------------------------------------------------------
# Choice-type questions (MCQ / true-false / fill-blank)
# ---------------------------------------------------------------------------


def _evaluate_choice(
    question: Question,
    answer: str,
    matcher: ConceptMatcher,
) -> QuestionEvaluation:
    qtype = question.question_type
    norm = normalize_text(answer)
    reasoning: List[str] = []
    flags: List[EvaluationFlag] = []

    matched = False
    if qtype == QuestionType.MCQ:
        expected_opts = [
            _leading_letter(r) for r in question.reference_answers
            if _leading_letter(r)
        ]
        contents = [_without_option(r) for r in question.reference_answers
                    if r.strip()]
        option = _leading_letter(answer)
        match_content = _mcq_content_is_in(norm, contents)
        option_match = bool(option) and option in expected_opts
        if option_match and not match_content:
            matched = True
            reasoning.append(
                f"student selected option {option!r}, which is one of the "
                f"expected options {sorted(expected_opts)}"
            )
        elif match_content:
            matched = True
            reasoning.append("student answer matches a reference answer by content")
        else:
            reasoning.append("no expected option or reference content matched")

    elif qtype == QuestionType.TRUE_FALSE:
        reference = (question.reference_answers[0] if question.reference_answers
                     else "").strip().lower()
        ref_bool = "false" if "false" in reference else "true"
        words = normalize_text(answer).split()
        explicit = next((w for w in words if w in ("true", "false")), None)
        if explicit is not None:
            matched = explicit == ref_bool
            reasoning.append(
                f"student explicitly answered {explicit!r}; expected {ref_bool!r}"
            )
        else:
            # No explicit boolean: does the answer assert the expected side?
            uri = question.answer_key
            if ref_bool == "false":
                might = [
                    m for m in matcher.match_all(question.text, answer, uri)
                    if m.disposition == Disposition.SATISFIED
                ]
                matched = bool(might)
                reasoning.append(
                    "no explicit true/false; accepted via demonstrated "
                    f"concepts ({len(might)}) on the expected 'false' side"
                )
            else:
                reasoning.append(
                    "no explicit true/false and reference is 'true'; "
                    "no concept evidence was available"
                )

    elif qtype == QuestionType.FILL_BLANK:
        refs = [normalize_text(r) for r in question.reference_answers if r.strip()]
        alts = [normalize_text(a) for a in question.accepted_alternatives if a.strip()]
        matched = any(r and r in norm for r in refs) or any(
            a and a in norm for a in alts
        )
        reasoning.append(
            "blank filled with a reference answer or accepted alternative"
            if matched else "no reference form or accepted alternative present"
        )

    marks = float(question.max_marks) if matched else -question.negative_marks
    if not matched and question.negative_marks > 0:
        flags.append(EvaluationFlag.NEGATIVE_MARKING_APPLIED)
        reasoning.append(
            f"incorrect; negative marking applied ({marks:+g})"
        )

    status = EvaluationStatus.OK
    return QuestionEvaluation(
        question_id=question.question_id,
        max_marks=question.max_marks,
        answered=True,
        status=status,
        marks=marks,
        flags=flags,
        confidence=0.9 if matched else 0.7,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# Concept + rubric questions (incl. math)
# ---------------------------------------------------------------------------


def _concept_and_rubric(
    question: Question,
    answer: str,
    matcher: ConceptMatcher,
    *,
    run_math: bool,
) -> QuestionEvaluation:
    reasoning: List[str] = []

    matches: List[ConceptMatch] = matcher.match_all(
        question.text, answer, question.answer_key, alias_map(question.answer_key)
    )
    reasoning.append(
        "concept matching: " + "; ".join(
            f"{m.concept}={m.disposition.value}"
            for m in matches
        ) or "no expected concepts configured"
    )

    flags: List[EvaluationFlag] = []
    if any(m.disposition.value == "satisfied" and m.level.value == "semantic"
           and not m.alternative_of for m in matches):
        flags.append(EvaluationFlag.PARAPHRASE_ACCEPTED)
    if any(m.alternative_of for m in matches):
        flags.append(EvaluationFlag.ALTERNATIVE_ACCEPTED)
    if any("spelling" in m.reason for m in matches):
        flags.append(EvaluationFlag.SPELLING_ACCEPTED)
    if any(m.disposition.value == "contradicted" for m in matches):
        flags.append(EvaluationFlag.CONCEPT_CONFLICT)

    math_eval: Optional[MathEvaluation] = None
    if run_math:
        key = derive_math_key(question)
        if key is not None:
            math_eval = math_evaluate(answer, key)
            reasoning.append(
                "math evaluation: final answer correct="
                f"{math_eval.final_answer_correct}"
            )

    criteria = evaluate_rubric(question, matches, math_eval, matcher.settings)
    if math_eval is not None:
        step_map = {step.step: step for step in math_eval.steps}
        for criterion in criteria:
            if criterion.bound_concept.startswith("math:"):
                name = criterion.bound_concept[len("math:"):]
                step = step_map.get(name)
                if step is not None:
                    step.marks = criterion.marks
                    step.max_marks = criterion.max_marks
        math_eval.marks, math_eval.max_marks = math_marks_from_criteria(criteria)

    total = sum(c.marks for c in criteria)
    if criteria:
        reasoning.append(
            "rubric: " + "; ".join(
                f"{c.criterion}={c.disposition.value}({c.marks:g}/{c.max_marks:g})"
                for c in criteria
            )
        )
    else:
        # No rubric: award proportionally to fully-demonstrated concepts so a
        # rubric-less long answer is never silently zeroed.
        satisfied = sum(
            1 for m in matches if m.disposition == Disposition.SATISFIED
        )
        total = len(matches)
        total = round(question.max_marks * satisfied / total, 2) if total else 0.0
        reasoning.append(
            f"no rubric configured; awarded {total:g}/{question.max_marks:g} "
            f"from concept coverage ({satisfied}/{total if matches else 0})"
        )

    status = EvaluationStatus.OK
    if math_eval is not None and math_eval.final_answer_correct is None:
        flags.append(EvaluationFlag.LOW_EVALUATION_CONFIDENCE)
    if (math_eval is not None
            and any(s.satisfied for s in math_eval.steps)
            and math_eval.final_answer_correct is False):
        flags.append(EvaluationFlag.STEP_MARKS_PRESERVED)
        reasoning.append(
            "final answer wrong but correct intermediate steps preserved"
        )

    if any(m.disposition.value == "contradicted" for m in matches):
        flags.append(EvaluationFlag.AMBIGUOUS_ANSWER)

    confidence = _mean([m.confidence for m in matches], 0.85)
    if math_eval is not None and math_eval.final_answer_correct is None:
        confidence = min(confidence, 0.6)
    if any(m.disposition.value == "contradicted" for m in matches):
        confidence = min(confidence, 0.5)

    return QuestionEvaluation(
        question_id=question.question_id,
        max_marks=question.max_marks,
        answered=True,
        status=status,
        marks=round(total, 2),
        concepts=matches,
        criteria=criteria,
        math=math_eval,
        flags=flags,
        confidence=confidence,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# Question evaluation
# ---------------------------------------------------------------------------


def _entry_creates_ambiguity(entry: AnswerSheetEntry) -> bool:
    return bool(
        entry.issues
        and any(i in entry.issues for i in (
            MappingIssue.MULTIPLE_ATTEMPTS,
            MappingIssue.AMBIGUOUS_MAPPING,
            MappingIssue.QUESTION_NUMBER_OCR_ERROR,
            MappingIssue.UNKNOWN_QUESTION,
        ))
    )


def evaluate_question(
    question: Question,
    entry: AnswerSheetEntry,
    settings: Optional[EvaluatorSettings] = None,
    matcher: Optional[ConceptMatcher] = None,
) -> QuestionEvaluation:
    """Evaluate one question's answer entry against its key and rubric."""
    settings = settings or EvaluatorSettings()
    matcher = matcher or ConceptMatcher(settings)

    answer = (entry.text or "").strip()
    answered = bool(answer)
    flags: List[EvaluationFlag] = []
    # A confidence of exactly 0 means "not measured": the OCR pipeline never
    # emits zero for a real page, so we treat the absence of a measurement as
    # readable rather than guessing a quality penalty either way.
    confidence = entry.confidence if entry.confidence > 0 else 1.0

    if not answered:
        if confidence >= settings.min_answer_confidence:
            return QuestionEvaluation(
                question_id=question.question_id,
                max_marks=question.max_marks,
                answered=False,
                status=EvaluationStatus.OK,
                marks=0.0,
                flags=[EvaluationFlag.BLANK_ANSWER
                       ] + ([EvaluationFlag.CROSSED_OUT] if entry.crossed_out else []),
                confidence=confidence,
                reasoning=[
                    "answer is blank" + (" (crossed out)" if entry.crossed_out else "")
                ],
            )
        return QuestionEvaluation(
            question_id=question.question_id,
            max_marks=question.max_marks,
            answered=False,
            status=EvaluationStatus.UNSCORED,
            marks=None,
            flags=[EvaluationFlag.LOW_ANSWER_CONFIDENCE],
            review_reasons=[FLAG_REVIEW_REASON[EvaluationFlag.LOW_ANSWER_CONFIDENCE]],
            confidence=confidence,
            reasoning=[
                "no text and OCR confidence below the readability gate; "
                "cannot distinguish a blank from an unreadable answer"
            ],
        )

    if confidence < settings.min_answer_confidence:
        return QuestionEvaluation(
            question_id=question.question_id,
            max_marks=question.max_marks,
            answered=True,
            status=EvaluationStatus.UNSCORED,
            marks=None,
            flags=[EvaluationFlag.LOW_ANSWER_CONFIDENCE],
            review_reasons=[FLAG_REVIEW_REASON[EvaluationFlag.LOW_ANSWER_CONFIDENCE]],
            confidence=confidence,
            reasoning=[
                "answer text present but OCR confidence below the readability "
                "gate; marking withheld rather than guessed"
            ],
        )

    reasoning: List[str] = []
    if _entry_creates_ambiguity(entry):
        flags.append(EvaluationFlag.AMBIGUOUS_ANSWER)
        reasoning.append(
            "structuring flagged an ambiguity on this entry "
            f"({', '.join(sorted(i.value for i in entry.issues))})"
        )

    if question.question_type in (
        QuestionType.MCQ, QuestionType.TRUE_FALSE, QuestionType.FILL_BLANK,
    ):
        evaluation = _evaluate_choice(question, answer, matcher)
    elif question.question_type in (QuestionType.NUMERICAL, QuestionType.MATHEMATICAL):
        evaluation = _concept_and_rubric(question, answer, matcher, run_math=True)
    elif question.question_type == QuestionType.DIAGRAM and not answered:
        evaluation = QuestionEvaluation(
            question_id=question.question_id,
            max_marks=question.max_marks,
            answered=False,
            status=EvaluationStatus.UNSCORED,
            marks=None,
            flags=[EvaluationFlag.AMBIGUOUS_ANSWER],
            confidence=confidence,
            reasoning=[
                "diagram question answered on the image only; the figure is "
                "outside the text contract and cannot be judged here"
            ],
        )
    else:
        evaluation = _concept_and_rubric(question, answer, matcher, run_math=False)

    evaluation.flags.extend(flags)
    evaluation.reasoning = reasoning + evaluation.reasoning

    for flag in evaluation.flags:
        reason = FLAG_REVIEW_REASON.get(flag)
        if reason and reason not in evaluation.review_reasons:
            evaluation.review_reasons.append(reason)

    if evaluation.review_reasons:
        evaluation.status = EvaluationStatus.REVIEW
    elif evaluation.marks is None:
        evaluation.status = EvaluationStatus.UNSCORED
    return evaluation


# ---------------------------------------------------------------------------
# Sheet evaluation
# ---------------------------------------------------------------------------


def evaluate_sheet(
    config: ExamConfiguration,
    sheet: StructuredAnswerSheet,
    settings: Optional[EvaluatorSettings] = None,
    matcher: Optional[ConceptMatcher] = None,
) -> ExamEvaluation:
    """Evaluate a whole structured answer sheet against an exam configuration."""
    settings = settings or EvaluatorSettings()
    matcher = matcher or ConceptMatcher(settings)

    by_question: Dict[str, AnswerSheetEntry] = {
        entry.question_id: entry for entry in sheet.answers
    }
    evaluations = [
        evaluate_question(
            question,
            by_question.get(question.question_id, AnswerSheetEntry(
                question_id=question.question_id,
                confidence=min(settings.min_answer_confidence, 0.99),
            )),
            settings,
            matcher,
        )
        for question in config.questions
    ]

    scored = [qe for qe in evaluations if qe.marks is not None]
    total = sum(qe.marks for qe in scored)
    total = _apply_rounding(config, total)
    total = max(total, settings.negative_marking_floor)

    review_reasons: List[EvalReviewReason] = []
    for qe in evaluations:
        for reason in qe.review_reasons:
            if reason not in review_reasons:
                review_reasons.append(reason)

    flags: List[EvaluationFlag] = []
    for qe in evaluations:
        for flag in qe.flags:
            if flag not in flags:
                flags.append(flag)

    max_marks = sum(q.max_marks for q in config.questions)
    confidence = _mean([qe.confidence for qe in scored], 0.0)

    return ExamEvaluation(
        paper_id=config.exam_id,
        student=sheet.student,
        question_evaluations=evaluations,
        max_marks=round(max_marks, 4),
        total_marks=total,
        confidence=confidence,
        flags=flags,
        review_reasons=review_reasons,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_sheet_json(data: dict) -> StructuredAnswerSheet:
    """Accept the plan's phase-5 surface or a full StructuredAnswerSheet."""
    if isinstance(data, dict) and "student" in data and "answers" in data:
        student_data = data["student"]
        student = StudentIdentity(
            name=student_data.get("name", ""),
            roll_no=student_data.get("roll_no", ""),
            register_no=student_data.get("register_no", ""),
        )
        entries: List[AnswerSheetEntry] = []
        for raw in data["answers"]:
            entries.append(AnswerSheetEntry(
                question_id=raw["question_id"],
                pages=list(raw.get("pages", [])),
                text=raw.get("text", ""),
                confidence=float(raw.get("confidence", 1.0)),
            ))
        return StructuredAnswerSheet(student=student, answers=entries)
    if isinstance(data, dict) and "student" in data:
        return StructuredAnswerSheet.model_validate(data)
    raise ValueError(
        "answer sheet must be the phase-5 JSON surface "
        "{'student': ..., 'answers': [...]} or a StructuredAnswerSheet"
    )


def _print_summary(evaluation: ExamEvaluation) -> None:
    fig = evaluation.student
    print(f"Paper {evaluation.paper_id} -- {fig.name} ({fig.roll_no})")
    print(f"Total {evaluation.total_marks:g} / {evaluation.max_marks:g}"
          f"   confidence {evaluation.confidence:.2f}")
    for qe in evaluation.question_evaluations:
        marks = "unscored" if qe.marks is None else f"{qe.marks:g}"
        print(f"  {qe.question_id:<6} {marks:>8} / {qe.max_marks:g}"
              f"   {qe.status.value}"
              + (f"   [{','.join(r.value for r in qe.review_reasons)}]"
                 if qe.review_reasons else ""))
    if evaluation.unscored_questions:
        print("Unscored:", ", ".join(evaluation.unscored_questions))
    if evaluation.review_reasons:
        print("Review:", ", ".join(r.value for r in evaluation.review_reasons))


def _validate_roster(sheet: StructuredAnswerSheet, roster: Roster) -> None:
    for entry in roster.entries:
        if entry.roll_no == sheet.student.roll_no:
            sheet.student.matched = True
            sheet.student.roster_roll = entry.roll_no
            sheet.student.roster_name = entry.name or ""
            return
    sheet.student.roster_roll = ""
    sheet.student.roster_name = ""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aos_v0.exam.evaluate",
        description="Phase 6 semantic evaluation of a structured answer sheet.",
    )
    parser.add_argument("sheet", help="phase-5 answer-sheet JSON")
    parser.add_argument("--config", required=True, help="exam configuration JSON")
    parser.add_argument("--roster", help="optional roster JSON for identity check")
    parser.add_argument("--json", action="store_true",
                        help="emit the plan-shaped JSON evaluation surface")
    args = parser.parse_args(argv)

    try:
        config = ExamConfiguration.model_validate(json.load(open(args.config)))
        data = json.load(open(args.sheet))
        sheet = parse_sheet_json(data)
        if args.roster:
            roster = Roster.model_validate(json.load(open(args.roster)))
            _validate_roster(sheet, roster)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    evaluation = evaluate_sheet(config, sheet)
    if args.json:
        print(json.dumps(evaluation.to_plan_json(), indent=2))
    else:
        _print_summary(evaluation)
    return 0