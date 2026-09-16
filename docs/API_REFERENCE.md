# API_REFERENCE.md — Complete API Reference

## cli.py — Pipeline Orchestrator

```python
def run(
    user_prompt: str,
    inputs: dict[str, str] | None = None,
    budget_usd: float = 0.50,
    context: RequestContext | None = None,
    event_sink: Callable | None = None,
    *,
    registry: CapabilityRegistry | None = None,
    manager: ManagerAgent | None = None,
    dna_extractor: DNAExtractor | None = None,
) -> str
```
Core pipeline. Returns final integrated answer string.

```python
def main(args: list[str] | None = None) -> None
```
CLI entry point. Parses `--budget`, `--medical-folder`, `--input`, `--audio`, `--image`.

---

## agents/manager_agent.py

```python
class ManagerAgent:
    def create_plan(
        self,
        user_prompt: str,
        inputs: dict[str, str] | None = None,
    ) -> Graph
```
Decomposes prompt into validated task graph with synthesis node appended.

```python
    @staticmethod
    def write_plan(graph: Graph) -> None
```
Renders `data/outputs/plan.md`.

---

## agents/graph_executor.py

```python
class GraphExecutor:
    def __init__(
        self,
        registry: CapabilityRegistry,
        failure_manager: FailureManager | None = None,
        event_sink: Callable | None = None,
        request_id: str = "",
    ) -> None

    def run(
        self,
        graph: Graph,
        inputs: dict[str, str] | None = None,
    ) -> Graph
```
Executes all nodes wave-by-wave. Returns graph with outputs filled.

---

## agents/sub_agent.py

```python
class SubAgent:
    def __init__(
        self,
        name: str,
        capability: str,
        registry: CapabilityRegistry,
        failure_manager: FailureManager | None = None,
        event_sink: Callable | None = None,
        request_id: str = "",
    ) -> None

    def perform(self, node: Node) -> Node
```
Executes a single node. Routes, invokes via FailureManager, returns updated node.

---

## agents/integrator_agent.py

```python
class IntegratorAgent:
    def integrate(self, graph: Graph) -> str
```
Extracts final answer from sink nodes or synthesis node.

---

## core/models.py

```python
def risk_rank(level: str) -> int
```
Returns index of risk level in RISK_LEVELS list.

```python
class DNAOrdinals(BaseModel):
    reasoning_depth: int = 0
    planning_horizon: int = 0
    tool_complexity: int = 0
    memory_dependence: int = 0
    parallelizability: int = 0
    def demand(self) -> float

class DNAConstraints(BaseModel):
    cost_ceiling_usd: float = 1.0
    latency_slo_ms: int = 120000
    min_quality: float = 0.0
    risk_tolerance: RiskLevel = "medium"

class CapabilityDNA(BaseModel):
    flags: List[str]
    ordinals: DNAOrdinals
    constraints: DNAConstraints
    confidence: float = 0.0
    extracted_by: Optional[str] = None
    def effective_min_quality(self) -> float

class Artifact(BaseModel):
    id: str
    modality: str
    name: Optional[str] = None
    artifact_type: str = "unknown"
    mime_type: str
    size: int
    path: str
    source: str = "user_input"
    metadata: Dict = {}
    lifecycle: str = "active"

class Node(BaseModel):
    id: str
    description: str
    capability: str
    depends_on: List[str] = []
    input: Optional[str] = None
    output: str = ""
    status: str = "pending"
    performed_by: str = ""
    dna: Optional[CapabilityDNA] = None
    bound_resource: str = ""
    routing_mode: str = "dna"
    data_inputs: List[str] = []

class Graph(BaseModel):
    job: str
    nodes: List[Node]
    artifacts: dict[str, Artifact] = {}
```

---

## core/graph_utils.py

```python
class GraphValidationError(Exception): ...

def validate_graph(graph: Graph) -> None
    """Raises GraphValidationError on invalid graph."""

def build_waves(graph: Graph) -> list[list[Node]]
    """Returns topologically sorted waves."""

def get_sink_nodes(graph: Graph) -> list[Node]
    """Returns terminal nodes with no dependents."""
```

---

## core/capability_registry.py

```python
def missing_flags(dna: CapabilityDNA, provided: List[str]) -> List[str]
def required_input_modality(dna_flags: List[str], capability: str = "") -> Optional[str]

class CapabilityManifest(BaseModel):
    # ... (see DATA_MODELS.md)
    def quality_for(self, flags: List[str]) -> float

class CapabilityRegistry:
    def __init__(
        self,
        pessimising_factor: float = 1.0,
        optimising_factor: float = 1.0,
        weights: DimensionWeights | None = None,
        lambda_cost: float = 0.3,
        mu_latency: float = 0.2,
    ) -> None

    def register(self, manifest: CapabilityManifest, run_fn: Callable) -> None
    def describe(self, resource_id: str) -> CapabilityManifest
    def manifests(self) -> List[CapabilityManifest]
    def run_fn(self, resource_id: str) -> Callable
    def provided_flags(self) -> List[str]
    def routable_manifests(self) -> List[CapabilityManifest]
    def routable_ids(self) -> List[str]
    def routable_for_modality(self, modality: Optional[str]) -> List[CapabilityManifest]
    def find(self, flags: List[str]) -> List[str]
    def find_by_capability(self, capability: str) -> Callable
    def unsatisfiable_flags(self, dna: CapabilityDNA) -> List[str]
    def score_against_dna(self, manifest: CapabilityManifest, dna: CapabilityDNA) -> DNAScore
    def select(self, dna: CapabilityDNA, required_modality: Optional[str] = None) -> SelectionResult
    def bind(self, dna: CapabilityDNA, required_modality: Optional[str] = None) -> Tuple[SelectionResult, Callable]
    def feasible(self, dna: CapabilityDNA) -> Tuple[List[CapabilityManifest], Dict[str, str]]

    @staticmethod
    def is_routable(manifest: CapabilityManifest) -> bool
    @staticmethod
    def accepts_input(manifest: CapabilityManifest, modality: Optional[str]) -> bool

class InfeasibleDNAError(RuntimeError):
    dna: CapabilityDNA
    rejections: Dict[str, str]
```

---

## core/dna_extractor.py

```python
class DNAExtractor:
    def __init__(
        self,
        cheap_model: str = "openai/gpt-oss-20b",
        strong_model: str = "openai/gpt-oss-120b",
        confidence_threshold: float = 0.7,
        client: Groq | None = None,
    ) -> None

    def extract_graph(self, graph: Graph) -> Graph
    def extract(self, node: Node, job: str) -> CapabilityDNA
```

---

## core/constraint_policy.py

```python
class ConstraintPolicy:
    def __init__(
        self,
        registry: CapabilityRegistry,
        job_budget_usd: float = 0.50,
        job_latency_slo_ms: int | None = None,
    ) -> None

    def derive(self, dna: CapabilityDNA, node_count: int) -> DNAConstraints
    def apply(self, graph: Graph) -> None
```

---

## core/failure_manager.py

```python
class FailureManager:
    def __init__(
        self,
        registry: CapabilityRegistry,
        max_attempts: int = 2,
    ) -> None

    def detect(self, node: Node, output: Optional[str], error: Optional[Exception]) -> List[Detection]
    def execute(
        self,
        node: Node,
        primary_fn: Callable,
        primary_resource_id: str,
        candidate_ids: List[str],
        log: Callable = print,
        event_sink: Callable | None = None,
        request_id: str = "",
    ) -> str
    def report(self) -> str

    @staticmethod
    def classify(detections: List[Detection]) -> Optional[str]
```

---

## core/events.py

```python
class EventBus:
    def subscribe(self, listener: Callable[[OrchestrationEvent], None]) -> Callable[[], None]
    def emit(self, event: OrchestrationEvent) -> None
```

---

## core/artifacts.py

```python
class ArtifactManager:
    def __init__(
        self,
        storage_dir: str | Path,
        max_size_bytes: int = 100 * 1024 * 1024,
        copy_files: bool = True,
    ) -> None

    def register(self, file_path: str | Path) -> Artifact
    def register_many(self, paths: Iterable[str | Path]) -> list[Artifact]
    def get(self, artifact_id: str) -> Optional[Artifact]
    def list(self) -> list[Artifact]
    def remove(self, artifact_id: str, delete_stored_file: bool = False) -> bool
    def cleanup(self) -> int
    @staticmethod
    def detect_type(path: str | Path) -> str
```

---

## core/diagram_utils.py

```python
def build_mermaid(graph: Graph, waves: List[List[Node]]) -> str
    """Deterministic Mermaid flowchart from validated graph."""
```

---

## core/runtime.py

```python
class RequestContext(BaseModel):
    @classmethod
    def create(cls, session_id: str, user_input: str, artifacts: List[Artifact] | None = None) -> "RequestContext"

class Session(BaseModel):
    def record_event(self, event: OrchestrationEvent) -> None
    def summary(self) -> dict
```

---

## capabilities/*.py

```python
# web_search.py
def run(query: str, instruction: Optional[str] = None) -> str

# summarization.py
def run(text: str, instruction: Optional[str] = None) -> str

# vision.py
def run(image_path: str, instruction: Optional[str] = None) -> str

# synthesis.py
def run(text: str, instruction: Optional[str] = None) -> str

# document.py
def run(doc_path: str, instruction: Optional[str] = None) -> str
```

---

## providers/hf.py

```python
class HFProvider:
    def __init__(self, *, token=None, model=None, provider=None, timeout=None, base_url=None, client=None) -> None
    def execute(self, model=None, messages=None, **generation_parameters) -> HFResult

def build_hf_manifests(*, provider=None) -> List[CapabilityManifest]
def register_hf_resources(registry, *, provider=None, client=None) -> List[CapabilityManifest]
def validate_hf_connection(token=None) -> str | None
def run(text, instruction=None, *, model=None, provider=None, system=None, temperature=0.3, max_tokens=2048, client=None) -> str
def run_result(text, instruction=None, *, model=None, provider=None, system=None, temperature=0.3, max_tokens=2048, client=None) -> HFResult
```

---

## services/resource_registration.py

```python
def build_default_registry() -> CapabilityRegistry
def build_hf_enabled_registry(provider: str | None = None) -> CapabilityRegistry
```

---

## services/evaluation.py

```python
def classify_error(exc: Exception) -> str
def normalize_result(*, raw, resource_id, provider, model, task, latency_ms, error, expected) -> ExecutionMetrics
def run_case(*, invoke, resource_id, input_text, instruction, provider, model, task, expected) -> ExecutionMetrics
def run_case_on(registry, case: EvalCase) -> ExecutionMetrics
def benchmark(registry, cases: List[EvalCase]) -> List[ExecutionMetrics]
def summarize(records: List[ExecutionMetrics]) -> Dict[str, Dict[str, Any]]
```

---

## medical/workflow.py

```python
def build_medical_job(folder: str | Path, user_prompt: str | None = None) -> tuple[str, list[FileManifest]]
def folder_from_prompt(prompt: str) -> Optional[str]
```

---

## medical/registration.py

```python
def register_medical_resources(registry: CapabilityRegistry) -> CapabilityRegistry
```

---

## medical/manifest.py

```python
def classify_file(path: Path, detected_type: str = "") -> dict
def scan_folder(folder: str | Path) -> List[FileManifest]
def emit_block(kind: str, payload) -> str
def parse_blocks(text: str) -> Dict[str, list]
def parse_manifest(text: str) -> List[FileManifest]
def folder_hint(text: str) -> Optional[str]
```

---

## medical/clinical.py

```python
def extract_pdf_pages(path: str | Path) -> List[Tuple[int, str]]
def extract_text(path: str | Path) -> str
def find_on_page(text: str, keyword: str) -> Optional[int]
def parse_lab_lines(page_text: str, source: str, default_page: int | None = None) -> List[LabResult]
def merge_statuses(documented: str | None, computed: str | None) -> str
def extract_patient_info(text: str, source: str = "") -> PatientProfile
```

---

## medical/hf_medical.py

```python
def medic_gemma_describe(image_path: str, instruction: str | None = None, model: str | None = None, temperature=0.2, max_tokens=1024) -> str
def medic_lab_ner_lines(text: str, model: str | None = None) -> str
def medic_lab_ner(text: str, source: str = "", page: int | None = None, model: str | None = None) -> List[LabResult]
```

---

## interactive.py

```python
class InteractiveService:
    def __init__(self, storage_dir: str | Path = "data/artifacts") -> None
    def upload(self, paths: list[str]) -> list[Artifact]
    def submit(self, prompt: str, artifact_ids: list[str] | None = None, budget_usd: float = 0.50) -> str
    def subscribe(self, listener: Callable[[OrchestrationEvent], None]) -> None
```

---

## logbook.py

```python
class SessionLogger:
    def __init__(self, prompt: str = "", tag: str = "run") -> None
    def start(self) -> Path
    def stop(self) -> None
    def __enter__(self) -> "SessionLogger"
    def __exit__(self, *exc) -> None
```
