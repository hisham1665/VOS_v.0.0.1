"""Hugging Face model access: local transformers *and* hosted inference.

Why both
--------
A study needs two very different kinds of model:

* small discriminative vision models (a ViT classifier, CLIP) -- a few hundred
  MB, fast, and cheap to keep resident on a laptop GPU; and
* a large multimodal medical VLM that writes the actual impression -- 4B+
  parameters, which does not fit alongside anything else on a 4GB card.

So the backend routes per model class: small models run locally when torch and
transformers are importable, and large ones go to the Hugging Face Inference
API. ``MAOS_BACKEND=local`` or ``remote`` overrides the routing outright.

Loading discipline
------------------
Models are loaded once, lazily, behind a lock and kept in ``_MODEL_CACHE``. A
wide DAG will call the same classifier from several threads at once; without
this each thread would load its own copy and exhaust VRAM. A semaphore then
caps how many threads may touch the GPU simultaneously.
"""

from __future__ import annotations

import threading
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from config import HF_TOKEN, MODELS, RUNTIME
from core.errors import BackendError, ConfigError
from core.logging_utils import get_logger, timed
from core.retry import with_retry
from core.routing import TaskRequirements, performance_history, rank_candidates

_log = get_logger("hf")

# --------------------------------------------------------------------------
# Loaded-model registry
# --------------------------------------------------------------------------
_MODEL_CACHE: dict[str, Any] = {}
_LOAD_LOCK = threading.Lock()
# Recency order of loaded *pipelines* (small classifiers), for LRU eviction.
# The local VLM bundle is managed separately and never evicted here.
_PIPELINE_LRU: "OrderedDict[str, None]" = OrderedDict()

# Only this many threads may run local GPU inference at once. On a 4GB card
# that is one; a bigger card can raise MAOS_MAX_GPU_WORKERS.
_GPU_SEMAPHORE = threading.BoundedSemaphore(max(1, RUNTIME.max_gpu_workers))

# Records which concrete checkpoint served each capability, for the report's
# provenance block.
_MODELS_USED: dict[str, str] = {}
_USED_LOCK = threading.Lock()


def record_model(capability: str, model_id: str) -> None:
    with _USED_LOCK:
        _MODELS_USED[capability] = model_id


def models_used() -> dict[str, str]:
    with _USED_LOCK:
        return dict(_MODELS_USED)


# --------------------------------------------------------------------------
# Capability probing
# --------------------------------------------------------------------------
def _try_import(name: str) -> Any | None:
    try:
        import importlib

        return importlib.import_module(name)
    except ImportError:
        return None


def transformers_available() -> bool:
    return _try_import("transformers") is not None and _try_import("torch") is not None


def hf_api_available() -> bool:
    return bool(HF_TOKEN) and _try_import("huggingface_hub") is not None


def resolve_device() -> str:
    """Pick the torch device string for local inference."""
    if RUNTIME.device in {"cuda", "cpu"}:
        return RUNTIME.device
    torch = _try_import("torch")
    if torch is not None and torch.cuda.is_available():
        return "cuda"
    return "cpu"


def torch_dtype():
    """Half precision on CUDA, float32 on CPU (fp16 on CPU is slower)."""
    torch = _try_import("torch")
    if torch is None:
        return None
    if resolve_device() == "cuda" and RUNTIME.fp16:
        return torch.float16
    return torch.float32


def _dtype_kwarg() -> dict:
    """Return the precision kwarg under whichever name this transformers uses.

    transformers 5.x renamed ``torch_dtype`` to ``dtype`` and warns on the old
    spelling; 4.x only accepts the old one.
    """
    dtype = torch_dtype()
    if dtype is None:
        return {}
    transformers = _try_import("transformers")
    version = getattr(transformers, "__version__", "0") if transformers else "0"
    major = int(version.split(".")[0]) if version.split(".")[0].isdigit() else 0
    return {"dtype": dtype} if major >= 5 else {"torch_dtype": dtype}


def gpu_memory_gb() -> float:
    """Total VRAM in GB, or 0.0 when no CUDA device is present."""
    torch = _try_import("torch")
    if torch is None or not torch.cuda.is_available():
        return 0.0
    return torch.cuda.get_device_properties(0).total_memory / (1024**3)


def prefer_local(model_id: str, *, size_hint_gb: float = 0.5) -> bool:
    """Decide whether ``model_id`` should run locally.

    ``size_hint_gb`` is the rough half-precision footprint. We only keep a
    model local when it fits comfortably (60% of VRAM), leaving headroom for
    activations and for whatever else the run is doing.
    """
    if RUNTIME.backend == "local":
        return True
    if RUNTIME.backend == "remote":
        return False
    if not transformers_available():
        return False
    if resolve_device() == "cpu":
        # CPU inference is acceptable for tiny models only.
        return size_hint_gb <= 0.6
    return size_hint_gb <= gpu_memory_gb() * 0.6


# --------------------------------------------------------------------------
# Local pipelines
# --------------------------------------------------------------------------
def load_pipeline(task: str, model_id: str, **kwargs) -> Any:
    """Load (or reuse) a transformers pipeline for ``task``/``model_id``."""

    def _build() -> Any:
        if not transformers_available():
            raise BackendError(
                "transformers/torch are not installed; "
                "run `pip install transformers torch` or set MAOS_BACKEND=remote"
            )
        from transformers import pipeline

        return pipeline(
            task,
            model=model_id,
            device=0 if resolve_device() == "cuda" else -1,
            token=HF_TOKEN or None,
            **_dtype_kwarg(),
            **kwargs,
        )

    return load_local(f"pipeline:{task}:{model_id}", _build, label=model_id)


def load_local(key: str, factory: Callable[[], Any], *, label: str | None = None) -> Any:
    """Load (or reuse) any small local model under the shared LRU budget.

    ``factory`` builds the model; it runs at most once per key while resident.
    Used for transformers pipelines and for non-transformers checkpoints such
    as TorchXRayVision, so both count against ``MAOS_MAX_RESIDENT_MODELS``.
    """
    with _LOAD_LOCK:
        cached = _MODEL_CACHE.get(key)  # another thread may have won the race
        if cached is not None:
            _PIPELINE_LRU.move_to_end(key, last=True)
            return cached

        _evict_for_new_pipeline()

        name = label or key
        _log.info("loading %s on %s", name, resolve_device())
        with timed(_log, f"load {name}"):
            try:
                built = factory()
            except BackendError:
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced as BackendError
                raise BackendError(f"could not load {name}: {exc}") from exc

        _MODEL_CACHE[key] = built
        _PIPELINE_LRU[key] = None
        return built


def _evict_for_new_pipeline() -> None:
    """Drop least-recently-used pipelines until one more fits. Holds _LOAD_LOCK.

    A caller still holding a reference to an evicted pipeline keeps working;
    the weights are freed once that call returns.
    """
    limit = max(1, RUNTIME.max_resident_models)
    evicted = False
    while len(_PIPELINE_LRU) >= limit:
        old_key, _ = _PIPELINE_LRU.popitem(last=False)
        _MODEL_CACHE.pop(old_key, None)
        _log.info("evicting %s to stay within %d resident model(s)", old_key, limit)
        evicted = True
    if evicted:
        import gc

        gc.collect()
        torch = _try_import("torch")
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()


def run_local(fn: Callable[[], Any], *, label: str = "local inference") -> Any:
    """Run ``fn`` holding the GPU slot, under ``torch.inference_mode``."""
    torch = _try_import("torch")
    with _GPU_SEMAPHORE:
        if torch is None:
            return fn()
        with torch.inference_mode():
            try:
                return fn()
            except RuntimeError as exc:
                if "out of memory" in str(exc).lower():
                    torch.cuda.empty_cache()
                    raise BackendError(
                        f"{label}: GPU out of memory. Lower MAOS_BATCH_SIZE / "
                        f"MAOS_MAX_IMAGE_EDGE, or set MAOS_BACKEND=remote."
                    ) from exc
                raise


def unload_all() -> None:
    """Drop every loaded model and free VRAM. Used between studies in a server."""
    torch = _try_import("torch")
    with _LOAD_LOCK:
        _MODEL_CACHE.clear()
        _PIPELINE_LRU.clear()
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


# --------------------------------------------------------------------------
# Hosted inference (Hugging Face Inference API)
# --------------------------------------------------------------------------
def inference_client(model_id: str | None = None):
    """Return a configured ``huggingface_hub.InferenceClient``."""
    hub = _try_import("huggingface_hub")
    if hub is None:
        raise BackendError(
            "huggingface_hub is not installed; run `pip install huggingface_hub`"
        )
    if not HF_TOKEN:
        raise ConfigError(
            "HF_TOKEN is not set. Create a token at "
            "https://huggingface.co/settings/tokens and put HF_TOKEN=... in .env"
        )
    return hub.InferenceClient(model=model_id, token=HF_TOKEN, timeout=RUNTIME.call_timeout)


def remote_vlm(
    image_path: Path | str,
    prompt: str,
    *,
    system: str | None = None,
    model_id: str | None = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
) -> tuple[str, str]:
    """Send one image + prompt to a hosted VLM.

    Returns ``(text, model_id_actually_used)``. Falls back to the configured
    secondary VLM when the primary is gated or unavailable for this token.
    """
    from ingest.preprocess import image_data_url

    data_url = image_data_url(Path(image_path))
    candidates = [model_id] if model_id else [MODELS.report_vlm, MODELS.report_vlm_fallback]

    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    )

    errors: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            client = inference_client(candidate)

            def _call() -> str:
                completion = client.chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return (completion.choices[0].message.content or "").strip()

            text = with_retry(
                _call,
                attempts=RUNTIME.max_retries,
                base_delay=RUNTIME.retry_base_delay,
                label=f"vlm:{candidate}",
            )
            if text:
                return text, candidate
            errors.append(f"{candidate}: empty response")
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            _log.warning("VLM %s unavailable: %s", candidate, exc)
            errors.append(f"{candidate}: {type(exc).__name__}: {exc}")

    raise BackendError("no vision-language model was usable -> " + "; ".join(errors))


def remote_text(
    prompt: str,
    *,
    system: str | None = None,
    model_id: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.3,
) -> tuple[str, str]:
    """Hosted text generation through the HF Inference API."""
    target = model_id or MODELS.hf_text_llm
    client = inference_client(target)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    def _call() -> str:
        completion = client.chat_completion(
            messages=messages, max_tokens=max_tokens, temperature=temperature
        )
        return (completion.choices[0].message.content or "").strip()

    text = with_retry(
        _call,
        attempts=RUNTIME.max_retries,
        base_delay=RUNTIME.retry_base_delay,
        label=f"text:{target}",
    )
    return text, target


# --------------------------------------------------------------------------
# Hosted text LLM (Groq) -- used for planning and synthesis
# --------------------------------------------------------------------------
_groq_client = None
_groq_lock = threading.Lock()


def groq_client():
    """Return a shared Groq client, or raise a useful ConfigError."""
    global _groq_client
    from config import GROQ_API_KEY

    if not GROQ_API_KEY:
        raise ConfigError(
            "GROQ_API_KEY is not set. Put GROQ_API_KEY=... in .env, or set "
            "MAOS_TEXT_BACKEND=hf to route text generation through Hugging Face."
        )
    if _groq_client is None:
        with _groq_lock:
            if _groq_client is None:
                from groq import Groq

                _groq_client = Groq(api_key=GROQ_API_KEY, timeout=RUNTIME.call_timeout, max_retries=0)
    return _groq_client


def chat_text(
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    task: str = "conversation",
    json_mode: bool = False,
    validator: Callable[[str], Any] | None = None,
    constraints: dict | None = None,
) -> tuple[str, str]:
    """Generate text, preferring Groq and falling back to HF Inference.

    Returns ``(text, model_id_used)``.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    from config import GROQ_API_KEY
    from capabilities.text_catalog import profiles

    requested_backend = os.environ.get("MAOS_TEXT_BACKEND", "auto").lower()
    entries = [item for item in profiles(task)
               if (requested_backend == "auto" or item[0] == requested_backend)
               and ((item[0] == "groq" and GROQ_API_KEY) or (item[0] == "hf" and hf_api_available()))]
    required = {"skills": (task,), "inputs": ("text",),
                "outputs": ("json",) if json_mode else ("text",),
                "context_tokens": (len(prompt) + len(system or "")) // 4 + max_tokens}
    limits = dict(constraints or {})
    for key in ("skills", "inputs", "outputs"):
        if key in limits:
            limits[key] = tuple(dict.fromkeys(required[key] + tuple(limits[key])))
    limits["context_tokens"] = max(required["context_tokens"], int(limits.get("context_tokens", 0)))
    requirements = TaskRequirements(**{**required, **limits})
    candidates, decisions = rank_candidates([item[2] for item in entries], requirements, performance_history())
    _TEXT_TRACE.decision = {"task": task, "requirements": requirements.to_dict(), "candidates": decisions, "attempts": []}
    by_name = {item[2].name: item for item in entries}
    errors = []
    for dna in candidates:
        provider, model_id, _ = by_name[dna.name]
        started = time.perf_counter()
        try:
            if provider == "groq":
                client = groq_client()
                def _call() -> str:
                    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
                    completion = client.chat.completions.create(model=model_id, messages=messages,
                                                               temperature=temperature, max_tokens=max_tokens, **kwargs)
                    return (completion.choices[0].message.content or "").strip()
                text = with_retry(_call, attempts=RUNTIME.max_retries,
                                  base_delay=RUNTIME.retry_base_delay, label=dna.name)
            else:
                text, _ = remote_text(prompt, system=system, model_id=model_id,
                                      max_tokens=max_tokens, temperature=temperature)
            if not text:
                raise BackendError("empty model response")
            if json_mode:
                import json

                if not isinstance(json.loads(text), dict):
                    raise ValueError("JSON response must be an object")
            if validator:
                validator(text)
            performance_history().record(dna.name, success=True, seconds=time.perf_counter() - started)
            _TEXT_TRACE.decision["attempts"].append({"model": dna.name, "status": "accepted"})
            _log.info("selected %s for %s from %d eligible model(s)", dna.name, task, len(candidates))
            return text, model_id
        except Exception as exc:  # noqa: BLE001 - an alternative must pass the same evaluator
            performance_history().record(dna.name, success=False, seconds=time.perf_counter() - started, cooldown_s=30)
            _TEXT_TRACE.decision["attempts"].append({"model": dna.name, "status": "rejected", "error_type": type(exc).__name__})
            _log.warning("text model %s rejected (%s); adapting selection", dna.name, type(exc).__name__)
            errors.append(f"{dna.name}: {type(exc).__name__}: {exc}")
    raise BackendError("No usable text model satisfies the task. " + ("; ".join(errors) or "Check credentials, catalog, context budget, or recent endpoint cooldown."))


_TEXT_TRACE = threading.local()


def text_selection_trace() -> dict:
    return dict(getattr(_TEXT_TRACE, "decision", {}))


def backend_report() -> dict[str, Any]:
    """Snapshot of what this machine can actually run. Printed by `--doctor`."""
    return {
        "transformers": transformers_available(),
        "hf_token": bool(HF_TOKEN),
        "hf_api": hf_api_available(),
        "device": resolve_device(),
        "gpu_memory_gb": round(gpu_memory_gb(), 2),
        "fp16": RUNTIME.fp16,
        "backend_mode": RUNTIME.backend,
        "loaded_models": sorted(_MODEL_CACHE.keys()),
    }
