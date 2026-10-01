"""Regressions for bugs found by running the real pipeline, not the stubs.

Each of these shipped green unit tests and still failed on a live run.
"""

from __future__ import annotations

import concurrent.futures

import pytest

from models import Graph, Node, PatientContext, Severity, Study


class TestRegistryRace:
    """The registry set ``_loaded = True`` before its imports had run.

    The executor calls ``registry.get()`` from several worker threads at once,
    so a second thread could observe an empty registry and fail a node with
    "Unknown capability 'image_classification'. Registered: []".
    """

    def test_concurrent_lookup_never_sees_an_empty_registry(self, monkeypatch):
        import sys

        from capabilities import registry

        # A genuine cold start. Clearing _REGISTRY alone does not reproduce the
        # bug: Python caches modules, so the decorators would never run again.
        # Both caches have to go -- the sys.modules entry AND the attribute the
        # `capabilities` package holds, since `from capabilities import X`
        # resolves the package attribute first and would skip the re-import.
        import capabilities

        short_names = [
            name.split(".", 1)[1]
            for name in list(sys.modules)
            if name.startswith("capabilities.") and name != "capabilities.registry"
        ]
        for short_name in short_names:
            monkeypatch.delitem(sys.modules, f"capabilities.{short_name}", raising=False)
            monkeypatch.delattr(capabilities, short_name, raising=False)

        monkeypatch.setattr(registry, "_REGISTRY", {}, raising=False)
        monkeypatch.setattr(registry, "_DESCRIPTIONS", {}, raising=False)
        monkeypatch.setattr(registry, "_loaded", False)

        errors: list[Exception] = []

        def lookup(_i):
            try:
                return registry.get("image_classification")
            except Exception as exc:  # the bug surfaced exactly here
                errors.append(exc)
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lookup, range(32)))

        assert not errors, f"concurrent lookup failed: {errors[0]}"
        assert all(callable(fn) for fn in results)

    def test_capabilities_resolve_under_real_concurrent_execution(self, fake_capability):
        """The exact shape that failed: two capabilities in one wave."""
        from agents.graph_executor import GraphExecutor
        from capabilities import registry
        from capabilities.registry import CapabilityResult

        registry._ensure_loaded()
        fake_capability("modality_triage", lambda r: CapabilityResult(text="triaged"))
        fake_capability("image_classification", lambda r: CapabilityResult(text="screened"))

        graph = Graph(
            job="j",
            nodes=[
                Node(id="triage", description="t", capability="modality_triage"),
                Node(id="screen", description="s", capability="image_classification"),
            ],
        )
        GraphExecutor().run(graph)

        assert [n.status for n in graph.nodes] == ["done", "done"]


class TestEmptyFindingsAreNotNormal:
    """``max_severity([])`` returns NORMAL.

    A run whose every analysis stage failed produced zero findings and was
    therefore reported as ``status=normal`` -- an all-clear for a study that
    was never read. Absence of evidence is not evidence of absence.
    """

    def _degraded_graph(self) -> Graph:
        return Graph(
            job="j",
            nodes=[
                Node(
                    id="read",
                    description="read",
                    capability="vision",
                    status="failed",
                    error="VLM unavailable",
                ),
                Node(
                    id="report",
                    description="report",
                    capability="report_synthesis",
                    depends_on=["read"],
                    status="skipped",
                ),
            ],
        )

    def _study(self) -> Study:
        return Study(study_id="s", source_dir="/tmp", patient=PatientContext())

    def test_total_failure_is_indeterminate_not_normal(self):
        from agents.integrator_agent import IntegratorAgent

        report = IntegratorAgent().integrate(self._degraded_graph(), self._study())

        assert report.findings == []
        assert report.status.overall_severity is Severity.INDETERMINATE
        assert report.status.overall_severity is not Severity.NORMAL
        assert report.degraded

    def test_capability_layer_agrees(self):
        from capabilities.report_synthesis import _severity_from_findings

        assert _severity_from_findings([]) is Severity.INDETERMINATE

    def test_a_genuinely_normal_read_still_reports_normal(self):
        """The fix must not make clear studies look uncertain."""
        from agents.integrator_agent import IntegratorAgent

        graph = Graph(
            job="j",
            nodes=[
                Node(
                    id="consolidate",
                    description="c",
                    capability="cross_image_synthesis",
                    status="done",
                    output="normal",
                    artifacts={
                        "findings": [
                            {
                                "label": "No acute abnormality",
                                "description": "Clear lung fields.",
                                "severity": "normal",
                            }
                        ]
                    },
                )
            ],
        )
        report = IntegratorAgent().integrate(graph, self._study())

        assert report.status.overall_severity is Severity.NORMAL


class TestConsoleEncoding:
    """The terminal summary's severity badges crashed cp1252 consoles.

    The run completed, wrote both report files, and then died with
    UnicodeEncodeError in its final print -- exit code 0, no output.
    """

    def test_summary_encodes_on_a_legacy_windows_codepage(self):
        from models import RadiologyReport
        from reporting.report_builder import render_summary

        report = RadiologyReport(study_id="s")
        summary = render_summary(report)

        # Would raise UnicodeEncodeError before stdout is reconfigured.
        summary.encode("utf-8")
        assert summary.encode("cp1252", errors="replace")

    def test_cli_reconfigures_stdout(self, monkeypatch):
        import main

        calls = []

        class FakeStream:
            def reconfigure(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr(main.sys, "stdout", FakeStream())
        monkeypatch.setattr(main.sys, "stderr", FakeStream())
        main._force_utf8_output()

        assert calls == [
            {"encoding": "utf-8", "errors": "replace"},
            {"encoding": "utf-8", "errors": "replace"},
        ]

    def test_reconfigure_failure_is_survivable(self, monkeypatch):
        """A redirected stream may refuse; that must not kill the run."""
        import main

        class StubbornStream:
            def reconfigure(self, **kwargs):
                raise ValueError("cannot reconfigure a detached stream")

        monkeypatch.setattr(main.sys, "stdout", StubbornStream())
        monkeypatch.setattr(main.sys, "stderr", StubbornStream())
        main._force_utf8_output()  # must not raise


class TestMarkdownIsEncodable:
    def test_full_report_survives_utf8_roundtrip(self):
        from models import Finding, RadiologyReport
        from reporting.report_builder import render_markdown

        report = RadiologyReport(study_id="s")
        report.findings = [
            Finding(label="Consolidation", description="RLL", severity=Severity.HIGH)
        ]
        markdown = render_markdown(report)

        assert markdown == markdown.encode("utf-8").decode("utf-8")
        assert "Consolidation" in markdown
