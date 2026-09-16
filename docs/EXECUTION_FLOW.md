# EXECUTION_FLOW.md — Pipeline Execution Walkthrough

## Complete Pipeline (cli.run)

```python
# Entry: cli.py
def run(
    user_prompt: str,
    inputs: dict[str, str] | None = None,
    budget_usd: float = 0.50,
    context: RequestContext | None = None,
    event_sink=None,
    *,
    registry=None,
    manager=None,
    dna_extractor=None,
) -> str
```

### Step-by-step:

```
1.  Create Session + SessionLogger
2.  Emit REQUEST_RECEIVED event
3.  ManagerAgent.create_plan(user_prompt, inputs)
    │
    ├── LLM call → initial Graph
    ├── validate_graph() → structural check
    │   └── retry on GraphValidationError (max 1)
    ├── _check_completeness() → semantic audit
    │   └── retry on INCOMPLETE (max 1)
    ├── _append_synthesis_node() → terminal "final" node
    ├── validate_graph() → final structural check
    └── write_plan() → data/outputs/plan.md
    │
    ▼  Graph returned
4.  DNAExtractor.extract_graph(graph)
    │
    ├── For each node (skip kernel-authored DNAs):
    │   ├── Try cheap model → CapabilityDNA
    │   ├── If confidence < 0.7 → escalate to strong model
    │   └── If both fail → heuristic fallback
    └── Return annotated graph
    │
    ▼  Graph with node.dna filled
5.  ConstraintPolicy(graph, registry, budget).apply()
    │
    └── For each node with DNA:
        ├── cost_ceiling = max(budget/N, cheapest_pool_cost)
        ├── latency_slo = max(job_slo/N, pool_p95 * 1.5)
        ├── min_quality = lerp(0.45, 0.85, demand)
        └── risk = "high" if external-data flags, else "low"
    │
    ▼  Constraints derived from actual pool
6.  Admission Control (_check_satisfiable)
    │
    └── For each node: check unsatisfiable_flags()
        └── Raise RuntimeError if any node's flags can't be met
    │
    ▼  All nodes satisfiable
7.  GraphExecutor.run(graph, inputs)
    │
    ├── build_waves(graph) → topological waves
    ├── Register input artifacts (audio/image/document)
    ├── For each wave:
    │   └── ThreadPoolExecutor:
    │       └── For each node in wave:
    │           ├── Resolve input (data_inputs → parent outputs → root routing)
    │           ├── Inject vision/audio/document context for non-media nodes
    │           ├── SubAgent.perform(node)
    │           │   ├── _route(node) → (fn, resource_id, substitutes)
    │           │   │   ├── DNA-based: registry.select(dna)
    │           │   │   ├── Relaxed fallback: any overlap
    │           │   │   └── Exact fallback: capability string match
    │           │   └── failure_manager.execute(node, fn, resource_id, substitutes)
    │           │       ├── detect() → classification
    │           │       ├── If healthy → return output
    │           │       └── If failed → recovery ladder
    │           │           ├── retry_same
    │           │           ├── retry_with_feedback
    │           │           └── resource_substitution
    │           ├── Register output artifact
    │           └── Emit AGENT_COMPLETED event
    └── Return completed graph
    │
    ▼  Graph with all node.output filled
8.  IntegratorAgent.integrate(graph)
    │
    ├── Get sink nodes
    ├── If synthesis node → return its output
    └── Otherwise → concatenate labeled sink outputs
    │
    ▼  Final string
9.  Emit REQUEST_COMPLETED event
10. Return final string to caller
```

## Medical Pipeline Variant

When `--medical-folder` is specified:

```
1.  scan_folder(folder) → FileManifest[]
2.  build_medical_job(folder, prompt) → job_prompt with embedded manifest
3.  register_medical_resources(registry) → adds 7 medical capabilities
4.  Standard pipeline (steps 3-10 above)
```

The kernel doesn't know it's running medical — medical capabilities are ordinary manifests.

## Interactive Pipeline (InteractiveService)

```
1.  User uploads files → ArtifactManager.register()
2.  User submits prompt
3.  ArtifactManager builds inputs dict from modality/path
4.  RequestContext created with selected artifacts
5.  cli.run(prompt, inputs, context) → standard pipeline
6.  Result stored in Session
7.  Events forwarded to EventBus subscribers
```

## Input Routing Logic (inside GraphExecutor)

For each node, input is resolved in priority order:

```
1. If node.data_inputs is set:
   └── Route specified artifact by ID, validate modality
2. If node.depends_on is non-empty:
   └── Concatenate parent outputs with labels
3. If root node (no deps, no data_inputs):
   ├── If DNA has audio flags → route input_audio
   ├── If DNA has image flags → route input_image
   ├── If DNA has document flags → route input_document
   └── Otherwise → route graph.job (the prompt text)
```

## Resource Selection Paths (SubAgent._route)

```
DNA exists?
├── Yes → registry.select(dna, modality)
│   ├── Success → bind winner, record substitutes
│   └── InfeasibleDNAError → _route_relaxed()
│       └── Any resource with 1+ overlapping flag
│           └── If none → _route_exact()
└── No → _route_exact()
    └── registry.find_by_capability(capability_string)
        └── Raises KeyError if missing
```

Routing modes recorded on node: `"dna"`, `"relaxed"`, `"exact"`.
