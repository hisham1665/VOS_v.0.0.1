"""Aggregate image/report coverage, or replay explicitly supplied study folders.

Examples:
  python evaluate_images.py --reports outputs/reports/example.json
  python evaluate_images.py --studies ./cxr_cases/case1 ./ct_cases/case2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.image_evaluation import aggregate_reports
from models import RadiologyReport


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate coverage separately by evidence kind, modality and region")
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--reports", nargs="+", type=Path, help="Existing report JSON files; no inference")
    sources.add_argument("--studies", nargs="+", type=Path, help="Replay these folders with the configured image/text backends")
    parser.add_argument("--output", type=Path, help="Optional coverage JSON output")
    args = parser.parse_args()
    if args.reports:
        reports = [RadiologyReport.model_validate_json(path.read_text(encoding="utf-8")) for path in args.reports]
    else:
        from pipeline import analyze_study
        reports = [analyze_study(folder, with_literature=False, write_output=False)[0] for folder in args.studies]
    summary = aggregate_reports(reports)
    summary["models_used"] = sorted({model for report in reports for model in report.models_used.values()})
    text = json.dumps(summary, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
