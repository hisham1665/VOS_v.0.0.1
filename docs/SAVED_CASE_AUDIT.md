# Audit of the saved plan/result pairs

Reviewed 2026-10-07. This audit covers the 20 Markdown pairs in
`tests/test2full/testfiles/` and `tests/tetingmed1/`. The actual directory name
is `tetingmed1`; the supplied description called it `testingmed1`.

These files are historical execution artifacts. They are useful for finding
orchestration, provenance and failure-reporting defects, but they are not a
clinical-accuracy benchmark. The saved runs predate the current local Ollama
vision route and therefore do not measure the current direct-image path.

## Pairing integrity

The first issue is fixture identity, before model quality is considered:

| Indexed case | Pair status | Evidence |
|---|---|---|
| Rib fracture (`test2full`) | **Invalid/mispaired** | `ribfracture.md` has the hernia history, study `uploads-0bc7ad35`, 10 abdominal documents and the same job as `hernia.md`; `ribfraacture1.md` is also a hernia report. The output filename contains an extra `a`. |
| Bronchitis (`test2full`) | **Invalid/mispaired result** | `bronchitis.md` has the bronchitis history and study `uploads-52d211c7`, but `bronchitis1.md` reports an IDH-wildtype glioblastoma with brain imaging/pathology sources. |
| `tetingmed1` index names | **Filename drift** | `index1.txt` names `left_tibial_fracture`, `skull fracture`, `nose fracture` and `ligament fracture`; actual pairs use `lowerlegfracture`, underscores/spaces, and different plan/report suffixes. The index should not be used as an exact file manifest without normalization. |
| Remaining pairs | Pairable by study ID and content | The plan/result study IDs agree and the report topic is consistent enough for failure analysis. |

The two invalid pairs must be corrected or excluded before using aggregate
clinical results. The application cannot infer this from filenames alone; the
study ID and history in the plan are the stronger identity signals.

## `tests/test2full/testfiles/`

Nine of the ten saved reports carry a degraded-run marker. All ten report
`Images: 0 (unknown)`, so these are document-only runs even when the documents
contain written imaging reports or image-review drafts.

| Case | Historical result | Main break or quality issue | Current interpretation |
|---|---|---|---|
| Rib fracture | Invalid pair | The plan and result are actually the hernia case. | Exclude from disease-level evaluation until fixture identity is repaired. |
| Lung cancer | Degraded, critical/immediate | Synthesis unavailable with 58 findings carried forward; hosted VLMs returned `model_not_supported`; reasoning validation also failed for provider/model output. | Backend and validation failure obscured a potentially coherent malignant-evidence summary. Current fallback now limits escalation and preserves source findings, but a rerun is required. |
| Liver cirrhosis | Degraded, high/urgent | The report combines written sources, but visual verification failed because both hosted VLMs returned `model_not_supported`. | Keep the written-report synthesis, mark visual verification as unavailable, and do not present the old run as a direct image read. |
| Kidney failure | Not marked degraded, critical/immediate | Strong document synthesis, but still 0 direct images; written ultrasound/nuclear-medicine reports are separate from image review. | Best historical document-only case. It still needs explicit source/coverage labeling. |
| Hernia | Degraded, moderate/routine | Mostly coherent source-linked differential; visual verification rejected a narrative `Preliminary CT Image Review` response as invalid JSON. | The parser failure was overstated as unavailable extraction. Current verification fallback retains the narrative as unstructured, limited review. |
| Fatty liver | Degraded, high/urgent | Visual verification returned narrative urinalysis text instead of JSON. | Same structured-versus-narrative verification fix applies; etiologic uncertainty still needs clinician review. |
| Brain tumour | Degraded, critical/immediate | Visual verification returned narrative MR spectroscopy/urinalysis text; the report otherwise combines imaging and pathology. | Preserve the pathology distinction and label the visual reread as unstructured rather than discarding it. |
| Bronchitis | Invalid pair | Result is a brain-tumour report, not the bronchitis plan. | Exclude until the saved result is replaced. |
| Stroke | Degraded, critical/immediate | All 13 document nodes completed. The evidence audit rejected a hemorrhagic-stroke rule-out based on negative/limited evidence. | This later run demonstrates the conservative rule-out gate; it is distinct from the earlier all-node extraction failure in `TEST_RUN_ANALYSIS.md`. |
| Hepatitis | Degraded, normal/routine | Synthesis unavailable with 44 findings carried forward; the text model emitted the invalid status `unlikely`, the hosted fallback was unsupported, and visual verification exhausted its budget after partial rereads. | Normal tests must not settle unresolved symptoms when reasoning is unavailable. The summary normalization does not repair invalid status values; this case needs a fresh run. |

## `tests/tetingmed1/`

Eight of the ten reports carry a degraded-run marker. The historical image
metadata is weak: most named teaching images are `Unknown` and therefore were
not screened, while the MRI was classified but had no supervised screener.
Filename hints now provide conservative modality/body-region metadata for these
teaching images without replacing stronger DICOM/header evidence.

| Case | Historical result | Main break or quality issue | Current interpretation |
|---|---|---|---|
| Lower-leg fracture | Degraded | Direct image reads and document visual verification failed because hosted VLMs were unsupported; `lowerlegfracture.png` was `Unknown`/not screened. | Current filename hints classify this teaching image conservatively as X-ray/limb; rerun with the local vision backend. |
| Cholecystitis | Degraded | `cholecystitis.webp` was `Unknown`/not screened; hosted VLMs returned `model_not_supported`; written ultrasound/lab evidence still supported the report. | Current hints classify the body region as abdomen while leaving modality unknown; direct review remains separate from the written report. |
| Myocardial infarction | Degraded | MRI and electrogram image metadata were incomplete; one route had TLS failure and others had unsupported hosted VLMs. Critical troponin evidence drove the urgent report while direct imaging was unavailable. | Keep the critical laboratory evidence, explicitly state direct-image unavailability, and rerun only with a reachable configured backend. |
| Appendicitis | Degraded | Image was `Unknown`/not screened; the report was driven by inflammatory laboratory evidence and recommended confirmatory imaging. | The result is a document/lab synthesis, not an image confirmation. |
| Skull fracture | Not marked degraded | One `Unknown` image; report describes a linear skull fracture and recommends CT to assess occult injury. | Usable as a document/report provenance example, not as validated direct-image performance. |
| Nose fracture | Not marked degraded | One `Unknown` image; direct image read contradicted the written radiology report. | Valuable discordance case. The current evidence model should retain both sources and prioritize confirmatory imaging, as this report did. |
| Varicella-Zoster Virus | Degraded | Visual verification returned narrative descriptions of blank laboratory pages instead of JSON. | Current unstructured verification fallback preserves the observation and marks structured findings absent. |
| Cataract | Degraded | Synthesis unavailable with 36 findings carried forward; Groq rate limit and unsupported text backend errors. | Current fallback de-duplicates labels, removes administrative metadata from escalation and caps red flags. Rerun with a healthy text route. |
| Ligament fracture | Degraded | Synthesis unavailable with 25 findings carried forward; no usable text model; two image files were `Unknown`/not screened; visual rereads were narrative. | Current filename hints cover fracture/limb cases, but the report still requires a working text and vision backend. |
| Pancreatitis | Degraded | Synthesis unavailable with 30 findings carried forward; no usable text model; two pancreatic images were `Unknown`/not screened; visual verification returned narrative ultrasound text. | Current fallback and filename/body-region handling improve presentation, but a fresh run is needed for a structured differential. |

## Changes made from this audit

- Added conservative filename hints in `ingest/study_loader.py` for compound
  teaching-image names such as `lowerlegfracture.png`, `cataract.jpg` and
  `cardiac_mri.jpg`. Hints fill missing metadata only; they do not claim that a
  disease has been visually confirmed.
- Added administrative-label filtering in `capabilities/document_analysis.py`
  so patient demographics, report dates, indications and similar metadata do
  not become clinical findings or red flags.
- Added an unstructured visual-verification result in
  `capabilities/document_analysis.py`. Narrative model output is retained as
  limited reviewer evidence, while structured findings remain empty and the
  parser limitation is explicit.
- Added conservative report-payload normalization in
  `capabilities/report_synthesis.py` for string evidence, missing optional
  sections, malformed differentials and duplicate/administrative fallback
  escalation. Unlinked evidence is not accepted as validated support.
- Added regression tests for all of the above in
  `tests/test_adaptive_imaging.py`.

## Re-run requirements

Before comparing disease-level outcomes:

1. Replace or relabel the invalid rib-fracture and bronchitis artifacts.
2. Normalize the `tetingmed1` manifest against the actual filenames and study
   IDs.
3. Run each case with the same model/backend configuration and retain raw
   source files, prompts, model versions, coverage metadata and diagnostics.
4. Score direct image reads separately from written imaging-report extraction.
5. Have clinical reviewers assess source links, discordance handling,
   differential ranking and proposed discriminating tests.
