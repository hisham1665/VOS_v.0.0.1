# MODEL_BENCHMARK.md — Phase 1 Benchmark Specification

**Phase:** 1 · **Project:** AOS Exam Evaluator · **Status:** harness + dataset skeleton (execution pending tokens/GPU)

## Objective

Validate the Phase-1 model selection (`MODEL_SELECTION.md` /
`src/aos_v0/exam/model_selection.py`) with measured evidence. Every estimated
figure in the survey — quality, latency, VRAM/RAM, failure rate — must be
replaced by telemetry from this benchmark before it feeds the Capability
Registry's scoring constants. The benchmark is *Model Selection Quality* input
for the Phase-13 research evaluation.

## Dataset layout

The benchmark dataset lives in `model-benchmark/`, one category per directory
per the implementation plan:

```text
model-benchmark/
├── exact_answers/          # answers that must be accepted at full marks
├── paraphrased_answers/    # valid paraphrases that must be accepted
├── partial_answers/        # partial credit cases (step marking)
├── incorrect_answers/      # answers that must be rejected
├── spelling_errors/        # spelling vs conceptual-error discrimination
├── handwritten/            # line/page handwriting scans (OCR quality)
├── mathematical/           # numerical/derivation cases (step marking)
├── diagrams/               # diagram/table questions (VLM evaluation)
├── multilingual/           # non-English answers (when multilingual configured)
└── difficult_documents/    # rotated, skewed, low-light, cropped, blank scans
```

Each category is a directory of case files. The skeleton is committed with
`.gitkeep`; cases are added as data (see next section).

## Case format

One JSON file per case (or a list of cases per file), e.g.:

```json
{
  "resource_id": "semantic_answer_evaluation",
  "task": "semantic.answer_evaluation",
  "input_text": "TCP creates a connection between endpoints and makes sure data is delivered reliably.",
  "instruction": "Evaluate against the answer key for Q1. Key: transport layer; connection-oriented; reliable communication. Emit marks, concepts_satisfied, missing_concepts, reasoning, confidence.",
  "expected": "connection-oriented"
}
```

Fields:
- `resource_id` — the registry resource the case targets (e.g. `document_ocr`,
  `semantic_answer_evaluation`, `evaluation_reconciliation`).
- `input_text` — the input: a text answer, or a path/artifact id for image cases
  (`handwritten/`, `diagrams/`, `difficult_documents/`).
- `instruction` — the node-style instruction (becomes the model prompt).
- `expected` — the reference for an exact/containment check; **not** a semantic
  score. Semantic scoring belongs to the Phase-6 evaluator and Phase-13
  human-vs-AI comparison, not this layer.

## Harness

`src/aos_v0/exam/benchmark.py` reuses the kernel's provider-agnostic evaluation
layer (`aos_v0.services.evaluation`), so every case runs through the *same* code
path (`run_case_on` → `normalize_result` → `summarize`) whatever provider backs
the resource — exactly the monitoring contract DOC1 M3 asks for.

Run:

```bash
# default (Groq/Ollama) pool
python3 -m aos_v0.exam.benchmark --root model-benchmark --registry default

# default + HF catalog (requires valid HF_TOKEN)
python3 -m aos_v0.exam.benchmark --root model-benchmark --registry hf

# default + declared exam resources (cases against document_ocr, ... )
python3 -m aos_v0.exam.benchmark --root model-benchmark --registry exam
```

Per-case output is a normalized `ExecutionMetrics` record (success, provider,
model, `latency_ms`, token counts when the provider exposes them, error
category, text). The CLI prints the per-resource rollup from `summarize`
(count, success rate, mean latency, mean token counts, error categories).

## Metrics measured

Per the Phase-1 metrics list, each with the above harness:

- **OCR accuracy** — `handwritten/`, `exact_answers/` with image cases;
  containment/edit-distance against `expected`.
- **Semantic similarity** — paraphrased/evaluated answers against the key.
- **Concept extraction accuracy** — `semantic.answer_evaluation` output vs the
  rubric's required concepts.
- **Evaluation agreement** — same case run on both `semantic_answer_evaluation`
  and `answer_verification`; compared by the reconciliation case.
- **Latency** — measured on every execution (`latency_ms`), broken out
  per resource/model.
- **VRAM / RAM usage** — measured externally (nvidia-smi / psutil during runs);
  recorded per model in the results, not asserted in advance.
- **Failure rate** — `1 - success_rate` plus the per-resource `error_categories`.

## What the results update

1. **Capability Registry manifests** — replace the architecture-default
   `LatencyModel` / `CostModel` / `Availability` and neutral `quality_priors`
   with measured values (the HF catalog already documents this path).
2. **Resource profiles and constraints** — update the estimates in
   `model_selection.py::ResourceProfile` and confirm (or relax) the
   `SelectionConstraints` table in `MODEL_SELECTION.md`.
3. **Fallback selection** — validate that the declared fallback chains beat the
   primary on the dimension they were chosen for (e.g. handwriting: VLM fallback
   only when TrOCR accuracy is below threshold).

## Execution prerequisites (currently blocked)

- **HF token**: no `HF_TOKEN`/`HUG` is set in this environment, so
  `build_hf_enabled_registry()` skips HF resources (registry is fail-safe to the
  default pool). Set a valid token to benchmark HF models.
- **GPU**: VRAM estimates assume GPU inference; CPU runs are possible for the
  `cpu_feasible=True` models but do not validate the planned latencies.
- **PaddleOCR / Transformers / vLLM transports** are Phase 3/4 wiring (gap G3);
  until then the exam registry entries are declared interfaces and will raise a
  typed error if invoked. The `--registry exam` option exists to prove the
  harness path against the declared resources, not to run real inference.

## Recommended experiment order

1. Embedding: BGE-M3 vs Multilingual-E5 on `paraphrased_answers/` (latency +
   retrieval quality) — no GPU needed.
2. Handwriting OCR: TrOCR base vs large on `handwritten/` (accuracy + latency).
3. Math: Qwen2.5-Math-7B vs R1-Distill-7B on `mathematical/` (step-marking).
4. Semantic evaluation: Qwen3-30B-A3B vs Qwen3-32B on `paraphrased_answers/`
   + `incorrect_answers/` (accept/reject discrimination).
5. Reconciliation: R1-Distill-32B vs Qwen3-30B-A3B on paired agent outputs.
6. Diagrams/difficult documents: VLM path on `diagrams/` + `difficult_documents/`.

Results are collated into the Phase-13 `BENCHMARK_REPORT.md` /
`evaluation_results.csv`.