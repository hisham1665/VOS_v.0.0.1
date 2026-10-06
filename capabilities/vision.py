"""Vision-language reading of a single medical image.

This is the narrative half of the pipeline: a medical VLM looks at one prepared
image and produces a structured read (modality, quality, observations,
impression). The classifier's probabilities are injected into the prompt when
available, so the VLM is grounding its prose against a second opinion rather
than free-associating.

Three routes are supported, tried in the order that is cheapest for the machine
it runs on:

1. **Ollama** -- a local server (e.g. ``medgemma``) at ``MAOS_OLLAMA_URL``.
   Free, private, no data leaves the machine. Preferred when reachable.
2. **Hugging Face Inference API** -- hosted ``MAOS_REPORT_VLM``. Needed on
   small GPUs, since a 4B VLM in fp16 wants ~8GB of VRAM.
3. **Local transformers** -- only when the card is genuinely large enough.

Each read is cached on ``(model, prompt, image hash)``, so a re-run after a
crash costs nothing.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from config import MODELS, RUNTIME
from core import cache
from core.errors import CapabilityError
from core.logging_utils import get_logger, timed
from capabilities.hf_backend import (
    gpu_memory_gb,
    hf_api_available,
    prefer_local,
    record_model,
    remote_vlm,
    run_local,
)
from capabilities.registry import CapabilityRequest, CapabilityResult, register
from models import Modality

_log = get_logger("vision")

OLLAMA_URL = os.environ.get("MAOS_OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.environ.get("MAOS_OLLAMA_MODEL", "gemma3:4b")
VLM_BACKEND = os.environ.get("MAOS_VLM_BACKEND", "auto").lower()  # auto|ollama|openai|hf|local
VLM_URL = os.environ.get("MAOS_VLM_URL", "http://localhost:8080/v1")
VLM_MODEL = os.environ.get("MAOS_VLM_MODEL", "medgemma-1.5-4b")
_SERVER_GATE = threading.BoundedSemaphore(max(1, RUNTIME.max_gpu_workers))

_SYSTEM_PROMPT = (
    "You are an experienced radiologist producing a structured preliminary read "
    "for a referring clinician. You describe only what is visible in the image "
    "supplied. You never invent measurements, comparisons with priors you were "
    "not given, or patient history you were not told.\n\n"
    "If image quality limits interpretation (rotation, penetration, motion, "
    "cropping, artefact), say so explicitly and state what it obscures.\n"
    "If the image is not a medical image at all, say exactly that and stop.\n"
    "Where you are uncertain, use hedged radiological language ('cannot be "
    "excluded', 'suggestive of') rather than asserting a diagnosis."
)

_READ_TEMPLATE = """\
Produce a structured preliminary read of this image.

{context}

Answer under exactly these headings:

MODALITY AND VIEW: the imaging modality, projection/view, and body region.
IMAGE QUALITY: whether the study is diagnostic, and any limitation.
OBSERVATIONS: each distinct finding on its own line, in the form
  - <finding>: <location> | <severity: critical|high|moderate|low|normal> | <confidence: high|medium|low>
   Use "- No acute abnormality: n/a | normal | high" only if the supplied image
   is interpretable and shows none. A single frame cannot establish a normal
   complete CT/MRI study. For limited/blank/non-medical images, state that no
   reliable assessment is possible; never substitute a normal finding.
IMPRESSION: two or three sentences summarising the clinical significance.
"""


@register(
    "vision",
    "Read one medical image with a vision-language model and describe findings.",
)
def run(request: CapabilityRequest) -> CapabilityResult:
    images = request.images()
    if not images:
        return CapabilityResult(
            text="No image was supplied to this node, so no read was produced."
        )

    # One node reads one image. The planner fans out a node per image so the
    # reads run concurrently; if a node was handed several, read them in turn.
    reads: dict[str, str] = {}
    errors: dict[str, str] = {}
    models_used: set[str] = set()

    for image in images:
        try:
            quality = (request.artifacts.get("image_quality") or {}).get(image.image_id) or image.quality
            if quality.get("status") == "unusable":
                raise ValueError("Uniform/blank pixel data; image was not interpreted")
            if image.image_id in set(request.artifacts.get("non_medical", [])):
                raise ValueError("Excluded by non-medical image gate; image was not interpreted")
            text, model_id = _read_image(image, request)
            reads[image.image_id] = text
            models_used.add(model_id)
        except Exception as exc:  # noqa: BLE001 - recorded, other images continue
            _log.warning("read failed for %s: %s", image.image_id, exc)
            errors[image.image_id] = f"{type(exc).__name__}: {exc}"

    if not reads:
        raise CapabilityError(
            "vision",
            "every image read failed -> " + "; ".join(errors.values()),
        )

    parts = []
    for image_id, text in reads.items():
        parts.append(f"=== Read of image `{image_id}` ===\n{text}")
    for image_id, message in errors.items():
        parts.append(f"=== Image `{image_id}` could not be read: {message} ===")

    return CapabilityResult(
        text="\n\n".join(parts),
        artifacts={
            "reads": reads,
            "read_errors": errors,
            "image_quality": {image.image_id: ((request.artifacts.get("image_quality") or {}).get(image.image_id) or image.quality)
                              for image in images},
            "vlm_models": sorted(models_used),
            "_models_used": {"vision": ", ".join(sorted(models_used))},
        },
    )


def _read_image(image, request: CapabilityRequest) -> tuple[str, str]:
    """Return ``(read_text, model_id)`` for one image, using the cache."""
    prompt = _build_prompt(image, request)
    route = _select_route()
    key = cache.make_key("vlm-v2", route, _route_model(route),
                         OLLAMA_URL if route == "ollama" else None,
                         _SYSTEM_PROMPT, image.model_input_hash, prompt)

    cached = cache.read_json("vision", key)
    if cached:
        _log.info("cache hit for read of %s", image.image_id)
        record_model("vision", cached["model"])
        return cached["text"], cached["model"]
    from core.execution_context import remaining_time
    remaining_time(RUNTIME.call_timeout)

    with timed(_log, f"VLM read of {image.image_id} via {route}"):
        text, model_id = _dispatch(route, Path(image.path_for_model), prompt)

    if not text.strip():
        raise CapabilityError("vision", f"{model_id} returned an empty read")

    record_model("vision", model_id)
    cache.write_json("vision", key, {"text": text, "model": model_id})
    return text, model_id


def _build_prompt(image, request: CapabilityRequest) -> str:
    """Assemble the per-image prompt from study context and upstream artifacts."""
    context_lines: list[str] = []

    if image.modality != Modality.UNKNOWN:
        context_lines.append(f"Stated modality: {image.modality.value}")
    if image.body_part:
        context_lines.append(f"Body region: {image.body_part}")
    if image.view:
        context_lines.append(f"View: {image.view}")
    if image.series_id:
        context_lines.append(f"Source series: {image.series_id}; ordered frame position: {image.series_order}; source frame index: {image.frame_index}")
    quality = (request.artifacts.get("image_quality") or {}).get(image.image_id) or image.quality
    if quality:
        context_lines.append("Technical quality check (not diagnostic adequacy): " + str(quality))

    study = request.study
    if study is not None:
        series = next((s for s in study.series if s.series_id == image.series_id), None)
        if series:
            context_lines.append(f"This is one 2D image/frame from {len(series.image_ids)}/{series.acquired_count} supplied decoded frames; acquisition completeness {series.coverage}.")
            context_lines.extend(series.warnings)
            context_lines.append("Other frames are not supplied to this call. Do not claim to have reviewed the complete volume or compare unseen slices.")
        patient = study.patient
        details = [
            f"age {patient.age}" if patient.age else "",
            f"sex {patient.sex}" if patient.sex else "",
        ]
        details = [d for d in details if d]
        if details:
            context_lines.append("Patient: " + ", ".join(details))
        if patient.clinical_history:
            context_lines.append(f"Clinical history: {patient.clinical_history}")
        if patient.referring_question:
            context_lines.append(
                f"Referring question: {patient.referring_question}"
            )

    context_lines.extend(_cascade_context(image, request.artifacts or {}))

    if image.warnings:
        context_lines.append("Preprocessing notes: " + "; ".join(image.warnings))

    # The node description is the planner's specific instruction for this node.
    if request.instruction:
        context_lines.append(f"Specific task: {request.instruction}")

    context = "\n".join(f"- {line}" for line in context_lines) or "- No prior context."
    return _READ_TEMPLATE.format(context=f"Context:\n{context}")


def _cascade_context(image, artifacts: dict) -> list[str]:
    """Describe the screening cascade's evidence for this image, with its limits.

    The old prompt passed a bare "PNEUMONIA 53%" from a pneumonia-only model,
    even for CT slices, and the VLM duly echoed it. Every claim here names the
    model's scope, and an unscreened image says so instead of saying nothing.
    """
    lines: list[str] = []
    triage = (artifacts.get("triage") or {}).get(image.image_id) or {}
    if triage and (triage.get("confidence") or 0) < 0.6 and not triage.get("non_medical"):
        lines.append(
            f"Automated modality detection is uncertain ({triage.get('modality')}, "
            f"{(triage.get('confidence') or 0):.0%}). Determine the modality and body "
            "region yourself from the image before interpreting it."
        )

    screening = (artifacts.get("screening") or {}).get(image.image_id)
    if not screening:
        for record in (artifacts.get("confirmation") or {}).get(image.image_id, []):
            if record.get("verdict") != "unverifiable":
                lines.append(f"Narrow specialist re-check (no broad screener): {record['target']} {record['verdict']} ({record['model']}, {record['probability']:.0%}); not a diagnosis.")
        return lines
    if screening.get("status") != "screened":
        lines.append(
            f"No supervised screening model was run on this image "
            f"({screening.get('reason', 'not covered')}). Your read is the only "
            "narrative interpretation; be explicit about uncertainty."
        )
        for record in (artifacts.get("confirmation") or {}).get(image.image_id, []):
            if record.get("verdict") != "unverifiable":
                lines.append(f"Narrow specialist re-check (no broad screener): {record['target']} {record['verdict']} ({record['model']}, {record['probability']:.0%}); not a diagnosis.")
        return lines

    candidates = screening.get("candidates") or []
    models = ", ".join(screening.get("models") or [])
    if candidates:
        rendered = ", ".join(f"{c['condition']} {c['score']:.0%}" for c in candidates[:5])
        lines.append(
            f"Narrow screening model(s) [{models}] trained on public 2D datasets "
            f"for this modality/region flagged: {rendered}."
        )
    else:
        lines.append(
            f"Narrow screening model(s) [{models}] flagged nothing above threshold. "
            "They only know their own label set; this is not evidence of normality."
        )

    for record in (artifacts.get("confirmation") or {}).get(image.image_id, []):
        if record.get("verdict") == "unverifiable":
            continue
        lines.append(
            f"Specialist re-check for {record['target']} ({record['model']}): "
            f"{record['verdict'].replace('_', ' ')} ({record['probability']:.0%})."
        )

    lines.append(
        "Treat all of the above as a second opinion. Confirm a flagged condition only "
        "if you can see the corresponding finding, say plainly when you cannot, and "
        "report anything else you see even though no model flagged it."
    )
    return lines


def _select_route() -> str:
    """Choose ollama / hf / local for this run."""
    if VLM_BACKEND in {"ollama", "openai", "hf", "local"}:
        return VLM_BACKEND
    if _ollama_reachable():
        return "ollama"
    if hf_api_available():
        return "hf"
    # A 4B VLM in fp16 needs roughly 8GB; only go local on a big card.
    if prefer_local(MODELS.report_vlm, size_hint_gb=8.0) and gpu_memory_gb() >= 12:
        return "local"
    return "hf"  # will raise a clear ConfigError about the missing token


def _route_model(route: str) -> str:
    if route == "openai":
        return f"{VLM_MODEL}@{VLM_URL.rstrip('/')}"
    return OLLAMA_MODEL if route == "ollama" else MODELS.report_vlm


def _dispatch(route: str, image_path: Path, prompt: str) -> tuple[str, str]:
    if route == "ollama":
        return _read_ollama(image_path, prompt)
    if route == "openai":
        return _read_openai_server(image_path, prompt, url=VLM_URL, model=VLM_MODEL, label="openai-compatible")
    if route == "local":
        return _read_local(image_path, prompt)
    return remote_vlm(image_path, prompt, system=_SYSTEM_PROMPT)


_ollama_checked: bool | None = None
_ollama_checked_at = 0.0
_ollama_lock = threading.Lock()


def _ollama_reachable() -> bool:
    """Probe the Ollama server once per process.

    Locked because every read node calls this concurrently: without it, each
    of N parallel reads pays the connection timeout separately before the
    result is memoised.
    """
    global _ollama_checked
    ttl = 30.0 if _ollama_checked else 5.0
    if _ollama_checked is not None and time.monotonic() - _ollama_checked_at < ttl:
        return _ollama_checked
    with _ollama_lock:
        if _ollama_checked is not None and time.monotonic() - _ollama_checked_at < ttl:
            return _ollama_checked
        return _probe_ollama()


def _probe_ollama() -> bool:
    global _ollama_checked, _ollama_checked_at
    try:
        import requests

        base = OLLAMA_URL.rstrip("/").removesuffix("/v1")
        response = requests.get(f"{base}/api/tags", timeout=1.5)
        available = response.ok
        if available:
            tags = {m.get("name", "") for m in response.json().get("models", [])}
            available = OLLAMA_MODEL in tags
            if not available:
                _log.info(
                    "Ollama is up but %s is not pulled (have: %s)",
                    OLLAMA_MODEL,
                    sorted(tags)[:5],
                )
            else:
                model_response = requests.post(f"{base}/api/show", json={"model": OLLAMA_MODEL}, timeout=1.5)
                available = model_response.ok and "vision" in model_response.json().get("capabilities", [])
    except Exception:  # noqa: BLE001 - absence is the normal case
        available = False
    _ollama_checked = available
    _ollama_checked_at = time.monotonic()
    return available


def _read_ollama(image_path: Path, prompt: str) -> tuple[str, str]:
    return _read_openai_server(image_path, prompt, url=OLLAMA_URL, model=OLLAMA_MODEL, label="ollama")


def _read_openai_server(image_path: Path, prompt: str, *, url: str, model: str, label: str) -> tuple[str, str]:
    from openai import OpenAI

    from core.retry import with_retry
    from ingest.preprocess import image_data_url

    client = OpenAI(base_url=url, api_key="local", timeout=RUNTIME.call_timeout, max_retries=0)
    data_url = image_data_url(image_path)

    def _call() -> str:
        from core.execution_context import call_deadline, remaining_time
        timeout = remaining_time(RUNTIME.call_timeout)
        if call_deadline.get() is not None:
            timeout -= min(0.5, timeout / 10)  # leave time to preserve partial reads
        completion = client.with_options(timeout=timeout).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=0.2,
            max_tokens=900,
        )
        return (completion.choices[0].message.content or "").strip()

    # The small GPU is shared by all local-server nodes. Queue one image call
    # at a time instead of asking a 4GB card to hold several contexts at once.
    from core.execution_context import remaining_time
    if not _SERVER_GATE.acquire(timeout=remaining_time(RUNTIME.call_timeout)):
        raise TimeoutError("local vision concurrency queue exceeded capability deadline")
    try:
        text = with_retry(_call, attempts=RUNTIME.max_retries, base_delay=RUNTIME.retry_base_delay,
                          label=f"{label}:{model}")
    finally:
        _SERVER_GATE.release()
    return text, f"{model} ({label})"


def local_server_info() -> dict:
    """Inspect configured model metadata without sending patient pixels."""
    import requests

    if VLM_BACKEND == "openai":
        try:
            response = requests.get(VLM_URL.rstrip("/") + "/models", timeout=2)
            response.raise_for_status()
            ids = {entry.get("id") for entry in response.json().get("data", [])}
            return {"backend": "openai", "url": VLM_URL, "model": VLM_MODEL,
                    "reachable": VLM_MODEL in ids, "vision_verified": False,
                    "detail": "Model listed; projector/image inference must be smoke-tested separately"}
        except Exception as exc:
            return {"backend": "openai", "reachable": False, "model": VLM_MODEL, "detail": str(exc)}
    base = OLLAMA_URL.rstrip("/").removesuffix("/v1")
    try:
        response = requests.post(f"{base}/api/show", json={"model": OLLAMA_MODEL}, timeout=2)
        response.raise_for_status()
        payload = response.json()
        capabilities = payload.get("capabilities", [])
        return {"backend": "ollama", "model": OLLAMA_MODEL, "reachable": True,
                "vision_verified": "vision" in capabilities, "capabilities": capabilities,
                "details": payload.get("details", {})}
    except Exception as exc:
        return {"backend": "ollama", "model": OLLAMA_MODEL, "reachable": False, "detail": str(exc)}


def _read_local(image_path: Path, prompt: str) -> tuple[str, str]:
    """Run the VLM from local weights. Only viable on a large GPU."""
    from transformers import AutoModelForImageTextToText, AutoProcessor

    from capabilities.hf_backend import (
        HF_TOKEN,
        _LOAD_LOCK,
        _MODEL_CACHE,
        _dtype_kwarg,
        resolve_device,
    )
    from ingest.preprocess import load_pil

    key = f"vlm:{MODELS.report_vlm}"
    with _LOAD_LOCK:
        bundle = _MODEL_CACHE.get(key)
        if bundle is None:
            _log.info("loading local VLM %s", MODELS.report_vlm)
            processor = AutoProcessor.from_pretrained(
                MODELS.report_vlm, token=HF_TOKEN or None
            )
            model = AutoModelForImageTextToText.from_pretrained(
                MODELS.report_vlm,
                device_map="auto" if resolve_device() == "cuda" else None,
                token=HF_TOKEN or None,
                **_dtype_kwarg(),
            )
            model.eval()
            bundle = (processor, model)
            _MODEL_CACHE[key] = bundle

    processor, model = bundle
    image = load_pil(image_path)
    messages = [
        {"role": "system", "content": [{"type": "text", "text": _SYSTEM_PROMPT}]},
        {
            "role": "user",
            "content": [{"type": "image", "image": image}, {"type": "text", "text": prompt}],
        },
    ]

    def _generate() -> str:
        inputs = processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(model.device, dtype=model.dtype)
        prompt_len = inputs["input_ids"].shape[-1]
        generated = model.generate(**inputs, max_new_tokens=900, do_sample=False)
        return processor.decode(
            generated[0][prompt_len:], skip_special_tokens=True
        ).strip()

    text = run_local(_generate, label="vlm-local")
    return text, f"{MODELS.report_vlm} (local)"
