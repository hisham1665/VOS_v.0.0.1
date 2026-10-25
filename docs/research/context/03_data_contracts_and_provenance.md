# Data contracts and provenance

Medical AOS uses Pydantic models for the main clinical and orchestration state.
The purpose is not only validation: a complete run can be serialized, audited,
replayed, handed to an integration, or inspected after partial failure.

## 1. Orchestration models

### `TaskBudget`

`TaskBudget` contains hard execution limits:

```text
max_replans
max_added_nodes
max_attempts_per_node
max_elapsed_s
max_cost
max_memory_gb
```

Unknown declared cost or memory cannot satisfy a hard constraint. Reservations are
based on declared capability costs, not retrospective provider billing.

### `TaskEvidence`, `AcceptanceCriterion`, and `TaskUnderstanding`

These models convert a natural-language job into an explicit task contract:

- source ID and evidence kind;
- source availability and limitations;
- missing evidence;
- required live-registry skills;
- output/source coverage criteria; and
- execution budget.

Acceptance is structural. For example, the system can verify that every assigned
image has a read artifact and that a `clinical_assessment` artifact exists. It
cannot establish from this check that a medical inference is correct.

### `Node`

A node stores:

- stable ID, description, capability, and dependencies;
- optional/partial dependency policy;
- assigned image/document IDs;
- status: `pending`, `running`, `done`, `failed`, or `skipped`;
- prose output and structured artifacts;
- selected implementation/model metadata;
- error and duration; and
- adaptation/retry trace.

The distinction between `failed` and `skipped` is important. A node can be
skipped because an upstream strict dependency failed, while the upstream node
contains the actual error.

### `Graph`

`Graph` stores the job and nodes plus:

- whether the plan is deterministic;
- the normalized task contract;
- `original_plan` before execution;
- `revisions` added at runtime; and
- `acceptance_results` after execution.

The report therefore contains both intended and revised execution state.

## 2. Clinical input models

### `StudyImage`

An image carries:

```text
image_id, source_path, prepared_path
content_hash, prepared_hash
modality, body_part, view
width, height, is_dicom
series_id, frame_index, series_order
quality, metadata, warnings
```

`content_hash` identifies source bytes. `prepared_hash` identifies the actual
model pixels after decoding/windowing/resizing. Inference cache keys use the
prepared representation so a changed JPEG quality, window, or prepared pixel
set cannot reuse an old model read.

### `DocumentPage` and `StudyDocument`

Every document page/chunk has a 1-based page number, extracted text, optional
rendered image, extraction method, and warnings. The `StudyDocument` adds a
stable document ID, source path, content hash, format, page count, and document
warnings.

The canonical source reference is:

```text
<document_id>#page=<page_number>
```

Model-provided references are not trusted. The requesting capability binds the
source reference itself, preventing a model from citing a page that was not
actually supplied.

### `PatientContext`

Patient context contains optional age, sex, history, referring question, prior
study labels, notes, prior assessment, and clinician updates. It is carried into
prompts only when relevant, while the literature query is constructed from
finding labels rather than patient identity or history.

### `StudySeries`

Series context records scanner/series identity when available, modality, region,
description, image IDs, acquired count, ordering method, coverage (`unknown` or
`partial`), and warnings. A complete acquisition is never inferred merely from
the files supplied by the caller.

## 3. Finding and report models

### `Finding`

Each finding has:

- label and description;
- severity and optional confidence;
- image IDs and/or document IDs;
- exact source references;
- source class (`vlm`, `document_analysis`, `classifier`, `literature`, or
  `manual`);
- evidence kind (`direct_image`, `document`, `mixed`, or unspecified); and
- optional body region.

Evidence kind is computed from actual source fields. A written CT report is not
counted as a direct CT image read.

### `PatientStatus`

The headline status contains overall severity, summary, triage, red flags, and
recommended actions. Triage is a bounded urgency label, not a diagnosis.

### `ClinicalAssessment`

The longitudinal reasoning object contains:

```text
integrated_summary
limitations
validation_issues
```

Each `DiagnosticHypothesis` contains condition, status, rationale, supporting and
opposing `EvidenceLink`s, missing information, suggested tests, exclusion reason,
and `considered_sources`.

Allowed statuses are:

```text
possible | likely | less_likely | ruled_out | confirmed
```

The evaluator applies conservative status rules:

- a generated image sentence cannot alone establish `confirmed` or `ruled_out`;
- a negative test normally lowers support but does not prove exclusion;
- a rule-out requires explicit documentation in an authoritative source or an
  unambiguous clinician update;
- unsupported numeric claims are removed; and
- invented/unknown source references are removed.

## 4. Report provenance

`RadiologyReport` retains the following provenance planes:

### Input plane

- `input_manifest` lists supplied images/documents/skipped files;
- `image_count`, `document_count`, modalities, and series describe scope;
- `image_quality`, warnings, and series coverage expose technical limitations.

### Evidence plane

- `image_reads` contains direct image interpretations;
- `source_reads` contains document-page outputs;
- `findings` contains source-bound observations;
- `screening` contains stage-1/stage-2 per-image results;
- `verification` contains additional checks; and
- `evaluation_groups` separates direct images from written reports.

### Execution plane

- `task_understanding` stores the normalized job contract;
- `original_plan` and `plan_revisions` record planning/adaptation;
- `acceptance_results` records structural coverage;
- `node_trace` records capability, status, duration, selected implementation,
  model-selection trace, and error; and
- `models_used` records models actually used during this run.

### Clinical output plane

- indication and technique;
- findings and impression;
- status and triage;
- references;
- structured differential; and
- recommended next steps.

## 5. Artifact propagation

Artifacts move along graph edges and are merged deterministically. Examples:

```text
image_quality -> image_quality
modality_triage -> triage, non_medical
image_classification -> screening, classification, flagged
specialist_confirmation -> confirmation
vision -> reads, read_errors, image_quality
document_analysis -> document_reads, document_types, findings, quality_issues
cross_image_synthesis -> findings, discrepancies, verification_requests
report_synthesis -> status, impression, clinical_assessment
```

Private orchestration keys begin with `_` and are excluded from downstream
clinical artifact merging. This prevents selection traces, cost reservations,
and internal controls from being mistaken for evidence.

## 6. What provenance proves and does not prove

Provenance can prove that:

- a claim was linked to a supplied image/page ID;
- a model or capability produced an artifact;
- a node succeeded, failed, or was skipped;
- a source was not available or was only partially covered;
- a report was degraded; and
- a numeric token occurred in the native source text.

Provenance alone cannot prove:

- that the source itself is clinically correct;
- that a model's visual interpretation is accurate;
- that two models are independent in their training data;
- that a negative test excludes a condition; or
- that a supplied series is a complete acquisition.

Those distinctions should be explicit in a research methods and limitations
section.
