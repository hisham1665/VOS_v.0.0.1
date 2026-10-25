# AOS v1.0.0 current implementation

**Status:** current runtime reference. This document describes the code in the
repository as of the `v1.0.0` release. The research specification and roadmap
documents in this directory are historical design references, not promises that
every proposed subsystem is already present.

## What runs today

AOS is a single-process Python 3.11+ orchestration kernel. A request follows
this path:

```text
CLI / InteractiveService
        |
        v
prompt.process.run
        |
        +--> ManagerAgent.create_plan       -> validated DAG + terminal synthesis node
        +--> DNAExtractor.extract_graph     -> per-node Capability DNA
        +--> ConstraintPolicy.apply         -> kernel-owned constraints
        +--> satisfiability check
        +--> GraphExecutor                  -> dependency-ready concurrent execution
        |       +--> CapabilityRegistry     -> resource binding
        |       +--> SubAgent               -> one node invocation
        |       +--> FailureManager          -> detect, classify, recover
        |       +--> optional shared memory
        +--> IntegratorAgent                -> final answer from sink outputs
        +--> plan and log artifacts
```

The planner is asked only for the work graph. The kernel appends the terminal
`synthesis` node itself, so the final answer still receives the user's original
request rather than a planner paraphrase.

## Current resource pools

`build_default_registry()` registers these executable resources:

| Resource | Input | Role |
|---|---|---|
| `web_search` | text | web search plus evidence extraction |
| `summarization` | text | deep summarization and reasoning |
| `quick_summarization` | text | cheaper, faster summarization/classification |
| `synthesis` | text | final answer generation |
| `vision` | image path | image understanding through the configured vision profile |
| `speech_transcription` | audio path | speech-to-text through the configured audio profile |
| `document_extraction` | document path | deterministic PDF/text extraction |

The optional Hugging Face catalog is added by
`build_hf_enabled_registry()` only when `HF_TOKEN` (or the legacy `HUG`) is
valid. It contributes 17 catalog manifests. Chat completion, ASR, audio-chat,
and audio-classification interfaces are wired in the adapter; embedding,
reranking, image classification, object detection, and image-feature entries
remain declared interfaces that fail explicitly if invoked. The default pool is
still available when the Hugging Face token is absent or rejected.

## Routing behavior

For a node with DNA, `CapabilityRegistry.select()` first excludes resources that
are not routable, unavailable, modality-incompatible, over the node's risk,
cost, or latency limits, below its quality floor, or in a provider cooldown.
The remaining resources are ranked by the continuous DNA score:

```text
score = acceptance_rate * pessimising_factor
        - rejection_rate * optimising_factor
```

The score records per-dimension contributions for flags, reasoning, planning,
tool complexity, cost, latency, and quality. Ties are resolved by
`resource_id`. If no resource passes the gates, `SubAgent` attempts a
capability-overlap fallback and marks the node degraded so the final answer can
surface the gap. Nodes without DNA use the exact coarse capability lookup.

## Input and artifact behavior

The CLI accepts `--input PATH` (extension-detected), `--image PATH`, and
`--audio PATH`; `--use-inputs-folder` enables the optional scan of
`data/inputs/`. The interactive service stores uploaded files as content-hashed
artifacts under `data/artifacts/` and tracks their category, MIME type, size,
path, and metadata.

Built-in execution support is:

- PDF and text-like documents: deterministic extraction with source/page
  markers;
- images: the configured vision model;
- audio: the configured transcription model;
- web search: the configured search/provider path.

Video, datasets, archives, and other retained artifact categories need a
registered parser/capability before their binary contents can be processed.

## Shared memory

Memory is enabled by default through `aos.yaml` and can be disabled with
`AOS_MEMORY=off` or the CLI flag `--no-memory`. It has:

- a bounded request tier and an optional interactive session tier;
- deterministic summary keys and key-only retrieval;
- selective heuristic admission with decision logging;
- exact reuse keyed by normalized `(capability, input)` for
  `web_search`, `document_extraction`, `speech_transcription`, and `vision`;
- bounded context injection, recall counters, eviction, and orchestration
  events.

The implementation does **not** include a learned admission controller,
semantic subgraph transplantation, or benchmark results proving the targets in
`SHARED_MEMORY_PLAN.md`. Those remain future evaluation work.

## Configuration source of truth

- `aos.yaml`: validated runtime limits, memory policy, model profiles, and
  operator-registered capabilities;
- `.env`: API keys and local secrets; never commit this file;
- `AOS_CONFIG`: optional path to an alternate YAML profile;
- `HF_TOKEN` / `HUG`, `HF_PROVIDER`, and `HF_MODEL`: optional Hugging Face
  catalog settings;
- `AOS_MEMORY`: process-level memory override.

See [`CONFIGURATION.md`](CONFIGURATION.md) for the supported YAML contract and
extension examples.

## Generated outputs and logs

- `data/outputs/plan.md`: deterministic node table, DNA details, waves, and
  Mermaid graph;
- `data/outputs/registry_data.json`: registry export produced by
  `python -m aos_v0.services.registry_export`;
- `log/`: session logs, recovery output, and optional memory decision/dump files;
- `data/artifacts/`: copies made by the interactive artifact manager.

## What is historical

- `DOC1_Research_Architecture_Specification.md` records the research thesis and
  proposed future modules.
- `DOC2_Implementation_Proposal_3Month.md` records an earlier roadmap.
- `SHARED_MEMORY_PLAN.md` and `SHARED_MEMORY_EXECUTION_PLAN.md` preserve the
  design rationale and phased prompts; their current implementation status is
  summarized above.
- `REGISTRY_REPORT.md` is a catalog snapshot and must be regenerated when the
  Hugging Face catalog changes.

## Verification

From the repository root:

```bash
python -m pytest tests/ -q
python -c "import aos_v0.cli"
python -m aos_v0.cli --help
```

Live model calls additionally require the provider credentials and, for local
vision, an available Ollama-compatible endpoint.
