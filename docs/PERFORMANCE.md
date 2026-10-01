# Performance and tuning

## Where the time goes

For a 6-image chest study with the default configuration:

| Stage | Cost | Notes |
|---|---|---|
| Ingest | ~0.1s/image | Parallel decode; a cache hit is ~0.01s |
| Triage (CLIP) | ~0.6s total | Batched, one local GPU pass |
| Screen (classifier) | ~0.6s total | Batched, one local GPU pass |
| Read (VLM) | 3–15s | **Dominant.** 6 calls, but concurrent |
| Consolidate | 2–5s | One text LLM call |
| Literature | 2–6s | Search plus one text LLM call |
| Report | 2–5s | One text LLM call |

The VLM reads dominate, which is why they run in parallel and are cached.

## The optimisations, and what each is worth

### 1. Preprocess once, cache by content hash

The largest avoidable cost in the original code. Source images went to the
model verbatim.

Measured on this repository's `image.jpg` (5000×3333):

```
6722KB → 279KB   (24× smaller payload)
0.48s  → 0.011s  (43× faster on the second call)
```

Base64 inflates payloads by ~33%, so 6.7MB became ~9MB on the wire — per
request, for a model that resizes to ~896px internally regardless.

### 2. One bounded pool, not one per wave

The original constructed `ThreadPoolExecutor(max_workers=len(wave))` per wave.
A 40-image study made 40 threads, each loading its own model weights; an empty
wave crashed outright on `max_workers=0`.

Now: one pool for the run, capped by `MAOS_MAX_WORKERS`, with a separate GPU
semaphore.

### 3. Models load once

`hf_backend._MODEL_CACHE` is process-wide behind a double-checked lock. Eight
concurrent read nodes share one loaded classifier instead of loading eight.

Measured: 23.96s first load (with download) → 3.38s warm → 0s thereafter.

### 4. Batched local inference

CLIP and the classifier receive `MAOS_BATCH_SIZE` images per call rather than
one. Six images cost ~0.6s total, not 6 × 0.6s.

### 5. Two LLM round trips removed from planning

The deterministic planner replaced graph generation plus a completeness audit.
Saves ~4–10s on every single run.

### 6. Short-circuits

- `summarization` passes text under 600 characters straight through — no call.
- `image_classification` skips non-radiographs entirely.
- `web_search` skips the lookup when all findings are normal.
- Every model output is cached by `(model, prompt, content hash)`.

## Tuning

### Small GPU (≤6GB)

Defaults are already tuned for this.

```bash
MAOS_MAX_GPU_WORKERS=1     # never two vision models at once
MAOS_BATCH_SIZE=4          # lower if you see OOM
MAOS_MAX_IMAGE_EDGE=896
MAOS_FP16=1
```

An out-of-memory error is caught and re-raised as a `BackendError` naming these
exact variables.

### Large GPU (≥16GB)

```bash
MAOS_BACKEND=local         # keep everything local, no network
MAOS_VLM_BACKEND=local
MAOS_MAX_GPU_WORKERS=3
MAOS_BATCH_SIZE=16
```

### CPU only

```bash
MAOS_DEVICE=cpu
MAOS_BACKEND=remote        # only tiny models are viable locally
MAOS_BATCH_SIZE=2
```

### Lowest latency

```bash
MAOS_MAX_WORKERS=16
MAOS_MAX_IMAGE_EDGE=672    # fewer image tokens, slightly less detail
```
plus `--no-literature`, which removes a search and an LLM call from the graph.

### Large series (CT/MR)

```bash
MAOS_MAX_IMAGES=48         # default 24; images above this are evenly sampled
```

Raising it increases VLM calls proportionally. The loader samples evenly across
the series rather than truncating, so anatomical coverage is preserved — but a
finding on an unsampled slice is still missed. See `docs/FAILURE_MODES.md`.

### Slow or flaky network

```bash
MAOS_CALL_TIMEOUT=300
MAOS_MAX_RETRIES=5
MAOS_RETRY_BASE_DELAY=2
```

## Cache management

```bash
python main.py --clear-cache    # drop preprocessed images and model outputs
MAOS_CACHE=0                    # disable entirely (benchmarking)
MAOS_CACHE_DIR=/fast/ssd/cache  # relocate
```

The cache is content-addressed, so it is always safe to delete — the next run
simply recomputes.

## Estimating a run before you pay for it

```python
from agents.planner import build_radiology_plan, estimate_cost
from ingest.study_loader import load_study

graph = build_radiology_plan(load_study("./studies/p1"))
print(estimate_cost(graph))
# {'nodes': 10, 'vlm_calls': 6, 'text_llm_calls': 3,
#  'local_vision_passes': 2, 'max_concurrency': 6}
```

This is also logged at INFO on every run.

## Benchmarking honestly

Clear the cache first, or you will measure the cache:

```bash
python main.py --clear-cache
time python main.py --study ./studies/p1 --verbose
```

`--verbose` logs per-stage timings via `core.logging_utils.timed`, and the
generated report's *Run diagnostics* table gives a per-node breakdown.
