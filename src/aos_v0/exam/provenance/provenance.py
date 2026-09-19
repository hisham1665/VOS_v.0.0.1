"""Provenance record builder and verifier (Production Hardening plan)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from aos_v0.exam.dna import EXAM_DNA_TEMPLATES

ANSWER_KEY_VERSION_LABEL = "answer-key"
RUBRIC_VERSION_LABEL = "rubric"


class EvaluationProvenance(BaseModel):
    """Immutable-ish reproducibility record for one evaluated paper.

    ``fingerprint`` covers every field except ``timestamp`` (which is an
    attribute of *when* the evaluation ran, not its identity).
    """

    model_config = ConfigDict(extra="forbid")

    exam_id: str
    exam_title: str
    exam_version: str = "0"

    answer_key_version: str
    rubric_version: str
    models: List[str] = Field(default_factory=list)
    model_versions: List[str] = Field(default_factory=list)
    capability_dna: str
    aos_config_fingerprint: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    paper_id: str
    total_marks: float = 0.0
    max_marks: float = 0.0
    confidence: float = 0.0
    status: str = "ok"
    needs_review: bool = False
    review_reasons: List[str] = Field(default_factory=list)
    recovery_count: int = 0

    fingerprint: str = ""

    def model_canonical(self) -> dict:
        """Stable, insertion-ordered subset that defines the identity."""
        return {
            "exam_id": self.exam_id,
            "exam_version": self.exam_version,
            "answer_key_version": self.answer_key_version,
            "rubric_version": self.rubric_version,
            "models": self.models,
            "model_versions": self.model_versions,
            "capability_dna": self.capability_dna,
            "aos_config_fingerprint": self.aos_config_fingerprint,
            "paper_id": self.paper_id,
            "evaluation": {
                "total_marks": self.total_marks,
                "max_marks": self.max_marks,
                "confidence": self.confidence,
                "status": self.status,
                "needs_review": self.needs_review,
                "review_reasons": self.review_reasons,
                "recovery_count": self.recovery_count,
            },
        }


def provenance_fingerprint(record: EvaluationProvenance) -> str:
    return fingerprint(record.model_canonical())


def fingerprint(payload: dict) -> str:
    """sha256 over canonical sorted JSON ('sort_keys' keeps output stable)."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _content_version(label: str, payload) -> str:
    return f"{label}:{fingerprint(payload)}"


def _answer_key_payload(exam) -> dict:
    return {
        "questions": [
            {
                "question_id": q.question_id,
                "answer_key": q.answer_key.model_dump(mode="json", exclude_none=True),
            }
            for q in exam.questions
        ]
    }


def _rubric_payload(exam) -> dict:
    return {
        "questions": [
            {
                "question_id": q.question_id,
                "rubric": q.rubric.model_dump(mode="json", exclude_none=True),
            }
            for q in exam.questions
        ]
    }


def _models_from_result(result) -> List[str]:
    """Unique 'model' ids the trace actually ran (per capability)."""
    return sorted(
        {node.model for node in result.trace.nodes if node.model}
    )


def _resourced_models(result) -> List[str]:
    return sorted(
        {f"{node.model}@{node.resource_id}" for node in result.trace.nodes if node.model}
    )


def _dna_fingerprint(result) -> str:
    payload = {}
    for node in result.trace.nodes:
        template = EXAM_DNA_TEMPLATES.get(node.capability)
        if template is not None:
            payload[node.capability] = template.model_dump()
    return fingerprint(payload) if payload else "none"


def build_provenance(
    exam,
    result,
    *,
    aos_config: Optional[dict] = None,
) -> EvaluationProvenance:
    """Record every reproducibility field for one evaluated paper."""
    aos_config = aos_config or {}
    record = EvaluationProvenance(
        exam_id=exam.exam_id,
        exam_title=getattr(exam, "exam_title", None) or (result.exam_title or exam.exam_id),
        exam_version=getattr(exam, "version", "0"),
        answer_key_version=_content_version(ANSWER_KEY_VERSION_LABEL, _answer_key_payload(exam)),
        rubric_version=_content_version(RUBRIC_VERSION_LABEL, _rubric_payload(exam)),
        models=_models_from_result(result),
        model_versions=_resourced_models(result),
        capability_dna=_dna_fingerprint(result),
        aos_config_fingerprint=fingerprint(aos_config or {}),
        paper_id=result.paper_id,
        total_marks=float(result.total_marks),
        max_marks=float(result.max_marks),
        confidence=float(result.confidence),
        status=result.status,
        needs_review=bool(result.needs_review),
        review_reasons=list(result.review_reasons),
        recovery_count=len(result.recovery),
    )
    record.fingerprint = provenance_fingerprint(record)
    return record


def verify_provenance(record: EvaluationProvenance, exam) -> dict:
    """Re-derive identity fields from ``exam`` and report drift."""
    expected = {
        "exam_id": exam.exam_id,
        "exam_version": getattr(exam, "version", "0"),
        "answer_key_version": _content_version(ANSWER_KEY_VERSION_LABEL, _answer_key_payload(exam)),
        "rubric_version": _content_version(RUBRIC_VERSION_LABEL, _rubric_payload(exam)),
    }
    return {
        key: {"recorded": getattr(record, key), "matches": getattr(record, key) == expected[key]}
        for key in expected
    }


def provenance_cli_main(argv: Optional[List[str]] = None) -> int:
    """CLI: emit per-paper provenance records + a manifest, option to verify."""
    import argparse
    import sys

    from aos_v0.exam.config import load_exam_configuration
    from aos_v0.exam.reporting.assemble import read_results

    parser = argparse.ArgumentParser(
        prog="aos_v0.exam.provenance",
        description="Reproducibility records for evaluated papers (Phase 14).",
    )
    parser.add_argument("exam_config", help="path to the exam JSON/YAML config")
    parser.add_argument("results", help="append-only results.jsonl from a batch run")
    parser.add_argument("--out", default="provenance", help="output directory")
    parser.add_argument("--verify", action="store_true", help="verify recorded identity vs exam")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    exam = load_exam_configuration(args.exam_config)
    results = read_results(args.results)

    from pathlib import Path

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, dict] = {}
    for result in results:
        record = build_provenance(exam, result)
        per_paper = out_dir / f"provenance_{result.paper_id}.json"
        per_paper.write_text(
            json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        entry = {
            "paper_id": result.paper_id,
            "exam_id": record.exam_id,
            "exam_version": record.exam_version,
            "fingerprint": record.fingerprint,
            "status": record.status,
            "needs_review": record.needs_review,
        }
        if args.verify:
            entry["verify"] = verify_provenance(record, exam)
        manifest[result.paper_id] = entry

    manifest_path = out_dir / "provenance_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if args.json:
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
    else:
        print(f"provenance records: {len(manifest)} -> {out_dir}")
        if args.verify:
            drifted = [
                pid
                for pid, entry in manifest.items()
                if any(not field["matches"] for field in (entry.get("verify") or {}).values())
            ]
            print(f"identity drift: {len(drifted)}" + (f" ({sorted(drifted)})" if drifted else ""))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return provenance_cli_main(argv)