"""Zero-shot modality and body-region triage.

Exported studies often arrive as ``IMG_0042.jpg`` with no DICOM header and no
useful filename. Triage decides which modality and which body region an image
shows, because everything downstream is routed on that answer: the screening
cascade picks its disease models by modality+region, and the VLM prompt says
what it is looking at.

Two CLIP models, two questions
------------------------------
1. **Is this a medical image at all?** -- general CLIP
   (``MODELS.nonmedical_clip``). It has seen millions of everyday photos and is
   good at rejecting them. PubMedCLIP is not: it called a holiday photo "MRI".
2. **Which modality and region?** -- PubMedCLIP (``MODELS.modality_clip``),
   CLIP fine-tuned on radiology figures. General CLIP labelled 47 of 48 brain
   MRIs as CT; PubMedCLIP got 47 right.

Several phrasings per class are scored and summed per class (a light prompt
ensemble), which was worth +4 points over one phrase per class.

Measured on 241 public images (X-ray chest/limb, CT chest/head, MRI brain,
breast ultrasound, everyday photos): modality 96%, region 95%. Breast
ultrasound is the weak class (~50%) -- see ``docs/SCREENING_CASCADE.md``.

Header and filename evidence
----------------------------
A DICOM ``Modality`` tag is authoritative and is never overridden. A filename
hint ("ct.jpg") is a prior: it stands unless CLIP disagrees with >= 80%
confidence, in which case CLIP wins and a warning records the conflict.
"""

from __future__ import annotations

from config import MODELS, RUNTIME
from core import cache
from core.logging_utils import get_logger, timed
from capabilities.hf_backend import (
    load_pipeline,
    prefer_local,
    record_model,
    run_local,
)
from capabilities.registry import CapabilityRequest, CapabilityResult, register
from models import Modality

_log = get_logger("triage")

# Several phrasings per class; scores are summed per class. Phrasing matters:
# describing what the image *looks like* beats naming the modality.
_MODALITY_PROMPTS: dict[str, Modality] = {
    "a chest X-ray radiograph": Modality.XRAY,
    "an X-ray radiograph of bones, a hand, arm, leg, knee or foot": Modality.XRAY,
    "a CT scan slice, with grey soft tissue and bright white bone": Modality.CT,
    "an axial CT scan of the head showing the skull and brain": Modality.CT,
    "a CT scan of the chest or abdomen": Modality.CT,
    "an MRI scan of the brain": Modality.MRI,
    "an MRI scan of the spine, knee or abdomen": Modality.MRI,
    "an ultrasound image with a fan-shaped field of view": Modality.ULTRASOUND,
    "a grainy greyscale ultrasound sonogram of soft tissue": Modality.ULTRASOUND,
    "a breast ultrasound showing a dark hypoechoic lesion": Modality.ULTRASOUND,
    "a mammogram of breast tissue": Modality.MAMMOGRAPHY,
    "a PET scan with coloured metabolic uptake": Modality.PET,
    "a stained histopathology microscope slide": Modality.PATHOLOGY,
    "a colour photograph of people, animals, objects or a scene": Modality.UNKNOWN,
    "a document, chart or screenshot": Modality.UNKNOWN,
}

# Canonical regions match capabilities.model_zoo.REGIONS.
_REGION_PROMPTS: dict[str, str] = {
    "a chest X-ray or chest CT showing the lungs and heart": "chest",
    "an image of the lungs": "chest",
    "an image of the head, skull or brain": "head",
    "a brain scan": "head",
    "an image of the abdomen, liver or kidneys": "abdomen",
    "an image of the pelvis and hips": "pelvis",
    "an image of the spine or vertebrae": "spine",
    "an image of an arm, hand, wrist, leg, knee, ankle or foot": "limb",
    "an image of the breast": "breast",
    "an image of the neck": "neck",
}

_GATE_PROMPTS: dict[str, bool] = {  # True = medical
    "a medical scan or radiology image": True,
    "a microscope slide": True,
    "an everyday colour photograph": False,
    "a document, chart or screenshot": False,
}

_REGION_TEXT = {
    "chest": "chest",
    "head": "head and brain",
    "abdomen": "abdomen",
    "pelvis": "pelvis and hips",
    "spine": "spine",
    "limb": "limb",
    "breast": "breast",
    "neck": "neck",
}

# Below this the label is a coin flip; say so rather than inventing certainty.
_CONFIDENCE_FLOOR = 0.35
# A filename hint is overridden only by a confident disagreement.
_OVERRIDE_HINT_AT = 0.80
# Gate: probability of "not medical" above which an image is excluded.
_NON_MEDICAL_AT = 0.5


@register(
    "modality_triage",
    "Identify imaging modality and body region, and flag non-medical images.",
)
def run(request: CapabilityRequest) -> CapabilityResult:
    images = request.images()
    if not images:
        return CapabilityResult(text="No images supplied for triage.")

    per_image: dict[str, dict] = {}
    warnings: list[str] = []

    try:
        clip = _classify(images)
    except Exception as exc:  # noqa: BLE001 - triage is best-effort
        _log.warning("CLIP triage unavailable (%s); using header/filename only", exc)
        clip = {}
        warnings.append(f"Zero-shot triage unavailable ({type(exc).__name__}: {exc}).")

    for img in images:
        guess = clip.get(img.image_id)
        per_image[img.image_id] = _decide(img, guess, warnings)

    # Write results back onto the study so every later node sees them.
    if request.study is not None:
        by_id = {img.image_id: img for img in request.study.images}
        for image_id, info in per_image.items():
            target = by_id.get(image_id)
            if target is None:
                continue
            try:
                target.modality = Modality(info["modality"])
            except ValueError:
                target.modality = Modality.UNKNOWN
            if info.get("body_part") and not target.body_part:
                target.body_part = info["body_part"]
            for warning in info.get("warnings", []):
                if warning not in target.warnings:
                    target.warnings.append(warning)

    lines = ["Imaging triage:"]
    for image_id, info in per_image.items():
        confidence = info.get("confidence") or 0.0
        body = f", {info['region']}" if info.get("region") else ""
        lines.append(
            f"- {image_id}: {info['modality']}{body} "
            f"(confidence {confidence:.0%}, via {info['source']})"
        )
    non_medical = [i for i, info in per_image.items() if info.get("non_medical")]
    if non_medical:
        lines.append(
            "WARNING: these files do not appear to be medical images and should "
            f"be excluded from interpretation: {', '.join(non_medical)}"
        )
    lines.extend(f"Note: {w}" for w in warnings)

    return CapabilityResult(
        text="\n".join(lines),
        artifacts={"triage": per_image, "non_medical": non_medical},
    )


def _decide(img, guess: dict | None, warnings: list[str]) -> dict:
    """Combine DICOM header, filename hint and CLIP into one routing decision."""
    from capabilities.image_classification import _region_from_text

    prior = img.modality
    info: dict = {
        "modality": prior.value,
        "region": _region_from_text(img.body_part),
        "body_part": img.body_part,
        "source": "dicom-header" if img.is_dicom and prior != Modality.UNKNOWN else "filename-hint",
        "confidence": 1.0 if img.is_dicom and prior != Modality.UNKNOWN else 0.6,
        "non_medical": False,
        "warnings": [],
    }
    if prior == Modality.UNKNOWN:
        info.update(source="none", confidence=0.0)
    if guess is None:
        return info

    info["clip"] = guess
    # Without the gate model, PubMedCLIP's own "photo/document" classes are a
    # weaker fallback signal.
    non_medical_score = guess["non_medical_score"] or (
        guess["modality_score"] if guess["modality"] == Modality.UNKNOWN.value else 0.0
    )
    if non_medical_score >= _NON_MEDICAL_AT and not img.is_dicom:
        info.update(
            modality=Modality.UNKNOWN.value,
            region=None,
            body_part=None,
            source=f"gate:{MODELS.nonmedical_clip}",
            confidence=round(non_medical_score, 3),
            non_medical=True,
        )
        return info

    clip_modality = guess["modality"]
    clip_conf = guess["modality_score"]
    header_authoritative = img.is_dicom and prior != Modality.UNKNOWN

    if header_authoritative:
        pass  # never second-guess the scanner
    elif prior == Modality.UNKNOWN:
        if clip_conf >= _CONFIDENCE_FLOOR and clip_modality != Modality.UNKNOWN.value:
            info.update(modality=clip_modality, source=f"clip:{MODELS.modality_clip}",
                        confidence=round(clip_conf, 3))
        else:
            info.update(confidence=round(clip_conf, 3), source=f"clip:{MODELS.modality_clip}")
    elif clip_modality != prior.value and clip_conf >= _OVERRIDE_HINT_AT:
        message = (
            f"{img.image_id}: filename suggests {prior.value} but the image looks like "
            f"{clip_modality} ({clip_conf:.0%}); using {clip_modality}."
        )
        warnings.append(message)
        info["warnings"].append(message)
        info.update(modality=clip_modality, source=f"clip:{MODELS.modality_clip} (overrode filename)",
                    confidence=round(clip_conf, 3))
    elif clip_modality == prior.value:
        info["confidence"] = round(max(info["confidence"], clip_conf), 3)

    # Region: header/filename text first, CLIP when nothing better is known.
    if not info["region"] and guess.get("region") and guess["region_score"] >= _CONFIDENCE_FLOOR:
        info["region"] = guess["region"]
        info["body_part"] = info["body_part"] or _REGION_TEXT.get(guess["region"])
    return info


def _classify(images: list) -> dict[str, dict]:
    """Return ``{image_id: {modality, modality_score, region, region_score, non_medical_score}}``."""
    out: dict[str, dict] = {}
    pending = []
    for img in images:
        hit = cache.read_json("triage", _cache_key(img))
        if hit is None:
            pending.append(img)
        else:
            out[img.image_id] = hit
    if not pending:
        return out

    if prefer_local(MODELS.modality_clip, size_hint_gb=0.65):
        computed = _classify_local(pending)
    else:
        computed = _classify_remote(pending)

    for img in pending:
        if img.image_id in computed:
            cache.write_json("triage", _cache_key(img), computed[img.image_id])
    out.update(computed)
    return out


def _cache_key(img) -> str:
    return cache.make_key(
        "triage-v2", MODELS.modality_clip, MODELS.nonmedical_clip, img.content_hash
    )


def _aggregate(scores, mapping: dict) -> dict:
    """Sum prompt-level probabilities into their class."""
    totals: dict = {}
    for entry in scores:
        label = entry["label"] if isinstance(entry, dict) else entry.label
        score = float(entry["score"] if isinstance(entry, dict) else entry.score)
        if label not in mapping:
            continue
        key = mapping[label]
        totals[key] = totals.get(key, 0.0) + score
    return totals


def _summarise(modality_scores, region_scores, gate_scores) -> dict:
    modality = _aggregate(modality_scores, _MODALITY_PROMPTS)
    region = _aggregate(region_scores, _REGION_PROMPTS) if region_scores else {}
    gate = _aggregate(gate_scores, _GATE_PROMPTS) if gate_scores else {}
    top_modality = max(modality, key=modality.get)
    top_region = max(region, key=region.get) if region else None
    return {
        "modality": top_modality.value,
        "modality_score": round(modality[top_modality], 4),
        "modality_scores": {k.value: round(v, 4) for k, v in modality.items()},
        "region": top_region,
        "region_score": round(region.get(top_region, 0.0), 4) if top_region else 0.0,
        "non_medical_score": round(gate.get(False, 0.0), 4),
    }


def _classify_local(images: list) -> dict[str, dict]:
    clip = load_pipeline("zero-shot-image-classification", MODELS.modality_clip)
    record_model("modality_triage", MODELS.modality_clip)

    from ingest.preprocess import load_pil

    modality_labels = list(_MODALITY_PROMPTS)
    region_labels = list(_REGION_PROMPTS)
    gate_labels = list(_GATE_PROMPTS)
    raw: dict[str, list] = {img.image_id: [None, None, None] for img in images}

    def _batched(fn):
        for start in range(0, len(images), RUNTIME.batch_size):
            batch = images[start : start + RUNTIME.batch_size]
            pil_batch = [load_pil(img.path_for_model) for img in batch]
            results = fn(pil_batch)
            if batch and results and isinstance(results[0], dict):
                results = [results]
            yield batch, results

    with timed(_log, f"PubMedCLIP triage of {len(images)} image(s)"):
        for slot, labels in ((0, modality_labels), (1, region_labels)):
            for batch, results in _batched(
                lambda pil, labels=labels: run_local(
                    lambda: clip(pil, candidate_labels=labels), label="clip-triage"
                )
            ):
                for img, result in zip(batch, results):
                    raw[img.image_id][slot] = result

    try:
        gate = load_pipeline("zero-shot-image-classification", MODELS.nonmedical_clip)
        record_model("nonmedical_gate", MODELS.nonmedical_clip)
        with timed(_log, f"non-medical gate on {len(images)} image(s)"):
            for batch, results in _batched(
                lambda pil: run_local(
                    lambda: gate(pil, candidate_labels=gate_labels), label="clip-gate"
                )
            ):
                for img, result in zip(batch, results):
                    raw[img.image_id][2] = result
    except Exception as exc:  # noqa: BLE001 - the gate is a refinement
        _log.warning("non-medical gate unavailable: %s", exc)

    return {
        image_id: _summarise(modality, region, gate)
        for image_id, (modality, region, gate) in raw.items()
    }


def _classify_remote(images: list) -> dict[str, dict]:
    """Modality only, through the hosted Inference API (region needs local)."""
    from capabilities.hf_backend import inference_client
    from core.retry import with_retry

    client = inference_client(MODELS.modality_clip)
    record_model("modality_triage", f"{MODELS.modality_clip} (hosted)")
    modality_labels = list(_MODALITY_PROMPTS)
    out: dict[str, dict] = {}

    for img in images:
        def _call(image=img):
            return client.zero_shot_image_classification(
                image.path_for_model, candidate_labels=modality_labels
            )

        scores = with_retry(
            _call,
            attempts=RUNTIME.max_retries,
            base_delay=RUNTIME.retry_base_delay,
            label="clip-remote",
        )
        out[img.image_id] = _summarise(scores, None, None)
    return out
