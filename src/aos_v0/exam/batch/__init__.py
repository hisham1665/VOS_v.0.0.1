"""Phase 10 -- mass paper evaluation (batch driver).

`discovery` turns a folder / ZIP / single file into the batch's pending items;
`models` carries the driver's own status, checkpoint and report contract;
`driver` runs the parallel, checkpoint-resuming, failure-isolated batch over
the `ExamOrchestrator` (Phase 8/9). All of it lives in the driver -- the
kernel never learns that a batch exists.
"""

from aos_v0.exam.batch.discovery import (
    ANSWER_EXTENSIONS,
    discover_answer_sheets,
    resolve_zip_inputs,
)
from aos_v0.exam.batch.driver import (
    BatchRunner,
    ProcessedOutcome,
    batch_cli_main,
    run_batch,
)
from aos_v0.exam.batch.models import (
    BatchCheckpoint,
    BatchConfig,
    BatchItem,
    BatchReport,
    ItemStatus,
    load_checkpoint,
    save_checkpoint,
)

__all__ = [
    "ANSWER_EXTENSIONS",
    "BatchCheckpoint",
    "BatchConfig",
    "BatchItem",
    "BatchReport",
    "BatchRunner",
    "ItemStatus",
    "ProcessedOutcome",
    "batch_cli_main",
    "discover_answer_sheets",
    "load_checkpoint",
    "resolve_zip_inputs",
    "run_batch",
    "save_checkpoint",
]