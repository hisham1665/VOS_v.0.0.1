# Medical AOS

An agentic pipeline that reads a study folder containing medical images —
X-rays, CT/MR slices, DICOM series — plus PDFs, lab/blood reports, clinical
notes, CSV/JSON exports, DOCX and XLSX files. It produces one structured
preliminary report with patient status, findings, source references, and
recommended next steps for a clinician to verify.

> ⚠️ **AI-generated draft for clinician review.** This is decision support
> only: not a diagnosis, not validated for clinical use, not a regulated
> device. Every report must be verified by a qualified radiologist before it
> informs patient care. See [docs/SAFETY.md](docs/SAFETY.md).

---

## What it does

```
studies/patient-001/          →   a report the referring doctor can act on
├── chest_pa.dcm                  ├── Patient status: 🟠 HIGH, triage URGENT
├── chest_lateral.dcm             ├── Findings table with severity + confidence
├── prior_ct_slice.jpg            ├── Impression
└── patient.json                  ├── Recommended next steps
                                  ├── Reference literature
                                  └── Full provenance: models, timings, failures
```

Seven stages, run as a parallel task graph:

1. **Ingest** — walk the folder, decode DICOM (rescale LUT, windowing), strip
   PHI, resize, cache, and extract bounded pages/chunks from PDF, TXT/MD,
   CSV/TSV, JSON, DOCX and XLSX. Unreadable files are recorded, never fatal.
2. **Triage** — a general CLIP gate rejects non-medical images; PubMedCLIP
   identifies modality (X-ray / CT / MRI / ultrasound / …) and body region.
   DICOM headers are authoritative; filename hints are a prior CLIP can overrule.
3. **Screen (cascade stage 1)** — each image is routed, by modality *and*
   region, to the screener trained for it (chest X-ray → NIH-14 multi-label,
   head CT → haemorrhage, brain MRI → tumour, limb X-ray → fracture, …).
   Images no model covers are marked *not screened*, never "normal".
4. **Confirm (cascade stage 2)** — every stage-1 candidate, and every condition
   named in the referring question, is re-checked by a disease-specific
   specialist (pneumonia ×2 incl. TorchXRayVision, TB, stroke, …) → supported /
   not supported / equivocal / unverifiable.
5. **Read** — a medical vision-language model reads each image with the
   cascade evidence (and its limits) in the prompt, while a document agent
   reads each extracted file/page in parallel.
6. **Consolidate** — merge duplicate findings across views and documents,
   reconcile disagreements, and surface specialist-supported candidates the
   VLM missed.
7. **Report** — impression, patient status, triage level, next steps, and a
   per-image screening-cascade table.

See [docs/SCREENING_CASCADE.md](docs/SCREENING_CASCADE.md) for the model
roster, benchmark numbers and — importantly — where it breaks.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # add HF_TOKEN and/or GROQ_API_KEY

python main.py --doctor       # check models, credentials, dependencies
python main.py --study ./studies/patient-001 \
               --history "54M, 3 days cough and fever" \
               --question "rule out pneumonia"
```

Reports are written to `outputs/reports/` as markdown and JSON, and a summary
prints to the terminal.

### Interactive chat

```bash
python main.py --chat --name patient-001   # full-screen chat; attach with @path or drag & drop
python main.py --resume                    # pick up any earlier session
```

Attach scans, PDFs and notes, ask questions, and ask follow-ups that remember
everything uploaded and said in the session. Each session lives in
`sessions/<name>_<id>/` with its uploads, reports, memory, a JSONL transcript
and a terminal log. Design and memory strategy:
[docs/CHAT_TUI.md](docs/CHAT_TUI.md).

### CLI

| Flag | Purpose |
|---|---|
| `--study PATH` | Folder of images, or a single image/DICOM file |
| `--history TEXT` | Clinical history — substantially improves the read |
| `--question TEXT` | The referring clinician's specific question |
| `--no-literature` | Skip the literature stage (faster, no network search) |
| `--json` | Print the full report as JSON |
| `--out DIR` | Where to write the report |
| `--doctor` | Environment preflight, then exit |
| `--clear-cache` | Drop cached images and model outputs |
| `--ask PROMPT` | Free-form job through the LLM planner instead of the fixed plan |
| `--chat` / `--name NAME` | Open the chat TUI on a new (named) session |
| `--resume [ID]` | Open the chat TUI on a saved session (picker without ID) |
| `--sessions` | List saved chat sessions |
| `--verbose` | Per-stage timing logs |

Exit codes: `0` normal, `3` triage is **immediate**, `1` error. Alert on `3`.

### Study folder layout

Anything readable works. A `patient.json` sidecar adds context:

```json
{
  "age": "54",
  "sex": "M",
  "clinical_history": "3 days cough and fever, no travel",
  "referring_question": "rule out pneumonia"
}
```

PDFs, text/tabular files and office documents are extracted into bounded,
source-attributed pages. `patient.json` fills patient context; JSON with extra
clinical fields is also analyzed as a document. Duplicate inputs are detected
by content hash. Set `MAOS_MAX_IMAGES` to a positive value to sample large
series; `0` (the default) reads all image files.

## Configuration

Everything is an environment variable; see [.env.example](.env.example) for the
full list. The defaults target a small laptop GPU.

```bash
HF_TOKEN=hf_...              # hosted model inference
GROQ_API_KEY=gsk_...         # fast text synthesis (optional; HF is the fallback)

MAOS_VLM_BACKEND=ollama      # keep images on-premise
MAOS_MAX_WORKERS=8           # concurrent nodes
MAOS_MAX_GPU_WORKERS=1       # concurrent local GPU calls
MAOS_MAX_IMAGES=0            # 0 = all images; positive value enables sampling
MAOS_MAX_FILE_MB=100         # per-input safety budget
MAOS_MAX_DOCUMENT_PAGES=200  # PDF/page/chunk extraction budget
MAOS_DOCUMENT_CHUNK_CHARS=8000
```

## Library use

```python
from pipeline import analyze_study

report, study, graph = analyze_study(
    "./studies/patient-001",
    clinical_history="54M, cough and fever",
    referring_question="rule out pneumonia",
)

print(report.status.overall_severity)   # Severity.HIGH
print(report.status.triage)             # "urgent"
for finding in report.findings:
    print(finding.label, finding.severity, finding.confidence)

report.to_json()                        # everything, serialised
```

Every stage is separately callable — `load_study`, `build_radiology_plan`,
`GraphExecutor().run`, `IntegratorAgent().integrate`, `write_report` — so a
service can cache ingest, or a test can drive the executor with a hand-built
graph.

## Where things live

```
main.py              CLI                  pipeline.py    stage sequencing
models.py            every type           config.py      all settings
ingest/              folder → Study       agents/        orchestration
capabilities/        the only layer that calls models
reporting/           report → markdown + JSON
core/                errors, logging, retry, cache
tests/               offline tests, no network, no weights
```

## Tests

```bash
python -m pytest tests/ -q     # offline suite; no credentials or model weights needed
```

Every model call is stubbed, so the suite needs no credentials, no network and
no weights. It covers graph validation, DICOM decoding and PHI stripping,
failure containment, timeout handling, the concurrency ceiling, artifact
propagation, and the safety invariant that severity is never downgraded below
the worst finding.

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, data flow, why the plan is code and not a prompt |
| [FAILURE_MODES.md](docs/FAILURE_MODES.md) | All 23 failure points found and fixed, plus remaining limitations |
| [MODELS.md](docs/MODELS.md) | The model roster, routing, swapping checkpoints |
| [PERFORMANCE.md](docs/PERFORMANCE.md) | Where time goes, measured optimisations, tuning by hardware |
| [SAFETY.md](docs/SAFETY.md) | Clinical safeguards, privacy, deployment checklist |

## Requirements

Python 3.10+. A GPU is optional — small models fall back to CPU, large ones to
hosted inference. `pydicom` is optional but required for `.dcm` files;
`python main.py --doctor` reports exactly what is missing and how to install it.
