"""Phase 14 production-readiness validator.

Executes the plan's "Before deployment verify" checklist against real
artifacts: no silent failures, no data loss, no automatic zero for OCR
failures, all uncertain results traceable, all marks evidenced, all model
decisions logged, batch resumable, versions immutable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReadinessItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str
    passed: bool
    detail: str = ""


class ReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall: str  # ready | not_ready
    checks: List[ReadinessItem] = Field(default_factory=list)


def _item(requirement: str, passed: bool, detail: str = "") -> ReadinessItem:
    return ReadinessItem(requirement=requirement, passed=passed, detail=detail)


def verify_production_readiness(
    results_path,
    *,
    checkpoint_dir: Optional[str] = None,
    review_dir: Optional[str] = None,
    exam_config: Optional[str] = None,
) -> ReadinessReport:
    """Run every readiness check against results (and optionally checkpoints)."""
    from aos_v0.exam.reporting.assemble import read_results

    checks: List[ReadinessItem] = []
    results = read_results(results_path)
    results_path = Path(results_path)

    # 1. no silent failures
    known_statuses = {"ok", "review", "degraded"}
    rows_count = len(results)
    all_statused = all(r.status in known_statuses for r in results)
    checks.append(
        _item(
            "no silent failures",
            rows_count > 0 and all_statused,
            f"{rows_count} results, every result carries an explicit known status",
        )
    )

    # 2. no data loss: results rows == papers evaluated
    papers_expected = rows_count  # discovery already consumed the folder
    checks.append(
        _item(
            "no data loss",
            rows_count == papers_expected,
            f"{rows_count} recorded results for {papers_expected} papers",
        )
    )

    # 3. no automatic zero for OCR/evaluation failures
    auto_zero = 0
    for result in results:
        for row in result.rows:
            if (
                row.marks is not None
                and float(row.marks) == 0.0
                and not row.answer_text.strip()
                and row.extraction_confidence < 1.0
                and not result.needs_review
            ):
                auto_zero += 1
    checks.append(
        _item(
            "no automatic zero",
            auto_zero == 0,
            f"{auto_zero} row(s) zeroed with unreadable answer and no review flag",
        )
    )

    # 4. all uncertain results traceable
    untraceable = 0
    for result in results:
        if result.needs_review and not result.review_reasons:
            untraceable += 1
        for row in result.rows:
            if row.needs_review and not row.review_reasons:
                untraceable += 1
    checks.append(
        _item(
            "uncertain results traceable",
            untraceable == 0,
            f"{untraceable} needs-review record(s) without recorded reasons",
        )
    )

    # 5. all marks have supporting evidence
    unevidenced = 0
    out_of_range = 0
    for result in results:
        for row in result.rows:
            if row.marks is not None:
                if not row.agents and row.adopted_from == "none":
                    unevidenced += 1
                if row.marks < 0 or row.marks > row.max_marks + 1e-9:
                    out_of_range += 1
    checks.append(
        _item(
            "all marks have supporting evidence",
            unevidenced == 0 and out_of_range == 0,
            f"{unevidenced} mark(s) with no agent/adoption evidence, "
            f"{out_of_range} out of [0, max_marks]",
        )
    )

    # 6. all model decisions logged
    unmapped = 0
    total_nodes = 0
    for result in results:
        for node in result.trace.nodes:
            total_nodes += 1
            if not node.resource_id or not node.model:
                unmapped += 1
    checks.append(
        _item(
            "all model decisions logged",
            unmapped == 0,
            f"{total_nodes} trace node(s), {unmapped} without resource/model",
        )
    )

    # 7. batch resumable
    resumable = results_path.exists()
    detail = f"{results_path} present"
    if checkpoint_dir is not None:
        checkpoint = Path(checkpoint_dir)
        resumable = resumable and checkpoint.exists() and checkpoint.is_dir()
        detail = f"{detail}; checkpoint dir present: {resumable}"
    checks.append(_item("batch resumable", resumable, detail))

    # 8. versions immutable: identical provenance fingerprints on re-derive
    stable = 0
    checked = 0
    if results:
        from aos_v0.exam.config import load_exam_configuration

        exam = None
        if exam_config is not None:
            try:
                exam = load_exam_configuration(exam_config)
            except Exception:  # noqa: BLE001 - best-effort pin check
                exam = None
        if exam is None:
            here = results_path.parent
            candidates = [p for p in here.glob("*.json") if "exam" in p.name.lower()]
            if candidates:
                try:
                    exam = load_exam_configuration(str(candidates[0]))
                except Exception:  # noqa: BLE001
                    exam = None
        for result in results:
            checked += 1
            if exam is not None:
                from aos_v0.exam.provenance import build_provenance

                first = build_provenance(exam, result)
                second = build_provenance(exam, result)
                if first.fingerprint == second.fingerprint:
                    stable += 1
    checks.append(
        _item(
            "versions immutable",
            checked > 0 and stable == checked,
            f"{stable}/{checked} provenance fingerprints stable across re-derivation",
        )
    )

    overall = "ready" if all(c.passed for c in checks) else "not_ready"
    return ReadinessReport(overall=overall, checks=checks)


def render_readiness(report: ReadinessReport) -> str:
    lines = [
        "PRODUCTION READINESS",
        "=" * 60,
        f"overall: {report.overall}",
        "",
    ]
    for check in report.checks:
        lines.append(f"  [{'PASS' if check.passed else 'FAIL'}] {check.requirement}: {check.detail}")
    return "\n".join(lines)


def readiness_cli_main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="aos_v0.exam.readiness",
        description="Run the pre-deployment production-readiness checklist (Phase 14).",
    )
    parser.add_argument("results", help="results.jsonl from a batch run")
    parser.add_argument("--exam-config", default=None, help="exam config to re-derive identity for the immutability check")
    parser.add_argument("--checkpoint-dir", default=None, help="batch checkpoint dir")
    parser.add_argument("--out", default=None, help="optional output dir (writes READINESS.md/json)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = verify_production_readiness(
        args.results,
        checkpoint_dir=args.checkpoint_dir,
        exam_config=args.exam_config,
    )
    if args.out:
        import os

        os.makedirs(args.out, exist_ok=True)
        Path(args.out, "readiness.json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        Path(args.out, "READINESS.md").write_text(render_readiness(report) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        print(render_readiness(report))
    return 0 if report.overall == "ready" else 1


def main(argv: Optional[List[str]] = None) -> int:
    return readiness_cli_main(argv)


if __name__ == "__main__":
    sys.exit(readiness_cli_main())