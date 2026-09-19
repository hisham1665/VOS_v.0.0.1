"""Phase 11 -- Human Review and Audit.

Covers the review queue (per-question + paper-level cards), the persistent
store + append-only audit log, manual mark editing (accept / modify /
escalate), evaluation history, the ASCII dashboard, and the CLI.
"""

import tempfile
import unittest
from pathlib import Path

from aos_v0.exam.orchestrator import ExamOrchestrator
from aos_v0.exam.review import (
    AuditEvent,
    ReviewDecision,
    ReviewItem,
    ReviewStatus,
    ReviewStore,
    collect_review_items,
    render_card,
    render_stats,
)
from aos_v0.exam.review.collector import row_to_review_item

from tests.test_orchestration import (
    agree_doc,
    make_task,
    pages_for,
    question,
)


def make_exam():
    return make_task(question("Q1", ["hold and wait", "circular wait"]))


class EvidenceTrailTests(unittest.TestCase):
    """The QuestionRow evidence the audit trail is built from."""

    def setUp(self):
        self.orch = ExamOrchestrator()
        self.task = make_exam()

    def test_rows_carry_answer_text_and_agent_verdicts(self):
        result = self.orch.execute(self.task, ocr_document=agree_doc())
        row = result.rows[0]
        self.assertIn("hold and wait", row.answer_text.lower())
        self.assertIn("primary", row.agents)
        self.assertIn("verifier", row.agents)
        self.assertEqual(row.agents["primary"]["marks"], 6.0)

    def test_disagreement_row_carries_both_agents(self):
        result = self.orch.execute(
            self.task,
            ocr_document=pages_for([("Q1. Explain",
                                     "a process holds a resource and waits "
                                     "for another while holding it, "
                                     "with cycles possible")]),
        )
        row = result.rows[0]
        self.assertTrue(row.needs_review)
        self.assertIn("agent_disagreement", row.review_reasons)
        self.assertEqual(len(row.agents), 2)
        self.assertEqual(row.agents["primary"]["agent"], "primary")


class CollectReviewItemsTests(unittest.TestCase):
    def setUp(self):
        self.orch = ExamOrchestrator()
        self.task = make_task(
            question("Q1", ["hold and wait", "circular wait"]),
            question("Q2", ["connection oriented", "reliable"], marks=4),
        )

    def test_clean_evaluation_produces_no_cards(self):
        result = self.orch.execute(self.task, ocr_document=pages_for([
            ("Q1. Explain", "hold and wait and circular wait"),
            ("Q2. What", "connection oriented and reliable"),
        ]))
        items = collect_review_items(result, questions=self.task.exam.questions)
        self.assertEqual(items, [])

    def test_disagreement_becomes_a_per_question_card(self):
        result = self.orch.execute(
            self.task,
            ocr_document=pages_for([
                ("Q1. Explain", "a process holds a resource and waits for "
                                "another while holding it, with cycles"),
                ("Q2. What", "connection oriented and reliable"),
            ]),
        )
        items = collect_review_items(result, questions=self.task.exam.questions)
        self.assertEqual(len(items), 1)
        card = items[0]
        self.assertFalse(card.paper_level)
        self.assertEqual(card.paper_id, result.paper_id)
        self.assertEqual(card.question_id, "Q1")
        self.assertEqual(card.question_text, "Explain.")
        self.assertIn("agent_disagreement", card.review_reasons)
        self.assertTrue(card.disagreement)
        self.assertEqual(card.agent1["agent"], "primary")
        self.assertEqual(card.agent2["agent"], "verifier")
        self.assertIsNotNone(card.agent1.get("marks"))
        self.assertGreater(card.proposed_confidence, 0.0)
        self.assertEqual(card.reconciliation["adopted_from"],
                         result.rows[0].adopted_from)
        self.assertEqual(card.proposed_marks, result.rows[0].marks)
        self.assertGreater(len(card.model_info), 0)

    def test_missing_identity_becomes_a_paper_level_card(self):
        doc = pages_for([("Q1. Explain", "hold and wait and circular wait")],
                        with_identity=False)
        result = self.orch.execute(self.task, ocr_document=doc)
        items = collect_review_items(result, questions=self.task.exam.questions)
        self.assertEqual(len(items), 1)
        card = items[0]
        self.assertTrue(card.paper_level)
        self.assertEqual(card.question_id, "*")
        self.assertEqual(card.review_reasons, ["identity_uncertain"])
        self.assertIsNone(card.proposed_marks)

    def test_row_and_paper_cards_coexist(self):
        result = self.orch.execute(
            self.task,
            ocr_document=pages_for([
                ("Q1. Explain", "a process holds a resource and waits for "
                                "another while holding it"),
                ("Q2. What", "connection oriented and reliable"),
            ], with_identity=False),
        )
        items = collect_review_items(result, questions=self.task.exam.questions)
        self.assertEqual(len(items), 2)
        ids = {item.question_id for item in items}
        self.assertEqual(ids, {"Q1", "*"})

    def test_row_to_card_keeps_the_full_audit_trail(self):
        result = self.orch.execute(self.task, ocr_document=agree_doc())
        row = result.rows[0]
        plain = row.model_copy(update={"needs_review": True,
                                       "review_reasons": ["low_evaluation_confidence"]})
        card = row_to_review_item(result, plain, answer_key_version="2",
                                  rubric_version="3", question_text="explain")
        self.assertEqual(card.answer_key_version, "2")
        self.assertEqual(card.rubric_version, "3")
        self.assertEqual(card.proposed_marks, row.marks)
        self.assertEqual(card.reconciliation["adopted_from"], "both")
        self.assertIsNone(card.status.value and card.final_marks)


class ReviewStoreTests(unittest.TestCase):
    def make_result(self):
        return {
            "task_id": "t1",
            "paper_id": "p42",
            "exam_title": "OS",
            "student": {"name": "Alice", "roll_no": "42"},
            "rows": [],
            "total_marks": 0.0,
            "max_marks": 6.0,
            "confidence": 0.6,
            "review_reasons": ["identity_uncertain"],
            "needs_review": True,
            "status": "review",
        }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.store = ReviewStore(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def card(self, **overrides):
        fields = dict(
            id="p42:Q1",
            paper_id="p42",
            question_id="Q1",
            question_text="explain",
            student={"name": "Alice", "roll_no": "42"},
            answer_text="answer text",
            ocr_confidence=0.4,
            agent1={"agent": "primary", "marks": 6.0, "max_marks": 6.0, "confidence": 0.9},
            agent2={"agent": "verifier", "marks": 5.0, "max_marks": 6.0, "confidence": 0.5},
            disagreement=True,
            reconciliation={"adopted_from": "primary"},
            proposed_marks=5.0,
            max_marks=6.0,
            proposed_confidence=0.62,
            review_reasons=["agent_disagreement"],
        )
        fields.update(overrides)
        return ReviewItem(**fields)

    def test_enqueue_adds_and_persists_cards(self):
        self.store.enqueue([self.card()])
        other = ReviewStore(self.dir)
        self.assertEqual(len(other.pending()), 1)
        self.assertEqual(other.pending()[0].paper_id, "p42")
        self.assertEqual(other.stats().pending, 1)
        self.assertEqual(other.stats().by_status["pending"], 1)

    def test_enqueue_is_idempotent(self):
        added = self.store.enqueue([self.card(), self.card()])
        self.assertEqual(added, ["p42:Q1"])
        self.assertEqual(len(self.store.pending()), 1)
        self.assertEqual(len(self.store.audit_events()), 1)

    def test_force_reenqueues_and_audits(self):
        self.store.enqueue([self.card()])
        resolved = self.store.resolve("p42:Q1", ReviewDecision.ACCEPT, reviewer="priya")
        self.store.enqueue([self.card()], force=True)
        self.assertEqual(resolved.status, ReviewStatus.REVIEWED)
        self.assertEqual(self.store.get("p42:Q1").status, ReviewStatus.PENDING)
        actions = [e.action for e in self.store.audit_events()]
        self.assertIn("REENQUEUE", actions)

    def test_accept_adopts_the_proposal(self):
        self.store.enqueue([self.card()])
        resolved = self.store.resolve("p42:Q1", ReviewDecision.ACCEPT, reviewer="priya")
        self.assertEqual(resolved.final_marks, 5.0)
        self.assertEqual(resolved.status, ReviewStatus.REVIEWED)
        self.assertEqual(resolved.reviewer, "priya")
        self.assertIsNotNone(resolved.reviewed_at)
        events = self.store.audit_events("p42")
        self.assertEqual(len(events), 2)
        self.assertTrue(all(isinstance(e, AuditEvent) for e in events))
        resolve_event = [e for e in events if e.action == "RESOLVE"][0]
        self.assertEqual(resolve_event.payload["final_marks"], 5.0)

    def test_modify_sets_final_marks(self):
        self.store.enqueue([self.card()])
        resolved = self.store.resolve("p42:Q1", ReviewDecision.MODIFY,
                                      final_marks=4.5, reviewer="ravi",
                                      note="half credit for missing concept")
        self.assertEqual(resolved.final_marks, 4.5)
        self.assertEqual(resolved.note, "half credit for missing concept")

    def test_modify_rejects_out_of_range_marks(self):
        self.store.enqueue([self.card()])
        with self.assertRaises(ValueError):
            self.store.resolve("p42:Q1", ReviewDecision.MODIFY, final_marks=7.0)
        with self.assertRaises(ValueError):
            self.store.resolve("p42:Q1", ReviewDecision.MODIFY, final_marks=-1.0)
        with self.assertRaises(ValueError):
            self.store.resolve("p42:Q1", ReviewDecision.MODIFY)

    def test_escalate_leaves_no_final_mark(self):
        self.store.enqueue([self.card()])
        resolved = self.store.resolve("p42:Q1", ReviewDecision.ESCALATE,
                                      reviewer="siren")
        self.assertIsNone(resolved.final_marks)
        self.assertEqual(resolved.decision, ReviewDecision.ESCALATE)
        with self.assertRaises(ValueError):
            self.store.resolve("p42:Q1", ReviewDecision.ESCALATE, final_marks=3.0)

    def test_accept_requires_a_proposal(self):
        self.store.enqueue([self.card(proposed_marks=None, max_marks=6.0)])
        with self.assertRaises(ValueError):
            self.store.resolve("p42:Q1", ReviewDecision.ACCEPT)

    def test_evaluation_history_returns_cards_per_paper(self):
        self.store.enqueue([self.card()])
        self.store.enqueue([self.card(id="p42:*", question_id="*",
                                      proposed_marks=None)])
        self.store.enqueue([self.card(id="p7:Q1", paper_id="p7")])
        history = self.store.evaluation_history("p42")
        self.assertEqual({i.id for i in history}, {"p42:Q1", "p42:*"})
        self.assertEqual(len(self.store.audit_events("p42")), 2)

    def test_cli_end_to_end(self):
        from aos_v0.exam.review import review_cli_main
        from unittest.mock import patch

        self.store.enqueue([self.card()])
        with patch("builtins.print") as mock_print:
            rc = review_cli_main([self.dir, "list"])
            self.assertEqual(rc, 0)
            rendered = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
            self.assertIn("p42:Q1", rendered)
            self.assertIn("agent_disagreement", rendered)
        rc = review_cli_main([self.dir, "resolve", "p42:Q1",
                              "--decision", "accept", "--reviewer", "cli"])
        self.assertEqual(rc, 0)
        self.assertEqual(ReviewStore(self.dir).get("p42:Q1").status,
                         ReviewStatus.REVIEWED)
        rc = review_cli_main([self.dir, "history", "--paper", "p42", "--json"])
        self.assertEqual(rc, 0)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.store = ReviewStore(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_card_shows_evidence_and_actions(self):
        card = ReviewItem(
            id="p1:Q7",
            paper_id="p1",
            question_id="Q7",
            question_text="Explain TCP",
            student={"name": "23CS042", "roll_no": "23CS042"},
            answer_text="TCP is a connection oriented",
            ocr_confidence=0.61,
            agent1={"agent": "primary", "marks": 8.0, "max_marks": 10.0, "confidence": 0.8},
            agent2={"agent": "verifier", "marks": 5.0, "max_marks": 10.0, "confidence": 0.5},
            disagreement=True,
            proposed_marks=8.0,
            max_marks=10.0,
            proposed_confidence=0.61,
            review_reasons=["agent_disagreement"],
        )
        text = render_card(card)
        for needle in ("REVIEW REQUIRED", "Q7", "TCP is a connection oriented",
                       "Agent 1", "Agent 2", "8 / 10", "61%",
                       "[Accept] [Modify] [Escalate]"):
            self.assertIn(needle, text)

    def test_queue_and_stats_render(self):
        self.store.enqueue([
            ReviewItem(id="p1:Q1", paper_id="p1", question_id="Q1",
                       proposed_marks=None, max_marks=5.0,
                       review_reasons=["identity_uncertain"]),
        ])
        from aos_v0.exam.review import render_queue
        table = render_queue(self.store.pending())
        self.assertIn("p1:Q1", table)
        self.assertIn("identity_uncertain", table)
        self.assertIn("pending", render_stats(self.store.stats()))

    def test_review_reasons_match_plan_enum(self):
        from aos_v0.exam.models import EvalReviewReason
        from aos_v0.exam.review import REVIEW_REASONS
        plan_reasons = (
            "LOW_OCR_CONFIDENCE",
            "AGENT_DISAGREEMENT",
            "AMBIGUOUS_ANSWER",
            "IDENTITY_UNCERTAIN",
            "DIAGRAM_UNCERTAIN",
            "MATHEMATICAL_UNCERTAINTY",
            "MISSING_PAGE",
            "MULTIPLE_ANSWERS",
            "LOW_EVALUATION_CONFIDENCE",
        )
        self.assertEqual(REVIEW_REASONS[:-1], plan_reasons)
        self.assertEqual(REVIEW_REASONS[-1], "RECOVERY_FAILED")
        self.assertTrue(all(hasattr(EvalReviewReason, r) for r in plan_reasons))


class BatchToReviewTests(unittest.TestCase):
    """Phase 11 consumes Phase 10 batch output without re-running anything."""

    def setUp(self):
        self.task = make_exam()
        self.exam = self.task.exam

    def test_batch_papers_enqueue_cards_without_reruns(self):
        import tempfile
        from pathlib import Path

        from aos_v0.exam.batch import BatchConfig, run_batch
        from aos_v0.exam.review import (
            ReviewStatus,
            enqueue_batch_results,
        )
        from tests.test_batch import make_png

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_png(root / "student001.png")  # gray -> review, no proposal

            report = run_batch(self.exam, root,
                               config=BatchConfig(max_workers=1, retries=0))
            self.assertTrue(all(
                item.result is not None
                for item in report.items if item.status.value == "review"
            ))
            store = ReviewStore(str(Path(tmp) / "store"))
            added = enqueue_batch_results(report, store, self.exam,
                                          flow_id="ph-11-test")
            self.assertEqual(added, ["student001:*"])
            card = store.pending()[0]
            self.assertTrue(card.paper_level)
            self.assertEqual(card.review_reasons, ["low_ocr_confidence"])
            self.assertIsNone(card.proposed_marks)
            store.resolve(card.id, ReviewDecision.ESCALATE, reviewer="dr.tandon",
                          note="rescan page")
            self.assertEqual(
                ReviewStore(str(Path(tmp) / "store")).get(card.id).status,
                ReviewStatus.REVIEWED)
            self.assertEqual(len(store.audit_events("student001")), 2)


if __name__ == "__main__":
    unittest.main()