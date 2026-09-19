# MODEL_SELECTION.md — Phase 1 Model Survey and Selection

**Phase:** 1 · **Project:** AOS Exam Evaluator · **Status:** initial survey (to be validated by `MODEL_BENCHMARK.md`)

## Purpose

Identify suitable Hugging Face models for every capability the exam evaluator
requires, so AOS's capability-driven dynamic model selection starts from a
grounded candidate set. Selection is **capability-driven**: a model is chosen
because it can satisfy the Capability DNA of the tasks that use it, not because
it is popular.

This document is the human-readable survey. The machine-readable mirror is
`src/aos_v0/exam/model_selection.py` (the *initial model registry*), which
drives the Capability Registry entries in `src/aos_v0/exam/resources.py`.

## Method and sources

- Candidate facts were gathered by web research on **2026-09-18** (OCR/PP-OCRv6,
  LayoutLMv3/Docling, InternVL 2.5, Qwen2.5-VL, BGE-M3 / Multilingual-E5,
  2026 open-LLM landscape, TrOCR handwriting, DeepSeek-Math / Qwen2.5-Math /
  R1-distill math roundups).
- **Provenance discipline**: every model identity below is either confirmed by
  that research or explicitly marked *verify repo*. VRAM/RAM/latency figures are
  **estimates for planning only** — they are labelled in each record and must be
  replaced by the benchmark's measured telemetry before they feed scoring.
- Licences are best-effort. **Two flags need decision before production:**
  `microsoft/layoutlmv3-base` (CC BY-NC-SA 4.0, research-only) and
  `meta-llama/Llama-3.3-70B-Instruct` (Llama 3.3 Community Licence terms).

## Capability coverage (13 required capabilities)

| Plan capability | Flag(s) | Primary model | Fallback(s) |
|---|---|---|---|
| DOCUMENT_OCR | `document.ocr` | PP-OCRv6 (PaddleOCR v3.7.0) | TrOCR base printed |
| HANDWRITING_OCR | `handwriting.ocr` | TrOCR base handwritten | TrOCR large handwritten → Qwen2.5-VL-7B |
| DOCUMENT_LAYOUT | `document.layout` | LayoutLMv3 base ⚠ NC | Qwen2.5-VL-7B (ad-hoc regions) |
| VISION_UNDERSTANDING | `vision.understanding` | Qwen2.5-VL-7B-Instruct | InternVL 2.5 8B |
| SEMANTIC_EMBEDDING | `semantic.embedding` | BGE-M3 | Multilingual-E5-large |
| SEMANTIC_EVALUATION | `semantic.answer_evaluation` | Qwen3-30B-A3B-Instruct | Qwen3-32B |
| GENERAL_REASONING | `reasoning.deep` | Qwen3-30B-A3B-Instruct | Llama-3.3-70B-Instruct ⚠ license |
| MATHEMATICAL_REASONING | `mathematical.evaluation` | Qwen2.5-Math-7B-Instruct | R1-Distill-Qwen-7B, DeepSeekMath-RL-7B |
| DIAGRAM_UNDERSTANDING | `diagram.evaluation` | Qwen2.5-VL-7B-Instruct | InternVL 2.5 8B |
| MULTILINGUAL_UNDERSTANDING | `text.normalization` | BGE-M3 + Qwen3-30B-A3B | Multilingual-E5-large |
| STUDENT_ID_EXTRACTION | `student_id.extraction` | Qwen2.5-VL (header reading) | PP-OCRv6 + Qwen3-30B-A3B |
| QUESTION_SEGMENTATION | `question.segmentation` | Qwen3-30B-A3B (mapping) | Qwen2.5-VL-7B |
| EVALUATION_RECONCILIATION | `evaluation.reconciliation` | R1-Distill-Qwen-32B | Qwen3-30B-A3B |

⚠ = licence must be resolved for production (see per-capability cards).

## Candidate records (plan field list)

Each record follows the implementation plan's "Candidate Model Information":
Model Name, HF Repository, Architecture, Parameter Count, Modality, Input
Format, Output Format, Context Length, Language Support, OCR/Handwriting/Vision/
Reasoning Capability, Quantization Options, VRAM, RAM, CPU Feasibility,
Inference Framework, License, Expected Latency, Known Limitations, Fallback
Candidates.

### DOCUMENT_OCR — `document.ocr`

**Primary — PP-OCRv6 (PaddleOCR v3.7.0)** · `PaddlePaddle/PaddleOCR` · Apache-2.0
- Architecture: PP-OCRv6 detection + recognition pipeline. Params: ~15–35M/stage.
- Modality: image → text + bbox + confidence. Context: page-level (chunked).
- Languages: 100+ (multilingual). OCR: SOTA-class published accuracy (PaddleOCR-VL
  96.33% OmniDocBench v1.6, 2026).
- Quant: int8/fp16. VRAM 1–4 GB. RAM 4–8 GB. **CPU-feasible: yes.** Framework: PaddlePaddle.
- Expected latency: 100–500 ms/page CPU, faster on GPU.
- Limitations: deployed via `paddleocr` pip / Paddle deployment, not a Transformers
  pipeline; the HF entry is the repository, not a serverless model id.
- **Fallbacks:** TrOCR base printed.

**Fallback — TrOCR base printed** · `microsoft/trocr-base-printed` · MIT
- ViT encoder + RoBERTa decoder, 334M; line-crop OCR; strong on clean printed
  lines, needs a detection/layout stage, English-centric.

### HANDWRITING_OCR — `handwriting.ocr`

**Primary — TrOCR base handwritten** · `microsoft/trocr-base-handwritten` · MIT
- ViT + RoBERTa decoder, 334M; line-level handwriting (IAM baseline); fp16 <1 GB
  VRAM; **CPU feasible**; 100–400 ms/line on GPU.
- Limitations: line crops only, cursive/marginal scans degrade; needs layout to
  locate handwriting regions.

**Fallback — TrOCR large handwritten** (`microsoft/trocr-large-handwritten`, MIT,
412M) → then **fallback 2 — Qwen2.5-VL-7B-Instruct** (`Qwen/Qwen2.5-VL-7B-Instruct`,
Apache-2.0, VLM whole-page transcription). This is the Phase-4 "vision fallback"
step: dedicated handwriting OCR → VLM → human review, never automatic zero.

### DOCUMENT_LAYOUT — `document.layout`

**Primary — LayoutLMv3 base** · `microsoft/layoutlmv3-base` · ⚠ **CC BY-NC-SA 4.0 (research-only)**
- Multimodal Transformer (text + layout + image), 133M; <2 GB VRAM; **CPU feasible**.
- Needs OCR text+bbox as input → sits after the OCR stage, labels question/answer
  regions, tables, diagrams.
- Limitations: non-commercial licence (must resolve before production).
- **Fallbacks:** Qwen2.5-VL-7B (prompted full-page region analysis); PP-Structure via PaddleOCR.

### VISION_UNDERSTANDING — `vision.understanding`

**Primary — Qwen2.5-VL-7B-Instruct** · `Qwen/Qwen2.5-VL-7B-Instruct` · Apache-2.0
- Qwen2.5 LLM + ViT, 7B, 32K context, multilingual; int4 ~8 GB VRAM (fp16 ~16 GB);
  GPU required; 1–3 s/page.
- Already registered in the AOS HF model catalog.
- **Fallback — InternVL 2.5 8B** · `OpenGVLab/InternVL2_5-8B` · MIT — GPT-4o/
  Claude-3.5-competitive document/image reasoning.

### SEMANTIC_EMBEDDING — `semantic.embedding`

**Primary — BGE-M3** · `BAAI/bge-m3` · MIT
- 568M; dense+sparse+ColBERT; 1024-dim; 8192-token context; 100+ languages;
  int8 ~1–2 GB VRAM; **CPU feasible**; 5–50 ms/sentence on CPU.
- Rationale: local bi-encoder beats proprietary Google Embeddings 2 for latency
  (GE2 measured ~14× slower); retrieval supplies concept evidence to the
  semantic evaluator.
- **Fallback — Multilingual E5 large** · `intfloat/multilingual-e5-large` · MIT ·
  560M, 1024-dim, 512-token cap, 100 languages.

### SEMANTIC_EVALUATION — `semantic.answer_evaluation`

**Primary — Qwen3-30B-A3B-Instruct** · `Qwen/Qwen3-30B-A3B-Instruct` · Apache-2.0
- MoE ~30B total / ~3.3B active; 40,960 context; 100+ languages; tool calling;
  int4 ~18 GB VRAM; GPU required; 1–4 s/answer.
- Evaluates **meaning and concepts** vs the answer key, never exact wording;
  must emit marks, satisfied/missing concepts, reasoning, confidence (Phase 6/7).
- **Fallback — Qwen3-32B** · `Qwen/Qwen3-32B` · Apache-2.0 — higher-capacity dense
  chat model for hard cases.

### GENERAL_REASONING — `reasoning.deep`

**Primary — Qwen3-30B-A3B-Instruct** (as above).
**Fallback — Llama-3.3-70B-Instruct** · `meta-llama/Llama-3.3-70B-Instruct` ·
⚠ Llama 3.3 Community Licence; 70B, 128K context, int4 ~40 GB VRAM, already in catalog.

### MATHEMATICAL_REASONING — `mathematical.evaluation`

**Primary — Qwen2.5-Math-7B-Instruct** · `Qwen/Qwen2.5-Math-7B-Instruct` · Apache-2.0
- 7B dense, math-specialised; int4 ~7 GB (fp16 ~15 GB); **CPU feasible (slow)**;
  0.5–3 s/problem. Strong GSM/MATH500 for size (2026 evaluation roundups).
- Supports step-marking: formula → substitution → calculation → units → final
  answer; a wrong final value must not erase correct intermediate work.
- **Fallbacks:** R1-Distill-Qwen-7B (`deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`,
  MIT, MATH-500 74.6) → DeepSeekMath-RL-7B (`deepseek-ai/DeepSeekMath-RL-7B`,
  MIT, GSM8K 88.2 / MATH 51.7).

### DIAGRAM_UNDERSTANDING — `diagram.evaluation`

**Primary — Qwen2.5-VL-7B-Instruct** (as above) reads figure + question together.
**Fallback — InternVL 2.5 8B** (as above).

### MULTILINGUAL_UNDERSTANDING — `text.normalization`

**Primary — BGE-M3 (multilingual evidence) + Qwen3-30B-A3B (reasoning)**.
Multilingual semantic retrieval feeds a multilingual reasoning LLM; evaluated on
meaning in-language, matching the Phase-6 rule "support multilingual responses
when configured". **Fallback — Multilingual E5 large.**

### STUDENT_ID_EXTRACTION — `student_id.extraction`

**Primary — Qwen2.5-VL-7B-Instruct (header reading)** — reads printed/handwritten
header fields in context; outputs name/roll/register JSON.
**Fallback — PP-OCRv6 + Qwen3-30B-A3B** — dedicated OCR for digits/labels + LLM
normalisation and roster-anchored validation (Phase 5 identity validation).
Identity uncertainty → `IDENTITY_UNCERTAIN` review reason, never a silent guess.

### QUESTION_SEGMENTATION — `question.segmentation`

**Primary — Qwen3-30B-A3B-Instruct (mapping over structured OCR)** — maps
layout-labelled OCR blocks to question ids, handles out-of-order and
continuation answers, merges multi-page answers.
**Fallback — Qwen2.5-VL-7B-Instruct** (page-level mapping when layout labels unreliable).

### EVALUATION_RECONCILIATION — `evaluation.reconciliation`

**Primary — R1-Distill-Qwen-32B** · `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B` · MIT
- 32B dense; documented reasoning strength (AIME 72.6 / MATH-500 90); int4 ~20 GB
  VRAM; GPU required; 2–8 s/decision. Self-checking arbitration of two
  independent agents (Phase 7/9).
- **Fallback — Qwen3-30B-A3B-Instruct** (fast instruction-grade arbitration).

## Selection constraints (summary)

| Capability | min_quality | max_latency_ms | max_cost_usd | min_context |
|---|---|---|---|---|
| DOCUMENT_OCR | 0.90 | 60 000 | 0.05 | 512 |
| HANDWRITING_OCR | 0.80 | 90 000 | 0.05 | 512 |
| DOCUMENT_LAYOUT | 0.80 | 30 000 | 0.05 | 512 |
| VISION_UNDERSTANDING | 0.85 | 30 000 | 0.05 | 4096 |
| SEMANTIC_EMBEDDING | 0.80 | 5 000 | 0.01 | 256 |
| SEMANTIC_EVALUATION | 0.90 | 60 000 | 0.05 | 16 000 |
| GENERAL_REASONING | 0.85 | 60 000 | 0.05 | 16 000 |
| MATHEMATICAL_REASONING | 0.85 | 90 000 | 0.05 | 4096 |
| DIAGRAM_UNDERSTANDING | 0.85 | 60 000 | 0.05 | 8192 |
| MULTILINGUAL_UNDERSTANDING | 0.80 | 20 000 | 0.05 | 4096 |
| STUDENT_ID_EXTRACTION | 0.90 | 30 000 | 0.05 | 4096 |
| QUESTION_SEGMENTATION | 0.85 | 45 000 | 0.05 | 16 000 |
| EVALUATION_RECONCILIATION | 0.90 | 90 000 | 0.05 | 16 000 |

These are captured in `model_selection.py::SelectionConstraints` and mirrored
into each capability's `CapabilityDNA` (`dna_for_capability`). They are
**starting gates for the benchmark**, not measurements.

## Fallback chains (Phase 4 / Phase 9 recovery)

- OCR: PP-OCRv6 → TrOCR printed → Qwen2.5-VL → human review (never zero).
- Handwriting: TrOCR base → TrOCR large → Qwen2.5-VL → human review.
- Evaluation: Qwen3-30B-A3B → Qwen3-32B (agent escalation on low confidence).
- Reconciliation: R1-Distill-32B → Qwen3-30B-A3B → human review on disagreement.
- Routing-level fallback is AOS's existing `FailureManager` recovery ladder
  (retry → feedback reformulation → scored runner-up substitution).

## Provenance / verification notes

1. **Everything estimated must be measured** in `MODEL_BENCHMARK.md`: VRAM, RAM,
   latency, quality priors, failure rates.
2. **Repo checks**: marks *verify repo* — `Qwen/Qwen3-32B`, `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`,
   `deepseek-ai/DeepSeekMath-RL-7B`, Qwen2.5-family context-length defaults.
   Confirm exact ids on huggingface.co before wiring (Phase 3/4).
3. **Licences to resolve**: LayoutLMv3 (CC BY-NC-SA 4.0) and Llama-3.3
   Community Licence. Alternatives if rejected: Qwen2.5-VL for layout, and
   Qwen3-30B-A3B for general reasoning.
4. **Deployment note**: PaddleOCR routes through PaddlePaddle, not Transformers;
   the exam resource manifests record `PaddlePaddle/PaddleOCR` as the source repo.
5. Execution transports are **not wired in Phase 1** — the registry entries are
   declared interfaces (`transport=declared`), consistent with the HF stub
   pattern. Wiring is Phase 3/4 (integration-spec gap G3).