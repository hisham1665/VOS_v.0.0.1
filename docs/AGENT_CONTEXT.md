# AGENT_CONTEXT.md — AI Agent System Context

> **Purpose:** Single source of truth for any AI agent working on this codebase. Read this file first.

## Project Identity

| Field | Value |
|-------|-------|
| Name | AOS (Adaptive Cognitive AI Microkernel) |
| Version | 0.0.5 |
| Package | `aos_v0` |
| Language | Python ≥3.11 |
| Layout | `src/` layout (`src/aos_v0/`) |
| Entry points | `aos` (CLI), `aos-tui` (TUI), `python -m aos_v0` |
| Git branch | `Medical-AOS` |

## What AOS Does

AOS decomposes a user prompt into a **task graph** (DAG), extracts **Capability DNA** (typed resource requirements) for each node, selects the best resource via **feasibility filtering + Pareto scoring**, executes nodes **concurrently in dependency waves**, and integrates the final answer.

```
User Prompt → ManagerAgent (DAG) → DNAExtractor (requirements) → CapabilityRegistry (selection) → GraphExecutor (waves) → IntegratorAgent (output)
```

## Critical Architecture Invariants

1. **Planning ≠ Resource Selection.** ManagerAgent decides *what*; DNAExtractor describes *what kind*; CapabilityRegistry decides *which*.
2. **Feasibility before scoring.** Filter unsuitable resources first, then score survivors.
3. **Deterministic graph ops.** Validation, wave building, diagram generation are NOT LLM-generated.
4. **Cheap-first escalation.** DNA extraction tries cheap model first, escalates to strong on low confidence.
5. **Explicit audit trails.** Every binding decision records winner, runner-up, margin, and rejection reasons.

## File Map

```
src/aos_v0/
├── __init__.py              # Package version (0.0.5)
├── __main__.py              # python -m aos_v0 entry
├── cli.py                   # Core pipeline orchestrator
├── config.py                # Env vars + client factories
├── interactive.py           # Interactive service adapter
├── logbook.py               # Terminal log capture (Tee)
├── tui.py                   # Textual terminal UI
├── tui_commands.py          # Command registry for TUI
├── core/
│   ├── models.py            # All Pydantic types (Node, Graph, CapabilityDNA, Artifact)
│   ├── graph_utils.py       # DAG validation, wave building, sink detection
│   ├── capability_registry.py # Resource registry + DNA scoring + selection
│   ├── dna_extractor.py     # LLM-based DNA extraction with escalation
│   ├── constraint_policy.py # Kernel-derived constraints from budget + pool
│   ├── failure_manager.py   # Detect → classify → recover loop
│   ├── events.py            # OrchestrationEvent pub/sub
│   ├── runtime.py           # RequestContext, Session, Telemetry
│   ├── artifacts.py         # ArtifactManager (file registration/lifecycle)
│   └── diagram_utils.py     # Deterministic Mermaid generation
├── agents/
│   ├── manager_agent.py     # LLM-based task decomposition + validation
│   ├── graph_executor.py    # Wave-by-wave concurrent execution
│   ├── sub_agent.py         # Single-node executor with routing
│   └── integrator_agent.py  # Sink-node output collection
├── capabilities/
│   ├── web_search.py        # DuckDuckGo + Groq fact extraction
│   ├── summarization.py     # Fact-preserving summarizer
│   ├── vision.py            # Ollama image description
│   ├── synthesis.py         # Final answer composition
│   └── document.py          # PDF text extraction (pdfplumber)
├── providers/
│   ├── hf.py               # HuggingFace Inference Providers adapter (17 models)
│   └── errors.py           # Typed provider errors
├── medical/
│   ├── manifest.py          # Folder scanning, file classification, block parsing
│   ├── clinical.py          # PDF page extraction, lab parsing, demographics
│   ├── capabilities.py      # 7 medical capability run functions
│   ├── registration.py      # Medical resource registration
│   ├── workflow.py          # Medical job prompt builder
│   └── hf_medical.py        # MedGemma + Lab NER wrappers
└── services/
    ├── resource_registration.py # Default + HF registry builders
    ├── evaluation.py         # Provider-agnostic benchmarking
    └── registry_export.py    # JSON registry export CLI
```

## Entry Points & Commands

```bash
# Standard text query
python -m aos_v0 "Compare solar and wind energy"

# Image-aware query
python -m aos_v0 "Identify the object" --image data/inputs/photo.jpg

# Medical workflow
python -m aos_v0 --medical-folder data/inputs/patient1 "Build a chart"

# TUI interactive mode
aos-tui

# Registry export
aos-registry-export
```

## Convention: Capability Function Signature

Every capability `run` function follows:

```python
def run(text: str, instruction: Optional[str] = None) -> str
```

- `text`: Primary input (query, file path, or upstream output)
- `instruction`: Node description (the task instruction from the DAG)
- Returns: Result string, or `NO_DATA: <reason>` sentinel on empty/unavailable

## Convention: DNA Flags Format

Flags use `"family.specific"` dot notation:

```
web.search, text.summarization, vision.understanding, reasoning.deep,
answer.synthesis, document.extraction, speech.transcription,
medical.folder_ingestion, medical.laboratory_analysis, ...
```

57 flags total defined in `core/models.py::CAPABILITY_FLAGS`.

## Convention: Resource Registration

```python
from aos_v0.core.capability_registry import CapabilityManifest, CapabilityRegistry

manifest = CapabilityManifest(
    resource_id="my_resource",
    resource_class="llm",
    capabilities=["text.summarization", "reasoning.deep"],
    input_schema=IOSchema(type="text", format="str"),
    output_schema=IOSchema(type="text", format="str"),
    cost_model=CostModel(unit="per_1k_tokens", estimate_usd=0.003),
    latency_model=LatencyModel(p50_ms=1500, p95_ms=5000),
    quality_priors={"text.summarization": 0.85, "reasoning.deep": 0.80},
    availability=Availability(status="up", rate_limit_rpm=30),
    risk_class="low",
    metadata={"provider": "groq", "model": "qwen/qwen3.6-27b"},
)
registry.register(manifest, my_run_fn)
```

## Key Functions by Purpose

| Purpose | Function | File |
|---------|----------|------|
| Full pipeline | `cli.run()` | `cli.py` |
| Plan only | `ManagerAgent.create_plan()` | `agents/manager_agent.py` |
| Execute only | `GraphExecutor.run()` | `agents/graph_executor.py` |
| Integrate only | `IntegratorAgent.integrate()` | `agents/integrator_agent.py` |
| Extract DNA | `DNAExtractor.extract_graph()` | `core/dna_extractor.py` |
| Select resource | `CapabilityRegistry.select()` | `core/capability_registry.py` |
| Validate graph | `validate_graph()` | `core/graph_utils.py` |
| Build waves | `build_waves()` | `core/graph_utils.py` |
| Apply constraints | `ConstraintPolicy.apply()` | `core/constraint_policy.py` |
| Detect failures | `FailureManager.detect()` | `core/failure_manager.py` |
| Emit events | `EventBus.emit()` | `core/events.py` |
| Register artifacts | `ArtifactManager.register()` | `core/artifacts.py` |
| Generate Mermaid | `build_mermaid()` | `core/diagram_utils.py` |
| Build default registry | `build_default_registry()` | `services/resource_registration.py` |
| Build HF registry | `build_hf_enabled_registry()` | `services/resource_registration.py` |
| Medical registration | `register_medical_resources()` | `medical/registration.py` |
| Medical job builder | `build_medical_job()` | `medical/workflow.py` |

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `GROQ_API_KEY` | Yes | — | Groq API for text capabilities |
| `HF_TOKEN` | No | — | HuggingFace Inference Providers |
| `HF_PROVIDER` | No | `auto` | HF provider routing |
| `HF_MODEL` | No | `""` | Default HF model override |
| `MEDICAL_IMAGE_MODEL` | No | `google/medgemma-1.5-4b-it` | Medical imaging model |
| `MEDICAL_LAB_MODEL` | No | `genzeonplatform/healthcare-brain-laboratory-ner` | Medical lab NER model |

## Testing

```bash
python -m pytest tests/ -v
```

Test files:
- `test_event_integration.py` — Event emission across lifecycle
- `test_document.py` — PDF extraction + document modality routing
- `test_medical.py` — Full medical workflow (12 scenarios)
- `test_interactive.py` — Command parsing + submission
- `test_artifacts.py` — Artifact registration + lifecycle
