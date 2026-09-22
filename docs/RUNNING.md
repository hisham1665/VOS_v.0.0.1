# AOS Exam Evaluator — Operator's Run Guide

Phase-by-phase command reference for running the exam evaluation system
(modules under `aos_v0.exam`). Everything here was verified against the source
on the branch **`phase14`** (phases 1–13, full suite: `376 passed,
5 subtests`).

---

## 1. Install

Requires Python **3.11+**.

```bash
pip install -r requirements.txt
```

> **Note (current packaging gap):** `Pillow` is imported at runtime by the
> PDF/image intake and the research batch-throughput probe, but is not yet
> declared in `requirements.txt`/`pyproject.toml`. Install it explicitly until
> the packaging fix lands:
>
> ```bash
> pip install Pillow
> ```

Optional: `pdfplumber`, `pypdfium2` are already in `requirements.txt`
(used by intake/OCR). No API keys are needed for the **local structural
engines**; `groq`/`openai`/`huggingface_hub` keys are only used if you run
remote model resources (via the `aos` kernel CLI or `--registry hf`).

Quick sanity check after install:

```bash
python3 -m aos_v0.exam.config examples/sample_exam.json
```

---

## 2. The one required artifact: an exam configuration

Every exam command consumes a **config JSON** (`examples/sample_exam.json` is
the reference). It declares the exam header plus, per question:

```jsonc
{
  "question_id": "Q1",
  "text": "Which switching technique forwards a frame only after the entire frame has been received?",
  "max_marks": 1,
  "question_type": "mcq",
  "negative_marks": 0.25,
  "answer_key": {
    "reference_answers": ["b. Store-and-forward switching"],
    "expected_concepts": ["store-and-forward switching"],
    "accepted_alternatives": [],
    "keywords": ["entire frame", "forward after receive"]
  }
}
```

Validate / inspect it at any time:

```bash
python3 -m aos_v0.exam.config <config.json|config.yaml>
```

---

## 3. CLI reference

### 3.1 App-level entry points (kernel CLI)

```bash
aos "explain TCP congestion control and cite two mechanisms"        # run a task
aos --budget 0.25 "summarize this file" --input notes.pdf           # typed input + budget cap
aos --image scan.png "transcribe and describe"                      # image input shortcut
aos-tui                                                            # interactive TUI
aos-registry-export                                                # writes data/outputs/registry_data.json
```

`aos` behaves differently from the exam batch CLIs: it is the original AOS
agent kernel, runs a single prompt, and prints a final answer (see
`aos_v0/cli.py`).

### 3.2 `python -m aos_v0.exam.intake` — document intake & pre-processing

```bash
python3 -m aos_v0.exam.intake <source> [options]
```

| Option | Meaning |
|---|---|
| `source` | PDF / image / ZIP file, or a folder of images |
| `--dpi FLOAT` | nominal scan DPI (default 300) |
| `--max-pages N` | cap pages read |
| `--auto-rotate` | rotate pages detected to be sideways |
| `--no-deskew` | disable deskewing |
| `--expected-pages N` | warn if page count differs |
| `--write-pages DIR` | dump normalized PNGs to `DIR` |
| `--json` | emit machine-readable result instead of prose |

### 3.3 `python -m aos_v0.exam.ocr` — OCR service (single pass or ladder)

```bash
python3 -m aos_v0.exam.ocr <source> [--dpi FLOAT]
    [--strategy {auto,printed,handwritten}] [--no-local] [--json]
```

`--strategy` selects the OCR ladder entry (`auto` tries structural, then
provider fallbacks). `--no-local` disables the bundled deterministic adapter
(forces registered remote models only).

### 3.4 `python -m aos_v0.exam.structure` — pages → structured answer sheet

```bash
python3 -m aos_v0.exam.structure <source> [--config <exam.json>] [--roster roster.json] [--json]
```

Produces the Phase-5 answer-sheet JSON (identity + per-question mapped blocks).
`--config` supplies question ids/labels; `--roster` supplies `{"entries": [{"roll_no": ..., "name": ...}]}` for identity lookup.

### 3.5 `python -m aos_v0.exam.evaluate` — evaluate a structured sheet

```bash
python3 -m aos_v0.exam.evaluate <sheet.json> --config <exam.json>
    [--roster roster.json] [--json]
```

Runs the two-agent semantic evaluation (primary + verifier + reconciliation).
`--json` prints the full plan-shaped evaluation object. Note: this evaluates a
**single** hand-produced sheet JSON; most users should use `batch` instead.

### 3.6 `python -m aos_v0.exam.batch` — mass evaluation (the main runner)

```bash
python3 -m aos_v0.exam.batch <exam.json> <answers> [options]
```

`answers` is a **folder** of sheets, a **ZIP**, or a single answer sheet.
This is the Phase-8 orchestrated pipeline: OCR → layout → identity →
segmentation → per-question two-agent evaluation → reconciliation → reports.

| Option | Meaning |
|---|---|
| `--out CHECKPOINT` | checkpoint dump dir (resume-safe: a rerun resumes instead of rerunning) |
| `--results RESULTS` | append-only JSONL of each paper's full `ExamRunResult` (required later by `reporting`) |
| `--workers N` | parallel workers (default 4) |
| `--retries N` | extra attempts per paper beyond the first (default 1) |
| `--timeout SECONDS` | per-paper wall-clock cap |
| `--force` | reprocess completed papers (ignore checkpoint) |
| `--status` | print a summary of the last run: `X completed, Y review, Z failed` |
| `--json` | JSON machine-readable summary |

```bash
# one-shot end to end, with everything reporting needs:
python3 -m aos_v0.exam.batch examples/sample_exam.json sheets/ \
    --out runs/checkpoint --results runs/results.jsonl --workers 4 --retries 1
```

### 3.7 `python -m aos_v0.exam.review` — human review queue + audit

```bash
python3 -m aos_v0.exam.review <queue_dir> <command> [args]
```

Commands:

```bash
python3 -m aos_v0.exam.review runs/review_queue list [--all]        # pending items (+ --all to include reviewed)
python3 -m aos_v0.exam.review runs/review_queue show <item_id>      # full review card (evidence, agents, proposal)
python3 -m aos_v0.exam.review runs/review_queue resolve <item_id> \
    --decision {accept,modify,escalate} [--marks FLOAT] [--reviewer NAME] [--note TEXT]
python3 -m aos_v0.exam.review runs/review_queue stats                # queue summary
python3 -m aos_v0.exam.review runs/review_queue history --paper <paper_id> [--json]
```

Semantics (golden rules):
- `accept`  — adopt the pipeline's proposed mark.
- `modify`  — set your own mark via `--marks` (must be within `[0, max_marks]`).
- `escalate`— leave the marks unset (`None`); the paper stays flagged `Review`.

Every `resolve` writes an append-only line to `audit.jsonl`; the store lives
under `<queue_dir>` (`review_queue.json` + `audit.jsonl`, both written
atomically).

To **fill** the queue from a finished batch (the CLI has no `enqueue`
subcommand; the store is populated from Python):

```bash
python3 - <<'PY'
from aos_v0.exam.config import load_exam_configuration
from aos_v0.exam.batch import run_batch, BatchConfig
from aos_v0.exam.review import ReviewStore, enqueue_batch_results

exam = load_exam_configuration("examples/sample_exam.json").exam
report = run_batch(exam, "sheets/", config=BatchConfig(results_path="runs/results.jsonl"))
enqueue_batch_results(report, ReviewStore("runs/review_queue"), exam)
PY
```

### 3.8 `python -m aos_v0.exam.reporting` — CSV / JSON / reports / analytics

```bash
python3 -m aos_v0.exam.reporting <exam.json> <results.jsonl> [options]
```

`results.jsonl` is what `batch --results` produces.

| Option | Meaning |
|---|---|
| `--out DIR` | output directory (default `reports/`) |
| `--review-dir DIR` | overlay resolved Phase-11 review decisions (accepted/modified replace the proposal; unresolved stay `Review`) |
| `--json-analysis` | print the analytics payload as JSON (stdout) |

Writes into `--out`:

```
student_results.csv         Roll No,Student Name,Q1..Qn,Total,Percentage,Status
detailed_evaluation.csv     per-question agent marks/confidence/review flag
issues.csv                  reason, severity, resolution
class_analytics.txt         ASCII analytics dashboard
batch_report.txt            one line per student + class rates
reports/<roll>.txt          individual student reports (if student identity known)
results.json                full JSON export
```

```bash
python3 -m aos_v0.exam.reporting examples/sample_exam.json runs/results.jsonl \
    --out reports --review-dir runs/review_queue
```

### 3.9 `python -m aos_v0.exam.research` — benchmarking/human-vs-AI harness

```bash
python3 -m aos_v0.exam.research <exam.json> --out DIR [options]
```

| Option | Meaning |
|---|---|
| `--dataset DIR` | evaluate an existing `benchmark_dataset/` directory |
| `--sample-dataset DIR` | build a demo dataset (4 entries/question: full, partial, paraphrase, incorrect) into `DIR` |
| `--out DIR` | **required** – deliverables dir |
| `--throughput N` | additionally measure batch throughput over `N` synthetic papers |
| `--json` | also write `research_results.json` |

Writes into `--out`:

```
evaluation_results.csv   per-entry AI vs human comparison
BENCHMARK_REPORT.md      all metrics + experiments A–F
experiment_results/      a.txt … f.txt (one per experiment)
```

```bash
python3 -m aos_v0.exam.research examples/sample_exam.json \
    --sample-dataset benchmark_dataset --out research_out --throughput 50
```

### 3.10 `python -m aos_v0.exam.benchmark` — model-selection benchmark

```bash
python3 -m aos_v0.exam.benchmark [--root model-benchmark] [--registry {default,hf,exam}]
```

Runs the Phase-1 harness over case files under `<root>/<category>/`
(exact_answers, paraphrased_answers, partial_answers, incorrect_answers,
spelling_errors, …) and reports per-resource success/accuracy.

> **Known quirk:** `aos_v0.exam.benchmark` and `aos_v0.exam.config` are single
> modules whose names collide with the parent package import, so `python -m`
> prints a `RuntimeWarning` about `sys.modules`. It is harmless and the CLI
> still runs. (`reporting`, `review`, `research`, `batch`, … already use
> package layouts and are warning-free.)

### 3.11 Phase 14 hardening — security, health, provenance, perf, readiness

```bash
# validate an exam payload before trusting it (structural + path guards),
# and summarize the append-only access log
python3 -m aos_v0.exam.security validate examples/sample_exam.json
python3 -m aos_v0.exam.security logs runs/

# model/storage/review-queue healthcheck (exit 0 = healthy)
python3 -m aos_v0.exam.health --review-dir runs/review_queue --json

# reproducibility records: exam id/version, answer-key & rubric content
# versions, traced models, capability DNA, AOS config, timestamp + immutable
# fingerprint; --verify re-checks identity against today's exam config
python3 -m aos_v0.exam.provenance examples/sample_exam.json runs/results.jsonl \
    --out runs/provenance --verify

# graduated-scale performance probe (real batch driver) -> performance_results.csv
python3 -m aos_v0.exam.perf examples/sample_exam.json --out runs/perf --scales 10,50,100

# executable pre-deployment checklist (exit 0 = ready). Pin --exam-config so
# the immutability check re-derives provenance fingerprints from the same exam
python3 -m aos_v0.exam.readiness runs/results.jsonl \
    --exam-config examples/sample_exam.json --out runs/readiness
```

RBAC (`exam/security`) is a policy contract the API layer enforces; transport
auth, student data isolation and secure storage backends stay on the
deployment/API layer (spec Ph14).

---

## 4. End-to-end walkthrough (the happy path)

```bash
# 0. optional: sanity-check the example exam config
python3 -m aos_v0.exam.config examples/sample_exam.json

# 1. evaluate a folder of answer sheets (PNG/PDF/ZIP) — resumes safely, keeps
#    the results JSONL that reporting needs
python3 -m aos_v0.exam.batch examples/sample_exam.json sheets/ \
    --out runs/checkpoint --results runs/results.jsonl

# 2. see how it went
python3 -m aos_v0.exam.batch examples/sample_exam.json sheets/ --status

# 3. populate the human review queue from the batch results, then review
#    (Python snippet above; then)
python3 -m aos_v0.exam.review runs/review_queue list
python3 -m aos_v0.exam.review runs/review_queue resolve p42:Q1 \
    --decision modify --marks 3 --reviewer "A. Teacher" --note "partial credit for two of three concepts"

# 4. produce the course deliverables (CSVs, reports, analytics), overlaying
#    the review decisions made in step 3
python3 -m aos_v0.exam.reporting examples/sample_exam.json runs/results.jsonl \
    --out reports --review-dir runs/review_queue

# 5. (optional) benchmark the evaluator against a ground-truth dataset
python3 -m aos_v0.exam.research examples/sample_exam.json \
    --sample-dataset benchmark_dataset --out research_out --throughput 20
```

---

## 5. File outputs at a glance

| Command | Produces |
|---|---|
| `intake --write-pages DIR` | normalized PNGs |
| `ocr` | page/block text + confidence (JSON with `--json`) |
| `structure` | structured answer-sheet JSON |
| `evaluate --json` | plan-shaped evaluation JSON |
| `batch --out` | checkpoint dump (used for resume) |
| `batch --results` | `results.jsonl` (one `ExamRunResult` per paper) |
| `review` | `<queue_dir>/review_queue.json` + `<queue_dir>/audit.jsonl` |
| `reporting` | 3 CSVs, 2 txt reports, `results.json`, per-student `.txt` |
| `research` | `evaluation_results.csv`, `BENCHMARK_REPORT.md`, `experiment_results/*` |
| `provenance` | `provenance_<paper>.json` + `provenance_manifest.json` |
| `perf` | `performance_results.csv` |
| `readiness` | `READINESS.md` + `readiness.json` |
| `security logs` | `access.log.jsonl` (append-only) |
| `health` | health table / JSON (remote resource pool, local adapters) |

All stores are append-only or write-then-rename atomic so a crashed run never
loses already-recorded work.

---

## 6. Notes & known quirks

- **Golden rules are enforced end-to-end:** the pipeline never auto-zeroes and
  never invents a mark; a resolved final mark comes only from a review
  decision (`accept`/`modify`), and `escalate` deliberately leaves the mark
  empty so nothing is guessed.
- `reporting` reads only the recorded evidence in `results.jsonl`; it never
  re-runs an evaluation. Re-run `batch` (or repair the JSONL) to refresh it.
- `research` scoring is honest for the bundled **local structural engines**:
  paraphrase answers that do not reuse the key tokens score 0 and are flagged
  for review — that is the measured semantic gap, not a bug.
- `batch_throughput` synthesizes plain pages, so the measured papers mostly
  route to review; it is a driver-progress measurement, not an OCR accuracy
  claim.
- `perf` measures the same driver contract at scale (throughput, failure and
  recovery rates, queue latency). GPU/CPU/VRAM utilization is deployment
  telemetry and intentionally not reported by the kernel.
- `readiness` "versions immutable" re-derives provenance fingerprints; give it
  the exact exam config (or place an `*exam*.json` next to the results) or the
  check is skipped, not failed.
- The `aos` kernel CLI requires network/API access for non-local model
  resources; the exam pipeline works fully offline through the local adapters.