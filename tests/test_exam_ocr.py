"""Phase 4 tests: OCR service, adapters, confidence, layout and fallback.

Covers the plan's Phase-4 deliverables without executing any ML model:

  * the runnable local structural engine produces real block geometry and
    never fabricates text (untranscribed blocks, confidence below the gate);
  * the declared ML adapters carry the correct Phase-1 model identities and
    raise the typed OcrUnavailableError (gap G3);
  * page/document confidence aggregation gates the fallback chain;
  * the rule-based layout analyzer labels header/question/answer/table/text
    regions deterministically;
  * the OCR service orchestrates adapter -> confidence -> fallback -> human
    review exactly as the plan's ladder describes;
  * intake pages flow through `run_document` unchanged.

Nothing here calls a model; the happy path uses scripted fake adapters.
"""

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from aos_v0.exam.intake import ingest
from aos_v0.exam.models import EvalReviewReason
from aos_v0.exam.ocr.adapters import (
    LocalStructuralAdapter,
    OcrAdapter,
    PaddleOcrAdapter,
    QwenVlOcrAdapter,
    TrocrHandwrittenAdapter,
    TrocrLargeHandwrittenAdapter,
    TrocrPrintedAdapter,
    default_adapters,
)
from aos_v0.exam.ocr.confidence import (
    document_confidence,
    page_confidence,
)
from aos_v0.exam.ocr.layout import (
    RuleLayoutAdapter,
)
from aos_v0.exam.ocr.models import (
    LayoutRegion,
    OcrBlock,
    OcrBlockType,
    OcrPage,
    OcrSettings,
    OcrStatus,
    OcrUnavailableError,
    RegionType,
)
from aos_v0.exam.ocr.pipeline import run_document, run_ocr


def make_sheet(seed: int = 1, *, blank: bool = False, dark: bool = False):
    """A synthetic answer-sheet-like page (header band + answer lines)."""
    base = 60 if dark else 255
    im = Image.new("RGB", (1200, 1600), (base, base, base))
    d = ImageDraw.Draw(im)
    d.rectangle([80, 80, 1120, 240 + seed * 40], fill=(40, 40, 40))
    for y in range(320, 1520, 70):
        d.line([80, y, 1120, y], fill=(5, 5, 5) if dark else (20, 20, 20), width=9)
    return im


class _FakePrintedAdapter(OcrAdapter):
    """Scripted high-confidence printed adapter for service happy paths."""

    adapter_id = "fake_printed"
    model_name = "Fake Printed OCR"
    model_repo = "test/fake-printed"

    def __init__(self, confidence: float = 0.95, answer_text: str = ""):
        self.confidence = confidence
        self.answer_text = answer_text

    def run(self, image, hints=None):
        return [
            OcrBlock(type=OcrBlockType.HEADER, text="Name: A", bbox=[40, 40, 1160, 120],
                     confidence=self.confidence, transcribed=True),
            OcrBlock(type=OcrBlockType.TEXT, text="Q1. What is TCP?",
                     bbox=[40, 220, 700, 250], confidence=self.confidence, transcribed=True),
            OcrBlock(type=OcrBlockType.TEXT, text=self.answer_text or "TCP is a transport protocol",
                     bbox=[60, 260, 1140, 300], confidence=self.confidence, transcribed=True),
        ]


class _LowConfidenceAdapter(OcrAdapter):
    adapter_id = "fake_low"
    model_name = "Fake Weak OCR"
    model_repo = "test/fake-weak"

    def run(self, image, hints=None):
        return [
            OcrBlock(type=OcrBlockType.TEXT, text="", bbox=[40, 40, 1160, 120],
                     confidence=0.3, transcribed=False),
        ]


class _FailingAdapter(OcrAdapter):
    adapter_id = "fake_error"
    model_name = "Fake Broken OCR"
    model_repo = "test/fake-broken"

    def run(self, image, hints=None):
        raise OcrUnavailableError("transport not installed (test)")


def fake_blocks(confidence: float = 0.95) -> list:
    return [
        OcrBlock(type=OcrBlockType.TEXT, text="Q1.", bbox=[40, 220, 120, 250],
                 confidence=confidence, transcribed=True),
        OcrBlock(type=OcrBlockType.TEXT, text="The answer is TCP.",
                 bbox=[40, 260, 900, 300], confidence=confidence, transcribed=True),
    ]


class LocalStructuralAdapterTests(unittest.TestCase):
    def test_detects_header_and_lines_untranscribed(self):
        adapter = LocalStructuralAdapter()
        blocks = adapter.run(make_sheet(seed=1))
        self.assertGreaterEqual(len(blocks), 15)
        first = blocks[0]
        self.assertEqual(first.type, OcrBlockType.HEADER)
        self.assertEqual(first.text, "")
        self.assertFalse(first.transcribed)
        for block in blocks:
            self.assertLessEqual(block.confidence, 0.55)
            self.assertLess(block.confidence, 0.6)  # under the review gate

    def test_bbox_in_bounds(self):
        blocks = LocalStructuralAdapter().run(make_sheet(seed=2))
        for block in blocks:
            x1, y1, x2, y2 = block.bbox
            self.assertGreaterEqual(x1, 0)
            self.assertGreaterEqual(y1, 0)
            self.assertGreater(x2, x1)
            self.assertGreater(y2, y1)

    def test_blank_page_no_blocks(self):
        blank = Image.new("RGB", (1200, 1600), "white")
        self.assertEqual(LocalStructuralAdapter().run(blank), [])

    def test_dark_page_no_blocks(self):
        self.assertEqual(LocalStructuralAdapter().run(make_sheet(dark=True)), [])

    def test_blank_hint_no_blocks(self):
        blocks = LocalStructuralAdapter().run(
            make_sheet(seed=1), hints={"blank": True}
        )
        self.assertEqual(blocks, [])


class DeclaredAdapterTests(unittest.TestCase):
    def test_all_declared_adapters_raise_unavailable(self):
        for adapter in [
            PaddleOcrAdapter(), TrocrPrintedAdapter(),
            TrocrHandwrittenAdapter(), TrocrLargeHandwrittenAdapter(),
            QwenVlOcrAdapter(),
        ]:
            with self.subTest(adapter=adapter.adapter_id):
                with self.assertRaises(OcrUnavailableError):
                    adapter.run(make_sheet(seed=1))
                with self.assertRaises(OcrUnavailableError):
                    adapter.run(make_sheet(seed=1), hints={})

    def test_model_identities_from_selection(self):
        self.assertEqual(PaddleOcrAdapter().model_repo, "PaddlePaddle/PaddleOCR")
        self.assertEqual(TrocrPrintedAdapter().model_repo, "microsoft/trocr-base-printed")
        self.assertEqual(TrocrHandwrittenAdapter().model_repo, "microsoft/trocr-base-handwritten")
        self.assertEqual(TrocrLargeHandwrittenAdapter().model_repo, "microsoft/trocr-large-handwritten")
        self.assertEqual(QwenVlOcrAdapter().model_repo, "Qwen/Qwen2.5-VL-7B-Instruct")

    def test_adapter_ids_unique(self):
        ids = [a.adapter_id for a in default_adapters("auto")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_default_chains_by_strategy(self):
        auto = [a.adapter_id for a in default_adapters("auto")]
        self.assertTrue(auto.index("local_structural") == len(auto) - 1)
        printed = [a.adapter_id for a in default_adapters("printed")]
        self.assertNotIn("trocr_base_handwritten", printed)
        hand = [a.adapter_id for a in default_adapters("handwritten")]
        self.assertNotIn("paddle_ocr_v6", hand)

    def test_local_can_be_omitted(self):
        chain = default_adapters("auto", local=False)
        self.assertNotIn("local_structural", [a.adapter_id for a in chain])


class ConfidenceTests(unittest.TestCase):
    def test_page_confidence_area_weighted(self):
        small = OcrBlock(type=OcrBlockType.TEXT, text="x", bbox=[0, 0, 10, 10],
                         confidence=1.0, transcribed=True)
        large = OcrBlock(type=OcrBlockType.TEXT, text="y", bbox=[0, 0, 100, 100],
                         confidence=0.5, transcribed=True)
        self.assertAlmostEqual(page_confidence([small, large]), 0.505, places=3)

    def test_untranscribed_blocks_penalised(self):
        good = OcrBlock(type=OcrBlockType.TEXT, text="ok", bbox=[0, 0, 100, 100],
                        confidence=1.0, transcribed=True)
        unread = OcrBlock(type=OcrBlockType.TEXT, text="", bbox=[0, 0, 100, 100],
                          confidence=0.4, transcribed=False)
        full = page_confidence([good])
        mixed = page_confidence([good, unread])
        self.assertGreater(full, mixed)
        self.assertEqual(mixed, 0.525)

    def test_empty_blocks_zero(self):
        self.assertEqual(page_confidence([]), 0.0)

    def test_document_confidence_is_min(self):
        pages = [
            OcrPage(page_no=1, blocks=fake_blocks(0.9), confidence=0.9),
            OcrPage(page_no=2, blocks=fake_blocks(0.4), confidence=0.4),
        ]
        self.assertEqual(document_confidence(pages), 0.4)

    def test_settings_verdict(self):
        settings = OcrSettings()
        self.assertEqual(settings.verdict(0.95), OcrStatus.OK)
        self.assertEqual(settings.verdict(0.7), OcrStatus.DEGRADED)
        self.assertEqual(settings.verdict(0.4), OcrStatus.REVIEW)


class RuleLayoutTests(unittest.TestCase):
    def test_question_answer_segmentation(self):
        im = Image.new("RGB", (1200, 1600), "white")
        blocks = [
            OcrBlock(type=OcrBlockType.HEADER, text="Name: X", bbox=[40, 40, 1160, 120],
                     confidence=0.9, transcribed=True),
            OcrBlock(type=OcrBlockType.TEXT, text="Read carefully", bbox=[40, 160, 900, 190],
                     confidence=0.95, transcribed=True),
            OcrBlock(type=OcrBlockType.TEXT, text="Q1. What is TCP?", bbox=[40, 220, 700, 250],
                     confidence=0.95, transcribed=True),
            OcrBlock(type=OcrBlockType.TEXT, text="TCP is a transport protocol",
                     bbox=[60, 260, 1140, 300], confidence=0.93, transcribed=True),
            OcrBlock(type=OcrBlockType.TEXT, text="2. What is UDP?", bbox=[40, 340, 500, 370],
                     confidence=0.9, transcribed=True),
            OcrBlock(type=OcrBlockType.TEXT, text="UDP is connectionless",
                     bbox=[60, 380, 900, 410], confidence=0.9, transcribed=True),
        ]
        regions = RuleLayoutAdapter().analyze(im, blocks)
        types = [r.region_type for r in regions]
        self.assertEqual(types.count(RegionType.QUESTION), 2)
        self.assertEqual(types.count(RegionType.ANSWER), 2)
        self.assertIn(RegionType.HEADER, types)
        self.assertIn(RegionType.TEXT, types)
        self.assertEqual(
            [r.region_type for r in regions],
            types,  # top-down ordering preserved
        )

    def test_table_detection(self):
        im = Image.new("RGB", (1200, 1600), "white")
        cells = []
        for row, y in enumerate((300, 330)):
            cells.append(OcrBlock(type=OcrBlockType.TEXT, text=f"a{row}",
                                  bbox=[80, y, 300, y + 20], confidence=0.9, transcribed=True))
            cells.append(OcrBlock(type=OcrBlockType.TEXT, text=f"b{row}",
                                  bbox=[320, y, 700, y + 20], confidence=0.9, transcribed=True))
        regions = RuleLayoutAdapter().analyze(im, cells)
        self.assertIn(RegionType.TABLE, [r.region_type for r in regions])

    def test_question_anchor_patterns(self):
        from aos_v0.exam.ocr.layout import _QUESTION_ANCHOR

        anchors = ["Q1.", "Q1: Define", "1) Explain", "2. What", "Question 3 - Name", "q4- State"]
        for text in anchors:
            self.assertTrue(_QUESTION_ANCHOR.match(text), text)
        for text in ["12", "A1", "abc"]:
            self.assertFalse(_QUESTION_ANCHOR.match(text), text)

    def test_empty_input_no_regions(self):
        im = Image.new("RGB", (1200, 1600), "white")
        self.assertEqual(RuleLayoutAdapter().analyze(im, []), [])


class ServiceTests(unittest.TestCase):
    def test_happy_path_accepted(self):
        result = run_ocr(make_sheet(seed=1), adapters=[_FakePrintedAdapter()])
        self.assertEqual(result.status, OcrStatus.OK)
        self.assertEqual(result.review_reasons, [])
        page = result.document.pages[0]
        self.assertEqual(page.status, OcrStatus.OK)
        self.assertGreater(page.confidence, 0.9)
        self.assertTrue(all(b.transcribed for b in page.blocks))
        self.assertTrue(page.regions)  # layout ran over accepted blocks

    def test_plan_json_shape(self):
        result = run_ocr(make_sheet(seed=1), adapters=[_FakePrintedAdapter()])
        payload = result.to_plan_json()
        self.assertEqual(payload[0]["page"], 1)
        block = payload[0]["blocks"][0]
        self.assertEqual(
            set(block), {"type", "text", "bbox", "confidence"}
        )
        self.assertIsInstance(block["bbox"], list)
        self.assertEqual(len(block["bbox"]), 4)

    def test_fallback_to_higher_confidence_adapter(self):
        result = run_ocr(
            make_sheet(seed=1),
            adapters=[_LowConfidenceAdapter(), _FakePrintedAdapter(confidence=0.95)],
        )
        self.assertEqual(result.status, OcrStatus.OK)
        self.assertEqual(
            [a.adapter_id for a in result.fallback_trace],
            ["fake_low", "fake_printed"],
        )
        low, high = result.fallback_trace
        self.assertEqual(low.status.value, "low_confidence")
        self.assertEqual(high.status.value, "ok")
        self.assertEqual(result.document.pages[0].blocks[0].text_clean, "Name: A")

    def test_all_low_routes_to_human_review(self):
        result = run_ocr(make_sheet(seed=1), adapters=[_LowConfidenceAdapter()])
        page = result.document.pages[0]
        self.assertEqual(result.status, OcrStatus.REVIEW)
        self.assertIn(EvalReviewReason.LOW_OCR_CONFIDENCE, result.review_reasons)
        self.assertEqual(page.review_reasons, result.review_reasons)
        # Best (only) evidence is preserved, never fabricated.
        self.assertTrue(page.blocks)
        self.assertFalse(page.blocks[0].transcribed)

    def test_unavailable_adapters_continue_down_chain(self):
        result = run_ocr(
            make_sheet(seed=1),
            adapters=[_FailingAdapter(), _LowConfidenceAdapter()],
        )
        trace = result.fallback_trace
        self.assertEqual(trace[0].status.value, "unavailable")
        self.assertEqual(trace[1].status.value, "low_confidence")
        self.assertEqual(result.status, OcrStatus.REVIEW)

    def test_empty_adapter_list_unavailable(self):
        result = run_ocr(make_sheet(seed=1), adapters=[])
        self.assertEqual(result.status, OcrStatus.UNAVAILABLE)
        self.assertEqual(result.review_reasons, [])

    def test_transcript_block_carries_confidence_marker(self):
        result = run_ocr(make_sheet(seed=1), adapters=[_FakePrintedAdapter()])
        block = result.document.pages[0].transcript_block
        self.assertTrue(block.text.startswith("confidence:"))
        self.assertIn("TCP", block.text)

    def test_summary_surface(self):
        result = run_ocr(make_sheet(seed=1), adapters=[_FakePrintedAdapter()])
        summary = result.summary
        self.assertEqual(summary["pages"], 1)
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["blocks"], 3)


class DocumentPipelineTests(unittest.TestCase):
    def _folder(self):
        td = tempfile.TemporaryDirectory()
        p = Path(td.name)
        make_sheet(seed=1).save(p / "sheet1.png")
        make_sheet(seed=2).save(p / "sheet2.png")
        return td

    def test_run_document_over_intake_pages(self):
        td = self._folder()
        try:
            intake_result = ingest(td.name, dpi=300)
            result = run_document(intake_result.pages)
            self.assertEqual(len(result.document.pages), 2)
            self.assertEqual(result.document.pages[0].page_no, 1)
            # Local structural OCR alone cannot read -> human review.
            self.assertIn(EvalReviewReason.LOW_OCR_CONFIDENCE, result.review_reasons)
        finally:
            td.cleanup()

    def test_run_document_happy_path_with_adapters(self):
        td = self._folder()
        try:
            intake_result = ingest(td.name, dpi=300)
            result = run_document(
                intake_result.pages, adapters=[_FakePrintedAdapter()]
            )
            self.assertEqual(result.status, OcrStatus.OK)
            self.assertEqual(result.document.pages[0].source, "sheet1.png")
            self.assertEqual(result.summary["needs_review_pages"], [])
        finally:
            td.cleanup()

    def test_hints_flow_from_intake_issues(self):
        td = self._folder()
        try:
            blank = Image.new("RGB", (300, 400), "white")
            blank.save(Path(td.name) / "blank.png")
            intake_result = ingest(td.name, dpi=300)
            pages = {p.meta.member: p for p in intake_result.pages}
            page = pages["blank.png"]
            blank_result = run_ocr(
                page.image, hints={"blank": True}, adapters=[LocalStructuralAdapter()]
            )
            self.assertEqual(blank_result.document.pages[0].blocks, [])
            self.assertEqual(blank_result.status, OcrStatus.REVIEW)
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main()