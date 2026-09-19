"""Exam configuration API (implementation plan Phase 2).

Lets an examiner define an entire examination -- metadata, questions, answer
keys, rubrics, marking rules, negative marking and partial credit -- from a
plain JSON/YAML file, without touching source code.

The answer key defines *what knowledge is expected*, not the exact sentence the
student must write; that contract is enforced by collecting semantic issues
beyond the structural schema (see :func:`collect_issues`).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import ValidationError

from .models import (
    AnswerKey,
    ConceptRelationship,
    ExamConfiguration,
    PartialCreditRule,
    Question,
    Rubric,
    RubricCriterion,
)

Severity = Literal["error", "warning", "advisory"]

_JSON_SUFFIXES = (".json",)
_YAML_SUFFIXES = (".yaml", ".yml")


class ConfigLoadError(Exception):
    """Raised when an exam configuration file cannot be parsed or validated."""


@dataclass(frozen=True)
class ConfigIssue:
    severity: Severity
    path: str
    message: str

    def render(self) -> str:
        return f"[{self.severity:<7}] {self.path}: {self.message}"


def load_exam_configuration(path: str | Path) -> ExamConfiguration:
    """Load and validate an exam configuration from JSON or YAML."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ConfigLoadError(f"config file not found: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix in _JSON_SUFFIXES:
        raw = _load_text(file_path)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigLoadError(
                f"invalid JSON in {file_path}: {exc.msg} (line {exc.lineno})"
            ) from exc
    elif suffix in _YAML_SUFFIXES:
        data = _load_yaml(file_path)
    else:
        raise ConfigLoadError(
            f"unsupported config extension '{suffix}'; use "
            f"{', '.join(_JSON_SUFFIXES + _YAML_SUFFIXES)}"
        )

    if not isinstance(data, dict):
        raise ConfigLoadError("exam configuration must be a JSON/YAML object")

    try:
        return ExamConfiguration.model_validate(data)
    except ValidationError as exc:
        raise ConfigLoadError("invalid exam configuration:\n" + str(exc)) from exc


def dump_exam_configuration(
    config: ExamConfiguration, path: str | Path, *, indent: int = 2
) -> None:
    """Serialize an exam configuration to JSON (or YAML by file extension)."""
    file_path = Path(path)
    data = config.model_dump(mode="json")
    suffix = file_path.suffix.lower()

    if suffix in _JSON_SUFFIXES:
        payload = json.dumps(data, indent=indent, ensure_ascii=False)
    elif suffix in _YAML_SUFFIXES:
        import yaml  # type: ignore[import-not-found]

        payload = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    else:
        raise ConfigLoadError(
            f"unsupported config extension '{suffix}'; use "
            f"{', '.join(_JSON_SUFFIXES + _YAML_SUFFIXES)}"
        )

    file_path.write_text(payload + ("\n" if payload and not payload.endswith("\n") else ""), encoding="utf-8")


def collect_issues(config: ExamConfiguration) -> List[ConfigIssue]:
    """Run semantic checks that the schema alone cannot express.

    Returns a list of issues ordered by severity (errors first). Checks:

    * rubric totals vs question max marks,
    * duplicate rubric criteria,
    * partial-credit rules that reference unknown criteria,
    * concept relationships that reference unknown concepts,
    * empty / under-specified answer keys,
    * negative marking exceeding question marks,
    * partial credit flags that mute configured rules,
    * an overall exam total-marks sanity figure.
    """
    issues: List[ConfigIssue] = []
    for q in config.questions:
        _question_issues(q, issues)

    total = config.total_marks
    if total <= 0:
        issues.append(ConfigIssue("error", "(exam)", "total marks must be > 0"))
    elif config.duration_minutes and config.marking_rules.max_marks_floor > total:
        issues.append(
            ConfigIssue(
                "warning",
                "(exam)",
                "marking_rules.max_marks_floor exceeds the total available marks",
            )
        )
    return sorted(issues, key=lambda i: {"error": 0, "warning": 1, "advisory": 2}[i.severity])


def _question_issues(q: Question, issues: List[ConfigIssue]) -> None:
    path = f"questions.{q.question_id}"
    ak = q.answer_key

    if q.max_marks <= 0:
        issues.append(ConfigIssue("error", f"{path}.max_marks", "must be > 0"))

    expected = set(ak.expected_concepts)
    accepted = set(ak.accepted_alternatives)
    known = expected | accepted

    for rel in ak.concept_relationships:
        rel_path = f"{path}.answer_key.concept_relationships"
        if rel.source not in known:
            issues.append(
                ConfigIssue(
                    "error",
                    f"{rel_path}.source",
                    f"relationship source '{rel.source}' is not an expected "
                    "concept or accepted alternative",
                )
            )
        if rel.target not in known:
            issues.append(
                ConfigIssue(
                    "error",
                    f"{rel_path}.target",
                    f"relationship target '{rel.target}' is not an expected "
                    "concept or accepted alternative",
                )
            )
        if rel.source == rel.target:
            issues.append(
                ConfigIssue("warning", f"{rel_path}", "self-referencing relationship")
            )
        if rel.relationship.value == "conflicts" and rel.weight > 0.0:
            issues.append(
                ConfigIssue(
                    "advisory",
                    f"{rel_path}",
                    "'conflicts' relationships carry a weight; use weight=0 to "
                    "flag contradiction without awarding marks",
                )
            )

    if q.rubric is not None:
        _rubric_issues(q, issues)
    else:
        if not (ak.reference_answers or ak.expected_concepts
                or ak.accepted_alternatives or ak.keywords):
            issues.append(
                ConfigIssue(
                    "warning",
                    path,
                    "question has neither a rubric nor any answer-key content",
                )
            )

    # Partial-credit rules must target real rubric criteria.
    if q.partial_credit_rules:
        if q.rubric is None:
            issues.append(
                ConfigIssue(
                    "error",
                    f"{path}.partial_credit_rules",
                    "partial-credit rules require a rubric",
                )
            )
        else:
            criteria = q.rubric.criterion_map()
            for rule in q.partial_credit_rules:
                if rule.criterion not in criteria:
                    issues.append(
                        ConfigIssue(
                            "error",
                            f"{path}.partial_credit_rules.{rule.criterion}",
                            f"criterion '{rule.criterion}' is not in the rubric",
                        )
                    )
        if not q.partial_credit:
            issues.append(
                ConfigIssue(
                    "warning",
                    f"{path}.partial_credit_rules",
                    "partial_credit=False mutes all partial-credit rules",
                )
            )

    if q.negative_marks > q.max_marks:
        issues.append(
            ConfigIssue(
                "warning",
                f"{path}.negative_marks",
                f"negative marks {q.negative_marks} exceed the question max "
                f"marks {q.max_marks}",
            )
        )

    if not q.partial_credit and q.rubric is not None and q.special_rules is None:
        issues.append(
            ConfigIssue(
                "advisory",
                path,
                "partial_credit=False with a rubric treats every criterion as "
                "all-or-nothing; confirm this is intended",
            )
        )

    if len(ak.reference_answers) > 1 and q.question_type.value in (
        "mcq",
        "true_false",
        "fill_blank",
    ):
        issues.append(
            ConfigIssue(
                "advisory",
                path,
                f"'{q.question_type.value}' questions are usually scored against "
                "a single exact answer; consider accepted_alternatives rather "
                "than multiple reference answers",
            )
        )


def _rubric_issues(q: Question, issues: List[ConfigIssue]) -> None:
    assert q.rubric is not None
    path = f"questions.{q.question_id}"
    criteria = q.rubric.criterion_map()
    if len(criteria) != len(q.rubric.criteria):
        issues.append(
            ConfigIssue(
                "error",
                f"{path}.rubric.criteria",
                "duplicate criterion ids",
            )
        )
    total = q.rubric.total
    if abs(total - q.max_marks) > 1e-9:
        issues.append(
            ConfigIssue(
                "warning",
                f"{path}.rubric",
                f"rubric total ({total}) != question max marks ({q.max_marks})",
            )
        )


def summarize(config: ExamConfiguration) -> Dict[str, object]:
    """A compact, human-readable summary of the configuration."""
    types: Dict[str, int] = {}
    for q in config.questions:
        key = q.question_type.value
        types[key] = types.get(key, 0) + 1
    return {
        "exam_id": config.exam_id,
        "title": config.title,
        "version": config.version,
        "subject": config.subject,
        "questions": config.question_count,
        "total_marks": config.total_marks,
        "question_types": types,
        "duration_minutes": config.duration_minutes,
    }


def _load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigLoadError(f"cannot read config file {path}: {exc}") from exc


def _load_yaml(path: Path) -> object:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ConfigLoadError(
            "PyYAML is required to load .yaml/.yml configs; install it or use JSON"
        ) from exc
    try:
        return yaml.safe_load(_load_text(path))
    except yaml.YAMLError as exc:  # type: ignore[attr-defined]
        raise ConfigLoadError(f"invalid YAML in {path}: {exc}") from exc


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point: validate and summarize an exam configuration file."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print("usage: python3 -m aos_v0.exam.config <config.json|config.yaml>")
        return 0 if args else 2

    path = args[0]
    try:
        config = load_exam_configuration(path)
    except ConfigLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = summarize(config)
    print(f"Exam: {summary['title']} ({summary['exam_id']}, v{summary['version']})")
    print(f"  subject={summary['subject']}  questions={summary['questions']}  "
          f"total_marks={summary['total_marks']}  types={summary['question_types']}")
    if summary["duration_minutes"]:
        print(f"  duration_minutes={summary['duration_minutes']}")

    issues = collect_issues(config)
    if not issues:
        print("validation: OK - no issues")
        return 0

    for issue in issues:
        print(issue.render())
    errors = [i for i in issues if i.severity == "error"]
    print(f"validation: {len(issues)} issue(s), {len(errors)} error(s)", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())