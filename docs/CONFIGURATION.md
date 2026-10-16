# AOS 1.0 configuration

AOS uses two configuration layers:

1. `aos.yaml` controls runtime policy, model profiles, memory, and optional capabilities.
2. `.env` contains credentials and local endpoint overrides.

Start from the checked-in templates:

```bash
cp .env.example .env
python -m pip install -e .
```

Use another profile for an experiment without editing the repository:

```bash
AOS_CONFIG=profiles/research.yaml aos exam benchmark exam.json \
  --dataset benchmark_dataset --out research
```

## Model profiles

Each profile has:

```yaml
models:
  text:
    provider: groq
    model: openai/gpt-oss-20b
    api_key_env: GROQ_API_KEY
    max_tokens: 2048
```

Supported provider transports are Groq, OpenAI-compatible APIs, Ollama, and
the existing optional Hugging Face adapter. Ollama does not require an API key.
Clients are built lazily, so local paper evaluation does not require remote
credentials.

## Optional capabilities

Capabilities are manifest plus handler registrations. Python handlers are
loaded only when the YAML profile is used:

```yaml
capabilities:
  rubric_checker:
    handler: python
    callable: my_project.adapters:check_rubric
    flags: [rubric.evaluation]
    input_type: structured
    output_type: structured
    quality: 0.9
    latency_ms: 100
```

Chat capabilities use `handler: chat`, `model_profile`, `system_prompt`, and
text input. Every resource publishes cost, latency, quality, risk, input type,
and provider metadata to the Capability Registry.

## Memory

General AOS shared memory is bounded and selective. It can reuse exact outputs
for stable capabilities and retrieve relevant values within a context budget.
It is request-scoped by default. Use `AOS_MEMORY=off` for a clean baseline or
ablation. Paper-evaluation evidence is kept in the paper result and review
records rather than hidden in general memory.
