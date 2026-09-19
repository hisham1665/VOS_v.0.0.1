"""Phase 14 performance probe.

Runs the real batch driver over synthetic (blank, PIL-rendered) answer sheets
at graduated scales -- the plan's 10 / 50 / 100 / 500 / 1000 curve -- and
measures total & per-paper time, throughput, completion/review/failed rates,
failure rate, recovery rate (from each result's ``recovery`` records) and
queue latency (batch wall time). GPU/CPU/VRAM telemetry is deployment-layer
(spec Ph14) and intentionally not fabricated here.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from aos_v0.exam.batch import BatchConfig, run_batch

PLAN_SCALES = (10, 50, 100, 500, 1000)


class ScaleRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scale: int
    papers: int
    completed: int
    review_routed: int
    failed: int
    processed: int
    total_seconds: float
    avg_paper_seconds: float
    papers_per_second: float
    failure_rate: float
    recovery_rate: float
    queue_seconds: float
    results_rows: int = 0


def run_performance_probe(
    exam,
    out_dir: str,
    *,
    scales: tuple = PLAN_SCALES,
    workers: int = 2,
    retries: int = 0,
) -> List[ScaleRow]:
    """Run graduated-scale batches and write ``performance_results.csv``."""
    from PIL import Image

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows: List[ScaleRow] = []

    import csv

    with open(out / "performance_results.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[field for field in ScaleRow.model_fields])
        writer.writeheader()
        for scale in scales:
            row = _probe_scale(exam, scale, workers=workers, retries=retries)
            rows.append(row)
            writer.writerow(row.model_dump(mode="json"))
            fh.flush()
    return rows


def _probe_scale(
    exam,
    scale: int,
    *,
    workers: int,
    retries: int,
) -> ScaleRow:
    from PIL import Image

    folder = Path(tempfile.mkdtemp(prefix=f"aos_perf_{scale}_"))
    for index in range(scale):
        image = Image.new("RGB", (400, 300), (250, 250, 250))
        image.save(folder / f"sheet{index + 1:05d}.png")

    from aos_v0.exam.reporting.assemble import read_results

    results_path = folder / "results.jsonl"
    start = time.time()
    report = run_batch(
        exam,
        folder,
        config=BatchConfig(max_workers=workers, retries=retries, results_path=results_path),
    )
    queue_seconds = time.time() - start

    processed = sum(1 for item in report.items if item.status in ("completed", "review"))
    completed = sum(1 for item in report.items if item.status == "completed")
    failed = sum(1 for item in report.items if item.status == "failed")

    recovery_records = 0
    recovery_candidates = processed
    if results_path.exists():
        results = read_results(results_path)
        recovery_records = sum(len(r.recovery) for r in results)
        recovery_candidates = max(completed, len(results))

    results_count = (
        sum(1 for _ in open(results_path, encoding="utf-8"))
        if results_path.exists()
        else 0
    )
    scale_n = max(scale, 1)
    total = max(queue_seconds, 1e-6)
    return ScaleRow(
        scale=scale_n,
        papers=scale_n,
        completed=completed,
        review_routed=processed - completed,
        failed=failed,
        processed=processed,
        total_seconds=round(queue_seconds, 3),
        avg_paper_seconds=round(total / scale_n, 4),
        papers_per_second=round(processed / total, 3),
        failure_rate=round(failed / scale_n, 4),
        recovery_rate=round(recovery_records / recovery_candidates, 4)
        if recovery_candidates
        else 0.0,
        queue_seconds=round(queue_seconds, 3),
        results_rows=results_count,
    )


def render_performance(rows: List[ScaleRow]) -> str:
    header = (
        f"{'scale':<8}{'papers':<8}{'done':<7}{'review':<7}{'failed':<8}"
        f"{'papers/s':<10}{'avg s/paper':<13}{'fail%':<8}{'recovery%':<10}"
    )
    lines = ["PERFORMANCE PROBE", "=" * 80, header]
    for row in rows:
        lines.append(
            f"{row.scale:<8}{row.papers:<8}{row.completed:<7}{row.review_routed:<7}"
            f"{row.failed:<8}{row.papers_per_second:<10.3f}{row.avg_paper_seconds:<13.4f}"
            f"{row.failure_rate:<8.3f}{row.recovery_rate:<10.3f}"
        )
    if rows:
        lines.append("")
        lines.append(
            f"average papers/s across scales: "
            f"{sum(r.papers_per_second for r in rows) / len(rows):.3f}"
        )
    return "\n".join(lines)


def probe_cli_main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    from aos_v0.exam.config import load_exam_configuration

    parser = argparse.ArgumentParser(
        prog="aos_v0.exam.perf",
        description="Graduated-scale performance probe (Phase 14).",
    )
    parser.add_argument("exam_config", help="path to the exam JSON/YAML config")
    parser.add_argument("--out", default="perf_results", help="output directory")
    parser.add_argument(
        "--scales", default="10,50,100", help="comma-separated paper counts to probe"
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    scales = tuple(int(s) for s in args.scales.split(",") if s.strip())
    exam = load_exam_configuration(args.exam_config)
    rows = run_performance_probe(
        exam,
        args.out,
        scales=scales,
        workers=args.workers,
    )
    if args.json:
        import json

        print(json.dumps([r.model_dump(mode="json") for r in rows], indent=2))
    else:
        print(render_performance(rows))
        print(f"\nCSV: {os.path.join(args.out, 'performance_results.csv')}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return probe_cli_main(argv)


if __name__ == "__main__":
    sys.exit(probe_cli_main())