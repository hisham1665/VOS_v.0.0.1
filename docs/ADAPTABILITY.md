# AOS v1.0 adaptability contract

AOS is a general orchestration kernel. Domain behavior belongs in capabilities,
input adapters, and evaluation policies; graph scheduling, failure containment,
resource selection, provenance, and memory remain domain-neutral.

## Stable execution contract

1. An input becomes an `Artifact` with type, MIME, size, content hash, and path.
2. The manager creates a validated DAG.
3. Capability DNA declares required flags and complexity.
4. The registry filters by skills, input type, risk, quality, cost, latency, and
   recent health before scoring candidates.
5. The dependency-ready executor runs independent work in a bounded pool.
6. Every node records status, resource, attempts, output, and error provenance.
7. The integrator must surface gaps; a degraded branch cannot silently become a
   successful answer.

## Medical AOS improvements carried into the core

The medical-AOS audit identified reusable improvements that are now part of the
general engine: one import/package boundary, bounded worker concurrency,
dependency-ready scheduling, explicit failure containment, typed artifacts,
content-addressed inputs, performance-aware resource ranking, model/provider
provenance, deterministic PDF extraction with source markers, bounded recovery,
and structured task-understanding metadata. Clinical inference remains a
registered capability; no disease-specific rules are embedded in the kernel.

For a medical deployment, add capabilities such as `image.triage`,
`document.verification`, `evidence.reconciliation`, and
`clinical.report`; require source references in their result schemas and use a
reviewed evaluator before marking a result authoritative. A negative test or a
model confidence score should not be treated as a diagnosis unless the domain
capability explicitly supplies that evidence policy.

## Extension checklist

- Declare the capability's input and output types.
- Register every model/tool alternative under the same required flags.
- Add source references and an output schema for evidence-bearing work.
- Set cost, latency, quality, risk, timeout, and retry policy explicitly.
- Test empty, malformed, partial, timeout, and unavailable results.
- Keep secrets in `.env` and operational policy in YAML.
- Measure cold/warm latency, calls, token usage, failures, fallbacks, and cache
  reuse on identical inputs.
