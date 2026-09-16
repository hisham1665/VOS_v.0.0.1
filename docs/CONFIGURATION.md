# CONFIGURATION.md — Environment & Configuration Reference

## Environment Variables

All configuration is via environment variables. No config files are used at runtime.

### Required

| Variable | Purpose | Example |
|----------|---------|---------|
| `GROQ_API_KEY` | Groq API key for text capabilities (web_search, summarization, synthesis, DNA extraction) | `gsk_...` |

### Optional — HuggingFace

| Variable | Default | Purpose |
|----------|---------|---------|
| `HF_TOKEN` | `""` | HuggingFace Inference Providers token. Required for HF model catalog. |
| `HUG` | `""` | Legacy fallback for `HF_TOKEN`. |
| `HF_PROVIDER` | `"auto"` | Provider routing: `"auto"` or explicit (e.g. `"together"`, `"nebius"`, `"groq"`). |
| `HF_MODEL` | `""` | Default HF model override. Empty = use `DEFAULT_HF_MODEL` (`Qwen/Qwen3-30B-A3B-Instruct`). |

### Optional — Medical

| Variable | Default | Purpose |
|----------|---------|---------|
| `MEDICAL_IMAGE_MODEL` | `"google/medgemma-1.5-4b-it"` | MedGemma model for medical image analysis. |
| `MEDICAL_LAB_MODEL` | `"genzeonplatform/healthcare-brain-laboratory-ner"` | NER model for laboratory value extraction. |

### Setup

```bash
cp .env.example .env
# Edit .env with real values
# .env is git-ignored — NEVER commit credentials
```

Or export directly:

```bash
export GROQ_API_KEY="gsk_..."
export HF_TOKEN="hf_..."
```

## Pipeline Configuration (cli.run parameters)

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `budget_usd` | `float` | `0.50` | Job-level cost budget. Drives per-node cost ceiling via ConstraintPolicy. |
| `registry` | `CapabilityRegistry` | `None` (builds default) | Override resource pool. |
| `manager` | `ManagerAgent` | `None` (creates new) | Override planning agent. |
| `dna_extractor` | `DNAExtractor` | `None` (creates new) | Override DNA extractor. |

## DNA Extractor Configuration

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `cheap_model` | `"openai/gpt-oss-20b"` | First-pass extraction model |
| `strong_model` | `"openai/gpt-oss-120b"` | Escalation model |
| `confidence_threshold` | `0.7` | Below this, escalate to strong model |

## Scoring Configuration (CapabilityRegistry)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `pessimising_factor` | `1.0` | Multiplier on acceptance rate |
| `optimising_factor` | `1.0` | Multiplier on rejection rate |
| `lambda_cost` | `0.3` | Cost penalty weight |
| `mu_latency` | `0.2` | Latency penalty weight |

Scoring formula:
```
score = quality_prior × pessimising_factor - λ·cost - μ·latency
```

## Failure Manager Configuration

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `max_attempts` | `2` | Max recovery attempts per node |

## TUI Dependencies

Optional, installed via:

```bash
pip install -e ".[tui]"
```

Adds: `textual>=0.58,<2.0`, `rich>=13,<15`

## Package Configuration (pyproject.toml)

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "aos-v0"
version = "0.0.5"
requires-python = ">=3.11"

[project.scripts]
aos = "aos_v0.cli:main"
aos-tui = "aos_v0.tui:main"
aos-registry-export = "aos_v0.services.registry_export:main"

[project.optional-dependencies]
tui = ["textual>=0.58,<2.0", "rich>=13,<15"]
```

## Dependencies

```
pydantic>=2.0,<3.0
requests>=2.31.0,<3.0
python-dotenv>=1.0.0,<2.0
groq>=0.13.0
openai>=1.0.0
ddgs>=9.0.0
huggingface_hub[inference]>=0.35.0
pdfplumber>=0.11.0
```

## File System Layout

```
data/
├── inputs/           # User-provided files
├── outputs/          # Generated plans and artifacts
│   ├── plan.md       # Execution plan (written by ManagerAgent)
│   └── registry_data.json  # Registry export
├── artifacts/        # ArtifactManager storage
log/                  # Session logs (SessionLogger)
```

## Client Factories (config.py)

```python
def insecure_http_client() -> httpx.Client
    # SSL-verification disabled for proxy environments

def make_groq(api_key: str) -> Groq
    # Uses insecure_http_client
```
