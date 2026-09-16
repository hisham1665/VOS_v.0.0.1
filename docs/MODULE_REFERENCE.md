# MODULE_REFERENCE.md — Module-by-Module Reference

## Package: `aos_v0` (src/aos_v0/)

### `__init__.py`
- Version string: `__version__ = "0.0.5"`

### `__main__.py`
- Entry for `python -m aos_v0`. Delegates to `cli.main()`.

### `cli.py` (325 lines)
Core pipeline orchestrator. Single public function `run()` chains: ManagerAgent → DNAExtractor → ConstraintPolicy → admission control → GraphExecutor → IntegratorAgent. Also contains `main()` CLI entry with argument parsing.

### `config.py` (46 lines)
Environment variable loading (`GROQ_API_KEY`, `HF_TOKEN`, `HF_PROVIDER`, `HF_MODEL`, `MEDICAL_IMAGE_MODEL`, `MEDICAL_LAB_MODEL`). Client factories: `make_groq()`, `insecure_http_client()`.

### `interactive.py` (71 lines)
`InteractiveService` — thin adapter over `cli.run()`. Manages `Session`, `ArtifactManager`, `EventBus`. Handles upload/submit lifecycle. No planning or routing logic.

### `logbook.py` (123 lines)
`SessionLogger` — context manager that tees all `print()` to both terminal and timestamped log file via `Tee` class. Logs stored in `log/` directory.

### `tui.py` (106 lines)
Textual-based terminal UI. `AOSTui(App)` with event stream, input widget, command handling. Optional dependency (`textual`). Launches via `aos-tui` entry point.

### `tui_commands.py` (64 lines)
`CommandRegistry` — slash-command parser shared by TUI and tests. 11 commands: `/help`, `/clear`, `/history`, `/session`, `/status`, `/agents`, `/capabilities`, `/files`, `/upload`, `/config`, `/quit`.

---

## Package: `aos_v0.core`

### `models.py` (180+ lines)
All Pydantic domain types: `DNAOrdinals`, `DNAConstraints`, `CapabilityDNA`, `Artifact`, `Node`, `Graph`. Constants: `CAPABILITY_FLAGS` (57 flags), `ORDINAL_FIELDS` (5 axes), `RISK_LEVELS`.

### `graph_utils.py` (80 lines)
Deterministic DAG operations: `validate_graph()` (Kahn's algorithm cycle detection), `build_waves()` (topological level sets), `get_sink_nodes()` (terminal node detection).

### `capability_registry.py` (500+ lines)
Resource registry + scoring engine. `CapabilityManifest`, `CapabilityRegistry` with `select()`, `bind()`, `score_against_dna()`. 7-dimension scoring with feasibility filtering. `InfeasibleDNAError` for infeasible resources.

### `dna_extractor.py` (300+ lines)
LLM-based DNA extraction. `DNAExtractor` with cheap→strong model escalation and keyword heuristic fallback. Schema-constrained tool call extraction via Groq.

### `constraint_policy.py` (130 lines)
Kernel-derived constraint segment. `ConstraintPolicy` computes per-node cost ceiling, latency SLO, min quality, and risk tolerance from job budget and actual resource pool.

### `failure_manager.py` (350+ lines)
Cognitive failure manager. `FailureManager` with 5 failure classes, 3 recovery strategies, ensemble detection (regex + length + exception type), and closed-loop recovery ladder.

### `events.py` (60 lines)
Orchestration event stream. `EventType` (13 members), `OrchestrationEvent` (frozen dataclass), `EventBus` (thread-safe pub/sub).

### `runtime.py` (80 lines)
`RequestContext`, `Session`, `Telemetry` — per-request and per-session state containers.

### `artifacts.py` (150 lines)
`ArtifactManager` — file registration, storage, lifecycle. Handles 6 categories (document, text, image, audio, video, dataset, archive). Max 100MB per file.

### `diagram_utils.py` (70 lines)
Deterministic Mermaid diagram generation. `build_mermaid()` produces flowchart from validated graph with wave subgraphs and capability-based coloring.

---

## Package: `aos_v0.agents`

### `manager_agent.py` (576 lines)
LLM-based task decomposition. `ManagerAgent.create_plan()` → validates structurally + semantically → appends synthesis node → writes plan.md. Model: `openai/gpt-oss-120b`.

### `graph_executor.py` (275 lines)
Wave-by-wave concurrent execution. `GraphExecutor.run()` with ThreadPoolExecutor, input routing (data_inputs → parent outputs → root modality inference), vision/audio context propagation, artifact registration.

### `sub_agent.py` (230 lines)
Single-node executor. `SubAgent.perform()` with 3 routing paths (DNA → relaxed → exact fallback), delegates to FailureManager for execution with recovery.

### `integrator_agent.py` (35 lines)
Output collection. `IntegratorAgent.integrate()` returns synthesis node output if present, otherwise concatenates labeled sink outputs.

---

## Package: `aos_v0.capabilities`

### `web_search.py` (60 lines)
DuckDuckGo search + Groq fact extraction. Model: `qwen/qwen3.6-27b`. Emits `NO_DATA:` sentinel on empty results.

### `summarization.py` (50 lines)
Fact-preserving summarizer. Model: `qwen/qwen3.6-27b`. Instruction augments (not replaces) system prompt.

### `vision.py` (60 lines)
Ollama image description. Model: `medgemma1.5:latest` via localhost:11434. Base64 data URL encoding.

### `synthesis.py` (70 lines)
Final answer composition. Model: `qwen/qwen3.6-27b`. 5-rule system prompt. max_tokens=4096.

### `document.py` (40 lines)
PDF text extraction via pdfplumber. Deterministic, no LLM.

---

## Package: `aos_v0.providers`

### `hf.py` (1687 lines)
HuggingFace Inference Providers adapter. `HFProvider` class, `HFModelSpec` dataclass, 17-model catalog, 6 run functions (chat, ASR, audio chat, audio classification, image chat, token classification). `register_hf_resources()` for opt-in registration. `validate_hf_connection()` for token probe.

### `errors.py` (30 lines)
Typed provider errors: `ProviderError`, `ProviderAuthenticationError`, `ProviderRateLimitError`, `ProviderTimeoutError`, `ProviderBadRequestError`, `ProviderUnavailableError`.

---

## Package: `aos_v0.medical`

### `manifest.py` (404 lines)
Medical data structures + folder scanning + block parsing. `FileManifest`, `LabResult`, `ImagingFinding`, `Medication`, `MedicalCondition`, `PatientProfile`. `scan_folder()`, `classify_file()`, `emit_block()`, `parse_blocks()`.

### `clinical.py` (238 lines)
Clinical text extraction. `extract_pdf_pages()`, `extract_text()`, `parse_lab_lines()` (deterministic line-oriented parser), `extract_patient_info()`.

### `capabilities.py` (605 lines)
7 medical capability run functions. All follow `run(text, instruction=None) -> str`. Emit marker-wrapped `【MEDICAL:kind】` blocks.

### `registration.py` (103 lines)
Registers 7 medical capabilities into a CapabilityRegistry with calibrated priors.

### `workflow.py` (87 lines)
Medical workflow front-end. `build_medical_job()` assembles prompt with manifest and directive. `folder_from_prompt()` extracts FOLDER: hint.

### `hf_medical.py` (138 lines)
Thin HF wrappers: `medic_gemma_describe()` (MedGemma image chat), `medic_lab_ner_lines()` (lab NER), `medic_lab_ner()` (convenience wrapper).

---

## Package: `aos_v0.services`

### `resource_registration.py` (303 lines)
Registry builders. `build_default_registry()` (6 resources), `build_hf_enabled_registry()` (default + HF catalog). Hand-calibrated quality priors and cost/latency models.

### `evaluation.py` (334 lines)
Provider-agnostic benchmarking. `ExecutionMetrics`, `EvalCase`, `run_case()`, `benchmark()`, `summarize()`. Normalizes latency, captures tokens, classifies errors. Never fabricates cost or quality scores.

### `registry_export.py` (66 lines)
CLI utility. Exports registry to `data/outputs/registry_data.json`. Runnable as `python -m aos_v0.services.registry_export`.
