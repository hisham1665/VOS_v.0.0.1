# AOS v1.0.0 — system context for agents

This document is the current implementation-oriented map of AOS. The research
thesis and proposed distributed architecture remain in
`DOC1_Research_Architecture_Specification.md`; this file describes the code
that is actually present in this repository.

## Runtime pipeline

The execution entry point is `aos_v0.prompt.process.run()`:

```text
CLI / InteractiveService
  -> prompt.process.run
  -> ManagerAgent.create_plan
  -> DNAExtractor.extract_graph
  -> ConstraintPolicy.apply
  -> _check_satisfiable
  -> GraphExecutor
       -> CapabilityRegistry.select / exact lookup
       -> SubAgent.perform
       -> FailureManager.execute
  -> IntegratorAgent.integrate
  -> plan, event, session and log artifacts
```

The main stages are:

1. **Input collection** — `cli.py` accepts a prompt plus `--input`, `--image`,
   `--audio`, or the opt-in `--use-inputs-folder` scan. Interactive uploads are
   registered by `ArtifactManager` and passed through a `RequestContext`.
2. **Planning** — `ManagerAgent` asks the configured planner for a DAG, checks
   structure, performs the semantic completeness audit, appends the terminal
   `synthesis` node in code, and writes `data/outputs/plan.md`.
3. **Capability analysis** — `DNAExtractor` fills each node's typed
   `CapabilityDNA`; the kernel-authored synthesis node already has its DNA.
4. **Constraint application** — `ConstraintPolicy` derives cost, latency,
   quality, and risk constraints from the job budget and registry.
5. **Admission** — `_check_satisfiable` rejects a graph only when required flags
   cannot be supplied by any routable resource.
6. **Execution** — `GraphExecutor` dispatches dependency-ready nodes in a
   bounded `ThreadPoolExecutor`. Parent outputs are labeled and merged for
   dependent nodes. Artifact modality is checked before provider invocation.
7. **Node execution** — `SubAgent` binds a resource and hands the invocation to
   `FailureManager`, which detects empty, corrupt, refused, or failed outputs,
   classifies the failure, and walks the bounded recovery ladder.
8. **Integration** — `IntegratorAgent` combines sink-node results. Degraded
   branches remain visible through explicit gap markers.

## Capability DNA and routing

`CapabilityDNA` is a per-subtask requirement vector:

- `flags`: required capability names such as `web.search`,
  `text.summarization`, `vision.understanding`, `document.extraction`, and
  `answer.synthesis`;
- `ordinals`: five 0–4 complexity axes; reasoning depth, planning horizon, and
  tool complexity contribute to normalized demand;
- `constraints`: cost ceiling, latency SLO, minimum quality, and risk tolerance;
- provenance: extractor confidence and the model/strategy that produced the DNA.

`CapabilityRegistry.select()` applies hard gates for routability, availability,
required input modality, risk, cost, latency, quality, and provider cooldown.
It then scores the survivors continuously across flags, reasoning, planning,
tool complexity, cost, latency, and quality:

```text
score = acceptance_rate * pessimising_factor
        - rejection_rate * optimising_factor
```

The result contains the winner, ranked scores, runner-up margin, and rejection
reasons. Tie-breaking is deterministic by `resource_id`. If no resource is
available, `SubAgent` tries a capability-overlap fallback and marks the node
degraded. Nodes without DNA use the exact coarse capability lookup.

## Registered resources

The default registry contains executable resources for:

| Resource | Input | Function |
|---|---|---|
| `web_search` | text | search and evidence extraction |
| `summarization` | text | deep summarization/reasoning |
| `quick_summarization` | text | cheaper summarization/classification |
| `synthesis` | text | final answer generation |
| `vision` | image path | configured vision model |
| `speech_transcription` | audio path | configured transcription model |
| `document_extraction` | document path | local PDF/text extraction |

`build_hf_enabled_registry()` adds the 17-entry Hugging Face catalog only after
the configured token passes its authentication probe. It is an additive pool;
the default pool remains the fallback. Model manifests are provider-neutral to
the kernel. Interfaces marked `transport=declared` are intentionally excluded
from routing because their invocation is not wired to the text pipeline.

## Artifacts and modalities

`ArtifactManager` validates files, creates content-hashed metadata, and stores
interactive copies under `data/artifacts/`. Categories include document, text,
image, audio, video, dataset, archive, and unknown. The execution-compatible
modality is retained on the core `Artifact` model.

Current direct processing support is:

- PDF and text-like documents through deterministic extraction with source/page
  markers;
- images through the configured vision profile;
- audio through the configured transcription profile.

Other artifact categories are retained as context but need a registered parser
or capability before their binary content is processed.

## Shared memory

Shared memory is implemented as an optional bounded kernel service:

- `SharedMemoryBank` stores request-tier entries and can read an attached
  interactive session tier;
- `HeuristicController` performs selective admission and writes optional JSONL
  decision records;
- `MemoryRetriever` exposes all entries as summary keys and injects only
  relevant, bounded values;
- exact reuse uses normalized `(capability, input)` hashes for eligible,
  input-determined capabilities;
- recall counts, eviction, and memory lifecycle events are observable.

The repository does not yet implement the research proposal's learned
controller, semantic subgraph transplantation, distributed stores, or a
benchmark campaign proving the proposed memory targets. Those are future work,
not current runtime guarantees.

## Configuration and observability

`aos.yaml` is validated by `settings.load_settings()`. It configures runtime
limits, memory bounds, model profiles, and operator-defined capabilities. `.env`
holds secrets. `AOS_CONFIG` selects an alternate YAML profile and `AOS_MEMORY`
overrides memory enablement.

The kernel emits `OrchestrationEvent` messages for request lifecycle, capability
analysis, routing, node execution, recovery, results, and shared-memory
operations. The Textual UI is a presentation adapter; it does not plan, route,
or execute tasks.

Generated and operational files:

- `data/outputs/plan.md` — deterministic plan and Mermaid graph;
- `data/outputs/registry_data.json` — registry export;
- `data/artifacts/` — interactive artifact copies;
- `log/` — session logs and optional memory decision/dump files.

See [`CURRENT_STATUS.md`](CURRENT_STATUS.md) for the short operator-facing
status summary and [`CONFIGURATION.md`](CONFIGURATION.md) for the YAML contract.
