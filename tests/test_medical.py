"""Medical AOS extension tests.

Coverage mirrors the existing `tests/` style: manual graphs, stubbed run_fns,
and clipboard of the kernel's GraphExecutor/FailureManager. The medical run
functions are exercised for real (deterministic parsing paths) and the VLM/NER
calls are only stubbed where the network would otherwise be touched.

Scenarios covered:
  1. xray-only folder            -> imaging findings + confirmation flag
  2. blood PDF folder            -> lab rows with correct status values
  3. xray + blood                -> parallel analysis merged into the chart
  4. prescription + report + img -> medications + conditions + findings
  5. nested folders              -> manifest keeps relative paths
  6. unsupported file            -> reported, never crashes the run
  7. corrupt PDF / image         -> graceful notes, no fabricated values
  8. model/API failure           -> deterministic fallback within the capability
  9. agent failure + recovery    -> FailureManager substitution + degraded
 10. source + page preservation  -> every row carries its source file/page
 11. lab parsing correctness     -> HIGH/LOW/NORMAL vs the report's own range
 12. final chart generation      -> all sections present + clinical disclaimer
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("HF_TOKEN", "")

import aos_v0.medical.capabilities as med_caps
from aos_v0.agents.graph_executor import GraphExecutor
from aos_v0.core.capability_registry import CapabilityRegistry
from aos_v0.core.failure_manager import FailureManager
from aos_v0.core.models import CapabilityDNA, DNAOrdinals, Graph, Node
from aos_v0.medical.manifest import (
    parse_blocks,
    parse_manifest,
    scan_folder,
)
from aos_v0.medical.registration import register_medical_resources
from aos_v0.medical.workflow import build_medical_job
from aos_v0.providers.hf import ProviderError


def _dna(flag: str) -> CapabilityDNA:
    return CapabilityDNA(
        flags=[flag],
        ordinals=DNAOrdinals(),
        confidence=1.0,
        extracted_by="test",
    )


def _run_graph(job: str, nodes: list[Node], registry) -> Graph:
    return GraphExecutor(registry, FailureManager(registry)).run(
        Graph(job=job, nodes=nodes)
    )


def _blood_pdf(root: Path, name: str = "blood_test.pdf") -> Path:
    p = root / name
    p.write_text(
        "Glucose\t142\tmg/dL\t70-100 mg/dL\n"
        "Hemoglobin\t8.2\tg/dL\t13.5-17.5\n"
        "Sodium\t135\tmmol/L\t135-145 mmol/L\n"
    )
    return p


def _xray_png(root: Path, name: str = "chest_xray.png") -> Path:
    p = root / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return p


class MedicalFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    # -- 5. nested folders ------------------------------------------------
    def test_nested_folders_preserve_relative_paths(self):
        sub = self.root / "sub" / "lab"
        sub.mkdir(parents=True)
        _blood_pdf(sub, "blood.pdf")
        _xray_png(self.root, "xray.png")

        manifests = scan_folder(self.root)
        files = {(m.relative_path, m.detected_type) for m in manifests}
        self.assertIn(("sub/lab/blood.pdf", "laboratory_report"), files)
        self.assertIn(("xray.png", "xray"), files)

    # -- 6. unsupported file ----------------------------------------------
    def test_unsupported_file_reported_not_crash(self):
        (self.root / "notes.xlsx_on_disk.exe").write_bytes(b"MZ\x90\x00")
        (self.root / "readme.txt").write_text("hello")
        manifests = scan_folder(self.root)
        categories = {m.category for m in manifests}
        self.assertIn("unsupported", categories)

    # -- 1. xray-only folder ---------------------------------------------
    def test_xray_folder_route_and_chart_flag(self):
        _xray_png(self.root, "chest_xray.png")
        job, manifests = build_medical_job(self.root)
        self.assertEqual(len(manifests), 1)
        self.assertEqual(manifests[0].detected_type, "xray")

        original = med_caps.medic_gemma_describe
        med_caps.medic_gemma_describe = lambda path, **kw: (
            "A right-sided opacity is present on the chest X-ray."
        )
        try:
            registry = register_medical_resources(CapabilityRegistry())
            graph = _run_graph(job, [
                Node(id="ingest", description="list files",
                     capability="medical_folder_ingestion", depends_on=[],
                     dna=_dna("medical.folder_ingestion")),
                Node(id="img", description="analyze xray",
                     capability="medical_image_analysis", depends_on=["ingest"],
                     dna=_dna("medical.image_analysis")),
                Node(id="chart", description="build chart",
                     capability="medical_patient_synthesis", depends_on=["img"],
                     dna=_dna("medical.patient_synthesis")),
            ], registry)
        finally:
            med_caps.medic_gemma_describe = original

        chart = graph.nodes[-1].output
        self.assertIn("Patient Chart", chart)
        self.assertIn("right-sided opacity", chart.lower())
        self.assertIn("physician/radiologist confirmation", chart.lower())

    # -- 2. blood PDF folder ----------------------------------------------
    def test_blood_folder_lab_values_and_status(self):
        _blood_pdf(self.root, "blood_test.pdf")
        job, manifests = build_medical_job(self.root)
        self.assertEqual([m.detected_type for m in manifests], ["laboratory_report"])

        registry = register_medical_resources(CapabilityRegistry())
        graph = _run_graph(job, [
            Node(id="ingest", description="list files",
                 capability="medical_folder_ingestion", depends_on=[],
                 dna=_dna("medical.folder_ingestion")),
            Node(id="lab", description="analyze lab",
                 capability="medical_laboratory_analysis", depends_on=["ingest"],
                 dna=_dna("medical.laboratory_analysis")),
            Node(id="chart", description="build chart",
                 capability="medical_patient_synthesis", depends_on=["lab"],
                 dna=_dna("medical.patient_synthesis")),
        ], registry)

        lab_out = graph.nodes[1].output
        self.assertIn("Glucose", lab_out)
        self.assertIn("HIGH", lab_out)
        self.assertIn("LOW", lab_out)

        chart = graph.nodes[-1].output
        self.assertIn("142", chart)
        self.assertIn("8.2", chart)
        # 10. -- source + page preservation
        rows = parse_blocks(lab_out)["lab-results"]
        self.assertTrue(all(r["source_file"] for r in rows))
        self.assertTrue(all(r["page"] is not None for r in rows))

    # -- 3. xray + blood folder -------------------------------------------
    def test_xray_plus_blood_parallel_branches_merge(self):
        _blood_pdf(self.root, "blood.pdf")
        _xray_png(self.root, "chest_xray.png")
        job, manifests = build_medical_job(self.root)
        self.assertEqual(len(manifests), 2)

        original = med_caps.medic_gemma_describe
        med_caps.medic_gemma_describe = lambda path, **kw: (
            "Clear lung fields on the X-ray; no acute findings."
        )
        try:
            registry = register_medical_resources(CapabilityRegistry())
            graph = _run_graph(job, [
                Node(id="ingest", description="list files",
                     capability="medical_folder_ingestion", depends_on=[],
                     dna=_dna("medical.folder_ingestion")),
                Node(id="lab", description="analyze lab",
                     capability="medical_laboratory_analysis", depends_on=["ingest"],
                     dna=_dna("medical.laboratory_analysis")),
                Node(id="img", description="analyze xray",
                     capability="medical_image_analysis", depends_on=["ingest"],
                     dna=_dna("medical.image_analysis")),
                Node(id="chart", description="build chart",
                     capability="medical_patient_synthesis", depends_on=["lab", "img"],
                     dna=_dna("medical.patient_synthesis")),
            ], registry)
        finally:
            med_caps.medic_gemma_describe = original

        chart = graph.nodes[-1].output
        self.assertIn("Glucose", chart)
        self.assertIn("clear lung fields", chart.lower())

    # -- 4. prescription + report + image ---------------------------------
    def test_prescription_report_and_image_merge(self):
        (self.root / "prescription.txt").write_text(
            "Prescription for John Smith\n"
            "Amoxicillin 500 mg three times daily for 7 days\n"
            "Diagnosis: acute bronchitis\n"
        )
        (self.root / "discharge_report.txt").write_text(
            "Discharge summary\nDischarge Diagnosis: community-acquired pneumonia\n"
            "Patient Name: John Smith\nAge: 54\nSex: Male\n"
        )
        _xray_png(self.root, "chest.png")
        job, manifests = build_medical_job(self.root)
        types = [m.detected_type for m in manifests]
        self.assertIn("prescription", types)
        self.assertIn("discharge_summary", types)

        original = med_caps.medic_gemma_describe
        med_caps.medic_gemma_describe = lambda path, **kw: (
            "No acute cardiopulmonary abnormalities identified."
        )
        try:
            registry = register_medical_resources(CapabilityRegistry())
            graph = _run_graph(job, [
                Node(id="ingest", description="list files",
                     capability="medical_folder_ingestion", depends_on=[],
                     dna=_dna("medical.folder_ingestion")),
                Node(id="presc", description="analyze prescription",
                     capability="medical_prescription_analysis", depends_on=["ingest"],
                     dna=_dna("medical.prescription_analysis")),
                Node(id="rep", description="analyze report",
                     capability="medical_report_analysis", depends_on=["ingest"],
                     dna=_dna("medical.report_analysis")),
                Node(id="img", description="analyze xray",
                     capability="medical_image_analysis", depends_on=["ingest"],
                     dna=_dna("medical.image_analysis")),
                Node(id="chart", description="build chart",
                     capability="medical_patient_synthesis",
                     depends_on=["presc", "rep", "img"],
                     dna=_dna("medical.patient_synthesis")),
            ], registry)
        finally:
            med_caps.medic_gemma_describe = original

        chart = graph.nodes[-1].output
        self.assertIn("Amoxicillin", chart)
        self.assertIn("community-acquired pneumonia", chart.lower())
        self.assertIn("John Smith", chart)
        self.assertIn("no acute cardiopulmonary", chart.lower())

    # -- 7. corrupt PDF / image -------------------------------------------
    def test_corrupt_files_do_not_fabricate_values(self):
        broken_pdf = self.root / "broken.pdf"
        broken_pdf.write_text("this is not a real pdf at all")
        (self.root / "broken.png").write_bytes(b"\x00\x01\x02")
        job, manifests = build_medical_job(self.root)
        # "broken.pdf" carries no medical keyword -> generic medical document,
        # so the lab node below reports no values instead of inventing them.
        self.assertEqual(manifests[0].detected_type, "medical_document")

        original = med_caps.medic_gemma_describe
        med_caps.medic_gemma_describe = lambda path, **kw: (
            "Degraded image; unable to interpret."
        )
        try:
            registry = register_medical_resources(CapabilityRegistry())
            graph = _run_graph(job, [
                Node(id="ingest", description="list files",
                     capability="medical_folder_ingestion", depends_on=[],
                     dna=_dna("medical.folder_ingestion")),
                Node(id="lab", description="analyze lab",
                     capability="medical_laboratory_analysis", depends_on=["ingest"],
                     dna=_dna("medical.laboratory_analysis")),
                Node(id="img", description="analyze xray",
                     capability="medical_image_analysis", depends_on=["ingest"],
                     dna=_dna("medical.image_analysis")),
                Node(id="chart", description="build chart",
                     capability="medical_patient_synthesis", depends_on=["lab", "img"],
                     dna=_dna("medical.patient_synthesis")),
            ], registry)
        finally:
            med_caps.medic_gemma_describe = original

        chart = graph.nodes[-1].output
        self.assertIn("No laboratory values were extracted", chart)
        self.assertIn("Degraded image", chart)

    # -- 8. model/API failure -> deterministic in-capability fallback ------
    def test_ner_failure_falls_back_to_deterministic_parser(self):
        _blood_pdf(self.root, "blood.pdf")
        job, _ = build_medical_job(self.root)

        registry = register_medical_resources(CapabilityRegistry())
        # No HF_TOKEN, so medic_lab_ner_lines raises ProviderAuthenticationError;
        # the capability must fall back to the deterministic parser.
        out = med_caps.medical_laboratory_analysis_run(job)
        self.assertIn("Glucose", out)
        self.assertIn("HIGH", out)

    # -- 9. agent failure + recovery -> FailureManager degraded ------------
    def test_agent_failure_triggers_failure_manager_recovery(self):
        job, _ = build_medical_job(self.root)
        registry = register_medical_resources(CapabilityRegistry())

        def always_fails(text, instruction=None):
            raise ProviderError("simulated model outage")

        registry._run_fns["medical_laboratory_analysis"] = always_fails
        graph = _run_graph(job, [
            Node(id="lab", description="analyze lab",
                 capability="medical_laboratory_analysis", depends_on=[],
                 dna=_dna("medical.laboratory_analysis")),
        ], registry)

        self.assertEqual(graph.nodes[0].status, "degraded")
        self.assertEqual(graph.nodes[0].routing_mode, "dna")

    # -- 11. lab parsing status against the report's own range -------------
    def test_lab_status_derived_from_report_range(self):
        from aos_v0.medical.clinical import parse_lab_lines

        text = (
            "Glucose\t142\tmg/dL\t70-100 mg/dL\n"
            "Sodium\t140\tmmol/L\t135-145 mmol/L\n"
            "Potassium\t2.9\tmmol/L\t3.5-5.0 mmol/L\n"
            "Calcium\t9.0\tmg/dL\t8.5-10.5 mg/dL\n"
        )
        rows = parse_lab_lines(text, "blood.pdf", 2)
        by_test = {r.test: r for r in rows}
        self.assertEqual(by_test["Glucose"].status, "HIGH")
        self.assertEqual(by_test["Sodium"].status, "NORMAL")
        self.assertEqual(by_test["Potassium"].status, "LOW")
        self.assertEqual(by_test["Calcium"].status, "NORMAL")
        self.assertTrue(all(r.source_file == "blood.pdf" and r.page == 2 for r in rows))

    # -- 12. final chart structure ----------------------------------------
    def test_chart_contains_all_sections_and_disclaimer(self):
        _blood_pdf(self.root, "blood.pdf")
        job, _ = build_medical_job(self.root)
        registry = register_medical_resources(CapabilityRegistry())
        graph = _run_graph(job, [
            Node(id="ingest", description="list files",
                 capability="medical_folder_ingestion", depends_on=[],
                 dna=_dna("medical.folder_ingestion")),
            Node(id="lab", description="analyze lab",
                 capability="medical_laboratory_analysis", depends_on=["ingest"],
                 dna=_dna("medical.laboratory_analysis")),
            Node(id="chart", description="build chart",
                 capability="medical_patient_synthesis", depends_on=["lab"],
                 dna=_dna("medical.patient_synthesis")),
        ], registry)
        chart = graph.nodes[-1].output
        for section in [
            "# Patient Chart",
            "## Diagnostic Laboratory Values",
            "## Imaging Findings",
            "is NOT a medical opinion",
            "Review with a qualified clinician.",
        ]:
            self.assertIn(section, chart)

    # -- workflow sanity ---------------------------------------------------
    def test_build_medical_job_embeds_manifest(self):
        _blood_pdf(self.root, "blood.pdf")
        job, manifests = build_medical_job(self.root, user_prompt="Analyze this")
        self.assertIn("Analyze this", job)
        self.assertIn("MEDICAL WORKFLOW", job)
        self.assertIn("FOLDER:", job)
        from aos_v0.medical.manifest import folder_hint

        self.assertEqual(Path(folder_hint(job)), self.root.resolve())
        self.assertEqual(len(manifests), 1)


if __name__ == "__main__":
    unittest.main()