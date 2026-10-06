# Models

Hosted/transformers models use Hugging Face Hub ids; local servers use an explicit
model alias and URL. All are overridable by environment variable.

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

Different model families offer useful corroboration and visible disagreements.
Agreement alone does not establish a finding as true: both can share distribution
bias or fail on unsupported anatomy/acquisitions. Clinical verification remains essential.

## Gated models

`google/medgemma-4b-it` requires accepting its licence on the Hub. Without
that, the hosted call fails and `hf_backend.remote_vlm` automatically falls
back to `MAOS_REPORT_VLM_FALLBACK`. The report's provenance block records which
model actually served each capability, so you always know what produced a read.

To use MedGemma:

1. Visit <https://huggingface.co/google/medgemma-4b-it> and accept the terms.
2. Put a token with read access in `.env` as `HF_TOKEN`.
3. `python main.py --doctor` to confirm.

## Local vision model below 8B: RTX 2050 / 4GB

**Recommended medical model: MedGemma 1.5 4B, Q4_K_M, with its matching F16 vision
projector**, served using a recent CUDA-enabled llama.cpp build. The laptop has
4GB VRAM and 15.7GiB Windows RAM; WSL currently sees only 7.6GiB RAM. Full-precision
transformers loading is unsuitable for that GPU.

Published artifacts verified on 2026-10-05:

| File in `unsloth/medgemma-1.5-4b-it-GGUF` | Bytes | Approximate size |
|---|---:|---:|
| `medgemma-1.5-4b-it-Q4_K_M.gguf` | 2,489,894,976 | 2.49GB / 2.32GiB |
| `mmproj-F16.gguf` | 851,252,224 | 0.85GB / 0.79GiB |

Both files are needed for images. The quantized package identifies
`google/medgemma-1.5-4b-it` as its base, architecture Gemma 3, and
`image-text-to-text` as its task. This is a community quantization of Google's
medical multimodal model. Review the base model's Health AI Developer Foundations
terms. File sizes are not peak RAM/VRAM: KV cache, image encoder buffers, operating
system and other models add memory. Published compatibility was checked; actual
model loading, image inference, latency and clinical accuracy on this laptop
remain to be measured after installation.

### Download and serve (Windows PowerShell)

Install a recent [llama.cpp Windows CUDA release](https://github.com/ggml-org/llama.cpp/releases)
and Hugging Face's `hf` CLI in your Python environment. Download outside the repo:

```powershell
hf download unsloth/medgemma-1.5-4b-it-GGUF medgemma-1.5-4b-it-Q4_K_M.gguf mmproj-F16.gguf --revision 3855f948626b7ae42bccd082757f15078c53e758 --local-dir "$env:USERPROFILE\models\medgemma-1.5-4b"
```

From the directory containing the llama.cpp binaries:

```powershell
.\llama-server.exe -m "$env:USERPROFILE\models\medgemma-1.5-4b\medgemma-1.5-4b-it-Q4_K_M.gguf" --mmproj "$env:USERPROFILE\models\medgemma-1.5-4b\mmproj-F16.gguf" --alias medgemma-1.5-4b --host 127.0.0.1 --port 8080 -c 4096 -ngl 20 --no-mmproj-offload --parallel 1 -b 128 -ub 128
```

This conservative starting point puts the vision projector on CPU and offloads
20 language-model layers. Tune `-ngl` against actual VRAM use; reduce it if needed.
Keep one context and a 4096-token window, rather than allocating the advertised
128K model context. CPU offload trades speed for memory headroom.

Set these values in Medical AOS `.env`:

```dotenv
MAOS_VLM_BACKEND=openai
MAOS_VLM_URL=http://localhost:8080/v1
MAOS_VLM_MODEL=medgemma-1.5-4b
MAOS_MAX_WORKERS=2
MAOS_MAX_GPU_WORKERS=1
MAOS_MAX_RESIDENT_MODELS=1
MAOS_DEVICE=cpu
MAOS_CALL_TIMEOUT=600
```

`MAOS_DEVICE=cpu` keeps the application's small transformers classifiers off the
GPU occupied by the separate CUDA server. The server's own `-ngl` still controls
its GPU use. Ensure the server URL is reachable from whichever Windows/WSL
environment runs Medical AOS; localhost behavior depends on WSL networking mode.
Keep text-model configuration/credentials for document reasoning and reporting.

Run `python main.py --doctor`, then a known image case with `--no-literature`.
The `/v1/models` check proves only that the alias is listed; the first image read
must verify that the projector is loaded and pixels are actually accepted.
Per-run provenance records the serving model, image/frame references and coverage.

### Simple Ollama baseline

The verified official **`gemma3:4b`** tag is vision-capable (4.3B parameters,
Q4_K_M, 3.3GB download, Ollama 0.6+). It is a general-purpose comparison baseline,
not the medical-specialist recommendation:

```powershell
ollama pull gemma3:4b
```

```dotenv
MAOS_VLM_BACKEND=ollama
MAOS_OLLAMA_MODEL=gemma3:4b
```

Ollama availability expires after 5s negative/30s positive. `--doctor` inspects
`/api/show` for the configured model's `vision` capability. Do not assume a tag
containing “medgemma” accepts images: the checked `alibayram/medgemma:4b` listing
advertises text-only support. Arbitrary custom tags need their own verification.

**Local transformers** remains an option on larger GPUs (`MAOS_VLM_BACKEND=local`,
automatic local VLM routing requires ≥12GB VRAM).

Sources: [official MedGemma 1.5](https://huggingface.co/google/medgemma-1.5-4b-it),
[quantized files](https://huggingface.co/unsloth/medgemma-1.5-4b-it-GGUF/tree/main),
[llama.cpp image API/projector support](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md),
[Ollama Gemma 3 4B](https://ollama.com/library/gemma3:4b).

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
