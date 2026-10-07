"""Image format handling and installed vision-model routing regressions."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from capabilities import vision
from capabilities.registry import CapabilityRequest
from config import RUNTIME, SUPPORTED_IMAGE_SUFFIXES
from ingest.study_loader import load_study


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, "_ollama_checked", None)
    monkeypatch.setattr(vision, "_ollama_checked_at", 0.0)
    monkeypatch.setattr(vision, "_ollama_selected_model", None)
    monkeypatch.setattr(vision, "_ollama_details", {})
    monkeypatch.setattr(vision, "_OLLAMA_MODEL_EXPLICIT", False)
    monkeypatch.setattr(vision, "OLLAMA_MODEL", "medgemma1.5:latest")
    previous = RUNTIME.cache_dir
    object.__setattr__(RUNTIME, "cache_dir", tmp_path / "cache")
    yield
    object.__setattr__(RUNTIME, "cache_dir", previous)


def fake_ollama(monkeypatch, models, capabilities):
    import requests

    inspections = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: SimpleNamespace(
        ok=True, json=lambda: {"models": [{"name": name} for name in models]}))
    def show(url, *, json, timeout):
        inspections.append(json["model"])
        return SimpleNamespace(ok=True, json=lambda: {"capabilities": capabilities.get(json["model"], [])})
    monkeypatch.setattr(requests, "post", show)
    return inspections


def test_installed_medgemma_is_selected_and_used_in_image_request(monkeypatch, tmp_path):
    fake_ollama(monkeypatch, ["medgemma1.5:latest", "codellama:7b"], {"medgemma1.5:latest": ["completion", "vision"]})
    monkeypatch.setattr(vision, "VLM_BACKEND", "auto")
    monkeypatch.setattr(vision, "hf_api_available", lambda: pytest.fail("Installed local vision model must win over HF"))
    calls = []
    monkeypatch.setattr(vision, "_read_openai_server", lambda image, prompt, **kwargs: (calls.append(kwargs) or "Image read", kwargs["model"]))
    assert vision._select_route() == "ollama"
    vision._read_ollama(tmp_path / "prepared.jpg", "Read the supplied pixels")
    assert calls[0]["model"] == "medgemma1.5:latest"
    assert vision.local_server_info()["vision_verified"] is True
    assert vision._route_model("ollama") == "medgemma1.5:latest"


def test_missing_default_uses_installed_documented_vision_baseline(monkeypatch):
    inspections = fake_ollama(monkeypatch, ["gemma3:4b", "llama3.1:8b"], {"gemma3:4b": ["completion", "vision"]})
    assert vision._probe_ollama() is True
    assert inspections == ["gemma3:4b"]
    assert vision._route_model("ollama") == "gemma3:4b"
    assert vision.local_server_info()["configured_model"] == "medgemma1.5:latest"


def test_explicit_model_is_not_silently_replaced(monkeypatch):
    inspections = fake_ollama(monkeypatch, ["medgemma1.5:latest"], {"medgemma1.5:latest": ["vision"]})
    monkeypatch.setattr(vision, "_OLLAMA_MODEL_EXPLICIT", True)
    monkeypatch.setattr(vision, "OLLAMA_MODEL", "operator-model:latest")
    assert vision._probe_ollama() is False and not inspections
    assert "operator-model" in vision.local_server_info()["detail"]


def test_text_only_medical_tag_is_rejected_and_vision_baseline_is_used(monkeypatch):
    fake_ollama(monkeypatch, ["medgemma1.5:latest", "gemma3:4b"],
                {"medgemma1.5:latest": ["completion"], "gemma3:4b": ["completion", "vision"]})
    assert vision._probe_ollama() is True
    assert vision._route_model("ollama") == "gemma3:4b"
    assert vision.local_server_info()["attempts"][0]["status"] == "no_vision"


def test_explicit_omitted_tag_resolves_latest(monkeypatch):
    fake_ollama(monkeypatch, ["medgemma1.5:latest"], {"medgemma1.5:latest": ["vision"]})
    monkeypatch.setattr(vision, "_OLLAMA_MODEL_EXPLICIT", True)
    monkeypatch.setattr(vision, "OLLAMA_MODEL", "medgemma1.5")
    assert vision._probe_ollama() is True
    assert vision._route_model("ollama") == "medgemma1.5:latest"


def test_ollama_outage_reports_server_requirement(monkeypatch):
    import requests
    from core.errors import BackendError

    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("connection refused")))
    with pytest.raises(BackendError, match="Start Ollama"):
        vision._read_ollama("image.jpg", "Read")
    assert not vision.local_server_info()["server_reachable"]


@pytest.mark.parametrize("extension", sorted(SUPPORTED_IMAGE_SUFFIXES))
def test_every_supported_raster_format_decodes_and_reaches_common_vision_path(extension, tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / f"chest_xray{extension.upper()}"
    Image.linear_gradient("L").resize((256, 256)).save(source)
    study = load_study(source)
    dispatched = []
    monkeypatch.setattr(vision, "_select_route", lambda: "ollama")
    def dispatch(route, path, prompt):
        with Image.open(path) as prepared:
            assert prepared.format == "JPEG" and prepared.mode == "RGB"
        dispatched.append(path)
        return "MODALITY AND VIEW: chest X-ray\nOBSERVATIONS: synthetic read", "test-vision"
    monkeypatch.setattr(vision, "_dispatch", dispatch)
    result = vision.run(CapabilityRequest(instruction="Read image", study=study))
    assert len(study.images) == 1 and not study.skipped_files
    assert source.name in result.artifacts["reads"] and not result.artifacts["read_errors"]
    assert len(dispatched) == 1 and dispatched[0].suffix == ".jpg"


def test_summary_separates_completed_image_reads_from_unsupported_screening():
    from chat.engine import render_analysis
    from chat.session import AnalysisRun
    from models import RadiologyReport

    report = RadiologyReport(study_id="s", image_count=2, image_reads={"ct.jpg": "read"},
                             input_manifest=[{"kind": "image", "id": "ct.jpg"}, {"kind": "image", "id": "scan.png"}],
                             screening={"ct.jpg": {"status": "skipped", "reason": "no matching supervised model"}})
    run = AnalysisRun(1, "now", "report.md", "report.json", "indeterminate", "routine", [])
    rendered = render_analysis(report, run, 1)
    assert "Direct image reads:** 1/2 completed" in rendered
    assert "Image reads unavailable:** scan.png" in rendered
    assert "separate from the direct vision-model reads" in rendered


def test_model_not_supported_does_not_retry_because_request_id_contains_500():
    from core.retry import is_transient

    permanent = RuntimeError("Request ID: abc500def; model_not_supported: not supported by any provider")
    assert not is_transient(permanent)
    permanent.status_code = 400
    assert not is_transient(permanent)
    unavailable = RuntimeError("Temporarily overloaded")
    unavailable.status_code = 503
    assert is_transient(unavailable)
