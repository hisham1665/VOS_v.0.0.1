"""Phase 9 tests -- the confidence engine (plan Phase 9 "Confidence Categories").

Covers: threshold-gated HIGH / MEDIUM / LOW categorization from
`EvaluationSettings.confidence_high` / `confidence_low`; weighted aggregation
of the plan's confidence components (OCR, answer extraction, semantic, rubric,
agent agreement) with unmeasured components excluded rather than zeroed; and
the load-bearing rule that **confidence is not correctness** -- confidence only
decides how much verification / review a mark needs, never a mark, and a
genuinely LOW component is never masked out of the category.
"""

import unittest

from aos_v0.exam.confidence import (
    DEFAULT_COMPONENT_WEIGHTS,
    ConfidenceCategory,
    ConfidenceEngine,
    ConfidenceComponent,
    PaperConfidence,
    QuestionConfidence,
)
from aos_v0.exam.models import EvaluationSettings


def settings(
    *,
    high: float = 0.9,
    low: float = 0.6,
    **kwargs,
) -> EvaluationSettings:
    return EvaluationSettings(confidence_high=high, confidence_low=low, **kwargs)


class CategorizeTests(unittest.TestCase):
    def setUp(self):
        self.engine = ConfidenceEngine(settings())

    def test_threshold_boundaries(self):
        self.assertEqual(self.engine.categorize(0.9), ConfidenceCategory.HIGH)
        self.assertEqual(self.engine.categorize(0.899), ConfidenceCategory.MEDIUM)
        self.assertEqual(self.engine.categorize(0.6), ConfidenceCategory.MEDIUM)
        self.assertEqual(self.engine.categorize(0.599), ConfidenceCategory.LOW)
        self.assertEqual(self.engine.categorize(0.0), ConfidenceCategory.LOW)
        self.assertEqual(self.engine.categorize(1.0), ConfidenceCategory.HIGH)

    def test_thresholds_are_configurable(self):
        engine = ConfidenceEngine(settings(high=0.8, low=0.4))
        self.assertEqual(engine.categorize(0.85), ConfidenceCategory.HIGH)
        self.assertEqual(engine.categorize(0.45), ConfidenceCategory.MEDIUM)
        self.assertEqual(engine.categorize(0.39), ConfidenceCategory.LOW)


class EstimatePaperTests(unittest.TestCase):
    def setUp(self):
        self.engine = ConfidenceEngine(settings())

    def test_full_component_set_weighted_mean(self):
        estimate = self.engine.estimate_paper(
            ocr=0.95,
            answer_extraction=0.95,
            semantic=0.95,
            rubric=0.95,
            agent_agreement=0.95,
        )
        self.assertAlmostEqual(estimate.overall, 0.95, places=4)
        self.assertEqual(estimate.category, ConfidenceCategory.HIGH.value)
        self.assertEqual(len(estimate.components), 5)

    def test_weights_declared_sum_to_one(self):
        self.assertAlmostEqual(sum(DEFAULT_COMPONENT_WEIGHTS.values()), 1.0)
        self.assertEqual(
            list(DEFAULT_COMPONENT_WEIGHTS),
            ["ocr", "answer_extraction", "semantic", "rubric", "agent_agreement"],
        )

    def test_unmeasured_components_excluded_not_zeroed(self):
        estimate = self.engine.estimate_paper(ocr=0.95, semantic=0.95)
        self.assertEqual(
            {c.name for c in estimate.components}, {"ocr", "semantic"}
        )
        self.assertAlmostEqual(estimate.overall, 0.95, places=4)

    def test_low_component_never_masked(self):
        estimate = self.engine.estimate_paper(
            ocr=0.3,
            semantic=0.9,
            agent_agreement=1.0,
        )
        self.assertAlmostEqual(estimate.overall, 0.7385, places=4)
        self.assertEqual(estimate.category, ConfidenceCategory.LOW.value)

    def test_medium_component_pulls_category_to_medium(self):
        estimate = self.engine.estimate_paper(
            ocr=0.95,
            semantic=0.7,
            rubric=1.0,
            agent_agreement=1.0,
            answer_extraction=0.95,
        )
        self.assertEqual(estimate.category, ConfidenceCategory.MEDIUM.value)

    def test_no_evidence_is_honest_low(self):
        estimate = self.engine.estimate_paper()
        self.assertEqual(estimate.overall, 0.0)
        self.assertEqual(estimate.category, ConfidenceCategory.LOW.value)
        self.assertEqual(estimate.components, [])

    def test_component_records_carry_value_weight_category(self):
        estimate = self.engine.estimate_paper(ocr=0.95)
        component = estimate.components[0]
        self.assertIsInstance(component, ConfidenceComponent)
        self.assertEqual(component.name, "ocr")
        self.assertAlmostEqual(component.value, 0.95)
        self.assertAlmostEqual(component.weight, 0.2)
        self.assertEqual(component.category, ConfidenceCategory.HIGH.value)

    def test_returns_paper_confidence_model(self):
        self.assertIsInstance(self.engine.estimate_paper(ocr=0.9), PaperConfidence)


class EstimateQuestionTests(unittest.TestCase):
    def setUp(self):
        self.engine = ConfidenceEngine(settings())

    def test_question_confidence_categorizes_the_score(self):
        estimate = self.engine.estimate_question(
            question_id="Q1", score=0.95,
            extraction_confidence=0.9, agreed=True,
        )
        self.assertIsInstance(estimate, QuestionConfidence)
        self.assertEqual(estimate.category, ConfidenceCategory.HIGH.value)
        self.assertEqual(estimate.extraction_confidence, 0.9)
        self.assertTrue(estimate.agreed)

    def test_question_agreement_does_not_change_the_category(self):
        build = {"question_id": "Q1", "score": 0.62, "extraction_confidence": 0.8}
        agreed = self.engine.estimate_question(agreed=True, **build)
        disputed = self.engine.estimate_question(agreed=False, **build)
        self.assertEqual(agreed.category, disputed.category)
        self.assertFalse(disputed.agreed)


if __name__ == "__main__":
    unittest.main()