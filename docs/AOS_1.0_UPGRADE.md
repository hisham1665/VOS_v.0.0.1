# AOS 1.0 upgrade integration

The earlier core in `../AOS_v1.0.0` was the upgrade reference. The evaluator
repository already contained the paper domain, so the integration preserves
its public models and result schemas and upgrades the shared kernel services.

## Stable contract

1. Inputs are typed artifacts with modality and provenance.
2. A plan is a validated DAG of nodes and dependencies.
3. Capability DNA describes required flags, complexity, and constraints.
4. The registry selects an available resource and records the ranked decision.
5. The executor runs dependency-ready work in bounded waves.
6. Recovery classifies output failures and records retries/substitution.
7. Degraded work is visible to the integrator and paper review policy.

## Integrated improvements

- `settings.py`: strict YAML validation, profile defaults, and environment-only secrets.
- `providers/gateway.py`: lazy Groq/OpenAI-compatible/Ollama clients and request usage.
- `core/performance.py`: content-free health and latency history for routing.
- `core/shared_memory.py`: bounded request/session memory, exact reuse, eviction, and provenance.
- `core/memory_admission.py` and `core/memory_retrieval.py`: selective key-first context handling.
- `core/models.py`: structured task understanding, result surfaces, typed artifact content, and extension flags.
- `exam/cli.py`: simple init/validate/evaluate/batch/report/benchmark commands.

## Paper-domain boundary

The kernel does not contain marking rules, OCR heuristics, question semantics,
or human-review decisions. The exam package registers those as capabilities and
uses the kernel's registry and orchestration interfaces. This keeps the AOS
engine reusable for other domains while preserving an auditable paper workflow.
