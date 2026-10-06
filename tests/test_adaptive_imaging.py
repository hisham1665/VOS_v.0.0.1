"""Offline behavioral checks for adaptive execution and direct-image provenance."""
from __future__ import annotations

import json
import threading
import time
import sys
from types import SimpleNamespace

import pytest

from agents.graph_executor import GraphExecutor
from capabilities import registry
from capabilities.registry import CapabilityResult
from core.routing import CapabilityDNA, PerformanceHistory
from core.task_understanding import understand_task
from models import Graph, Node, TaskBudget


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from config import RUNTIME
    from core import routing

    registry._ensure_loaded()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_DNA", dict(registry._DNA))
    monkeypatch.setattr(registry, "_DESCRIPTIONS", dict(registry._DESCRIPTIONS))
    monkeypatch.setattr(routing, "_HISTORY", PerformanceHistory())
    previous = RUNTIME.cache_dir
    object.__setattr__(RUNTIME, "cache_dir", tmp_path / "cache")
    yield
    object.__setattr__(RUNTIME, "cache_dir", previous)


def register(name, skill, fn, *, outputs=("text",), **kwargs):
    registry.register(name, name, dna=CapabilityDNA(name, skills=(skill,), outputs=outputs, **kwargs))(fn)


def verification_request():
    return {"reason": "Extraction is incomplete", "description": "Verify the supplied source",
            "requirements": {"skills": ["verify-test"], "outputs": ["verification"]}}


def test_verification_rewires_pending_consumer_and_preserves_successful_work():
    calls, received = [], {}
    register("extract-test", "extract-test", lambda r: (calls.append(r.node_id) or CapabilityResult(
        text="partial extraction", artifacts={"observations": ["original"], "verification_requests": [verification_request()]})))
    register("verify-test", "verify-test", lambda r: CapabilityResult(text="source verified", artifacts={"verification": ["checked"]}),
             outputs=("verification",))
    register("finish-test", "finish-test", lambda r: (received.update(r.artifacts) or CapabilityResult(text=r.text)))
    graph = Graph(job="Review sources", nodes=[Node(id="extract", description="extract", capability="extract-test"),
                                               Node(id="finish", description="finish", capability="finish-test", depends_on=["extract"])])
    GraphExecutor(max_workers=2).run(graph)
    assert calls == ["extract"]
    assert received["observations"] == ["original"] and received["verification"] == ["checked"]
    assert graph.by_id()["finish"].depends_on == ["extract", "adapt1"]
    assert graph.original_plan[1]["depends_on"] == ["extract"]
    assert graph.revisions[0]["status"] == "applied"
    assert "source verified" in graph.by_id()["finish"].output


def test_runtime_recovery_uses_untried_third_implementation_and_keeps_failure_audit():
    for index in range(3):
        def work(r, index=index):
            if index < 2:
                raise RuntimeError(f"provider {index} down")
            return CapabilityResult(text="recovered evidence")
        register(f"provider-{index}", "recover-test", work, priority=10-index)
    register("consumer-test", "consumer-test", lambda r: CapabilityResult(text=r.text))
    graph = Graph(job="recover", nodes=[Node(id="source", description="s", capability="provider-0"),
                                       Node(id="consumer", description="c", capability="consumer-test", depends_on=["source"])])
    GraphExecutor().run(graph)
    assert graph.by_id()["source"].status == "failed"
    assert graph.by_id()["adapt1"].artifacts["_selected_capability"] == "provider-2"
    assert graph.by_id()["consumer"].status == "done"
    assert graph.by_id()["consumer"].depends_on == ["adapt1"]
    assert "recovered evidence" in graph.by_id()["consumer"].output


@pytest.mark.parametrize("budget", [TaskBudget(max_replans=0), TaskBudget(max_added_nodes=0)])
def test_recovery_budget_stops_added_work_and_records_reason(budget):
    register("trigger-test", "trigger-test", lambda r: CapabilityResult(text="uncertain", artifacts={"verification_requests": [verification_request()]}))
    register("verify-test", "verify-test", lambda r: pytest.fail("Recovery limit must stop this call"), outputs=("verification",))
    graph = Graph(job="bounded", task=understand_task("bounded", budget=budget),
                  nodes=[Node(id="source", description="s", capability="trigger-test")])
    GraphExecutor().run(graph)
    assert len(graph.nodes) == 1
    assert graph.revisions[0]["status"] == "budget_exhausted"


def test_verification_cannot_be_satisfied_by_same_output_with_wrong_skill():
    register("trigger-test", "trigger-test", lambda r: CapabilityResult(text="uncertain", artifacts={"verification_requests": [verification_request()]}))
    register("wrong-test", "unrelated", lambda r: pytest.fail("Wrong-skill verification must not run"), outputs=("verification",))
    graph = Graph(job="check", nodes=[Node(id="source", description="s", capability="trigger-test")])
    GraphExecutor().run(graph)
    assert graph.revisions[0]["status"] == "unavailable"


def test_task_cost_cap_rejects_unknown_prices_before_calling_provider():
    register("unknown-price", "cost-test", lambda r: pytest.fail("Unknown price cannot satisfy a hard cost cap"))
    graph = Graph(job="bounded cost", task=understand_task("bounded cost", budget=TaskBudget(max_cost=0.01)),
                  nodes=[Node(id="source", description="s", capability="unknown-price")])
    GraphExecutor().run(graph)
    assert graph.nodes[0].status == "failed" and "known cost" in graph.nodes[0].error


def test_verification_does_not_recurse_or_replay_original_extraction():
    count = []
    register("trigger-test", "trigger-test", lambda r: (count.append(r.node_id) or CapabilityResult(
        text="uncertain", artifacts={"verification_requests": [verification_request()]})))
    register("verify-test", "verify-test", lambda r: CapabilityResult(text="still uncertain", artifacts={"verification_requests": [verification_request()]}),
             outputs=("verification",))
    graph = Graph(job="bounded", nodes=[Node(id="source", description="s", capability="trigger-test")])
    executor = GraphExecutor()
    executor.run(graph)
    assert len(graph.nodes) == 2
    executor.run(graph)
    assert count == ["source", "source"] and len(graph.nodes) == 2
    assert len(graph.original_plan) == 1


def test_temporal_comparison_keeps_changed_values_and_ambiguous_dates():
    from capabilities.registry import CapabilityRequest
    from capabilities.verification import compare_temporal
    from models import DocumentPage, Study, StudyDocument

    documents = [StudyDocument(document_id=name, source_path=name, content_hash=name, format="txt",
                               pages=[DocumentPage(page_number=1, text=text)])
                 for name, text in (("a.txt", "2026-01-02 CRP 92 mg/L"), ("b.txt", "CRP 12 mg/L"))]
    findings = [{"label": "CRP", "description": f"CRP {value} mg/L", "source_refs": [f"{name}#page=1"]}
                for name, value in (("a.txt", 92), ("b.txt", 12))]
    result = compare_temporal(CapabilityRequest(instruction="compare", study=Study(study_id="s", source_dir=".", documents=documents),
                                                artifacts={"findings": findings}))
    record = result.artifacts["verification"][0]
    assert record["status"] == "limited" and len(record["observations"]) == 2
    assert record["observations"][1]["date"] is None
    assert "No trend/diagnosis inferred" in record["scope"]


def test_failed_additional_check_preserves_strict_original_consumer():
    register("trigger-test", "trigger-test", lambda r: CapabilityResult(text="usable evidence", artifacts={"verification_requests": [verification_request()]}))
    register("verify-test", "verify-test", lambda r: (_ for _ in ()).throw(RuntimeError("verification offline")), outputs=("verification",))
    register("consumer-test", "consumer-test", lambda r: CapabilityResult(text=r.text))
    graph = Graph(job="review", nodes=[Node(id="source", description="s", capability="trigger-test"),
                                      Node(id="consumer", description="c", capability="consumer-test", depends_on=["source"])])
    GraphExecutor().run(graph)
    assert graph.by_id()["adapt1"].status == "failed"
    assert graph.by_id()["consumer"].status == "done"
    assert "usable evidence" in graph.by_id()["consumer"].output


def test_global_deadline_stops_downstream_and_retries():
    def slow(request):
        time.sleep(0.15)
        return CapabilityResult(text="too late")
    register("deadline-test", "deadline-test", slow)
    register("after-deadline", "after-deadline", lambda r: pytest.fail("No calls may start after task deadline"))
    graph = Graph(job="time limited", task=understand_task("time limited", budget=TaskBudget(max_elapsed_s=0.03)),
                  nodes=[Node(id="source", description="s", capability="deadline-test"),
                         Node(id="consumer", description="c", capability="after-deadline", depends_on=["source"])])
    started = time.perf_counter()
    GraphExecutor(max_workers=1).run(graph)
    assert time.perf_counter() - started < 0.14
    assert graph.by_id()["source"].status == "failed" and graph.by_id()["consumer"].status == "skipped"


def dicom(path, *, frames=1, width=16, positions=None, instance=1, series_uid=None, color=False, blank_frame=None):
    import numpy as np
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    data = Dataset()
    data.file_meta = meta
    data.SOPClassUID, data.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    data.PatientName, data.PatientID = "Synthetic^Patient", "SYNTHETIC"
    data.Modality, data.BodyPartExamined = "CT", "CHEST"
    data.SeriesInstanceUID = series_uid or generate_uid()
    data.StudyInstanceUID = "1.2.826.0.1.3680043.8.498.123456789"
    data.InstanceNumber = instance
    data.Rows, data.Columns = 16, width
    data.SamplesPerPixel = 3 if color else 1
    data.PhotometricInterpretation = "RGB" if color else "MONOCHROME2"
    data.BitsStored = data.BitsAllocated = 8 if color else 16
    data.HighBit, data.PixelRepresentation = data.BitsStored - 1, 0
    data.NumberOfFrames = frames
    if color:
        data.PlanarConfiguration = 0
        pixels = np.zeros((frames, 16, width, 3), dtype="uint8")
        pixels[..., 0] = 100
        pixels[..., 1] = np.arange(width)
    else:
        pixels = np.stack([np.arange(16*width, dtype="uint16").reshape(16, width) + i*100 for i in range(frames)])
    if blank_frame is not None:
        pixels[blank_frame] = 0
    data.PixelData = pixels.tobytes()
    if positions is not None:
        data.PerFrameFunctionalGroupsSequence = []
        for position in positions:
            group, plane, orientation = Dataset(), Dataset(), Dataset()
            plane.ImagePositionPatient = [0, 0, position]
            orientation.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
            group.PlanePositionSequence = [plane]
            group.PlaneOrientationSequence = [orientation]
            data.PerFrameFunctionalGroupsSequence.append(group)
    data.save_as(str(path), enforce_file_format=True)
    return data


def test_multiframe_dicom_keeps_every_frame_metadata_and_cache_identity(tmp_path):
    from ingest.study_loader import load_study
    from models import Modality

    path = tmp_path / "volume.dcm"
    data = dicom(path, frames=3, positions=[2, 0, 1], width=3)
    first = load_study(path)
    second = load_study(path)
    assert len(first.images) == len(second.images) == 3
    assert [i.frame_index for i in first.images] == [1, 2, 0]  # geometry, not source/filename order
    assert len({i.content_hash for i in first.images}) == 3
    assert all(i.modality == Modality.CT and i.body_part == "CHEST" for i in second.images)
    assert first.images[0].metadata == second.images[0].metadata
    assert first.images[0].metadata["SeriesInstanceUID"] != data.SeriesInstanceUID
    assert "PatientName" not in first.images[0].metadata
    assert first.series[0].ordering == "patient-space slice position" and first.series[0].coverage == "unknown"


def test_dicom_sampling_is_frame_aware_and_explicitly_partial(tmp_path):
    from config import RUNTIME
    from ingest.study_loader import load_study

    path = tmp_path / "volume.dcm"
    dicom(path, frames=5, positions=[0, 1, 2, 3, 4])
    previous = RUNTIME.max_images_per_study
    object.__setattr__(RUNTIME, "max_images_per_study", 2)
    try:
        study = load_study(path)
    finally:
        object.__setattr__(RUNTIME, "max_images_per_study", previous)
    assert [image.frame_index for image in study.images] == [0, 4]
    assert study.series[0].acquired_count == 5 and study.series[0].coverage == "partial"
    assert any("sampled" in warning.lower() for warning in study.warnings)


def test_failed_dicom_frame_retains_good_frames_and_missing_coverage(tmp_path):
    from ingest.study_loader import load_study

    path = tmp_path / "volume.dcm"
    dicom(path, frames=3, positions=[0, 1, 2], blank_frame=1)
    study = load_study(path)
    assert len(study.images) == 2 and study.series[0].coverage == "partial"
    assert any("frame 2" in skipped for skipped in study.skipped_files)
    assert any("2/3" in warning for warning in study.series[0].warnings)


def test_multiframe_color_dicom_is_not_mistaken_for_monochrome_slice_axis(tmp_path):
    from ingest.study_loader import load_study
    from PIL import Image

    path = tmp_path / "color.dcm"
    dicom(path, frames=2, color=True)
    study = load_study(path)
    assert len(study.images) == 2
    with Image.open(study.images[0].prepared_path) as image:
        assert image.size == (16, 16)
        assert 90 <= image.getpixel((0, 0))[0] <= 110


def test_preprocessing_cache_retains_original_dimensions_and_quality(tmp_path):
    from ingest.preprocess import prepare_image
    from PIL import Image

    path = tmp_path / "large.png"
    Image.linear_gradient("L").resize((1200, 600)).save(path)
    _, original = prepare_image(path, max_edge=128)
    _, cached = prepare_image(path, max_edge=128)
    assert cached["cached"] is True
    assert original["original_size"] == cached["original_size"] == [1200, 600]
    assert cached["prepared_size"] == [128, 64] and cached["quality"] == original["quality"]


def test_blank_image_is_not_interpreted_as_normal_and_coverage_remains_unread(tmp_path, monkeypatch):
    from agents.integrator_agent import IntegratorAgent
    from capabilities import vision
    from capabilities.registry import CapabilityRequest
    from core.errors import CapabilityError
    from ingest.study_loader import load_study
    from PIL import Image

    path = tmp_path / "chest_xray.png"
    Image.new("L", (256, 256), color=0).save(path)
    study = load_study(path)
    monkeypatch.setattr(vision, "_read_image", lambda *a: pytest.fail("Blank pixels must not reach model"))
    with pytest.raises(CapabilityError):
        vision.run(CapabilityRequest(instruction="read", study=study))
    report = IntegratorAgent().integrate(Graph(job="read", nodes=[Node(id="read", description="read", capability="vision", status="failed")]), study)
    assert report.degraded and not report.input_manifest[0]["analysed"]
    assert report.evaluation_groups[0]["read"] == 0


def test_written_imaging_report_and_direct_image_are_evaluated_in_separate_lanes():
    from agents.integrator_agent import IntegratorAgent
    from core.image_evaluation import aggregate_reports
    from models import DocumentPage, Modality, Study, StudyDocument, StudyImage

    study = Study(study_id="s", source_dir=".", images=[StudyImage(image_id="cxr.png", source_path="cxr.png", content_hash="h", modality=Modality.XRAY, body_part="CHEST")],
                  documents=[StudyDocument(document_id="ct.txt", source_path="ct.txt", content_hash="d", format="txt",
                                           pages=[DocumentPage(page_number=1, text="Written CT report")])])
    node = Node(id="report", description="report", capability="report_synthesis", status="done",
                artifacts={"reads": {"cxr.png": "image read"}, "document_reads": {"ct.txt#page=1": "CT report read"},
                           "document_types": {"ct.txt#page=1": "ct"}})
    report = IntegratorAgent().integrate(Graph(job="read", nodes=[node]), study)
    lanes = aggregate_reports([report])["groups"]
    assert {(r["evidence_kind"], r["modality"], r["region"]) for r in lanes} == {
        ("direct_image", "X-ray", "chest"), ("written_imaging_report", "CT", "unknown")}
    assert report.image_reads == {"cxr.png": "image read"} and "cxr.png" not in report.source_reads


def test_invented_image_reference_is_not_accepted_as_a_finding():
    from capabilities.cross_image_synthesis import _normalise_findings

    result = _normalise_findings([{"label": "Invented mass", "image_ids": ["unseen.png"]}],
                                 allowed_refs=set(), allowed_image_ids={"actual.png"})
    assert result == []


def test_requested_specialist_can_run_without_a_broad_screener(monkeypatch):
    from capabilities import model_zoo, specialist_confirmation
    from capabilities.registry import CapabilityRequest
    from models import Modality, Study, StudyImage

    model = model_zoo.ZooModel(key="narrow", model_id="test", role=model_zoo.SPECIALIST,
                               modalities=("MRI",), regions=("head",), targets=("brain_tumor",))
    monkeypatch.setattr(model_zoo, "specialists_for", lambda *a: [model])
    monkeypatch.setattr(specialist_confirmation, "classify_images", lambda model, images: {images[0].image_id: [{"label": "brain_tumor", "score": 0.9}]})
    study = Study(study_id="s", source_dir=".", images=[StudyImage(image_id="mr", source_path="mr", content_hash="h", modality=Modality.MRI)])
    result = specialist_confirmation.run(CapabilityRequest(instruction="check", study=study,
        artifacts={"screening": {"mr": {"status": "skipped", "modality": "MRI", "region": "head", "routing_eligible": True, "models": []}},
                   "requested_conditions": ["brain_tumor"]}))
    assert result.artifacts["confirmation"]["mr"][0]["verdict"] == "supported"


def test_visual_verification_inspects_rendered_page_even_with_native_text(tmp_path, monkeypatch):
    from capabilities import document_analysis, vision
    from capabilities.registry import CapabilityRequest
    from capabilities.verification import verify_documents
    from models import DocumentPage, Study, StudyDocument
    from PIL import Image

    path = tmp_path / "page.png"
    Image.linear_gradient("L").save(path)
    study = Study(study_id="s", source_dir=".", documents=[StudyDocument(document_id="lab.pdf", source_path="lab.pdf", content_hash="h", format="pdf",
        pages=[DocumentPage(page_number=1, text="CRP 92", image_path=str(path))])])
    seen = []
    monkeypatch.setattr(vision, "_select_route", lambda: "openai")
    monkeypatch.setattr(vision, "_dispatch", lambda route, image, prompt: (seen.append((image, prompt)) or json.dumps({"document_type": "lab_report", "findings": [], "quality_issues": []}), "test-vlm"))
    monkeypatch.setattr(document_analysis.hf_backend, "chat_text", lambda *a, **k: pytest.fail("Rendered-page verification must use image route"))
    result = verify_documents(CapabilityRequest(instruction="verify", study=study))
    assert seen and seen[0][0] == path and "CRP 92" in seen[0][1]
    assert result.artifacts["verification"][0]["method"] == "rendered-page model reread"


def test_local_openai_image_requests_send_pixels_and_respect_server_concurrency(tmp_path, monkeypatch):
    from capabilities import vision
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image

    path = tmp_path / "image.jpg"
    Image.linear_gradient("L").save(path)
    calls, clients, active, peak = [], [], 0, 0
    lock = threading.Lock()
    def create(**kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        calls.append(kwargs)
        with lock:
            active -= 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Visible image read"))])
    class Client:
        def __init__(self, **kwargs):
            clients.append(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        def with_options(self, **kwargs):
            return self
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=Client))
    monkeypatch.setattr(vision, "_SERVER_GATE", threading.BoundedSemaphore(1))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: vision._read_openai_server(path, "Describe the visible image", url="http://local/v1", model="medical-4b", label="test"), range(2)))
    assert peak == 1 and len(calls) == 2
    assert all(c["base_url"] == "http://local/v1" and c["max_retries"] == 0 for c in clients)
    content = calls[0]["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert all(call["model"] == "medical-4b" for call in calls)
    assert results[0][0] == "Visible image read"


def test_ollama_auto_route_rejects_text_only_model_metadata(monkeypatch):
    from capabilities import vision
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: SimpleNamespace(ok=True, json=lambda: {"models": [{"name": vision.OLLAMA_MODEL}]}))
    monkeypatch.setattr(requests, "post", lambda *a, **k: SimpleNamespace(ok=True, json=lambda: {"capabilities": ["completion"]}))
    monkeypatch.setattr(vision, "_ollama_checked", None)
    assert vision._probe_ollama() is False


def test_mixed_input_pipeline_satisfies_coverage_and_retains_direct_image_provenance(tmp_path, monkeypatch):
    from pipeline import analyze_study
    from reporting.report_builder import render_markdown

    path = tmp_path / "volume.dcm"
    dicom(path, frames=2, positions=[0, 1])
    (tmp_path / "lab.txt").write_text("CRP 92 mg/L")
    def stub(name, fn):
        monkeypatch.setitem(registry._REGISTRY, name, fn)
    stub("modality_triage", lambda r: CapabilityResult(text="Header CT/CHEST", artifacts={"triage": {i: {"modality": "CT", "region": "chest", "confidence": 1} for i in r.image_ids}}))
    stub("image_classification", lambda r: CapabilityResult(text="No screener coverage", artifacts={"screening": {i: {"modality": "CT", "region": "chest", "status": "skipped", "reason": "unsupported", "models": []} for i in r.image_ids}}))
    stub("specialist_confirmation", lambda r: CapabilityResult(text="No applicable specialist", artifacts={"confirmation": {}}))
    stub("vision", lambda r: CapabilityResult(text="Visible opacity", artifacts={"reads": {i: "Visible opacity in this frame" for i in r.image_ids}}))
    stub("document_analysis", lambda r: CapabilityResult(text="CRP 92", artifacts={"document_reads": {"lab.txt#page=1": "CRP 92"}, "document_types": {"lab.txt#page=1": "lab_report"}}))
    stub("cross_image_synthesis", lambda r: CapabilityResult(text="Findings combined", artifacts={
        "findings": [{"label": "Opacity", "description": "Visible opacity", "severity": "moderate", "image_ids": list(r.artifacts["reads"])}]}))
    stub("report_synthesis", lambda r: CapabilityResult(text="Combined source-linked assessment", artifacts={
        "clinical_assessment": {"integrated_summary": "Imaging and laboratory observations require clinical correlation"},
        "status": {"overall_severity": "moderate", "triage": "routine"}, "impression": "Opacity requires clinician review"}))
    report, study, graph = analyze_study(tmp_path, with_literature=False, write_output=False)
    assert not report.degraded and all(row["status"] == "met" for row in report.acceptance_results)
    assert len(report.image_reads) == 2 and "lab.txt#page=1" in report.source_reads
    assert report.findings[0].evidence_kind == "direct_image"
    assert all(entry["analysed"] for entry in report.input_manifest if entry["kind"] in {"image", "document"})
    markdown = render_markdown(report, study)
    assert "Direct image reads" in markdown and "Evidence coverage" in markdown
    assert "2 / 2 / 2" in markdown


def test_unread_direct_image_prevents_normal_status_from_document_findings():
    from agents.integrator_agent import IntegratorAgent
    from models import Severity, Study, StudyImage

    study = Study(study_id="s", source_dir=".", images=[StudyImage(image_id="scan", source_path="scan", content_hash="h")])
    node = Node(id="report", description="r", capability="report_synthesis", status="done", artifacts={
        "findings": [{"label": "Normal blood result", "severity": "normal", "source_refs": ["lab.txt#page=1"]}],
        "status": {"overall_severity": "normal", "summary": "Blood result normal"}})
    report = IntegratorAgent().integrate(Graph(job="read", nodes=[node]), study)
    assert report.status.overall_severity == Severity.INDETERMINATE
    assert "normal imaging study cannot be established" in report.status.summary


def test_cost_budget_allows_one_affordable_call_and_blocks_unaffordable_retry():
    calls = []
    register("priced-primary", "priced-test", lambda r: (calls.append("primary") or (_ for _ in ()).throw(RuntimeError("provider down"))), cost_per_call=0.004, priority=10)
    register("priced-backup", "priced-test", lambda r: (calls.append("backup") or CapabilityResult(text="backup")), cost_per_call=0.004)
    graph = Graph(job="priced", task=understand_task("priced", budget=TaskBudget(max_cost=0.005, max_replans=0)),
                  nodes=[Node(id="source", description="s", capability="priced-primary")])
    GraphExecutor().run(graph)
    assert calls == ["primary"]
    assert graph.nodes[0].artifacts["_declared_cost_consumed"] == 0.004
    assert graph.nodes[0].artifacts["_selection"]["attempts"][1]["status"] == "budget_rejected"


@pytest.mark.parametrize("price", [-1, float("nan"), "unknown"])
def test_invalid_resource_price_cannot_satisfy_a_cost_constraint(price):
    from core.routing import TaskRequirements, mismatch

    assert mismatch(CapabilityDNA("invalid", skills=("test",), cost_per_call=price), TaskRequirements(max_cost=1))


def test_changed_prepared_pixels_do_not_reuse_cached_image_read(tmp_path, monkeypatch):
    from capabilities import vision
    from capabilities.registry import CapabilityRequest
    from config import RUNTIME
    from ingest.study_loader import load_study
    from PIL import Image

    path = tmp_path / "chest_xray.png"
    Image.linear_gradient("L").resize((256, 256)).save(path)
    calls = []
    monkeypatch.setattr(vision, "_select_route", lambda: "hf")
    monkeypatch.setattr(vision, "_dispatch", lambda *args: (calls.append(args) or "Visible findings", "test-model"))
    first = load_study(path)
    vision._read_image(first.images[0], CapabilityRequest(instruction="read", study=first))
    previous = RUNTIME.jpeg_quality
    object.__setattr__(RUNTIME, "jpeg_quality", 20)
    try:
        second = load_study(path)
    finally:
        object.__setattr__(RUNTIME, "jpeg_quality", previous)
    vision._read_image(second.images[0], CapabilityRequest(instruction="read", study=second))
    assert first.images[0].content_hash == second.images[0].content_hash
    assert len(calls) == 2


def test_unsupported_extracted_numbers_are_withheld_and_request_verification(monkeypatch):
    from capabilities import document_analysis
    from capabilities.registry import CapabilityRequest
    from models import DocumentPage, Study, StudyDocument

    payload = {"document_type": "lab_report", "findings": [{"label": "CRP", "description": "CRP 100 mg/L", "severity": "high"}], "quality_issues": []}
    monkeypatch.setattr(document_analysis.hf_backend, "chat_text", lambda *a, **k: (json.dumps(payload), "test-model"))
    study = Study(study_id="s", source_dir=".", documents=[StudyDocument(document_id="lab.txt", source_path="lab.txt", content_hash="h", format="txt",
                  pages=[DocumentPage(page_number=1, text="CRP 92 mg/L")])])
    for _ in range(2):  # raw-response cache must not bypass source grounding
        result = document_analysis.run(CapabilityRequest(instruction="read", study=study))
        assert not result.artifacts["findings"]
        assert result.artifacts["verification_requests"][0]["requirements"]["skills"] == ["verify_documents"]
        assert "100" in result.artifacts["document_reads"]["lab.txt#page=1"]  # raw response retained for audit


def test_numeric_argument_not_supported_by_cited_source_is_removed():
    from core.clinical_reasoning import validate_assessment

    raw = {"differential": [{"condition": "Inflammatory illness", "supporting_evidence": [
        {"detail": "CRP 100 mg/L", "source_refs": ["lab.txt#page=1"]}]}]}
    result = validate_assessment(raw, {"lab.txt#page=1": "CRP 92 mg/L"})
    assert result.validation_issues
    assert all(not h.supporting_evidence for h in result.differential)
