"""Stage 1 of the screening cascade: route each image to the right screener.

This runs *alongside* the VLM rather than instead of it, and the reason is
clinical: a generative model will happily produce fluent prose about a finding
it did not see. A classifier returns a calibrated probability for a fixed label
set, which gives the report something checkable to anchor on -- and gives the
synthesis step a way to flag disagreement between the two.

What changed
------------
The first version ran one binary pneumonia model on every image, and ran it in
the same wave as modality triage -- so a CT slice still had modality "Unknown"
when it was screened, passed the gate, and came back "PNEUMONIA 53%". The VLM
was then told to weigh that as a second opinion.

Now the node runs *after* triage and only on images whose modality and region
were identified with reasonable confidence. Each image is routed through
:mod:`capabilities.model_zoo` to the screener(s) trained on that modality and
region. The output is a list of *candidate conditions*, which
:mod:`capabilities.specialist_confirmation` re-checks with narrower models.

An image that no screener covers is reported as "not screened" with the reason.
Silence is never presented as normality.
"""

from __future__ import annotations

import functools

from config import RUNTIME
from core import cache
from core.logging_utils import get_logger, timed
from capabilities import model_zoo
from capabilities.hf_backend import (
    load_pipeline,
    prefer_local,
    record_model,
    run_local,
)
from capabilities.model_zoo import ZooModel
from capabilities.registry import CapabilityRequest, CapabilityResult, register
from models import Modality, Severity

_log = get_logger("classify")

# Triage below this confidence is a guess; routing a guess to a disease model
# reproduces exactly the "CT read as chest X-ray" failure this stage fixes.
_MIN_ROUTING_CONFIDENCE = 0.45


@register(
    "image_classification",
    "Cascade stage 1: route each image to the screener trained for its modality "
    "and region, and report candidate conditions.",
)
def run(request: CapabilityRequest) -> CapabilityResult:
    images = request.images()
    if not images:
        return CapabilityResult(text="No images supplied for classification.")

    artifacts = request.artifacts or {}
    triage = artifacts.get("triage", {}) or {}
    non_medical = set(artifacts.get("non_medical", []) or [])

    # Work out the route for each image before loading anything.
    routes: dict[str, list[ZooModel]] = {}
    screening: dict[str, dict] = {}
    for img in images:
        info = triage.get(img.image_id, {})
        region = info.get("region") or _region_from_text(img.body_part)
        entry = {
            "modality": img.modality.value,
            "region": region,
            "status": "skipped",
            "reason": "",
            "models": [],
            "abnormal_score": None,
            "candidates": [],
            "routing_eligible": False,
        }
        screening[img.image_id] = entry

        confidence = info.get("confidence")
        quality = (artifacts.get("image_quality") or {}).get(img.image_id) or img.quality
        if quality.get("status") == "unusable":
            entry["reason"] = "unusable/blank pixel data; supervised screening not run"
        elif img.image_id in non_medical:
            entry["reason"] = "not a medical image"
        elif img.modality == Modality.UNKNOWN:
            entry["reason"] = "modality could not be identified"
        elif confidence is not None and confidence < _MIN_ROUTING_CONFIDENCE:
            entry["reason"] = (
                f"modality identification too uncertain ({confidence:.0%}) to "
                "route to a disease model"
            )
        else:
            entry["routing_eligible"] = True
            models = model_zoo.screeners_for(img.modality, region)
            entry["screeners_available"] = [model.key for model in models]
            if not models:
                entry["reason"] = (
                    f"no supervised screener in the model zoo covers "
                    f"{img.modality.value}{' / ' + region if region else ''}"
                )
            else:
                routes[img.image_id] = models

    by_id = {img.image_id: img for img in images}
    raw_scores: dict[str, dict[str, list[dict]]] = {}
    errors: list[str] = []

    # Group by model so each checkpoint is loaded once and batched.
    for model in _unique_models(routes):
        targets = [by_id[i] for i, models in routes.items() if model in models]
        try:
            scores = classify_images(model, targets)
        except Exception as exc:  # noqa: BLE001 - degrade, do not abort the study
            _log.warning("screener %s unavailable: %s", model.model_id, exc)
            errors.append(f"{model.model_id}: {type(exc).__name__}: {exc}")
            continue
        for image_id, labels in scores.items():
            raw_scores.setdefault(image_id, {})[model.key] = labels

    flagged: list[dict] = []
    for image_id, models in routes.items():
        entry = screening[image_id]
        per_model = raw_scores.get(image_id, {})
        if not per_model:
            entry["reason"] = "screener failed to run (see warnings)"
            continue
        entry["status"] = "screened"
        abnormal: list[float] = []
        for model in models:
            labels = per_model.get(model.key)
            if labels is None:
                continue
            entry["models"].append(model.key)
            conditions = condition_scores(model, labels)
            abnormal.append(abnormal_score(model, conditions))
            for condition, score in conditions.items():
                if condition == "normal" or score < model.threshold:
                    continue
                candidate = {
                    "condition": condition,
                    "score": round(score, 4),
                    "model": model.key,
                    "model_id": model.model_id,
                    "severity": model_zoo.severity_for(condition).value,
                }
                entry["candidates"].append(candidate)
                flagged.append({"image_id": image_id, "label": condition, **candidate})
        entry["abnormal_score"] = round(max(abnormal), 4) if abnormal else None
        # Option-B fan-out: "something is off but no single label is sure"
        # still earns a specialist pass for everything that applies here.
        trigger = min(m.abnormal_threshold for m in models)
        if not entry["candidates"] and abnormal and max(abnormal) >= trigger:
            candidate = {
                "condition": "abnormal",
                "score": round(max(abnormal), 4),
                "model": entry["models"][0],
                "model_id": models[0].model_id,
                "severity": model_zoo.severity_for("abnormal").value,
            }
            entry["candidates"].append(candidate)
        entry["candidates"].sort(key=lambda c: c["score"], reverse=True)

    requested = model_zoo.requested_conditions(
        *(
            (request.study.patient.referring_question, request.study.patient.clinical_history)
            if request.study is not None
            else ()
        )
    )

    return CapabilityResult(
        text=_render(screening, requested, errors),
        artifacts={
            "screening": screening,
            # Raw label probabilities per image and model, for audit.
            "classification": raw_scores,
            "flagged": flagged,
            "requested_conditions": requested,
            "_models_used": {f"classifier:{key}": model_zoo.ZOO[key].model_id
                             for scores in raw_scores.values() for key in scores if key in model_zoo.ZOO},
            **({"screen_errors": errors} if errors else {}),
        },
    )


# --------------------------------------------------------------------------
# Score interpretation, shared with stage 2
# --------------------------------------------------------------------------
def condition_scores(model: ZooModel, labels: list[dict]) -> dict[str, float]:
    """Collapse raw labels into canonical conditions.

    Single-label (softmax) heads sum, so the four haemorrhage subtypes add up
    to one "intracranial_hemorrhage" probability. Multi-label (sigmoid) heads
    take the max, because their probabilities are not a partition.
    """
    out: dict[str, float] = {}
    for entry in labels:
        condition = model.canonical(entry["label"])
        score = float(entry["score"])
        if model.multi_label:
            out[condition] = max(out.get(condition, 0.0), score)
        else:
            out[condition] = out.get(condition, 0.0) + score
    return out


def abnormal_score(model: ZooModel, conditions: dict[str, float]) -> float:
    """Probability that the image is *not* normal, as far as this model knows."""
    if model.multi_label:
        # "No Finding" on a sigmoid head is just another label; the strongest
        # pathology is the better summary.
        return max((v for k, v in conditions.items() if k != "normal"), default=0.0)
    return max(0.0, 1.0 - conditions.get("normal", 0.0))


# Fallback for raw labels from a checkpoint not described in the zoo.
_SEVERITY_HINTS: tuple[tuple[tuple[str, ...], Severity], ...] = (
    (("pneumothorax", "tension", "haemorrhage", "hemorrhage", "dissection"), Severity.CRITICAL),
    (("pneumonia", "consolidation", "effusion", "oedema", "edema", "embolism"), Severity.HIGH),
    (("opacity", "infiltrate", "nodule", "mass", "lesion", "fracture"), Severity.MODERATE),
    (("cardiomegaly", "atelectasis", "thickening", "scarring"), Severity.LOW),
    (("normal", "no finding", "negative", "healthy"), Severity.NORMAL),
)


def _severity_for(label: str) -> Severity:
    """Severity of a raw label: zoo vocabulary first, substring hints second."""
    canonical = label.strip().lower().replace(" ", "_")
    if canonical in model_zoo.CONDITION_SEVERITY:
        return model_zoo.severity_for(canonical)
    lowered = label.lower()
    for fragments, severity in _SEVERITY_HINTS:
        if any(fragment in lowered for fragment in fragments):
            return severity
    return Severity.INDETERMINATE


# --------------------------------------------------------------------------
# Inference, shared with stage 2
# --------------------------------------------------------------------------
def classify_images(model: ZooModel, images: list) -> dict[str, list[dict]]:
    """Return ``{image_id: [{label, score}, ...]}`` sorted by descending score."""
    if model.backend == "torchxrayvision":
        return _classify_xrv(model, images)
    if prefer_local(model.model_id, size_hint_gb=model.size_gb):
        return _classify_local(model, images)
    return _classify_remote(model, images)


def _classify_local(model: ZooModel, images: list) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    uncached: list = []
    for img in images:
        hit = _cached(model, img)
        if hit is None:
            uncached.append(img)
        else:
            out[img.image_id] = hit
    if not uncached:
        record_model(f"classifier:{model.key}", model.model_id)
        return out

    extra = {"image_processor": _slow_processor(model.processor_id)} if model.processor_id else {}
    classifier = load_pipeline("image-classification", model.model_id, **extra)
    record_model(f"classifier:{model.key}", model.model_id)

    from ingest.preprocess import load_pil

    with timed(_log, f"{model.key}: classifying {len(uncached)} image(s)"):
        for start in range(0, len(uncached), RUNTIME.batch_size):
            batch = uncached[start : start + RUNTIME.batch_size]
            pil_batch = [load_pil(img.path_for_model) for img in batch]

            results = run_local(
                lambda: classifier(pil_batch, top_k=None), label=model.key
            )
            if results and isinstance(results[0], dict):
                results = [results]  # single-image call returns a flat list

            for img, labels in zip(batch, results):
                normalised = _normalise(labels)
                out[img.image_id] = normalised
                _store(model, img, normalised)
    return out


def _classify_xrv(model: ZooModel, images: list) -> dict[str, list[dict]]:
    """TorchXRayVision checkpoints: local only, same cache and output shape."""
    from capabilities import xrv_backend

    out: dict[str, list[dict]] = {}
    uncached = []
    for img in images:
        hit = _cached(model, img)
        if hit is None:
            uncached.append(img)
        else:
            out[img.image_id] = hit
    record_model(f"classifier:{model.key}", f"torchxrayvision/{model.model_id}")
    if uncached:
        results = xrv_backend.classify(model.model_id, [img.path_for_model for img in uncached])
        for img, labels in zip(uncached, results):
            normalised = _normalise(labels)
            out[img.image_id] = normalised
            _store(model, img, normalised)
    return out


@functools.lru_cache(maxsize=8)
def _slow_processor(processor_id: str):
    """Load the PIL-based image processor named in ``processor_id``'s config.

    ``AutoImageProcessor`` insists on torchvision in recent transformers even
    with ``use_fast=False``; resolving the concrete class avoids that.
    """
    import json

    import transformers
    from huggingface_hub import hf_hub_download

    from config import HF_TOKEN

    config_path = hf_hub_download(
        processor_id, "preprocessor_config.json", token=HF_TOKEN or None
    )
    with open(config_path, encoding="utf-8") as handle:
        class_name = json.load(handle).get("image_processor_type", "ViTImageProcessor")
    class_name = class_name.removesuffix("Fast")
    return getattr(transformers, class_name).from_pretrained(processor_id)


def _classify_remote(model: ZooModel, images: list) -> dict[str, list[dict]]:
    from capabilities.hf_backend import inference_client
    from core.retry import with_retry

    client = inference_client(model.model_id)
    record_model(f"classifier:{model.key}", f"{model.model_id} (hosted)")
    out: dict[str, list[dict]] = {}

    for img in images:
        hit = _cached(model, img)
        if hit is not None:
            out[img.image_id] = hit
            continue

        def _call(image=img):
            return client.image_classification(image.path_for_model)

        raw = with_retry(
            _call,
            attempts=RUNTIME.max_retries,
            base_delay=RUNTIME.retry_base_delay,
            label=f"classify-remote:{model.key}",
        )
        normalised = _normalise(raw)
        out[img.image_id] = normalised
        _store(model, img, normalised)
    return out


def _normalise(labels) -> list[dict]:
    """Coerce a pipeline/API label list into sorted ``{label, score}`` dicts."""
    entries = []
    for entry in labels:
        label = entry["label"] if isinstance(entry, dict) else entry.label
        score = entry["score"] if isinstance(entry, dict) else entry.score
        entries.append({"label": str(label), "score": round(float(score), 4)})
    return sorted(entries, key=lambda e: e["score"], reverse=True)


def _cache_key(model: ZooModel, img) -> str:
    return cache.make_key("cls-v2", model.model_id, img.model_input_hash)


def _cached(model: ZooModel, img) -> list[dict] | None:
    return cache.read_json("classification", _cache_key(model, img))


def _store(model: ZooModel, img, labels: list[dict]) -> None:
    cache.write_json("classification", _cache_key(model, img), labels)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _unique_models(routes: dict[str, list[ZooModel]]) -> list[ZooModel]:
    seen: dict[str, ZooModel] = {}
    for models in routes.values():
        for model in models:
            seen.setdefault(model.key, model)
    return list(seen.values())


def _region_from_text(body_part: str | None) -> str | None:
    """Map a free-text body part (DICOM tag, filename) onto a canonical region."""
    if not body_part:
        return None
    text = body_part.lower()
    aliases = {
        "chest": ("chest", "lung", "thorax"),
        "head": ("head", "brain", "skull"),
        "abdomen": ("abdomen", "abdo", "kidney", "liver"),
        "pelvis": ("pelvis", "hip"),
        "spine": ("spine", "vertebra", "lumbar", "cervical", "thoracic spine"),
        "limb": ("arm", "hand", "wrist", "leg", "knee", "foot", "ankle", "elbow",
                 "shoulder", "femur", "tibia", "extremity", "limb"),
        "breast": ("breast",),
        "neck": ("neck",),
    }
    for region, words in aliases.items():
        if any(word in text for word in words):
            return region
    return None


def _render(screening: dict[str, dict], requested: list[str], errors: list[str]) -> str:
    lines = ["Screening cascade, stage 1 (modality-matched general screeners):"]
    for image_id, entry in screening.items():
        where = f"{entry['modality']}{' / ' + entry['region'] if entry['region'] else ''}"
        if entry["status"] != "screened":
            lines.append(f"- {image_id} [{where}]: NOT SCREENED -- {entry['reason']}.")
            continue
        models = ", ".join(entry["models"])
        if entry["candidates"]:
            rendered = ", ".join(
                f"{c['condition']} {c['score']:.0%}" for c in entry["candidates"]
            )
            lines.append(
                f"- {image_id} [{where}] via {models}: candidate(s) {rendered} "
                f"(overall abnormality {entry['abnormal_score']:.0%})."
            )
        else:
            lines.append(
                f"- {image_id} [{where}] via {models}: no candidate above threshold "
                f"(overall abnormality {entry['abnormal_score']:.0%})."
            )
    if requested:
        lines.append(
            "Conditions named in the referring question/history (specialists will "
            f"check these regardless): {', '.join(requested)}."
        )
    if errors:
        lines.append("Screener errors: " + "; ".join(errors))
    lines.append(
        "Note: screeners only know their own label set and were trained on public "
        "2D research datasets. 'No candidate' means 'nothing this model was trained "
        "to see', not 'normal study'."
    )
    return "\n".join(lines)
