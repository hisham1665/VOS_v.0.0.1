# AOS v0.0.2 — Adaptive Cognitive AI Microkernel

**AOS** (Adaptive Cognitive System) is a lightweight agentic framework that decomposes user prompts into **task graphs** — directed acyclic graphs (DAGs) of capability-routed nodes — and executes them concurrently in dependency-respecting waves. The system is designed as a minimal, composable microkernel: each agent role is a single class, each capability is a standalone function, and the pipeline is deterministic and auditable.

---

## Table of Contents

- [Architecture](#architecture)
- [Pipeline Flow](#pipeline-flow)
- [Project Structure](#project-structure)
- [Components](#components)
  - [Models (`models.py`)](#models-modelspy)
  - [Manager Agent (`agents/manager_agent.py`)](#manager-agent-agentsmanager_agentpy)
  - [Graph Executor (`agents/graph_executor.py`)](#graph-executor-agentsgraph_executorpy)
  - [Sub-Agent (`agents/sub_agent.py`)](#sub-agent-agentssub_agentpy)
  - [Integrator Agent (`agents/integrator_agent.py`)](#integrator-agent-integrator_agentpy)
  - [Capabilities](#capabilities)
  - [Graph Utilities (`graph_utils.py`)](#graph-utilities-graph_utilspy)
  - [Diagram Utilities (`diagram_utils.py`)](#diagram-utilities-diagram_utilspy)
- [Data Models](#data-models)
- [Execution Model: Waves](#execution-model-waves)
- [Deterministic Diagrams](#deterministic-diagrams)
- [Semantic Completeness Verification](#semantic-completeness-verification)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [Configuration](#configuration)
- [Design Decisions](#design-decisions)
- [Upgrade Path](#upgrade-path)

---

## Architecture

```
User Prompt
     │
     ▼
┌──────────────────────┐
│   Manager Agent       │  LLM decomposes prompt → Graph (DAG of Nodes)
│                      │  Validates structurally + checks semantic completeness
└──────────────────────┘
     │
     ├──► writes outputs/plan.md  (node table + wave breakdown + Mermaid diagram)
     │
     ▼
┌──────────────────────┐
│   Graph Executor      │  Groups nodes into waves via topological sort
│                      │  Executes each wave concurrently via ThreadPoolExecutor
│                      │  Injects vision context into all downstream nodes
└──────────────────────┘
     │
     ▼
┌──────────────────────┐
│   Sub-Agent (×N)      │  One instance per node, capability-resolved at runtime
│                      │  Each calls the appropriate capability function
└──────────────────────┘
     │
     ▼
┌──────────────────────┐
│   Integrator Agent    │  Collects sink nodes (nodes with no dependents)
│                      │  Returns labeled concatenation of their outputs
└──────────────────────┘
     │
     ▼
   Final Output
```

## Pipeline Flow

1. **User input** — a text prompt and optionally an `--image` path.
2. **Manager Agent** sends the prompt to an LLM (Groq `llama-3.3-70b-versatile`) with a schema-constrained function-calling prompt. The LLM returns a `Graph` object: a list of `Node` objects with `id`, `description`, `capability`, and `depends_on` edges.
3. **Structural validation** — `validate_graph()` checks for duplicate IDs, dangling references, missing roots, and cycles. Up to 1 retry.
4. **Semantic completeness check** — a separate LLM call verifies every named entity in the prompt has at least one dedicated node. If incomplete, the graph is regenerated once.
5. **Plan output** — `outputs/plan.md` is written with a node table, wave breakdown, and a deterministic Mermaid diagram.
6. **Graph Executor** topologically sorts nodes into waves. Each wave's nodes execute concurrently via threads. Parent outputs are concatenated and passed as input to child nodes.
7. **Vision context injection** — once the vision node completes, its identification output is injected into every downstream non-vision node's input, ensuring image context is never lost through intermediate summarization.
8. **Integrator Agent** collects all sink nodes (nodes that no other node depends on) and returns their labeled concatenated outputs.

## Components

### Models (`models.py`)

Two Pydantic models form the type system:

| Model | Fields | Description |
|-------|--------|-------------|
| `Node` | `id`, `description`, `capability`, `depends_on`, `input`, `output`, `status`, `performed_by` | A single unit of work. |
| `Graph` | `job`, `nodes` | A full DAG: the original job + a list of Nodes. |

`capability` must be one of: `web_search`, `summarization`, `vision`.

### Manager Agent (`agents/manager_agent.py`)

The orchestrator. Responsibilities:

1. **Graph generation** — calls Groq's `llama-3.3-70b-versatile` with a system prompt that enforces:
   - One `web_search` + one `summarization` node per named entity (no collapsing multiple entities into one node).
   - Parallelism where possible.
   - Vision node when an image is provided.
2. **Structural validation** — `validate_graph()` detects cycles, dangling deps, and duplicate IDs. Up to 1 automated retry with the validation error fed back.
3. **Semantic completeness check** — a secondary LLM call audits whether every named entity in the job has dedicated node(s). Up to 1 retry. Prints `[manager-agent] completeness check: COMPLETE` or `INCOMPLETE` with reason.
4. **Artifact generation** — writes `outputs/plan.md` with:
   - Node table (ID, description, capability, dependencies)
   - Wave breakdown (plain text)
   - Mermaid diagram (via `diagram_utils.build_mermaid()`)
5. **README upkeep** — maintains the `## How AOS v0.0.2 plans and executes tasks` section in `README.md`.

**Retry budget:** max 1 structural retry + max 1 completeness retry = max 2 regenerations total.

### Graph Executor (`agents/graph_executor.py`)

Executes the DAG with concurrency:

1. **Wave computation** — calls `build_waves()` for a topological sort producing dependency-respecting waves.
2. **Concurrent execution** — each wave runs in a `ThreadPoolExecutor` (max_workers = wave size). Nodes within a wave are independent by definition.
3. **Input routing** — root nodes receive the original `graph.job` (or `image_path` for vision). Non-root nodes receive concatenated, labeled outputs from all parents.
4. **Vision context injection** — once the vision node produces its output, it is prefixed to the input of every subsequent non-vision node as `[IMAGE IDENTIFICATION]`, ensuring the image description is never lost through intermediate nodes.

### Sub-Agent (`agents/sub_agent.py`)

A thin dispatcher. One instance per node. Maps `node.capability` to the matching function via `CAPABILITY_MAP`:

```python
CAPABILITY_MAP = {
    "web_search": web_search.run,
    "summarization": summarization.run,
    "vision": vision.run,
}
```

Calls `fn(node.input, instruction=node.description)` and stores the result in `node.output`.

### Integrator Agent (`agents/integrator_agent.py`)

Collects sink nodes (nodes that no other node depends on, computed by `get_sink_nodes()`). If there's exactly one sink, returns its output directly. For multiple sinks, returns labeled concatenation.

### Capabilities

#### `capabilities/web_search.py`

Performs real web search via DuckDuckGo, then synthesizes results with Groq:

1. Calls `DDGS().text(query, max_results=5)` to fetch live search results.
2. Sends those results to Groq's `llama-3.3-70b-versatile` with a prompt that demands exact facts, numbers, and data points.
3. Returns the synthesized answer.

#### `capabilities/summarization.py`

Summarizes text using Groq's `llama-3.3-70b-versatile`. Prompt preserves all specific facts, statistics, and names — no vague language.

#### `capabilities/vision.py`

Analyzes images using a local Ollama instance with `medgemma1.5:latest`:

1. Validates the image file exists.
2. Base64-encodes the image.
3. Sends to Ollama's OpenAI-compatible endpoint at `localhost:11434/v1` with `image_url` content format.
4. Returns the model's description.

### Graph Utilities (`graph_utils.py`)

Three pure-Python functions:

| Function | Description |
|----------|-------------|
| `validate_graph(graph)` | Checks duplicate IDs, dangling references, missing roots, cycles. Raises `GraphValidationError` on failure. |
| `build_waves(graph)` | Topological sort returning `list[list[Node]]` — one list per wave. |
| `get_sink_nodes(graph)` | Returns nodes with no dependents. |

### Diagram Utilities (`diagram_utils.py`)

`build_mermaid(graph, waves)` — returns a Mermaid flowchart string deterministically (no LLM calls):

- Groups nodes into `subgraph Wave N` blocks by wave.
- Labels: `node_id["truncated description (capability)"]`
- Sanitizes characters that break Mermaid syntax.
- Renders dependency arrows after all subgraphs.
- Colors by capability: green (`#4CAF50`) for `web_search`, blue (`#2196F3`) for `summarization`, orange (`#FF9800`) for `vision`.

## Data Models

```
Node {
    id: str              # unique identifier (e.g. "a", "b", "search1")
    description: str     # instruction passed to the capability
    capability: str      # one of ["web_search", "summarization", "vision"]
    depends_on: list     # node IDs this node depends on
    input: str | None    # set at execution time
    output: str | None   # set after execution
    status: str          # "pending" | "running" | "done" | "failed"
    performed_by: str | None  # sub-agent instance name
}

Graph {
    job: str             # original user prompt
    nodes: list[Node]    # the DAG
}
```

## Execution Model: Waves

The graph executor uses a **wave-based concurrent execution model**:

1. Root nodes (no dependencies) form **Wave 0** — they run in parallel.
2. Once all nodes in wave `n` complete, nodes whose dependencies are all satisfied form **Wave n+1**.
3. Each wave executes with `ThreadPoolExecutor(max_workers=len(wave))`.

**Example:** For the graph `a → c → e` and `b → d → e`:
- Wave 0: `a`, `b` (parallel)
- Wave 1: `c`, `d` (parallel)
- Wave 2: `e`

**Multi-parent merging:** If node `e` depends on `c` and `d`, it receives:
```
From node 'c' (description of c):
[output of c]

From node 'd' (description of d):
[output of d]
```

## Deterministic Diagrams

The Mermaid diagrams in `outputs/plan.md` are generated by `diagram_utils.build_mermaid()` — **pure Python string formatting, no LLM calls**. This is a deliberate reliability choice:

- The diagram always faithfully represents the actual graph structure.
- Wave assignments are computed algorithmically from the dependency edges.
- Color coding by capability is automatic and consistent.
- No risk of the LLM inventing, omitting, or misplacing nodes.

## Semantic Completeness Verification

After structural validation passes, the Manager Agent performs an independent LLM audit:

1. Sends the original job text + the generated graph's node list to Groq.
2. Asks: "Does every entity/subtask named in the job have at least one dedicated node?"
3. If **COMPLETE** — proceeds.
4. If **INCOMPLETE: <reason>** — regenerates the graph once with the reason fed back.
5. If still incomplete after retry, raises `RuntimeError` rather than silently shipping an incomplete plan.

## Installation & Setup

```bash
# 1. Clone and enter the project directory
cd aos_v0

# 2. Create a virtual environment (recommended)
python -m venv venv
.\venv\Scripts\Activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
# Create .env with your Groq API key:
# GROQ_API_KEY=gsk_...

# 5. Install and start Ollama (for vision capability)
# Download from https://ollama.com
ollama pull medgemma1.5:latest
ollama serve

# 6. (Optional) Set Ollama parallel execution
# Higher values allow more concurrent local model requests
set OLLAMA_NUM_PARALLEL=3
```

## Usage

```bash
# Basic prompt (text only)
python main.py "Research electric vehicles and summarize the key trends"

# Prompt with image
python main.py "Identify this team and compare them with Brazil, Portugal, Spain" --image image.jpg

# Interactive mode (no args)
python main.py
Enter your prompt: what are the latest NBA playoff results
```

The output is written to `outputs/plan.md` and the final result is printed to the console.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GROQ_API_KEY` | Yes | — | Groq Cloud API key (for manager, web_search synthesis, summarization) |
| Ollama | For vision | `localhost:11434` | Local vision model via OpenAI-compatible API |
| `OLLAMA_NUM_PARALLEL` | No | `1` | Concurrent Ollama request limit |

**Project dependencies** (`requirements.txt`):

- `pydantic>=2.0` — data modeling
- `groq>=0.13.0` — Groq Cloud API client
- `openai>=1.0.0` — OpenAI-compatible API (used for Ollama vision)
- `ddgs>=9.0.0` — DuckDuckGo web search
- `python-dotenv>=1.0.0` — environment variable loading
- `requests>=2.31.0` — HTTP utilities

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Sub-Agent is generic, one class per capability** | The dispatcher pattern (`CAPABILITY_MAP`) allows adding new capabilities without touching agent code. |
| **Capabilities are standalone functions, not classes** | Simplifies testing in isolation. Each capability can be imported and run independently with a hardcoded string. |
| **Wave-based concurrent execution** | Topological sort guarantees correctness; ThreadPoolExecutor keeps it simple. No event loops, no actor frameworks. |
| **LLM generates the graph, code validates it** | The LLM is used for decomposition (where reasoning matters). Structural validation and diagram generation are deterministic. |
| **One web_search + one summarization per entity** | Prevents the LLM from collapsing distinct entities into a single vague node. A predictable 1-to-1 search-to-summarize ratio. |
| **Vision context injection** | Downstream nodes always receive the vision output as explicit context, preventing loss through intermediate summarization. |
| **Separate structural + semantic validation** | Structural: code checks cycles/dangling refs. Semantic: LLM audits entity coverage. Different concerns, different retry budgets. |
| **Mermaid diagram is deterministic, not LLM-generated** | Eliminates the risk of hallucinated nodes, missing edges, or syntax errors in the diagram. |

## Project Structure

```
aos_v0/
├── main.py                    # Entry point
├── models.py                  # Node, Graph (Pydantic)
├── graph_utils.py             # validate_graph(), build_waves(), get_sink_nodes()
├── diagram_utils.py           # build_mermaid() — deterministic diagram generation
├── config.py                  # Environment variable loading
├── requirements.txt           # Dependencies
├── .env                       # Environment variables (GROQ_API_KEY)
├── agents/
│   ├── manager_agent.py       # LLM decomposition + validation + plan output
│   ├── sub_agent.py           # Capability dispatcher
│   ├── graph_executor.py      # Wave-based concurrent execution
│   └── integrator_agent.py    # Sink node collection + output
├── capabilities/
│   ├── web_search.py          # DuckDuckGo search + Groq synthesis
│   ├── summarization.py       # Groq-based text summarization
│   └── vision.py              # Ollama/MedGemma image analysis
└── outputs/
    └── plan.md                # Generated task plan (node table + Mermaid)
```
