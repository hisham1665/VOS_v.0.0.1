# AOS v1.0 configuration

AOS reads operator policy from `aos.yaml` and credentials from `.env`. YAML is
validated with Pydantic when settings are loaded; unknown fields inside a
declared settings object are rejected, while named model and capability entries
are intentionally extensible.
Use `AOS_CONFIG=/path/to/profile.yaml` to select another configuration file.
Model profiles are merged with built-in defaults, so a profile may override only
the fields it needs.

```bash
cp .env.example .env
python -m pip install -e ".[tui]"
python -m aos_v0 "Summarize the supplied document" --input report.pdf
```

## Configuration file

The repository's `aos.yaml` is the source-of-truth example:

```yaml
schema_version: 1

runtime:
  max_workers: 4
  call_timeout_s: 90
  max_elapsed_s: 600
  max_retries: 2
  max_nodes: 64
  budget_usd: 0.50
  completeness_check: false
  output_dir: data/outputs
  log_dir: log

memory:
  enabled: true
  reuse: true
  max_entries: 64
  max_value_bytes: 32000
  max_total_bytes: 512000
  min_relevance: 0.15
  log_decisions: false

models:
  text:
    provider: groq
    model: openai/gpt-oss-20b
    api_key_env: GROQ_API_KEY
  planner:
    provider: groq
    model: openai/gpt-oss-120b
    api_key_env: GROQ_API_KEY
    max_tokens: 4096
  vision:
    provider: ollama
    model: medgemma1.5:latest
    api_key_env: ""
    base_url: http://localhost:11434/v1

capabilities: {}
```

### Runtime settings

| Setting | Default | Purpose |
|---|---:|---|
| `max_workers` | `4` | Validated concurrency setting; the current executor uses the compatibility `AOS_MAX_WORKERS` value |
| `call_timeout_s` | `90` | Timeout applied to provider calls |
| `max_elapsed_s` | `600` | Validated request elapsed-time budget; enforcement is not yet wired into the runner |
| `max_retries` | `2` | Validated retry-policy value; `FailureManager` currently uses its constructor default |
| `max_nodes` | `64` | Maximum planned graph nodes |
| `max_replans` | `2` | Reserved bounded replanning limit |
| `max_added_nodes` | `4` | Reserved bounded repair expansion limit |
| `context_chars` | `24000` | Total context characters available for memory retrieval |
| `per_source_chars` | `6000` | Per-memory-source context limit |
| `budget_usd` | `0.50` | Default request budget |
| `completeness_check` | `false` | Validated policy flag; the current manager performs its completeness check unconditionally |
| `verify_tls` | `true` | Verify provider TLS certificates |
| `output_dir` | `data/outputs` | Generated plan/output directory |
| `log_dir` | `log` | Session and decision-log directory |

The current CLI exposes `--budget USD`, `--config PATH`, `--input PATH`,
`--image PATH`, `--audio PATH`, `--use-inputs-folder`, and `--no-memory`.
For the current executor, set `AOS_MAX_WORKERS` when changing concurrency; keep
it aligned with `runtime.max_workers` so configuration and execution agree.

## Memory settings

Memory is request-scoped by default. The interactive service can attach a
session tier and promote selected entries between requests.

| Setting | Default | Purpose |
|---|---:|---|
| `enabled` | `true` | Enable the shared-memory service |
| `reuse` | `true` | Validated memory policy; exact reuse is currently enabled by the executor when memory is enabled |
| `max_entries` | `64` | Entry count limit per bank |
| `max_value_bytes` | `32000` | Maximum stored value size per entry |
| `max_total_bytes` | `512000` | Maximum stored value size per bank |
| `min_substance_chars` | `200` | Reject trivial admission candidates |
| `min_relevance` | `0.15` | Retrieval relevance floor |
| `ttl_s` | `3600` | Validated retention policy value; expiration is not yet enforced |
| `log_decisions` | `false` | Write JSONL admission decisions |
| `log_content` | `false` | Validated content-logging policy value; current decision logging truncates content fields |

Set `AOS_MEMORY=off` for a memory-free run. The CLI `--no-memory` flag sets the
same process-level override. Memory decisions, when enabled, are written under
`log/memory_decisions/`; memory dumps are written under `log/` by the current
request pipeline.

## Model profiles and providers

Each profile supports `provider`, `model`, `api_key_env`, optional `base_url`,
and `max_tokens`. Built-in provider values are `groq`, `openai`, `ollama`, and
`compatible`. AOS creates provider clients lazily, disables hidden SDK retries,
and applies `call_timeout_s`.

The built-in profiles are `text`, `planner`, `dna`, `dna_strong`, `synthesis`,
`vision`, and `audio`. The YAML file may override or add profiles, but all of
these names must remain available after defaults are merged.

```yaml
models:
  text:
    provider: openai
    model: gpt-4o-mini
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    max_tokens: 2048
```

The optional Hugging Face adapter has separate environment settings:
`HF_TOKEN` (or legacy `HUG`), `HF_PROVIDER`, and `HF_MODEL`. The expanded
catalog is opt-in through the registry builder and does not replace the default
resource pool.

## Adding a capability

Configured capabilities are registered from the `capabilities` mapping without
editing the kernel. A `chat` capability is text-only and uses a model profile:

```yaml
capabilities:
  contract_review:
    handler: chat
    flags: [domain.specific, reasoning.deep]
    model_profile: text
    system_prompt: Review the supplied contract and list evidence-linked risks.
    output_type: text
    quality: 0.8
    risk: medium
```

For deterministic or local integrations, use a Python adapter:

```yaml
capabilities:
  spreadsheet_reader:
    handler: python
    callable: my_adapters.spreadsheets:read
    flags: [document.extraction, data.tabular]
    input_type: document
    output_type: structured
```

Python adapters are imported lazily. The callable accepts
`(input, instruction=None)` and may return text, a mapping containing `text`, or
an object with a `.text` attribute. Capability flags may use namespaced dotted
extensions, so the same kernel can support medical, legal, finance, research,
engineering, and operations workflows.

Supported configured input types are `text`, `image`, `audio`, `document`, and
`structured`; output types are `text` and `structured`. A configured `chat`
capability must use `input_type: text`; media adapters should use `handler:
python`.

Disable a configured capability without deleting it:

```yaml
disabled_capabilities:
  - contract_review
```

## Secrets and local endpoints

Keep secrets in `.env`, never in YAML or committed source files:

```dotenv
GROQ_API_KEY=...
OPENAI_API_KEY=...
HF_TOKEN=...
HF_PROVIDER=auto
HF_MODEL=
```

The default vision profile targets an OpenAI-compatible Ollama endpoint at
`http://localhost:11434/v1`. It does not require an API key, but the local model
must be installed and the endpoint must be running.

## Built-in inputs and extension boundary

- PDF and text-like documents are extracted with source/page markers.
- Images are sent to the configured vision profile.
- Audio is sent to the configured transcription profile.
- Web search is available when its provider and network are configured.

The artifact manager can retain video, datasets, archives, and unknown files,
but the current kernel needs a registered parser/capability before it can
process their binary contents.
