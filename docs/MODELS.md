# Models

Every model is a Hugging Face Hub id, set in `config.py` and overridable by
environment variable. Swapping a checkpoint never requires a code change.

## The roster

| Role | Env var | Default | Size | Where it runs |
|---|---|---|---|---|
| Report VLM | `MAOS_REPORT_VLM` | `google/medgemma-4b-it` | ~8GB fp16 | Hosted (or local ≥12GB VRAM) |
| VLM fallback | `MAOS_REPORT_VLM_FALLBACK` | `Qwen/Qwen2.5-VL-7B-Instruct` | ~15GB fp16 | Hosted |
| Modality/region triage | `MAOS_MODALITY_CLIP` | `flaviagiammarino/pubmed-clip-vit-base-patch32` | ~600MB | Local |
| Non-medical gate | `MAOS_NONMEDICAL_CLIP` | `openai/clip-vit-base-patch32` | ~600MB | Local |
| Screening cascade | `MAOS_MODEL_ZOO` (JSON) | 12 checkpoints incl. TorchXRayVision, see `capabilities/model_zoo.py` | 0.1-0.4GB each | Local |
| Text summariser | `MAOS_SUMMARIZER` | `facebook/bart-large-cnn` | ~1.6GB | Hosted fallback |
| Text LLM | `MAOS_TEXT_LLM` | `openai/gpt-oss-120b` (Groq) | — | Hosted |

### Verified on this machine

Both local models were downloaded and run against a real image on an RTX 2050
(4GB) during development:

```
loading lxyuan/vit-xray-pneumonia-classification on cuda ... 23.96s first load
classifying 1 image(s) took 0.58s
→ PNEUMONIA 53%

loading openai/clip-vit-base-patch32 on cuda ... 22.16s first load
CLIP triage of 1 image(s) took 0.66s
→ "does not appear to be a medical image" (correctly — the test file is a photo)
```

First load includes the download. Subsequent loads from the HF cache: ~3.4s.

## Why this split

`google/medgemma-4b-it` is Google's medical multimodal model and is the right
default for the narrative read — but 4B parameters in fp16 needs roughly 8GB,
which does not fit on a 4GB laptop card alongside anything else. So the VLM
goes hosted by default and the small models stay local, where they cost a
sub-second GPU pass instead of a network round trip.

A finding is only trustworthy when two *different kinds* of model agree on it.
The classifier cannot describe anything, and the VLM cannot be calibrated —
running both and reporting the disagreement is the point, not redundancy.

## Gated models

`google/medgemma-4b-it` requires accepting its licence on the Hub. Without
that, the hosted call fails and `hf_backend.remote_vlm` automatically falls
back to `MAOS_REPORT_VLM_FALLBACK`. The report's provenance block records which
model actually served each capability, so you always know what produced a read.

To use MedGemma:

1. Visit <https://huggingface.co/google/medgemma-4b-it> and accept the terms.
2. Put a token with read access in `.env` as `HF_TOKEN`.
3. `python main.py --doctor` to confirm.

## Running the VLM locally instead

Two routes avoid sending images to a hosted endpoint — which matters for real
patient data.

**Ollama** (preferred; auto-detected when reachable):

```bash
ollama pull medgemma
MAOS_VLM_BACKEND=ollama
MAOS_OLLAMA_MODEL=medgemma:latest
```

The pipeline probes `localhost:11434` once per process and confirms the named
model is actually pulled before choosing this route.

**Local transformers weights** (needs ≥12GB VRAM):

```bash
MAOS_VLM_BACKEND=local
```

## Screening models

The single pneumonia classifier was replaced by a routed, two-stage cascade.
Roster, benchmark numbers, disabled models and failure modes:
[SCREENING_CASCADE.md](SCREENING_CASCADE.md).

To add, replace or disable a model without code changes, point
`MAOS_MODEL_ZOO` at a JSON list of entries using the `ZooModel` fields:

```json
[
  {"key": "cxr_pneumothorax", "enabled": false},
  {"key": "us_breast", "model_id": "org/breast-us-vit", "role": "screener",
   "modalities": ["Ultrasound"], "regions": ["breast"], "targets": ["mass"],
   "label_map": {"malignant": "mass", "benign": "normal", "normal": "normal"}}
]
```

`MAOS_CXR_CLASSIFIER` still works and replaces only the chest X-ray screener.

## Text backend

Planning, consolidation and report synthesis use Groq by default — it is fast,
and these calls are on the critical path. Without `GROQ_API_KEY`, `chat_text`
falls back to the HF Inference API automatically. With neither, the run fails
at preflight with a message naming both options rather than at an arbitrary
point mid-study.

## Adding a model

1. Add the field to `ModelConfig` in `config.py` with its env override.
2. Call `load_pipeline(task, MODELS.your_model)` for local, or
   `inference_client(MODELS.your_model)` for hosted, inside a capability.
3. Call `record_model("your_capability", model_id)` so it appears in report
   provenance.
4. Give `prefer_local` an honest `size_hint_gb` so routing works on small GPUs.
