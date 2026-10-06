# Medical AOS: foundation audit and patient-continuity improvements

Date: 2026-10-05. Branch: **`feature/aos-patient-continuity`**.

## Main finding

Medical AOS already had a useful capability registry, task DAGs, parallel
workers, actual invocation and modality-specific model routing. Its central
product gap was that **observations and a short impression were being used in
place of a persistent, evidence-linked diagnostic reasoning state**. Model
selection for text was largely fixed, and several internal fallback paths
looked successful despite losing information.

This work adds structured capability/selection contracts and longitudinal
clinical reasoning to the existing orchestration pipeline. Clinical inference
continues to run inside a registered capability. Generic selection, scheduling
and adaptation do not contain disease-specific rules.

Sources for the assessment: repository code, recovered assistant project
histories ([context review](AGENT_CONTEXT_REVIEW.md)), and all ten saved disease
plan/report pairs ([test-run analysis](TEST_RUN_ANALYSIS.md)).

## The 20-component checklist

**Implemented** means there is an executable implementation for the current
contract. **Partial** means useful machinery exists but the broader requirement
is not fully implemented. The scope/limits column is important.

| # | Component | Priority | Before | Now | Implementation / scope and limits |
|---|---|---|---|---|---|
| 1 | Capability DNA | Critical | Partial | Implemented | `core/routing.py`: serializable skills, inputs/outputs, domain, tools, context, memory, cost, latency, priority, enabled state and alternatives. Model-zoo DNA remains medical-specific. |
| 2 | Capability Registry | Critical | Partial | Implemented | `capabilities/registry.py`: live callable registry + metadata; `text_catalog.py`: hot-read configurable model profiles. Runtime health is learned from invocation. External plugin/service discovery remains future work. |
| 3 | Task Understanding | Critical | Partial | Implemented (bounded contract) | `core/task_understanding.py`: normalized intent, actual evidence, missing inputs, live skills, structural acceptance and explicit budgets, shared by fixed/free-form planning. Arbitrary natural-language constraint extraction remains outside this contract. |
| 4 | Capability Matching | Critical | Partial | Implemented | Task requirements match declared skills, input/output contracts, tools and hard constraints. Incompatible alternatives are rejected with reasons. |
| 5 | Dynamic Model/Agent Selection | Critical | Partial | Implemented | Task-aware text profiles, history-aware ranking, availability/cooldown filtering, and equivalent registered capability selection. Fast extraction is distinct from clinical reasoning. Profiles are operator declarations, not automatic proof of model competence. |
| 6 | Multi-Agent Orchestration | Critical | Implemented | Implemented | Dependency-ready DAG, bounded concurrent SubAgents, structured parent artifacts and partial-input joins. |
| 7 | Execution Planning | Important | Implemented | Implemented | Deterministic study template plus DNA-informed free-form JSON planner, validation and bounded plan repair. |
| 8 | Agent Invocation | Critical | Implemented | Implemented | Registry-selected callables invoke local/hosted models and tools through the existing request/result contract. |
| 9 | Result Evaluation | Important | Partial | Partial | Type/empty-output checks, structured JSON validation, source checks, conservative status gates, severity/triage floors and degradation. Source existence is not semantic proof that an argument is medically correct. |
| 10 | Adaptive Replanning | Critical | Partial | Implemented (bounded policy) | `core/replanning.py`: validated runtime DAG insertion of DNA-selected verification and untried equivalent replacements, pending-consumer rewiring, immutable successful work, one recovery level, budgets and original/revised-plan audit. This is explicit recovery policy, not arbitrary autonomous graph generation. |
| 11 | Capability Constraints | Important | Partial | Partial | Generic hard tool/context/memory/cost/latency matching, existing GPU gates and input budgets. Token counts are estimates; several resource/price fields are unknown until configured. |
| 12 | Performance-Aware Selection | Important | Missing | Implemented | Persisted invocation counts, successful evaluations and EWMA latency contribute to ranking. |
| 13 | Fallback Mechanism | Important | Implemented | Implemented | Existing VLM alternatives plus compatible capability/model alternatives and native lab-line salvage. A fallback must satisfy the task/evaluator. |
| 14 | Domain Independence | Critical | Partial | Implemented | Generic DNA/ranking/scheduler have no clinical imports/rules. The free-form planner no longer forces a medical final node for other domains. Offline translation-agent recovery verifies generic execution. |
| 15 | Composability | Important | Implemented | Implemented | Capabilities combine through dependency edges and typed artifacts; workflow templates remain separate from the executor. |
| 16 | Resource Awareness | Advanced | Partial | Partial | Existing VRAM/device/local-vs-hosted routing, GPU semaphore and model LRU; generic declared memory limits. Live compute/load-aware allocation is not present. |
| 17 | Cost Awareness | Advanced | Missing | Partial | Hard declared per-call cost constraints; unknown cost fails a hard budget. Billing/token-price accounting is not implemented. |
| 18 | Reliability Awareness | Advanced | Missing | Implemented | Smoothed successful-invocation/evaluation history and short failed-endpoint cooldowns. This measures execution reliability, not clinical accuracy. |
| 19 | Observability | Important | Partial | Implemented | Candidate eligibility/rejections/scores/history, attempts, chosen capabilities, model selections, plan traces, per-run model provenance and diagnostic-update/raw-response events. |
| 20 | Learning / Adaptation | Advanced | Missing | Partial | Routing aggregates adapt future choices and patient hypotheses update longitudinally. There is no learned orchestration policy or automatic clinical-model retraining. |

**Follow-up priorities 6/7 implemented:** normalized task contracts, bounded
runtime graph recovery, additional extraction/temporal/evidence verification,
all-frame DICOM context, image quality and separate direct-image/report coverage.
See [ADAPTIVE_IMAGING.md](ADAPTIVE_IMAGING.md) and the verified sub-8B
[local vision setup](MODELS.md#local-vision-model-below-8b-rtx-2050--4gb).
Semantic clinical correctness, live billing/load allocation and joint 3D image
interpretation still need separate work; this is not a fully autonomous general AOS.

## Patient conversations now reopen

```bash
python main.py --chat --name patient-001
```

- On the first visit: create the patient session.
- On later visits: reopen the exact same name, replay the transcript, reload
  uploads/index/summary and the persistent clinical assessment.
- Legacy duplicate names: choose the most recently active saved conversation.
  Activity uses precise metadata as well as file timestamps, so coarse Windows
  filesystem timestamps do not select a random duplicate.
- `/resume <id>` chooses a particular older conversation; `/new <name>` explicitly
  creates a new one. Startup does not use fuzzy patient-name matching.
- New report paths use portable separators; older Windows report paths are
  recognized by `/report`.

The existing session folders are retained. No automatic merging of separate
legacy transcripts is performed; those conversations remain selectable by id.

## The integrated reasoning strategy

### Observations → hypotheses → discriminating investigations

`report_synthesis` now returns a `ClinicalAssessment` alongside the impression
and patient status. Each `DiagnosticHypothesis` has:

```json
{
  "condition": "a disease or syndrome",
  "status": "possible|likely|less_likely|ruled_out|confirmed",
  "rationale": "why the combined evidence fits this explanation",
  "supporting_evidence": [{"detail": "source observation", "source_refs": ["labs.pdf#page=1"]}],
  "opposing_evidence": [{"detail": "contradiction and its limits", "source_refs": ["scan.pdf#page=1"]}],
  "missing_information": ["important unanswered question"],
  "suggested_tests": [{"test": "targeted investigation", "purpose": "what it distinguishes", "interpretation": "how results change support"}],
  "exclusion_reason": "documented reason when excluded"
}
```

The assessment also has an integrated summary, unresolved questions, limitations
and evaluator issues. Its prompt asks the model to connect history, lab and
imaging/pathology evidence, cite the sources, and avoid equating extraction
confidence/classifier scores with disease probabilities.

This is part of the existing final capability call, not another serial agent
stage. The report still retains the original detailed observations and reads.
JSON, full markdown, terminal summaries and chat expose the differential.

### Longitudinal state survives summaries and retrieval

`SessionMeta.clinical_assessment` is persisted atomically in `session.json`.
`clinician_updates` retains attributed clinician statements. The state is
included separately in the next context and passed into each new analysis via
`PatientContext.prior_assessment` / `clinician_updates`.

The rolling conversation summary and retrieved passages remain useful, but
they are not the only place where an exclusion lives. Raw files remain indexed;
older model answers remain distinguished from source evidence. Diagnostic
updates are logged in the transcript, and structured follow-up raw responses
are retained for auditing.

### Negative tests are not automatic rule-outs

The evaluator binds citations to existing sources, removes invented references,
and requires an explicit native-source or clinician diagnosis/exclusion before
accepting `confirmed` / `ruled_out`. Generated image prose cannot establish
those statuses by itself. Negative, missing, equivocal or unavailable tests
do not establish exclusion; opposing evidence can instead make a condition
`less_likely`.

An explicit clinician statement can be applied directly to an unambiguous
existing hypothesis, even during a model outage:

```text
Clinician update: bacterial pneumonia ruled out after specialist assessment.
```

The original statement and its binding to the saved hypothesis are recorded.
Questions such as “can pneumonia be ruled out?”, hypothetical statements and
ambiguous condition matches are not applied as clinician decisions.

The evaluator remembers which sources had already been considered. A model
reciting old labs/scans cannot silently resurrect an excluded condition. A new
contradictory supporting source can explicitly reopen it for review. Excluded
conditions remain in history rather than disappearing completely.

Matching handles punctuation and reordered parenthetical qualifiers. General
synonym/abbreviation/ontology resolution remains a recommended improvement.

### Example clinical loop

1. Upload blood results, a scan/report and history into `patient-001`.
2. Read the combined differential and why each possibility fits/does not fit.
3. Select a suggested investigation based on clinical judgment.
4. Attach its result and provide any clinician interpretation.
5. Reassess with earlier evidence and hypotheses included. A non-excluding
   negative result changes support; a documented exclusion moves the condition
   into the historical ruled-out list.
6. Close/reopen `patient-001`; `/differential` shows the same state.

## Core orchestration changes

### DNA and matching

`CapabilityDNA` / `TaskRequirements` in `core/routing.py` are domain-neutral
dataclasses. Built-in capabilities declare their required skills and artifact
contracts in `capabilities/registry.py`. A plugin may register its own DNA and
callable; no scheduler redesign is needed.

`SubAgent` matches requirements against available declarations, records the
candidate decisions and tries at most two compatible capability implementations.
Unrelated skills/output contracts cannot be substituted merely because their
endpoint is faster. Without a compatible alternative, the node fails visibly.

### Model selection and bounded adaptation

Text generation/planning accepts task classes such as `extraction`,
`clinical_reasoning`, `reconciliation` and `planning`.

- Default fast Groq profile: `openai/gpt-oss-20b` for extraction/summary/general
  conversation. Clinical reasoning continues to prefer the configured stronger
  primary (`openai/gpt-oss-120b` by default).
- Both IDs were checked against the account's actual model listing.
- JSON mode plus evaluators reject malformed/empty responses before acceptance.
- Eligible alternatives are ranked using declared policy and local history.
- Recent failures temporarily remove an endpoint from consideration; the next
  decision can use another eligible model.
- `MAOS_TEXT_BACKEND=auto|groq|hf` is honored. HF clinical chat no longer tries
  the BART summarizer as a chat-completion model.
- Ollama availability expires (5s negative, 30s positive), so turning the server
  on after an earlier failure can affect a later analysis.

Add/edit model declarations without restarting via `MAOS_TEXT_MODEL_CATALOG`:

```json
[
  {
    "provider": "groq",
    "model_id": "openai/gpt-oss-20b",
    "skills": ["extraction", "summarization", "conversation"],
    "context_tokens": 128000,
    "expected_latency_s": 2,
    "priority": 8,
    "enabled": true
  }
]
```

Memory/cost/latency fields are optional declarations. Unknown values do not
satisfy a hard constraint. Verify a new checkpoint's actual skills and limits
before declaring it eligible for clinical reasoning.

Telemetry in `.cache/orchestration-performance.json` contains model/capability
identifiers, counts, success rates, latency and cooldowns, not patient text.
These metrics improve routing reliability; they do not establish medical
competence.

### Scheduling and provenance

The executor starts children as soon as their own parents are terminal. A slow
independent document no longer blocks progress on a completed imaging branch.
Dependency checks, partial-input requirements and worker/GPU limits remain.

Executed plans contain selection/attempt traces; report JSON contains per-node
capability and model decisions. Model provenance is assembled from this run's
node artifacts rather than a process-global last-model map from another study.

## Failure and performance fixes

| Change | Reason |
|---|---|
| Define native lab-line fallback | Saved stroke run lost every document to an undefined function. Salvage exact reported lines as uncertain observations. |
| Preserve observations/reads through consolidation | Lung-cancer fallback said “no findings” despite retained abnormal source reads. |
| Deterministic document-only consolidation | Validated page observations need deduplication, not an LLM recopying dozens of lab entries. |
| Preserve normal/negative observations | These discriminate hypotheses and should not disappear behind a generic “normal” finding. |
| Enforce triage floor in integrator | HIGH severity with ROUTINE triage appeared in a saved report. |
| Mark internal synthesis/partial-read failures degraded | A DONE node can still have produced a fallback/incomplete result. |
| Correct document-quality mapping rendering | Source filenames were being printed as quality problems. |
| Page cache independent of question/graph position | New questions should not force rereading unchanged laboratory pages. |
| Model-catalog-aware page/report cache keys | Changed checkpoint policy must not silently reuse an old model's cached result. |
| Balanced source cards | A long early blood report must not crowd the last scan out of the reasoning prompt. Truncation is identified. |
| Reuse final report as upload answer | Removes a duplicate reasoning call while retaining analysis and assistant transcript events. |
| Single owned retry policy | Prevents multiplying explicit retries with hidden Groq SDK retries. |

Live smoke-test timings: **6.49s** initial analysis of a two-document synthetic
case; **0.14s** unchanged cached rerun. The leading differential was supported by
both lab and written imaging evidence, and the explicit clinician exclusion
persisted through reopen. These are fixture-specific timings. Larger archived
cases and other products require equal-input benchmarks before comparing speed.

## Verification

Final offline result: **235 passed in 8.54s** — 31 new regression cases plus
204 legacy cases. Python compilation and the CRLF-aware whitespace check pass.
Verification used an isolated Python environment under `/tmp/omnirush/`.

Run the new checked-in regressions with the project's installed dependencies:

```bash
python -m pytest tests/test_aos_continuity.py -q
```

The full compatibility check in this working environment was:

```bash
PYTHONPATH="$PWD" MAOS_CHAT_EMBEDDINGS=off \
  /tmp/omnirush/maos-venv/bin/python -m pytest \
  tests/test_aos_continuity.py /tmp/omnirush/legacy-maos/tests \
  -q --tb=short --import-mode=importlib \
  -o cache_dir=/tmp/omnirush/pytest-cache
```

- `tests/test_aos_continuity.py`: offline regressions for session/TUI reopening,
  exclusion limits/history, source validation, combined reasoning, native
  fallback, caching, equivalent nonmedical agents, model fallback and scheduling.
- The previously tracked offline suite was extracted from `HEAD` into
  `/tmp/omnirush/legacy-maos/tests` for compatibility checks. Its pre-existing
  working-tree deletions were retained.
- Headless Textual tests verify actual named-session replay.
- Real Groq calls used explicit synthetic text, including the follow-up
  exclusion/reopen scenario. Direct image inference and the ten original source
  input folders were not replayed in this smoke test.

## Recommended next core improvements

1. **General task-understanding contract (#3).** Produce a normalized task with
   intent, input types, required skill/output predicates and user constraints
   before planning. Add domain adapters; evaluate coverage against task-level
   acceptance criteria instead of assigning requirements only from a chosen
   capability's own declaration.
2. **Bounded runtime graph replanning (#10).** Define recovery policies that can
   replace a failed branch, add a verification capability or reroute remaining
   work while preserving successful artifacts. Record why, budget/attempt limits,
   the original/revised DAG and the final outcome. Current recovery changes the
   implementation/model, not arbitrary dependencies.
3. **Semantic output evaluation (#9).** Check whether the cited passage actually
   supports the claimed observation/clinical argument; evaluate differential
   quality and test usefulness with reviewed longitudinal cases. Syntax/source
   checks alone cannot catch a fluent but medically incorrect inference.
4. **Stable clinical identity and temporality.** Introduce controlled condition
   identifiers/aliases, specimen/acquisition dates, value trends and episode
   boundaries. Track negative-test sensitivity/timing/protocol explicitly.
   Filename/source freshness is currently a proxy for new evidence.
5. **A typed artifact contract at every capability edge.** Validate required
   artifact schemas and provenance before accepting a node. Generic text/result
   validation currently supplements richer per-capability validators, not a
   universal typed edge/schema system.
6. **Resource/token/cost accounting (#11/#16/#17).** Use actual tokenizer counts,
   available VRAM/compute load, token prices and request rate limits. Implement
   patient-wide evidence budgets and hierarchical synthesis for very large
   studies instead of relying only on balanced excerpts.
7. **Cancellation and bounded call lifetime.** Python thread timeouts cannot kill
   an already-running native/network operation. Backends need cancellable request
   handles or process isolation; abandoned work can otherwise occupy timeout-pool
   slots even after a node fails.
8. **Benchmark policy and maintain capability health.** Evaluate cold/warm p50/p95
   latency, calls/tokens, retry/fallback overhead and clinical task success on the
   same inputs. Add provider health/model-list discovery and verified capability
   benchmarks; a successful API response is not a clinically successful decision.
