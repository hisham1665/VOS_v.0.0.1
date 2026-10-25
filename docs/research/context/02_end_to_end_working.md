# End-to-end working

This document follows one study from disk to report. It describes both the
control flow and the structured evidence flow.

## 1. Input discovery and study construction

`load_study(path)` first resolves whether the input is a directory or a single
file. Directory traversal is recursive, skips hidden directories and known
desktop noise, and refuses symlinks that leave the supplied root. Every file is
classified by suffix or DICOM magic number.

Supported patient context comes from CLI arguments and a pure JSON sidecar:

```json
{
  "age": "54",
  "sex": "M",
  "clinical_history": "3 days cough and fever",
  "referring_question": "rule out pneumonia"
}
```

Extra clinical keys in JSON are retained as an analyzable document rather than
silently discarded. Unsupported files are recorded in `Study.skipped_files`.

### Image path

1. The source bytes are hashed.
2. Raster images are EXIF-transposed and converted to RGB.
3. DICOM images are decoded using `pydicom`; all decodable frames are retained.
4. DICOM modality, body part, view, geometry, frame index, and hashed UIDs are
   stored in metadata.
5. Pixel values receive modality LUT/rescale and window processing where present.
6. Technical checks detect uniform/blank pixels, low resolution, and very low
   contrast. These checks do not certify diagnostic quality.
7. The image is resized to `MAOS_MAX_IMAGE_EDGE` (default 896) and encoded as a
   cached JPEG.
8. `assemble_series()` groups and orders images; sampling is explicit and marks
   `partial` coverage.

### Document path

1. File size and archive-expansion budgets are checked.
2. Text files are decoded with UTF-8/BOM/UTF-16/cp1252 fallbacks.
3. PDFs retain physical page numbers and render each page to a cached image,
   including pages without native text.
4. DOCX paragraphs/tables and XLSX cell values are converted to bounded text.
5. Long text is split into bounded pages/chunks.
6. Warnings about truncation, unreadable pages, embedded media, formulas, and
   missing native text are attached to the document/page.

The result is a `Study` containing `images`, `documents`, `series`, `patient`,
warnings, skipped files, and load time. No model call is needed to construct the
study.

## 2. Plan construction

For a radiology study, `agents/planner.py` creates a deterministic graph. The
usual nodes are:

```text
quality
   ↓
triage
   ↓
screen
   ↓
confirm
   ├───────────────┐
read0 ... readN    │
document0 ... N ───┴─> consolidate -> [literature] -> report
```

Important dependency choices:

- screening depends on triage, preventing an `Unknown` modality from being sent
  to a chest classifier;
- image reads can continue when the screening cascade fails because they use a
  best-effort/partial dependency policy;
- consolidation needs at least one successful image or document read and also
  receives screening/confirmation artifacts directly;
- literature is optional and runs only after consolidation; and
- report synthesis can produce a degraded evidence-only report when upstream
  branches are incomplete.

The planner also attaches a `TaskUnderstanding` contract containing:

- normalized intent;
- available evidence and limitations;
- missing evidence;
- required capability skills;
- source/output acceptance criteria; and
- `TaskBudget` limits for replans, added nodes, attempts, elapsed time, cost,
  and memory.

The plan is written to `plan.md` before execution and refreshed afterward so
node statuses, durations, selection traces, revisions, and acceptance results
are inspectable.

## 3. Dependency-ready execution

`GraphExecutor` validates node IDs, dependencies, roots, cycles, and the maximum
64-node limit. It uses one `ThreadPoolExecutor` bounded by
`MAOS_MAX_WORKERS`; it does not construct an unbounded pool per wave.

For each pending node:

1. parent terminal states are checked;
2. strict failures block descendants, while `allow_partial` nodes may proceed
   with successful parents;
3. parent text is concatenated and capped at 30,000 characters;
4. parent artifacts are merged structurally (lists concatenate, dictionaries
   merge, deterministic later values replace scalar values);
5. task budgets and declared costs are reserved;
6. the node is marked `running` before submission; and
7. `SubAgent.perform()` executes it with a capability request.

Children start as soon as their own dependencies are terminal. A slow unrelated
root therefore does not create a global barrier for a ready child. Local GPU or
local vision-server calls have a separate semaphore (`MAOS_MAX_GPU_WORKERS`,
default 1 on a small GPU).

## 4. Capability invocation contract

Every capability receives:

```python
CapabilityRequest(
    instruction="the node description",
    text="parent prose",
    study=study,
    image_ids=[...],
    document_ids=[...],
    document_page_numbers=[...],
    node_id="read0",
    artifacts={...},
)
```

It returns:

```python
CapabilityResult(
    text="human-readable output for downstream prompts",
    artifacts={"typed_key": structured_value},
)
```

The prose channel supports model prompts and human-readable plan output. The
artifact channel carries typed evidence and prevents downstream components from
having to re-parse model prose. Examples include:

```text
triage -> screening -> confirmation
reads + document_reads -> findings
findings + source material -> clinical_assessment + status
```

`SubAgent` rejects unknown capabilities, empty output, wrong result types,
timeouts, and failed attempts. It tries compatible registered alternatives when
their DNA satisfies the same requirements. All attempts and selected
implementations are stored on the node.

## 5. The clinical interpretation cascade

### Stage 0: technical quality

Checks blank/uniform pixels, contrast/resolution, and series coverage. A blank
image is not converted into a normal finding.

### Stage 1: triage

`modality_triage` combines:

- authoritative DICOM modality headers;
- conservative filename/body-region hints;
- PubMedCLIP prompt ensembles for modality and region; and
- a general CLIP non-medical gate.

DICOM headers are not overridden. Filename hints are only priors and can be
overridden by confident visual disagreement. Low-confidence routing is withheld
from supervised screening.

### Stage 2: broad screening

`image_classification` asks only models that cover the image's modality and
region. It emits per-image model scores, candidate conditions, abnormality
score, routing status, and a reason for `not screened` when no valid model
exists. A quiet screener means “nothing in this model's label set crossed its
threshold,” not “normal.”

### Stage 3: specialist confirmation

`specialist_confirmation` maps candidates through `model_zoo.RECHECK`. Conditions
named in the history/referring question can trigger a specialist even if the
broad screener is quiet. Outcomes are:

- `supported`;
- `not_supported`;
- `equivocal`; or
- `unverifiable` when no independent specialist exists.

A screener is not counted as its own second opinion. The verdict is supporting
evidence for narrative interpretation, not a diagnosis.

### Stage 4: narrative image reading

`vision` reads each prepared image/frame independently. It receives series
context, patient age/sex when available, history, question, technical warnings,
and scoped cascade results. The prompt requires modality/view, image quality,
observations with severity/confidence, and an impression. It forbids invented
measurements, unseen prior comparisons, and unhedged diagnostic assertions.

Routes are selected in this order when `auto` is used:

1. reachable local Ollama vision model;
2. hosted Hugging Face VLM;
3. local Transformers VLM on a sufficiently large GPU.

The selected route and model ID are part of the cache key and report provenance.

### Stage 5: document reading

`document_analysis` processes each assigned page. Native text is preferred for
text-bearing pages; rendered-page vision is used for image-only pages or
explicit verification. The model must return a constrained JSON object. The
capability then:

- canonicalizes document type;
- drops administrative labels from the clinical findings list;
- clamps confidence and normalizes severity;
- discards model-supplied source references and binds the exact page in code;
- checks numeric tokens against native page text; and
- retains raw model output for audit.

If the text backend fails and the page contains recognizable laboratory lines,
exact native lab lines may be salvaged as `indeterminate` observations. Arbitrary
notes are not silently converted into clinical findings.

## 6. Consolidation and reasoning

`cross_image_synthesis` merges image/document reads and cascade evidence.

- Document-only validated observations are deduplicated deterministically to
  avoid an expensive LLM recopying dozens of laboratory entries.
- Image-containing studies use a reconciliation LLM to merge duplicates,
  preserve disagreements, and surface specialist-supported candidates that a
  narrative read missed.
- Findings without an allowed image ID or page source are rejected.
- The capability refuses to create a clinical findings list from screening output
  alone when every narrative/document read failed.

`report_synthesis` then constructs a balanced evidence body, preventing a long
laboratory page from crowding all imaging evidence out of the reasoning prompt.
It asks for:

- indication and technique;
- impression and one-line summary;
- triage and red flags;
- recommended investigation/referral/follow-up actions;
- a short differential with status;
- supporting and opposing evidence;
- missing information and unresolved questions; and
- targeted tests with interpretation of how results would change the differential.

Negative tests are not automatically treated as rule-outs. `confirmed` and
`ruled_out` require explicit authoritative source or clinician evidence.

## 7. Integration and status safety

`IntegratorAgent` reads structured artifacts, not only sink-node prose. It
constructs the report's findings, source reads, image reads, screening table,
verification records, evidence groups, node trace, model list, warnings, and
acceptance results.

Overall severity is the maximum severity of structured findings. If the model
returns a weaker value, the structured finding wins. Triage is floored by the
severity mapping:

| Severity | Minimum triage |
|---|---|
| critical | immediate |
| high | urgent |
| moderate/low/normal/indeterminate | routine, subject to incomplete-coverage rules |

An empty finding set is `indeterminate`, not `normal`. If images were supplied
but not all were read, a document-only normal result cannot establish a normal
imaging study.

## 8. Runtime adaptation

Capabilities may emit structured `verification_requests`. `RuntimeReplanner` can
insert a compatible verification node or an untried equivalent implementation.
It validates a detached graph before changing live edges, rewires only pending
consumers, and records the reason, selected capability, revised plan, and
budget outcome.

Default bounds include four replans, eight added nodes, eight verification
requests per source node, one recovery level, and the 64-node graph ceiling.
Completed/running work is immutable. A failed verification does not erase usable
original evidence.

## 9. Rendering and operational output

`write_report()` produces:

```text
outputs/reports/<study-id>-<timestamp>.md
outputs/reports/<study-id>-<timestamp>.json
outputs/plan.md
```

The Markdown report is clinician-oriented. The JSON report is the machine/audit
contract. Both include the safety disclaimer. `--json` prints the structured
report to stdout; otherwise the terminal receives a concise status summary.
