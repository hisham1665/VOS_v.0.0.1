# AOS — Integration Specification for the Exam Evaluator

**Document:** Phase 0 deliverable of
`AOS_Exam_Evaluator_Phase_Wise_Implementation_Plan.md`
**Project:** Adaptive Domain-Independent Multi-AI Agent Orchestration System (AOS)
**Status:** Result of a full source audit of the current AOS implementation.
All component references are to the actual code in `src/aos_v0/`, verified by
reading the modules (not only the README).
**Date:** 2026-09-18

---

## 1. Objective

Phase 0 ("Freeze and Understand Existing AOS") exists to guarantee that the
exam evaluator is integrated **without replacing or changing the current
orchestration algorithms**. This specification documents:

1. The current AOS architecture as implemented.
2. The exact seams a new domain can attach to.
3. The `ExamEvaluationTask` interface (defined in `src/aos_v0/exam/models.py`).
4. The integration contract mapping every plan phase to an AOS seam.
5. The gap register — what the exam domain must add, and where.
6. The regression/acceptance criteria.

**Verdict (from the audit): integration is possible.** The exam evaluator is a
domain built on top of AOS, exactly as the plan's Design Principle 20 requires.
Every requirement maps to an existing AOS mechanism; the gaps are additive
(vocabulary, resources, services), not rewrites.

---

## 2. What is frozen

The following existing AOS functionality must continue to pass its current
tests unchanged:

- AOS Controller / CLI pipeline (`cli.py`)
- Task representation (`core/models.py`)
- Capability Registry (`core/capability_registry.py`)
- Capability DNA (`core/models.py`)
- Dynamic model selection (registry continuous scorer)
- Constraint satisfaction (`core/constraint_policy.py`)
- DAG generation + validation (`agents/manager_agent.py`, `core/graph_utils.py`)
- DAG execution (`agents/graph_executor.py`)
- Agent management (`agents/sub_agent.py`, `agents/integrator_agent.py`)
- Fault recovery (`core/failure_manager.py`)
- Model adapters (`providers/*`, `capabilities/*`)
- Result aggregation, logging, configuration (`cli.py`, `logbook.py`, `config.py`)

Baseline confirmed by running the existing suite:

```
Ran 19 tests in 1.320s
OK
```

---

## 3. Current architecture (as implemented)

### 3.1 High-level flow

The kernel is invoked from `cli.run()` (`src/aos_v0/cli.py`):

1. **Registry construction** — `build_hf_enabled_registry()` (`services/resource_registration.py`). Default pool of 6 resources; HF catalog added only when `HF_TOKEN` authenticates (fail-safe).
2. **Planning** — `ManagerAgent.create_plan()` (`agents/manager_agent.py`): LLM decomposes the prompt into a DAG (schema-constrained tool call), structural validation loop (`validate_graph`), semantic completeness check, then the kernel appends a terminal `synthesis` node wired to every sink.
3. **DNA extraction** — `DNAExtractor.extract_graph()` (`core/dna_extractor.py`): per-node flags + ordinals, cheap-model-first with strong-model escalation on low confidence, offline keyword heuristic as last resort.
4. **Constraint derivation** — `ConstraintPolicy(registry, budget).apply(graph)` (`core/constraint_policy.py`): kernel derives cost ceiling / latency SLO / min quality per node from the job budget and live resource pool.
5. **Admission control seed** — `_check_satisfiable()` (`cli.py`): rejects the plan, naming missing capability flags, before spending.
6. **Execution** — `GraphExecutor.run()` (`agents/graph_executor.py`): topological waves, concurrent thread pool, multi-parent merging, media context propagation. Each node runs via `SubAgent.perform()` (`agents/sub_agent.py`) which routes and calls `FailureManager.execute()` (`core/failure_manager.py`) — detect → classify → recover.
7. **Integration** — `IntegratorAgent.integrate()` returns the synthesis-node output.

```
Prompt → Registry → Plan (DAG) → DNA → Constraints → Admission control
      → Waves (concurrent) → SubAgent (route → FailureManager → resource)
      → Integrator → Output
```

### 3.2 Core data models (`core/models.py`)

| Model | Purpose | Key fields |
|---|---|---|
| `CapabilityDNA` | Per-subtask requirement vector | `flags: List[str]`, `ordinals: DNAOrdinals`, `constraints: DNAConstraints`, `confidence`, `extracted_by` |
| `DNAOrdinals` | 5 axes scored 0–4 | `reasoning_depth`, `planning_horizon`, `tool_complexity`, `memory_dependence`, `parallelizability`; `demand()` = normalised difficulty |
| `DNAConstraints` | Continuous budgets | `cost_ceiling_usd`, `latency_slo_ms`, `min_quality`, `risk_tolerance` |
| `Node` | One DAG task | `id`, `description` (doubles as instruction), `capability` (coarse), `dna`, `depends_on`, `data_inputs`, `bound_resource`, `routing_mode`, `status` |
| `Graph` | The whole job | `job`, `nodes`, `artifacts` |
| `Artifact` | Typed file/output lineage | `id`, `modality` (image/audio/document/text), `path`, `source`, `metadata` |

**Vocabulary constraint (critical).** `CAPABILITY_FLAGS` (models.py:21) is a
closed list. Both `CapabilityDNA.flags` and `CapabilityManifest.capabilities`
are validated against it (models.py:176, capability_registry.py:215). A new
capability therefore requires **extending this one list** — the single
chokepoint through which every exam capability must pass before DNA routing can
use it. Resource *ids* are free-form strings; only flags are constrained.

**Input modality.** `required_input_modality()` (capability_registry.py:145)
maps flag sets to `image|audio|document|None`. The registry hard-excludes
resources whose `input_schema` does not accept the required modality, so an
image node is never fed to a text-only LLM. A `document` modality already
exists for the OCR-style document spine.

### 3.3 Manager Agent / DAG creation (`agents/manager_agent.py`)

- Planner LLM constrained by a JSON-schema `create_graph` tool call.
- **Hard-coded planner capability enum** `_CAPABILITIES` (manager_agent.py:19):
  `web_search, summarization, vision, speech_transcription, audio,
  document_extraction`. The planner may only emit nodes using these. `synthesis`
  is kernel-reserved.
- Structural validation (`validate_graph`, `core/graph_utils.py`): duplicate
  ids, dangling deps, missing roots, cycles.
- Semantic completeness audit: separate LLM call checks every named entity has
  dedicated node(s); graph regenerated when INCOMPLETE.
- Kernel appends the terminal `synthesis` node with kernel-authored DNA
  (`answer.synthesis` flag) so the final writer binds to the synthesis resource.

### 3.4 Capability DNA Extractor (`core/dna_extractor.py`)

- Cheap model `openai/gpt-oss-20b` → strong `openai/gpt-oss-120b`; escalation
  when `confidence < threshold` (0.7). The more confident result wins.
- Emits **flags + ordinals + confidence only**; constraints are kernel-derived
  (never model-invented).
- Keyword-heuristic fallback keeps the pipeline alive when both models fail
  (provenance `extractor=heuristic`, confidence 0.2).
- Kernel-authored DNA (e.g. synthesis node) is skipped.

### 3.5 Capability Registry + Dynamic Model Selection (`core/capability_registry.py`)

- `CapabilityManifest` = a resource's public declaration (DOC1 5.1): resource
  class, `capabilities`, input/output schema, cost model, latency model,
  `quality_priors`, availability, risk class, metadata.
- Registration: `registry.register(manifest, run_fn)`.
- Hard gates before scoring: routability (not a declared-only stub),
  availability (`status == "up"`), input-modality match. **No hard capability
  feasibility filter anymore** — missing flags score as rejection
  contributions, they do not exclude.
- Continuous scorer `score_against_dna()` evaluates 7 dimensions (flag match,
  reasoning, planning, tool, cost, latency, quality):

  ```
  score = acceptance_rate * pessimising_factor
        - rejection_rate  * optimising_factor
  ```

- `select()` returns `SelectionResult` (winner, score, quality, cost, latency,
  full ranked `all_scores`, runner-up + margin) — the ranked list is what the
  Failure Manager walks as its substitution ladder.
- Dynamic model selection is this scorer: models are neither hardcoded in the
  pipeline nor chosen by the planner; the registry picks the best scoring
  resource at execution time per node.

### 3.6 Model registration (`services/resource_registration.py`, `providers/hf.py`)

Two pools:

1. **Default pool** (`build_default_registry`): 6 hand-calibrated resources —
   `web_search`, `summarization`, `vision`, `document_extraction`, `synthesis`,
   `quick_summarization`. Resource ids deliberately match the coarse capability
   strings so one `register()` serves both DNA routing and exact-match fallback.
2. **HF pool** (`build_hf_enabled_registry` + `providers/hf.py`): one resource
   per catalog entry. Chat, ASR, audio-chat, audio-classification interfaces get
   wired run_fns; embedding/rerank are `transport=declared` stubs (unroutable).
   Registration pattern is `build_hf_manifests()` → `register_hf_resources()`.
   Skipped when `HF_TOKEN` is missing/rejected (fail-safe).

**New model = new capability = add a `(CapabilityManifest, run_fn)` pair.** This
is the established extension point.

### 3.7 Graph Executor (`agents/graph_executor.py`)

- `build_waves()` (`core/graph_utils.py`) → topological wave levels; each wave
  runs in a `ThreadPoolExecutor`.
- Input resolution per node: explicit `data_inputs` (artifact ids) → parent
  outputs (multi-parent merge, labeled) → root input (typed media artifact or
  job text).
- Media context propagation: image/audio/document artifacts produced upstream
  are injected as typed context blocks into downstream non-media nodes
  (`[IMAGE IDENTIFICATION]`, `[AUDIO TRANSCRIPTION]`, `[DOCUMENT CONTENT]`).
- Node outputs are registered as derived artifacts.

### 3.8 SubAgent routing (`agents/sub_agent.py`)

Routing ladder (`_route`):

1. **DNA routing** — `registry.select(node.dna, required_modality)`; winner +
   ranked substitutes (modality-guarded).
2. **Relaxed routing** — `InfeasibleDNAError` → best partial flag-overlap
   resource, marked `degraded`.
3. **Exact match** — no DNA flags → `registry.find_by_capability(node.capability)`.

### 3.9 Fault recovery (`core/failure_manager.py`)

Closed loop: **detect** (regex ensemble: empty output, corrupt patterns,
refusals, media-denial, short output) → **classify** (5 classes) → **recover**
(policy table `RECOVERY_TABLE`):

| Failure class | Recovery strategies |
|---|---|
| `resource.outage` | retry_same → resource_substitution |
| `tool.empty_result` | retry_with_feedback → resource_substitution |
| `resource.degraded` | retry_with_feedback → resource_substitution |
| `tool.output_corrupt` | resource_substitution → retry_with_feedback |
| `reasoning.refusal` | retry_with_feedback |

Unrecovered nodes become explicit `[UNAVAILABLE …]` gap markers and are marked
`degraded` — a clean seam for the exam rule "OCR failure must never auto-become
zero marks".

### 3.10 Observability and evaluation layer

- `core/events.py`: typed `OrchestrationEvent` stream (`EventType` enum),
  `EventBus`. Emitted by manager/executor/sub-agent/failure-manager.
- `logbook.py`: session log capture to `log/`.
- `services/evaluation.py`: provider-agnostic benchmark harness —
  `run_case`/`run_case_on`/`benchmark`/`summarize`, normalized `ExecutionMetrics`
  with latency/tokens/success/error-category. Directly reusable for Phase 13.

---

## 4. Interfaces for adding a new domain (the seams)

A domain attaches to AOS through five seams, none of which require changing
scheduler semantics:

| # | Seam | Location | Contract |
|---|---|---|---|
| S1 | **Capability vocabulary** | `core/models.py::CAPABILITY_FLAGS` | Add dotted `family.specific` flags; keep both sides drawing from it |
| S2 | **Resource registration** | registry `register(manifest, run_fn)` | `run_fn(input, instruction=None) -> str`; manifest declares flags/schema/cost/latency/priors |
| S3 | **Planner enum** | `agents/manager_agent.py::_CAPABILITIES` | Add allowed coarse capability strings (+ prompt guidance) |
| S4 | **Task graph** | `core/models.py::Graph/Node` | Domain adapters emit a valid DAG; kernel validates + executes |
| S5 | **Failure taxonomy** | `core/failure_manager.py::RECOVERY_TABLE` | Optional: add failure classes + strategy ladders |

Nothing else needs to change. Media modality (document) already handles scanned
answer-sheet uploads; wave concurrency already expresses two-agent parallelism;
the substitution ladder already expresses OCR/model fallback chains.

---

## 5. Exam evaluation task interface

Defined in `src/aos_v0/exam/models.py` (package `aos_v0.exam`), the Phase 0
deliverable:

| Model | Role |
|---|---|
| `ExamConfiguration` | exam metadata + `questions` + `marking_rules` (unique ids, positive max_marks) |
| `Question` | id, text, max_marks, type, embedded `AnswerKey`, rubric, partial credit (+ rules), negative marking, special rules |
| `AnswerKey` | what knowledge is expected (reference answers, expected concepts, accepted alternatives, keywords, concept relationships) |
| `ConceptRelationship` | typed links among expected concepts (requires/implies/alternative/conflicts) |
| `PartialCreditRule` / `MarkingRules` | per-criterion credit + exam-wide marking defaults |
| `Rubric` / `RubricCriterion` | mark distribution (Phase 2 shape) |
| `PaperReference` | one student's scans (`file_path` or `artifact_id`) |
| `Roster` / `RosterEntry` | institutional roll/name evidence for identity validation |
| `EvaluationSettings` | two-agent toggle, disagreement threshold, confidence bands, auto-review reasons |
| `ExamEvaluationTask` | root contract; `requirement_flags()` + `build_graph()` |

`ExamEvaluationTask.build_graph()` emits a structurally valid AOS DAG per paper:

```
ocr ─ layout ─┬─ student_id ───────────────────────┐
              └─ question_segmentation ─┐           │
                 ├─ eval<Qn>a  (evaluator)  ┐       │
                 ├─ eval<Qn>b  (verifier)    ├─ recon<Qn> ─┬─ report
                 └─ (independent per Qn)     ┘            │
```

Guarantees (covered by `tests/test_exam_task_interface.py`):

- Valid DAG (acyclic, roots, sinks) — `validate_graph`/`build_waves`.
- Two agents per question are structurally independent (same wave, no
  cross-dependency) — the plan's "Agent 2 must not see Agent 1" requirement.
- Reconciliation node depends on both agents.
- Report node is the sink.

Nodes carry coarse capability strings with no inline DNA: `build_graph()` keeps the
skeleton minimal, while the Phase-1 vocabulary addition means the exam flags are
now registered in `CAPABILITY_FLAGS` and the per-capability DNA templates live in
`src/aos_v0/exam/dna.py` (`dna_for_capability`). The graph is the interface, not
a runnable plan, until the execution transports are wired (Phase 3/4, gap G3).

---

## 6. Integration contract (plan phase → AOS seam)

| Plan phase | AOS seam | Action |
|---|---|---|
| Ph1 Model survey | S2 | Survey models; each selected model becomes a `CapabilityManifest` + run_fn (default pool and/or HF catalog). Delivered: `exam/model_selection.py` (initial registry) + `MODEL_SELECTION.md`/`MODEL_BENCHMARK.md` + `model-benchmark/` skeleton |
| Ph1 Capability DNA | S1 | Register exam flags (see §7 gap G1) and DNA definitions. Delivered: 19 flags in `CAPABILITY_FLAGS`; templates in `exam/dna.py`; declared registry entries in `exam/resources.py` |
| Ph2 Answer-key engine | none (domain) | Consumes `ExamConfiguration`/`AnswerKey`/`Rubric`/`MarkingRules` models; pure config, no kernel change. **Done** — answer-key layers + `MarkingRules`/`PartialCreditRule`/`ConceptRelationship` in `exam/models.py`; file-based API + CLI + semantic validation in `exam/config.py`; exemplar config `examples/sample_exam.json`; covered by `tests/test_exam_config.py` (config UI is the CLI; a web UI is deferred to Ph11+) |
| Ph3 Document intake | S4 + `Graph.artifacts` | Batch driver + artifact registration before `cli.run()`; add ingestion capabilities. **Done** — `exam/intake/` pipeline (file validation, page extraction for PDF/images/ZIP/folder, normalization, rotation detection, deskew, quality checks, page ordering), `pypdfium2` added to `requirements.txt`; covered by `tests/test_exam_intake.py` (CLI-based; Ph11+ web UI). Artifact-registration seam above is still pending driver wiring (gap G3) |
| Ph4 OCR/understanding | S2, S5 | New OCR/layout resources; failure classes for low OCR confidence + fallback ladders. **Done** — `exam/ocr/` service: structured OCR schema (plan JSON shape), confidence extraction, OCR model adapters (declared ML adapters bound to the Phase-1 selection — PP-OCRv6/TrOCR/Qwen2.5-VL — plus a runnable local structural engine), rule-based layout analyzer, and the `run_ocr`/`run_document` ladder that routes unreadable pages to human review (`EvalReviewReason.LOW_OCR_CONFIDENCE`, never auto-zero). S5 seam delivered: kernel failure class `tool.low_confidence` + recovery ladder in `failure_manager.py`. Kernel OCR/layout *resource* registration stays gated on the G3 transports (Phase 8); covered by `tests/test_exam_ocr.py` + `tests/test_failure_low_confidence.py` |
| Ph5 Sheet structuring | S2 | `student_id.extraction`, `question.segmentation` capabilities. **Done** — `exam/structure/` turns the Phase-4 OCR document into the plan's answer-sheet record: student identity + roster validation, question-label parser (Q3/Q3(a)/subquestions), out-of-order and continuation-aware answer mapping, page grouping, and the edge-case taxonomy (missing/OCR'd question numbers, multiple attempts, crossed-out, blank, ambiguity). Evidence is never rewritten; each ambiguity surfaces as a `MappingIssue` + `EvalReviewReason` of review routing. Covered by `tests/test_structuring.py`. Kernel `question_segmentation`/`student_id_extraction` resource *registration* stays gated on the G3 transports (Phase 8) |
| Ph6 Semantic engine | S2 | `semantic.answer_evaluation` capability (structured JSON in `run_fn` text contract). **Done** — `exam/evaluate/` semantic evaluation engine: three-level concept matching (surface → meaning → concept correctness) via a runnable local engine (`LocalSemanticAnalyzer` + curated paraphrase lexicon) with declared BGE-M3/Qwen3 analyzers standing in for gap G3; rubric + partial-credit + math step-marking (wrong final value never erases correct intermediate work, `STEP_MARKS_PRESERVED`, indeterminate finals route to `MATHEMATICAL_UNCERTAINTY` review); never auto-zero (low-confidence entries are `UNSCORED`); lower builds of plan-shaped evidence JSON deliverable via `--json`. Engine: `evaluate_sheet`/`evaluate_question`; CLI `python -m aos_v0.exam.evaluate`; covered by `tests/test_evaluation.py`. S2/S4 *resource registration* stays gated on the G3 transports (Phase 8) |
| Ph7 Two agents | S4 | DAG already provides independent parallel agents + merge node (`build_graph`). **Done** — `exam/two_agent.py` adds the runnable two-agent loop over the Phase-6 engine: `PrimaryEvaluator` (Agent 1, `semantic_answer_evaluation`) and `IndependentVerifier` (Agent 2, `answer_verification`) evaluate the same evidence with structurally independent acceptance policies (the verifier never receives Agent 1's verdict), both emitting the plan's structured surface (marks/concepts_satisfied/missing_concepts/reasoning/confidence); `reconcile_agents`/`evaluate_two_agent` perform the disagreement analysis -- agreed marks adopted, small divergence confidence-weighted, large divergence → `EvalReviewReason.AGENT_DISAGREEMENT` review with the mark confidence-gated (never averaged blindly, never auto-zero). Covered by `tests/test_two_agent.py` |
| Ph8 Dynamic orchestration | S1–S5 | Register all exam capabilities; let registry route per node. **Done** — `exam/orchestrator.py` adds the plan-shape adapter over the kernel Capability Registry: `ExamEvaluationTask.build_graph()` emits the DAG, `dna_for_node` hydrates each node's Capability DNA, `register_local_exam_transport`/`build_local_exam_manifests` re-register the eight requirement resources with runnable run_fns (gap G3 closed), `LocalExamRunner` provides the local structural engine, and `ExamOrchestrator.execute` selects a model per node via the registry's continuous DNA scorer and runs the wave-ordered DAG end to end (OCR → layout → student id → question segmentation → primary eval → independent verify → reconciliation → report), returning `ExamRunResult` with a full `ExecutionTrace` of the dynamic selection. Covered by `tests/test_orchestration.py`. Real PaddleOCR/Transformers/vLLM transports remain a deployment-time model choice (same run_fn contract) |
| Ph9 Fault recovery + confidence | S5 | New failure classes (`quality.low_confidence`), `confidence.estimation` capability. **Done** — `exam/recovery.py` adds the exam-side closed loop over the kernel failure taxonomy: `detect_node_failure` classifies a node's output into kernel classes (`resource.outage`, `resource.degraded`, `tool.empty_result`, `tool.output_corrupt`, `tool.low_confidence`, `reasoning.refusal`) plus S5's `evaluation.disagreement`; `EXAM_RECOVERY_TABLE` maps each class to a S5-style policy ladder (retry same / retry with feedback / resource substitution) that every ladder terminates in `ESCALATE_TO_REVIEW` — never a guess, never an auto-zero. `ExamRecoveryManager.run_node` drives the ladder with registry-ranked resource substitution and an honest degraded gap marker (no fabricated output keys); exhausted structural stages (OCR/layout/student-id/segmentation/reconciliation/report) route the whole paper to human review, exhausted per-question eval/verifier ladders route just that question (`RECOVERY_FAILED`/`LOW_OCR_CONFIDENCE` reasons, marks withheld). `exam/confidence.py` adds the `confidence.estimation` engine: OCR / answer-extraction / semantic / rubric / agent-agreement components weighted into an overall score and a HIGH/MEDIUM/LOW category (weakest-reading rule, thresholds from `EvaluationSettings.confidence_high`/`confidence_low`; confidence is not correctness). `ExamOrchestrator.execute` runs every DAG node under recovery and reports per-node outcomes in `NodeTrace` plus paper-level `ExamRunResult.confidence_evidence`/`confidence_category`/`recovery`/`escalations`. Covered by `tests/test_recovery.py` + `tests/test_confidence.py` + orchestration recovery tests |
| Ph10 Mass evaluation | new domain driver | Batch driver loops the kernel per paper; checkpoint/resume data lives in the driver, not the kernel. **Done** — `exam/batch/` delivers the mass-evaluation engine entirely in the driver: `discovery` validates a folder / ZIP / single sheet into pending items (extension + magic-byte checks, junk removal, natural ordering); `BatchRunner` runs the parallel worker pool (configurable workers, per-paper retry, timeout, failure isolation -- paper 37 failing never restarts or blocks papers 1-36 and 38-100), writes durable atomic checkpoints after every completed paper, and resumes by re-grading only unfinished papers (completed papers are never re-graded). `BatchReport` is the per-document status API (totals, progress, per-item status/review-reasons/marks/confidence). The kernel never learns that a batch exists. Covered by `tests/test_batch.py` |
| Ph11 Human review + audit | new domain service | Persistent per-question record store + review queue (reuse Artifact/event patterns). **Done** — `exam/review/` is the human side of uncertainty: the orchestrator's `QuestionRow` now carries the full evidence trail per question (answer text, OCR confidence, both agents' proposed verdicts) so the audit log never re-runs an evaluation. `collect_review_items` turns an `ExamRunResult` into the plan's "REVIEW REQUIRED" cards — one per flagged question plus a paper-level card for reasons no single row owns (identity_uncertain / low_ocr_confidence / missing_page). `ReviewStore` persists the queue (`review_queue.json`, atomic writes) plus an append-only audit log (`audit.jsonl`, ENQUEUE/REENQUEUE/RESOLVE events with actor + payload). `render_card`/`render_queue` draw the ASCII dashboard; `resolve(accept|modify|escalate)` is the only path from *proposal* to *final mark* (ACCEPT adopts the proposal, MODIFY clamps to `[0, max_marks]`, ESCALATE leaves it empty) and every decision lands in the audit log. CLI: `python -m aos_v0.exam.review <dir> list|show|resolve|history|stats`. Covered by `tests/test_review.py` |
| Ph12 Reports/CSV | new domain service | CSV/report capabilities (already in `EXAM_CAPABILITY_FLAGS`). **Done** — `exam/reporting/` turns the batch driver's results JSONL (`--results`) into the plan's deliverables: `student_results.csv` (one row per student, one column per question, total/percentage/status), `detailed_evaluation.csv` (per-question max marks, Agent 1/2 marks, final marks, confidence, review flag), `issues.csv` (issue, severity high/medium, resolution `Human Review`); plus individual per-student reports, a batch report, a JSON export, and the analytics API + ASCII dashboard (`class_analytics`: total/average/median/highest/lowest, question-wise mean + difficulty, review / OCR-failure / agent-disagreement rates). Phase-11 review-store feedback replaces proposed marks with resolved final marks and keeps unresolved papers `Review` — reporting derives everything from recorded evidence (never re-runs, never invents a mark). CLI: `python -m aos_v0.exam.reporting <exam.json> <results.jsonl> [--review-dir DIR]`. Covered by `tests/test_reporting.py` |
| Ph13 Benchmarking | `services/evaluation.py` | Done -- `exam/research` harness reuses the kernel evaluation layer (Phase 1 `benchmark.py` + full AOS pipeline); ground-truth dataset (`benchmark_dataset/`), `evaluation_results.csv`, `experiment_results/`, `BENCHMARK_REPORT.md`; batch throughput measured at driver level |
| Ph14 Hardening | new domain service | Auth/RBAC/storage on the API layer, not the kernel |

---

## 7. Gap register

| Id | Gap | Location | Action | Effort |
|---|---|---|---|---|
| G1 | Exam flags not in `CAPABILITY_FLAGS` | `models.py` | Add `EXAM_CAPABILITY_FLAGS` items (Phase 1) | **Done** — 19 exam flags added to the vocabulary (Phase 1); exam DNA templates in `src/aos_v0/exam/dna.py` validate against it |
| G2 | Planner enum closed at 6 capabilities | `manager_agent.py:19` | Extend `_CAPABILITIES` + system prompt for exam DAG | Low |
| G3 | No true OCR/layout resources | `capabilities/*`, `exam/ocr/adapters.py`, `exam/orchestrator.py` | New run_fns + manifests; HF catalog entry interface types. **Done** — Phase 4 delivered the adapter interface, declared adapters and the runnable local engine; Phase 8 (`register_local_exam_transport` + `build_local_exam_manifests`) re-registers all eight exam resources with `transport="wired"` runnable run_fns (runnable `LocalExamRunner` step per capability), closing the transport gap while preserving the declared-only contract of `register_exam_resources` (still asserted by `tests/test_exam_phase1.py`). Real PaddleOCR/Transformers/vLLM transports can be slotted into the same run_fn contract at deployment | Medium |
| G4 | Structured evaluation output (marks/concepts/confidence) | `run_fn` contract | Serialize as JSON-in-text; deterministic parse; keep `(input, instruction) -> str` | Low-Med |
| G5 | No batch/queue/checkpoint | absent | Domain driver over the kernel | Medium |
| G6 | No persistent audit/review store | absent | Domain service | Medium |
| G7 | Evaluation-confidence engine | absent | New capability + deterministic aggregation in recon node | Low-Med |
| G8 | Context-window / multilingual constraints | `models.py::DNAConstraints` | Extend schema (optional, Ph8) | Low |
| G9 | **Environment** | editable install pointed at a different repo copy (`VOS_v.0.0.1`) | Reinstall workspace as editable (done during Phase 0) | Done |

---

## 8. Non-negotiable rules inherited by the exam domain

1. Capability routing, not hardcoded pipelines (registry decides).
2. Confidence is **not** correctness; low confidence → human review, never silent override.
3. OCR failure must never become automatic zero marks (gap markers already exist).
4. Agent 1 and Agent 2 remain independent (parallel DAG nodes).
5. Every mark decision carries evidence (node outputs are preserved as artifacts).
6. Answer keys/rubrics are configuration, never produced by evaluation models.

---

## 9. Acceptance criteria (Phase 0 exit)

- [x] Existing architecture audited and documented (this document).
- [x] Interfaces for adding a domain identified (§4 seams).
- [x] Capability DNA, Registry, model registration, dynamic selection, DAG
      creation, execution and failure handling documented (§3).
- [x] `ExamEvaluationTask` defined (`src/aos_v0/exam/models.py`).
- [x] Integration tests added without changing existing behavior
      (`tests/test_exam_task_interface.py`).
- [x] All existing AOS functionality continues to pass its tests
      (19 existing + 12 new = 31 OK).

---

## Appendix A — Regression status

```
Ran 31 tests in 1.209s
OK
```

- 19 pre-existing tests (artifacts, document, event integration, interactive)
  unchanged and passing.
- 12 new Phase-0 interface tests in `tests/test_exam_task_interface.py`.

## Appendix B — Environment note

The user-site editable installation previously resolved `aos_v0` to a separate
copy at `/home/hisham/VOS_v.0.0.1/src`. Phase 0 reinstalled this workspace as
editable (`pip install --user --break-system-packages -e .`), so `import aos_v0`
now resolves to `/home/hisham/eval/src`. Bare `python3` on this machine is
PEP 668 managed; `--break-system-packages` matches the pre-existing install
pattern.