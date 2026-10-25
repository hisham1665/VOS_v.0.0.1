# Models and configuration

## 1. Model families

Medical AOS deliberately uses several model families rather than a single
universal model. Each family has a different role and failure profile.

| Family | Default/representative model | Role | Typical route |
|---|---|---|---|
| General CLIP | `openai/clip-vit-base-patch32` | Reject obvious non-medical images | Local |
| PubMedCLIP | `flaviagiammarino/pubmed-clip-vit-base-patch32` | Modality/body-region triage | Local |
| Modality-specific screeners | NIH chest, head-CT, lung-CT, brain-MRI entries in `model_zoo.py` | Candidate generation | Local supervised inference |
| Disease specialists | Pneumonia, TB, stroke, tumour, TorchXRayVision, etc. | Independent candidate re-check | Local where available |
| Medical VLM | `google/medgemma-4b-it`, fallback Qwen VLM | Narrative image read | Ollama, hosted, or large local GPU |
| Text LLM | `openai/gpt-oss-120b` and fast extraction profile | Planning, reconciliation, report reasoning | Groq/HF/local compatible backend |
| Search/text synthesis | DDGS plus text LLM | Background literature context | Network + text backend |

The model IDs are operator configuration, not a medical validation claim.

## 2. Model zoo and coverage

The catalog in `capabilities/model_zoo.py` stores model ID, role, modality,
region, label mapping, targets, threshold, backend, size hint, benchmark notes,
and enabled state. The catalog is the routing source of truth.

### Supported supervised areas

The currently useful supervised coverage is concentrated in:

- chest X-ray: NIH-style broad screen plus pneumonia/TB/XRV specialist checks;
- head CT: haemorrhage/abnormality screen plus stroke specialist;
- chest CT: lung-cancer screen;
- brain MRI: tumour screen plus second tumour model and optional dementia model.

### Deliberately unsupported or disabled areas

Most abdominal CT, ultrasound, mammography, PET, pathology, spine, limb, pelvis,
and many breast/neck cases have no trusted supervised screener. Fracture and
pneumothorax checkpoints that failed development benchmarking are disabled.

The resulting behavior is intentionally explicit:

```text
triage succeeds -> VLM may still read the image
no matching screener -> screening status = not screened
no independent specialist -> confirmation = unverifiable
```

No supervised silence is rendered as normality.

## 3. Two-stage cascade logic

The cascade is designed as:

```text
triage -> stage-1 screener -> candidate conditions
                         -> stage-2 specialist(s)
                         -> VLM/consolidation with scoped evidence
```

Stage 1 is recall-oriented and may emit a broad `abnormal` candidate when no
single condition crosses threshold but aggregate abnormality does. Stage 2 maps
that candidate to applicable independent specialists.

Verdict thresholds are model-specific. The general convention is:

```text
probability >= support_at -> supported
probability <= refute_at  -> not_supported
no independent model       -> unverifiable
```

TorchXRayVision's operating-point output and the failed pneumothorax benchmark
are handled with target-specific thresholds rather than a generic probability
assumption.

## 4. VLM routing

`capabilities/vision.py` supports:

- `ollama`: local OpenAI-compatible Ollama route;
- `openai`: local OpenAI-compatible server route, such as llama.cpp;
- `hf`: hosted Hugging Face route;
- `local`: local Transformers route; and
- `auto`: prefer reachable local vision, then hosted, then sufficiently large
  local hardware.

Ollama discovery checks `/api/tags` and `/api/show`, and requires the selected
model to advertise `vision`. Explicit model choices are not silently replaced.
The local server semaphore protects small GPUs from concurrent image contexts.

## 5. Text routing and selection

`core.routing.CapabilityDNA` declares skills, inputs, outputs, domain, tools,
context capacity, memory, cost, expected latency, priority, and alternatives.
`text_catalog.py` provides task-specific profiles such as:

- extraction;
- clinical reasoning;
- reconciliation;
- planning;
- summarization; and
- conversation.

Selection filters hard incompatibilities first. It then combines declared
priority, reliability history, latency, and preferred capability. Recent failures
can place an endpoint in a short cooldown. This measures provider execution
reliability, not clinical competence.

## 6. Environment configuration

Important settings include:

```dotenv
HF_TOKEN=...
GROQ_API_KEY=...
MAOS_VLM_BACKEND=auto
MAOS_VLM_URL=http://localhost:8080/v1
MAOS_VLM_MODEL=medgemma-1.5-4b
MAOS_OLLAMA_MODEL=medgemma1.5:latest
MAOS_MAX_WORKERS=8
MAOS_MAX_GPU_WORKERS=1
MAOS_MAX_IMAGES=0
MAOS_MAX_IMAGE_EDGE=896
MAOS_MAX_FILE_MB=100
MAOS_MAX_DOCUMENT_PAGES=200
MAOS_DOCUMENT_CHUNK_CHARS=8000
MAOS_MAX_RESIDENT_MODELS=4
MAOS_CACHE=1
MAOS_DEIDENTIFY=1
```

The configuration is loaded by `config.py`; `python main.py --doctor` reports
redacted configuration, model availability, optional dependencies, model-zoo
coverage, DICOM/document support, Ollama status, and problems.

## 7. Caching

The cache is content-addressed under `.cache/` and is safe to delete. Important
namespaces include:

| Namespace | Key includes | Purpose |
|---|---|---|
| `prepared` | source hash, edge, JPEG settings, DICOM/de-identification state | Decode/resize/re-encode once |
| `classification` | model ID and prepared-pixel hash | Reuse classifier outputs |
| `triage` | triage model IDs and prepared-pixel hash | Reuse routing |
| `vision` | route/model, system prompt, full read prompt, prepared hash | Reuse image reads only when context is unchanged |
| `document_analysis` | backend/profile/prompt/native text/image hash/source ref | Reuse validated page extraction |
| `clinical_report` | full reasoning prompt and model catalog | Reuse final reasoning when evidence/configuration is unchanged |

The full prompt is part of the VLM/report key. A changed history, referring
question, model profile, or prepared pixel representation therefore invalidates
the relevant result.

## 8. Resource and deployment profiles

### Small GPU, approximately 4 GB

- keep CLIP/classifiers local;
- use Ollama/llama.cpp quantized VLM or hosted VLM;
- `MAOS_MAX_GPU_WORKERS=1`;
- use model LRU eviction; and
- expect first-load/download latency.

### Large GPU

The VLM and screening models can remain local, reducing outbound image exposure.
The local route still requires an explicit, tested model/projector combination.

### CPU-only

Use smaller local classifiers where practical, constrain batch size, and route
larger text/vision calls to a permitted backend. Latency is expected to increase.

### Privacy-first deployment

Use local vision/text servers, encrypt `.cache/` and `sessions/`, and treat
uploaded raw DICOM/documents as patient data. Hosted inference is a deployment
choice with contractual and regulatory implications; the code cannot supply a
provider agreement.
