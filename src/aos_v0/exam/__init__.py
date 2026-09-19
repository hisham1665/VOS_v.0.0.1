"""Exam-evaluation domain (Ph0 interface + Ph1 survey + Ph2 config + Ph3 intake + Ph4 OCR + Ph5 structure + Ph6 evaluation).

Phase 2 adds the exam configuration and answer-key layer on top of the Phase-0
interface: `models` now carries `AnswerKey`, `ConceptRelationship`,
`PartialCreditRule` and `MarkingRules`, and `config` provides the file-based
configuration API (`load_exam_configuration`, `dump_exam_configuration`,
`collect_issues`) so an examiner can configure an entire examination without
changing source code.

The exam evaluator is deliberately a *domain built on top of AOS*, not a
replacement for it (AOS_Exam_Evaluator_Phase_Wise_Implementation_Plan.md,
Design Principle 20). This package owns the exam-specific data contract --
ExamConfiguration, Question, Rubric, PaperReference, Roster, EvaluationSettings
and the root ExamEvaluationTask -- plus the adapter that turns a task into an
AOS task graph (`ExamEvaluationTask.build_graph`).

Phase 1 adds the capability-driven model survey on top of that interface:
`model_selection` (the initial model registry: per-capability primary/fallback
+ resource profile + selection constraints), `dna` (a CapabilityDNA template
per exam flag), `resources` (opt-in Capability Registry entries, declared-only),
and `benchmark` (the model-benchmark harness that reuses the provider-agnostic
kernel evaluation layer).

Nothing in this package modifies the kernel. It only consumes `aos_v0.core.*`
models to produce a valid Graph skeleton, mirroring how the CLI already drives
the kernel. The capability flags referenced here are registered in the kernel
vocabulary as of Phase 1 (`CAPABILITY_FLAGS`); the graph remains a structurally
valid interface until the execution transports are wired (Phase 3/4, gap G3).

Phase 4 adds the OCR and document-understanding service on top of the intake
stage: `ocr/` supplies the structured output schema, confidence extraction,
OCR model adapters (declared ML adapters bound to the Phase-1 selection, plus
a runnable local structural engine), the rule-based layout analyzer, and the
orchestrating `run_ocr`/`run_document` pipeline whose confidence gate routes
unreadable pages to human review -- never to an automatic zero. The kernel
failure taxonomy gains the `tool.low_confidence` class (Seam S5).

Phase 5 adds the answer-sheet parser on top of the OCR document: `structure/`
extracts the student identity (+ validates it against an institutional roster),
detects question labels, maps answers to configured question ids (out-of-order
and continuation answers, subquestions, page grouping), and applies the plan's
edge-case taxonomy (missing / OCR'd question numbers, multiple attempts,
crossed-out and blank answers, ambiguous labels). Every ambiguity surfaces as a
`MappingIssue` plus an `EvalReviewReason` so the paper routes to human review
-- the parser never rewrites or silently attaches evidence.

Phase 6 adds the semantic evaluation engine on top of the structured sheet:
`evaluate/` matches the student's own words to the expected concepts (surface
keywords -> meaning -> concept correctness), awards marks through the configured
rubric with partial credit and math step-marking (a wrong final value never
erases correct intermediate work), and preserves every piece of evidence behind
a mark. Anything the engine cannot decide honestly routes to human review -- an
unreadable answer is never an automatic zero. The phase-5 sheet JSON flows into
`evaluate_sheet(...)`; run the CLI with `python -m aos_v0.exam.evaluate`.

Phase 7 adds the two-agent evaluation loop on top of that engine:
`two_agent` runs an independent primary evaluator and verifier over the same
evidence (the verifier never sees the primary's verdict) and a
disagreement-analysis engine (`reconcile_agents`) turns their structured
surfaces into a final verdict -- an honest disagreement routes to
`EvalReviewReason.AGENT_DISAGREEMENT` review with the mark confidence-gated,
never silently averaged or zeroed. The S4 DAG spine that fans each question out
to two parallel nodes and merges them (`ExamEvaluationTask.build_graph`) is the
structural counterpart of this loop.

Phase 8 integrates the whole workflow into AOS dynamic orchestration:
`orchestrator` is the exam adapter the plan's Phase-8 architecture calls for.
`ExamEvaluationTask.build_graph()` emits the DAG, `dna_for_node` hydrates every
node with its Capability DNA, `register_local_exam_transport` wires the eight
requirement resources (gap G3) with runnable run_fns, and `ExamOrchestrator`
selects a model per node through the registry's continuous DNA scorer and
executes the wave-ordered DAG, returning the plan-shaped `ExamRunResult`
with a full `ExecutionTrace` of the dynamic model selection.

Phase 9 adds fault recovery and the confidence engine on top of that flow:
`recovery` is the exam-side closed loop (detect -> classify -> policy ladder ->
escalate to review) that speaks the kernel failure taxonomy and runs every DAG
node under retry / reformulation / resource-substitution before degrading --
an exhausted ladder routes the question (or the whole paper, for structural
stages) to human review, never a guess, never an automatic zero.
`confidence` aggregates the plan's confidence components (OCR, answer
extraction, semantic, rubric, agent agreement) into a weighted score and a
HIGH / MEDIUM / LOW category whose thresholds come from
`EvaluationSettings.confidence_high` / `confidence_low`. Confidence decides
whether extra verification is needed, never a mark (confidence is not
correctness). The orchestrator records both per node (recovery outcome in
`NodeTrace`) and per paper (`ExamRunResult.confidence_evidence` /
`confidence_category` / `recovery` / `escalations`).

Phase 10 scales that single-paper controller to hundreds or thousands of
answer sheets: `batch` is a driver-owned mass-evaluation engine. `discovery`
turns a folder / ZIP / single answer sheet into the batch's pending items
(extension + magic-byte validation, junk removal, natural ordering);
`models` carries the driver's own status / checkpoint / report contract
(`ItemStatus`, `BatchCheckpoint`, `BatchReport`); `driver` provides the
`BatchRunner` -- parallel workers, per-paper retry, failure isolation
(paper 37 failing never restarts or blocks the rest), durable atomic
checkpoints written after every completed paper, and resume that only
re-grades unfinished papers. Nothing about the batch leaks into the kernel.

Phase 11 adds the human side on top of that output: `review` turns an
`ExamRunResult` into the plan's "REVIEW REQUIRED" cards
(`collect_review_items` -- one card per flagged question plus a paper-level
card for reasons no single row owns), stores them in a persistent queue with
an append-only audit log (`ReviewStore`), renders the ASCII dashboard
(`render_card` / `render_queue`), and performs manual mark editing --
accept / modify / escalate -- so the evaluator's *proposal* becomes a final
mark only through an explicit human decision. The audit trail per question
(student, image reference, OCR text + confidence, answer-key / rubric
version, both agents' verdicts, disagreement, reconciliation, proposal,
confidence, model info) all arrives already carried on `QuestionRow`, so the
audit log never has to re-run an evaluation.

Phase 12 turns the evaluated papers into deliverables: `reporting` reads the
batch driver's results JSONL (`--results`) and produces the plan's three CSVs
(student results, detailed evaluation with per-question agent marks /
confidence / review flag, and the issues log with severity + resolution), an
individual per-student report, a batch report, a JSON export, and the
class-analytics API + ASCII dashboard (totals, average/median/highest/lowest,
question-wise mean and difficulty, review rate, OCR failure rate, agent
disagreement rate). Optional Phase-11 review-store feedback replaces proposed
marks with resolved final marks and keeps unresolved papers `Review` --
reporting derives everything from recorded evidence and never invents a mark.

Phase 13 is the research / benchmarking harness (`research`): a ground-truth
dataset (questions, student answers, human marks, rubrics, question types,
optional OCR images) evaluated by the real AOS pipeline and measured against
the human baseline -- MAE, exact / tolerance agreement, false rejection /
acceptance, semantic acceptance, partial-mark agreement, OCR accuracy, agent
agreement, reconciliation success, fault-recovery success, model-selection
quality, AOS-dynamic-vs-fixed overhead, and batch throughput. It runs the
plan's six research experiments (single vs two-agent, exact vs semantic,
fixed vs AOS dynamic selection, no recovery vs AOS recovery, single-pass vs
fallback OCR, single-model vs capability routing) and writes the deliverables
`benchmark_dataset/`, `evaluation_results.csv`, `experiment_results/` and
`BENCHMARK_REPORT.md`. Golden rules hold: every AI mark is a recorded pipeline
mark or a human review decision -- never invented, never auto-zeroed.
"""

from aos_v0.exam.batch import (
    ANSWER_EXTENSIONS,
    BatchCheckpoint,
    BatchConfig,
    BatchItem,
    BatchReport,
    BatchRunner,
    ItemStatus,
    ProcessedOutcome,
    batch_cli_main,
    discover_answer_sheets,
    load_checkpoint,
    resolve_zip_inputs,
    run_batch,
    save_checkpoint,
)
from aos_v0.exam.benchmark import BENCHMARK_CATEGORIES, load_cases, report, run_benchmark
from aos_v0.exam.confidence import (
    DEFAULT_COMPONENT_WEIGHTS,
    ConfidenceCategory,
    ConfidenceComponent,
    ConfidenceEngine,
    PaperConfidence,
    QuestionConfidence,
)
from aos_v0.exam.config import (
    ConfigIssue,
    ConfigLoadError,
    collect_issues,
    dump_exam_configuration,
    load_exam_configuration,
    main as config_cli_main,
    summarize,
)
from aos_v0.exam.dna import (
    EXAM_DNA_TEMPLATES,
    dna_for_capability,
    dna_for_flag,
)
from aos_v0.exam.evaluate import (
    ConceptMatch,
    ConceptMatcher,
    CriterionResult,
    Disposition,
    EvaluationError,
    EvaluationFlag,
    EvaluationStatus,
    EvaluatorSettings,
    ExamEvaluation,
    LocalSemanticAnalyzer,
    MatchLevel,
    MathEvaluation,
    MathStepResult,
    QuestionEvaluation,
    SemanticAnalyzer,
    SemanticUnavailableError,
    derive_math_key,
    evaluate_question,
    evaluate_rubric,
    evaluate_sheet,
    math_evaluate,
)
from aos_v0.exam.two_agent import (
    AgentEvaluation,
    IndependentVerifier,
    PrimaryEvaluator,
    ReconciliationResult,
    agent_evaluation_from,
    evaluate_two_agent,
    reconcile_agents,
)
from aos_v0.exam.intake import (
    IMAGE_EXTENSIONS,
    Frame,
    IntakeError,
    IntakePage,
    IntakeResult,
    IntakeSummary,
    PageIssue,
    PageMeta,
    PageMetrics,
    PageStatus,
    UnsupportedFormatError,
    cli_main as intake_cli_main,
    ingest,
    sniff_kind,
)
from aos_v0.exam.model_selection import (
    EXAM_MODEL_SELECTION,
    PLAN_CAPABILITY_FLAGS,
    selection_for_capability,
    selection_for_flag,
)
from aos_v0.exam.models import (
    EXAM_CAPABILITY_FLAGS,
    EXAM_REQUIRED_FLAGS,
    AnswerKey,
    ConceptRelationship,
    ConceptRelationshipType,
    EvalReviewReason,
    EvaluationSettings,
    ExamConfiguration,
    ExamEvaluationTask,
    MarkingRules,
    PaperReference,
    PartialCreditRule,
    Question,
    QuestionType,
    Roster,
    RosterEntry,
    Rubric,
    RubricCriterion,
)
from aos_v0.exam.ocr import (
    DeclaredLayoutAdapter,
    DeclaredModelAdapter,
    LayoutAdapter,
    LayoutRegion,
    LocalStructuralAdapter,
    OcrAdapter,
    OcrAdapterAttempt,
    OcrAdapterAttemptStatus,
    OcrBlock,
    OcrBlockType,
    OcrDocument,
    OcrError,
    OcrLine,
    OcrPage,
    OcrRecognitionError,
    OcrResult,
    OcrSettings,
    OcrStatus,
    OcrStrategy,
    OcrUnavailableError,
    PaddleOcrAdapter,
    QwenVlOcrAdapter,
    RegionType,
    RuleLayoutAdapter,
    TrocrHandwrittenAdapter,
    TrocrLargeHandwrittenAdapter,
    TrocrPrintedAdapter,
    confidence_report,
    default_adapters,
    default_layout_adapter,
    document_confidence,
    mean_confidence,
    ocr_cli_main,
    page_confidence,
    run_document,
    run_ocr,
)
from aos_v0.exam.orchestrator import (
    ExamOrchestrator,
    ExamOrchestrationError,
    ExamRunResult,
    ExecutionTrace,
    LocalExamRunner,
    NodeTrace,
    QuestionRow,
    build_exam_registry,
    build_local_exam_manifests,
    dna_for_node,
    register_local_exam_transport,
)
from aos_v0.exam.recovery import (
    CLASS_EVALUATION_DISAGREEMENT,
    EXAM_RECOVERY_TABLE,
    REASONING_REFUSAL,
    RESOURCE_DEGRADED,
    RESOURCE_OUTAGE,
    TOOL_EMPTY_RESULT,
    TOOL_LOW_CONFIDENCE,
    TOOL_OUTPUT_CORRUPT,
    ExamRecoveryManager,
    RecoveryAttempt,
    RecoveryOutcome,
    RecoveryStrategy,
    detect_node_failure,
    disagreement_requires_review,
    disagreements_escalate_to_review,
    gap_marker,
    review_reason_for,
)
from aos_v0.exam.resources import (
    build_exam_manifests,
    exam_resource_ids,
    register_exam_resources,
)
from aos_v0.exam.review import (
    REVIEW_REASONS,
    AuditEvent,
    ReviewDecision,
    ReviewItem,
    ReviewStats,
    ReviewStatus,
    ReviewStore,
    collect_review_items,
    enqueue_batch_results,
    render_card,
    render_queue,
    render_stats,
    review_cli_main,
)
from aos_v0.exam.reporting import (
    ClassAnalytics,
    DetailRow,
    PaperRecord,
    QuestionStat,
    assemble_papers,
    batch_report,
    class_analytics,
    detailed_evaluation_csv,
    export_json,
    export_payload,
    generate_reports,
    issues_csv,
    question_stats,
    read_results,
    render_analytics,
    report_cli_main,
    student_report,
    student_results_csv,
    write_detailed_evaluation,
    write_issues,
    write_student_results,
)
from aos_v0.exam.research import (
    CorpusEntryResult,
    CorpusReport,
    DatasetManifest,
    EVALUATION_CSV_HEADER,
    ExperimentResult,
    GroundTruthEntry,
    agent_agreement,
    batch_throughput,
    build_dataset,
    character_accuracy,
    doc_for_answer,
    evaluate_entry,
    evaluate_entry_with_trace,
    exact_agreement,
    experiment_a_single_vs_two_agent,
    experiment_b_exact_vs_semantic,
    experiment_c_fixed_vs_dynamic,
    experiment_d_no_recovery_vs_recovery,
    experiment_e_single_pass_vs_fallback_ocr,
    experiment_f_single_model_vs_routing,
    evaluation_results_csv,
    false_acceptance,
    false_rejection,
    mean_absolute_error,
    ocr_accuracy,
    partial_mark_agreement,
    read_dataset,
    reconciliation_success,
    recovery_success,
    render_benchmark_report,
    render_experiment,
    run_corpus,
    selection_overhead,
    selection_quality,
    semantic_acceptance,
    task_for,
    tolerance_agreement,
    word_accuracy,
    write_benchmark_report,
    write_corpus_json,
    write_dataset,
    write_evaluation_csv,
    write_experiment_files,
)
from aos_v0.exam.structure import (
    AnswerMapping,
    AnswerSheetEntry,
    MappingIssue,
    MappedBlock,
    SheetStatus,
    StructuringError,
    StructuringSettings,
    StructuredAnswerSheet,
    StudentIdentity,
    cli_main as structure_cli_main,
    extract_student_identity,
    looks_crossed_out,
    map_answers,
    ocr_document_from_json,
    parse_question_label,
    structure,
)

__all__ = [
"BENCHMARK_CATEGORIES",
    "ANSWER_EXTENSIONS",
    "AgentEvaluation",
    "AnswerKey",
    "AnswerMapping",
    "AnswerSheetEntry",
    "AuditEvent",
    "BatchCheckpoint",
    "BatchConfig",
    "BatchItem",
    "BatchReport",
    "BatchRunner",
    "CLASS_EVALUATION_DISAGREEMENT",
    "ClassAnalytics",
    "ConceptRelationship",
    "ConceptRelationshipType",
    "ConceptMatch",
    "ConceptMatcher",
    "ConfidenceCategory",
    "ConfidenceComponent",
    "ConfidenceEngine",
    "ConfigIssue",
    "ConfigLoadError",
    "CriterionResult",
    "DEFAULT_COMPONENT_WEIGHTS",
    "DeclaredLayoutAdapter",
    "DeclaredModelAdapter",
    "DetailRow",
    "Disposition",
    "EXAM_CAPABILITY_FLAGS",
    "EXAM_DNA_TEMPLATES",
    "EXAM_MODEL_SELECTION",
    "EXAM_REQUIRED_FLAGS",
    "EXAM_RECOVERY_TABLE",
    "EvalReviewReason",
    "EvaluationError",
    "EvaluationFlag",
    "EvaluationSettings",
    "EvaluationStatus",
    "EvaluatorSettings",
    "ExamConfiguration",
    "ExamEvaluation",
    "ExamEvaluationTask",
    "ExamOrchestrationError",
    "ExamOrchestrator",
    "ExamRecoveryManager",
    "ExamRunResult",
    "ExecutionTrace",
    "Frame",
"IMAGE_EXTENSIONS",
    "IndependentVerifier",
    "IntakeError",
    "IntakePage",
    "IntakeResult",
    "IntakeSummary",
    "ItemStatus",
"LayoutAdapter",
    "LayoutRegion",
    "LocalExamRunner",
    "LocalSemanticAnalyzer",
    "LocalStructuralAdapter",
    "MappingIssue",
    "MappedBlock",
    "MarkingRules",
    "MatchLevel",
    "MathEvaluation",
    "MathStepResult",
    "NodeTrace",
    "OcrAdapter",
    "OcrAdapterAttempt",
    "OcrAdapterAttemptStatus",
    "OcrBlock",
    "OcrBlockType",
    "OcrDocument",
    "OcrError",
    "OcrLine",
    "OcrPage",
    "OcrRecognitionError",
    "OcrResult",
    "OcrSettings",
    "OcrStatus",
    "OcrStrategy",
    "OcrUnavailableError",
    "PLAN_CAPABILITY_FLAGS",
    "PaddleOcrAdapter",
    "PageIssue",
    "PageMeta",
    "PageMetrics",
    "PageStatus",
    "PaperConfidence",
    "PaperRecord",
    "PaperReference",
    "PartialCreditRule",
    "PrimaryEvaluator",
    "ProcessedOutcome",
    "QwenVlOcrAdapter",
    "Question",
    "QuestionConfidence",
    "QuestionStat",
    "QuestionEvaluation",
    "QuestionType",
    "REASONING_REFUSAL",
    "REVIEW_REASONS",
    "RESOURCE_DEGRADED",
    "RESOURCE_OUTAGE",
    "ReconciliationResult",
    "RecoveryAttempt",
    "RecoveryOutcome",
    "RecoveryStrategy",
    "RegionType",
    "ReviewDecision",
    "ReviewItem",
    "ReviewStats",
    "ReviewStatus",
    "ReviewStore",
    "Roster",
    "RosterEntry",
    "Rubric",
    "RubricCriterion",
    "RuleLayoutAdapter",
    "SemanticAnalyzer",
    "SemanticUnavailableError",
    "SheetStatus",
    "StructuredAnswerSheet",
    "StructuringError",
    "StructuringSettings",
    "StudentIdentity",
    "TOOL_EMPTY_RESULT",
    "TOOL_LOW_CONFIDENCE",
    "TOOL_OUTPUT_CORRUPT",
    "TrocrHandwrittenAdapter",
    "TrocrLargeHandwrittenAdapter",
    "TrocrPrintedAdapter",
    "UnsupportedFormatError",
    "assemble_papers",
    "batch_cli_main",
    "batch_report",
    "build_exam_manifests",
    "class_analytics",
    "collect_issues",
    "collect_review_items",
    "confidence_report",
    "config_cli_main",
    "default_adapters",
    "default_layout_adapter",
    "derive_math_key",
    "detailed_evaluation_csv",
    "detect_node_failure",
    "disagreement_requires_review",
    "disagreements_escalate_to_review",
    "discover_answer_sheets",
    "dna_for_capability",
    "dna_for_flag",
    "document_confidence",
    "dump_exam_configuration",
    "enqueue_batch_results",
    "evaluate_question",
    "evaluate_rubric",
    "evaluate_sheet",
    "exam_resource_ids",
    "export_json",
    "export_payload",
    "extract_student_identity",
    "gap_marker",
    "generate_reports",
    "ingest",
    "intake_cli_main",
    "load_cases",
    "load_checkpoint",
    "load_exam_configuration",
    "looks_crossed_out",
    "map_answers",
    "math_evaluate",
    "mean_confidence",
    "ocr_cli_main",
    "ocr_document_from_json",
    "page_confidence",
    "parse_question_label",
    "register_exam_resources",
    "render_analytics",
    "render_card",
    "render_queue",
    "render_stats",
    "report",
    "report_cli_main",
    "resolve_zip_inputs",
    "review_cli_main",
    "review_reason_for",
    "read_results",
    "run_benchmark",
    "run_batch",
    "run_document",
    "run_ocr",
    "save_checkpoint",
    "selection_for_capability",
    "selection_for_flag",
    "sniff_kind",
    "structure",
    "structure_cli_main",
    "student_report",
    "student_results_csv",
    "summarize",
    "write_detailed_evaluation",
    "write_issues",
    "write_student_results",
    "agent_agreement",
    "batch_throughput",
    "build_dataset",
    "character_accuracy",
    "CorpusEntryResult",
    "CorpusReport",
    "DatasetManifest",
    "doc_for_answer",
    "evaluate_entry",
    "evaluate_entry_with_trace",
    "exact_agreement",
    "EVALUATION_CSV_HEADER",
    "evaluation_results_csv",
    "ExperimentResult",
    "experiment_a_single_vs_two_agent",
    "experiment_b_exact_vs_semantic",
    "experiment_c_fixed_vs_dynamic",
    "experiment_d_no_recovery_vs_recovery",
    "experiment_e_single_pass_vs_fallback_ocr",
    "experiment_f_single_model_vs_routing",
    "false_acceptance",
    "false_rejection",
    "GroundTruthEntry",
    "mean_absolute_error",
    "ocr_accuracy",
    "partial_mark_agreement",
    "read_dataset",
    "reconciliation_success",
    "recovery_success",
    "render_benchmark_report",
    "render_experiment",
    "run_corpus",
    "selection_overhead",
    "selection_quality",
    "semantic_acceptance",
    "task_for",
    "tolerance_agreement",
    "word_accuracy",
    "write_benchmark_report",
    "write_corpus_json",
    "write_dataset",
    "write_evaluation_csv",
    "write_experiment_files",
]