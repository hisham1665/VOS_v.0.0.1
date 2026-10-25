# Medical AOS research documentation

This folder is a research-oriented description of the Medical AOS codebase. It
is intended to give a writing assistant enough technical context to prepare a
methods, architecture, evaluation, limitations, or discussion section without
having to infer the system from isolated source files.

## Reading order

1. [Project scope and research context](context/00_project_scope.md)
2. [Codebase map](context/01_codebase_map.md)
3. [End-to-end working](context/02_end_to_end_working.md)
4. [Data contracts and provenance](context/03_data_contracts_and_provenance.md)
5. [Models and configuration](context/04_models_and_configuration.md)
6. [Chat and longitudinal continuity](context/05_chat_and_continuity.md)
7. [Safety, limitations, and research scope](context/06_safety_limitations_and_scope.md)
8. [Integrated architecture](architecture.md)
9. [Evaluation and saved-result analysis](results.md)

## What this package covers

- The problem addressed by Medical AOS and its intended operating modes.
- The responsibilities and boundaries of every major source directory.
- The ingestion, planning, scheduling, model-routing, synthesis, and reporting
  workflow.
- The typed data model, evidence binding, audit trail, failure containment, and
  bounded runtime adaptation.
- The modality/region screening cascade and its model coverage.
- The interactive chat/TUI and persistent patient-continuity design.
- Positive findings and limitations in both saved disease-case collections.
- The difference between operational regression evidence and clinical accuracy.

## Evidence and interpretation policy

The results document distinguishes three kinds of evidence:

| Evidence class | Meaning | Suitable claim |
|---|---|---|
| **Source inspection** | Behavior visible in the current Python implementation | Design and implementation claim |
| **Offline regression** | Tests using synthetic data, stubs, or injected endpoints | Software-behavior claim |
| **Saved disease run** | Historical Markdown plan/report artifacts in `tests/` | Observed run behavior and failure analysis |

The saved disease runs do **not** provide a held-out, clinician-adjudicated
diagnostic-accuracy benchmark. Most of `tests/test2full/testfiles/` contains
written reports and laboratory documents rather than raw images. Direct-image
coverage is therefore reported separately from document interpretation.

## Existing documentation used as supporting material

This package consolidates, but does not replace, the more focused operational
documents already under `docs/`:

- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [SCREENING_CASCADE.md](../SCREENING_CASCADE.md)
- [SAVED_CASE_AUDIT.md](../SAVED_CASE_AUDIT.md)
- [TEST_RUN_ANALYSIS.md](../TEST_RUN_ANALYSIS.md)
- [ADAPTIVE_IMAGING.md](../ADAPTIVE_IMAGING.md)
- [AOS_IMPROVEMENTS.md](../AOS_IMPROVEMENTS.md)
- [SAFETY.md](../SAFETY.md)
- [PERFORMANCE.md](../PERFORMANCE.md)
- [MODELS.md](../MODELS.md)

The exact saved fixture directory is `tests/tetingmed1/`; the directory name is
misspelled in the repository. Some historical index entries also use filenames
that no longer match the files on disk. Those identity problems are part of the
evaluation result and are not silently corrected in this documentation.
