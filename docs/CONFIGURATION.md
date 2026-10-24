# AOS v1.0 configuration

AOS is configured in `aos.yaml`; secrets remain in `.env`. The default file is
safe to edit, and `AOS_CONFIG=/path/to/file.yaml` selects another profile. The
configuration is validated at load time and unknown keys fail early.

```bash
cp .env.example .env
python -m pip install -e ".[tui]"
python -m aos_v0 "Summarize the supplied document" --input report.pdf
```

## Providers and models

Each model profile declares `provider`, `model`, `api_key_env`, optional
`base_url`, and output limits. Supported built-in transports are Groq,
OpenAI-compatible endpoints, Ollama, and audio transcription endpoints. AOS
creates clients lazily, disables hidden SDK retries, and applies the configured
timeout.

```yaml
models:
  text:
    provider: openai
    model: gpt-4o-mini
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
```

## Adding a capability

The registry contract is a manifest plus an invocation function. The simple
YAML form creates a text capability backed by a model profile:

```yaml
capabilities:
  contract_review:
    handler: chat
    flags: [legal.review, reasoning.deep]
    model_profile: text
    system_prompt: Review the supplied contract and list evidence-linked risks.
    output_type: text
    quality: 0.8
```

For deterministic or local integrations, use a Python adapter:

```yaml
capabilities:
  spreadsheet_reader:
    handler: python
    callable: my_adapters.spreadsheets:read
    flags: [document.extraction, data.tabular]
    input_type: document
```

The adapter accepts `(input, instruction=None)` and returns text, a mapping with
`text`, or a result object with `.text`. Custom flags use namespaced dotted
names, making the same kernel usable for medical, legal, finance, research,
engineering, and operations workflows.

## Built-in inputs

- PDF and text-like documents are extracted with page markers.
- Images are routed to the configured vision model.
- Audio is routed to the configured transcription model.
- Web search is available when the provider and network are configured.

The operator can add parsers for spreadsheets, archives, video, databases, or
domain-specific artifacts without changing the graph executor.

## Memory and privacy

Shared memory is selective, bounded, and exact for reuse. It exposes summary
keys to agents and injects values only when relevant. It is request-scoped by
default; interactive sessions may promote stable document/search results into a
session bank. Set `AOS_MEMORY=off` to disable it. Decision logging is off by
default; if enabled, content fields are truncated and should be reviewed before
shipping logs outside the operator's trust boundary.
