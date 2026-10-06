"""Offline regressions for patient continuity, evidence reasoning, and generic AOS.

The supplied testingdeseases artifacts are retained unchanged. These tests use
synthetic evidence and injected endpoints: no weights, credentials or network.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.graph_executor import GraphExecutor
from agents.integrator_agent import IntegratorAgent
from capabilities import registry
from capabilities.registry import CapabilityRequest, CapabilityResult
from chat.engine import ChatEngine
from chat.memory import Chunk
from chat.session import AnalysisRun, SessionStore
from core.clinical_reasoning import evidence_body, validate_assessment
from core.routing import CapabilityDNA, PerformanceHistory, TaskRequirements, rank_candidates
from models import ClinicalAssessment, DocumentPage, Graph, Node, RadiologyReport, Study, StudyDocument


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from config import RUNTIME
    from core import routing
    import chat.engine as engine

    monkeypatch.setattr(routing, "_HISTORY", PerformanceHistory())
    monkeypatch.setattr(engine, "warm_up", lambda: None)
    old = RUNTIME.cache_dir
    object.__setattr__(RUNTIME, "cache_dir", tmp_path / "cache")
    registry._ensure_loaded()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_DNA", dict(registry._DNA))
    monkeypatch.setattr(registry, "_DESCRIPTIONS", dict(registry._DESCRIPTIONS))
    yield
    object.__setattr__(RUNTIME, "cache_dir", old)


def hypothesis(condition="Pulmonary embolism", status="possible", ref="scan.pdf#page=1", **kwargs):
    return {"condition": condition, "status": status, "rationale": "Correlate imaging with clinical findings.",
            "supporting_evidence": [{"detail": "Dyspnea and abnormal scan", "source_refs": [ref]}],
            **kwargs}


def document(name, text):
    return StudyDocument(document_id=name, source_path=name, content_hash=name, format="txt", page_count=1,
                         pages=[DocumentPage(page_number=1, text=text)])


def test_named_session_reopens_exact_patient_and_preserves_files_transcript_and_memory(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    original, resumed = store.open_named("patient-001")
    assert not resumed
    uploaded = tmp_path / "lab.txt"
    uploaded.write_text("CRP 92 mg/L")
    original.add_file(uploaded)
    original.log_event("user", "Discussed the elevated CRP")
    original.meta.clinical_assessment = {"integrated_summary": "Inflammation requires investigation."}
    original.save()
    reopened, resumed = store.open_named("PATIENT-001")
    assert resumed and reopened.id == original.id
    assert reopened.meta.files[0].name == "lab.txt"
    assert reopened.conversation()[0]["text"] == "Discussed the elevated CRP"
    assert reopened.meta.clinical_assessment["integrated_summary"]
    other, resumed = store.open_named("patient-002")
    assert not resumed and other.id != original.id


def test_legacy_duplicate_names_reopen_latest_activity_and_explicit_new_still_creates(tmp_path):
    store = SessionStore(tmp_path)
    first = store.create("patient-001")
    second = store.create("patient-001")
    first.log_event("user", "Most recent activity on the older conversation")
    assert store.open_named("patient-001")[0].id == first.id
    assert second.id != first.id
    assert store.open_named("patient-00")[1] is False  # no fuzzy patient startup


@pytest.mark.parametrize("text", [
    "D-dimer negative", "Can pulmonary embolism be ruled out?",
    "Pulmonary embolism cannot be excluded", "If pulmonary embolism is excluded, consider pneumonia",
    "Pulmonary embolism not ruled out",
])
def test_negative_or_hypothetical_evidence_is_not_a_ruleout(text):
    h = hypothesis(status="ruled_out", supporting_evidence=[],
                   opposing_evidence=[{"detail": text, "source_refs": ["scan.pdf#page=1"]}],
                   exclusion_reason="Negative test")
    assessment = validate_assessment({"differential": [h]}, {"scan.pdf#page=1": text})
    assert assessment.differential[0].status == "less_likely"
    assert assessment.validation_issues


def test_explicit_exclusion_is_retained_even_when_model_omits_or_repeats_old_support():
    sources = {"scan.pdf#page=1": "Pulmonary embolism excluded after CTPA review."}
    old = validate_assessment({"differential": [hypothesis(status="ruled_out", supporting_evidence=[],
                              opposing_evidence=[{"detail": sources["scan.pdf#page=1"], "source_refs": list(sources)}],
                              exclusion_reason="Clinician documented exclusion")]}, sources)
    assert old.differential[0].status == "ruled_out"
    omitted = validate_assessment({"differential": []}, sources, old.model_dump())
    repeated = validate_assessment({"differential": [hypothesis()]}, sources, old.model_dump())
    assert omitted.differential[0].status == repeated.differential[0].status == "ruled_out"
    sources["new.pdf#page=1"] = "New filling defect observed"
    reopened = validate_assessment({"differential": [hypothesis(ref="new.pdf#page=1")]}, sources, old.model_dump())
    assert reopened.differential[0].status == "possible"
    assert "Reopened" in reopened.differential[0].rationale


def test_generated_image_read_cannot_establish_confirmed_or_ruled_out_status():
    model_read = {"image.jpg": "Pulmonary embolism confirmed"}
    result = validate_assessment({"differential": [hypothesis(status="confirmed", ref="image.jpg")]},
                                 model_read, status_sources={})
    assert result.differential[0].status == "likely"


def test_invented_source_is_rejected_and_never_saved_as_evidence():
    result = validate_assessment({"integrated_summary": "Cancer confirmed [fake-biopsy.pdf]",
                                 "differential": [hypothesis("Cancer", "confirmed", "fake-biopsy.pdf")]},
                                 {"labs.pdf#page=1": "CRP elevated"})
    assert not result.differential
    assert "Cancer confirmed" not in result.integrated_summary
    assert result.validation_issues


def test_reordered_parenthetical_condition_keeps_one_longitudinal_hypothesis():
    sources = {"scan.pdf#page=1": "Abnormal opacity"}
    prior = {"differential": [hypothesis("Atypical (viral or Mycoplasma) pneumonia")]}
    new = validate_assessment({"differential": [hypothesis("Atypical pneumonia (viral or Mycoplasma)")]}, sources, prior)
    assert len(new.differential) == 1


def test_prompt_balances_earlier_labs_and_later_imaging_sources():
    sources = {"labs.pdf#page=1": "Lab data " * 5000, "scan.pdf#page=1": "Right lung consolidation"}
    body = evidence_body([], sources, budget=2000)
    assert "[labs.pdf#page=1]" in body and "[scan.pdf#page=1]" in body
    assert "Right lung consolidation" in body and "truncated" in body
    assert len(body) < 2100


def test_document_consolidation_retains_positive_and_negative_evidence_without_model_call(monkeypatch):
    from capabilities import cross_image_synthesis as synthesis

    monkeypatch.setattr(synthesis, "chat_text", lambda *a, **k: pytest.fail("Unnecessary consolidation LLM call"))
    findings = [
        {"label": "CRP", "description": "CRP 92 mg/L elevated", "severity": "high", "source_refs": ["labs.pdf#page=1"]},
        {"label": "Consolidation", "description": "Right lower lobe consolidation", "severity": "high", "source_refs": ["scan.pdf#page=1"]},
        {"label": "Culture", "description": "No pathogenic growth", "severity": "normal", "source_refs": ["culture.pdf#page=1"]},
    ]
    result = synthesis.run(CapabilityRequest(instruction="consolidate", text="reads",
                                           artifacts={"findings": findings, "document_reads": {"labs.pdf#page=1": "CRP 92"}}))
    assert len(result.artifacts["findings"]) == 3
    assert result.artifacts["document_reads"]
    assert result.artifacts["findings"][-1]["description"] == "No pathogenic growth"


def test_lab_backend_failure_salvages_native_lines_without_nameerror_or_diagnosis(monkeypatch):
    from capabilities import document_analysis as analysis

    monkeypatch.setattr(analysis.hf_backend, "chat_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Endpoint down")))
    study = Study(study_id="lab", source_dir=".", documents=[document("labs.txt", "CRP 92 mg/L (reference <5)\nCreatinine 1.1 mg/dL")])
    result = analysis.run(CapabilityRequest(instruction="read", study=study))
    assert len(result.artifacts["findings"]) == 2
    assert all(f["severity"] == "indeterminate" for f in result.artifacts["findings"])
    assert "fallback" in str(result.artifacts["quality_issues"]).lower()


def test_page_extraction_cache_survives_new_question_and_changed_graph_position(monkeypatch):
    from capabilities import document_analysis as analysis

    calls = []
    payload = {"document_type": "lab_report", "findings": [{"label": "CRP", "description": "CRP 92 mg/L", "severity": "high"}], "quality_issues": []}
    monkeypatch.setattr(analysis.hf_backend, "chat_text", lambda *a, **k: (calls.append(k) or json.dumps(payload), "stub"))
    study = Study(study_id="lab", source_dir=".", documents=[document("labs.txt", "CRP 92 mg/L")])
    analysis.run(CapabilityRequest(instruction="read document0", text="First graph job", study=study))
    study.patient.referring_question = "Which conditions are less likely now?"
    analysis.run(CapabilityRequest(instruction="read document1", text="Changed graph job", study=study))
    assert len(calls) == 1


def test_final_reasoning_uses_combined_sources_and_renders_differential_and_tests(monkeypatch):
    from capabilities import report_synthesis
    from reporting.report_builder import render_markdown

    prompts = []
    study = Study(study_id="synthetic", source_dir=".", documents=[document("labs.txt", "CRP 92 mg/L elevated"),
                                                                             document("scan.txt", "Right lower lobe consolidation")])
    h = hypothesis("Pneumonia", "likely", "labs.txt#page=1")
    h["supporting_evidence"].append({"detail": "Right lower lobe consolidation", "source_refs": ["scan.txt#page=1"]})
    h["rationale"] = "Inflammatory markers and focal consolidation together support pneumonia."
    h["suggested_tests"] = [{"test": "Targeted microbiology", "purpose": "Identify pathogen if clinically indicated", "interpretation": "Positive results increase etiologic support"}]
    payload = {"impression": h["rationale"], "summary": "Pneumonia is likely", "triage": "routine",
               "clinical_assessment": {"integrated_summary": h["rationale"], "differential": [h]}}
    monkeypatch.setattr(report_synthesis, "chat_text", lambda p, **k: (prompts.append(p) or json.dumps(payload), "stub"))
    findings = [{"label": "CRP", "description": "CRP 92 mg/L", "severity": "high", "source_refs": ["labs.txt#page=1"]},
                {"label": "Consolidation", "description": "Right lower lobe consolidation", "severity": "high", "source_refs": ["scan.txt#page=1"]}]
    result = report_synthesis.run(CapabilityRequest(instruction="report", study=study, artifacts={"findings": findings}))
    node = Node(id="report", description="report", capability="report_synthesis", status="done", artifacts=result.artifacts)
    report = IntegratorAgent().integrate(Graph(job="clinical reasoning", nodes=[node]), study)
    markdown = render_markdown(report)
    assert "labs.txt#page=1" in prompts[0] and "scan.txt#page=1" in prompts[0]
    assert report.status.triage == "urgent"
    assert "Pneumonia — likely" in markdown and "To distinguish" in markdown
    assert report.clinical_assessment.differential[0].suggested_tests
    report_synthesis.run(CapabilityRequest(instruction="report", study=study, artifacts={"findings": findings}))
    assert len(prompts) == 1  # unchanged source/context/catalog report cache


def test_synthesis_failure_is_degraded_and_integrator_enforces_triage_floor(monkeypatch):
    from capabilities import report_synthesis

    monkeypatch.setattr(report_synthesis, "chat_text", lambda *a, **k: (_ for _ in ()).throw(ValueError("Bad JSON")))
    finding = {"label": "Lung mass", "description": "Abnormal mass", "severity": "high", "source_refs": ["ct.txt#page=1"]}
    result = report_synthesis.run(CapabilityRequest(instruction="report", artifacts={"findings": [finding]}))
    result.artifacts["status"]["triage"] = "routine"  # reproduce the saved lung-cancer inconsistency
    report = IntegratorAgent().integrate(Graph(job="test", nodes=[Node(id="report", description="r", capability="report_synthesis",
                                          status="done", artifacts=result.artifacts)]), Study(study_id="test", source_dir="."))
    assert report.degraded and report.status.triage == "urgent"
    assert report.findings[0].label == "Lung mass"
    assert any("Reasoning degraded" in w for w in report.warnings)


def test_all_failed_new_run_keeps_prior_exclusion_as_explicit_historical_state():
    study = Study(study_id="new-run", source_dir=".")
    study.patient.prior_assessment = {"differential": [hypothesis(status="ruled_out", exclusion_reason="Clinician exclusion")]}
    graph = Graph(job="new results", nodes=[Node(id="report", description="r", capability="report_synthesis", status="skipped")])
    report = IntegratorAgent().integrate(graph, study)
    assert report.degraded and report.clinical_assessment.differential[0].status == "ruled_out"
    assert "current reasoning did not complete" in report.clinical_assessment.limitations[-1]


def test_clinician_ruleout_persists_through_close_reopen_and_later_question(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = store.create("patient-001")
    session.meta.analyses = [AnalysisRun(1, "now", "", "", "high", "urgent", [])]
    session.meta.clinical_assessment = {"differential": [hypothesis()]}
    session.save()
    payload = {"answer": "The new clinician assessment changes the differential.", "clinical_assessment": {
        "integrated_summary": "Pulmonary embolism is documented as excluded.",
        "differential": [hypothesis(status="ruled_out", supporting_evidence=[], exclusion_reason="Clinician reviewed CTPA",
                         opposing_evidence=[{"detail": "Clinician excluded pulmonary embolism", "source_refs": ["clinician message 1"]}])]}}
    engine = ChatEngine(session, store, llm=lambda *a, **k: (json.dumps(payload), "stub"))
    engine.memory.add([Chunk(id="scan", kind="document", source="scan.pdf#page=1", text="Earlier suspected pulmonary embolism")])
    engine.handle("Pulmonary embolism ruled out after clinician review of CTPA.")
    engine.close()
    reopened, resumed = store.open_named("patient-001")
    assert resumed and reopened.meta.clinical_assessment["differential"][0]["status"] == "ruled_out"
    prompts = []
    payload["clinical_assessment"]["differential"] = [hypothesis()]
    engine = ChatEngine(reopened, store, llm=lambda p, **k: (prompts.append(p) or json.dumps(payload), "stub"))
    engine.handle("What remains possible?")
    assert "ruled_out" in prompts[-1]
    assert engine.session.meta.clinical_assessment["differential"][0]["status"] == "ruled_out"
    assert any(e["kind"] == "diagnostic_update" for e in engine.session.events())
    engine.close()


def test_new_upload_passes_prior_differential_and_avoids_second_reasoning_call(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = store.create("patient-001")
    session.meta.clinical_assessment = {"integrated_summary": "Prior assessment", "differential": [hypothesis()]}
    lab = tmp_path / "labs.txt"
    lab.write_text("CRP 92")
    seen = []
    def analyze(folder, **kwargs):
        seen.append(kwargs)
        return RadiologyReport(study_id="new"), Study(study_id="new", source_dir=str(folder)), Graph(job="new")
    engine = ChatEngine(session, store, analyzer=analyze, llm=lambda *a, **k: pytest.fail("Duplicate answer LLM call"))
    engine.handle(f"@{lab} Update the combined differential")
    assert seen[0]["prior_assessment"]["differential"][0]["condition"] == "Pulmonary embolism"
    assert seen[0]["clinician_updates"]
    engine.close()


def test_shortened_explicit_clinician_exclusion_is_saved_even_when_model_is_down(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = store.create("patient-001")
    session.meta.clinical_assessment = {"differential": [hypothesis("Community-acquired bacterial pneumonia", "likely", "labs.txt#page=1")]}
    engine = ChatEngine(session, store, llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Endpoint down")))
    engine.memory.add([Chunk(id="lab", kind="document", source="labs.txt#page=1", text="CRP elevated")])
    engine.handle("Clinician update: bacterial pneumonia ruled out after specialist assessment.")
    engine.close()
    reopened, _ = store.open_named("patient-001")
    assert reopened.meta.clinical_assessment["differential"][0]["status"] == "ruled_out"
    assert reopened.meta.clinical_assessment["differential"][0]["opposing_evidence"]


def test_model_choice_constraints_and_historical_reliability_survive_restart(tmp_path):
    history = PerformanceHistory(tmp_path / "performance.json")
    slow = CapabilityDNA("slow", skills=("reason",), context_tokens=8000, expected_latency_s=10)
    fast = CapabilityDNA("fast", skills=("reason",), context_tokens=32000, expected_latency_s=1)
    task = TaskRequirements(skills=("reason",), context_tokens=16000)
    ranked, trace = rank_candidates([slow, fast], task, history)
    assert ranked[0].name == "fast" and not trace[0]["eligible"]
    history.record("fast", success=False, seconds=20, cooldown_s=30)
    loaded = PerformanceHistory(history.path)
    assert loaded.snapshot("fast")["attempts"] == 1
    assert not rank_candidates([fast], task, loaded)[0]


def test_unknown_cost_does_not_satisfy_hard_budget():
    unknown = CapabilityDNA("unknown-cost", skills=("reason",))
    known = CapabilityDNA("known-cost", skills=("reason",), cost_per_call=0.01)
    ranked, _ = rank_candidates([unknown, known], TaskRequirements(skills=("reason",), max_cost=0.02), PerformanceHistory())
    assert [d.name for d in ranked] == ["known-cost"]


def test_unknown_context_limit_does_not_satisfy_hard_context_requirement():
    unknown = CapabilityDNA("unknown-context", skills=("reason",))
    assert not rank_candidates([unknown], TaskRequirements(skills=("reason",), context_tokens=8000), PerformanceHistory())[0]


@pytest.mark.parametrize("payload", [[], {"model": None}, {"model": {"latency_s": "invalid"}}])
def test_corrupt_optional_telemetry_cannot_block_capability_selection(tmp_path, payload):
    path = tmp_path / "history.json"
    path.write_text(json.dumps(payload))
    history = PerformanceHistory(path)
    ranked, _ = rank_candidates([CapabilityDNA("model", skills=("reason",))], TaskRequirements(skills=("reason",)), history)
    assert ranked[0].name == "model"


def test_equivalent_nonmedical_agent_selected_after_failure():
    for name, function in (("translator-primary", lambda r: (_ for _ in ()).throw(RuntimeError("Endpoint down"))),
                           ("translator-backup", lambda r: CapabilityResult(text="Bonjour"))):
        registry.register(name, "Translate generic text", dna=CapabilityDNA(name, skills=("translate",), priority=5 if name.endswith("primary") else 0))(function)
    node = Node(id="translate", description="Translate hello into French", capability="translator-primary")
    GraphExecutor().run(Graph(job="Translate hello", nodes=[node]))
    assert node.status == "done" and node.output == "Bonjour"
    assert node.artifacts["_selected_capability"] == "translator-backup"
    assert [a["status"] for a in node.artifacts["_selection"]["attempts"]] == ["rejected", "accepted"]


def test_ready_scheduler_starts_child_before_unrelated_slow_root_finishes():
    child_ran = threading.Event()
    def work(request):
        if request.node_id == "slow":
            assert child_ran.wait(2), "Global wave barrier delayed an independently-ready child"
        if request.node_id == "child":
            child_ran.set()
        return CapabilityResult(text=request.node_id)
    registry.register("scheduler-test", "generic dependency work")(work)
    graph = Graph(job="parallel", nodes=[Node(id="fast", description="f", capability="scheduler-test"),
                                           Node(id="slow", description="s", capability="scheduler-test"),
                                           Node(id="child", description="c", capability="scheduler-test", depends_on=["fast"])])
    GraphExecutor(max_workers=2).run(graph)
    assert all(n.status == "done" for n in graph.nodes)


def test_text_router_rejects_malformed_primary_and_uses_compatible_alternative(monkeypatch):
    from capabilities import hf_backend, text_catalog
    import config

    monkeypatch.setattr(config, "GROQ_API_KEY", "offline-stub")
    monkeypatch.setenv("MAOS_TEXT_BACKEND", "groq")
    entries = [("groq", "primary", CapabilityDNA("groq:primary", skills=("clinical_reasoning",), outputs=("json",), context_tokens=32000, priority=5)),
               ("groq", "backup", CapabilityDNA("groq:backup", skills=("clinical_reasoning",), outputs=("json",), context_tokens=32000))]
    monkeypatch.setattr(text_catalog, "profiles", lambda task: entries)
    calls = []
    def create(**kwargs):
        calls.append(kwargs["model"])
        text = "malformed" if kwargs["model"] == "primary" else '{"answer":"valid"}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
    monkeypatch.setattr(hf_backend, "groq_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    text, model = hf_backend.chat_text("Reason over sources", task="clinical_reasoning", json_mode=True,
                                     constraints={"context_tokens": 10000})
    assert calls == ["primary", "backup"] and model == "backup"
    assert json.loads(text)["answer"] == "valid"
    assert hf_backend.text_selection_trace()["attempts"][0]["status"] == "rejected"


def test_named_tui_replays_existing_transcript_without_explicit_resume(tmp_path):
    from tui.app import MedicalAOSApp, UserBubble

    store = SessionStore(tmp_path / "sessions")
    original = store.create("patient-001")
    original.log_event("user", "Existing clinical conversation", raw="Existing clinical conversation")
    def factory(session, store, callback):
        return ChatEngine(session, store, on_event=callback, llm=lambda *a, **k: ("ok", "stub"))
    async def scenario():
        app = MedicalAOSApp(name="patient-001", store=store, engine_factory=factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.05)
            assert app.engine.session.id == original.id
            assert any(b._text == "Existing clinical conversation" for b in app.query(UserBubble))
    asyncio.run(scenario())
