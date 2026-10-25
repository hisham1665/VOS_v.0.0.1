# Project report readiness

**Review date:** 2026-10-25  
**Repository:** Medical AOS  
**Purpose:** single current reference for a teammate preparing the project report.

This document records what is implemented in the current workspace, where the
supporting documentation lives, the live capability registry, the current
screening-model registry, and the checks completed for this report package.
Historical saved-case analysis remains in `SAVED_CASE_AUDIT.md` and
`TEST_RUN_ANALYSIS.md`; this page is the current implementation snapshot.

## Executive summary

Medical AOS is a provenance-aware, agentic medical study-analysis pipeline. It
accepts mixed study folders containing raster images, DICOM files, PDFs, text,
tabular data and office documents. It creates a typed `Study`, builds a
deterministic radiology graph, executes registered capabilities with bounded
concurrency, integrates structured evidence into a `RadiologyReport`, and
writes Markdown and JSON outputs.

The project is report-ready for an **architecture and software-behavior report**
covering:

- heterogeneous ingestion and DICOM/document provenance;
- modality and body-region routing;
- two-stage supervised screening and specialist confirmation;
- dependency-aware multi-agent execution;
- bounded runtime verification and recovery;
- evidence-linked clinical assessment and longitudinal session state;
- direct-image versus written-report coverage accounting; and
- offline regression evidence for the implemented contracts.

The output is an AI-generated preliminary draft for qualified clinician review.
The repository does not claim clinical validation, regulatory clearance, or
autonomous diagnostic authority.

## Current system flow

```text
study folder
  -> ingest and de-identification
  -> technical image quality and series context
  -> modality/body-region triage
  -> routed stage-1 screening
  -> specialist confirmation
  -> direct image reads and document-page analysis
  -> evidence consolidation
  -> optional literature context
  -> structured clinical assessment and report
  -> Markdown + JSON + plan/provenance diagnostics
```

The fixed plan is built in `agents/planner.py`. The executor is generic and
dependency-aware; model and tool calls are isolated in registered capabilities.
The graph carries both prose and structured artifacts so downstream stages do
not need to recover evidence by re-parsing model text.

## Current capability registry

The registry was inspected from `capabilities.registry` on 2026-10-25.
It contains **13 registered capabilities**. Each capability accepts a
`CapabilityRequest` and returns a `CapabilityResult`.

| Capability | Skills | Inputs | Outputs | Purpose |
|---|---|---|---|---|
| `image_quality` | `check_image_quality` | images | image_quality | Technical pixel checks and supplied-series context. |
| `modality_triage` | `identify_modality` | images | triage | Medical-image gate plus modality and body-region routing. |
| `image_classification` | `screen_images` | images, triage | screening | Stage-1 routed screening and candidate conditions. |
| `specialist_confirmation` | `confirm_candidates` | images, screening | confirmation | Stage-2 specialist re-checks. |
| `vision` | `read_images` | images | reads | Narrative VLM read of prepared images/frames. |
| `document_analysis` | `extract_documents` | documents | document_reads, findings | Structured page-level extraction with source binding. |
| `cross_image_synthesis` | `reconcile_observations` | text | findings | Deduplication, reconciliation and discrepancy surfacing. |
| `report_synthesis` | `clinical_reasoning` | text, findings | clinical_assessment, status | Impression, status, differential and next steps. |
| `web_search` | `search_literature` | text | references | De-identified background literature context. |
| `summarization` | `summarize` | text | text | Bounded text condensation utility. |
| `document_verification` | `verify_documents` | documents, text | verification | Rendered-page or native-text extraction verification. |
| `temporal_comparison` | `compare_temporal_evidence` | text, findings | verification | Date-aware comparison of repeated observations. |
| `evidence_verification` | `verify_evidence` | text, findings | verification | Provenance and exact-number audit. |

Registry implementation: `capabilities/registry.py`. The registry is loaded
lazily under a lock so worker threads see a complete registry. DNA metadata is
available through `registry.metadata()` and is included by `python main.py
--doctor`.

## Current capability behavior

### Ingest and data contracts

- `ingest/study_loader.py` accepts a directory or a single supported file.
- Raster formats include PNG, JPG/JPEG, BMP, TIFF and WebP.
- DICOM supports `.dcm`, `.dicom`, `.ima` and extensionless DICOM magic.
- Documents include PDF, TXT/MD/LOG, CSV/TSV, JSON/NDJSON, XML/HTML, YAML,
  RTF, DOCX and XLSX.
- DICOM modality, view, body part, series identity, frame index and ordering
  context are retained in the typed study model.
- Multi-frame objects retain decodable frames, while sampling is explicit when
  `MAOS_MAX_IMAGES` is positive.
- Prepared-image and inference caches use content hashes.
- DICOM identifiers are de-identified by default through `MAOS_DEIDENTIFY=1`.

### Planning and execution

- The fixed radiology graph is deterministic and auditable.
- The usual path is quality -> triage -> screen -> confirm -> read/document
  analysis -> consolidate -> optional literature -> report.
- Independent nodes run through one bounded worker pool.
- Local GPU/server calls are separately limited by `MAOS_MAX_GPU_WORKERS`.
- Nodes retain status, duration, errors, selected capability, model trace and
  structured artifacts.
- `TaskBudget` supports limits for replans, added nodes, attempts, elapsed time,
  declared cost and declared memory.
- Runtime verification/replacement work is bounded and recorded in the original
  plan, revised plan and report diagnostics.

### Evidence and report output

`models.py` defines the main report contract. The final `RadiologyReport` can
contain:

- patient context, indication and technique;
- findings with severity, confidence, source IDs and evidence kind;
- direct image reads and document-page reads as separate evidence lanes;
- screening and specialist-confirmation tables per image;
- series ordering and selected/decoded coverage;
- structured clinical assessment and differential hypotheses;
- references, warnings, verification records and acceptance results;
- original/revised plans and node-level diagnostics; and
- models used and total runtime.

`reporting/report_builder.py` writes:

```text
outputs/reports/<study-id>-<timestamp>.md
outputs/reports/<study-id>-<timestamp>.json
outputs/plan.md
```

## Current model registry

The model registry was inspected directly from `capabilities.model_zoo.ZOO` on
2026-10-25. It contains **13 entries: 10 enabled and 3 disabled**. Disabled
entries remain visible so coverage decisions are auditable and can be replaced
through `MAOS_MODEL_ZOO` without changing the routing code.

| Key | Model id | Role | Modality / region | Targets | Backend | State |
|---|---|---|---|---|---|---|
| `cxr_nih14` | `Sohaibsoussi/ViT-NIH-Chest-X-ray-dataset-small` | screener | X-ray / chest | NIH-14 findings | transformers | enabled |
| `cxr_pneumonia` | `lxyuan/vit-xray-pneumonia-classification` | specialist | X-ray / chest | pneumonia | transformers | enabled |
| `cxr_tuberculosis` | `runaksh/chest_xray_tuberculosis_detection` | specialist | X-ray / chest | tuberculosis | transformers | enabled |
| `cxr_xrv_all` | `densenet121-res224-all` | specialist | X-ray / chest | pneumonia, pneumothorax, effusion, fracture, edema, cardiomegaly, mass, nodule, atelectasis | torchxrayvision | enabled |
| `cxr_pneumothorax` | `mrm8488/vit-base-patch16-224_finetuned-pneumothorax` | specialist | X-ray / chest | pneumothorax | transformers | disabled |
| `msk_fracture` | `Hemgg/bone-fracture-detection-using-xray` | screener | X-ray / limb, pelvis, spine | fracture | transformers | disabled |
| `ct_head_hemorrhage` | `DifeiT/rsna-intracranial-hemorrhage-detection` | screener | CT / head | intracranial_hemorrhage | transformers | enabled |
| `ct_head_stroke` | `BTX24/hiera-base-224-in1k-hf-finetuned-stroke-binary` | specialist | CT / head | stroke | transformers | enabled |
| `ct_chest_lung_cancer` | `oohtmeel/swin-tiny-patch4-finetuned-lung-cancer-ct-scans` | screener | CT / chest | lung_cancer | transformers | enabled |
| `ct_kidney_tumor` | `Rahii123/kidney-disease-classification-v2` | screener | CT / abdomen | kidney_tumor | transformers | disabled |
| `mri_brain_tumor` | `Devarshi/Brain_Tumor_Classification` | screener | MRI / head | brain_tumor | transformers | enabled |
| `mri_brain_tumor_vit` | `Hemgg/brain-tumor-classification` | specialist | MRI / head | brain_tumor | transformers | enabled |
| `mri_brain_dementia` | `evanrsl/resnet-Alzheimer` | specialist | MRI / head | dementia | transformers | enabled |

### Registry routing rules

- `screeners_for(modality, region)` returns enabled stage-1 models matching the
  image modality and body region.
- `specialists_for(condition, modality, region)` returns enabled models that
  can independently check a condition.
- `RECHECK` maps broad candidates to specialist targets.
- A requested condition can trigger a specialist even when stage 1 is quiet.
- An unsupported condition is reported as `unverifiable`, not as a normal result.
- A screener is not counted as its own independent specialist confirmation.
- Thresholds, label maps, benchmark notes and target-specific verdict bounds are
  stored on each `ZooModel` entry.

## Text and vision model configuration

The main defaults in `config.py` and `capabilities/vision.py` are:

| Function | Default |
|---|---|
| Report VLM | `google/medgemma-4b-it` |
| Report VLM fallback | `Qwen/Qwen2.5-VL-7B-Instruct` |
| Modality/region CLIP | `flaviagiammarino/pubmed-clip-vit-base-patch32` |
| Non-medical gate | `openai/clip-vit-base-patch32` |
| Summarizer | `facebook/bart-large-cnn` |
| Primary text model | `openai/gpt-oss-120b` |
| Fast text model | `openai/gpt-oss-20b` |
| HF text fallback | `Qwen/Qwen2.5-7B-Instruct` |
| Chat embeddings | `sentence-transformers/all-MiniLM-L6-v2` when enabled |

Vision routes are `auto`, `ollama`, `openai`, `hf` and `local`. In `auto`, a
reachable local vision server is preferred, followed by hosted inference and
then sufficiently large local hardware. Text selection uses task-aware model
profiles, capability requirements and persisted reliability/latency aggregates.

## Main configuration groups

The complete environment template is `.env.example`. The report-relevant
groups are:

- credentials: `HF_TOKEN`, `GROQ_API_KEY`;
- model selection: `MAOS_REPORT_VLM`, `MAOS_MODEL_ZOO`,
  `MAOS_TEXT_MODEL_CATALOG`;
- vision routing: `MAOS_VLM_BACKEND`, `MAOS_OLLAMA_URL`,
  `MAOS_OLLAMA_MODEL`, `MAOS_VLM_URL`, `MAOS_VLM_MODEL`;
- execution: `MAOS_MAX_WORKERS`, `MAOS_MAX_GPU_WORKERS`,
  `MAOS_CALL_TIMEOUT`, `MAOS_MAX_RETRIES`;
- image/document budgets: `MAOS_MAX_IMAGES`, `MAOS_MAX_FILE_MB`,
  `MAOS_MAX_DOCUMENT_PAGES`, `MAOS_DOCUMENT_CHUNK_CHARS`;
- adaptation: `MAOS_MAX_REPLANS`, `MAOS_MAX_ADDED_NODES`;
- privacy/cache: `MAOS_DEIDENTIFY`, `MAOS_CACHE`, `MAOS_CACHE_DIR`; and
- chat context: `MAOS_CHAT_*` settings.

Use `python main.py --doctor` to inspect the redacted active configuration,
capability DNA, model-zoo coverage, backend availability and optional document
support.

## Documentation map

| Area | Primary document |
|---|---|
| End-to-end architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Screening and model coverage | [SCREENING_CASCADE.md](SCREENING_CASCADE.md) |
| Model setup and swapping | [MODELS.md](MODELS.md) |
| Adaptive verification and DICOM context | [ADAPTIVE_IMAGING.md](ADAPTIVE_IMAGING.md) |
| Chat and longitudinal continuity | [CHAT_TUI.md](CHAT_TUI.md) |
| Safety/privacy | [SAFETY.md](SAFETY.md) |
| Performance | [PERFORMANCE.md](PERFORMANCE.md) |
| Historical failure audit | [FAILURE_MODES.md](FAILURE_MODES.md) |
| Saved-case audit | [SAVED_CASE_AUDIT.md](SAVED_CASE_AUDIT.md) |
| Research-writing context | [research/README.md](research/README.md) |
| Positive test-result digest | [../tests/TEST_RESULTS.md](../tests/TEST_RESULTS.md) |

## Verification snapshot

The current offline suite was run from the repository root with:

```bash
python -m pytest tests/ -q --tb=short
```

Result: **82 passed in 2.22 seconds**.

The collected suite contains:

- 36 adaptive-imaging and verification cases;
- 31 AOS continuity and reasoning cases; and
- 15 image-route and format cases.

The Python source tree also passed a repository-wide `py_compile` check during
this documentation review. The positive case digest in `tests/TEST_RESULTS.md`
records selected saved outputs from both case folders for the project report.

## Report hand-off recommendation

For the teammate writing the final project report, use this order:

1. [research/context/00_project_scope.md](research/context/00_project_scope.md)
   for the problem statement and scope;
2. [research/architecture.md](research/architecture.md) and
   [ARCHITECTURE.md](ARCHITECTURE.md) for the system design;
3. this document for the current registry and implementation snapshot;
4. [tests/TEST_RESULTS.md](../tests/TEST_RESULTS.md) for positive verification
   evidence; and
5. [research/results.md](research/results.md),
   [SAVED_CASE_AUDIT.md](SAVED_CASE_AUDIT.md), and [SAFETY.md](SAFETY.md) when
   writing evaluation interpretation and scope boundaries.
