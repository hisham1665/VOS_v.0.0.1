"""The screening cascade: routing, candidate generation, specialist re-check.

No weights are loaded: ``classify_images`` is stubbed per model key, so what is
under test is routing and evidence handling -- the part that used to send CT
slices to a chest-X-ray pneumonia model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabilities.registry import CapabilityRequest
from models import Modality, Study, StudyImage, PatientContext


def _image(image_id: str, modality: Modality, *, body_part=None, is_dicom=False) -> StudyImage:
    return StudyImage(
        image_id=image_id,
        source_path=f"/tmp/{image_id}",
        content_hash=f"h-{image_id}",
        modality=modality,
        body_part=body_part,
        is_dicom=is_dicom,
    )


def _study(*images: StudyImage, question: str | None = None) -> Study:
    return Study(
        study_id="s",
        source_dir="/tmp/s",
        images=list(images),
        patient=PatientContext(referring_question=question),
    )


def _triage(image_id: str, modality: str, region: str | None, confidence: float = 0.9) -> dict:
    return {image_id: {"modality": modality, "region": region, "confidence": confidence}}


@pytest.fixture
def stub_classifier(monkeypatch):
    """Install fixed label probabilities per model key; record what ran."""
    calls: list[tuple[str, list[str]]] = []

    def install(outputs: dict[str, list[dict]]):
        def fake(model, images):
            calls.append((model.key, [i.image_id for i in images]))
            if model.key not in outputs:
                raise AssertionError(f"unexpected model routed: {model.key}")
            return {img.image_id: outputs[model.key] for img in images}

        monkeypatch.setattr("capabilities.image_classification.classify_images", fake)
        monkeypatch.setattr("capabilities.specialist_confirmation.classify_images", fake)
        return calls

    return install


def _xrv(**scores) -> list[dict]:
    """A TorchXRayVision-style multi-label output (0.5 = operating point)."""
    return [{"label": label.replace("_", " ").title(), "score": score} for label, score in scores.items()]


# --------------------------------------------------------------------------
# Model zoo
# --------------------------------------------------------------------------
class TestModelZoo:
    def test_chest_xray_routes_to_chest_screener_only(self):
        from capabilities.model_zoo import screeners_for

        keys = {m.key for m in screeners_for(Modality.XRAY, "chest")}
        assert keys == {"cxr_nih14"}

    @pytest.mark.parametrize(
        "modality,region,expected",
        [
            (Modality.CT, "head", {"ct_head_hemorrhage"}),
            (Modality.CT, "chest", {"ct_chest_lung_cancer"}),
            (Modality.MRI, "head", {"mri_brain_tumor"}),
            (Modality.XRAY, "limb", set()),  # every fracture model failed benchmark
            (Modality.CT, "abdomen", set()),  # kidney models failed benchmark
            (Modality.CT, None, set()),  # region unknown: route nowhere
            (Modality.ULTRASOUND, "abdomen", set()),  # no ultrasound model
        ],
    )
    def test_routing_is_modality_and_region_specific(self, modality, region, expected):
        from capabilities.model_zoo import screeners_for

        assert {m.key for m in screeners_for(modality, region)} == expected

    def test_no_chest_xray_model_ever_routes_to_ct(self):
        from capabilities.model_zoo import ZOO

        for model in ZOO.values():
            if Modality.CT.value in model.modalities:
                assert Modality.XRAY.value not in model.modalities

    def test_specialists_found_by_condition(self):
        from capabilities.model_zoo import specialists_for

        assert [m.key for m in specialists_for("pneumonia", Modality.XRAY, "chest")] == [
            "cxr_pneumonia", "cxr_xrv_all"]
        assert [m.key for m in specialists_for("pneumothorax", Modality.XRAY, "chest")] == ["cxr_xrv_all"]
        assert specialists_for("pneumonia", Modality.CT, "chest") == []

    def test_referring_question_names_conditions(self):
        from capabilities.model_zoo import requested_conditions

        assert requested_conditions("rule out pneumonia", None) == ["pneumonia"]
        assert "fracture" in requested_conditions("fall, ?fracture wrist")
        assert requested_conditions("abdominal pain") == []

    def test_every_label_maps_into_the_shared_vocabulary(self):
        from capabilities.model_zoo import CONDITION_SEVERITY, ZOO

        for model in ZOO.values():
            for condition in model.label_map.values():
                assert condition in CONDITION_SEVERITY, (model.key, condition)
            for target in model.targets:
                assert target in CONDITION_SEVERITY, (model.key, target)

    def test_json_override_adds_and_disables(self, tmp_path, monkeypatch):
        import importlib
        import json

        override = tmp_path / "zoo.json"
        override.write_text(json.dumps([
            {"key": "cxr_pneumonia", "enabled": False},
            {"key": "us_breast", "model_id": "org/us", "role": "screener",
             "modalities": ["Ultrasound"], "regions": ["breast"],
             "targets": ["mass"], "label_map": {"Malignant": "mass", "Benign": "normal"}},
        ]), encoding="utf-8")
        monkeypatch.setenv("MAOS_MODEL_ZOO", str(override))
        import capabilities.model_zoo as zoo

        reloaded = importlib.reload(zoo)
        try:
            assert not reloaded.ZOO["cxr_pneumonia"].enabled
            assert reloaded.ZOO["us_breast"].label_map == {"malignant": "mass", "benign": "normal"}
            assert [m.key for m in reloaded.screeners_for(Modality.ULTRASOUND, "breast")] == ["us_breast"]
        finally:
            monkeypatch.delenv("MAOS_MODEL_ZOO")
            importlib.reload(zoo)


class TestScoreInterpretation:
    def test_single_label_subtypes_sum_into_one_condition(self):
        from capabilities.image_classification import condition_scores
        from capabilities.model_zoo import ZOO

        model = ZOO["ct_head_hemorrhage"]
        scores = condition_scores(model, [
            {"label": "subdural", "score": 0.3},
            {"label": "epidural", "score": 0.25},
            {"label": "normal", "score": 0.45},
        ])
        assert scores["intracranial_hemorrhage"] == pytest.approx(0.55)

    def test_multi_label_takes_max_and_ignores_no_finding(self):
        from capabilities.image_classification import abnormal_score, condition_scores
        from capabilities.model_zoo import ZOO

        model = ZOO["cxr_nih14"]
        conditions = condition_scores(model, [
            {"label": "No Finding", "score": 0.9},
            {"label": "Effusion", "score": 0.4},
            {"label": "Mass", "score": 0.1},
        ])
        assert abnormal_score(model, conditions) == pytest.approx(0.4)


# --------------------------------------------------------------------------
# Stage 1
# --------------------------------------------------------------------------
class TestScreeningStage:
    def test_ct_slice_never_reaches_a_chest_xray_model(self, stub_classifier):
        """The original bug: CT read through the pneumonia X-ray model."""
        from capabilities.image_classification import run

        calls = stub_classifier({"ct_head_hemorrhage": [
            {"label": "normal", "score": 0.9}, {"label": "subdural", "score": 0.1}]})
        study = _study(_image("ct.jpg", Modality.CT))
        result = run(CapabilityRequest(
            instruction="screen", study=study, image_ids=["ct.jpg"],
            artifacts={"triage": _triage("ct.jpg", "CT", "head")},
        ))
        assert calls == [("ct_head_hemorrhage", ["ct.jpg"])]
        assert result.artifacts["screening"]["ct.jpg"]["candidates"] == []

    def test_candidates_above_threshold_are_reported(self, stub_classifier):
        from capabilities.image_classification import run

        stub_classifier({"cxr_nih14": [
            {"label": "Infiltration", "score": 0.62},
            {"label": "No Finding", "score": 0.2},
            {"label": "Mass", "score": 0.01},
        ]})
        study = _study(_image("cxr.png", Modality.XRAY))
        result = run(CapabilityRequest(
            instruction="screen", study=study, image_ids=["cxr.png"],
            artifacts={"triage": _triage("cxr.png", "X-ray", "chest")},
        ))
        entry = result.artifacts["screening"]["cxr.png"]
        assert entry["status"] == "screened"
        assert [c["condition"] for c in entry["candidates"]] == ["infiltration"]

    @pytest.mark.parametrize(
        "artifacts,modality,reason",
        [
            ({"triage": _triage("x.png", "X-ray", "chest", 0.2)}, Modality.XRAY, "uncertain"),
            ({"non_medical": ["x.png"]}, Modality.XRAY, "not a medical image"),
            ({}, Modality.UNKNOWN, "could not be identified"),
        ],
    )
    def test_unroutable_images_are_skipped_with_a_reason(
        self, stub_classifier, artifacts, modality, reason
    ):
        from capabilities.image_classification import run

        calls = stub_classifier({})
        study = _study(_image("x.png", modality))
        result = run(CapabilityRequest(
            instruction="screen", study=study, image_ids=["x.png"], artifacts=artifacts,
        ))
        assert calls == []
        assert reason in result.artifacts["screening"]["x.png"]["reason"]

    def test_diffuse_abnormality_becomes_an_abnormal_candidate(self, stub_classifier):
        """No single label clears threshold, but the film is not normal: fan out."""
        from capabilities.image_classification import run

        stub_classifier({"cxr_nih14": [
            {"label": "Infiltration", "score": 0.22},
            {"label": "Effusion", "score": 0.18},
            {"label": "No Finding", "score": 0.5},
        ]})
        study = _study(_image("cxr.png", Modality.XRAY))
        result = run(CapabilityRequest(
            instruction="screen", study=study, image_ids=["cxr.png"],
            artifacts={"triage": _triage("cxr.png", "X-ray", "chest")},
        ))
        (candidate,) = result.artifacts["screening"]["cxr.png"]["candidates"]
        assert candidate["condition"] == "abnormal"

    def test_abnormal_candidate_runs_every_applicable_specialist(self, stub_classifier):
        from capabilities.specialist_confirmation import run

        normal = [{"label": "NORMAL", "score": 0.9}, {"label": "PNEUMONIA", "score": 0.1}]
        calls = stub_classifier({
            "cxr_pneumonia": normal,
            "cxr_tuberculosis": [{"label": "NORMAL", "score": 0.9}, {"label": "TUBERCULOSIS", "score": 0.1}],
            "cxr_xrv_all": _xrv(pneumonia=0.1, pneumothorax=0.1, effusion=0.1, fracture=0.1),
        })
        study = _study(_image("cxr.png", Modality.XRAY))
        run(CapabilityRequest(
            instruction="confirm", study=study, image_ids=["cxr.png"],
            artifacts={"screening": _screened("cxr.png", "X-ray", "chest", ["cxr_nih14"],
                                              [("abnormal", 0.22)])},
        ))
        assert {key for key, _ in calls} == {"cxr_pneumonia", "cxr_tuberculosis", "cxr_xrv_all"}

    def test_screener_failure_degrades_instead_of_raising(self, monkeypatch):
        from capabilities.image_classification import run

        def boom(model, images):
            raise RuntimeError("weights missing")

        monkeypatch.setattr("capabilities.image_classification.classify_images", boom)
        study = _study(_image("cxr.png", Modality.XRAY))
        result = run(CapabilityRequest(
            instruction="screen", study=study, image_ids=["cxr.png"],
            artifacts={"triage": _triage("cxr.png", "X-ray", "chest")},
        ))
        assert result.artifacts["screening"]["cxr.png"]["status"] == "skipped"
        assert "weights missing" in result.artifacts["screen_errors"][0]


# --------------------------------------------------------------------------
# Stage 2
# --------------------------------------------------------------------------
def _screened(image_id: str, modality: str, region: str, models, candidates) -> dict:
    return {image_id: {
        "modality": modality, "region": region, "status": "screened", "reason": "",
        "models": models, "abnormal_score": 0.6,
        "candidates": [
            {"condition": c, "score": s, "model": models[0]} for c, s in candidates
        ],
    }}


class TestSpecialistStage:
    def test_infiltration_triggers_pneumonia_and_tb_specialists(self, stub_classifier):
        from capabilities.specialist_confirmation import run

        calls = stub_classifier({
            "cxr_pneumonia": [{"label": "PNEUMONIA", "score": 0.91}, {"label": "NORMAL", "score": 0.09}],
            "cxr_tuberculosis": [{"label": "NORMAL", "score": 0.88}, {"label": "TUBERCULOSIS", "score": 0.12}],
            "cxr_xrv_all": _xrv(pneumonia=0.2, consolidation=0.62),
        })
        study = _study(_image("cxr.png", Modality.XRAY))
        result = run(CapabilityRequest(
            instruction="confirm", study=study, image_ids=["cxr.png"],
            artifacts={"screening": _screened("cxr.png", "X-ray", "chest", ["cxr_nih14"],
                                              [("infiltration", 0.62)])},
        ))
        verdicts = {(r["target"], r["model"]): r["verdict"]
                    for r in result.artifacts["confirmation"]["cxr.png"]}
        assert verdicts == {
            ("pneumonia", "cxr_pneumonia"): "supported",
            # xrv consolidation folds into pneumonia; 0.62 clears its 0.5 bar
            ("pneumonia", "cxr_xrv_all"): "supported",
            ("tuberculosis", "cxr_tuberculosis"): "not_supported",
        }
        assert {key for key, _ in calls} == {"cxr_pneumonia", "cxr_tuberculosis", "cxr_xrv_all"}
        flagged = result.artifacts["flagged"]
        assert {(f["label"], f["model"]) for f in flagged} == {
            ("pneumonia", "cxr_pneumonia"), ("pneumonia", "cxr_xrv_all")}

    def test_requested_condition_is_checked_even_when_screener_is_quiet(self, stub_classifier):
        from capabilities.specialist_confirmation import run

        stub_classifier({
            "cxr_pneumonia": [{"label": "PNEUMONIA", "score": 0.5}, {"label": "NORMAL", "score": 0.5}],
            "cxr_xrv_all": _xrv(pneumonia=0.4, consolidation=0.3),
        })
        study = _study(_image("cxr.png", Modality.XRAY))
        result = run(CapabilityRequest(
            instruction="confirm", study=study, image_ids=["cxr.png"],
            artifacts={
                "screening": _screened("cxr.png", "X-ray", "chest", ["cxr_nih14"], []),
                "requested_conditions": ["pneumonia"],
            },
        ))
        records = result.artifacts["confirmation"]["cxr.png"]
        assert {r["verdict"] for r in records} == {"equivocal"}

    def test_screener_is_not_its_own_second_opinion(self, stub_classifier):
        """Lung cancer on CT has one model; re-running it is fake corroboration."""
        from capabilities.specialist_confirmation import run

        calls = stub_classifier({})
        study = _study(_image("chest.png", Modality.CT))
        result = run(CapabilityRequest(
            instruction="confirm", study=study, image_ids=["chest.png"],
            artifacts={"screening": _screened("chest.png", "CT", "chest", ["ct_chest_lung_cancer"],
                                              [("lung_cancer", 0.6)])},
        ))
        assert calls == []
        (record,) = result.artifacts["confirmation"]["chest.png"]
        assert record["verdict"] == "unverifiable"
        assert "no independent check" in record["reason"]

    def test_disabled_models_are_never_routed(self):
        from capabilities.model_zoo import ZOO, screeners_for, specialists_for

        disabled = {k for k, m in ZOO.items() if not m.enabled}
        assert {"cxr_pneumothorax", "msk_fracture", "ct_kidney_tumor"} <= disabled
        assert "cxr_pneumothorax" not in {
            m.key for m in specialists_for("pneumothorax", Modality.XRAY, "chest")}
        assert screeners_for(Modality.XRAY, "limb") == []

    def test_pneumothorax_needs_a_stricter_bar_than_pneumonia(self, stub_classifier):
        """xrv pneumothorax is not separable from pneumonia: it may refute, never confirm."""
        from capabilities.specialist_confirmation import run

        stub_classifier({"cxr_xrv_all": _xrv(pneumothorax=0.99, pneumonia=0.6)})
        study = _study(_image("cxr.png", Modality.XRAY))
        result = run(CapabilityRequest(
            instruction="confirm", study=study, image_ids=["cxr.png"],
            artifacts={"screening": _screened("cxr.png", "X-ray", "chest", ["cxr_nih14"],
                                              [("pneumothorax", 0.7)])},
        ))
        (record,) = result.artifacts["confirmation"]["cxr.png"]
        assert (record["target"], record["verdict"]) == ("pneumothorax", "equivocal")

    def test_xrv_is_never_routed_off_chest(self):
        from capabilities.model_zoo import ZOO

        xrv = ZOO["cxr_xrv_all"]
        assert xrv.applies_to(Modality.XRAY, "chest")
        assert not xrv.applies_to(Modality.XRAY, "limb")
        assert not xrv.applies_to(Modality.CT, "chest")

    def test_head_ct_haemorrhage_is_cross_checked_by_the_stroke_model(self, stub_classifier):
        from capabilities.specialist_confirmation import run

        calls = stub_classifier({"ct_head_stroke": [
            {"label": "inme var", "score": 0.97}, {"label": "inme yok", "score": 0.03}]})
        study = _study(_image("head.png", Modality.CT))
        result = run(CapabilityRequest(
            instruction="confirm", study=study, image_ids=["head.png"],
            artifacts={"screening": _screened("head.png", "CT", "head", ["ct_head_hemorrhage"],
                                              [("intracranial_hemorrhage", 0.99)])},
        ))
        assert calls == [("ct_head_stroke", ["head.png"])]
        verdicts = {r["target"]: r["verdict"] for r in result.artifacts["confirmation"]["head.png"]}
        assert verdicts == {"intracranial_hemorrhage": "unverifiable", "stroke": "supported"}

    def test_nothing_to_check_is_a_clean_no_op(self, stub_classifier):
        from capabilities.specialist_confirmation import run

        calls = stub_classifier({})
        study = _study(_image("cxr.png", Modality.XRAY))
        result = run(CapabilityRequest(
            instruction="confirm", study=study, image_ids=["cxr.png"],
            artifacts={"screening": _screened("cxr.png", "X-ray", "chest", ["cxr_nih14"], [])},
        ))
        assert calls == []
        assert result.artifacts["confirmation"] == {}


# --------------------------------------------------------------------------
# Triage decisions
# --------------------------------------------------------------------------
def _guess(modality: str, score: float, region="chest", non_medical=0.0) -> dict:
    return {"modality": modality, "modality_score": score, "region": region,
            "region_score": 0.9, "non_medical_score": non_medical}


class TestTriageDecision:
    def test_confident_clip_overrides_a_wrong_filename(self):
        from capabilities.modality_triage import _decide

        warnings: list[str] = []
        info = _decide(_image("chest.png", Modality.XRAY), _guess("CT", 0.92), warnings)
        assert info["modality"] == "CT"
        assert warnings and "filename suggests X-ray" in warnings[0]

    def test_weak_clip_keeps_the_filename_hint(self):
        from capabilities.modality_triage import _decide

        info = _decide(_image("chest.png", Modality.XRAY), _guess("CT", 0.55), [])
        assert info["modality"] == "X-ray"

    def test_dicom_header_is_never_overridden(self):
        from capabilities.modality_triage import _decide

        info = _decide(_image("a.dcm", Modality.MRI, is_dicom=True), _guess("CT", 0.99), [])
        assert info["modality"] == "MRI"
        assert info["confidence"] == 1.0

    def test_gate_marks_photos_as_non_medical(self):
        from capabilities.modality_triage import _decide

        info = _decide(_image("holiday.jpg", Modality.UNKNOWN), _guess("MRI", 0.4, non_medical=0.9), [])
        assert info["non_medical"]
        assert info["modality"] == "Unknown"

    def test_region_comes_from_clip_when_unknown(self):
        from capabilities.modality_triage import _decide

        info = _decide(_image("img.png", Modality.UNKNOWN), _guess("MRI", 0.8, region="head"), [])
        assert (info["modality"], info["region"]) == ("MRI", "head")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("ct.jpg", Modality.CT),
        ("chest_ct.png", Modality.CT),
        ("chest_xray_pa.png", Modality.XRAY),
        ("brain_mri.jpg", Modality.MRI),
        ("abdominal_ultrasound.png", Modality.ULTRASOUND),
        ("doctor.png", Modality.UNKNOWN),
        ("abstract.png", Modality.UNKNOWN),
    ],
)
def test_filename_hints_use_real_token_boundaries(name, expected):
    from ingest.study_loader import _infer_modality

    assert _infer_modality(Path("study") / name, {}) is expected


# --------------------------------------------------------------------------
# Plan shape
# --------------------------------------------------------------------------
class TestCascadePlan:
    def test_cascade_is_sequential_and_precedes_reads(self):
        from agents.planner import build_radiology_plan
        from graph_utils import build_waves

        graph = build_radiology_plan(_study(_image("a.png", Modality.UNKNOWN)))
        wave_of = {n.id: i for i, wave in enumerate(build_waves(graph)) for n in wave}
        assert wave_of["triage"] < wave_of["screen"] < wave_of["confirm"] < wave_of["read0"]

    def test_consolidation_needs_a_narrative_read(self):
        from agents.planner import build_radiology_plan

        graph = build_radiology_plan(_study(_image("a.png", Modality.UNKNOWN)))
        consolidate = graph.by_id()["consolidate"]
        assert {"screen", "confirm"} <= set(consolidate.depends_on)
        assert consolidate.partial_requires == ["read0"]

    def test_executor_skips_join_when_only_cascade_succeeded(self, fake_capability):
        from agents.graph_executor import GraphExecutor
        from capabilities.registry import CapabilityResult
        from models import Graph, Node

        fake_capability("modality_triage", lambda r: CapabilityResult(text="ok"))
        fake_capability("vision", lambda r: (_ for _ in ()).throw(RuntimeError("down")))
        fake_capability("cross_image_synthesis", lambda r: CapabilityResult(text="merged"))
        graph = Graph(job="j", nodes=[
            Node(id="screen", description="s", capability="modality_triage"),
            Node(id="read0", description="r", capability="vision"),
            Node(id="consolidate", description="c", capability="cross_image_synthesis",
                 depends_on=["read0", "screen"], allow_partial=True,
                 partial_requires=["read0"]),
        ])
        GraphExecutor(max_workers=2).run(graph)
        assert graph.by_id()["consolidate"].status == "skipped"


class TestXrvAdapter:
    def test_xrv_models_dispatch_to_the_adapter_and_cache(self, monkeypatch):
        from capabilities import image_classification as ic, xrv_backend
        from capabilities.model_zoo import ZOO

        calls: list[list[str]] = []

        def fake(weights, paths):
            calls.append(paths)
            return [[{"label": "Pneumothorax", "score": 0.2},
                     {"label": "Consolidation", "score": 0.7}] for _ in paths]

        monkeypatch.setattr(xrv_backend, "classify", fake)
        image = _image("cxr.png", Modality.XRAY)
        first = ic.classify_images(ZOO["cxr_xrv_all"], [image])
        second = ic.classify_images(ZOO["cxr_xrv_all"], [image])

        assert len(calls) == 1  # second call served from the disk cache
        assert first == second
        scores = ic.condition_scores(ZOO["cxr_xrv_all"], first["cxr.png"])
        assert scores["pneumonia"] == pytest.approx(0.7)  # consolidation folds in

    def test_missing_library_is_a_backend_error(self, monkeypatch):
        from capabilities import xrv_backend
        from core.errors import BackendError

        monkeypatch.setattr(xrv_backend, "available", lambda: False)
        with pytest.raises(BackendError, match="pip install torchxrayvision"):
            xrv_backend.classify("densenet121-res224-all", ["x.png"])
