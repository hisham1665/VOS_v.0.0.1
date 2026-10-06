# Analysis of the saved disease runs

Reviewed on 2026-10-05. The supplied index is
`tests/testingdeseases/testingdetails.txt`; the paired plans and reports are in
`tests/testingdeseases/testingmed/`. There are nine indexed cases, plus an
additional hepatitis plan/report pair in the same folder.

These are saved execution outputs, not assertions or a diagnostic-accuracy
benchmark. Every report lists **0 images**; the inputs in these runs are PDF
documents, including written imaging reports and image-review drafts. They
therefore demonstrate document interpretation, not independent validation of
the direct image screening cascade.

## Case-by-case findings

| Case | Total | Consolidation | Report | What the output demonstrates / main issue |
|---|---:|---:|---:|---|
| Rib fracture | 53.3s | 38.55s | 11.67s | Fracture is linked to pain; little need for a long LLM copying stage when normal labs and a source-bound fracture are already extracted. |
| Lung cancer | 101.9s | 1.45s | 53.60s | CT document failed with undefined `_fallback_from_laboratory_text`. Other sources retain malignant cells/immunostains, PET uptake and a lung mass, but final synthesis says **no findings extracted**. HIGH severity appears with ROUTINE triage. |
| Liver cirrhosis | 116.9s | 1.04s | 50.37s | The impression already connects HCV evidence, cirrhotic morphology, portal hypertension and synthetic dysfunction. Many duplicate source findings remain; no structured differential/exclusion history exists. Endoscopy is suggested despite an existing endoscopy report. |
| Kidney failure | 132.7s | 49.86s | 56.53s | Good combined account of renal impairment, small echogenic kidneys, albuminuria and complications. More than 100s is spent in serial synthesis stages; no explicit competing causes, discriminating tests or longitudinal uncertainty. |
| Hernia | 149.8s | 60.78s | 55.75s | The hernia and absent complication signs are described. Detailed lab copying dominates; negative observations should be retained as opposing evidence for complications. |
| Fatty liver | 149.2s | 56.15s | 55.77s | Steatosis, metabolic findings and fibrosis assessments are connected. Etiologic labels need explicit uncertainty when missing alcohol/other-cause history; normal hepatitis tests should reduce support with their limits. |
| Brain tumour | 93.8s | 1.53s | 49.86s | Imaging mass effect and pathology are combined. Explicit pathology diagnoses should be distinguished from classifier/narrative suggestions; alternative imaging hypotheses and subsequent pathology resolution should remain traceable. |
| Bronchitis | 113.4s | 39.35s | 48.09s | Already gives some cross-source reasoning. Negative viral tests/culture are not universal exclusions; differentiate airway inflammation, pneumonia and other causes using documented imaging, timing and limitations. |
| Stroke | 9.4s | skipped | skipped | All 13 document nodes failed with the undefined fallback function. The short runtime is failed execution, not a successful performance result. |
| Hepatitis (unindexed) | 67.9s | 45.25s | 4.60s | Normal source observations collapse into “No acute abnormality”, including an invalid image id `0`; report synthesis fails with BadRequestError but the report is not marked degraded. Normal tests do not settle persistent symptoms. |

The original test documents are needed for an independent assessment of whether
their values and diagnoses were extracted correctly. The report filenames are
case labels supplied by the test author, not ground-truth evidence by themselves.

## Cross-cutting causes

### 1. Observation collection is not a differential

Some existing impressions already combine reports well. The missing contract is
consistent, structured output recording **which diseases explain the combined
evidence**, which observations oppose each explanation, what remains unknown,
and what investigation would distinguish alternatives. A four-sentence
impression cannot reliably serve as that persistent state.

**Changed:** `ClinicalAssessment` / `DiagnosticHypothesis` contain those fields.
`report_synthesis` produces them in the existing final reasoning call; markdown,
JSON and chat display the result. Findings remain available for detailed review.

### 2. Failures look too successful

The lab fallback was referenced but never defined. Failed consolidation could
discard structured observations. Report synthesis caught failures internally,
returned fallback text, and left its node DONE. The integrator only counted
failed/skipped nodes as degraded and enforced severity but not its triage floor.
Those mechanisms explain the inconsistent lung-cancer and hepatitis outputs.

**Changed:** exact native lab-line salvage exists; malformed non-lab responses
remain explicit failures. Observations/source reads survive consolidation
fallback. Synthesis errors, partial reads, native fallback and assessment
validation issues contribute to degradation. Triage floors are enforced again
when assembling the final report.

### 3. Warning merging loses meaning

The “Image quality — labs.pdf#page=1” warnings show a document-quality mapping
being iterated as dictionary keys. A source name is not a quality problem.

**Changed:** document-quality mappings retain their actual issue text and source.

### 4. Serial copying and endpoint pressure create latency

The larger reports contain dozens of lab entries with raw JSON and a second
prose representation. Consolidation often consumes 40–60s copying them, followed
by a similarly slow report. Changed graph jobs/questions invalidate document
reads, and upload-with-question formerly added another text answer call.

**Changed:** document-only deduplication is deterministic; extraction is
question-independent and uses a fast qualified model; final reasoning receives
balanced source cards; upload answers reuse that report. SDK retry duplication
is removed and selection can route around recent endpoint failures.

## Verification of the new path

A live synthetic case with elevated WBC/neutrophils/CRP, a written focal lung
consolidation report and cough/fever/pleuritic pain returned:
- community-acquired bacterial pneumonia — likely;
- atypical/viral pneumonia — possible;
- pulmonary embolism with infarction — less likely;
- supporting/opposing source links and targeted investigations for each.

This is an observed model output, not a verified diagnosis. An explicit
synthetic clinician exclusion of bacterial pneumonia then moved the saved
hypothesis to `ruled_out`; closing and reopening restored the same session.
Changing the wording/order of parenthetical disease qualifiers no longer creates
a second copy of an existing hypothesis.

The live study took **6.49s** initially and **0.14s** for an unchanged cached
rerun. It contains two documents, so those timings cannot be compared directly
to the 6–13-document archived cases. The regression suite separately checks
source rejection, negative-test limits, exclusion persistence, native lab
salvage, schema/provider fallback and dependency-ready execution.

## Next evaluation to perform

1. Re-run all ten original input folders and retain raw source documents,
   prompt/model versions, reports and timings alongside the case index.
2. Have clinician reviewers assess the ranked differential, the correctness of
   each support/opposition link, and whether proposed tests add information.
3. Add sequential follow-up results: non-excluding negative tests, explicit
   exclusions, pathology confirmation and genuinely contradictory new evidence.
4. Compare equal-input cold/warm latency and API call/token counts with the
   baseline, including failures and retries. Evaluate direct images separately
   from written imaging reports.
