"""AOS Exam Evaluator -- Phase 13 research evaluation harness.

Phase 12 covers reports/CSV/analytics; Phase 13 (this package) measures the
AOS-based evaluator against a human ground-truth dataset and runs the research
experiments and batch-throughput probes mandated by the implementation plan
(`AOS_Exam_Evaluator_Phase_Wise_Implementation_Plan.md` Ph13). Deliverables are
the ``benchmark_dataset/`` folder, ``evaluation_results.csv``,
``experiment_results/`` and ``BENCHMARK_REPORT.md``, all generated from the
recorded (never mocked) evaluation runs.

CLI::

    python -m aos_v0.exam.research <exam.json> --dataset <entries-dir> --out DIR
    python -m aos_v0.exam.research <exam.json> --sample-dataset DIR --out DIR

Golden rules are enforced like everywhere else: no auto-zeroing, no invented
marks -- an AI mark is either the pipeline's recorded mark or a human review
decision.
"""

from .corpus import (
    CorpusEntryResult,
    CorpusReport,
    doc_for_answer,
    evaluate_entry,
    evaluate_entry_with_trace,
    run_corpus,
    task_for,
    write_corpus_json,
)
from .experiments import (
    ExperimentResult,
    batch_throughput,
    experiment_a_single_vs_two_agent,
    experiment_b_exact_vs_semantic,
    experiment_c_fixed_vs_dynamic,
    experiment_d_no_recovery_vs_recovery,
    experiment_e_single_pass_vs_fallback_ocr,
    experiment_f_single_model_vs_routing,
)
from .ground_truth import (
    DatasetManifest,
    GroundTruthEntry,
    build_dataset,
    read_dataset,
    write_dataset,
)
from .metrics import (
    agent_agreement,
    character_accuracy,
    exact_agreement,
    false_acceptance,
    false_rejection,
    mean_absolute_error,
    ocr_accuracy,
    partial_mark_agreement,
    recovery_success,
    reconciliation_success,
    selection_overhead,
    selection_quality,
    semantic_acceptance,
    tolerance_agreement,
    word_accuracy,
)
from .report import (
    EVALUATION_CSV_HEADER,
    evaluation_results_csv,
    render_benchmark_report,
    render_experiment,
    write_benchmark_report,
    write_evaluation_csv,
    write_experiment_files,
)

__all__ = [
    "DatasetManifest",
    "GroundTruthEntry",
    "build_dataset",
    "read_dataset",
    "write_dataset",
    "CorpusEntryResult",
    "CorpusReport",
    "doc_for_answer",
    "evaluate_entry",
    "evaluate_entry_with_trace",
    "run_corpus",
    "task_for",
    "write_corpus_json",
    "ExperimentResult",
    "batch_throughput",
    "experiment_a_single_vs_two_agent",
    "experiment_b_exact_vs_semantic",
    "experiment_c_fixed_vs_dynamic",
    "experiment_d_no_recovery_vs_recovery",
    "experiment_e_single_pass_vs_fallback_ocr",
    "experiment_f_single_model_vs_routing",
    "mean_absolute_error",
    "exact_agreement",
    "tolerance_agreement",
    "false_rejection",
    "false_acceptance",
    "semantic_acceptance",
    "partial_mark_agreement",
    "agent_agreement",
    "reconciliation_success",
    "recovery_success",
    "selection_quality",
    "selection_overhead",
    "ocr_accuracy",
    "character_accuracy",
    "word_accuracy",
    "EVALUATION_CSV_HEADER",
    "evaluation_results_csv",
    "render_benchmark_report",
    "render_experiment",
    "write_benchmark_report",
    "write_evaluation_csv",
    "write_experiment_files",
]