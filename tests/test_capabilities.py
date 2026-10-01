"""Unit tests for capability internals that do not require a model."""

from __future__ import annotations

import pytest

from capabilities.registry import CapabilityRequest, CapabilityResult
from models import Modality, PatientContext, Severity, Study, StudyImage


def _study(**patient_kwargs) -> Study:
    return Study(
        study_id="s1",
        source_dir="/tmp/s1",
        patient=PatientContext(**patient_kwargs),
        images=[
            StudyImage(
                image_id="img1.png",
                source_path="/tmp/img1.png",
                content_hash="h1",
                modality=Modality.XRAY,
            ),
            StudyImage(
                image_id="img2.png",
                source_path="/tmp/img2.png",
                content_hash="h2",
                modality=Modality.CT,
            ),
        ],
    )


class TestRegistry:
    def test_every_capability_is_registered(self):
        from capabilities import registry

        assert set(registry.names()) == {
            "cross_image_synthesis",
            "document_analysis",
            "image_classification",
            "modality_triage",
            "report_synthesis",
            "specialist_confirmation",
            "summarization",
            "vision",
            "web_search",
        }

    def test_unknown_capability_error_lists_valid_names(self):
        from capabilities import registry
        from core.errors import UnknownCapabilityError

        with pytest.raises(UnknownCapabilityError, match="vision"):
            registry.get("telepathy")

    def test_request_selects_only_its_assigned_images(self):
        request = CapabilityRequest(
            instruction="read", study=_study(), image_ids=["img2.png"]
        )
        assert [i.image_id for i in request.images()] == ["img2.png"]

    def test_request_without_assignment_sees_every_image(self):
        request = CapabilityRequest(instruction="read", study=_study())
        assert len(request.images()) == 2

    def test_result_strips_whitespace(self):
        assert CapabilityResult(text="  hello  ").text == "hello"


class TestClassificationMapping:
    @pytest.mark.parametrize(
        "label,expected",
        [
            ("PNEUMOTHORAX", Severity.CRITICAL),
            ("Pneumonia", Severity.HIGH),
            ("pleural effusion", Severity.HIGH),
            ("lung nodule", Severity.MODERATE),
            ("cardiomegaly", Severity.LOW),
            ("NORMAL", Severity.NORMAL),
            ("no finding", Severity.NORMAL),
            ("something unmapped", Severity.INDETERMINATE),
        ],
    )
    def test_labels_map_to_severity(self, label, expected):
        from capabilities.image_classification import _severity_for

        assert _severity_for(label) is expected

    def test_images_without_a_matching_screener_are_not_screened(self):
        """A CT with no known region must not fall through to a chest model."""
        from capabilities.image_classification import run

        study = _study()
        request = CapabilityRequest(
            instruction="screen", study=study, image_ids=["img2.png"]  # a CT
        )
        result = run(request)

        entry = result.artifacts["screening"]["img2.png"]
        assert entry["status"] == "skipped"
        assert "no supervised screener" in entry["reason"]
        assert result.artifacts["classification"] == {}
        assert "NOT SCREENED" in result.text

    def test_scores_normalise_and_sort(self):
        from capabilities.image_classification import _normalise

        out = _normalise(
            [{"label": "b", "score": 0.1}, {"label": "a", "score": 0.9}]
        )
        assert [e["label"] for e in out] == ["a", "b"]
        assert out[0]["score"] == 0.9


class TestJsonExtraction:
    @pytest.mark.parametrize(
        "raw",
        [
            '{"findings": []}',
            '```json\n{"findings": []}\n```',
            'Here you go:\n{"findings": []}\nHope that helps.',
            '```\n{"findings": []}\n```',
        ],
    )
    def test_json_survives_model_formatting_habits(self, raw):
        from capabilities.cross_image_synthesis import _parse_json

        assert _parse_json(raw) == {"findings": []}

    def test_missing_json_raises(self):
        from capabilities.cross_image_synthesis import _parse_json

        with pytest.raises(ValueError, match="no JSON object"):
            _parse_json("I could not complete that request.")

    def test_malformed_findings_are_dropped_not_crashed(self):
        from capabilities.cross_image_synthesis import _normalise_findings

        out = _normalise_findings(
            [
                {"label": "Valid", "severity": "high", "confidence": 0.8},
                {"label": "", "severity": "high"},  # no label
                "not a dict",
                {"label": "Bad severity", "severity": "catastrophic"},
                {"label": "Bad confidence", "confidence": "very"},
            ]
        )

        labels = [f["label"] for f in out]
        assert labels == ["Valid", "Bad severity", "Bad confidence"]
        assert out[1]["severity"] == "indeterminate"  # invalid value coerced
        assert out[2]["confidence"] is None

    def test_confidence_is_clamped(self):
        from capabilities.cross_image_synthesis import _normalise_findings

        out = _normalise_findings([{"label": "x", "confidence": 5.0}])
        assert out[0]["confidence"] == 1.0


class TestLiteratureQuery:
    def test_query_is_built_from_findings_not_patient_data(self):
        from capabilities.web_search import _build_query

        request = CapabilityRequest(
            instruction="look it up",
            study=_study(patient_id="PID-99", clinical_history="John Smith, 54"),
            artifacts={
                "findings": [
                    {"label": "Pleural effusion", "severity": "high"},
                    {"label": "Cardiomegaly", "severity": "low"},
                ]
            },
        )
        query = _build_query(request)

        assert "Pleural effusion" in query
        assert "PID-99" not in query
        assert "John Smith" not in query

    def test_normal_findings_do_not_trigger_a_search(self):
        from capabilities.web_search import _build_query

        request = CapabilityRequest(
            instruction="",
            artifacts={"findings": [{"label": "No acute abnormality", "severity": "normal"}]},
        )
        assert _build_query(request) == ""

    def test_clinical_domains_rank_first(self):
        from capabilities.web_search import _PREFERRED_DOMAINS

        assert "radiopaedia.org" in _PREFERRED_DOMAINS
        assert "pubmed.ncbi.nlm.nih.gov" in _PREFERRED_DOMAINS


class TestSummarization:
    def test_short_text_skips_the_model_call(self):
        from capabilities.summarization import run

        request = CapabilityRequest(instruction="summarize", text="Short finding.")
        result = run(request)

        assert result.text == "Short finding."
        assert "passthrough" in result.artifacts["summarization"]

    def test_empty_input_is_handled(self):
        from capabilities.summarization import run

        result = run(CapabilityRequest(instruction="summarize", text="   "))
        assert "No input text" in result.text


class TestReportSynthesisSafety:
    def test_triage_is_derived_from_severity(self):
        from capabilities.report_synthesis import _triage_for

        assert _triage_for(Severity.CRITICAL) == "immediate"
        assert _triage_for(Severity.HIGH) == "urgent"
        assert _triage_for(Severity.LOW) == "routine"

    def test_overall_severity_is_the_worst_finding(self):
        from capabilities.report_synthesis import _severity_from_findings

        severity = _severity_from_findings(
            [{"severity": "low"}, {"severity": "critical"}, {"severity": "normal"}]
        )
        assert severity is Severity.CRITICAL

    def test_no_findings_gives_indeterminate_not_normal(self):
        """Absence of findings is not evidence of absence of disease."""
        from capabilities.report_synthesis import _severity_from_findings

        assert _severity_from_findings([]) is Severity.INDETERMINATE


class TestRetry:
    def test_transient_errors_are_retried(self):
        from core.retry import with_retry

        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("connection reset")
            return "ok"

        assert with_retry(flaky, attempts=5, base_delay=0.01) == "ok"
        assert calls["n"] == 3

    def test_permanent_errors_fail_immediately(self):
        from core.retry import with_retry

        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise ValueError("malformed argument")

        with pytest.raises(ValueError):
            with_retry(broken, attempts=5, base_delay=0.01)
        assert calls["n"] == 1  # not retried

    @pytest.mark.parametrize(
        "message,transient",
        [
            ("rate limit exceeded", True),
            ("503 Service Unavailable", True),
            ("Model is currently loading", True),
            ("invalid api key", False),
        ],
    )
    def test_transient_classification(self, message, transient):
        from core.retry import is_transient

        assert is_transient(RuntimeError(message)) is transient

    def test_fallback_chain_uses_first_success(self):
        from core.retry import first_success

        result = first_success(
            [
                lambda: (_ for _ in ()).throw(RuntimeError("gated model")),
                lambda: "fallback worked",
            ]
        )
        assert result == "fallback worked"


class TestCache:
    def test_round_trip(self):
        from core import cache

        cache.write_json("ns", "key1", {"a": 1})
        assert cache.read_json("ns", "key1") == {"a": 1}

    def test_miss_returns_none(self):
        from core import cache

        assert cache.read_json("ns", "absent") is None

    def test_corrupt_entry_is_discarded(self):
        from core import cache

        cache.write_json("ns", "key2", {"a": 1})
        path = cache._path_for("ns", "key2", ".json")
        path.write_text("{not json", encoding="utf-8")

        assert cache.read_json("ns", "key2") is None
        assert not path.exists()  # cleaned up rather than left to fail again

    def test_disabling_the_cache_is_honoured(self, override_runtime):
        from core import cache

        override_runtime(cache_enabled=False)
        cache.write_json("ns", "key3", {"a": 1})
        assert cache.read_json("ns", "key3") is None
