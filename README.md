# AOS v1.0.0 — Adaptive Cognitive AI Microkernel

AOS (Adaptive Cognitive System) is a lightweight agentic framework that decomposes user prompts into task graphs — directed acyclic graphs (DAGs) of capability-routed nodes — and executes them concurrently in dependency-respecting waves.

AOS is designed as a minimal, composable, deterministic, and auditable microkernel. Each agent role has a focused responsibility, capabilities are independently implemented, and resource selection is separated from task planning through Capability DNA and a dedicated DNA Extractor.

## Table of Contents

- [Architecture](#architecture)
- [Pipeline Flow](#pipeline-flow)
- [Project Structure](#project-structure)
- [Task Graph Model](#task-graph-model)
- [Capability DNA](#capability-dna)
- [Capability Binding](#capability-binding)
- [Components](#components)
  - [Models (models.py)](#models-modelspy)
  - [Manager Agent (agents/manager_agent.py)](#manager-agent-agentsmanager_agentpy)
  - [DNA Extractor](#dna-extractor)
  - [Capability Registry](#capability-registry)
  - [Graph Executor (agents/graph_executor.py)](#graph-executor-agentsgraph_executorpy)
  - [Sub-Agent (agents/sub_agent.py)](#sub-agent-agentssub_agentpy)
  - [Integrator Agent (agents/integrator_agent.py)](#integrator-agent-agentsintegrator_agentpy)
- [Capabilities](#capabilities)
- [Graph Utilities (graph_utils.py)](#graph-utilities-graph_utilspy)
- [Diagram Utilities (diagram_utils.py)](#diagram-utilities-diagram_utilspy)
- [Execution Model: Waves](#execution-model-waves)
- [Multi-Parent Merging](#multi-parent-merging)
- [Semantic Completeness Verification](#semantic-completeness-verification)
- [Deterministic Diagrams](#deterministic-diagrams)
- [Auditability and Logging](#auditability-and-logging)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [Configuration](#configuration)
- [Design Decisions](#design-decisions)
- [Upgrade Path](#upgrade-path)

## Architecture

AOS separates planning, requirement extraction, resource selection, and execution.

```
                         User Prompt
                              │
                              ▼
                    ┌─────────────────────┐
                    │    Manager Agent    │
                    │                     │
                    │ Decomposes prompt   │
                    │ into task graph     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Structural +        │
                    │ Semantic Validation │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    DNA Extractor    │
                    │                     │
                    │ Task → Capability   │
                    │ DNA requirement     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Capability Registry │
                    │                     │
                     │ Admission Gates     │
                     │         ↓           │
                     │ DNA Scoring         │
                     │         ↓           │
                     │ Resource Binding    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Graph Executor    │
                    │                     │
                    │ Topological waves   │
                    │ Concurrent execution│
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │     Sub-Agents      │
                    │                     │
                    │ Execute bound       │
                    │ capabilities       │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  Integrator Agent   │
                    │                     │
                    │ Collects sink nodes │
                    │ and builds output   │
                    └──────────┬──────────┘
                               │
                               ▼
                         Final Output
```

The key architectural principle in v1.0.0 is:

> The Manager decides what needs to be done; the DNA Extractor describes what resources are required; the Capability Registry decides how it should be performed.

## Pipeline Flow

1. **User input** — A text prompt and optional `--input`, `--image`, or `--audio` paths are supplied.

2. **Task decomposition** — The Manager Agent sends the prompt to the configured LLM and produces a Graph consisting of task nodes and dependency edges.

3. **Structural validation** — `validate_graph()` checks the generated graph for:
   - Duplicate node IDs
   - Dangling dependencies
   - Missing roots
   - Cycles
   - Invalid graph structure

   The Manager can retry graph generation when validation fails.

4. **Semantic completeness verification** — A separate LLM-based check verifies that all important named entities in the job are represented by appropriate task nodes. If the graph is incomplete, it can be regenerated.

5. **Plan generation** — A deterministic execution plan is written to `data/outputs/plan.md`, including:
   - Node table
   - Dependencies
   - Execution waves
   - Capability information
   - Mermaid graph

6. **Capability DNA extraction** — After planning, the DNA Extractor independently converts each subtask into a typed capability requirement vector.

7. **Resource admission** — The Capability Registry removes resources that are unavailable, unroutable, modality-incompatible, outside the node's risk/cost/latency limits, or below its effective quality floor.

8. **Resource scoring** — Remaining resources are ranked using the continuous DNA score:

   ```
   score = acceptance_rate * pessimising_factor
           − rejection_rate * optimising_factor
   ```

   Each dimension records its own acceptance/rejection contribution; cost and latency are normalized against the individual subtask.

9. **Capability binding** — The highest-scoring surviving resource is selected for the node. The runner-up margin and rejected-resource reasons are recorded for auditability.

10. **Wave execution** — The Graph Executor topologically sorts the graph into dependency waves. Independent nodes in the same wave execute concurrently.

11. **Multi-parent input merging** — Nodes with multiple dependencies receive labeled outputs from all completed parents.

12. **Media context handling** — Image, audio, and document inputs are routed by modality. With memory enabled, downstream context is handled through bounded shared-memory retrieval; the legacy direct context blocks remain available in memory-off execution.

13. **Integration** — The Integrator Agent collects sink-node outputs and combines them into the final response.

## Project Structure

A typical AOS project is organized around a small set of focused components:

```
AOS/
├── src/aos_v0/
│   ├── cli.py
│   ├── config.py
│   ├── core/          # models, graph logic, policies, registry
│   ├── agents/        # planning, execution, integration
│   ├── capabilities/  # concrete task capabilities
│   ├── providers/     # external model/provider adapters
│   └── services/      # registry construction and export
├── data/
│   ├── inputs/        # user-provided files
│   └── outputs/       # generated plans and artifacts
├── docs/              # architecture and implementation documents
├── pyproject.toml
├── requirements.txt
└── README.md
```

The exact module layout may evolve, but the architectural separation remains:

- **Agents** orchestrate work.
- **Models** define the typed data structures.
- **Capabilities** perform concrete work.
- **DNA extraction** defines resource requirements.
- **Capability Registry** performs resource selection.
- **Graph utilities** validate and analyze DAGs.
- **Diagram utilities** generate deterministic visualizations.

## Task Graph Model

AOS represents every job as a directed acyclic graph.

### Node

A node represents one unit of work.

Each node contains information such as:

| Field          | Purpose                                      |
|----------------|----------------------------------------------|
| `id`           | Unique node identifier                       |
| `description`  | Task instruction                             |
| `capability`   | Coarse capability hint                       |
| `capability_dna`| Typed resource requirements                 |
| `depends_on`   | IDs of prerequisite nodes                    |
| `input`        | Runtime input                                |
| `output`       | Produced result                              |
| `status`       | Execution state                              |
| `performed_by` | Selected resource/capability                 |

The coarse capability field provides a high-level hint such as:
- `web_search`
- `summarization`
- `vision`

The Capability DNA provides the more precise resource requirements used during capability binding.

### Edge

An edge represents a dependency.

For example:
```
C depends_on A and B
```
means C cannot execute until both A and B complete.

```
A ──► C
B ──► C
```

The outputs from A and B are then merged and supplied to C.

## Capability DNA

AOS v1.0.0 separates task planning from resource selection.

The Manager Agent is responsible for determining what work is required. It does not need to decide which concrete resource should perform that work.

The DNA Extractor converts each planned subtask into a typed requirement vector with three logical segments:

```
Capability DNA
├── flags
├── ordinals
└── constraints
```

### Flags

Discrete capability requirements describing what a resource must be able to do.

Examples include:
- `web.search`
- `text.summarization`
- `vision.understanding`
- `answer.synthesis`
- `document.extraction`

A resource must provide every required flag to pass the feasibility stage.

### Ordinals

Ordinal requirements describe task difficulty or capability level on a bounded scale.

AOS uses a difficulty range of:
```
0 ─────────────── 4
easy              hard
```

These values allow the registry to distinguish between resources with different capability levels.

### Constraints

Constraints describe the operational requirements for the task.

The v1.0.0 model includes:
- Cost ceiling
- Latency SLO
- Minimum quality
- Risk tolerance

A resource that violates a hard constraint is rejected before scoring.

### Adaptive extraction

The DNA Extractor uses a two-tier strategy:

```
Task
 │
 ▼
Cheap model
 │
 ├── confidence ≥ threshold ──► Accept DNA
 │
 └── confidence < threshold
             │
             ▼
        Strong model
             │
             ▼
          Final DNA
```

This keeps routine requirement extraction inexpensive while allowing difficult or ambiguous tasks to receive stronger reasoning.

## Capability Binding

AOS v1.0.0 uses hard admission gates followed by a continuous Capability DNA
score. The old two-stage Pareto description from the research documents is
historical and no longer describes `CapabilityRegistry.select()`.

Before scoring, the registry excludes resources that are:

- declared-only or unavailable;
- incompatible with the node's required input modality;
- above the node's risk, cost, or latency limits;
- below the effective quality floor; or
- in a recent provider-failure cooldown.

Remaining resources are scored across required flags, reasoning, planning, tool
complexity, cost, latency, and quality. The current score is:

```
score = acceptance_rate * pessimising_factor
        − rejection_rate * optimising_factor
```

Each dimension contributes an auditable acceptance or rejection value. The
highest score wins, with a deterministic `resource_id` tie-break. The complete
ranking, runner-up margin, and rejected-resource reasons remain available for
traces and routing summaries. If no resource survives the gates, `SubAgent`
uses a capability-overlap fallback and marks the node degraded rather than
silently presenting a partial match as a normal success.

## Components

### Models (core/models.py)

Pydantic models provide the type system for task graphs and their execution state.

The core models include:

| Model                | Purpose                              |
|----------------------|--------------------------------------|
| `Node`               | Represents an individual task        |
| `Graph`              | Represents the complete DAG          |
| `Capability DNA model` | Represents typed resource requirements |

A `Node` combines the original task information with its extracted Capability DNA and execution metadata.

### Manager Agent (agents/manager_agent.py)

The Manager Agent is responsible for planning, not resource selection.

Its responsibilities include:

- **Graph generation** – Converts the user prompt into a DAG. Creates individual nodes for required subtasks. Preserves parallelism where possible. Adds vision processing when an image is supplied.
- **Structural validation** – Detects duplicate IDs, dangling dependencies, missing roots, and cycles.
- **Semantic completeness** – Checks whether named entities and required concepts have dedicated tasks. Regenerates the graph when the semantic audit reports incompleteness.
- **Plan artifact generation** – Writes `data/outputs/plan.md`. Produces node tables, wave breakdowns, and deterministic Mermaid diagrams.
- **Separation of concerns** – The Manager describes the work. It does not choose the concrete resource that performs each task.

### DNA Extractor (core/dna_extractor.py)

The DNA Extractor runs after task decomposition.

Its purpose is to translate:
```
Natural-language subtask
          │
          ▼
Typed Capability DNA
```

For every node, the extractor identifies:
- Required capability flags
- Difficulty/ability ordinals
- Cost ceiling
- Latency SLO
- Minimum quality
- Risk tolerance

The extractor uses a cheap model by default and escalates to a stronger model when confidence is below the configured threshold.

This allows planning and resource selection to remain independent.

### Capability Registry (core/capability_registry.py)

The Capability Registry maintains the available resources and performs runtime binding.

For each node it:
- Reads the node's Capability DNA.
- Applies the feasibility filter.
- Rejects incompatible resources.
- Scores all feasible resources.
- Selects the highest-scoring candidate.
- Records the runner-up and score margin.
- Records why rejected resources were filtered.

The Registry therefore acts as the bridge between:

```
Task requirements
        │
        ▼
Capability DNA
        │
        ▼
Available resources
        │
        ▼
Selected resource
```

### Graph Executor (agents/graph_executor.py)

The Graph Executor is responsible for execution rather than planning.

It:
- Topologically sorts the graph.
- Groups nodes into dependency waves.
- Executes independent nodes concurrently.
- Waits for parent nodes before executing children.
- Merges parent outputs.
- Propagates vision context to downstream tasks.

Independent nodes are executed using a thread pool.

### Sub-Agent (agents/sub_agent.py)

A Sub-Agent executes an individual graph node.

Its responsibilities include:
- Receiving the node's task and runtime input.
- Using the resource selected by the Capability Registry.
- Invoking the appropriate capability.
- Recording the result.
- Updating node execution metadata.

This keeps individual task execution isolated from global graph orchestration.

### Integrator Agent (agents/integrator_agent.py)

The Integrator Agent produces the final result from completed graph branches.

A sink node is a node that has no downstream dependents.

The Integrator:
- Identifies sink nodes.
- Collects their outputs.
- Labels each output.
- Combines them into the final response.

This allows independent branches of a graph to contribute to the final answer.

## Capabilities

Capabilities are standalone functions responsible for concrete operations.

The default executable resources include:
- `web_search`
- `summarization`
- `quick_summarization`
- `synthesis`
- `vision`
- `speech_transcription`
- `document_extraction`

Operator-defined capabilities can be added through `aos.yaml`. The optional
Hugging Face catalog contributes additional manifests when `HF_TOKEN` (or
legacy `HUG`) is valid; declared-only interfaces are kept out of execution by
the registry rather than being presented as working adapters.

The architecture is intentionally extensible. New capabilities can be registered without changing the fundamental graph execution model.

## Graph Utilities (graph_utils.py)

Graph utilities provide deterministic graph operations such as:
- Structural validation
- Cycle detection
- Dependency analysis
- Root detection
- Sink detection
- Wave construction

These operations are kept outside the LLM so graph correctness does not depend on generated text.

## Diagram Utilities (diagram_utils.py)

Diagram utilities generate Mermaid diagrams directly from the validated graph.

The LLM does **not** generate the diagram.

This ensures the visualization represents the actual graph rather than a model-generated approximation.

## Execution Model: Waves

AOS executes DAGs in dependency-respecting waves.

Consider:
```
      A       B
       \     /
        \   /
          C
         / \
        D   E
```
The execution becomes:
```
Wave 0: A, B
Wave 1: C
Wave 2: D, E
```
Nodes inside the same wave have no unresolved dependencies and can execute concurrently.

This provides parallel execution while preserving dependency correctness.

### Example

```mermaid
flowchart TD
    subgraph Wave 0
        a["Research solar energy (web_search)"]
        b["Research wind energy (web_search)"]
    end

    subgraph Wave 1
        c["Summarize solar (summarization)"]
        d["Summarize wind (summarization)"]
    end

    subgraph Wave 2
        e["Compare summaries (summarization)"]
    end

    a --> c
    b --> d
    c --> e
    d --> e

    classDef web_searchStyle fill:#4CAF50,color:#fff
    classDef summarizationStyle fill:#2196F3,color:#fff

    class a web_searchStyle
    class b web_searchStyle
    class c summarizationStyle
    class d summarizationStyle
    class e summarizationStyle
```

Here:
- `a` and `b` execute concurrently.
- `c` and `d` execute concurrently after their respective parents finish.
- `e` waits for both summaries.

## Multi-Parent Merging

A node can depend on multiple parents.

For example:
```
Research A ──►
              \
               ► Compare
              /
Research B ──►
```
The Compare node receives labeled outputs from both parents.

Conceptually:
```
[A output]
[B output]
```
This allows downstream nodes to:
- Compare results.
- Combine information.
- Reconcile conflicting outputs.
- Perform cross-source analysis.

Parent outputs are therefore preserved rather than arbitrarily selecting one branch.

## Semantic Completeness Verification

Structural validity alone does not guarantee that a graph represents the user's intent.

A graph may be a valid DAG while still omitting an important entity from the original request.

AOS therefore performs a separate semantic completeness check.

For example, if the prompt requests:
> Compare Apple, Microsoft, and Google.

the planner should produce dedicated work for all three entities rather than collapsing them into a single generic research task.

The completeness verifier audits the generated graph and reports a status such as:
```
[manager-agent] completeness check: COMPLETE
```
or:
```
[manager-agent] completeness check: INCOMPLETE
```
When incomplete, the Manager can regenerate the graph.

## Deterministic Diagrams

AOS deliberately keeps diagram generation outside the LLM.

The process is:
```
Validated Graph
      │
      ▼
diagram_utils.build_mermaid()
      │
      ▼
Deterministic Mermaid Diagram
```

The generated diagram reflects:
- Actual nodes
- Actual dependency edges
- Actual execution waves
- Actual capability types

This prevents the LLM from inventing, omitting, or misrepresenting graph structure in the visual artifact.

## Auditability and Logging

AOS v1.0.0 expands auditability beyond graph execution.

For capability binding, the system records:
- Selected resource
- Winning score
- Runner-up resource
- Runner-up score
- Winner/runner-up margin
- Resources rejected during admission gates
- Reason each resource was rejected
- Relevant DNA requirements
- Binding decisions

This makes resource selection explainable.

A simplified decision trace looks like:
```
Node: summarize_solar

DNA:
  flags       = [summarization]
  difficulty  = 2
  cost_max    = ...
  latency_slo = ...
  min_quality = ...
  risk        = ...

Admission gates:
  Resource A → ACCEPT
  Resource B → REJECT: latency SLO violated
  Resource C → REJECT: missing required flag

Scoring:
  Resource A → 0.82

Selected:
  Resource A

Runner-up:
  Resource D

Margin:
  0.11
```

The goal is to make both *what* AOS did and *why* AOS selected a particular resource inspectable.

## Installation & Setup

Clone the repository and install the project's dependencies according to the environment configuration.

For example:
```bash
git clone <repository>
cd AOS
python -m pip install -e .
```

Configure the required model/provider credentials through environment variables or the project's configuration mechanism.

For example:
```bash
export GROQ_API_KEY="your-api-key"
```

Do not commit API keys or other credentials to the repository.

## Usage

Install the package first; this project uses the standard `src/` layout, so
the package is not importable from the repository root until the editable
installation is complete.

A typical AOS invocation accepts a natural-language task:
```bash
python -m aos_v0 "Compare solar and wind energy"
```

For image-aware tasks:
```bash
python -m aos_v0 "Identify the object and research it" --image data/inputs/image.jpg
```

The execution produces a task graph, validates it, extracts Capability DNA, binds suitable resources, executes the graph, and integrates the final results.

The generated execution plan is written to:
```
data/outputs/plan.md
```

## Interactive TUI and artifacts

Install the optional terminal UI dependencies, then start the event-driven
interactive client:

```bash
python -m pip install -e ".[tui]"
aos-tui
```

The primary interaction is natural language. Upload files before a request:

```text
AOS › /upload research.pdf diagram.png recording.wav
AOS › Analyze the paper and explain the diagram.
```

`ArtifactManager` stores uploads in `data/artifacts/` and standardizes each as
an `Artifact` with an ID, category, MIME type, size, path and metadata. The
interactive service creates a `RequestContext` containing selected artifact
references and passes it into the unchanged planning/DNA/routing/execution
kernel. The Textual UI only renders `OrchestrationEvent` messages emitted by
that kernel; it never makes routing decisions.

Commands: `/help`, `/clear`, `/history`, `/session`, `/status`, `/agents`,
`/capabilities`, `/files`, `/upload`, `/config`, and `/quit`.

Current kernel support for direct media execution includes images, audio, and
PDF/text-like documents. Documents are extracted locally with source/page
markers; images and audio are sent to their configured provider profiles.
Datasets, archives, video, and other retained artifact categories require a
corresponding registered parsing capability before AOS can process their binary
contents.

## Configuration

The v1.0.0 architecture uses validated YAML configuration for runtime, memory,
model profiles, and operator-defined capabilities. See
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for the supported schema.

Important configuration concepts include:

| Setting                    | Purpose                                      |
|----------------------------|----------------------------------------------|
| `runtime.*`                | Worker, timeout, budget, graph, context and output limits |
| `memory.*`                 | Shared-memory enablement, bounds, relevance and logging |
| `models.*`                 | Provider, model, endpoint and output settings |
| `capabilities.*`           | Operator-defined chat or Python capability manifests |
| `.env` / `AOS_CONFIG`      | Credentials and alternate YAML profile selection |

## Design Decisions

### Planning and resource selection are separate
- The Manager Agent answers: *What needs to be done?*
- The DNA Extractor answers: *What kind of resource is required?*
- The Capability Registry answers: *Which available resource should perform it?*

This separation prevents task planning from becoming tightly coupled to a particular model or tool.

### Admission before scoring
A resource should not win merely because it scores well if it violates a hard
requirement. The registry applies availability, routability, modality, risk,
cost, latency, quality, and provider-cooldown gates before DNA scoring.

Therefore:
```
Filter first → Score second
```
is preferred over scoring every resource indiscriminately.

### Per-task normalization
Cost and latency are normalized relative to the individual task's requirements. This avoids treating all tasks as though they have identical performance expectations.

### Cheap-first DNA extraction
Most subtasks do not require expensive reasoning to classify. The DNA Extractor therefore uses a cheap model first and escalates only when confidence is insufficient.

### Deterministic graph operations
Graph validation, wave construction, dependency handling, and diagram generation are implemented deterministically rather than delegated to the LLM.

### Concurrent execution
Independent nodes should execute concurrently whenever dependencies allow it. This reduces unnecessary wall-clock execution time while preserving DAG semantics.

### Explicit audit trails
Capability binding decisions are recorded so that resource selection can be inspected and debugged after execution.

## Upgrade Path

AOS v1.0.0 establishes the foundation for increasingly sophisticated resource-aware execution.

Potential future extensions include:
- Additional capability types.
- Richer Capability DNA schemas.
- Learned or richer multi-objective resource selection.
- Resource health and availability tracking.
- Dynamic resource registration.
- Historical performance priors.
- Adaptive cost/latency weights.
- Persistent execution traces.
- More sophisticated retry and fallback policies.
- Capability composition.
- Distributed graph execution.
- Resource-aware scheduling across waves.

The central architecture remains:
```
Prompt
  │
  ▼
Task Graph
  │
  ▼
Capability DNA
  │
  ▼
Admission Gates
  │
  ▼
Continuous DNA Resource Scoring
  │
  ▼
Concurrent DAG Execution
  │
  ▼
Integrated Result
```

AOS v1.0.0 moves the system from a capability-routed task executor toward a
resource-aware adaptive cognitive microkernel, while retaining composability,
determinism, concurrency, and auditability.

## v1.0.0 operator quick start

`v1.0.0` combines the `v0.0.6` core with the `v5.2` shared-memory work. Install
it in editable mode, copy `.env.example` to `.env`, set the provider key, and
edit `aos.yaml` to choose models or add capabilities:

```bash
python -m pip install -e ".[tui]"
cp .env.example .env
python -m aos_v0 "Research the latest treatment options and cite the sources"
python -m aos_v0 "Analyze this report" --input data/inputs/report.pdf
```

The kernel includes web research, PDF/text extraction, image understanding,
audio transcription, bounded shared memory, exact reuse, failure recovery,
content-hashed artifacts, and performance-aware resource routing. See
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for extension examples and
[`docs/ADAPTABILITY.md`](docs/ADAPTABILITY.md) for the domain-independent
capability contract and the medical-AOS improvements applied to the core.

## Current implementation reference

For the versioned runtime status, supported resource pools, shared-memory
boundary, configuration source of truth, generated artifacts, and verification
commands, see [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md).
