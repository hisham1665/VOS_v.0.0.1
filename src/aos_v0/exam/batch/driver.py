"""Mass paper evaluation driver (plan Phase 10 deliverables).

Batch processing engine .. worker pool .. queue .. progress tracking ..
checkpoint system .. resume mechanism .. batch status API, delivered by one
small driver that owns everything the kernel must not own: paper discovery,
a parallel worker pool, durable checkpoint/resume, retry and failure
isolation. If paper 37 fails, papers 1-36 stay completed, 37 is recorded as
failed/review, and 38-100 still process -- the batch never restarts because
completed papers are never re-graded on resume.
"""

from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from aos_v0.exam.batch.discovery import discover_answer_sheets, resolve_zip_inputs
from aos_v0.exam.batch.models import (
    BatchCheckpoint,
    BatchConfig,
    BatchItem,
    BatchReport,
    ItemStatus,
    load_checkpoint,
    save_checkpoint,
)
from aos_v0.exam.models import (
    EvaluationSettings,
    ExamConfiguration,
    ExamEvaluationTask,
    PaperReference,
    Roster,
)
from aos_v0.exam.orchestrator import ExamOrchestrator, ExamRunResult


class ProcessedOutcome(BaseModel):
    """What one paper produced, in batch terms (status + audit trail)."""

    model_config = ConfigDict(extra="forbid")

    status: str  # "completed" | "review" (batch-level)
    review_reasons: List[str] = []
    total_marks: Optional[float] = None
    max_marks: Optional[float] = None
    confidence: Optional[float] = None
    student: dict = {}
    result: Optional[ExamRunResult] = Field(
        default=None, description="full evidence record for the review queue"
    )


def _default_process(
    exam: ExamConfiguration,
    *,
    roster: Optional[Roster] = None,
    settings: Optional[EvaluationSettings] = None,
) -> Callable[[BatchItem], ProcessedOutcome]:
    """Evaluate one answer sheet through the Phase-9 recovery-enabled controller."""

    def process(item: BatchItem) -> ProcessedOutcome:
        task = ExamEvaluationTask(
            task_id=f"batch-{item.paper_id}",
            paper=PaperReference(paper_id=item.paper_id, file_path=item.source),
            exam=exam,
            roster=roster,
            settings=settings or EvaluationSettings(),
        )
        result: ExamRunResult = ExamOrchestrator().execute(
            task, source=item.source
        )
        return ProcessedOutcome(
            status="completed" if result.status == "ok" else "review",
            review_reasons=list(result.review_reasons),
            total_marks=result.total_marks,
            max_marks=result.max_marks,
            confidence=result.confidence,
            student=dict(result.student),
            result=result,
        )

    return process


class BatchRunner:
    """Runs one batch (or resumes an interrupted one) end to end."""

    def __init__(
        self,
        exam: ExamConfiguration,
        *,
        roster: Optional[Roster] = None,
        settings: Optional[EvaluationSettings] = None,
        config: Optional[BatchConfig] = None,
        process: Optional[Callable[[BatchItem], ProcessedOutcome]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.exam = exam
        self.roster = roster
        self.settings = settings
        self.config = config or BatchConfig()
        self.process = process or _default_process(
            exam, roster=roster, settings=settings
        )
        self.log = log or (lambda _message: None)

    # -- public API ----------------------------------------------------------

    def run(self, source: str | Path) -> BatchReport:
        source = Path(source)
        tmp: Optional[tempfile.TemporaryDirectory] = None
        try:
            items = discover_answer_sheets(source)
            if not items:
                raise FileNotFoundError(
                    f"no answer sheets found in '{source}' (PDF/ZIP/images)"
                )
            if self.config.checkpoint_path is not None:
                checkpoint = load_checkpoint(self.config.checkpoint_path)
            else:
                checkpoint = None
            if checkpoint is not None and not self.config.force:
                checkpoint.items = self._merge_resume(checkpoint, items)
            else:
                self.log(f"[batch] starting fresh batch of {len(items)} papers")
                checkpoint = BatchCheckpoint(items=items)

            zip_input = source if source.is_file() else None
            extract_dir: Optional[Path] = None
            if zip_input is not None and source.suffix.lower() == ".zip":
                tmp = tempfile.TemporaryDirectory(prefix="aos-batch-")
                extract_dir = Path(tmp.name)
                checkpoint.items = resolve_zip_inputs(
                    checkpoint.items, source, extract_dir
                )

            started = time.monotonic()
            checkpoint = self._drain(checkpoint)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if self.config.checkpoint_path is not None:
                save_checkpoint(checkpoint, self.config.checkpoint_path)
            report = BatchReport.from_checkpoint(
                checkpoint,
                finished_at=self._now_iso(),
                elapsed_ms=elapsed_ms,
            )
            self._log_report(report)
            return report
        finally:
            if tmp is not None:
                tmp.cleanup()

    def status(self, source: str | Path) -> BatchReport:
        """Batch status API: inspect a checkpoint (or live items) without running."""
        if self.config.checkpoint_path is not None:
            checkpoint = load_checkpoint(self.config.checkpoint_path)
            if checkpoint is not None:
                return BatchReport.from_checkpoint(checkpoint)
        items = discover_answer_sheets(Path(source))
        return BatchReport.from_checkpoint(BatchCheckpoint(items=items))

    def resume(self, source: str | Path) -> BatchReport:
        """Explicit resume: same as ``run`` when a checkpoint already exists."""
        return self.run(source)

    # -- internals -----------------------------------------------------------

    def _merge_resume(
        self, checkpoint: BatchCheckpoint, discovered: List[BatchItem]
    ) -> List[BatchItem]:
        known = {item.paper_id: item for item in checkpoint.items}
        for item in discovered:
            prior = known.get(item.paper_id)
            if prior is not None and prior.status == ItemStatus.COMPLETED:
                item.status = ItemStatus.COMPLETED
                item.attempts = prior.attempts
                item.completed_at = prior.completed_at
                item.total_marks = prior.total_marks
                item.max_marks = prior.max_marks
                item.confidence = prior.confidence
                item.review_reasons = list(prior.review_reasons)
                item.student = dict(prior.student)
        completed = sum(
            1 for it in checkpoint.items if it.status == ItemStatus.COMPLETED
        )
        self.log(
            f"[batch] resuming: {completed} paper(s) already completed, "
            f"{len(discovered) - completed} to process"
        )
        return discovered

    def _drain(self, checkpoint: BatchCheckpoint) -> BatchCheckpoint:
        pending = [
            item for item in checkpoint.items
            if item.status != ItemStatus.COMPLETED or self.config.force
        ]
        futures: dict[Future, BatchItem] = {}
        with ThreadPoolExecutor(
            max_workers=self.config.max_workers,
            thread_name_prefix="exam-batch",
        ) as pool:
            for item in pending:
                item.status = ItemStatus.PROCESSING
                futures[pool.submit(self._process_item, item)] = item

            done = 0
            for future in as_completed(futures):
                item = futures[future]
                if self.config.timeout_seconds is not None:
                    try:
                        future.result(timeout=self.config.timeout_seconds)
                    except TimeoutError:
                        item.status = ItemStatus.FAILED
                        item.error = "timed out after "
                        item.error += f"{self.config.timeout_seconds:g}s"
                else:
                    future.result()  # may re-raise; the runner never blocks others
                done += 1
                if self.config.checkpoint_path is not None:
                    save_checkpoint(checkpoint, self.config.checkpoint_path)
                self.log(
                    f"[batch] progress {done}/{len(pending)} -> "
                    f"{item.paper_id}: {item.status.value}"
                )
        return checkpoint

    def _process_item(self, item: BatchItem) -> None:
        """One paper under retry + failure isolation (never raises outward)."""
        for attempt in range(self.config.retries + 1):
            item.attempts += 1
            try:
                outcome = self.process(item)
            except Exception as exc:  # noqa: BLE001 - isolation boundary
                item.error = f"{type(exc).__name__}: {exc}"
                if attempt < self.config.retries:
                    self.log(
                        f"[batch] retrying {item.paper_id} "
                        f"(attempt {item.attempts}): {item.error}"
                    )
                    continue
                item.status = ItemStatus.FAILED
                return
            if outcome.status == "review":
                item.status = ItemStatus.REVIEW
            else:
                item.status = ItemStatus.COMPLETED
            item.review_reasons = list(outcome.review_reasons)
            item.total_marks = outcome.total_marks
            item.max_marks = outcome.max_marks
            item.confidence = outcome.confidence
            item.student = dict(outcome.student)
            item.result = outcome.result
            item.completed_at = self._now_iso()
            item.error = ""
            if (
                self.config.results_path is not None
                and outcome.result is not None
            ):
                self._append_result(outcome.result)
            return

    def _append_result(self, result: ExamRunResult) -> None:
        assert self.config.results_path is not None
        path = Path(self.config.results_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(result.model_dump(mode="json")) + "\n")

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def _log_report(self, report: BatchReport) -> None:
        totals = report.totals
        self.log(
            "[batch] finished: "
            f"{totals['completed']} completed, "
            f"{totals['review']} review, "
            f"{totals['failed']} failed, "
            f"{totals['pending']} pending "
            f"in {report.elapsed_ms}ms"
        )


def run_batch(
    exam: ExamConfiguration,
    source: str | Path,
    *,
    roster: Optional[Roster] = None,
    settings: Optional[EvaluationSettings] = None,
    config: Optional[BatchConfig] = None,
    process: Optional[Callable[[BatchItem], ProcessedOutcome]] = None,
) -> BatchReport:
    """One-shot convenience entry point: build the runner and run."""
    return BatchRunner(
        exam,
        roster=roster,
        settings=settings,
        config=config,
        process=process,
    ).run(source)


def batch_cli_main(argv: Optional[list] = None) -> int:
    """CLI for the batch driver: `python -m aos_v0.exam.batch ...`."""
    import argparse
    import json

    from aos_v0.exam.config import load_exam_configuration

    parser = argparse.ArgumentParser(
        prog="aos-v0-exam-batch",
        description="Mass paper evaluation (Phase 10): a folder / ZIP / file "
                    "of answer sheets, evaluated in parallel with "
                    "checkpoint/resume.",
    )
    parser.add_argument("exam_config", help="exam configuration JSON file")
    parser.add_argument("answers", help="folder, ZIP archive, or single answer sheet")
    parser.add_argument("--out", dest="checkpoint", default=None,
                        help="checkpoint JSON file (required for resume)")
    parser.add_argument("--results", dest="results", default=None,
                        help="append-only JSONL file of per-paper results "
                             "(Phase 12 reporting input)")
    parser.add_argument("--workers", type=int, default=4,
                        help="parallel workers (default 4)")
    parser.add_argument("--retries", type=int, default=1,
                        help="retries per paper beyond the first attempt (default 1)")
    parser.add_argument("--timeout", dest="timeout", type=float, default=None,
                        help="per-paper timeout in seconds")
    parser.add_argument("--force", action="store_true",
                        help="reprocess completed papers too")
    parser.add_argument("--status", action="store_true",
                        help="print the checkpoint status and exit without running")
    parser.add_argument("--json", action="store_true",
                        help="emit the report as JSON")
    args = parser.parse_args(argv)

    config_data = load_exam_configuration(args.exam_config)
    exam = config_data.exam if hasattr(config_data, "exam") else config_data
    batch_config = BatchConfig(
        max_workers=args.workers,
        retries=args.retries,
        checkpoint_path=Path(args.checkpoint) if args.checkpoint else None,
        results_path=Path(args.results) if args.results else None,
        timeout_seconds=args.timeout,
        force=args.force,
    )
    runner = BatchRunner(exam, config=batch_config, log=print)
    if args.status:
        report = runner.status(args.answers)
    else:
        report = runner.run(args.answers)
    if args.json:
        print(json.dumps(report.to_plan_json(), indent=2))
    else:
        print(f"batch status: {report.to_plan_json()}")
    return 0