# Positive test-results digest

**Review date:** 2026-10-25  
**Project:** Medical AOS  
**Purpose:** report-ready summary of the successful verification checks and
selected positive saved-case outputs.

This digest intentionally records positive results only. It is a presentation
summary for the project report; the complete engineering and saved-case audit is
kept in `docs/FAILURE_MODES.md`, `docs/SAVED_CASE_AUDIT.md`, and
`docs/TEST_RUN_ANALYSIS.md`.

## Automated verification result

Command run from the repository root:

```bash
python -m pytest tests/ -q --tb=short
```

**Result: 82 passed in 2.22 seconds.**

The successful suite covers:

| Test group | Collected cases | Positive behavior represented |
|---|---:|---|
| `tests/test_adaptive_imaging.py` | 36 | Bounded verification, runtime graph updates, DICOM frame handling, image quality, evidence lanes, source validation, local vision routing and resource budgets. |
| `tests/test_aos_continuity.py` | 31 | Named-session continuity, evidence-linked reasoning, persistent differential state, source-aware assessment, model selection and dependency-ready scheduling. |
| `tests/test_image_routes.py` | 15 | Supported raster formats, Ollama model selection, OpenAI-compatible image requests, image-read reporting and retry classification. |

Additional source verification completed during the documentation review:

```bash
python -m py_compile <all repository Python modules>
```

The repository-wide compilation check completed successfully.

## Positive saved-case examples: `tests/test2full/testfiles/`

These files are saved report artifacts used as examples of source-linked output
organization. They are included here as report examples, not as a clinical
accuracy score.

| Case artifact | Positive result demonstrated |
|---|---|
| `kidneyfailure1.md` | Connects renal function, proteinuria, renal ultrasound, nuclear medicine and CKD-related laboratory observations to a structured clinical assessment with source-page references and recommended next steps. |
| `liverdesease1.md` | Combines hepatitis C evidence, cirrhotic morphology, portal hypertension, ascites, laboratory findings and a structured differential. |
| `hernia1.md` | Preserves an abdominal-wall hernia finding, source references, alternative hypotheses and follow-up investigations in one report. |
| `fattyliver1.md` | Integrates ultrasound/CT written findings, liver enzymes, metabolic context and fibrosis-related observations into a source-linked assessment. |
| `braintumour1.md` | Brings together MRI, CT, spectroscopy, PET, histopathology and molecular-report evidence in a structured report. |
| `stroke1.md` | Organizes written CT, MRI, angiography, laboratory, ECG and echocardiography observations into an urgent stroke-focused assessment. |

These examples demonstrate that the saved reports preserve patient status,
source references, findings, clinical assessment, next steps and report
provenance in a consistent Markdown structure.

## Positive saved-case examples: `tests/tetingmed1/`

The repository folder is named `tetingmed1`. The examples below show successful
heterogeneous evidence organization from that collection.

| Case artifact | Positive result demonstrated |
|---|---|
| `cholecystitis1.md` | Combines abdominal pain, inflammatory laboratory findings and ultrasound observations into a structured acute calculous cholecystitis assessment with follow-up investigations. |
| `Appendicitis1.md` | Converts CBC, CRP, metabolic-panel and urinalysis observations into a source-linked appendicitis differential and recommended confirmatory imaging path. |
| `skull_fracture1.md` | Preserves mixed image/document evidence, a skull-fracture finding, related soft-tissue observation and a clear next-imaging recommendation. |
| `nose_fracture1.md` | Keeps direct-image and written-report evidence visibly separate while retaining the fracture assessment, source references and confirmatory CT recommendation. |
| `lowerlegfracture1.md` | Provides a fracture-focused saved report example with history, findings, impression and provenance sections. |
| `Pancreatitis1.md` | Provides a multi-source abdominal-case report example with clinical context, findings and recommended follow-up. |

## Report-ready evidence statements

The verified results support these project-report statements:

1. The checked-in offline suite executes successfully with 82 passing cases.
2. The suite exercises continuity, adaptive verification, DICOM handling, image
   routing, source binding, report assembly and backend selection.
3. Saved artifacts in both case folders contain structured report sections,
   source-page references, clinical assessments and recommended next steps.
4. The project keeps direct image evidence and written-document evidence
   distinguishable in the report model and renderer.
5. The current implementation snapshot, registry and capability list are
   documented in [docs/PROJECT_REPORT_READINESS.md](../docs/PROJECT_REPORT_READINESS.md).

## How to reproduce

```bash
python -m pytest tests/ -q --tb=short
python -m pytest --collect-only -q tests/test_adaptive_imaging.py
python -m pytest --collect-only -q tests/test_aos_continuity.py
python -m pytest --collect-only -q tests/test_image_routes.py
```

The test suite is designed for offline execution with injected endpoints and
synthetic fixtures, so the documented result does not require credentials,
network access or downloaded model weights.
