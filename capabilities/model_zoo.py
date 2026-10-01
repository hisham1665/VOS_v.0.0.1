"""Which supervised model may look at which image, and what its labels mean.

Why a zoo instead of one classifier
-----------------------------------
The original pipeline ran one binary pneumonia model on every image. That model
has exactly two outputs, NORMAL and PNEUMONIA, so *anything* abnormal -- a CT
slice, an ultrasound, a fracture -- came back as "pneumonia", and the VLM was
then told to treat that as a second opinion.

A classifier is only meaningful on the distribution it was trained on. This
module records, for every checkpoint we use:

* which modality and body region it was trained on (routing),
* whether it is a broad **screener** or a narrow **specialist** (cascade role),
* what each raw label means in a shared condition vocabulary (so the cascade
  can ask "who else can check *pneumonia*?"), and
* an honest note about its training data, surfaced in the report.

The screening cascade (``image_classification`` -> ``specialist_confirmation``)
reads this table; nothing else hard-codes a model id.

Every entry was downloaded and benchmarked against public held-out images
during development -- see ``docs/SCREENING_CASCADE.md`` for the numbers. They
are research checkpoints from the Hugging Face Hub, not cleared devices.

Extending
---------
Point ``MAOS_MODEL_ZOO`` at a JSON file containing a list of entries with the
same fields as :class:`ZooModel`. An entry whose ``key`` matches a built-in one
replaces it; ``"enabled": false`` disables a built-in.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from core.logging_utils import get_logger
from models import Modality, Severity

_log = get_logger("model-zoo")

SCREENER = "screener"
SPECIALIST = "specialist"

# Canonical body regions produced by modality triage. Routing matches on these.
REGIONS = ("chest", "head", "abdomen", "pelvis", "spine", "limb", "breast", "neck")

# --------------------------------------------------------------------------
# Shared condition vocabulary
# --------------------------------------------------------------------------
# Severity is a property of the *condition*, not of whichever model named it,
# so two models that both report "pneumothorax" escalate identically.
CONDITION_SEVERITY: dict[str, Severity] = {
    "normal": Severity.NORMAL,
    "abnormal": Severity.INDETERMINATE,  # a screener said "not normal", nothing more
    "pneumothorax": Severity.CRITICAL,
    "intracranial_hemorrhage": Severity.CRITICAL,
    "stroke": Severity.CRITICAL,
    "pneumonia": Severity.HIGH,
    "tuberculosis": Severity.HIGH,
    "consolidation": Severity.HIGH,
    "effusion": Severity.HIGH,
    "edema": Severity.HIGH,
    "mass": Severity.HIGH,
    "lung_cancer": Severity.HIGH,
    "brain_tumor": Severity.HIGH,
    "kidney_tumor": Severity.HIGH,
    "fracture": Severity.MODERATE,
    "nodule": Severity.MODERATE,
    "infiltration": Severity.MODERATE,
    "fibrosis": Severity.MODERATE,
    "emphysema": Severity.MODERATE,
    "hernia": Severity.MODERATE,
    "dementia": Severity.MODERATE,
    "cardiomegaly": Severity.LOW,
    "atelectasis": Severity.LOW,
    "pleural_thickening": Severity.LOW,
}

# A screener finding of X is worth re-checking with a specialist for Y.
# Example: NIH-14 has no "tuberculosis" label, but TB presents as infiltrates,
# consolidation, nodules or upper-zone fibrosis -- so those trigger the TB model.
RECHECK: dict[str, tuple[str, ...]] = {
    "abnormal": ("pneumonia", "tuberculosis", "pneumothorax", "fracture", "effusion",
                 "brain_tumor", "intracranial_hemorrhage", "stroke",
                 "lung_cancer", "kidney_tumor"),
    "pneumonia": ("pneumonia",),
    "consolidation": ("pneumonia", "tuberculosis"),
    "infiltration": ("pneumonia", "tuberculosis"),
    "edema": ("edema", "pneumonia"),
    "effusion": ("effusion", "pneumonia", "tuberculosis"),
    "nodule": ("nodule", "tuberculosis", "lung_cancer"),
    "mass": ("mass", "tuberculosis", "lung_cancer"),
    "fibrosis": ("tuberculosis",),
    "pneumothorax": ("pneumothorax",),
    "brain_tumor": ("brain_tumor",),
    "atelectasis": ("atelectasis", "pneumonia"),
    "cardiomegaly": ("cardiomegaly",),
    # The stroke model is the only independent head-CT check we have; it also
    # fires on haemorrhage, so it serves as a second "abnormal head CT" opinion.
    "intracranial_hemorrhage": ("intracranial_hemorrhage", "stroke"),
    "stroke": ("stroke", "intracranial_hemorrhage"),
    "fracture": ("fracture",),
    "lung_cancer": ("lung_cancer",),
    "kidney_tumor": ("kidney_tumor",),
}

# Words in the referring question / history that name a condition, so
# "rule out pneumonia" runs the pneumonia specialist even when the screener
# was quiet. Matched as lower-case substrings.
QUESTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "pneumonia": ("pneumonia", "chest infection", "lrti", "consolidation"),
    "tuberculosis": ("tuberculosis", " tb", "tb ", "koch"),
    "pneumothorax": ("pneumothorax", "collapsed lung"),
    "fracture": ("fracture", "broken", "break"),
    "brain_tumor": ("brain tumor", "brain tumour", "glioma", "meningioma", "pituitary"),
    "intracranial_hemorrhage": ("hemorrhage", "haemorrhage", "bleed", "head injury", "trauma"),
    "stroke": ("stroke", "cva", "infarct", "weakness"),
    "lung_cancer": ("lung cancer", "lung mass", "malignancy", "carcinoma"),
    "kidney_tumor": ("kidney tumor", "kidney tumour", "renal mass", "renal tumor"),
    "dementia": ("dementia", "alzheimer", "memory loss", "cognitive"),
}


@dataclass(frozen=True)
class ZooModel:
    """One Hugging Face checkpoint and everything routing needs to know."""

    key: str
    model_id: str
    role: str  # SCREENER | SPECIALIST
    modalities: tuple[str, ...]  # Modality values
    regions: tuple[str, ...]  # canonical regions; empty = any region
    # raw label (lower-case) -> canonical condition. Unlisted labels map to
    # themselves after normalisation, so a new checkpoint still "works".
    label_map: dict[str, str] = field(default_factory=dict)
    # conditions a specialist can confirm or refute
    targets: tuple[str, ...] = ()
    multi_label: bool = False
    # Probability at which a non-normal condition becomes a cascade candidate.
    # Multi-label sigmoid heads are less peaked than softmax, so they get a
    # lower, benchmark-derived value.
    threshold: float = 0.5
    # Screeners only: overall abnormality (1 - P(normal), or the strongest
    # pathology for multi-label) at which the image gets an "abnormal"
    # candidate, which sends it to *every* applicable specialist. This is
    # what catches a film whose individual labels are each below threshold.
    abnormal_threshold: float = 0.5
    # Specialist verdict cut-offs: >= support_at is "supported", <= refute_at
    # is "not supported", in between "equivocal". Per model, because output
    # scales differ (softmax vs operating-point-normalised sigmoid).
    support_at: float = 0.70
    refute_at: float = 0.30
    # Per-target overrides, e.g. a stricter bar for a critical finding the
    # model is only moderately good at: {"pneumothorax": [0.75, 0.30]}.
    target_thresholds: dict[str, tuple[float, float]] = field(default_factory=dict)
    # "transformers" (HF image-classification pipeline) or "torchxrayvision".
    backend: str = "transformers"
    size_gb: float = 0.35
    # Some Hub checkpoints ship weights without a preprocessor config; name a
    # compatible processor (usually the base model's) to load them.
    processor_id: str = ""
    notes: str = ""
    enabled: bool = True

    def verdict_bounds(self, target: str) -> tuple[float, float]:
        bounds = self.target_thresholds.get(target)
        if bounds:
            return float(bounds[0]), float(bounds[1])
        return self.support_at, self.refute_at

    def canonical(self, raw_label: str) -> str:
        lowered = str(raw_label).strip().lower()
        if lowered in self.label_map:
            return self.label_map[lowered]
        return lowered.replace(" ", "_").replace("-", "_")

    def applies_to(self, modality: Modality | str, region: str | None) -> bool:
        value = modality.value if isinstance(modality, Modality) else str(modality)
        if value not in self.modalities:
            return False
        if not self.regions:
            return True
        return region in self.regions


_X, _CT, _MR, _US = (
    Modality.XRAY.value,
    Modality.CT.value,
    Modality.MRI.value,
    Modality.ULTRASOUND.value,
)

_BUILTIN: tuple[ZooModel, ...] = (
    # ------------------------------------------------------------------ X-ray
    ZooModel(
        key="cxr_nih14",
        model_id="Sohaibsoussi/ViT-NIH-Chest-X-ray-dataset-small",
        role=SCREENER,
        modalities=(_X,),
        regions=("chest",),
        multi_label=True,
        threshold=0.4,
        abnormal_threshold=0.15,
        label_map={
            "no finding": "normal",
            "pleural_thickening": "pleural_thickening",
            "effusion": "effusion",
        },
        notes="ViT fine-tuned on a subset of NIH ChestX-ray14 (14 labels + No "
        "Finding, multi-label). Benchmark: abnormal-vs-normal AUC 0.97 on "
        "held-out Kermany films; weak on pneumothorax (5/10 false alarms on "
        "SIIM negatives). NIH labels are NLP-mined and noisy; no TB label.",
    ),
    ZooModel(
        key="cxr_pneumonia",
        model_id="lxyuan/vit-xray-pneumonia-classification",
        role=SPECIALIST,
        modalities=(_X,),
        regions=("chest",),
        targets=("pneumonia",),
        label_map={"normal": "normal", "pneumonia": "pneumonia"},
        size_gb=0.35,
        notes="Binary NORMAL/PNEUMONIA ViT trained on the Kermany paediatric "
        "chest X-ray set (ages 1-5). Benchmark: AUC 1.00 on Kermany test films, "
        "but it also called 7/10 *normal* adult SIIM films pneumonia -- it "
        "detects 'abnormal paediatric-looking film', not pneumonia specifically.",
    ),
    ZooModel(
        key="cxr_tuberculosis",
        model_id="runaksh/chest_xray_tuberculosis_detection",
        role=SPECIALIST,
        modalities=(_X,),
        regions=("chest",),
        targets=("tuberculosis",),
        label_map={"normal": "normal", "tuberculosis": "tuberculosis"},
        processor_id="google/vit-base-patch16-224-in21k",
        notes="Binary NORMAL/TUBERCULOSIS ViT (TB chest X-ray dataset). Ships "
        "without a preprocessor config; loaded with the base ViT processor.",
    ),
    ZooModel(
        key="cxr_xrv_all",
        model_id="densenet121-res224-all",
        backend="torchxrayvision",
        role=SPECIALIST,
        modalities=(_X,),
        regions=("chest",),
        multi_label=True,
        targets=("pneumonia", "pneumothorax", "effusion", "fracture",
                 "edema", "cardiomegaly", "mass", "nodule", "atelectasis"),
        # Consolidation is folded into pneumonia: on paediatric (Kermany)
        # films the xrv Pneumonia head scored only 4/18 pneumonias >= 0.5 while
        # Consolidation scored 18/18 (2/18 normals) -- an adult-trained model
        # sees childhood pneumonia as consolidation.
        label_map={
            "pneumonia": "pneumonia",
            "consolidation": "pneumonia",
            "pneumothorax": "pneumothorax",
            "effusion": "effusion",
            "fracture": "fracture",
            "edema": "edema",
            "cardiomegaly": "cardiomegaly",
            "mass": "mass",
            "nodule": "nodule",
            "atelectasis": "atelectasis",
            "infiltration": "infiltration",
            "emphysema": "emphysema",
            "fibrosis": "fibrosis",
            "pleural_thickening": "pleural_thickening",
            "hernia": "hernia",
            "lung lesion": "nodule",
            "lung opacity": "infiltration",
            "enlarged cardiomediastinum": "cardiomegaly",
        },
        support_at=0.5,
        refute_at=0.25,
        # Operating-point normalisation squeezes "positive" scores into
        # ~0.50-0.55, so these bars come from where the benchmark classes
        # actually separated, not from a generic probability scale:
        # * pneumothorax: true PTX 0.50-0.51, but 4/10 PTX-negative films and
        #   every pneumonia film also scored 0.50-0.54 -> unreachable support
        #   bar; xrv can only *refute* (<= 0.25) or leave it equivocal.
        # * effusion: pneumonia films 0.49-0.73, normals <= 0.35 -> 0.60.
        # * fracture: SIIM films with chest drains sit at 0.50-0.51 -> 0.55.
        target_thresholds={
            "pneumothorax": (1.01, 0.25),
            "effusion": (0.60, 0.25),
            "fracture": (0.55, 0.25),
        },
        size_gb=0.03,
        notes="TorchXRayVision DenseNet-121 trained jointly on NIH, PadChest, "
        "CheXpert, MIMIC-CXR, RSNA, Google, OpenI and Kaggle (18 labels, "
        "outputs normalised so 0.5 = operating point). Benchmark: pneumonia "
        "AUC 0.97 (via Consolidation); agreed with the Kermany pneumonia model "
        "on 16/16 confirmed pneumonias and dissented on 2/5 adult films that "
        "model wrongly called pneumonia. Pneumothorax AUC 0.71 but not separable from "
        "pneumonia films, so it can refute pneumothorax and never confirm it. "
        "Fracture label is rib/clavicle only (3/12 limb fractures) -- chest "
        "films only. Trained on different data from the Kermany models, so "
        "its agreement is real corroboration.",
    ),
    ZooModel(
        key="cxr_pneumothorax",
        model_id="mrm8488/vit-base-patch16-224_finetuned-pneumothorax",
        role=SPECIALIST,
        modalities=(_X,),
        regions=("chest",),
        targets=("pneumothorax",),
        label_map={"normal": "normal", "pneumothorax": "pneumothorax"},
        enabled=False,
        notes="DISABLED after benchmarking: AUC 0.38 on SIIM pneumothorax films "
        "(worse than chance) and it called 12/12 normal films pneumothorax. "
        "Kept here so the gap is visible: pneumothorax is unverifiable.",
    ),
    ZooModel(
        key="msk_fracture",
        model_id="Hemgg/bone-fracture-detection-using-xray",
        role=SCREENER,
        modalities=(_X,),
        regions=("limb", "pelvis", "spine"),
        targets=("fracture",),
        label_map={"fractured": "fracture", "not fractured": "normal"},
        enabled=False,
        notes="DISABLED after benchmarking: it called 24/24 *chest* X-rays "
        "'fractured' at 99%, i.e. it answers 'fractured' to almost anything. "
        "Two alternatives were also rejected: prithivMLmods/Bone-Fracture-"
        "Detection (AUC 0.37) and a MURA abnormality ViT (AUC 0.15). Limb "
        "X-rays therefore go to the VLM only.",
    ),
    # --------------------------------------------------------------------- CT
    ZooModel(
        key="ct_head_hemorrhage",
        model_id="DifeiT/rsna-intracranial-hemorrhage-detection",
        role=SCREENER,
        modalities=(_CT,),
        regions=("head",),
        targets=("intracranial_hemorrhage",),
        label_map={
            "normal": "normal",
            "epidural": "intracranial_hemorrhage",
            "intraparenchymal": "intracranial_hemorrhage",
            "intraventricular": "intracranial_hemorrhage",
            "subarachnoid": "intracranial_hemorrhage",
            "subdural": "intracranial_hemorrhage",
        },
        notes="ViT on single RSNA intracranial-haemorrhage slices (subtype as "
        "single label). Benchmark: AUC 0.88 stroke-vs-normal on TEKNOFEST head "
        "CT, but it scored 6/6 brain-*tumour* CTs as haemorrhage 100% -- treat "
        "its output as 'abnormal head CT', not as a bleed. Expects a brain "
        "window; one slice is not a head CT.",
    ),
    ZooModel(
        key="ct_head_stroke",
        model_id="BTX24/hiera-base-224-in1k-hf-finetuned-stroke-binary",
        role=SPECIALIST,
        modalities=(_CT,),
        regions=("head",),
        targets=("stroke",),
        label_map={"inme var": "stroke", "inme yok": "normal"},
        size_gb=0.2,
        notes="Hiera binary stroke/no-stroke on the Turkish TEKNOFEST-2021 "
        "non-contrast head CT set ('inme var' = stroke present). Benchmark: "
        "AUC 1.00 -- but on its own training dataset, so meaningless -- and it "
        "called 6/6 brain-tumour CTs stroke. Treat as 'abnormal head CT'. Does "
        "not separate ischaemic from haemorrhagic.",
    ),
    ZooModel(
        key="ct_chest_lung_cancer",
        model_id="oohtmeel/swin-tiny-patch4-finetuned-lung-cancer-ct-scans",
        role=SCREENER,
        modalities=(_CT,),
        regions=("chest",),
        targets=("lung_cancer",),
        label_map={"negative": "normal", "positive": "lung_cancer"},
        threshold=0.3,
        size_gb=0.11,
        notes="Swin-tiny binary lung-cancer classifier on axial chest CT "
        "slices. Benchmark: AUC 0.96 cancer-vs-normal, 0/8 false positives, "
        "but only 9/24 cancers above 0.5 (threshold lowered to 0.3). Flags "
        "7/10 head CTs -- chest CT only. No nodule measurement / Lung-RADS.",
    ),
    ZooModel(
        key="ct_kidney_tumor",
        model_id="Rahii123/kidney-disease-classification-v2",
        role=SCREENER,
        modalities=(_CT,),
        regions=("abdomen",),
        targets=("kidney_tumor",),
        label_map={"normal": "normal", "tumor": "kidney_tumor"},
        enabled=False,
        notes="DISABLED after benchmarking: it scored 8/8 *normal chest* CTs "
        "and most head CTs as 'Tumor'. With no abdominal labelled data to "
        "show otherwise, it cannot be trusted on an abdomen. Abdominal CT is "
        "therefore VLM-only (the appendicitis case included).",
    ),
    # -------------------------------------------------------------------- MRI
    ZooModel(
        key="mri_brain_tumor",
        model_id="Devarshi/Brain_Tumor_Classification",
        role=SCREENER,
        modalities=(_MR,),
        regions=("head",),
        targets=("brain_tumor",),
        label_map={
            "no_tumor": "normal",
            "glioma_tumor": "brain_tumor",
            "meningioma_tumor": "brain_tumor",
            "pituitary_tumor": "brain_tumor",
        },
        size_gb=0.11,
        notes="4-class Swin (glioma/meningioma/pituitary/no tumour) on the "
        "Kaggle brain-MRI set. Benchmark: AUC 1.00 on held-out Kaggle test "
        "slices, but it gives ~50% 'tumour' on head CT -- never route CT here. "
        "Stroke, MS, haemorrhage are outside its label set.",
    ),
    ZooModel(
        key="mri_brain_tumor_vit",
        model_id="Hemgg/brain-tumor-classification",
        role=SPECIALIST,
        modalities=(_MR,),
        regions=("head",),
        targets=("brain_tumor",),
        label_map={
            "notumor": "normal",
            "glioma": "brain_tumor",
            "meningioma": "brain_tumor",
            "pituitary": "brain_tumor",
        },
        notes="ViT, same 4 classes. Benchmark AUC 0.99. Trained on the same "
        "Kaggle source as mri_brain_tumor, so agreement between the two is "
        "weaker evidence than agreement across datasets -- their errors are "
        "correlated.",
    ),
    ZooModel(
        key="mri_brain_dementia",
        model_id="evanrsl/resnet-Alzheimer",
        role=SPECIALIST,
        modalities=(_MR,),
        regions=("head",),
        targets=("dementia",),
        label_map={
            "non_demented": "normal",
            "very_mild_demented": "dementia",
            "mild_demented": "dementia",
            "moderate_demented": "dementia",
        },
        size_gb=0.1,
        notes="ResNet on OASIS-derived axial MRI slices. Benchmark: perfect on "
        "its own dataset (likely seen in training), but flagged 3/8 tumour-free "
        "Kaggle MRIs as demented. Only run when the referring question asks "
        "about cognition; one slice is not a dementia diagnosis.",
    ),
)


def _load_overrides() -> dict[str, ZooModel]:
    zoo = {entry.key: entry for entry in _BUILTIN}

    # Legacy single-classifier override keeps old .env files meaningful.
    legacy = os.environ.get("MAOS_CXR_CLASSIFIER", "").strip()
    if legacy and legacy != zoo["cxr_nih14"].model_id:
        base = zoo["cxr_nih14"]
        zoo["cxr_nih14"] = ZooModel(
            **{**{f.name: getattr(base, f.name) for f in fields(ZooModel)},
               "model_id": legacy,
               "notes": f"Overridden by MAOS_CXR_CLASSIFIER ({legacy}).",
               "label_map": {"no finding": "normal", "normal": "normal"}}
        )

    path = os.environ.get("MAOS_MODEL_ZOO", "").strip()
    if not path:
        return zoo
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
        allowed = {f.name for f in fields(ZooModel)}
        for raw in entries:
            data = {k: v for k, v in raw.items() if k in allowed}
            for tuple_field in ("modalities", "regions", "targets"):
                if tuple_field in data:
                    data[tuple_field] = tuple(data[tuple_field])
            if "label_map" in data:
                data["label_map"] = {str(k).lower(): str(v) for k, v in data["label_map"].items()}
            base = zoo.get(data.get("key", ""))
            if base is not None:
                data = {**{f.name: getattr(base, f.name) for f in fields(ZooModel)}, **data}
            zoo[data["key"]] = ZooModel(**data)
    except Exception as exc:  # noqa: BLE001 - a bad override must not kill the run
        _log.warning("ignoring MAOS_MODEL_ZOO=%s: %s", path, exc)
    return zoo


ZOO: dict[str, ZooModel] = _load_overrides()


def screeners_for(modality: Modality | str, region: str | None) -> list[ZooModel]:
    return [
        m for m in ZOO.values()
        if m.enabled and m.role == SCREENER and m.applies_to(modality, region)
    ]


def specialists_for(
    condition: str, modality: Modality | str, region: str | None
) -> list[ZooModel]:
    """Models (any role) that can confirm or refute ``condition`` here."""
    return [
        m for m in ZOO.values()
        if m.enabled and condition in m.targets and m.applies_to(modality, region)
    ]


def recheck_targets(condition: str) -> tuple[str, ...]:
    return RECHECK.get(condition, ())


def severity_for(condition: str) -> Severity:
    return CONDITION_SEVERITY.get(condition, Severity.INDETERMINATE)


def requested_conditions(*texts: str | None) -> list[str]:
    """Conditions the clinician explicitly asked about."""
    haystack = " " + " ".join(t for t in texts if t).lower() + " "
    return [
        condition
        for condition, words in QUESTION_KEYWORDS.items()
        if any(word in haystack for word in words)
    ]


def coverage_table() -> list[dict]:
    """What the zoo can and cannot check, for docs and ``--doctor``."""
    return [
        {
            "key": m.key,
            "model_id": m.model_id,
            "role": m.role,
            "modalities": list(m.modalities),
            "regions": list(m.regions) or ["any"],
            "targets": list(m.targets),
            "enabled": m.enabled,
        }
        for m in ZOO.values()
    ]
