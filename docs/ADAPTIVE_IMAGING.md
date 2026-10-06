# Adaptive verification and direct-image workflows

Implemented on `feature/aos-patient-continuity`, 2026-10-05.

## Structured task understanding

Both planners attach a `TaskUnderstanding` contract: normalized intent, actual
evidence inventory, missing/limited evidence, required live-registry skills,
structural acceptance criteria and execution budgets. Free-form planning sees
the same contract; evidence and budgets are bound by the caller, not invented
by a model. Budget values are explicit API inputs, not guesses parsed from prose.

```python
from models import TaskBudget
from pipeline import analyze_study

report, study, graph = analyze_study(
    "./studies/patient-001",
    with_literature=False,
    task_budget=TaskBudget(max_replans=4, max_added_nodes=8,
                           max_attempts_per_node=2, max_elapsed_s=600),
)
```

`max_elapsed_s` bounds graph execution, excluding ingest/render. Memory and cost
limits are optional hard constraints against capability declarations. Unknown
prices/memory cannot satisfy a hard limit. Cost reservations are conservative
declared capability-call costs, not measured provider billing; model/tool
capabilities without a reliable all-inclusive declaration remain ineligible.

Acceptance checks measure output/source coverage, not clinical correctness.
Failed reads cannot satisfy coverage merely because a file decoded successfully.
An unmet criterion is exposed in report JSON/diagnostics and degrades the run.

## Bounded runtime graph revision

`core/replanning.py` handles domain-neutral graph changes. Capabilities can emit:

```json
{
  "verification_requests": [{
    "reason": "Extraction is incomplete",
    "description": "Recheck the specified source page",
    "requirements": {
      "skills": ["verify_documents"], "outputs": ["verification"]
    },
    "artifacts": {
      "document_ids": ["labs.pdf"], "document_page_numbers": [2]
    }
  }]
}
```

The executor selects an eligible live capability through DNA, validates a
detached DAG patch, inserts the node, and makes pending direct consumers wait
for its result. It does not modify running/completed nodes or replay successful
extraction. Optional verification failure retains usable original evidence.
Initial implementation failures can introduce an untried equivalent capability
and rewire pending consumers/partial-input requirements to that replacement.

Limits: four applied patches/eight added nodes by default, at most eight
requests per original node, one recovery level, no recursive verification,
and the existing 64-node ceiling. `MAOS_MAX_REPLANS` / `MAOS_MAX_ADDED_NODES`
override defaults. Rejected, unavailable and budget-exhausted requests remain
visible. Original plans, revised edges, reasons, selected capabilities and
structural acceptance results are saved with the report and executed plan.

Cooperative deadlines prevent expired queued work and further retries/model
calls. Python still cannot forcibly terminate a running native call. Local
OpenAI-compatible image calls honor the remaining HTTP deadline and serialize
GPU-server requests through `MAOS_MAX_GPU_WORKERS`.

### Medical verification capabilities

- **`document_verification`**: uncertain/failed/visual-only extraction triggers
  a rendered-page reread when available, even when native text exists. Otherwise
  it performs a limited exact-numeric native-text audit. Differences and missing
  verification remain explicit; reread findings do not silently replace originals.
- **`temporal_comparison`**: differing repeated numeric observations across
  sources trigger a date-aware comparison. Missing or multiple dates on a page
  remain ambiguous. No unit conversion, inferred trend or diagnosis is produced.
- **`evidence_verification`**: extraction/reasoning disagreements or invalid
  assessment arguments trigger a provenance/numeric audit. It does not establish
  semantic entailment or clinical validity.

Verification output feeds downstream reasoning with its scope attached. A
post-report evidence audit is also retained in the final report. Definitive
diagnostic status continues to require native/clinician evidence under the
existing longitudinal assessment rules.

## Direct images and DICOM context

- All decodable frames of a multi-frame object are retained by study ingestion;
  each has an exact `file.dcm#frame=N` reference and independent frame hash.
  `read_dicom()` retains a single-frame compatibility preview API; it is not
  the study-ingestion path.
- pydicom 3 streams decoded frames; pydicom 2 falls back to a bounded array.
  `MAOS_MAX_DICOM_DECODED_MB=512` checks decompressed size before pixel decode.
  Missing codecs, bad/blank frames and excluded files remain visible.
- Scanner study/series identities are hashed under de-identification. Geometry,
  instance/frame ordering, shared/per-frame enhanced-DICOM transforms and window
  metadata survive preprocessing-cache hits. Original dimensions/quality survive
  too; a cached JPEG is not mistaken for the original acquisition.
  Inference-cache keys use the actual prepared-pixel hash, so changed windowing,
  resizing or encoding cannot reuse a read of an older pixel representation.
- Images are partitioned by study/series, modality/region, orientation and known
  temporal/stack identity. Position projected onto the slice normal determines
  anatomical order when available; otherwise ordering limitations are explicit.
  Irregular spacing/repeated positions are flagged, not diagnosed as missing data.
- Positive `MAOS_MAX_IMAGES` samples decoded, ordered frames across represented
  series and marks partial coverage. `0` includes all decoded frames. Sampling
  bounds inference, not the cost of initial decoding/preparing frames.
- Technical checks identify blank/uniform images, very low resolution and low
  contrast. They do not certify motion/positioning/exposure or diagnostic adequacy.
  Blank/non-medical images do not reach narrative inference as normal images.
- Routing is modality/region-specific. An unsupported broad screener does not
  imply normality or absence of a narrative image read. A matching requested
  specialist can run even when no broad screener covers the image.

The current VLM sees **one 2D image/frame per call**, with series context in its
prompt. Full series retention is not validated joint 3D CT/MRI interpretation.
Acquisition completeness remains `unknown` unless sampling/decode failures make
it explicitly `partial`; the software never asserts that supplied exports are
a complete scanner acquisition. Prompt/synthesis context limits still apply to
large studies, so review per-frame reads and coverage counts separately.

## Separate evaluation lanes

Reports expose `image_reads`, document `source_reads`, `image_quality`, `series`,
actual `input_manifest.analysed` flags and `evaluation_groups`. Findings identify
direct-image/document/mixed evidence. Invented image references are rejected by
consolidation. Markdown separates generated image reads from written reports.

```bash
# Existing report JSON, no new inference:
python evaluate_images.py --reports outputs/reports/example.json

# Explicit replay after installing/configuring the image model:
python evaluate_images.py --studies ./cases/chest-xray ./cases/head-ct
```

Groups are kept separate by evidence kind, modality and body region, with supplied,
read, screened and quality-limited counts. Archived reports without these new
groups cannot retrospectively establish direct-image coverage. These are
operational metrics; no diagnostic-accuracy claim follows from them.

The ten archived disease reports contain zero direct images. Real image-model
evaluation still needs held-out cases with reference findings, modality/body
region, patient-level separation and clinician review. Measure sensitivity,
false alarms, unsupported claims and calibration separately for each image
distribution; do not pool written-report performance with pixel interpretation.

## Verification performed

Offline regressions cover graph insertion/recovery, immutable original plans,
optional verification failure, bounded attempts/cost/deadline handling, temporal
ambiguity, all-frame grayscale/color DICOM ingestion, geometry ordering,
sampling/failure coverage, cache metadata, blank-image rejection, source binding,
separate image/report lanes, requested specialist routing and rendered-page rereads.
They use synthetic files/injected endpoints, not pretrained model accuracy.

The extracted legacy suite has two fixture-contract updates **only in the
temporary copy**: the exact registry roster includes four new capabilities, and
the happy-path report stub supplies the now-required structured assessment.
Repository test deletions and archived disease outputs are preserved.

Latest full run: **266 passed in 8.91s** (31 continuity cases + 31 adaptive/image
cases + 204 legacy cases with the two temporary fixture updates). Compilation
and CRLF-aware `git diff --check` passed.

```bash
PYTHONPATH="$PWD" MAOS_CHAT_EMBEDDINGS=off \
  /tmp/omnirush/maos-venv/bin/python -m pytest \
  tests/test_adaptive_imaging.py tests/test_aos_continuity.py \
  /tmp/omnirush/legacy-maos/tests -q --tb=short --import-mode=importlib \
  -o cache_dir=/tmp/omnirush/pytest-cache
```
