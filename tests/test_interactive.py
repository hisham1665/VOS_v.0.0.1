import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aos_v0.core.events import EventType, OrchestrationEvent
from aos_v0.core.runtime import Session
from aos_v0.interactive import InteractiveService
from aos_v0.tui_commands import CommandRegistry
from aos_v0.tui import format_event


class InteractiveTests(unittest.TestCase):
    def test_command_registry_parses_and_reports_unknown_commands(self):
        registry = CommandRegistry()
        registry.register("ping", "Test command", lambda args: " ".join(args))
        self.assertEqual(registry.execute("/ping hello world"), "hello world")
        self.assertEqual(registry.execute("natural language"), None)
        self.assertIn("Unknown command", registry.execute("/nope"))

    def test_command_registry_preserves_windows_upload_path(self):
        registry = CommandRegistry()
        seen = []
        registry.register("upload", "Test upload", lambda args: seen.append(args) or "ok")
        registry.execute('/upload "C:\\Users\\Asus\\My Files\\paper.pdf"')
        self.assertEqual(seen, [[r"C:\Users\Asus\My Files\paper.pdf"]])

    def test_session_records_execution_and_recovery_events(self):
        session = Session(session_id="aos_test")
        session.record_event(OrchestrationEvent(EventType.AGENT_FAILED, "req", {}))
        session.record_event(OrchestrationEvent(EventType.RECOVERY_COMPLETED, "req", {"recovered": True}))
        summary = session.summary()
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["recoveries"], 1)

    def test_event_renderer_uses_actual_score_payload(self):
        event = OrchestrationEvent(EventType.AGENT_SELECTED, "req", {
            "resource": "vision", "candidates": [{"resource": "vision", "score": 1.25}],
        })
        self.assertIn("1.250", format_event(event))


class InteractiveSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = InteractiveService(storage_dir=self.root / "stored")

    def tearDown(self):
        self.temp.cleanup()

    def make_pdf(self, name):
        path = self.root / name
        path.write_bytes(b"%PDF-1.4 fake pdf bytes " + name.encode())
        return str(path)

    def test_submit_defaults_to_latest_uploaded_document(self):
        first = self.make_pdf("first.pdf")
        second = self.make_pdf("second.pdf")
        first_art = self.service.upload([first])[0]
        second_art = self.service.upload([second])[0]
        self.assertNotEqual(first_art.id, second_art.id)

        with mock.patch("aos_v0.interactive.run", return_value="ok") as mocked_run:
            self.service.submit("summarize the document")

        inputs = mocked_run.call_args.args[1]
        self.assertEqual(inputs["document"], second_art.path)
        self.assertNotEqual(inputs["document"], first_art.path)

    def test_submit_explicit_ids_still_win(self):
        first = self.make_pdf("first.pdf")
        second = self.make_pdf("second.pdf")
        by_name = {a.name: a for a in self.service.upload([first, second])}

        with mock.patch("aos_v0.interactive.run", return_value="ok") as mocked_run:
            self.service.submit("use this one", artifact_ids=[by_name["first.pdf"].id])

        inputs = mocked_run.call_args.args[1]
        self.assertEqual(inputs["document"], by_name["first.pdf"].path)

    def test_submit_with_no_uploads_sends_no_inputs(self):
        with mock.patch("aos_v0.interactive.run", return_value="ok") as mocked_run:
            self.service.submit("just answer")
        self.assertIsNone(mocked_run.call_args.args[1])
