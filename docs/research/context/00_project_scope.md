# Project scope and research context

## 1. System identity

Medical AOS is an agentic medical study-analysis pipeline. Its primary input is
a study folder containing any mixture of:

- raster images such as PNG, JPEG, BMP, TIFF, or WebP;
- DICOM files and multi-frame DICOM objects;
- PDF reports, including scanned or image-only pages;
- plain text, Markdown, CSV/TSV, JSON, XML, HTML, YAML, and RTF;
- DOCX and XLSX office documents; and
- an optional `patient.json` or `patient_context.json` sidecar.

Its primary output is a structured `RadiologyReport`, rendered as both Markdown
and JSON. The report contains patient context, indication, technique, findings,
impression, patient status, triage, recommended next steps, source references,
model provenance, diagnostics, coverage, and limitations.

The system is designed as clinical decision support. The output is an
AI-generated preliminary draft and must be reviewed by an appropriately
qualified clinician, including a radiologist for imaging. The code explicitly
does not claim regulatory clearance, clinical validation, or autonomous
diagnostic authority.

## 2. Problem being addressed

A conventional medical-AI prototype often receives one image and asks one model
for a diagnosis. That approach is insufficient for a real exported study because
the study may contain multiple views, a DICOM series, laboratory values,
referral history, previous written reports, pathology, and incomplete or
unreadable files. The system therefore treats the study as a provenance-bearing
collection of evidence rather than as one undifferentiated prompt.

The engineering problem has five parts:

1. **Heterogeneous ingestion:** decode different formats without losing source
   identity or allowing one bad file to abort the study.
2. **Modality-aware interpretation:** avoid applying a model outside the anatomy
   and modality represented in its training data.
3. **Multi-agent coordination:** run independent work concurrently while
   preserving deterministic dependencies and bounded resource use.
4. **Evidence-linked synthesis:** merge image, document, laboratory, and history
   observations without losing negative evidence, source links, or disagreement.
5. **Safe degradation:** produce an explicitly incomplete report when a model,
   file, provider, or verification stage fails.

## 3. Research-oriented system objective

The implementation can be studied as an operational hypothesis:

> A typed, dependency-aware, modality-routed agentic pipeline can provide more
> auditable and failure-tolerant preliminary study analysis than a single
> universal image classifier or an unconstrained LLM prompt, provided that
> unsupported coverage and uncertainty remain visible.

This hypothesis has several measurable engineering outcomes:

- independent files can be processed despite partial failures;
- image and document evidence remain separately attributable;
- classifier scope is constrained by modality and body region;
- a specialist check is distinguishable from a first-stage screening result;
- severe findings cannot be downgraded by later prose;
- missing or failed interpretation is represented as indeterminate/degraded;
- changed source pixels invalidate image-inference cache entries; and
- longitudinal hypotheses and explicit clinician exclusions survive chat resume.

These are software and workflow claims. They are not claims of sensitivity,
specificity, calibration, or clinical outcome benefit.

## 4. Operating modes

### Fixed study analysis

```bash
python main.py --study ./studies/patient-001 \
               --history "54M, cough and fever" \
               --question "rule out pneumonia"
```

This uses the deterministic radiology planner. The graph shape is known in
advance and changes with the supplied images/documents and the literature flag.

### Free-form graph analysis

```bash
python main.py --ask "compare these two imaging guidelines" --study ./studies/p1
```

This uses `ManagerAgent` and the LLM planner because the graph shape is not
known in advance. The generated graph is still checked against the live
capability registry and DAG validation rules.

### Interactive continuity mode

```bash
python main.py --chat --name patient-001
python main.py --resume
```

Files are copied into a persistent session, the complete accumulated study is
reanalyzed when needed, and subsequent questions use a layered evidence and
conversation context. Structured differential hypotheses are persisted outside
the rolling summary.

### Library mode

```python
from pipeline import analyze_study

report, study, graph = analyze_study(
    "./studies/patient-001",
    clinical_history="54M, cough and fever",
    referring_question="rule out pneumonia",
)
```

The same staged objects are available to services and tests: `Study`, `Graph`,
`RadiologyReport`, and the executed node trace.

## 5. Conceptual vocabulary

| Term | Meaning in this project |
|---|---|
| **Study** | An ingested folder or file plus images, documents, series, patient context, warnings, and skipped-file records |
| **Node** | One graph task, such as triage, image reading, document extraction, consolidation, or reporting |
| **Capability** | A registered callable with a common request/result contract and declared DNA |
| **Artifact** | Structured data passed between nodes, such as `findings`, `triage`, `screening`, `reads`, or `verification` |
| **Screener** | A broad supervised model used to generate candidate conditions for a supported modality/region |
| **Specialist** | A disease-specific model used to support, refute, or qualify a screening candidate |
| **Direct image evidence** | A model read tied to an image/frame identifier |
| **Written-report evidence** | A finding extracted from a document page, including a written imaging report |
| **Degraded run** | A report in which a stage, source, acceptance criterion, verification check, or direct image read did not complete successfully |
| **Indeterminate** | The system does not have enough reliable evidence to call a study normal |

## 6. Scope boundary for a research paper

The strongest paper contribution supported by this repository is an
architecture/workflow contribution: typed orchestration, provenance, cascade
routing, bounded adaptation, and visible degradation. A defensible evaluation
section can report:

- graph and capability behavior under controlled failures;
- source coverage, read completion, and screening coverage;
- fixture integrity and backend availability;
- qualitative cross-source reasoning examples; and
- latency/cache/resource observations.

It should not present the saved disease cases as a diagnostic test set unless the
inputs are repaired, direct image cases are separated from written reports, and
independent clinical ground truth is added.
