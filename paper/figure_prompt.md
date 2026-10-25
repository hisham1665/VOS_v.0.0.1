# Figure-generation prompt for the MedicalAOS paper

Create a clean, publication-quality vector-style system architecture diagram for
an IEEE conference paper titled **“MedicalAOS: A Capability-Aware,
Provenance-Preserving Agentic Pipeline for Heterogeneous Medical Study Analysis.”**

## Canvas and style

- Aspect ratio: approximately 16:9, landscape.
- White background, black/dark-gray text, restrained blue accent, one amber
  accent for warnings and one red accent for failures.
- Use a legible sans-serif font such as Helvetica/Arial; no decorative icons,
  photographs, gradients, 3-D effects, or tiny text.
- Draw as SVG/PDF if possible; the final figure must remain readable at one-column
  width in an IEEE two-column paper.
- Use consistent rounded rectangles, arrows with clear direction, and three
  visually distinct horizontal bands.

## Required content

### Band 1: heterogeneous input and provenance

Show a left-to-right input group containing:

1. raster images;
2. DICOM and multi-frame series;
3. PDF/DOCX/XLSX/text documents; and
4. optional patient context.

Connect these to **Study construction**. Under that box, show small labels:
`content hash`, `metadata`, `page/frame IDs`, `quality warnings`, and
`skipped-file record`.

### Band 2: capability-aware execution

Show **deterministic radiology DAG / bounded executor** as the central block. The
flow should be:

`quality → modality triage → stage-1 screening → specialist confirmation`

with parallel branches from the central scheduler to:

- per-image narrative vision reads;
- page-level document extraction; and
- optional literature lookup.

Then merge these into **cross-source consolidation**.

Show a small side loop labelled **bounded runtime replanning** from consolidation
back to a verification or compatible replacement node. Label the loop with:
`budget`, `timeout`, `retry`, `cooldown`, and `audit trace`.

At the right side of this band, show the **capability registry/model router**
connected to local models, Ollama/compatible local servers, and hosted text/vision
backends. Make clear that the executor calls registered capabilities rather than
calling disease models directly.

### Band 3: typed evidence and output

After consolidation, show **report synthesis** and **typed integration**. The
output group must contain:

- Markdown report;
- JSON/audit report;
- patient status and triage;
- findings and source references;
- direct-image vs written-report evidence groups; and
- warnings/degraded-run diagnostics.

Place a warning callout below the outputs: **“Decision support only; clinician
and radiologist review required.”**

## Diagram semantics

- Use solid arrows for normal data flow.
- Use dashed arrows for provenance metadata and audit information.
- Use a red-bordered callout near the output reading **“failed or incomplete
  branches remain visible; empty evidence is indeterminate, not normal.”**
- Do not imply diagnostic validation, autonomous diagnosis, or a complete 3-D
  interpretation.
- Do not include numerical performance claims in the figure.

## Caption intended for the paper

“MedicalAOS separates heterogeneous-study ingestion, capability-aware execution,
and typed evidence integration. The executor schedules dependency-ready work
under bounded resource and recovery policies; provenance and degradation metadata
remain attached to the final report.”
