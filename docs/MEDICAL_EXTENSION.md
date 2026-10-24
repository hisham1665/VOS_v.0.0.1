# Medical AOS extension boundary

Medical AOS should be an additive capability pack on top of this kernel. The
core must not decide that an observation is a disease, convert a negative test
into a rule-out, or treat a generated image description as authoritative.

## Recommended medical capability graph

```text
folder_ingestion
   ├── document_analysis ─┐
   ├── laboratory_analysis ├── evidence_reconciliation ── report_synthesis
   ├── image_analysis ────┘             │
   └── prescription_analysis            └── clinician_review queue
```

Each medical capability is a normal AOS resource manifest. It should declare
its artifact input type, output schema, source-reference requirements, cost,
latency, and a conservative risk class. No medical implementation needs to
modify `GraphExecutor`, `CapabilityRegistry`, or shared memory.

## Evidence contract

Use `CapabilityResult` for medical results and populate:

- `text` for human-readable evidence;
- `data` for typed labs, medications, conditions, and hypotheses;
- `source_refs` such as `labs.pdf#page=2` or `scan.dcm#frame=4`;
- `status=degraded` and `issues` for unreadable or incomplete inputs;
- `verification_requests` when a second registered capability is needed.

The integrator should preserve the evidence list and distinguish observations,
hypotheses, opposing evidence, missing information, and clinician decisions.
Structured clinical assessment is a domain capability, not a scheduler rule.

## Safety and continuity rules

1. A failed or unprocessed branch is visible in the final report.
2. Direct-image reads and written radiology reports are separate evidence lanes.
3. A model score, extraction confidence, or negative result is not by itself a
   definitive diagnosis or exclusion.
4. Patient/session memory stores source-linked observations and clinician
   updates; summaries cannot be the only record of an exclusion.
5. Longitudinal updates must keep historical hypotheses rather than deleting
   them, record the source and timestamp, and require explicit evidence before
   changing `possible`, `likely`, `less_likely`, `ruled_out`, or `confirmed`.
6. Hosted providers are opt-in for sensitive data; use a local compatible model
   profile when policy requires data to stay on the machine.

This boundary carries the useful Medical-AOS improvements into the general
engine while keeping clinical validation, terminology, patient identity,
temporality, and clinician review inside the medical extension where they can
be tested with clinical fixtures.
