# Safety, limitations, and research scope

## 1. Safety controls implemented in code

### Severity and triage floors

Severity is derived from structured findings. It is not parsed from the last LLM
paragraph. The report integrator and report-synthesis capability both apply a
floor so a critical/high finding cannot be rendered with a weaker triage.

### Empty evidence is not normality

If every analysis stage fails or no finding is accepted, the report is
`indeterminate`. If images were supplied but unread, a normal document result is
not allowed to establish a normal imaging study.

### Visible degradation

Failed/skipped nodes, missing image reads, unmet acceptance criteria, failed
verification, synthesis fallback, read errors, and partial series coverage
contribute to `degraded=True`. Markdown displays a degraded-run banner and
diagnostics; JSON preserves the same details for machines.

### Source binding and numeric checks

Document findings are bound to the requested page in code. Unknown source
references are removed. Numeric tokens in generated arguments are compared with
native source text; unsupported values are withheld and can trigger verification.

### Untrusted document content

PDF/text/office content is treated as data, not instructions. The document
prompt explicitly prevents document text from issuing commands to the model.

### Privacy controls

With `MAOS_DEIDENTIFY=1`, direct DICOM identifiers are removed and scanner UIDs
are hashed in derived metadata. Literature queries use finding labels, not
patient identifiers or full history. File/page/document limits prevent
unbounded extraction and common office archive hazards.

## 2. Clinical/model limitations

The following limitations remain material:

1. No default checkpoint is clinically validated or a regulated medical device.
2. Most direct image models process one 2D frame, not a validated 3D volume.
3. Sampling a long CT/MR series can miss a finding on an unsampled slice.
4. DICOM headers are treated as authoritative; a wrong header can misroute an
   image.
5. Closed label sets mean “no candidate” only means no known candidate for that
   model.
6. Public datasets may be pediatric, narrow, mislabeled, or correlated across
   checkpoints; high benchmark scores can be misleading.
7. Unsupported anatomy remains VLM-only and may have no supervised corroboration.
8. The VLM can hallucinate or overinterpret a written report; the cascade makes
   disagreement visible but does not make the generator reliable by itself.
9. Python thread timeouts cannot forcibly terminate every blocking native call;
   a timed-out underlying operation may continue in the background.
10. Hosted inference exposes images/text to external providers unless a fully
    local deployment is configured.
11. Document extraction can be syntactically valid while clinically incomplete;
    source existence is not semantic proof.
12. Longitudinal identity and temporality are only partially normalized; dates,
    units, aliases, and episode boundaries need further modeling.

## 3. Evaluation scope supported by the repository

### Appropriate claims

- The graph can contain independent branches and bounded parallel work.
- A failed capability is contained and recorded.
- DICOM multi-frame handling preserves decodable frames/metadata and exposes
  partial coverage.
- The model cascade prevents obvious cross-modality use of a chest classifier.
- Unsupported screening is explicit.
- Source-bound reasoning and longitudinal exclusion behavior have offline tests.
- Historical cases expose real backend, fixture, extraction, and synthesis
  failure modes.

### Claims not supported by the repository

- Sensitivity/specificity of the overall pipeline.
- Clinical superiority over radiologists or another product.
- Generalization to a patient population.
- Calibration of model confidence as disease probability.
- Safety of treatment recommendations.
- Clinical value of saved reports without clinician adjudication.

## 4. Recommended deployment/evaluation discipline

Before any real clinical use, the project documentation calls for:

- representative local data with radiologist reference labels;
- patient-level train/validation/test separation;
- subgroup analysis by age, sex, scanner, acquisition, and demographic factors;
- direct-image evaluation separate from written-report extraction;
- cold/warm latency, call count, token, retry, fallback, and cache measurements;
- mandatory qualified review before filing;
- provider privacy contracts or fully local inference; and
- drift/checkpoint update monitoring.

The research results document below follows this discipline when interpreting the
available artifacts: it treats operational outputs as evidence about software
behavior and flags where a clinical conclusion would be overclaiming.
