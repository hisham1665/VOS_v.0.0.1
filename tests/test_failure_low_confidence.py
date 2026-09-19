"""Kernel tests for the Phase-4 low-confidence failure seam (spec §6 Ph4, S5).

Adds no behavior to the healthy path: only structured output that explicitly
declares a sub-0.6 confidence trips `tool.low_confidence`, whose recovery
ladder asks the resource to retry with feedback and then substitutes.
"""

import unittest

from aos_v0.core.failure_manager import (
    CLASS_RESOURCE_DEGRADED,
    CLASS_TOOL_LOW_CONFIDENCE,
    CLASS_TOOL_OUTPUT_CORRUPT,
    FailureManager,
    RECOVERY_TABLE,
    STRATEGY_RESOURCE_SUBSTITUTION,
    STRATEGY_RETRY_WITH_FEEDBACK,
    Detection,
)
from aos_v0.core.models import Node

NODE = Node(id="ocr1", description="OCR the page", capability="document_ocr")


class LowConfidenceDetectorTests(unittest.TestCase):
    def setUp(self):
        self.fm = FailureManager.__new__(FailureManager)
        self.node = NODE

    def test_low_confidence_marker_detected(self):
        output = '{"page": 1, "confidence": 0.3, "blocks": []}'
        fired = {d.failure_class for d in self.fm.detect(self.node, output, None)}
        self.assertIn(CLASS_TOOL_LOW_CONFIDENCE, fired)

    def test_colon_space_form_detected(self):
        output = "confidence: 0.42\npartial transcript text"
        fired = {d.failure_class for d in self.fm.detect(self.node, output, None)}
        self.assertIn(CLASS_TOOL_LOW_CONFIDENCE, fired)

    def test_avg_conf_form_detected(self):
        output = "avg_conf: 0.5\nnoise"
        fired = {d.failure_class for d in self.fm.detect(self.node, output, None)}
        self.assertIn(CLASS_TOOL_LOW_CONFIDENCE, fired)

    def test_healthy_confidence_is_not_faulted(self):
        output = "confidence: 0.87\n" + "The page reads cleanly. " * 5
        self.assertEqual(self.fm.detect(self.node, output, None), [])

    def test_boundary_zero_six_is_healthy(self):
        output = "confidence: 0.60\n" + "usable transcript " * 5
        self.assertEqual(self.fm.detect(self.node, output, None), [])

    def test_plain_prose_is_not_faulted(self):
        output = "ordinary prose without any marker " * 4
        self.assertEqual(self.fm.detect(self.node, output, None), [])

    def test_classify_prefers_low_confidence_over_thinness(self):
        detections = [
            Detection(CLASS_TOOL_LOW_CONFIDENCE, "matched low confidence"),
            Detection(CLASS_RESOURCE_DEGRADED, "thin output"),
        ]
        self.assertEqual(self.fm.classify(detections), CLASS_TOOL_LOW_CONFIDENCE)

    def test_corrupt_output_outranks_low_confidence(self):
        detections = [
            Detection(CLASS_TOOL_LOW_CONFIDENCE, "matched low confidence"),
            Detection(CLASS_TOOL_OUTPUT_CORRUPT, "media denial"),
        ]
        self.assertEqual(self.fm.classify(detections), CLASS_TOOL_OUTPUT_CORRUPT)

    def test_recovery_ladder(self):
        self.assertEqual(
            RECOVERY_TABLE[CLASS_TOOL_LOW_CONFIDENCE],
            [STRATEGY_RETRY_WITH_FEEDBACK, STRATEGY_RESOURCE_SUBSTITUTION],
        )


if __name__ == "__main__":
    unittest.main()