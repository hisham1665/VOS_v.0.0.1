# DATA_MODELS.md — All Data Structures

## Core Types (core/models.py)

### Capability DNA

```python
class DNAOrdinals(BaseModel):
    reasoning_depth: int = 0       # 0-4, how deep the reasoning required
    planning_horizon: int = 0      # 0-4, how many steps ahead
    tool_complexity: int = 0       # 0-4, how many tool interactions
    memory_dependence: int = 0     # 0-4, how much context needed
    parallelizability: int = 0     # 0-4, how parallelizable

    def demand(self) -> float:
        """Normalized 0-1 difficulty. = (reasoning + planning + tool) / 12"""
```

```python
class DNAConstraints(BaseModel):
    cost_ceiling_usd: float = 1.0      # Max resource cost
    latency_slo_ms: int = 120000       # Max latency (2 min default)
    min_quality: float = 0.0           # Min quality threshold
    risk_tolerance: RiskLevel = "medium"  # "low" | "medium" | "high"
```

```python
class CapabilityDNA(BaseModel):
    flags: List[str]                    # Required capability flags
    ordinals: DNAOrdinals               # Difficulty axes
    constraints: DNAConstraints         # Operational constraints
    confidence: float = 0.0             # Extraction confidence
    extracted_by: Optional[str]         # "cheap", "strong", "heuristic", "kernel"

    def effective_min_quality(self) -> float:
        """max(constraints.min_quality, ordinals.demand())"""
```

### Graph Structures

```python
class Node(BaseModel):
    id: str                              # Unique node identifier
    description: str                     # Task instruction
    capability: str                      # Coarse capability hint
    depends_on: List[str] = []           # Parent node IDs
    input: Optional[str] = None          # Runtime input
    output: str = ""                     # Produced result
    status: str = "pending"              # pending|running|done|degraded|failed
    performed_by: str = ""               # Selected resource ID
    dna: Optional[CapabilityDNA] = None  # Extracted DNA
    bound_resource: str = ""             # Bound resource ID
    routing_mode: str = "dna"            # dna|relaxed|exact
    data_inputs: List[str] = []          # Explicit artifact IDs

class Graph(BaseModel):
    job: str                             # Original user prompt
    nodes: List[Node]                    # Task nodes
    artifacts: dict[str, Artifact] = {}  # Artifact registry
```

### Artifacts

```python
class Artifact(BaseModel):
    id: str                              # "art_<uuid>"
    modality: str                        # text|image|audio|document|unknown
    name: Optional[str] = None           # Original filename
    artifact_type: str = "unknown"       # document|image|audio|video|dataset|archive|unknown
    mime_type: str                       # Detected MIME type
    size: int                            # File size in bytes
    path: str                            # Storage path
    source: str = "user_input"           # user_input|node:<node_id>
    metadata: Dict = {}                  # Extension, category, etc.
    lifecycle: str = "active"            # active|archived|deleted
```

### Capability Vocabulary Constants

```python
CAPABILITY_FLAGS: List[str]  # 57 flags in "family.specific" format
# Examples:
#   "web.search", "text.summarization", "vision.understanding",
#   "reasoning.deep", "reasoning.shallow", "answer.synthesis",
#   "document.extraction", "speech.transcription",
#   "medical.folder_ingestion", "medical.laboratory_analysis", ...

ORDINAL_FIELDS: List[str]  # 5 axes
#   ["reasoning_depth", "planning_horizon", "tool_complexity",
#    "memory_dependence", "parallelizability"]

RISK_LEVELS: List[str]  # ["low", "medium", "high"]
```

## Registry Types (core/capability_registry.py)

### Resource Manifest

```python
class CapabilityManifest(BaseModel):
    resource_id: str                          # Unique resource identifier
    resource_class: ResourceClass             # llm|vlm|asr|tts|tool|api|...
    capabilities: List[str]                   # DNA flags this resource provides
    input_schema: IOSchema                    # Accepted input type
    output_schema: IOSchema                   # Output type
    cost_model: CostModel                     # Cost per unit
    latency_model: LatencyModel               # p50/p95 latency
    quality_priors: Dict[str, float]          # Flag → quality score (0-1)
    availability: Availability                # Status + rate limit
    risk_class: str                           # "low"|"medium"|"high"
    metadata: Dict[str, str]                  # Provider, model, transport, etc.

    def quality_for(self, flags: List[str]) -> float:
        """Mean quality prior across requested flags. Default 0.5 for missing."""
```

```python
class IOSchema(BaseModel):
    type: Literal["text", "image", "audio", "document", "structured"]
    format: str

class CostModel(BaseModel):
    unit: Literal["per_1k_tokens", "per_call"]
    estimate_usd: float

class LatencyModel(BaseModel):
    p50_ms: int = 0
    p95_ms: int = 0

class Availability(BaseModel):
    status: Literal["up", "degraded", "down"] = "up"
    rate_limit_rpm: int = 30
```

### Scoring Types

```python
@dataclass
class DimensionDetail:
    name: str               # "flag_match"|"reasoning"|"planning"|"tool"|"cost"|"latency"|"quality"
    agent_value: float      # Resource's capability on this dimension
    task_value: float       # Task's requirement on this dimension
    weight: float           # Dimension weight
    contribution: float     # Score contribution
    accepted: bool          # Whether this dimension was satisfied

@dataclass
class DNAScore:
    resource_id: str
    score: float            # Combined score
    acceptance_rate: float  # Sum of positive contributions
    rejection_rate: float   # Sum of negative contributions
    dimensions: List[DimensionDetail]
    quality: float
    cost_usd: float
    latency_ms: int

@dataclass
class SelectionResult:
    resource_id: str        # Winner
    score: float
    quality: float
    cost_usd: float
    latency_ms: int
    all_scores: List[DNAScore]

    @property
    def runner_up(self) -> Optional[str]
    @property
    def runner_up_margin(self) -> Optional[float]
    @property
    def candidates(self) -> List[DNAScore]
```

### Resource Classes (Literal)

```python
ResourceClass = Literal[
    "llm", "vlm", "asr", "tts", "tool", "api",
    "database", "sensor", "robot", "embedder",
    "reranker", "audio", "image"
]
```

## Runtime Types (core/runtime.py)

```python
class RequestContext(BaseModel):
    request_id: str              # "req_<uuid>"
    session_id: str
    user_input: str
    artifacts: List[Artifact]
    metadata: Dict

    @classmethod
    def create(cls, session_id, user_input, artifacts=None) -> "RequestContext"

class Telemetry(BaseModel):
    request_latency_ms: float = 0
    capability_latency_ms: float = 0
    routing_latency_ms: float = 0
    artifact_processing_latency_ms: float = 0
    agent_latencies_ms: Dict[str, float] = {}
    failures: int = 0
    recoveries: int = 0
    model_used: List[str] = []

class Session(BaseModel):
    session_id: str                    # "aos_<uuid>"
    requests: List[RequestContext]
    artifacts: List[Artifact]
    events: List[OrchestrationEvent]
    results: Dict[str, str]
    telemetry: Telemetry
    started_at: float

    def record_event(self, event) -> None
    def summary(self) -> dict          # Stats dict
```

## Event Types (core/events.py)

```python
class EventType(StrEnum):
    REQUEST_RECEIVED
    INTENT_DETECTED
    CAPABILITY_ANALYSIS_STARTED
    CAPABILITY_DETECTED
    ROUTING_STARTED
    AGENT_SELECTED
    AGENT_STARTED
    AGENT_COMPLETED
    AGENT_FAILED
    RECOVERY_STARTED
    RECOVERY_COMPLETED
    RESULT_READY
    REQUEST_COMPLETED

@dataclass(frozen=True)
class OrchestrationEvent:
    type: EventType
    request_id: str
    payload: Dict[str, Any]
    timestamp: datetime   # UTC
```

## Failure Types (core/failure_manager.py)

```python
@dataclass
class Detection:
    failure_class: str    # "resource_outage"|"resource_degraded"|"empty_result"|"output_corrupt"|"reasoning_refusal"
    symptom: str

@dataclass
class RecoveryAttempt:
    strategy: str         # "retry_same"|"retry_with_feedback"|"resource_substitution"
    resource_id: str
    succeeded: bool
    detail: str

@dataclass
class NodeOutcome:
    node_id: str
    detections: List[Detection]
    attempts: List[RecoveryAttempt]
    recovered: bool
    degraded: bool
    elapsed_ms: int

    def summary(self) -> str  # "healthy" | "recovered from X via Y" | "DEGRADED, unrecovered X"
```

## Medical Types (medical/manifest.py)

```python
class FileManifest(BaseModel):
    filename: str
    relative_path: str
    extension: str
    mime_type: str
    file_size: int
    detected_type: str          # "pdf"|"image"|"text"|"unknown"
    processing_status: str      # "pending"|"processed"|"error"
    category: str               # "laboratory_report"|"radiology"|"prescription"|"..."
    absolute_path: str
    page_count: Optional[int]
    diagnosis: Optional[str]
    notes: Optional[str]

class LabResult(BaseModel):
    test: str                   # "Glucose"
    value: str                  # "142"
    unit: str                   # "mg/dL"
    reference_range: str        # "70-100"
    status: str                 # "HIGH"|"LOW"|"NORMAL"
    source_file: str
    page: Optional[int]
    source_type: str
    note: Optional[str]

class ImagingFinding(BaseModel):
    finding: str                # Description
    source: str                 # Source file
    evidence: str               # Supporting evidence
    status: str                 # "Requires physician/radiologist confirmation"

class Medication(BaseModel):
    medicine: str
    dose: Optional[str]
    frequency: Optional[str]
    duration: Optional[str]
    source: str
    page: Optional[int]
    source_type: str
    note: Optional[str]

class MedicalCondition(BaseModel):
    condition: str
    source: str
    page: Optional[int]
    source_type: str
    note: Optional[str]

class PatientProfile(BaseModel):
    name: Optional[str]
    age: Optional[str]
    sex: Optional[str]
    notes: Optional[str]
```

## Provider Types (providers/hf.py)

```python
@dataclass
class HFResult:
    text: str
    model: Optional[str] = None
    provider: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    raw: Any = None

@dataclass(frozen=True)
class HFModelSpec:
    resource_id: str
    model: str
    resource_class: str
    task: str
    capabilities: List[str]
    capability_provenance: Dict[str, str]   # "documented"|"inferred"
    input_type: str
    output_type: str
    output_format: str
    interface: str                           # "chat_completion"|"automatic_speech_recognition"|...
    params: str
    context_length: str
    modality: str
    source: str
    price: str
    system: str = ""
    temperature: float = 0.2
    max_tokens: int = 2048
```

## Error Types (providers/errors.py)

```python
class ProviderError(RuntimeError)           # Base
class ProviderAuthenticationError(ProviderError)  # 401/403
class ProviderRateLimitError(ProviderError)        # 429
class ProviderTimeoutError(ProviderError)          # Timeout
class ProviderBadRequestError(ProviderError)       # 400/404/422
class ProviderUnavailableError(ProviderError)      # 5xx/network
```

## Exceptions

```python
class GraphValidationError(Exception)       # graph_utils.py
class InfeasibleDNAError(RuntimeError):     # capability_registry.py
    dna: CapabilityDNA
    rejections: Dict[str, str]

class ArtifactError(ValueError)             # artifacts.py
class ArtifactModalityMismatch(RuntimeError):  # models.py
    node_id: str
    artifact_id: str
    expected: str
    actual: str
```
