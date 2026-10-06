"""Stage 2 of the screening cascade: re-check candidates with specialist models.

Stage 1 (:mod:`capabilities.image_classification`) asks a broad question --
"what, of the things this screener knows, might be here?" -- and is tuned for
recall. This stage asks the narrow follow-up for each candidate: "a model
trained *only* on this disease, does it agree?"

Which specialist checks which candidate comes from ``model_zoo.RECHECK``: an
NIH-14 "infiltration" triggers both the pneumonia and the tuberculosis
specialists, because both present that way and the screener has no TB label.
Conditions named in the referring question ("rule out pneumonia") are checked
even when the screener was quiet, because a clinician asking is itself a prior.

Each check ends in one of four verdicts:

* ``supported``     -- specialist probability >= 0.70 (per-model, see
  ``ZooModel.support_at``; TorchXRayVision uses its 0.5 operating point)
* ``not_supported`` -- specialist probability <= 0.30 (``refute_at``)
* ``equivocal``     -- in between
* ``unverifiable``  -- no independent specialist exists for this condition on
  this modality/region (the honest answer for most of the body)

The verdicts are evidence for the VLM and the consolidation step, never a
diagnosis on their own.
"""

from __future__ import annotations

from core.logging_utils import get_logger
from capabilities import model_zoo
from capabilities.image_classification import (
    abnormal_score,
    classify_images,
    condition_scores,
)
from capabilities.model_zoo import ZooModel
from capabilities.registry import CapabilityRequest, CapabilityResult, register

_log = get_logger("confirm")

SUPPORT_AT = 0.70
REFUTE_AT = 0.30


@register(
    "specialist_confirmation",
    "Cascade stage 2: re-check each screening candidate with a disease-specific "
    "specialist model and record whether it is supported.",
)
def run(request: CapabilityRequest) -> CapabilityResult:
    artifacts = request.artifacts or {}
    screening: dict[str, dict] = artifacts.get("screening", {}) or {}
    requested: list[str] = list(artifacts.get("requested_conditions", []) or [])
    images = {img.image_id: img for img in request.images()}

    # plan[image_id] = {target: [trigger descriptions]}
    plan: dict[str, dict[str, list[str]]] = {}
    # (image, target) pairs a screener named outright, as opposed to targets
    # reached by fan-out. Only these are worth an "unverifiable" line when no
    # model can check them -- otherwise an "abnormal" chest film would list
    # "brain_tumor: unverifiable".
    named: set[tuple[str, str]] = set()
    for image_id, entry in screening.items():
        if image_id not in images:
            continue
        if entry.get("status") != "screened" and not (requested and entry.get("routing_eligible")):
            continue
        targets: dict[str, list[str]] = {}
        for candidate in entry.get("candidates", []):
            named.add((image_id, candidate["condition"]))
            for target in model_zoo.recheck_targets(candidate["condition"]):
                targets.setdefault(target, []).append(
                    f"{candidate['model']}: {candidate['condition']} {candidate['score']:.0%}"
                )
        for target in requested:
            targets.setdefault(target, []).append("named in referring question/history")
            named.add((image_id, target))
        if targets:
            plan[image_id] = targets

    if not plan:
        return CapabilityResult(
            text=(
                "Screening cascade, stage 2: no candidate needed specialist "
                "re-checking (nothing flagged by stage 1 and nothing named in "
                "the referring question that a specialist covers)."
            ),
            artifacts={"confirmation": {}},
        )

    # Resolve each (image, target) to the specialists that can check it. A
    # model that produced the candidate is not an independent second opinion,
    # so it is excluded -- unless it is the only thing that can speak to a
    # condition the clinician explicitly asked about.
    jobs: dict[str, list[tuple[str, str, list[str]]]] = {}  # model key -> [(image, target, triggers)]
    models: dict[str, ZooModel] = {}
    confirmation: dict[str, list[dict]] = {}
    for image_id, targets in plan.items():
        entry = screening[image_id]
        used = set(entry.get("models", []))
        for target, triggers in targets.items():
            available = model_zoo.specialists_for(target, entry["modality"], entry.get("region"))
            independent = [m for m in available if m.key not in used]
            if not independent:
                if available:
                    reason = (
                        f"only {', '.join(m.key for m in available)} covers {target} here, "
                        "and it already produced the stage-1 result; no independent check"
                    )
                else:
                    reason = (
                        f"no model in the zoo can check {target} on "
                        f"{entry['modality']}{' / ' + entry['region'] if entry.get('region') else ''}"
                    )
                if available or (image_id, target) in named:
                    confirmation.setdefault(image_id, []).append(
                        {"target": target, "triggers": triggers, "verdict": "unverifiable",
                         "reason": reason}
                    )
                continue
            for model in independent:
                models[model.key] = model
                jobs.setdefault(model.key, []).append((image_id, target, triggers))

    errors: list[str] = []
    flagged: list[dict] = []
    for key, items in jobs.items():
        model = models[key]
        targets_for_model = list({image_id for image_id, _, _ in items})
        try:
            scores = classify_images(model, [images[i] for i in targets_for_model])
        except Exception as exc:  # noqa: BLE001 - one specialist failing is not fatal
            _log.warning("specialist %s unavailable: %s", model.model_id, exc)
            errors.append(f"{model.model_id}: {type(exc).__name__}: {exc}")
            for image_id, target, triggers in items:
                confirmation.setdefault(image_id, []).append(
                    {"target": target, "triggers": triggers, "model": key,
                     "model_id": model.model_id, "verdict": "unverifiable",
                     "reason": f"specialist failed to run ({type(exc).__name__})"}
                )
            continue

        for image_id, target, triggers in items:
            labels = scores.get(image_id, [])
            conditions = condition_scores(model, labels)
            probability = conditions.get(target, 0.0)
            # A binary specialist that only knows normal/abnormal still speaks
            # to the target through its abnormality score.
            if target not in conditions and "abnormal" in conditions:
                probability = abnormal_score(model, conditions)
            verdict = _verdict(probability, *model.verdict_bounds(target))
            record = {
                "target": target,
                "triggers": triggers,
                "model": key,
                "model_id": model.model_id,
                "probability": round(probability, 4),
                "verdict": verdict,
                "severity": model_zoo.severity_for(target).value,
                "raw": labels[:4],
            }
            confirmation.setdefault(image_id, []).append(record)
            if verdict == "supported":
                flagged.append({"image_id": image_id, "label": target,
                                "score": record["probability"], "severity": record["severity"],
                                "model": key, "stage": "specialist"})

    return CapabilityResult(
        text=_render(confirmation, errors),
        artifacts={
            "confirmation": confirmation,
            "_models_used": {f"specialist:{r['model']}": r["model_id"]
                             for records in confirmation.values() for r in records
                             if r.get("verdict") != "unverifiable" and r.get("model_id")},
            "flagged": flagged,
            **({"confirm_errors": errors} if errors else {}),
        },
    )


def _verdict(probability: float, support_at: float = SUPPORT_AT, refute_at: float = REFUTE_AT) -> str:
    if probability >= support_at:
        return "supported"
    if probability <= refute_at:
        return "not_supported"
    return "equivocal"


def _render(confirmation: dict[str, list[dict]], errors: list[str]) -> str:
    lines = ["Screening cascade, stage 2 (specialist re-check of stage-1 candidates):"]
    for image_id, records in confirmation.items():
        for r in records:
            trigger = "; ".join(r.get("triggers", []))
            if r["verdict"] == "unverifiable":
                lines.append(f"- {image_id}: {r['target']} UNVERIFIABLE -- {r['reason']} (trigger: {trigger}).")
            else:
                lines.append(
                    f"- {image_id}: {r['target']} {r['verdict'].upper().replace('_', ' ')} "
                    f"by {r['model']} ({r['probability']:.0%}; trigger: {trigger})."
                )
    if errors:
        lines.append("Specialist errors: " + "; ".join(errors))
    lines.append(
        "Interpretation: 'supported' means two differently-trained models agree; "
        "it is still a 2D single-image research model, not a diagnosis. "
        "'Not supported' lowers, but does not remove, suspicion."
    )
    return "\n".join(lines)
