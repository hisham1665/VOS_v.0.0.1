"""Phase 8 tests -- AOS dynamic orchestration of the exam evaluation workflow.

Covers the deliverable: the exam DAG (``ExamEvaluationTask.build_graph``) is
driven through the AOS Capability Registry -- every node is hydrated with its
Capability DNA, a model is selected per node by the registry's continuous
scorer (wired run_fns, gap G3), and the wave-ordered DAG executes end to end
into a plan-shaped ``ExamRunResult`` carrying the full ``ExecutionTrace`` of
the dynamic selection. Golden rules hold inside the orchestration: unverifiable
identity routes the paper to review but never zeroes marks, and an honest
two-agent disagreement surfaces as ``AGENT_DISAGREEMENT`` review.
"""

import unittest

from aos_v0.core.capability_registry import CapabilityRegistry
from aos_v0.exam.models import (
    EXAM_REQUIRED_FLAGS,
    AnswerKey,
    EvaluationSettings,
    EvalReviewReason,
    ExamConfiguration,
    ExamEvaluationTask,
    PaperReference,
    Question,
)
from aos_v0.exam.ocr.models import (
    LayoutRegion,
    OcrBlock,
    OcrDocument,
    OcrPage,
    RegionType,
)
from aos_v0.exam.orchestrator import (
    ExamOrchestrationError,
    ExamOrchestrator,
    LocalExamRunner,
    build_exam_registry,
    build_local_exam_manifests,
    dna_for_node,
    register_local_exam_transport,
)
from aos_v0.exam.structure.identity import extract_student_identity


def question(qid, concepts, marks=6):
    return Question(
        question_id=qid,
        text="Explain.",
        max_marks=marks,
        answer_key=AnswerKey(expected_concepts=concepts),
        rubric={"criteria": [
            {"criterion": concept.replace(" ", "_"), "marks": marks // len(concepts)}
            for concept in concepts
        ]},
    )


def make_task(*questions):
    return ExamEvaluationTask(
        task_id="task_orch",
        paper=PaperReference(paper_id="p42", file_path="p42.pdf"),
        exam=ExamConfiguration(exam_id="E", title="Mid", questions=list(questions)),
    )


def pages_for(answers, *, with_identity=True):
    blocks = []
    if with_identity:
        blocks.extend([
            OcrBlock(text="Name: Alice Clark", bbox=[0, 30, 400, 80], confidence=0.95),
            OcrBlock(text="Roll: 42", bbox=[0, 90, 200, 130], confidence=0.95),
        ])
    for i, (label, text) in enumerate(answers):
        y = 220 + i * 160
        blocks.append(OcrBlock(text=label, bbox=[0, y, 300, y + 50], confidence=0.95))
        blocks.append(OcrBlock(text=text, bbox=[0, y + 60, 600, y + 140], confidence=0.95))
    regions = [LayoutRegion(region_type=RegionType.HEADER, bbox=[0, 20, 1000, 180],
                            confidence=0.95, label="header")] if with_identity else []
    return OcrDocument(pages=[OcrPage(page_no=1, source="p1.png", blocks=blocks,
                                      regions=regions, confidence=0.95)])


def agree_doc():
    return pages_for([("Q1. Explain", "hold and wait and circular wait")])


class RegistryWiringTests(unittest.TestCase):
    def test_phase1_declared_registry_stays_unroutable(self):
        registry = build_exam_registry()
        self.assertEqual(registry.routable_ids(), [])

    def test_local_transport_wires_all_resources(self):
        registry = build_exam_registry()
        runner_less = None
        manifests = build_local_exam_manifests()
        wired_ids = {m.resource_id for m in manifests}
        self.assertEqual(len(wired_ids), 8)

        from aos_v0.exam.resources import exam_resource_ids
        self.assertEqual(wired_ids, set(exam_resource_ids()))

        class _Stub:
            def dispatch(self, *a, **k):
                return {}

        register_local_exam_transport(registry, _Stub())
        self.assertEqual(set(registry.routable_ids()), wired_ids)
        self.assertEqual(runner_less is None, True)

    def test_local_manifests_cover_requirement_flags(self):
        union = set()
        for manifest in build_local_exam_manifests():
            union.update(manifest.capabilities)
        self.assertTrue(EXAM_REQUIRED_FLAGS.issubset(union))

    def test_dna_for_node_hydration(self):
        dna = dna_for_node("semantic_answer_evaluation")
        self.assertEqual(dna.flags, ["semantic.answer_evaluation"])
        self.assertEqual(dna.extracted_by, "exam.orchestrator")
        for capability, flag in (
            ("document_ocr", "document.ocr"),
            ("document_layout", "document.layout"),
            ("student_id_extraction", "student_id.extraction"),
            ("question_segmentation", "question.segmentation"),
            ("answer_verification", "answer.verification"),
            ("evaluation_reconciliation", "evaluation.reconciliation"),
            ("report_generation", "report.generation"),
        ):
            self.assertEqual(dna_for_node(capability).flags, [flag], capability)
        self.assertIsNone(dna_for_node("not_an_exam_capability"))

    def test_registry_selects_deterministically_per_capability(self):
        registry = build_exam_registry()
        register_local_exam_transport(registry, _DummyRunner())
        winners = {}
        for manifest in build_local_exam_manifests():
            for flag in manifest.capabilities:
                dna = dna_for_node(_projected_capability(flag))
                if dna is None:
                    continue
                decision = registry.select(dna)
                winners.setdefault(flag, decision.resource_id)
        self.assertEqual(winners["document.ocr"], "document_ocr")
        self.assertEqual(winners["document.layout"], "document_layout")
        self.assertEqual(winners["question.segmentation"], "question_segmentation")
        self.assertEqual(winners["semantic.answer_evaluation"], "semantic_answer_evaluation")


def _projected_capability(flag):
    return {
        "document.ocr": "document_ocr",
        "document.layout": "document_layout",
        "student_id.extraction": "student_id_extraction",
        "question.segmentation": "question_segmentation",
        "answer.extraction": "question_segmentation",
        "semantic.answer_evaluation": "semantic_answer_evaluation",
        "answer.verification": "answer_verification",
        "evaluation.reconciliation": "evaluation_reconciliation",
        "confidence.estimation": "report_generation",
        "report.generation": "report_generation",
    }[flag]


class _DummyRunner:
    def dispatch(self, *a, **k):
        return {}


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.orch = ExamOrchestrator()
        self.task = make_task(question("Q1", ["hold and wait", "circular wait"]))

    def test_literal_answer_adopted_from_both_without_review(self):
        result = self.orch.execute(self.task, ocr_document=agree_doc())
        row = result.rows[0]
        self.assertEqual(result.status, "ok")
        self.assertTrue(row.agreed)
        self.assertEqual(row.adopted_from, "both")
        self.assertEqual(row.marks, 6.0)
        self.assertEqual(row.max_marks, 6.0)
        self.assertEqual(result.review_reasons, [])
        self.assertFalse(result.needs_review)
        self.assertEqual(result.total_marks, 6.0)
        self.assertEqual(result.confidence, row.confidence)

    def test_identity_is_extracted_by_the_pipeline(self):
        result = self.orch.execute(self.task, ocr_document=agree_doc())
        self.assertEqual(result.student["name"], "Alice Clark")
        self.assertEqual(result.student["roll_no"], "42")
        self.assertEqual(result.paper_id, "p42")

    def test_missing_identity_routes_to_review_but_never_zeroes(self):
        doc = pages_for([("Q1. Explain", "hold and wait and circular wait")],
                        with_identity=False)
        result = self.orch.execute(self.task, ocr_document=doc)
        self.assertEqual(result.status, "review")
        self.assertTrue(result.needs_review)
        self.assertIn("identity_uncertain", result.review_reasons)
        self.assertEqual(result.rows[0].marks, 6.0)

    def test_paraphrase_divergence_routes_to_agent_disagreement(self):
        doc = pages_for([("Q1. Explain",
                          "a process holds a resource and waits for another "
                          "while holding it, with cycles possible")])
        result = self.orch.execute(self.task, ocr_document=doc)
        row = result.rows[0]
        self.assertFalse(row.agreed)
        self.assertEqual(row.adopted_from, "primary")
        self.assertIn(EvalReviewReason.AGENT_DISAGREEMENT.value, row.review_reasons)
        self.assertIn("hold and wait", row.disputed_concepts)
        self.assertTrue(row.needs_review)
        self.assertEqual(result.status, "review")

    def test_two_agent_disabled_runs_primary_only(self):
        settings = EvaluationSettings(two_agent_evaluation=False)
        result = self.orch.execute(self.task, ocr_document=agree_doc(), settings=settings)
        row = result.rows[0]
        self.assertEqual(row.adopted_from, "primary")
        self.assertTrue(row.agreed)
        self.assertEqual(row.review_reasons, [])

    def test_multiple_questions_total(self):
        task = make_task(
            question("Q1", ["hold and wait", "circular wait"]),
            question("Q2", ["connection oriented", "reliable"], marks=4),
        )
        result = self.orch.execute(task, ocr_document=pages_for([
            ("Q1. Explain", "hold and wait and circular wait"),
            ("Q2. What", "connection oriented and reliable"),
        ]))
        self.assertEqual([r.marks for r in result.rows], [6.0, 4.0])
        self.assertEqual(result.total_marks, 10.0)
        self.assertEqual(result.max_marks, 10.0)
        self.assertEqual(result.status, "ok")

    def test_no_evidence_raises_typed_error(self):
        with self.assertRaises(ExamOrchestrationError):
            self.orch.execute(self.task)

    def test_result_plan_shape(self):
        result = self.orch.execute(self.task, ocr_document=agree_doc())
        plan = result.to_plan_json()
        for key in ("task_id", "paper_id", "exam_title", "student", "rows",
                    "total_marks", "max_marks", "confidence", "review_reasons",
                    "status", "artifacts", "trace"):
            self.assertIn(key, plan)
        self.assertEqual(result.scored_rows[0].marks, 6.0)


class DynamicSelectionTraceTests(unittest.TestCase):
    def test_every_node_routed_through_registry(self):
        task = make_task(question("Q1", ["hold and wait", "circular wait"]))
        result = ExamOrchestrator().execute(task, ocr_document=agree_doc())
        graph = task.build_graph()
        self.assertEqual(len(result.trace.nodes), len(graph.nodes))
        self.assertGreater(result.trace.waves, 0)
        for node_trace in result.trace.nodes:
            self.assertTrue(node_trace.resource_id)
            self.assertEqual(node_trace.routing_mode, "dna")
            self.assertEqual(node_trace.model, "local-structural-engine")
            self.assertEqual(node_trace.status, "done")
            self.assertGreater(node_trace.output_chars, 0)

    def test_winners_resolve_to_the_owning_resource(self):
        task = make_task(question("Q1", ["hold and wait", "circular wait"]))
        result = ExamOrchestrator().execute(task, ocr_document=agree_doc())
        winners = {}
        for node_trace in result.trace.nodes:
            winners[node_trace.capability] = node_trace.resource_id
        self.assertEqual(winners["document_ocr"], "document_ocr")
        self.assertEqual(winners["document_layout"], "document_layout")
        self.assertEqual(winners["student_id_extraction"], "student_id_extraction")
        self.assertEqual(winners["question_segmentation"], "question_segmentation")
        self.assertEqual(winners["semantic_answer_evaluation"], "semantic_answer_evaluation")
        self.assertEqual(winners["answer_verification"], "answer_verification")
        self.assertEqual(winners["evaluation_reconciliation"], "evaluation_reconciliation")
        self.assertEqual(winners["report_generation"], "report_generation")

    def test_independent_identity_extraction_matches_orchestrated_value(self):
        task = make_task(question("Q1", ["reliable"]))
        document = agree_doc()
        direct, _ = extract_student_identity(document)
        result = ExamOrchestrator().execute(task, ocr_document=document)
        self.assertEqual(result.student["name"], direct.name)
        self.assertEqual(result.student["roll_no"], direct.roll_no)


class _FlakyOcrRunner(LocalExamRunner):
    """Fails the OCR stage a fixed number of times, then behaves normally."""

    def __init__(self, *args, fail_times=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.ocr_calls = 0
        self.fail_times = fail_times

    def dispatch(self, resource_id, inputs, *, node_id=None):
        if resource_id == "document_ocr":
            self.ocr_calls += 1
            if self.ocr_calls <= self.fail_times:
                raise RuntimeError("ocr backend unavailable")
        return super().dispatch(resource_id, inputs, node_id=node_id)


class _DeadOcrRunner(_FlakyOcrRunner):
    def dispatch(self, resource_id, inputs, *, node_id=None):
        if resource_id == "document_ocr":
            self.ocr_calls += 1
            raise RuntimeError("ocr backend permanently down")
        return super().dispatch(resource_id, inputs, node_id=node_id)


class _DeadEvalRunner(LocalExamRunner):
    """Both evaluation stages (primary evaluator + verifier) are down."""

    def dispatch(self, resource_id, inputs, *, node_id=None):
        if resource_id in ("semantic_answer_evaluation", "answer_verification"):
            raise RuntimeError("evaluator crashed")
        return super().dispatch(resource_id, inputs, node_id=node_id)


class Phase9RecoveryTests(unittest.TestCase):
    """Phase 9 -- recovery + confidence folded into the orchestrated run."""

    def setUp(self):
        self.orch = ExamOrchestrator()
        self.task = make_task(question("Q1", ["hold and wait", "circular wait"]))

    def test_healthy_run_reports_no_recovery(self):
        result = self.orch.execute(self.task, ocr_document=agree_doc())
        self.assertEqual(result.recovery, [])
        self.assertEqual(result.escalations, 0)
        self.assertIn(result.confidence_category, ("high", "medium", "low"))
        self.assertGreaterEqual(len(result.confidence_evidence["components"]), 5)
        for node in result.trace.nodes:
            self.assertEqual(node.failure_class, "")
            self.assertFalse(node.recovered)
            self.assertFalse(node.degraded)
            self.assertEqual(node.attempt_count, 0)

    def test_transient_outage_recovers_inside_the_trace(self):
        runner = _FlakyOcrRunner(self.task, source=None, roster=None,
                                 ocr_document=agree_doc())
        result = self.orch.execute(self.task, runner=runner)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.escalations, 0)
        self.assertEqual(len(result.recovery), 1)
        self.assertEqual(result.recovery[0]["failure_class"], "resource.outage")
        self.assertTrue(result.recovery[0]["recovered"])
        ocr_trace = [n for n in result.trace.nodes
                     if n.capability == "document_ocr"][0]
        self.assertTrue(ocr_trace.recovered)
        self.assertEqual(ocr_trace.attempt_count, 1)
        self.assertEqual(ocr_trace.failure_class, "resource.outage")

    def test_exhausted_structural_ladder_routes_whole_paper_to_review(self):
        runner = _DeadOcrRunner(self.task, source=None, roster=None,
                                ocr_document=agree_doc())
        result = self.orch.execute(self.task, runner=runner)
        self.assertEqual(result.status, "review")
        self.assertEqual(result.rows, [])
        self.assertEqual(result.total_marks, 0.0)
        self.assertEqual(result.max_marks, 6.0)
        self.assertIn(EvalReviewReason.RECOVERY_FAILED.value, result.review_reasons)
        self.assertEqual(result.confidence_category, "low")
        self.assertEqual(result.escalations, 1)
        self.assertTrue(result.recovery[0]["degraded"])
        ocr_trace = [n for n in result.trace.nodes
                     if n.capability == "document_ocr"][0]
        self.assertTrue(ocr_trace.degraded)
        self.assertEqual(ocr_trace.status, "degraded")

    def test_exhausted_eval_ladder_holds_marks_not_a_zero(self):
        runner = _DeadEvalRunner(self.task, source=None, roster=None,
                                 ocr_document=agree_doc())
        result = self.orch.execute(self.task, runner=runner)
        row = result.rows[0]
        self.assertIsNone(row.marks)
        self.assertTrue(row.needs_review)
        self.assertEqual(row.adopted_from, "none")
        self.assertIn(EvalReviewReason.RECOVERY_FAILED.value, row.review_reasons)
        self.assertEqual(result.status, "review")
        self.assertEqual(result.escalations, 2)
        self.assertEqual(result.total_marks, 0.0)

    def test_low_confidence_ocr_payload_routes_to_ocr_review(self):
        low_doc = pages_for([("Q1. Explain",
                              "hold and wait and circular wait")],
                            with_identity=True)
        for page in low_doc.pages:
            page.confidence = 0.5
            for block in page.blocks:
                block.confidence = 0.5
            for region in page.regions:
                region.confidence = 0.5
        result = self.orch.execute(self.task, ocr_document=low_doc)
        self.assertEqual(result.status, "review")
        self.assertEqual(result.rows, [])
        self.assertIn(EvalReviewReason.LOW_OCR_CONFIDENCE.value,
                      result.review_reasons)
        self.assertTrue(result.recovery[0]["degraded"])
        self.assertEqual(result.recovery[0]["failure_class"], "tool.low_confidence")

    def test_recovery_disabled_still_reports_degradation(self):
        settings = EvaluationSettings(recovery_enabled=False)
        runner = _DeadOcrRunner(self.task, source=None, roster=None,
                                ocr_document=agree_doc())
        result = self.orch.execute(self.task, runner=runner, settings=settings)
        self.assertEqual(result.status, "review")
        self.assertEqual(result.rows, [])
        self.assertEqual(result.escalations, 1)
        self.assertEqual(len(result.recovery), 1)
        self.assertTrue(result.recovery[0]["degraded"])

    def test_recovered_run_carries_confidence_evidence(self):
        result = self.orch.execute(self.task, ocr_document=agree_doc())
        evidence = result.confidence_evidence
        self.assertIn("overall", evidence)
        self.assertIn("category", evidence)
        self.assertIn("semantic", evidence["components"])
        self.assertEqual(result.confidence, result.rows[0].confidence)


if __name__ == "__main__":
    unittest.main()