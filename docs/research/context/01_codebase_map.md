# Codebase map

## 1. Top-level layout

```text
medical_aos/
├── main.py                 CLI entry point and exit-code behavior
├── pipeline.py             fixed pipeline sequencing and preflight
├── models.py               Pydantic domain and orchestration contracts
├── config.py               environment-backed model/runtime settings
├── graph_utils.py          DAG validation, waves, sinks, descendants
├── evaluate_images.py      operational evidence-coverage evaluation
├── agents/                 planning, execution, recovery, integration
├── capabilities/           registered model/tool capabilities
├── ingest/                 files -> Study, DICOM, documents, preprocessing
├── core/                   routing, cache, retry, evidence, reasoning, logs
├── reporting/              RadiologyReport -> Markdown and JSON
├── chat/                   persistent sessions, memory, attachment parsing
├── tui/                    Textual terminal interface
├── tests/                  offline regressions and saved execution artifacts
└── docs/                   operational and research documentation
```

## 2. Entry points and control flow

### `main.py`

`build_parser()` exposes study, history, referring question, output directory,
literature, JSON, doctor/preflight, cache clearing, free-form, chat, resume,

1. `--doctor` -> `pipeline.preflight()`;
2. `--chat`/`--resume` -> the Textual application;
3. `--ask` -> `pipeline.run_freeform()`;
4. `--study` -> `pipeline.analyze_study()`.

The study CLI returns `3` when the final triage is `immediate`, `0` for a
normal completed invocation, `2` for missing CLI input, and `1` for a handled
application error. The exit-code triage signal is useful to an external alerting
wrapper but does not replace review of the report.

### `pipeline.py`

`analyze_study()` sequences five implementation stages:

1. ingest with `load_study()`;
2. deterministic plan construction with `build_radiology_plan()`;
3. graph execution with `GraphExecutor().run()`;
4. typed integration with `IntegratorAgent().integrate()`; and
5. Markdown/JSON rendering with `write_report()`.

The README describes seven clinical stages—ingest, triage, screen, confirm,
read, consolidate, report—because triage/screen/confirm/read are internal nodes
within the execution stage. The two descriptions are consistent at different
levels of abstraction.

## 3. `ingest/`: input normalization

| Module | Responsibility |
|---|---|
| `study_loader.py` | Recursively walks the study, separates image/document files, absorbs pure patient sidecars, deduplicates by content hash, loads inputs concurrently, records skipped files, and assembles series |
| `document_loader.py` | Bounded text, PDF, DOCX, and XLSX extraction; preserves physical page/chunk boundaries and rendered PDF page images; warns on truncation, missing text, unsupported embedded content, and archive risks |
| `dicom_utils.py` | Reads DICOM headers and pixels, applies modality LUT/windowing, handles MONOCHROME1, color and multi-frame data, hashes UIDs when de-identification is enabled, and strips direct PHI tags |
| `preprocess.py` | Converts images to model-ready RGB JPEG, assesses technical pixel quality, resizes to the configured edge, and caches prepared bytes plus original dimensions/quality metadata |
| `series.py` | Groups images by acquisition identity/orientation/region, orders them by patient-space geometry or explicit fallback, detects irregular/repeated positions, marks partial coverage, and performs even sampling when configured |

The output is a `Study`, not a loose list of files. Every image/document has a
stable local identifier, content hash, source path, metadata, warnings, and
analysis status that can later appear in the report.

## 4. `agents/`: orchestration

| Module | Responsibility |
|---|---|
| `planner.py` | Builds the fixed radiology DAG and task-understanding contract; assigns exact image/document IDs; creates acceptance criteria |
| `manager_agent.py` | Uses an LLM only for genuinely open-ended jobs; validates capabilities and graph shape; renders `plan.md` and Mermaid |
| `graph_executor.py` | Validates the graph, assembles parent text/artifacts, starts dependency-ready tasks in one bounded pool, applies budgets, and invokes runtime replanning |
| `sub_agent.py` | Resolves a capability and compatible alternatives, applies per-node timeout/retry boundaries, validates `CapabilityResult`, records attempts, and leaves a terminal node state |
| `integrator_agent.py` | Merges typed artifacts into `RadiologyReport`, derives findings/status/coverage/provenance, and marks degradation |

The executor is intentionally domain-agnostic. It does not know how a chest
classifier works; it only knows how to satisfy a node's declared contract.

## 5. `capabilities/`: model and tool boundary

Capabilities are registered through `capabilities.registry.register()`. They all
accept `CapabilityRequest` and return `CapabilityResult`. This is the only layer
that directly calls vision/text models or external search services.

| Capability | Role |
|---|---|
| `image_quality` | Technical pixel and series/coverage checks |
| `modality_triage` | General non-medical gate plus PubMedCLIP modality/region routing |
| `image_classification` | Stage-1 modality/region-matched screening |
| `specialist_confirmation` | Stage-2 candidate and question-driven specialist checks |
| `vision` | Per-image narrative VLM interpretation |
| `document_analysis` | Page-level structured extraction with exact page binding |
| `cross_image_synthesis` | Reconciliation/deduplication of image, document, and cascade evidence |
| `report_synthesis` | Final status, impression, differential, tests, and next steps |
| `web_search` | De-identified literature background from finding labels |
| `summarization` | Text summarization fallback/utility |
| `document_verification` | Rendered-page or native-text extraction checks |
| `temporal_comparison` | Bounded comparison of repeated numeric observations |
| `evidence_verification` | Source-reference and exact-number audit |

`registry.py` lazily imports built-ins under a lock so worker threads cannot see
an empty partially initialized registry. `model_zoo.py` is the medical-specific
catalog for supervised screening models; `hf_backend.py` owns local/hosted text
and image model routing, model caches, GPU limits, and telemetry.

## 6. `core/`: reusable control and validation

| Module | Responsibility |
|---|---|
| `routing.py` | Domain-neutral capability DNA, task requirements, hard resource matching, performance-aware ranking, cooldowns, and persisted aggregate telemetry |
| `task_understanding.py` | Normalizes intent, evidence, missing inputs, acceptance criteria, and budgets; evaluates structural coverage |
| `replanning.py` | Inserts bounded verification/replacement nodes after structured requests; preserves completed work and records original/revised plans |
| `clinical_reasoning.py` | Validates source-linked evidence, prevents unsupported numbers, preserves explicit exclusions, and maintains the differential across sessions |
| `evidence_checks.py` | Exact numeric-token comparison against native source text; deliberately not semantic entailment |
| `execution_context.py` | Cooperative call deadlines for node work and retries |
| `cache.py` | SHA-256/content-addressed JSON and byte cache with per-key locks and atomic writes |
| `retry.py` | Exponential backoff with transient/permanent error classification |
| `errors.py` | Typed application/backend/planning/capability errors |
| `logging_utils.py` | Structured logging, stage timers, and session log integration |
| `image_evaluation.py` | Separates direct-image and written-report operational coverage groups |

## 7. `reporting/`: output boundary

`report_builder.py` renders a single structured report in two forms:

- Markdown ordered for human triage: disclaimer, status, red flags, assessment,
  findings, evidence, coverage, warnings, diagnostics, models, and provenance.
- JSON for services, EHR/PACS adapters, auditing, replay, and machine analysis.

The disclaimer is emitted unconditionally in both forms. It is part of the
report contract, not an optional UI decoration.

## 8. `chat/` and `tui/`

`chat/` is the UI-independent continuity layer. `ChatEngine` parses attachments,
copies files into a session, invokes the same pipeline, indexes outputs, builds a
budgeted context, and persists diagnostic state. `SessionStore` owns folders,
atomic metadata, transcripts, uploaded-file hashes, reports, and logs.

`tui/` is a Textual presentation layer. It runs engine work away from the UI
thread and renders progress/events; the engine remains testable without a
terminal UI.

## 9. Dependency direction

The intended dependency rule is:

```text
CLI / pipeline / chat / reporting
              ↓
           agents
              ↓
          registry
              ↓
       capabilities + core
              ↓
             ingest
```

In practice, `capabilities` imports selected `core` utilities and `models`, but
the executor does not import individual model implementations. New capabilities
can be registered without redesigning the scheduler. Medical rules stay in
medical capabilities and the model zoo; generic routing and scheduling remain
domain-neutral.
