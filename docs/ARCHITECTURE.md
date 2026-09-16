# ARCHITECTURE.md — AOS System Architecture

## Overview

AOS is a **capability-routed, auditable agent execution kernel**. It decomposes user prompts into directed acyclic graphs (DAGs) of tasks, extracts typed resource requirements (Capability DNA), binds the best available resource per task, executes concurrently in dependency-respecting waves, and integrates the final result.

## Architectural Principle

> The Manager decides what needs to be done; the DNA Extractor describes what resources are required; the Capability Registry decides how it should be performed.

## Pipeline Stages

```
                         User Prompt
                              │
                              ▼
                    ┌─────────────────────┐
                    │    Manager Agent    │  Stage 1: PLANNING
                    │  Decomposes prompt  │
                    │  into task graph    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Structural +        │  Stage 2: VALIDATION
                    │ Semantic Validation │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    DNA Extractor    │  Stage 3: REQUIREMENT EXTRACTION
                    │  Task → Capability  │
                    │  DNA requirement    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    Constraint       │  Stage 4: CONSTRAINT DERIVATION
                    │    Policy           │
                    │  Budget-aware caps  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Capability Registry │  Stage 5: RESOURCE SELECTION
                    │  Feasibility Filter │
                    │  Pareto Scoring     │
                    │  Resource Binding   │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Graph Executor    │  Stage 6: EXECUTION
                    │  Topological waves  │
                    │  Concurrent exec    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  Failure Manager    │  (inline) DETECT → RECOVER
                    │  per-node recovery  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  Integrator Agent   │  Stage 7: INTEGRATION
                    │  Collects outputs   │
                    │  Builds response    │
                    └──────────┬──────────┘
                               │
                               ▼
                         Final Output
```

## Stage Details

### Stage 1: Planning (ManagerAgent)

**File:** `agents/manager_agent.py`

The Manager Agent sends the user prompt to an LLM (Groq `openai/gpt-oss-120b`) with a forced `create_graph` tool call. The LLM returns a JSON graph with nodes and edges.

**Key behaviors:**
- Enforces one-node-per-named-entity rule
- Preserves parallelism where possible
- Adds vision processing node when `--image` is supplied
- Appends a kernel-owned `synthesis` node (`id="final"`) as the terminal node
- Retries on structural validation failure (up to 1 retry)
- Retries on semantic incompleteness (up to 1 retry)

### Stage 2: Validation (graph_utils.py)

**File:** `core/graph_utils.py`

Deterministic, non-LLM validation:

1. **Duplicate node IDs** — All node IDs must be unique
2. **Dangling dependencies** — Every `depends_on` must reference an existing node
3. **Root nodes** — At least one node must have no dependencies
4. **Cycle detection** — Kahn's algorithm (topological sort)
5. **Semantic completeness** — LLM auditor checks all named entities from the job are represented

### Stage 3: DNA Extraction (DNAExtractor)

**File:** `core/dna_extractor.py`

For each node, extracts a `CapabilityDNA` with:
- **flags**: Required capability identifiers (e.g. `["web.search", "reasoning.shallow"]`)
- **ordinals**: Difficulty scores 0–4 on 5 axes
- **constraints**: Derived by ConstraintPolicy (not LLM-generated)

**Two-tier strategy:**
1. Try cheap model (`openai/gpt-oss-20b`) with schema-constrained tool call
2. If confidence < 0.7, escalate to strong model (`openai/gpt-oss-120b`)
3. If both fail, fall back to keyword heuristics (confidence=0.2)

### Stage 4: Constraint Derivation (ConstraintPolicy)

**File:** `core/constraint_policy.py`

Derives constraints from the job budget and actual resource pool rather than letting the LLM hallucinate values:

| Constraint | Derivation |
|------------|------------|
| Cost ceiling | `max(budget/node_count, cheapest_resource_cost)` |
| Latency SLO | `max(job_SLO/node_count, pool_p95 * 1.5)` |
| Min quality | Linear interpolation 0.45–0.85 based on ordinal demand |
| Risk tolerance | `"high"` if DNA has external-data flags, else `"low"` |

### Stage 5: Resource Selection (CapabilityRegistry)

**File:** `core/capability_registry.py`

Two-stage binding:

**Stage 5a — Feasibility Filtering:**
- Resource must provide ALL required DNA flags
- Resource must satisfy cost ceiling, latency SLO, min quality, risk tolerance
- Resource must be available and routable
- Resource must accept the input modality

**Stage 5b — Pareto Scoring:**
```
score = quality_prior - λ·cost - μ·latency
```
- 7 dimensions: flag match, reasoning, planning, tool, cost, latency, quality
- Cost and latency normalized against the individual subtask's requirements
- Winner selected; runner-up margin and rejection reasons recorded for audit

### Stage 6: Execution (GraphExecutor)

**File:** `agents/graph_executor.py`

- Builds waves via `build_waves()` (topological level sets)
- Each wave executes nodes concurrently via `ThreadPoolExecutor`
- Each node is executed by a `SubAgent` which:
  - Routes to the selected resource (DNA → relaxed → exact fallback)
  - Delegates to `FailureManager.execute()` for detect→recover
  - Registers output as an artifact
- Vision/audio/document context is propagated to downstream non-media nodes
- Multi-parent nodes receive labeled concatenated outputs

### Stage 7: Integration (IntegratorAgent)

**File:** `agents/integrator_agent.py`

- Identifies sink nodes (no downstream dependents)
- If a synthesis node exists (kernel-appended), returns its output directly
- Otherwise concatenates labeled sink outputs

## Data Flow

```
UserPrompt(str)
    │
    ▼
Graph(job, nodes[], artifacts{})
    │
    ▼  [DNAExtractor]
Graph with node.dna filled
    │
    ▼  [ConstraintPolicy.apply()]
Graph with node.dna.constraints derived from budget
    │
    ▼  [CapabilityRegistry.bind() per node]
Graph with node.bound_resource, node.performed_by set
    │
    ▼  [GraphExecutor.run()]
Graph with node.output filled, node.status="done"
    │
    ▼  [IntegratorAgent.integrate()]
str (final answer)
```

## Concurrency Model

Nodes in the same dependency wave are independent and execute concurrently:

```
Wave 0: A, B         (parallel)
Wave 1: C             (after A,B complete)
Wave 2: D, E         (parallel, after C completes)
```

Implementation: `ThreadPoolExecutor` with per-wave barriers.

## Event System

**File:** `core/events.py`

Thread-safe pub/sub for UI-independent observation:

```python
bus = EventBus()
bus.subscribe(lambda event: print(event))
bus.emit(OrchestrationEvent(
    type=EventType.AGENT_COMPLETED,
    request_id="req_xxx",
    payload={"node_id": "research_solar", "latency_ms": 1200}
))
```

13 event types cover the full lifecycle from `REQUEST_RECEIVED` to `REQUEST_COMPLETED`.

## Failure Recovery

**File:** `core/failure_manager.py`

5 failure classes with ordered recovery ladders:

| Class | Strategies |
|-------|-----------|
| Resource outage | retry_same → retry_with_feedback → resource_substitution |
| Resource degraded | retry_same → retry_with_feedback → resource_substitution |
| Tool empty result | retry_with_feedback → resource_substitution |
| Tool output corrupt | retry_with_feedback → resource_substitution |
| Reasoning refusal | retry_with_feedback → resource_substitution |

Detection uses ensemble pattern matching (regex + length + exception type).
Recovery returns `[UNAVAILABLE — ...]` gap markers if unrecovered.
