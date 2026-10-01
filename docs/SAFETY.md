# Clinical safety and privacy

## What this system is

Decision support that produces a **draft preliminary read for a qualified
radiologist to verify**. It is not a diagnostic device, it is not validated for
clinical use, and no output should reach a patient record without a clinician's
review and sign-off.

None of the default models is a regulated medical device. `medgemma-4b-it` is a
research model; `vit-xray-pneumonia-classification` is a community checkpoint
trained on a narrow public dataset. Using them on real patients, in any
jurisdiction, is a regulatory question (FDA SaMD, EU MDR, UKCA) that this
codebase does not and cannot answer.

The disclaimer in `models.DISCLAIMER` is emitted at the top *and* bottom of
every report and in the terminal summary. It is not configurable, and should
not be made so.

## Safeguards built into the code

These are enforced in code, not by prompt instruction — a model cannot talk its
way past them.

**Severity cannot be downgraded.** Overall status is computed from the
structured findings, never parsed from prose, and floored by the worst finding
at two independent layers (`report_synthesis.run` and
`IntegratorAgent._build_status`). A model that describes a pneumothorax and
then calls the study routine is overridden, and the override is logged.

**Absence of findings is not normality.** An empty findings list yields
`indeterminate`, never `normal`. A pipeline that failed to find anything and a
study that is genuinely clear must not look identical.

**Failure is visible, never silent.** Any failed or skipped stage sets
`degraded=True`, which puts a banner at the top of the report and lists every
failure in *Run diagnostics*. A partial analysis never presents itself as
complete.

**Non-medical inputs are flagged.** CLIP triage identifies images that are not
medical images, and document analysis classifies non-medical pages without
turning them into an all-clear result. The report warns that these inputs should
be disregarded as clinical evidence.

**Classifier scope is enforced.** The chest classifier refuses to score
non-radiographs rather than emitting confident nonsense about a knee MRI.

**Model disagreement is surfaced, not hidden.** When the classifier flags
something no narrative read mentions, the consolidation stage records it as a
discrepancy and it appears in the report's warnings.

**Hedged language is required.** The VLM system prompt mandates radiological
hedging ("cannot be excluded", "suggestive of") over diagnostic assertion, and
forbids inventing measurements or comparisons with priors it was not given.

**No treatment advice.** `report_synthesis` restricts recommendations to
investigations, referral and follow-up intervals.

**Document content is untrusted.** Extracted PDF/text/office content is never
allowed to issue instructions to the model. Structured document findings are
bound in code to the actual source file and physical page; model-provided
source references are discarded. Exact laboratory values are retained when
present, while missing units and reference ranges remain uncertain.

## Privacy

**PHI is stripped at ingest.** `ingest/dicom_utils._PHI_TAGS` removes patient
name, ID, birth date, address, telephone, referring and performing physician,
institution, accession number and study ID. Clinically useful, non-identifying
fields (age, sex, modality, body part, view) are kept. Controlled by
`MAOS_DEIDENTIFY`, on by default. Tested in
`tests/test_ingest.py::TestDicom::test_phi_is_stripped_by_default`.

**Outbound searches carry no patient data.** `web_search._build_query` builds
its query from finding labels only. This is enforced in the function, not
requested in a prompt. Tested in
`tests/test_capabilities.py::TestLiteratureQuery`.

**Document extraction is bounded.** File size, archive expansion, page count,
text length and chunk size are configurable safety limits. Truncation and
unsupported embedded content are surfaced as warnings rather than silently
omitted.

**Patient data is git-ignored.** `.gitignore` excludes `studies/`, `phi/`,
`*.dcm`, `outputs/reports/` and `.cache/`.

**But hosted inference sends images off the machine.** This is the significant
residual exposure. By default the VLM read goes to the Hugging Face Inference
API, and text synthesis goes to Groq. For real patient data under HIPAA/GDPR
you need either a signed BAA/DPA with those providers, or a fully local
deployment:

```bash
MAOS_VLM_BACKEND=ollama      # local VLM, images never leave
MAOS_BACKEND=local           # local screening models
# and a local text model for synthesis
```

Note that the `.cache/` directory contains preprocessed copies of patient
images and the model's reads of them. Treat it as patient data: encrypt the
volume, and clear it between patients with `python main.py --clear-cache`.

## Known clinical limitations

1. **Nothing here is validated.** No sensitivity or specificity figures exist
   for this pipeline on any population. Do not quote a confidence score to a
   patient — the numbers are model outputs, not clinical performance.
2. **Sampling misses slices.** Series longer than `MAOS_MAX_IMAGES` are evenly
   sampled. A finding visible only on an unsampled slice will be missed. The
   report warns when sampling occurred.
3. **No volumetric reasoning.** CT and MR are read as independent 2D frames.
   Findings that only manifest across slices will be missed.
4. **No prior comparison.** Interval change — often the single most important
   finding — is not assessed.
5. **Narrow label set.** The default classifier screens for one condition.
   Anything outside its vocabulary rests on the VLM alone.
6. **Demographic bias is unmeasured.** Public chest X-ray datasets have known
   skews in age, sex and equipment. Performance across subgroups is unknown.
7. **Adversarial and out-of-distribution inputs.** Triage catches obvious
   non-medical images; it does not catch a subtly corrupted or wrong-patient
   study.

## Before any clinical deployment

- [ ] Validate on a representative local dataset with radiologist ground truth
- [ ] Measure sensitivity/specificity per subgroup, not just in aggregate
- [ ] Obtain regulatory clearance for the intended use
- [ ] Execute a BAA/DPA with every hosted provider, or deploy fully locally
- [ ] Route every report through mandatory radiologist sign-off before filing
- [ ] Log and retain every report with its provenance block for audit
- [ ] Define the escalation path for `triage: immediate` (the CLI exits `3`)
- [ ] Set a review cadence for model drift and checkpoint updates

## Reporting a safety concern

Findings that affect patient safety should be treated as blocking defects, not
feature requests. The provenance block in every JSON report records the exact
models, node statuses and timings needed to reproduce a run.
