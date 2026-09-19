"""Phase 1 model-benchmark harness (reuses the kernel evaluation layer).

Phase 1's benchmark deliverable is the *skeleton plus this runner*: a case
datasets directory (`model-benchmark/`) and one code path that executes any
case against any registered resource through `aos_v0.services.evaluation` --
the same provider-agnostic layer the research methodology mandates (DOC1 M3).

Case format (JSON per file under ``model-benchmark/<category>/``)::

    {
      "resource_id": "document_ocr",
      "task": "document.ocr",
      "input_text": "path/to/scan.png",
      "instruction": "Transcribe the page ...",
      "expected": "TCP is a connection-oriented ..."
    }

Everything is data, so an annotated pair (a rubric-scored answer in
``exact_answers/``, a paraphrase in ``paraphrased_answers/``) can be scored by
any model without changing code. Run with::

    python3 -m aos_v0.exam.benchmark --root model-benchmark
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

from aos_v0.services.evaluation import EvalCase, ExecutionMetrics, benchmark, summarize

# The benchmark dataset layout defined by the implementation plan (Phase 1).
BENCHMARK_CATEGORIES = [
    "exact_answers",
    "paraphrased_answers",
    "partial_answers",
    "incorrect_answers",
    "spelling_errors",
    "handwritten",
    "mathematical",
    "diagrams",
    "multilingual",
    "difficult_documents",
]


def benchmark_categories() -> List[str]:
    """The dataset categories the benchmark expects, in plan order."""
    return list(BENCHMARK_CATEGORIES)


def load_cases(root: str | Path) -> List[EvalCase]:
    """Load every ``*.json`` case under ``root`` into a benchmark case list.

    A case file may be a single case object or a list of them. Missing files
    are skipped silently so an empty skeleton directory is still loadable.
    """
    base = Path(root)
    cases: List[EvalCase] = []
    for category in BENCHMARK_CATEGORIES:
        base_dir = base / category
        if not base_dir.is_dir():
            continue
        for path in sorted(base_dir.glob("*.json")):
            raw_docs = json.loads(path.read_text())
            if isinstance(raw_docs, dict):
                raw_docs = [raw_docs]
            for raw in raw_docs:
                cases.append(
                    EvalCase(
                        resource_id=raw["resource_id"],
                        input_text=raw["input_text"],
                        instruction=raw.get("instruction"),
                        task=raw.get("task"),
                        expected=raw.get("expected"),
                    )
                )
    return cases


def run_benchmark(root: str | Path, registry) -> List[ExecutionMetrics]:
    """Execute every loaded case against the registry (provider-agnostic)."""
    return benchmark(registry, load_cases(root))


def report(records: List[ExecutionMetrics]) -> Dict[str, Dict[str, object]]:
    """Descriptive per-resource rollup; no invented quality scores."""
    return summarize(records)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the exam model benchmark.")
    parser.add_argument("--root", default="model-benchmark",
                        help="case dataset root directory")
    parser.add_argument("--registry", default="default",
                        choices=["default", "hf", "exam"],
                        help="which resource pool to benchmark against")
    args = parser.parse_args(argv)

    from aos_v0.services.resource_registration import (
        build_default_registry,
        build_hf_enabled_registry,
    )

    if args.registry == "default":
        registry = build_default_registry()
    elif args.registry == "hf":
        registry = build_hf_enabled_registry()
    else:
        registry = build_default_registry()
        from aos_v0.exam.resources import register_exam_resources
        register_exam_resources(registry)

    cases = load_cases(args.root)
    if not cases:
        print(f"[benchmark] no cases under '{args.root}'; dataset skeleton only.")
        return 0

    print(f"[benchmark] {len(cases)} case(s) -> {len(registry.manifests())} "
          f"registered resource(s)")
    records = run_benchmark(args.root, registry)
    for resource_id, stats in report(records).items():
        print(f"  {resource_id:<32} success={stats['success_rate']:.2f} "
              f"mean_latency_ms={stats['mean_latency_ms']} "
              f"errors={stats['error_categories']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())