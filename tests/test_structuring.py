"""Phase 5 tests -- answer-sheet structuring over structured OCR output.

Covers the parser deliverable: student identity + roster validation, question
detection/parsing, out-of-order and continuation mapping, page grouping, and
the plan's edge cases (missing/OCR'd question numbers, subquestions, multiple
attempts, crossed-out answers, blank answers, ambiguous labels). The parser
never rewrites evidence: every ambiguity must surface as a MappingIssue and
route the sheet to review.
"""

import unittest

from aos_v0.exam.models import (
    AnswerKey,
    ExamConfiguration,
    EvalReviewReason,
    Question,
    Roster,
    RosterEntry,
)
from aos_v0.exam.ocr.models import (
    LayoutRegion,
    OcrBlock,
    OcrDocument,
    OcrPage,
    OcrStatus,
    RegionType,
)
from aos_v0.exam.structure.identity import extract_student_identity
from aos_v0.exam.structure.models import MappingIssue, SheetStatus
from aos_v0.exam.structure.questions import (
    canonical_question_id,
    looks_crossed_out,
    map_answers,
    parse_question_label,
)
from aos_v0.exam.structure.sheet import (
    ocr_document_from_json,
    structure,
)


def block(text, y, x=0, conf=0.9, width=180, height=60):
    return OcrBlock(text=text, bbox=[x, y, x + width, y + height], confidence=conf)


def page(number, blocks, regions=None, status=OcrStatus.OK, reasons=None):
    return OcrPage(
        page_no=number,
        source=f"p{number}.png",
        blocks=blocks,
        regions=regions or [],
        confidence=0.9,
        status=status,
        review_reasons=reasons or [],
    )


def config(*questions):
    return ExamConfiguration(
        exam_id="midterm",
        title="Midterm",
        questions=[
            Question(question_id=qid, text=qid, max_marks=5)
            for qid in questions
        ],
    )


def header_page():
    return page(1, [
        block("Name: Anjali", 40),
        block("Roll No: 23CS042", 100),
        block("Register No: 23CS042R", 160),
        block("Class: III CS", 220),
        block("Department: CSE", 280),
        block("Exam: Midterm", 340),
        block("Subject: Networks", 400),
        block("Q1. Define OSI.", 500),
    ], regions=[
        LayoutRegion(region_type=RegionType.HEADER, bbox=[0, 20, 1000, 460],
                     confidence=0.9, label="header"),
    ])


class LabelParserTests(unittest.TestCase):
    def test_forms_parse_to_canonical(self):
        expected = {
            "Q3(a)": "Q3(a)",
            "Q3a": "Q3(a)",
            "3 a)": "Q3(a)",
            "3a": "Q3(a)",
            "3(a)": "Q3(a)",
            "3.": "Q3",
            "3)": "Q3",
            "Question 2 - Explain": "Q2",
            "q4-": "Q4",
            "Q 4.": "Q4",
            "Q3 Explain": "Q3",
            "Q3b Explain": "Q3(b)",
            "Q10": "Q10",
        }
        for raw, want in expected.items():
            self.assertEqual(parse_question_label(raw), want, raw)

    def test_non_labels_rejected(self):
        for raw in ("12", "A1", "TCP is the protocol", "3(a) State",
                    "Q", "Question"):
            self.assertIsNone(parse_question_label(raw), raw)

    def test_canonical_forms(self):
        self.assertEqual(canonical_question_id(3), "Q3")
        self.assertEqual(canonical_question_id(12, "b"), "Q12(b)")

    def test_crossed_out_detection(self):
        self.assertTrue(looks_crossed_out("xxxx the answer is xxxx"))
        self.assertTrue(looks_crossed_out("XXX not this one XXX"))
        self.assertTrue(looks_crossed_out("✗✗✗ wrong answer ✗✗✗"))
        self.assertFalse(looks_crossed_out("The answer is TCP/IP model"))
        self.assertFalse(looks_crossed_out("x"))


class IdentityTests(unittest.TestCase):
    def test_extracts_fields_from_header(self):
        doc = OcrDocument(pages=[header_page()])
        identity, issues = extract_student_identity(doc)
        self.assertEqual(identity.name, "Anjali")
        self.assertEqual(identity.roll_no, "23CS042")
        self.assertEqual(identity.class_, "III CS")
        self.assertEqual(identity.department, "CSE")
        self.assertEqual(identity.subject, "Networks")
        self.assertTrue(identity.identity_ok)
        self.assertNotIn(MappingIssue.IDENTITY_UNVERIFIED, issues)

    def test_roster_validation_matches(self):
        doc = OcrDocument(pages=[header_page()])
        roster = Roster(entries=[RosterEntry(roll_no="23CS042", name="Anjali")])
        identity, issues = extract_student_identity(doc, roster)
        self.assertTrue(identity.matched)
        self.assertEqual(identity.roster_name, "Anjali")
        self.assertEqual(issues, [])

    def test_roster_roll_mismatch_flags_unverified(self):
        doc = OcrDocument(pages=[header_page()])
        roster = Roster(entries=[RosterEntry(roll_no="23CS001", name="Rahul")])
        identity, issues = extract_student_identity(doc, roster)
        self.assertFalse(identity.matched)
        self.assertIn(MappingIssue.IDENTITY_UNVERIFIED, issues)

    def test_roster_name_mismatch_flags_mismatch(self):
        doc = OcrDocument(pages=[header_page()])
        roster = Roster(entries=[RosterEntry(roll_no="23CS042", name="Other")])
        _, issues = extract_student_identity(doc, roster)
        self.assertIn(MappingIssue.IDENTITY_MISMATCH, issues)

    def test_missing_identity_routes_to_review(self):
        doc = OcrDocument(pages=[page(1, [block("Q1. Answer", 60)])])
        identity, issues = extract_student_identity(doc)
        self.assertFalse(identity.identity_ok)
        self.assertIn(MappingIssue.IDENTITY_UNVERIFIED, issues)
        sheet = structure(doc, config=config("Q1"))
        self.assertEqual(sheet.status, SheetStatus.REVIEW)
        self.assertIn(EvalReviewReason.IDENTITY_UNCERTAIN,
                      sheet.review_reasons)


class MappingTests(unittest.TestCase):
    def test_out_of_order_answers_still_map(self):
        doc = OcrDocument(pages=[page(1, [
            block("Q1. first", 300), block("first answer", 360),
            block("Q2. second", 500), block("second answer", 560),
            block("Q5. fifth", 700), block("fifth answer", 760),
            block("Q3. third", 900), block("third answer", 960),
            block("Q4. fourth", 1100), block("fourth answer", 1160),
        ])])
        sheet = structure(doc, config=config("Q1", "Q2", "Q3", "Q4", "Q5"))
        ids = [entry.question_id for entry in sheet.answers]
        self.assertEqual(ids, ["Q1", "Q2", "Q3", "Q4", "Q5"])
        texts = {e.question_id: e.text for e in sheet.answers}
        self.assertIn("fifth answer", texts["Q5"])
        self.assertIn(MappingIssue.OUT_OF_ORDER, sheet.issues)

    def test_continuation_merges_pages(self):
        doc = OcrDocument(pages=[
            page(1, [block("Q3. middle", 300), block("part one", 360)]),
            page(2, [block("part two", 60), block("part three", 130)]),
            page(3, [block("Q5. last", 60), block("final", 130)]),
        ])
        sheet = structure(doc, config=config("Q3", "Q5"))
        by_id = {e.question_id: e for e in sheet.answers}
        q3 = by_id["Q3"]
        self.assertEqual(q3.pages, [1, 2])
        self.assertEqual(q3.text, "middle\npart one\npart two\npart three")
        self.assertIn(MappingIssue.CONTINUATION, q3.issues)
        self.assertEqual(by_id["Q5"].pages, [3])

    def test_subquestions_are_separate_entries(self):
        doc = OcrDocument(pages=[page(1, [
            block("Q3(a) define", 300), block("aa text", 360),
            block("Q3(b) draw", 500), block("bb text", 560),
        ])])
        sheet = structure(doc, config=config("Q3(a)", "Q3(b)"))
        ids = [e.question_id for e in sheet.answers]
        self.assertEqual(ids, ["Q3(a)", "Q3(b)"])
        self.assertIn("aa text", sheet.answers[0].text)
        self.assertIn("bb text", sheet.answers[1].text)

    def test_missing_question_number_is_reviewed(self):
        doc = OcrDocument(pages=[page(1, [
            block("this answer has no label", 60),
            block("Q1. labelled", 300), block("q1 text", 360),
        ])])
        sheet = structure(doc, config=config("Q1"))
        self.assertIn(MappingIssue.MISSING_QUESTION_NUMBER, sheet.issues)
        entry = sheet.answers[0]
        self.assertEqual(entry.question_id, "Q1")
        unlabelled = [e for e in sheet.answers if e.question_id == ""]
        self.assertEqual(len(unlabelled), 1)
        self.assertIn(MappingIssue.MISSING_QUESTION_NUMBER,
                      unlabelled[0].issues)
        self.assertIn(EvalReviewReason.AMBIGUOUS_ANSWER,
                      sheet.review_reasons)

    def test_question_number_ocr_error_surfaced(self):
        doc = OcrDocument(pages=[page(1, [
            block("Q1. one", 300), block("one text", 360),
            block("Q31. three?", 500), block("mystery text", 560),
        ])])
        sheet = structure(doc, config=config("Q1", "Q2", "Q3"))
        ids = [e.question_id for e in sheet.answers]
        self.assertEqual(ids, ["Q1", "Q2", "Q3", "Q31"])
        q31 = sheet.answers[-1]
        self.assertIn(MappingIssue.QUESTION_NUMBER_OCR_ERROR, q31.issues)
        self.assertIn(MappingIssue.QUESTION_NUMBER_OCR_ERROR, sheet.issues)
        self.assertIn(EvalReviewReason.AMBIGUOUS_ANSWER, sheet.review_reasons)

    def test_ambiguous_letter_label(self):
        doc = OcrDocument(pages=[page(1, [
            block("Q3. only part", 300), block("text", 360),
        ])])
        sheet = structure(doc, config=config("Q3(a)", "Q3(b)"))
        q3 = [e for e in sheet.answers if e.question_id == "Q3"][0]
        self.assertIn(MappingIssue.AMBIGUOUS_MAPPING, q3.issues)

    def test_multiple_attempts_flagged(self):
        doc = OcrDocument(pages=[page(1, [
            block("Q2. attempt one", 300),
            block("draft try one", 360),
            block("Q3. brief", 500),
            block("q3 text", 560),
            block("Q2. final answer", 700),
            block("second try two", 760),
        ])])
        sheet = structure(doc, config=config("Q2", "Q3"))
        q2 = [e for e in sheet.answers if e.question_id == "Q2"][0]
        self.assertIn("draft try one", q2.text)
        self.assertIn("second try two", q2.text)
        self.assertIn(MappingIssue.MULTIPLE_ATTEMPTS, q2.issues)
        self.assertIn(EvalReviewReason.MULTIPLE_ANSWERS,
                      sheet.review_reasons)

    def test_separated_boxes_on_one_page_flagged(self):
        doc = OcrDocument(pages=[page(1, [
            block("Q3. two boxes", 300), block("box one text", 360),
            block("box two separated", 1400),
        ])])
        sheet = structure(doc, config=config("Q3"))
        q3 = [e for e in sheet.answers if e.question_id == "Q3"][0]
        self.assertIn(MappingIssue.MULTIPLE_ATTEMPTS, q3.issues)

    def test_crossed_out_answer_is_stripped_and_flagged(self):
        doc = OcrDocument(pages=[page(1, [
            block("Q1. x", 300),
            block("xxxx NOT the answer xxxx", 360),
            block("the real answer", 440),
        ])])
        sheet = structure(doc, config=config("Q1"))
        entry = sheet.answers[0]
        self.assertIn(MappingIssue.CROSSED_OUT, entry.issues)
        self.assertTrue(entry.crossed_out)
        self.assertNotIn("NOT the answer", entry.text)
        self.assertIn("the real answer", entry.text)

    def test_blank_answer_entry(self):
        doc = OcrDocument(pages=[page(1, [block("Q1. answered", 300)])])
        sheet = structure(doc, config=config("Q1", "Q2"))
        q2 = [e for e in sheet.answers if e.question_id == "Q2"][0]
        self.assertTrue(q2.blank)
        self.assertEqual(q2.pages, [])
        self.assertIn(MappingIssue.BLANK_ANSWER, q2.issues)

    def test_plan_json_shape(self):
        doc = OcrDocument(pages=[header_page()])
        sheet = structure(doc, config=config("Q1"))
        plan = sheet.to_plan_json()
        self.assertEqual(plan["student"]["name"], "Anjali")
        self.assertEqual(plan["student"]["roll_no"], "23CS042")
        answer = plan["answers"][0]
        self.assertEqual(set(answer), {"question_id", "pages", "text", "regions"})

    def test_header_blocks_are_not_answers(self):
        doc = OcrDocument(pages=[header_page()])
        mapping = map_answers(doc)
        self.assertEqual(mapping.unassigned, [])
        sheet = structure(doc, config=config("Q1"))
        ids = [e.question_id for e in sheet.answers]
        self.assertEqual(ids, ["Q1"])
        self.assertEqual(sheet.answers[0].text, "Define OSI.")
        self.assertNotIn(MappingIssue.MISSING_QUESTION_NUMBER, sheet.issues)

    def test_summary_and_answers(self):
        doc = OcrDocument(pages=[page(1, [block("Q1. a", 50)])])
        sheet = structure(doc)
        self.assertEqual(sheet.status, SheetStatus.REVIEW)
        self.assertEqual([e.question_id for e in sheet.answers], ["Q1"])

    def test_upstream_low_confidence_propagates(self):
        doc = OcrDocument(pages=[page(
            1, [block("Q1. a", 50)],
            status=OcrStatus.REVIEW,
            reasons=[EvalReviewReason.LOW_OCR_CONFIDENCE],
        )])
        sheet = structure(doc, config=config("Q1"))
        self.assertIn(EvalReviewReason.LOW_OCR_CONFIDENCE,
                      sheet.review_reasons)
        self.assertEqual(sheet.status, SheetStatus.REVIEW)


class PlainJsonRoundTripTests(unittest.TestCase):
    def test_ocr_json_round_trip(self):
        payload = [{
            "page": 1,
            "blocks": [
                {"type": "text", "text": "Q1. hello", "bbox": [0, 50, 200, 110],
                 "confidence": 0.9},
                {"type": "text", "text": "world", "bbox": [0, 120, 150, 180],
                 "confidence": 0.95},
            ],
        }]
        doc = ocr_document_from_json(payload)
        self.assertEqual(doc.pages[0].page_no, 1)
        sheet = structure(doc, config=config("Q1"))
        self.assertEqual(sheet.answers[0].text, "hello\nworld")

    def test_pipeline_json_envelope(self):
        doc = OcrDocument(pages=[page(1, [block("Q1. x", 50)])])
        doc_ocr_json = doc.to_plan_json()
        rebuilt = ocr_document_from_json({"pages_ocr": doc_ocr_json})
        sheet = structure(rebuilt, config=config("Q1"))
        self.assertEqual([e.question_id for e in sheet.answers], ["Q1"])


if __name__ == "__main__":
    unittest.main()