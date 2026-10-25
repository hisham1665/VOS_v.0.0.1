# AOS OCR integration

**Status:** implemented transport layer, with model quality still deployment- and
dataset-dependent.

This document describes how scanned answer-booklet OCR attaches to AOS. OCR is
an exam-domain capability; the AOS kernel remains responsible for routing,
bounded execution, failure handling, and audit events. The evaluator owns page
ingestion, transcription structure, confidence gating, and human-review policy.

## User-visible flow

```text
PDF/image/folder/ZIP
  -> exam intake + normalization
  -> Ollama OCR/VLM (configured profile)
  -> Hugging Face multimodal fallback
  -> declared specialised OCR slots
  -> local structural detector
  -> low-confidence human review
  -> layout + identity + question segmentation + evaluation
```

The remote adapters are lazy. Importing AOS or running the offline unit tests
does not create a client or call a model. Every remote failure is recorded in
the OCR fallback trace. A page that cannot be read is preserved as evidence and
marked for review; it is never converted into an automatic zero.

## Model profiles

The dedicated `models.ocr` profile in `aos.yaml` is separate from the general
`models.vision` profile:

```yaml
models:
  ocr:
    provider: ollama
    model: deepseek-ocr:3b
    api_key_env: ""
    base_url: http://localhost:11434/v1
    max_tokens: 4096
```

The default local recommendation is `deepseek-ocr:3b`, an OCR-focused model.
Other useful local candidates are `qwen2.5vl:7b` for broader visual reasoning
and `minicpm-v:8b` where that model is available. The adapter does not assume
that a model is installed: an unavailable model falls through to the next
provider and then to review.

For Hugging Face Inference Providers, set `HF_TOKEN` and optionally override:

```dotenv
AOS_HF_OCR_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
HF_PROVIDER=auto
```

`Qwen/Qwen2.5-VL-7B-Instruct` is the broadly compatible multimodal fallback.
Operators can evaluate OCR-focused `allenai/olmOCR-7B-0725` or
`stepfun-ai/GOT-OCR2_0` through the same adapter when the selected Inference
Provider exposes chat image input for that checkpoint. Availability must be
verified at deployment; the repository does not claim a model is installed or
that a remote provider has mapped every checkpoint.

Useful model references:

- <https://ollama.com/library/deepseek-ocr>
- <https://ollama.com/library/qwen2.5vl>
- <https://ollama.com/library/minicpm-v>
- <https://huggingface.co/allenai/olmOCR-7B-0725>
- <https://huggingface.co/stepfun-ai/GOT-OCR2_0>
- <https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct>

## Installation and runtime

For Ollama, install Ollama and pull the configured model:

```text
ollama pull deepseek-ocr:3b
```

Start the Ollama service before running the model-backed command. The endpoint
is normally `http://localhost:11434/v1`. The repository's `ModelGateway` sends
the page as a base64 image content part and selects the `ocr` profile without
changing the general AOS `vision` profile.

For Hugging Face, copy `.env.example` to `.env`, set `HF_TOKEN`, and ensure the
token can call Inference Providers. Tokens are never placed in YAML, OCR JSON,
fallback traces, or review records.

## Commands

Run the OCR CLI with remote providers enabled (the default CLI behavior):

```text
python -m aos_v0.exam.ocr path/to/booklet.pdf --json
```

Use `--strategy printed` or `--strategy handwritten` when the paper is known.
Use `--offline` to exercise only declared/local fallback behavior without any
network or model-server call:

```text
python -m aos_v0.exam.ocr path/to/booklet.pdf --offline --json
```

The full evaluator opts into the same remote chain through
`LocalExamRunner.ensure_ocr`. Direct library calls to `run_document` remain
offline by default for reproducibility; pass
`default_adapters("auto", remote=True)` when building a model-backed library
flow.

## Structured response contract

Remote models receive a prompt requiring JSON in this shape:

```json
{
  "blocks": [
    {
      "type": "answer",
      "text": "TCP uses acknowledgements...",
      "bbox": [120, 480, 1800, 560],
      "confidence": 0.88,
      "transcribed": true
    }
  ],
  "page_confidence": 0.88
}
```

Supported block types include `header`, `question_label`, `answer`, `text`,
`table`, `diagram`, `figure`, and `formula`. The parser accepts pixel bboxes and
normalized `[0,1]` bboxes, preserves equations and diagram descriptions, and
handles fenced JSON. A non-JSON model response is retained as a full-page block
with confidence `0.55`, below the normal acceptance threshold, so it remains
reviewable without being treated as verified transcription.

The OCR schema is in `src/aos_v0/exam/ocr/models.py`. The confidence reducer in
`confidence.py` is deliberately conservative: a model's confidence is a
readability signal, not proof of correctness. `OcrStatus.REVIEW` and
`EvalReviewReason.LOW_OCR_CONFIDENCE` are the expected outcomes for unreadable
or ambiguous pages.

## AOS seams

The feature uses existing AOS seams rather than adding model logic to the
kernel:

| Concern | Implementation |
|---|---|
| Model profile | `settings.py` + `aos.yaml` `models.ocr` |
| Provider transport | `providers/gateway.py` for Ollama; existing `providers/hf.py` for HF |
| Domain adapter | `exam/ocr/adapters.py` |
| Fallback/review | `exam/ocr/pipeline.py` + `OcrSettings` |
| Full evaluator wiring | `exam/orchestrator.py::LocalExamRunner.ensure_ocr` |
| Evidence | `OcrResult.fallback_trace`, page blocks, and review queue |

No capability vocabulary or scheduler behavior was changed for this transport
addition. The adapter boundary remains `(image, hints) -> List[OcrBlock]`, and
the AOS model gateway remains lazy and provider-neutral.

## Verification and limits

The automated tests use injected/fake responses to verify image transport,
structured parsing, block typing, coordinate normalization, and fallback
behavior without requiring credentials or a model download. A real deployment
still needs a representative booklet benchmark containing printed text,
handwriting, engineering equations, tables, diagrams, blur, rotation, and
blank pages. Recognition quality, latency, and GPU/RAM requirements must be
measured on that dataset before production marking is enabled.

If Ollama and Hugging Face are both unavailable, the local structural adapter
can still detect ink bands and page geometry. It intentionally emits empty text
with low confidence; this is a valid review artifact, not an OCR success.
