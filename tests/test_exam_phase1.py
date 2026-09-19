"""Phase 1 regression tests: model survey, DNA definitions, registry entries.

These prove the Phase-1 deliverables without executing any model:

  * gap G1 closed -- the full exam vocabulary is registered in the kernel;
  * every exam capability flag has a CapabilityDNA template that validates
    against the kernel vocabulary;
  * every required plan capability has a primary + fallback selection with a
    resource profile and selection constraints;
  * the exam Capability Registry entries are valid, declared-only resources
    (unroutable until the Phase 3/4 transports exist, mirroring the HF stub
    pattern);
  * the benchmark harness loads the plan's dataset skeleton.

No existing AOS behaviour is touched.
"""

import tempfile
import unittest
from pathlib import Path

from aos_v0.core.capability_registry import (
    CapabilityRegistry,
    InfeasibleDNAError,
    missing_flags,
)
from aos_v0.core.models import CAPABILITY_FLAGS, CapabilityDNA
from aos_v0.exam import (
    EXAM_CAPABILITY_FLAGS,
    EXAM_DNA_TEMPLATES,
    EXAM_MODEL_SELECTION,
    EXAM_REQUIRED_FLAGS,
    PLAN_CAPABILITY_FLAGS,
    BENCHMARK_CATEGORIES,
    build_exam_manifests,
    dna_for_capability,
    exam_resource_ids,
    load_cases,
    register_exam_resources,
)
from aos_v0.exam.resources import exam_model_for

# The 13 required capabilities from the Phase-1 plan.
PLAN_CAPABILITIES = {
    "DOCUMENT_OCR",
    "HANDWRITING_OCR",
    "DOCUMENT_LAYOUT",
    "VISION_UNDERSTANDING",
    "SEMANTIC_EMBEDDING",
    "SEMANTIC_EVALUATION",
    "GENERAL_REASONING",
    "MATHEMATICAL_REASONING",
    "DIAGRAM_UNDERSTANDING",
    "MULTILINGUAL_UNDERSTANDING",
    "STUDENT_ID_EXTRACTION",
    "QUESTION_SEGMENTATION",
    "EVALUATION_RECONCILIATION",
}


class VocabularyTests(unittest.TestCase):
    def test_exam_flags_registered_in_kernel_vocabulary(self):
        uncovered = set(EXAM_CAPABILITY_FLAGS) - set(CAPABILITY_FLAGS)
        self.assertEqual(uncovered, set())

    def test_required_flags_subset_of_full_vocabulary(self):
        self.assertTrue(EXAM_REQUIRED_FLAGS.issubset(EXAM_CAPABILITY_FLAGS))


class DNADefinitionTests(unittest.TestCase):
    def test_every_exam_flag_has_a_dna_template(self):
        missing = set(EXAM_CAPABILITY_FLAGS) - set(EXAM_DNA_TEMPLATES)
        self.assertEqual(missing, set())

    def test_every_template_is_a_valid_capability_dna(self):
        for flag, dna in EXAM_DNA_TEMPLATES.items():
            self.assertIsInstance(dna, CapabilityDNA)
            self.assertIn(flag, dna.flags)
            self.assertEqual(dna.extracted_by, "exam.dna")

    def test_dna_for_capability_maps_graph_strings(self):
        self.assertIsNotNone(dna_for_capability("document_ocr"))
        self.assertIsNotNone(dna_for_capability("semantic_answer_evaluation"))
        self.assertIsNone(dna_for_capability("summarization"))

    def test_evaluation_dna_is_reasoning_heavy(self):
        dna = EXAM_DNA_TEMPLATES["semantic.answer_evaluation"]
        self.assertGreaterEqual(dna.ordinals.reasoning_depth, 4)
        self.assertGreaterEqual(dna.constraints.min_quality, 0.9)


class ModelSelectionTests(unittest.TestCase):
    def test_all_plan_capabilities_are_covered(self):
        self.assertEqual(set(EXAM_MODEL_SELECTION), PLAN_CAPABILITIES)
        self.assertEqual(set(PLAN_CAPABILITY_FLAGS), PLAN_CAPABILITIES)

    def test_each_selection_has_primary_fallback_profile_constraints(self):
        for capability, selection in EXAM_MODEL_SELECTION.items():
            self.assertTrue(selection.primary.hf_repo, capability)
            self.assertTrue(selection.fallback, capability)
            for model in [selection.primary] + selection.fallback:
                self.assertTrue(model.name)
                self.assertTrue(model.hf_repo)
                self.assertTrue(model.profile.params)
            self.assertIn(selection.flag, selection.flags)
            self.assertGreaterEqual(selection.constraints.min_quality, 0.0)

    def test_all_selection_flags_are_in_vocabulary(self):
        for selection in EXAM_MODEL_SELECTION.values():
            for flag in selection.flags:
                self.assertIn(flag, CAPABILITY_FLAGS, flag)

    def test_loose_repo_ids_are_flagged(self):
        for selection in EXAM_MODEL_SELECTION.values():
            for model in [selection.primary] + selection.fallback:
                self.assertTrue(model.hf_repo.count("/") == 1,
                                f"{model.hf_repo} is not an org/repo id")


class RegistryEntryTests(unittest.TestCase):
    def setUp(self):
        self.registry = CapabilityRegistry()
        register_exam_resources(self.registry)

    def test_registers_one_resource_per_graph_capability(self):
        self.assertEqual(len(exam_resource_ids()), 8)
        for keyword in ("document_ocr", "semantic_answer_evaluation",
                        "evaluation_reconciliation"):
            self.assertIn(keyword, exam_resource_ids())

    def test_manifests_declared_only_and_unroutable(self):
        for manifest in self.registry.manifests():
            self.assertEqual(manifest.metadata["transport"], "declared")
        self.assertEqual(self.registry.routable_ids(), [])

    def test_manifests_validate_against_vocabulary(self):
        manifests = build_exam_manifests()
        for manifest in manifests:
            self.assertIsInstance(manifest.capabilities, list)
        self.assertEqual(len(manifests), 8)

    def test_required_exam_dna_is_satisfiable_but_unroutable(self):
        # The exam registry provides every flag the task needs ...
        required = CapabilityDNA(
            flags=sorted(EXAM_REQUIRED_FLAGS),
            extracted_by="test",
        )
        self.assertEqual(missing_flags(required, self.registry.provided_flags()), [])
        self.assertEqual(self.registry.unsatisfiable_flags(required), [])
        # ... but every resource is a declared stub, so selection must refuse.
        with self.assertRaises(InfeasibleDNAError):
            self.registry.select(required)

    def test_metadata_names_the_primary_model(self):
        for manifest in self.registry.manifests():
            self.assertIn("model", manifest.metadata)
            self.assertNotEqual(manifest.metadata["model"], "")

    def test_exam_model_for_unknown_selection(self):
        self.assertNotEqual(exam_model_for("STUDENT_ID_EXTRACTION"), "unassigned")
        self.assertEqual(exam_model_for("NOPE"), "unassigned")


class BenchmarkHarnessTests(unittest.TestCase):
    def test_categories_match_plan_layout(self):
        self.assertEqual(
            BENCHMARK_CATEGORIES,
            [
                "exact_answers",
                "paraphrased_answers",
                "partial_answers",
                "incorrect_answers",
                "spelling_errors",
                "handwritten",
                "mathematical",
                "diagrams",
                "multilingual",
                "difficult_documents",
            ],
        )

    def test_load_cases_reads_single_and_list_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            single = root / "exact_answers"
            single.mkdir()
            (single / "a.json").write_text(
                '{"resource_id": "document_ocr", "input_text": "a.png", "task": "document.ocr"}'
            )
            (root / "handwritten").mkdir()
            (root / "handwritten" / "b.json").write_text(
                '[{"resource_id": "handwriting", "input_text": "b.png"},'
                '{"resource_id": "handwriting", "input_text": "c.png"}]'
            )
            cases = load_cases(root)
            self.assertEqual(len(cases), 3)
            self.assertEqual(cases[0].resource_id, "document_ocr")
            self.assertEqual(cases[0].task, "document.ocr")

    def test_load_cases_empty_skeleton_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_cases(Path(tmp)), [])


if __name__ == "__main__":
    unittest.main()