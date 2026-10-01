"""End-to-end: folder in, report out, with every model call stubbed.

No network, no weights. What is under test is the wiring -- that findings,
severities and warnings survive the trip from capability output to rendered
report, and that a partial failure still yields a usable, clearly-degraded one.
"""

from __future__ import annotations

import json

import pytest

from capabilities.registry import CapabilityResult
from models import Severity


@pytest.fixture
def stub_models(fake_capability):
    """Install deterministic stand-ins for all seven capabilities."""

    def triage(request):
        return CapabilityResult(
            text="Imaging triage: chest X-ray",
            artifacts={
                "triage": {i: {"modality": "X-ray"} for i in request.image_ids},
                "non_medical": [],
            },
        )

    def screen(request):
        return CapabilityResult(
            text="Screening: pneumonia 0.82",
            artifacts={
                "classification": {
                    i: [
                        {"label": "PNEUMONIA", "score": 0.82},
                        {"label": "NORMAL", "score": 0.18},
                    ]
                    for i in request.image_ids
                },
                "flagged": [
                    {"image_id": i, "label": "PNEUMONIA", "score": 0.82, "severity": "high"}
                    for i in request.image_ids
                ],
            },
        )

    def read(request):
        assert request.image_ids, "vision node received no image assignment"
        # The classifier's scores must have reached this node.
        assert "classification" in request.artifacts
        return CapabilityResult(
            text=(
                "MODALITY AND VIEW: chest radiograph, PA.\n"
                "OBSERVATIONS:\n- Right lower lobe consolidation: RLL | high | medium"
            ),
            artifacts={"reads": {request.image_ids[0]: "consolidation"}},
        )

    def consolidate(request):
        return CapabilityResult(
            text="Consolidated findings: RLL consolidation",
            artifacts={
                "findings": [
                    {
                        "label": "Right lower lobe consolidation",
                        "description": "Airspace opacity in the right lower lobe.",
                        "severity": "high",
                        "confidence": 0.75,
                        "image_ids": ["chest_xray_pa.png"],
                        "body_part": "chest",
                    }
                ],
                "quality_issues": ["Slight rotation on the lateral view."],
                "discrepancies": [],
            },
        )

    def literature(request):
        return CapabilityResult(
            text="Community-acquired pneumonia is the usual cause.",
            artifacts={"references": ["Radiopaedia - Consolidation"]},
        )

    def report(request):
        assert request.artifacts.get("findings"), "report node lost the findings"
        return CapabilityResult(
            text="IMPRESSION: RLL consolidation consistent with pneumonia.",
            artifacts={
                "indication": "Cough and fever.",
                "technique": "PA and lateral chest radiographs.",
                "impression": "RLL consolidation consistent with pneumonia.",
                "status": {
                    "overall_severity": "high",
                    "summary": "Right lower lobe pneumonia; start treatment pathway.",
                    "triage": "urgent",
                    "red_flags": ["Hypoxia risk"],
                    "recommended_actions": ["Bloods and sputum culture", "Repeat film in 6 weeks"],
                },
            },
        )

    for name, fn in (
        ("modality_triage", triage),
        ("image_classification", screen),
        ("vision", read),
        ("cross_image_synthesis", consolidate),
        ("web_search", literature),
        ("report_synthesis", report),
    ):
        fake_capability(name, fn)


class TestHappyPath:
    def test_produces_a_complete_report(self, study_folder, stub_models, tmp_path):
        from pipeline import analyze_study

        report, study, graph = analyze_study(
            study_folder, output_dir=tmp_path / "out"
        )

        assert not report.degraded
        assert report.image_count == 2
        assert all(n.status == "done" for n in graph.nodes)

        assert len(report.findings) == 1
        assert report.findings[0].severity is Severity.HIGH
        assert report.status.triage == "urgent"
        assert report.status.overall_severity is Severity.HIGH
        assert "pneumonia" in report.impression.lower()
        assert report.references

    def test_writes_markdown_and_json(self, study_folder, stub_models, tmp_path):
        from pipeline import analyze_study

        out = tmp_path / "out"
        report, _study, _graph = analyze_study(study_folder, output_dir=out)

        # plan.md also lands here, so select the report by name.
        markdown_files = [f for f in out.glob("*.md") if f.name != "plan.md"]
        json_files = list(out.glob("*.json"))
        assert len(markdown_files) == 1
        assert len(json_files) == 1

        markdown = markdown_files[0].read_text(encoding="utf-8")
        assert "Patient status" in markdown
        assert "clinician review" in markdown  # the disclaimer
        assert "Right lower lobe consolidation" in markdown
        assert "Run diagnostics" in markdown

        payload = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert payload["study_id"] == report.study_id
        assert payload["status"]["triage"] == "urgent"

    def test_patient_context_flows_into_the_report(self, study_folder, stub_models, tmp_path):
        from pipeline import analyze_study

        report, _, _ = analyze_study(
            study_folder,
            clinical_history="ex-smoker",
            referring_question="exclude malignancy",
            output_dir=tmp_path / "out",
        )
        assert report.patient.clinical_history == "ex-smoker"
        assert report.patient.referring_question == "exclude malignancy"

    def test_skipped_files_appear_as_warnings(self, study_folder, stub_models, tmp_path):
        from pipeline import analyze_study

        report, _, _ = analyze_study(study_folder, output_dir=tmp_path / "out")
        assert any("notes.xyz" in w for w in report.warnings)

    def test_quality_issues_are_surfaced(self, study_folder, stub_models, tmp_path):
        from pipeline import analyze_study

        report, _, _ = analyze_study(study_folder, output_dir=tmp_path / "out")
        assert any("rotation" in w.lower() for w in report.warnings)

    def test_literature_stage_can_be_skipped(self, study_folder, stub_models, tmp_path):
        from pipeline import analyze_study

        _report, _study, graph = analyze_study(
            study_folder, with_literature=False, output_dir=tmp_path / "out"
        )
        assert not any(n.capability == "web_search" for n in graph.nodes)

    def test_plan_markdown_is_written_beside_the_report(
        self, study_folder, stub_models, tmp_path
    ):
        """The plan must land in the run's own output dir, not a global one."""
        from config import OUTPUT_DIR
        from pipeline import analyze_study

        out = tmp_path / "out"
        analyze_study(study_folder, output_dir=out)

        plan = (out / "plan.md").read_text(encoding="utf-8")
        assert "```mermaid" in plan
        assert "Execution waves" in plan
        # A test run must not clobber the project's own outputs/plan.md.
        assert out / "plan.md" != OUTPUT_DIR / "plan.md"


class TestDegradedRuns:
    def test_vision_failure_still_yields_a_report(
        self, study_folder, stub_models, fake_capability, tmp_path
    ):
        def broken(_request):
            raise RuntimeError("VLM endpoint down")

        fake_capability("vision", broken)

        from pipeline import analyze_study

        report, _study, graph = analyze_study(study_folder, output_dir=tmp_path / "out")

        assert report.degraded
        assert any("VLM endpoint down" in w for w in report.warnings)
        # Downstream stages were skipped, not silently reported as fine.
        assert {n.status for n in graph.nodes} >= {"failed", "skipped"}
        # A report object still exists and says it cannot be relied on.
        assert report.impression

    def test_report_stage_failure_falls_back_to_findings(
        self, study_folder, stub_models, fake_capability, tmp_path
    ):
        fake_capability(
            "report_synthesis", lambda r: (_ for _ in ()).throw(RuntimeError("LLM down"))
        )

        from pipeline import analyze_study

        report, _, _ = analyze_study(study_folder, output_dir=tmp_path / "out")

        assert report.degraded
        # Findings from the consolidation stage survive the report failure.
        assert len(report.findings) == 1
        assert "radiologist review" in report.impression.lower()

    def test_status_never_downgrades_below_worst_finding(
        self, study_folder, stub_models, fake_capability, tmp_path
    ):
        """A model claiming 'routine' must not override a critical finding."""

        def critical_findings(_request):
            return CapabilityResult(
                text="critical",
                artifacts={
                    "findings": [
                        {
                            "label": "Tension pneumothorax",
                            "description": "Large right pneumothorax.",
                            "severity": "critical",
                            "confidence": 0.9,
                            "image_ids": ["chest_xray_pa.png"],
                        }
                    ]
                },
            )

        def understated_report(_request):
            return CapabilityResult(
                text="IMPRESSION: minor",
                artifacts={
                    "impression": "Probably nothing.",
                    "status": {
                        "overall_severity": "low",
                        "summary": "Looks fine.",
                        "triage": "routine",
                        "red_flags": [],
                        "recommended_actions": [],
                    },
                },
            )

        fake_capability("cross_image_synthesis", critical_findings)
        fake_capability("report_synthesis", understated_report)

        from pipeline import analyze_study

        report, _, _ = analyze_study(study_folder, output_dir=tmp_path / "out")

        assert report.status.overall_severity is Severity.CRITICAL


class TestRendering:
    def test_summary_shows_status_and_findings(self, study_folder, stub_models, tmp_path):
        from pipeline import analyze_study
        from reporting.report_builder import render_summary

        report, _, _ = analyze_study(study_folder, output_dir=tmp_path / "out")
        summary = render_summary(report)

        assert "PATIENT STATUS" in summary
        assert "URGENT" in summary
        assert "Right lower lobe consolidation" in summary
        assert "clinician review" in summary

    def test_degraded_banner_appears_in_markdown(
        self, study_folder, stub_models, fake_capability, tmp_path
    ):
        fake_capability("vision", lambda r: (_ for _ in ()).throw(RuntimeError("down")))

        from pipeline import analyze_study
        from reporting.report_builder import render_markdown

        report, study, _ = analyze_study(study_folder, output_dir=tmp_path / "out")
        markdown = render_markdown(report, study)

        assert "DEGRADED RUN" in markdown
